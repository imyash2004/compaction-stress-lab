"""LLM judge for free-text answers (why-questions, next-step answers): one batched call per run, verdicts cached.

Rubric: 2 = states the reference (paraphrase OK), 1 = partially or vaguely right, 0 = wrong, missing or contradicting.
An empty or UNKNOWN answer is scored 0 with verdict "abstain" and never sent. Judge deployment: env LAB_JUDGE_MODEL
(default gpt-6.1-sol on Foundry). Cache: data/judge_cache.json, key = sha256(judge model + PROMPT_VERSION + item).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from typing import Callable

from lab import probe
from lab.transcript import ROOT

PROMPT_VERSION = "judge-v1"
CACHE_PATH = ROOT / "data" / "judge_cache.json"
MAX_ANSWER_CHARS = 2000

RUBRIC = """You grade answers that an engineering assistant gave about a past work session, against a reference.
Each item has a type, a question, the reference (what really happened in the session) and the assistant's answer.

Scores:
2 = the answer states the reference (paraphrase is fine; extra correct detail is fine).
    type "decision": it gives the same reason as the reference.
    type "continuation": it names the same next action on the same target (file, test or command) as the reference.
1 = partially or vaguely right: part of the reference, or too vague to tell it apart from other plausible answers.
0 = wrong, missing, or contradicting the reference (a confident but different reason or action is 0).
Grade only agreement with the reference, not style or length. Do not reward hedged lists of possibilities.

Reply with exactly one JSON object and nothing else:
{"verdicts": [{"n": <item number>, "score": 0 | 1 | 2, "reason": "<at most 15 words>"}, ...]}
with one verdict for every item."""

_lock = threading.Lock()
_cache: dict | None = None
_adapter = None
_ABSTAIN = re.compile(r"[\s\W]*(unknown|n/?a|none|null|not (known|sure|recorded|available)|unsure|i do ?n.?t know|don.?t know|"
                      r"do not know|no (idea|record|information))( (in|from) (this|the) (session|conversation|context|history))?[\s\W]*", re.I)
# after a leading "UNKNOWN", content worth judging: a number, a cause or a guess ("UNKNOWN: 1Gi was over the 800Mi max")
_SUBSTANCE = re.compile(r"\d|\b(because|since|due|caus\w*|so|after|likely|probabl\w*|possibl\w*|maybe|perhaps|but|was|were)\b", re.I)


def judge_model() -> str:
    return os.environ.get("LAB_JUDGE_MODEL", "gpt-6.1-sol")


def keyterm(answer: str | None, patterns: list[str]) -> float:
    """Fraction of key-point regexes found in the answer (case-insensitive)."""
    if not patterns:
        return 0.0
    a = answer or ""
    return round(sum(bool(re.search(p, a, re.I)) for p in patterns) / len(patterns), 3)


def is_abstain(answer: str | None) -> bool:
    a = (answer or "").strip()
    m = re.match(r"\W*unknown\b", a, re.I)
    return not a or bool(_ABSTAIN.fullmatch(a)) or (m is not None and len(a) < 120 and not _SUBSTANCE.search(a[m.end():]))


def _key(model: str, item: dict) -> str:
    blob = json.dumps({"model": model, "version": PROMPT_VERSION, "measure": item.get("measure"), "question": item.get("question"),
                       "reference": item.get("reference"), "answer": item.get("answer")}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            _cache = {}
    return _cache


def _save() -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(f".tmp{threading.get_ident()}")
    tmp.write_text(json.dumps(_cache, indent=0, sort_keys=True))
    os.replace(tmp, CACHE_PATH)


def build_prompt(items: list[dict]) -> str:
    parts = [RUBRIC, ""]
    for n, it in enumerate(items, 1):
        ans = str(it.get("answer") or "")[:MAX_ANSWER_CHARS]
        parts += [f"Item {n} (type: {it.get('measure', 'decision')})", f"Question: {it.get('question', '')}",
                  f"Reference: {it.get('reference', '')}", f"Answer: {ans}", ""]
    return "\n".join(parts)


def default_ask(prompt: str) -> str:
    global _adapter
    from lab import providers
    with _lock:
        if _adapter is None:
            _adapter = providers.build_adapter("openai", judge_model())
    res = _adapter.ask([], prompt, max_tokens=16000)
    if not res.ok:
        raise RuntimeError(res.error or "judge call failed")
    return res.text


def _parse(text: str, n: int) -> dict[int, dict]:
    obj = probe.parse_json(text) or {}
    out = {}
    for v in obj.get("verdicts") or []:
        try:
            i, s = int(v.get("n")), int(v.get("score"))
        except (TypeError, ValueError, AttributeError):
            continue
        if 1 <= i <= n and s in (0, 1, 2):
            out[i] = {"score": s, "reason": str(v.get("reason") or "")[:300]}
    return out


def judge_items(items: list[dict], ask: Callable[[str], str] | None = None) -> list[dict]:
    """One verdict per item, in order: {id, score: 0|1|2|None, verdict: abstain|judged|judge_error, reason, cached}.
    Items needing a call go out in ONE batched request (retried once for unparseable or missing verdicts)."""
    ask = ask or default_ask
    model = judge_model()
    out: list[dict | None] = [None] * len(items)
    todo = []
    with _lock:
        cache = _load()
        for i, it in enumerate(items):
            if is_abstain(it.get("answer")):
                out[i] = {"id": it.get("id"), "score": 0, "verdict": "abstain", "reason": "empty or UNKNOWN answer", "cached": False}
            elif (hit := cache.get(_key(model, it))) is not None:
                out[i] = {"id": it.get("id"), "score": hit["score"], "verdict": "judged", "reason": hit.get("reason", ""), "cached": True}
            else:
                todo.append(i)
    errors = []
    for _ in range(2):
        if not todo:
            break
        batch = [items[i] for i in todo]
        try:
            got = _parse(ask(build_prompt(batch)), len(batch))
        except Exception as e:  # noqa: BLE001 - a failed judge call is recorded per item
            errors.append(f"{type(e).__name__}: {e}")
            got = {}
        done = []
        with _lock:
            cache = _load()
            for n, i in enumerate(todo, 1):
                if n in got:
                    cache[_key(model, items[i])] = {**got[n], "model": model, "version": PROMPT_VERSION}
                    out[i] = {"id": items[i].get("id"), **got[n], "verdict": "judged", "cached": False}
                    done.append(i)
            if done:
                _save()
        todo = [i for i in todo if i not in done]
    for i in todo:
        out[i] = {"id": items[i].get("id"), "score": None, "verdict": "judge_error", "reason": "; ".join(errors)[:300] or "no verdict",
                  "cached": False}
    return out  # type: ignore[return-value]
