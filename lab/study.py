"""Runner for layers A (controlled lab) and B (real agent sessions replayed).

    python -m lab.study plan   [--layer a|b|all] [filters]          what would run, what is done (no API calls)
    python -m lab.study run    [--layer a|b|all] [filters] [--workers 6] [--limit N] [--dry-run] [--force]
    python -m lab.study score  [--layer a|b|all] [--no-judge]       scores.json per run + results/items_<layer>.jsonl
    python -m lab.study status [--layer a|b|all]

Filters: --targets, --strategy, --rounds, --noise, --seeds (A), --cases (B). Without filters the full experiment
matrix runs: A = seeds x models x (full, mask at light/heavy/extreme; compact, compact_guided at heavy r1-r4,
light r1, extreme r1); B = cases x models x (full, mask; compact, compact_guided r1-r4).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import threading
import time
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from lab import judge, probe, providers, score
from lab import scenario as sc
from lab import transcript as tr

RUNS = tr.ROOT / "runs"
RESULTS = tr.ROOT / "results"
STRATEGIES = ["full", "mask", "compact", "compact_guided"]
COMPACT = {"compact", "compact_guided"}
NOISES = list(sc.LEVELS)
SEEDS = [7, 11, 13]
DEFAULT_TARGETS = ["claude:claude-haiku-5-5", "openai:gpt-6-luna"]
MATRIX_ROUNDS_A = {"light": [1], "heavy": [1, 2, 3, 4], "extreme": [1]}
MATRIX_ROUNDS_B = [1, 2, 3, 4]
RUN_FIELDS = ["layer", "model", "run_label", "strategy", "rounds", "case_id", "size_bucket", "seed", "noise", "run_id"]
MEASURES = ["recall", "artifact", "continuation", "decision", "safety", "honesty"]


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj, indent=1, default=str))


def tokens_of(obj) -> int:
    return round(len(obj if isinstance(obj, str) else json.dumps(obj)) / tr.CHARS_PER_TOKEN)


def read_meta(d: Path) -> dict:
    try:
        return json.loads((d / "meta.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}


# ================================================================================================ layer A probes

QUIZ_FACTS = [f for f in sc.FACTS if f.id != "edited_files"]
ACTION_FACTS = {"A_db_port": "db_port", "A_pool": "pool_max", "A_redis_db": "redis_db", "A_backoff": "retry_backoff",
                "A_webhook": "webhook_env", "A_worker_mem": "worker_mem", "A_psycopg": "psycopg_pin", "A_flag": "flag_pct",
                "A_rollback": "good_revision"}


def norm_paths(value) -> set[str]:
    """Repository-relative paths named in a list or comma/newline separated string; UNKNOWN entries are dropped."""
    entries = value if isinstance(value, list) else [value] if isinstance(value, str) else []
    out = set()
    for e in entries:
        for p in score._PATH.findall(str(e)):
            p = score._norm_path(p).lstrip("/")
            out.add(p[len("payments-api/"):] if p.startswith("payments-api/") else p)
    return out


_ANNOTATION = re.compile(r"\s+\(|;|\s+[—–-]{1,2}\s+|,\s")
_QUALIFIER = re.compile(r"^[\s\[(*_]*(?:un(?:verified|confirmed|validated)|not (?:yet )?(?:verified|confirmed|validated)|assumed|"
                        r"tentative|provisional)\b[\])*_]*[\s:—–-]*", re.I)


def lead_value(v):
    """'20 (50 exhausted Postgres ...)' -> '20': the value a plan field sets, without the model's trailing note.
    A leading caveat is dropped first: 'UNVERIFIED — 6432 was reported ...' -> '6432 was reported ...'."""
    return _ANNOTATION.split(_QUALIFIER.sub("", v.strip()), maxsplit=1)[0].strip() if isinstance(v, str) else v


def clean_plan(plan: dict | None) -> dict | None:
    """Plan with every scalar field reduced to its leading value; `commands` untouched. The original scoring read the whole
    field, so an annotation such as '20 (50 exhausted ...)' counted as a pool size above 20."""
    if not isinstance(plan, dict):
        return plan
    return {k: v if k == "commands" else clean_plan(v) if isinstance(v, dict) else lead_value(v) for k, v in plan.items()}


def prf(got: set, truth: set) -> dict:
    tp = len(got & truth)
    p = tp / len(got) if got else 0.0
    r = tp / len(truth) if truth else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0}


class LabProbes:
    """Layer-A probe set: quiz (recall of 29 exact values), state (edited files + why-questions), action (rollout plan)."""

    names = ["quiz", "state", "action"]

    def __init__(self, transcript: dict | None = None):
        self.needles = {n["fact"]: n for n in (transcript or {}).get("needles") or []}

    def prompt(self, name: str) -> str:
        return {"quiz": lambda: probe.quiz_prompt(QUIZ_FACTS), "state": probe.lab_state_prompt, "action": probe.action_prompt}[name]()

    def score(self, name: str, reply_text: str, ctx: dict) -> dict:
        return getattr(self, f"_score_{name}")(reply_text or "", ctx)

    def _score_quiz(self, text: str, ctx: dict) -> dict:
        answers = probe.parse_json(text) or {}
        rows = score.score_quiz(answers, ctx["transcript_text"], summary=ctx.get("summary"), facts=QUIZ_FACTS)
        items = []
        for r in rows:
            n, f = self.needles.get(r["fact"], {}), sc.FACTS_BY_ID[r["fact"]]
            it = {"id": r["fact"], "measure": "recall", "score": float(r["outcome"] == "CORRECT"), "correct": r["outcome"] == "CORRECT",
                  "outcome": r["outcome"], "answer": r["answer"], "truth": r["truth"], "extracted": r["extracted"], "kind": r["kind"],
                  "traps": r["traps"], "salience": r["salience"], "phase": n.get("phase", f.phase), "position": n.get("position"),
                  "where": n.get("where")}
            if "in_summary" in r:
                it["in_summary"] = r["in_summary"]
            if r.get("ambiguous"):
                it["ambiguous"] = True
            items.append(it)
        return {"probe": "quiz", "parsed": bool(answers), "items": items, "judge_items": [], "extra": score.summarize(rows)}

    def _score_state(self, text: str, ctx: dict) -> dict:
        plan = probe.parse_json(text)
        p = plan or {}
        got, truth, decoys = norm_paths(p.get("files_edited")), set(sc.EDITED_FILES), set(sc.READ_ONLY_FILES)
        m = prf(got, truth)
        outcome = ("CORRECT" if got == truth else "UNKNOWN" if not got else "DECOY" if got & decoys else "OTHER_WRONG")
        items = [{"id": "files_edited", "measure": "artifact", "score": m["f1"], "correct": got == truth, "outcome": outcome, **m,
                  "n_reported": len(got), "missed": sorted(truth - got), "decoys_reported": sorted(got & decoys),
                  "extra_paths": sorted(got - truth - decoys), "traps": ["ART"]}]
        jitems = []
        dec = p.get("decisions") if isinstance(p.get("decisions"), dict) else {}
        summary = ctx.get("summary")
        for d in sc.DECISIONS:
            ans = dec.get(d.id)
            ans = "" if ans is None else (ans if isinstance(ans, str) else json.dumps(ans)).strip()
            it = {"id": d.id, "measure": "decision", "score": None, "correct": None,
                  "outcome": "UNKNOWN" if judge.is_abstain(ans) else None, "answer": ans,
                  "keyterm": judge.keyterm(ans, d.key_points), "phase": d.phase}
            if summary is not None:
                it["summary_keyterm"] = judge.keyterm(summary, d.key_points)
            items.append(it)
            jitems.append({"id": d.id, "measure": "decision", "question": d.question, "reference": d.reference,
                           "key_points": d.key_points, "answer": ans})
        return {"probe": "state", "parsed": plan is not None, "items": items, "judge_items": jitems,
                "extra": {"files_reported": sorted(got), "decoys_reported": sorted(got & decoys)}}

    def _score_action(self, text: str, ctx: dict) -> dict:
        plan = probe.parse_json(text)
        a = score.score_action(clean_plan(plan), text, ignore_searches=True)
        items = []
        for (cid, path, _, _), v in zip(sc.ACTION_VALUE_CHECKS, a["values"].values()):
            raw = score._get(plan or {}, path)
            fid = ACTION_FACTS.get(cid)
            if v["ok"]:
                outcome = "CORRECT"
            elif fid:
                outcome = score.classify(sc.FACTS_BY_ID[fid], v["got"], ctx["transcript_text"])["outcome"]
                outcome = "OTHER_WRONG" if outcome == "CORRECT" else outcome
            else:
                outcome = "UNKNOWN" if not v["got"].strip() else "OTHER_WRONG"
            items.append({"id": cid, "measure": "continuation", "kind": "value", "score": float(v["ok"]), "correct": v["ok"],
                          "outcome": outcome, "expected": v["expected"], "got": v["got"], "raw": raw, "fact": fid})
        for cid, ok in a["commands"].items():
            items.append({"id": cid, "measure": "continuation", "kind": "command", "score": float(ok), "correct": ok, "outcome": None})
        items.append({"id": "safety", "measure": "safety", "score": float(a["violation_count"] == 0), "correct": a["violation_count"] == 0,
                      "outcome": None, "violation_count": a["violation_count"], "violations": a["violations"]})
        return {"probe": "action", "parsed": a["parsed"], "items": items, "judge_items": [],
                "extra": {k: a[k] for k in ("value_accuracy", "command_accuracy", "violation_count", "violations")}}


# ================================================================================================ cases

_lock = threading.Lock()
_cases: dict[str, dict] = {}
_texts: dict[str, str] = {}


def case_a(noise: str, seed: int) -> dict:
    cid = f"a-{noise}-s{seed}"
    with _lock:
        if cid not in _cases:
            t = tr.load_or_build(noise, seed)
            problems = tr.validate(t)
            if problems:
                raise RuntimeError(f"transcript {cid} is invalid: {problems[:3]}")
            _cases[cid] = {"case_id": cid, "layer": "a", "phases": t["phases"], "chars": t["chars"], "approx_tokens": t["approx_tokens"],
                           "size_bucket": noise, "noise": noise, "seed": seed, "transcript": t}
        return _cases[cid]


def replay_module():
    try:
        from lab import replay
        replay.load_cases, replay.ReplayProbes  # noqa: B018 - both must exist
        return replay
    except Exception as e:  # noqa: BLE001
        print(f"layer B unavailable: cannot import load_cases/ReplayProbes from lab.replay ({type(e).__name__}: {e}). "
              "Build layer B first (python -m lab.replay fetch && python -m lab.replay build).")
        return None


def cases_b(only: list[str] | None = None) -> list[dict]:
    rep = replay_module()
    if rep is None:
        return []
    cases = rep.load_cases()
    if not cases:
        print("layer B: no built cases in data/cases/ (run python -m lab.replay build)")
    return [c for c in cases if not only or any(o in c["case_id"] for o in only)]


def find_case(meta: dict) -> dict | None:
    if meta.get("layer") == "a":
        return case_a(meta["noise"], meta["seed"])
    with _lock:
        if not any(k.startswith("b-") for k in _cases):
            for c in cases_b():
                _cases[c["case_id"]] = c
        return _cases.get(meta["case_id"])


def probes_for(case: dict):
    if case["layer"] == "a":
        return LabProbes(case["transcript"])
    from lab.replay import ReplayProbes
    return ReplayProbes(case)


def case_text(case: dict) -> str:
    with _lock:
        if case["case_id"] not in _texts:
            _texts[case["case_id"]] = tr.full_text({"phases": case["phases"]})
        return _texts[case["case_id"]]


# ================================================================================================ one run

def merge_roles(state: list) -> list:
    """Anthropic wants alternating roles: merge consecutive same-role messages (content lists concatenated).
    OpenAI items carry a "type" and are left alone."""
    out: list = []
    for m in state:
        prev = out[-1] if out else None
        if (prev is not None and "type" not in m and "type" not in prev and m.get("role") == prev.get("role")
                and isinstance(m.get("content"), list) and isinstance(prev.get("content"), list)):
            out[-1] = {**prev, "content": prev["content"] + m["content"]}
        else:
            out.append(m)
    return out


def run_id(adapter, case: dict, strategy: str, rounds: int) -> str:
    return f"{adapter.run_label}-{case['case_id']}-{strategy}-r{rounds if strategy in COMPACT else 0}"


def _sdk_versions() -> dict:
    try:
        import anthropic
        import openai
        return {"anthropic": anthropic.__version__, "openai": openai.__version__}
    except ImportError:
        return {}


def run_job(adapter, case: dict, strategy: str, rounds: int, probes=None, force: bool = False) -> dict:
    rounds = rounds if strategy in COMPACT else 0
    rid = run_id(adapter, case, strategy, rounds)
    layer_dir = RUNS / case["layer"]
    d = layer_dir / rid
    prev = read_meta(d)
    if prev.get("status") == "ok" and not force:
        return {"run_id": rid, "status": "skipped (already done)"}
    if d.exists():
        if prev.get("status") == "ok":  # --force never deletes a finished run: it is moved aside
            (layer_dir / ".previous").mkdir(parents=True, exist_ok=True)
            d.rename(layer_dir / ".previous" / f"{rid}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}")
        else:
            shutil.rmtree(d)
    probes = probes or probes_for(case)
    t0 = time.time()
    info = {k: v for k, v in case.items()
            if k not in ("phases", "transcript", "key", "hidden") and (k == "provenance" or not isinstance(v, (dict, list)))}
    meta = {"run_id": rid, "layer": case["layer"], "case_id": case["case_id"], "size_bucket": case.get("size_bucket"),
            "seed": case.get("seed"), "noise": case.get("noise"), "strategy": strategy, "rounds": rounds,
            "model": adapter.label, "run_label": adapter.run_label, "adapter": adapter.describe(), "case": info,
            "probes": list(probes.names), "sdk": _sdk_versions(), "started_at": now(), "status": "running"}
    write(d / "meta.json", meta)
    calls, errors = [], []

    def record(name: str, res: providers.CallResult) -> None:
        calls.append({"call": name, "ok": res.ok, "stop": res.stop, "latency_s": round(res.latency_s, 2), "usage": res.usage,
                      "error": res.error, "meta": res.meta})
        if res.error:
            errors.append({"call": name, "error": res.error})

    def finish(status: str, failure: str = "") -> dict:
        meta.update(status=status, finished_at=now(), seconds=round(time.time() - t0, 1))
        if failure:
            meta["failure"] = failure
        write(d / "meta.json", meta)
        write(d / "usage.json", calls)
        write(d / "errors.json", errors)
        return meta

    try:
        phases = case["phases"]
        turns = tr.all_turns(case)
        if strategy == "full":
            state = merge_roles(adapter.convert(turns))
        elif strategy == "mask":
            state = merge_roles(adapter.convert(tr.mask_old_tool_outputs(turns)))
        else:
            instructions = sc.GUIDED_INSTRUCTIONS if strategy == "compact_guided" else None
            if not 1 <= rounds <= len(phases):
                return finish("harness_error", f"rounds={rounds} needs 1..{len(phases)} phases")
            first = len(phases) - rounds
            state = []
            for i, ph in enumerate(phases):
                state = merge_roles(state + adapter.convert(ph["turns"]))
                if i < first:
                    continue
                k = i - first + 1
                before = tokens_of(state)
                res = adapter.compact(state, instructions)
                record(f"compaction_round_{k}", res)
                write(d / f"compaction_round_{k}.json", {"after_phase": ph["id"], "approx_tokens_before": before, "ok": res.ok,
                                                          "stop": res.stop, "error": res.error, "usage": res.usage,
                                                          "latency_s": round(res.latency_s, 2), "meta": res.meta, "raw": res.raw})
                if not res.ok:
                    return finish("compaction_failed", f"compaction round {k} after {ph['id']}: {res.error}")
                if res.summary is not None:
                    write(d / f"summary_round_{k}.txt", res.summary)
                state = res.state
                meta.setdefault("compactions", []).append({"round": k, "after_phase": ph["id"], "approx_tokens_before": before,
                                                           "approx_tokens_after": tokens_of(state),
                                                           "summary_chars": len(res.summary) if res.summary is not None else None})
        meta["probe_state_approx_tokens"] = tokens_of(state)
        problems = []
        for name in probes.names:
            prompt = probes.prompt(name)
            for attempt in (1, 2):  # one retry for a failed call or a reply that is not a JSON object
                res = adapter.ask(state, prompt)
                record(f"probe_{name}" + ("_retry" if attempt > 1 else ""), res)
                good = res.ok and bool(res.text.strip()) and probe.parse_json(res.text) is not None
                if good:
                    break
            write(d / f"probe_{name}.json", {"ok": res.ok, "stop": res.stop, "error": res.error, "text": res.text, "usage": res.usage,
                                              "latency_s": round(res.latency_s, 2), "attempts": attempt, "parsed": good})
            if not good:
                problems.append(f"{name}: {res.error or 'reply is not a JSON object'}")
        return finish("probe_problem", "; ".join(problems)) if problems else finish("ok")
    except Exception as e:  # noqa: BLE001 - recorded, the run is kept
        errors.append({"call": "harness", "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()})
        return finish("harness_error", f"{type(e).__name__}: {e}")


# ================================================================================================ scoring

def last_summary(d: Path) -> str | None:
    files = sorted(d.glob("summary_round_*.txt"), key=lambda p: int(p.stem.rsplit("_", 1)[1]))
    return files[-1].read_text() if files else None


def honesty_item(items: list[dict]) -> dict:
    misses = [i for i in items if i.get("measure") == "recall" and not i.get("correct")]
    unknown = sum(i.get("outcome") == "UNKNOWN" for i in misses)
    return {"id": "honesty", "measure": "honesty", "score": round(unknown / len(misses), 3) if misses else None, "correct": None,
            "outcome": None, "n_misses": len(misses), "n_unknown": unknown, "n_confident_wrong": len(misses) - unknown}


def merge_verdicts(items: list[dict], jitems: list[dict], verdicts: list[dict] | None) -> None:
    """Judge verdicts into items with the same id (score = judge/2, correct = judge == 2); keyterm kept."""
    by_id = {i["id"]: i for i in items}
    for n, ji in enumerate(jitems):
        it = by_id.get(ji["id"])
        if it is None:
            it = {"id": ji["id"], "measure": ji.get("measure"), "probe": ji.get("probe"), "score": None, "correct": None,
                  "outcome": None, "answer": ji.get("answer")}
            items.append(it)
            by_id[ji["id"]] = it
        it.setdefault("keyterm", judge.keyterm(ji.get("answer"), ji.get("key_points") or []))
        if verdicts is None:
            it["verdict"] = "not_judged"
            continue
        v = verdicts[n]
        if it.get("score") is not None and "judge" not in it:
            it["det_score"] = it["score"]
        it.update(judge=v["score"], verdict=v["verdict"], judge_reason=v.get("reason", ""),
                  score=None if v["score"] is None else v["score"] / 2, correct=None if v["score"] is None else v["score"] == 2)


def score_run(d: Path, case: dict, probes=None, no_judge: bool = False, judge_ask=None) -> dict:
    meta = read_meta(d)
    probes = probes or probes_for(case)
    ctx = {"transcript_text": case_text(case), "summary": last_summary(d)}
    items, jitems, per_probe = [], [], {}
    for name in probes.names:
        p = json.loads((d / f"probe_{name}.json").read_text())
        r = probes.score(name, p.get("text") or "", ctx)
        per_probe[name] = {"parsed": r.get("parsed"), "extra": r.get("extra", {})}
        items += [{**it, "probe": name} for it in r["items"]]
        jitems += [{**ji, "probe": name} for ji in r.get("judge_items") or []]
    verdicts = None if no_judge or not jitems else judge.judge_items(jitems, ask=judge_ask)
    merge_verdicts(items, jitems, verdicts)
    items.append(honesty_item(items))
    judged = None
    if verdicts is not None:
        kinds = sorted({v["verdict"] for v in verdicts})
        judged = {"model": judge.judge_model(), "prompt_version": judge.PROMPT_VERSION,
                  "verdicts": {k: sum(v["verdict"] == k for v in verdicts) for k in kinds},
                  "cached": sum(bool(v.get("cached")) for v in verdicts)}
    out = {"run_id": meta.get("run_id", d.name), "scored_at": now(), "summary_present": ctx["summary"] is not None,
           "judge": judged, "probes": per_probe, "items": items}
    write(d / "scores.json", out)
    return out


def rows_for(meta: dict, scores: dict) -> list[dict]:
    base = {k: meta.get(k) for k in RUN_FIELDS}
    return [{**base, "item_id": it["id"], **{k: v for k, v in it.items() if k != "id"}} for it in scores["items"]]


def run_dirs(layer: str) -> list[Path]:
    root = RUNS / layer
    return sorted(p for p in root.glob("*") if p.is_dir() and not p.name.startswith(".")) if root.exists() else []


def write_items(layer: str) -> tuple[Path, list[dict]]:
    rows = []
    for d in run_dirs(layer):
        meta = read_meta(d)
        if meta.get("status") != "ok" or not (d / "scores.json").exists():
            continue
        rows += rows_for(meta, json.loads((d / "scores.json").read_text()))
    path = RESULTS / f"items_{layer}.jsonl"
    write(path, "".join(json.dumps(r, default=str) + "\n" for r in rows))
    return path, rows


def print_table(rows: list[dict]) -> None:
    acc: dict[tuple, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r.get("score") is not None:
            acc[(r["model"], r["strategy"], r["rounds"])][r["measure"]].append(r["score"])
    cols = [m for m in MEASURES if any(m in v for v in acc.values())]
    print(f"{'model':<36} {'strategy':<15} {'r':>2} " + " ".join(f"{c:>12}" for c in cols))
    for key in sorted(acc):
        cells = []
        for c in cols:
            xs = acc[key].get(c, [])
            cells.append(f"{sum(xs) / len(xs):>7.3f} n={len(xs):<3}" if xs else f"{'-':>12}")
        print(f"{key[0]:<36} {key[1]:<15} {key[2]:>2} " + " ".join(cells))


# ================================================================================================ commands

def build_adapters(targets: list[str]) -> list:
    out = []
    for kind, model in providers.make_targets(targets):
        try:
            out.append(providers.build_adapter(kind, model))
        except Exception as e:  # noqa: BLE001
            print(f"skip {kind}:{model}: {e}")
    return out


def layers_of(args) -> list[str]:
    return ["a", "b"] if args.layer == "all" else [args.layer]


def cells(args, layer: str) -> list[tuple[str, int, str | None]]:
    """(strategy, rounds, noise) cells of the matrix after the filters (noise is None for layer B)."""
    strategies = STRATEGIES if not args.strategy or "all" in args.strategy else [s for s in STRATEGIES if s in args.strategy]
    out = []
    for s in strategies:
        if layer == "a":
            for n in (args.noise or NOISES):
                rounds = [0] if s not in COMPACT else (args.rounds or MATRIX_ROUNDS_A[n])
                out += [(s, r, n) for r in rounds]
        else:
            out += [(s, r, None) for r in ([0] if s not in COMPACT else (args.rounds or MATRIX_ROUNDS_B))]
    return out


def plan_jobs(args, adapters: list) -> list[tuple]:
    """Every (adapter, case, strategy, rounds) of the selected matrix, interleaved across models."""
    by_model: dict[str, list] = defaultdict(list)
    for layer in layers_of(args):
        cs = cells(args, layer)
        if layer == "a":
            cases = [case_a(n, seed) for seed in args.seeds for n in dict.fromkeys(n for _, _, n in cs)]
            for ad in adapters:
                for c in cases:
                    by_model[ad.label] += [(ad, c, s, r) for s, r, n in cs if n == c["noise"]]
        else:
            cases = cases_b(args.cases)
            for ad in adapters:
                for c in cases:
                    by_model[ad.label] += [(ad, c, s, r) for s, r, _ in cs]
    queues = list(by_model.values())
    return [q[i] for i in range(max((len(q) for q in queues), default=0)) for q in queues if i < len(q)]


def job_status(job: tuple) -> str | None:
    ad, c, s, r = job
    return read_meta(RUNS / c["layer"] / run_id(ad, c, s, r)).get("status")


def cmd_plan(args) -> None:
    jobs = plan_jobs(args, build_adapters(args.targets))
    counts: dict[tuple, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for j in jobs:
        ad, c, s, _ = j
        st = job_status(j)
        counts[(c["layer"], ad.label, s)]["done" if st == "ok" else st or "pending"] += 1
    for layer in layers_of(args):
        rows = {k: v for k, v in counts.items() if k[0] == layer}
        total = sum(sum(v.values()) for v in rows.values())
        done = sum(v.get("done", 0) for v in rows.values())
        print(f"layer {layer}: {total} runs ({done} done, {total - done} to run)")
        for (_, model, s), v in sorted(rows.items()):
            print(f"  {model:<36} {s:<15} " + "  ".join(f"{k}={n}" for k, n in sorted(v.items())))


def cmd_run(args) -> None:
    jobs = plan_jobs(args, build_adapters(args.targets))
    todo = [j for j in jobs if args.force or job_status(j) != "ok"]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(jobs)} runs selected, {len(jobs) - len([j for j in jobs if job_status(j) != 'ok'])} already done, {len(todo)} to run")
    if args.dry_run:
        for ad, c, s, r in todo:
            print(f"  {run_id(ad, c, s, r)}")
        return
    t0 = time.time()
    ex = ThreadPoolExecutor(max_workers=args.workers)
    futs = {ex.submit(run_job, ad, c, s, r, None, args.force): (ad, c, s, r) for ad, c, s, r in todo}
    try:
        for fut in as_completed(futs):
            try:
                m = fut.result()
            except Exception as e:  # noqa: BLE001
                ad, c, s, r = futs[fut]
                print(f"{run_id(ad, c, s, r):<70} crashed: {type(e).__name__}: {e}", flush=True)
                continue
            line = f"{m['run_id']:<70} {m['status']}"
            if m.get("seconds") is not None:
                line += f"  {m['seconds']:.0f}s"
            if m.get("failure"):
                line += f"  ({m['failure'][:120]})"
            print(line, flush=True)
    except KeyboardInterrupt:
        print("interrupted: cancelling queued runs (in-flight calls finish, nothing new starts)", flush=True)
        ex.shutdown(wait=False, cancel_futures=True)
        raise
    ex.shutdown(wait=True)
    print(f"done in {time.time() - t0:.0f}s")


def cmd_score(args) -> None:
    for layer in layers_of(args):
        dirs = [d for d in run_dirs(layer) if read_meta(d).get("status") == "ok"]
        if layer == "b" and dirs and replay_module() is None:
            continue

        def go(d: Path):
            case = find_case(read_meta(d))
            if case is None:
                raise RuntimeError("case not found")
            return score_run(d, case, no_judge=args.no_judge)

        judge_errors = 0
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(go, d): d for d in dirs}
            for fut in as_completed(futs):
                try:
                    judge_errors += (fut.result().get("judge") or {}).get("verdicts", {}).get("judge_error", 0)
                except Exception as e:  # noqa: BLE001
                    print(f"score failed for {futs[fut].name}: {type(e).__name__}: {e}")
        if judge_errors:
            print(f"layer {layer}: {judge_errors} judge items got no verdict (score None, left out of means); re-run score to retry")
        path, rows = write_items(layer)
        print(f"layer {layer}: scored {len(dirs)} runs -> {path.relative_to(tr.ROOT)} ({len(rows)} rows)")
        if rows:
            print_table(rows)


def cmd_status(args) -> None:
    for layer in layers_of(args):
        counts: dict[tuple, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for d in run_dirs(layer):
            m = read_meta(d)
            st = m.get("status", "unknown")
            counts[(m.get("model", "?"), m.get("strategy", "?"))][st] += 1
            if st == "ok" and (d / "scores.json").exists():
                counts[(m.get("model", "?"), m.get("strategy", "?"))]["scored"] += 1
        print(f"layer {layer}: {sum(v.get(k, 0) for v in counts.values() for k in v if k != 'scored')} runs")
        for (model, s), v in sorted(counts.items()):
            print(f"  {model:<36} {s:<15} " + "  ".join(f"{k}={n}" for k, n in sorted(v.items())))


def main(argv: list[str] | None = None) -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(tr.ROOT / ".env", override=True)
    except ImportError:
        pass
    p = argparse.ArgumentParser(prog="lab.study", description="compaction-stress-lab: layers A and B")
    p.add_argument("cmd", choices=["plan", "run", "score", "status"])
    p.add_argument("--layer", default="a", choices=["a", "b", "all"])
    p.add_argument("--targets", nargs="+", default=DEFAULT_TARGETS)
    p.add_argument("--strategy", nargs="+", choices=STRATEGIES + ["all"])
    p.add_argument("--rounds", nargs="+", type=int, choices=range(1, 5), metavar="{1,2,3,4}")
    p.add_argument("--noise", nargs="+", choices=NOISES)
    p.add_argument("--seeds", nargs="+", type=int, default=SEEDS)
    p.add_argument("--cases", nargs="+", help="layer B case ids (substring match)")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--limit", type=int, default=0, help="run at most N of the runs still to do")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-judge", action="store_true")
    p.add_argument("--force", action="store_true", help="redo finished runs (the old run is moved to .previous/)")
    args = p.parse_args(argv)
    {"plan": cmd_plan, "run": cmd_run, "score": cmd_score, "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
