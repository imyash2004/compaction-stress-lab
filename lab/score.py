"""Deterministic scoring of probe answers against the hidden answer key. No LLM judge.

Outcome categories (one per fact per run):
    CORRECT      exact current value
    STALE        a value that was true earlier in the session
    DECOY        a near-miss value present elsewhere in the context
    INVENTED     a value that appears nowhere in the transcript
    OTHER_WRONG  wrong (or ambiguous between several values), but present somewhere in the transcript
    UNKNOWN      the model said it doesn't know (or gave nothing)
"""

from __future__ import annotations

import json
import re

from lab import scenario as sc

OUTCOMES = ["CORRECT", "STALE", "DECOY", "INVENTED", "OTHER_WRONG", "UNKNOWN"]
_UNKNOWN_RX = re.compile(
    r"\b(unknown|n/?a|none|unsure|unclear|no (record|idea|information|data)|"
    r"not (known|sure|clear|recorded|retained|available|mentioned|specified|stated|provided)|"
    r"don.?t know|do not know|cannot (determine|tell|recall)|can.?t (determine|tell|recall))\b", re.I)
_TRANSITION = re.compile(r"->|→|=>|\bto\b|\bnow\b", re.I)
_EARLIER = re.compile(r"\b(was|were|previously|formerly|earlier|old|before|superseded|replac\w*|from|not)\b", re.I)

# ------------------------------------------------------------------------------------------------ extractors
# Each returns normalized candidate values in order of appearance.

_INT = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:ms)?(?![\w]|\.\d)")
_VERSION = re.compile(r"(?<![\w.])v?(\d+\.\d+(?:\.\d+)?)(?![\w]|\.\d)")
_HASH = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{7,40})(?![0-9a-fA-F])")
_PATH_LINE = re.compile(r"([\w./-]+\.py)\"?(?:\s*[:,]\s*(?:line\s+)?|\s+(?:at\s+)?line\s+|\s*\(line\s+)(\d+)")
_PATH = re.compile(r"[\w./-]+\.(?:py|ya?ml|txt|toml|cfg|ini|json|md)\b")
_DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|milliseconds?|s|secs?|seconds?|m|mins?|minutes?)\b", re.I)
_MEM = re.compile(r"(\d+(?:\.\d+)?)\s*(Mi|MiB|Gi|GiB|M|G|MB|GB)\b")
_TIME = re.compile(r"(\d{2}:\d{2}:\d{2})")


def _norm_path(p: str) -> str:
    p = p.strip("`'\" ")
    for prefix in ("/app/", "./", "/workspace/payments-api/"):
        if p.startswith(prefix):
            p = p[len(prefix):]
    return p


def _dur_seconds(num: str, unit: str) -> str:
    v = float(num)
    u = unit.lower()
    if u.startswith("ms") or u.startswith("milli"):
        v = v / 1000
    elif u in ("m", "min", "mins", "minute", "minutes"):
        v = v * 60
    return f"{v:g}s"


def _mem_mi(num: str, unit: str) -> str:
    v = float(num)
    if unit.upper().startswith("G"):
        v *= 1024
    return f"{v:g}Mi"


def _boundary(value: str, case_sensitive: bool) -> re.Pattern:
    flags = 0 if case_sensitive else re.I
    return re.compile(rf"(?<![\w-]){re.escape(value)}(?![\w-])", flags)


def extract(kind: str, text: str, known: list[str] | None = None) -> list[str]:
    if kind == "int":
        return [m.group(1).replace(",", "") for m in _INT.finditer(text)]
    if kind == "version":
        return [m.group(1) for m in _VERSION.finditer(text)]
    if kind == "hash":
        return [m.group(1).lower() for m in _HASH.finditer(text) if re.search(r"[a-f]", m.group(1).lower())]
    if kind == "path_line":
        return [f"{_norm_path(m.group(1))}:{m.group(2)}" for m in _PATH_LINE.finditer(text)]
    if kind == "duration":
        return [_dur_seconds(m.group(1), m.group(2)) for m in _DURATION.finditer(text)]
    if kind == "mem":
        return [_mem_mi(m.group(1), m.group(2)) for m in _MEM.finditer(text)]
    if kind == "timestamp":
        return [m.group(1) for m in _TIME.finditer(text)]
    if kind in ("token", "token_cs"):
        hits = []
        for v in known or []:
            for m in _boundary(v, kind == "token_cs").finditer(text):
                hits.append((m.start(), -len(v), v))
        return [v for _, _, v in sorted(hits)]
    raise ValueError(kind)


def _same(kind: str, a: str, b: str) -> bool:
    if kind == "hash":
        return len(a) >= 7 and (b.startswith(a) or a.startswith(b))
    if kind == "path_line":
        pa, la = a.rsplit(":", 1)
        pb, lb = b.rsplit(":", 1)
        return la == lb and (pa == pb or pb.endswith("/" + pa))
    if kind == "duration":
        return a == b or _dur_norm(a) == _dur_norm(b)
    if kind == "mem":
        return _mem_norm(a) == _mem_norm(b)
    if kind == "token":
        return a.lower() == b.lower()
    return a == b


def _dur_norm(v: str) -> str:
    m = _DURATION.search(v)
    return _dur_seconds(m.group(1), m.group(2)) if m else v


def _mem_norm(v: str) -> str:
    m = _MEM.search(v)
    return _mem_mi(m.group(1), m.group(2)) if m else v


def _in_transcript(kind: str, value: str, transcript_text: str) -> bool:
    if kind == "path_line":
        value = value.split(":")[0]
    if kind == "hash":
        return bool(re.search(rf"(?<![0-9a-f]){re.escape(value)}", transcript_text, re.I))
    if kind == "duration":
        num = value.rstrip("s")
        return bool(re.search(rf"(?<![\w.]){re.escape(num)}\s*s\b", transcript_text))
    if kind == "mem":
        return value in transcript_text
    return bool(_boundary(value, kind == "token_cs").search(transcript_text))


def _label(fact: sc.Fact, value: str) -> str | None:
    if any(_same(fact.kind, value, v) for v in [fact.truth, *fact.alts]):
        return "CORRECT"
    if any(_same(fact.kind, value, v) for v in fact.stale):
        return "STALE"
    if any(_same(fact.kind, value, v) for v in fact.decoys):
        return "DECOY"
    return None


# ------------------------------------------------------------------------------------------------ recall quiz

def classify(fact: sc.Fact, answer: str | None, transcript_text: str) -> dict:
    ans = (answer or "").strip()
    row = {"fact": fact.id, "answer": ans, "truth": fact.truth, "extracted": None, "outcome": "UNKNOWN"}
    if not ans:
        return row

    if fact.kind == "list":
        truth = set(fact.truth.split(","))
        got = {_norm_path(p) for p in _PATH.findall(ans)}
        if not got:
            row["outcome"] = "UNKNOWN" if _UNKNOWN_RX.search(ans) else "OTHER_WRONG"
            return row
        tp = len(truth & got)
        row.update(extracted=sorted(got), precision=round(tp / len(got), 3), recall=round(tp / len(truth), 3))
        row["outcome"] = "CORRECT" if got == truth else "OTHER_WRONG"
        if got != truth and (got & set(fact.decoys)):
            row["outcome"] = "DECOY"
        return row

    if fact.kind == "text":
        def has_all(target: str) -> bool:
            return all(_boundary(tok, False).search(ans) for tok in target.lower().split())
        if has_all(fact.truth) or any(has_all(a) for a in fact.alts):
            row["outcome"] = "CORRECT"
        elif any(has_all(d) for d in fact.decoys):
            row["outcome"] = "DECOY"
        elif _UNKNOWN_RX.search(ans):
            row["outcome"] = "UNKNOWN"
        else:
            row["outcome"] = "OTHER_WRONG"
        return row

    known = [fact.truth, *fact.alts, *fact.stale, *fact.decoys]
    cands = extract(fact.kind, ans, known)
    if not cands:
        if _UNKNOWN_RX.search(ans):
            row["outcome"] = "UNKNOWN"
        elif fact.kind in ("token", "token_cs"):
            pieces = re.findall(r"[\w.:/-]{3,}", ans)
            row["outcome"] = "OTHER_WRONG" if any(_in_transcript("token", p, transcript_text) for p in pieces) else "INVENTED"
        else:
            row["outcome"] = "OTHER_WRONG"
        return row

    labelled = [(c, _label(fact, c)) for c in cands]
    distinct_known = {lab_value for lab_value, lab in labelled if lab}
    if _TRANSITION.search(ans) and len(cands) > 1:
        chosen = cands[-1]  # "5433 -> 6432", "moved to 6432": the end state is the answer
    elif len(distinct_known) > 1:
        first, first_lab = labelled[0]
        rest = ans[ans.find(first) + len(first):] if first in ans else ""
        if first_lab == "CORRECT" and _EARLIER.search(rest):
            chosen = first  # "6432 (was 5433)"
        else:
            row.update(extracted=cands, outcome="OTHER_WRONG", ambiguous=True)
            return row
    else:
        chosen = cands[0]
    row["extracted"] = chosen
    lab = _label(fact, chosen)
    if lab:
        row["outcome"] = lab
    elif _in_transcript(fact.kind, chosen, transcript_text):
        row["outcome"] = "OTHER_WRONG"
    else:
        row["outcome"] = "INVENTED"
    return row


def in_summary(fact: sc.Fact, summary: str) -> bool:
    """White-box check on readable text (a compaction summary or a memory dump): is the current value written in it?"""
    if not summary:
        return False
    if fact.summary_rx:
        return bool(re.search(fact.summary_rx, summary))
    if fact.kind == "list":
        got = {_norm_path(p) for p in _PATH.findall(summary)}
        return set(fact.truth.split(",")) <= got
    if fact.kind == "text":
        return all(_boundary(tok, False).search(summary) for tok in fact.truth.lower().split())
    if fact.kind in ("token", "token_cs"):
        return bool(_boundary(fact.truth, fact.kind == "token_cs").search(summary))
    if fact.kind == "path_line":
        path, line = fact.truth.split(":")
        return any(_same("path_line", c, fact.truth) for c in extract("path_line", summary)) or bool(
            re.search(rf"{re.escape(path)}\D{{0,12}}\b{line}\b", summary))
    if fact.kind == "hash":
        return bool(re.search(rf"(?<![0-9a-f]){re.escape(fact.truth[:7])}", summary, re.I))
    return any(_same(fact.kind, c, fact.truth) for c in extract(fact.kind, summary, [fact.truth]))


def score_quiz(answers: dict, transcript_text: str, summary: str | None = None, facts: list | None = None) -> list[dict]:
    rows = []
    for f in (sc.FACTS if facts is None else facts):
        ans = answers.get(f.id)
        if isinstance(ans, list):
            ans = ", ".join(str(a) for a in ans)
        row = classify(f, None if ans is None else str(ans), transcript_text)
        row["traps"] = f.traps
        row["salience"] = f.salience
        row["kind"] = f.kind
        if summary is not None:
            row["in_summary"] = in_summary(f, summary)
        rows.append(row)
    return rows


# ------------------------------------------------------------------------------------------------ action task

def _get(d: dict, path: tuple) -> str:
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return ""
        cur = cur[k]
    return "" if cur is None else str(cur)


def command_segments(commands) -> list[str]:
    """Executable pieces of the plan's commands: split on newlines and shell operators, drop comments/echo/printf."""
    if isinstance(commands, str):
        commands = commands.splitlines()
    if not isinstance(commands, list):
        return []
    segs = []
    for c in commands:
        for piece in re.split(r"\n|&&|\|\||;", str(c)):
            piece = re.sub(r"\s#.*$", "", piece).strip()
            piece = re.sub(r"^(?:(?:then|do|else|if|while|until|!)\s+)+", "", piece)  # leading shell keywords only
            if not piece or re.match(r"^(#|echo\b|printf\b|:(\s|$)|true\b|false\b|fi\b|done\b|\[)", piece):
                continue
            segs.append(piece)
    return segs


# a read-only search (grep and friends, also after a pipe) names values only as search patterns: everything from it on
_SEARCH_CMD = re.compile(r"(?:^|\||\s--)\s*(?:sudo\s+|xargs\s+)?(?:git\s+grep|[ef]?grep|rg|ack|ag)\b")


def acting_part(segment: str) -> str:
    """The part of a command segment before any read-only search command ('... env | grep X' -> '... env')."""
    m = _SEARCH_CMD.search(segment)
    return segment[:m.start()].strip() if m else segment


def score_action(plan: dict | None, raw_text: str, ignore_searches: bool = False) -> dict:
    """ignore_searches: rule checks skip read-only search commands, so `grep -rn OLD_NAME src/` is not a
    use of OLD_NAME. The original 10-model scoring ran without it."""
    out = {"parsed": plan is not None, "values": {}, "commands": {}, "violations": {}}
    plan = plan or {}
    for cid, path, expected, kind in sc.ACTION_VALUE_CHECKS:
        got = _get(plan, path)
        if kind == "contains":
            ok = expected.lower() in got.lower()
        elif kind in ("token", "token_cs"):
            g = re.sub(r"^\$\{?|\}$", "", got.strip().strip("`'\""))
            ok = g == expected if kind == "token_cs" else g.lower() == expected.lower()
        else:
            cands = extract(kind, got, [expected])
            ok = bool(cands) and _same(kind, cands[0], expected)
        out["values"][cid] = {"expected": expected, "got": got, "ok": ok}
    segs = command_segments(plan.get("commands"))
    for cid, rx in sc.ACTION_COMMAND_CHECKS.items():
        out["commands"][cid] = any(re.search(rx, s, re.I) for s in segs)
    # values the plan actually sets, plus executable commands; never comments or prose
    set_values = [f"{k}={v}" for k, v in (plan.get("env") or {}).items()] if isinstance(plan.get("env"), dict) else []
    set_values += [_get(plan, ("psycopg_requirement",)), f"rollback_revision={_get(plan, ('rollback_revision',))}",
                   f"webhook={_get(plan, ('env', 'webhook_secret_env_var'))}"]
    if ignore_searches:
        segs = [a for a in map(acting_part, segs) if a]
    scope = set_values + segs
    for vid, rx in sc.VIOLATIONS.items():
        if rx == "special":  # pool size above the rejected threshold, in env or commands
            vals = extract("int", _get(plan, ("env", "POOL_MAX_SIZE")))
            vals += [m.group(1) for s in segs for m in re.finditer(r"POOL_MAX_SIZE\D{0,4}(\d+)", s)]
            hit = any(int(v) > 20 for v in vals)
        else:
            flags = 0 if vid == "V1_revoked_secret" else re.I
            hit = any(re.search(rx, s, flags) for s in scope)
        out["violations"][vid] = hit
    out["value_accuracy"] = round(sum(v["ok"] for v in out["values"].values()) / len(out["values"]), 3)
    out["command_accuracy"] = round(sum(out["commands"].values()) / len(out["commands"]), 3)
    out["violation_count"] = sum(out["violations"].values())
    return out


def summarize(rows: list[dict]) -> dict:
    counts = {o: 0 for o in OUTCOMES}
    for r in rows:
        counts[r["outcome"]] += 1
    n = len(rows) or 1
    res = {"n": len(rows), "accuracy": round(counts["CORRECT"] / n, 3), **{o.lower(): c for o, c in counts.items()}}
    if rows and "in_summary" in rows[0]:
        res["in_summary_rate"] = round(sum(r["in_summary"] for r in rows) / n, 3)
    return res
