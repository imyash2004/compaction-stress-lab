"""Aggregate results/items_a.jsonl, results/items_b.jsonl and results/runs_c.jsonl into
reports/data/summary.json (every number the results use). Measured data only — no prices or costs.

    python -m lab.aggregate          # writes reports/data/summary.json

Units for intervals: a run (layer A: one seed x noise level; layer B: one real session; layer C: one live task
attempt). Items inside a run are correlated, so intervals are bootstrapped over runs, never over items.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from lab import scenario as sc
from lab import stats
from lab.transcript import ROOT

RESULTS = ROOT / "results"
RUNS = ROOT / "runs"
OUT = ROOT / "reports" / "data"

STRATS = ["full", "mask", "compact", "compact_guided"]
STRAT_NAMES = {"full": "Full history", "mask": "Drop old tool outputs", "compact": "Default compaction",
               "compact_guided": "Compaction + keep-exact instructions"}
MEASURES = ["recall", "artifact", "continuation", "decision", "safety"]
NOISES = ["light", "heavy", "extreme"]
OUTCOMES = ["CORRECT", "UNKNOWN", "STALE", "DECOY", "INVENTED", "OTHER_WRONG"]


def model_name(label: str) -> str:
    if "haiku" in label:
        return "Claude Haiku 5.5"
    if "luna" in label:
        return "GPT-6 Luna"
    return label


def load_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def _ci(xs: list[float], seed: int = 0) -> dict:
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"mean": None, "lo": None, "hi": None, "n": 0}
    m, lo, hi = stats.bootstrap_ci(xs, seed=seed)
    return {"mean": round(m, 4), "lo": round(lo, 4), "hi": round(hi, 4), "n": len(xs)}


def _wilson(k: int, n: int) -> dict:
    p, lo, hi = stats.wilson(k, n)
    return {"mean": None if n == 0 else round(p, 4), "lo": round(lo, 4), "hi": round(hi, 4), "k": k, "n": n}


# ------------------------------------------------------------------------------------------------ layers A and B

ADJUDICATION = ROOT / "data" / "safety_adjudication.json"
if not ADJUDICATION.exists():  # fresh clone: the committed copy
    ADJUDICATION = ROOT / "inputs" / "safety_adjudication.json"


def adjudication() -> dict:
    """Hand verdicts on every plan the rule checker flagged (see data/safety_adjudication.json)."""
    try:
        return {k: v for k, v in json.loads(ADJUDICATION.read_text()).items() if not k.startswith("_")}
    except (OSError, json.JSONDecodeError):
        return {}


def run_table(items: list[dict]) -> dict[str, dict]:
    """run_id -> run metadata + per-measure run score (mean of the run's item scores). Safety uses the hand verdict
    where one exists: a flagged plan whose match is a false positive or borderline counts as safe."""
    adj = adjudication()
    runs: dict[str, dict] = {}
    acc: dict[tuple, list] = defaultdict(list)
    for it in items:
        r = runs.setdefault(it["run_id"], {k: it.get(k) for k in ("run_id", "model", "strategy", "rounds", "case_id",
                                                                  "size_bucket", "seed", "noise", "layer")})
        if it["measure"] == "honesty":
            if it.get("n_misses"):
                r["honesty"] = it["n_unknown"] / it["n_misses"]
            continue
        # layer B scores the next step twice; keep them apart: judged (continuation) vs file+action match (exact)
        ms = "continuation_exact" if it.get("item_id") == "next_step" else it["measure"]
        score_v = it.get("score")
        if ms == "safety" and it["run_id"] in adj:
            score_v = 0.0 if adj[it["run_id"]]["verdict"] == "violation" else 1.0
        if score_v is not None:
            acc[(it["run_id"], ms)].append(float(score_v))
    for (rid, m), xs in acc.items():
        runs[rid][m] = sum(xs) / len(xs)
    return runs


def cell(runs: list[dict], measure: str, seed: int = 0) -> dict:
    return _ci([r.get(measure) for r in runs], seed)


def select(runs: dict, **kw) -> list[dict]:
    out = []
    for r in runs.values():
        if all((r.get(k) in v) if isinstance(v, (list, tuple, set)) else r.get(k) == v for k, v in kw.items()):
            out.append(r)
    return out


def headline_runs(runs: dict, model: str, strategy: str, layer: str, noise=None) -> list[dict]:
    """Headline cell: full/mask at rounds 0, compaction after one round; layer A pools the noise levels."""
    kw = {"model": model, "strategy": strategy, "rounds": 0 if strategy in ("full", "mask") else 1}
    if layer == "a" and noise:
        kw["noise"] = noise
    return select(runs, **kw)


def layer_summary(layer: str, items: list[dict]) -> dict:
    runs = run_table(items)
    models = sorted({r["model"] for r in runs.values()})
    out: dict = {"models": models, "n_runs": len(runs), "n_items": len(items), "headline": {}, "rounds": {},
                 "retention": {}, "size": {}, "outcomes": {}, "honesty": {}}
    measures = MEASURES if layer == "a" else ["recall", "artifact", "continuation", "continuation_exact", "decision"]
    out["measures"] = measures
    for m in models:
        out["headline"][m] = {s: {ms: cell(headline_runs(runs, m, s, layer), ms) for ms in measures + ["honesty"]} for s in STRATS}
        # breaking curve: rounds 1..4 (layer A on heavy noise only, so only the round count changes)
        base = {"noise": "heavy"} if layer == "a" else {}
        out["rounds"][m] = {}
        out["retention"][m] = {}
        for s in STRATS:
            if s in ("full", "mask"):
                out["rounds"][m][s] = {ms: cell(select(runs, model=m, strategy=s, rounds=0, **base), ms) for ms in measures}
                continue
            curve = {k: {ms: cell(select(runs, model=m, strategy=s, rounds=k, **base), ms) for ms in measures} for k in (1, 2, 3, 4)}
            out["rounds"][m][s] = curve
            pts = {k: v["recall"]["mean"] for k, v in curve.items() if v["recall"]["mean"]}
            if len(pts) >= 3:
                a, r = stats.fit_retention(pts, full=True)
                out["retention"][m][s] = {"a": round(a, 4), "r": round(r, 4), "points": pts}
        if layer == "a":  # context size: same story, three noise volumes
            out["size"][m] = {s: {nz: {ms: cell(headline_runs(runs, m, s, layer, noise=nz), ms) for ms in measures}
                                  for nz in NOISES} for s in STRATS}
        else:
            out["size"][m] = {s: {b: {ms: cell(select(runs, model=m, strategy=s, size_bucket=b,
                                                      rounds=0 if s in ("full", "mask") else 1), ms) for ms in measures}
                                  for b in ("short", "medium", "long")} for s in STRATS}
    # recall outcomes (honesty): pooled over every compaction round so the mix of failure kinds is stable
    rec = [it for it in items if it["measure"] == "recall"]
    for m in models:
        out["outcomes"][m] = {}
        for s in STRATS:
            for scope, rounds in (("r1", {0, 1}), ("all", {0, 1, 2, 3, 4})):
                c = Counter(it.get("outcome") or "OTHER_WRONG" for it in rec
                            if it["model"] == m and it["strategy"] == s and it["rounds"] in rounds)
                out["outcomes"][m].setdefault(s, {})[scope] = {o: c.get(o, 0) for o in OUTCOMES}
    return out | {"runs": runs}


def breakdowns_a(items: list[dict]) -> dict:
    """Recall by trap / salience / phase / kind / position, pooled over seeds and noise levels at one round."""
    rec = [it for it in items if it["measure"] == "recall" and it["rounds"] in (0, 1)]
    out: dict = {}
    for dim in ("traps", "salience", "phase", "kind", "position"):
        tab: dict = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        for it in rec:
            keys = it.get(dim) or ["?"]
            keys = keys if isinstance(keys, list) else [keys]
            for k in keys:
                cellv = tab[(it["model"], it["strategy"])][k]
                cellv[0] += bool(it.get("correct"))
                cellv[1] += 1
        out[dim] = {f"{m}|{s}": {k: _wilson(*v) for k, v in sorted(d.items())} for (m, s), d in tab.items()}
    facts: dict = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for it in rec:
        facts[f"{it['model']}|{it['strategy']}"][it["item_id"]][0] += bool(it.get("correct"))
        facts[f"{it['model']}|{it['strategy']}"][it["item_id"]][1] += 1
    out["facts"] = {k: {f: _wilson(*v) for f, v in d.items()} for k, d in facts.items()}
    # in-summary vs answered (Claude summaries are readable): does the answer track what the summary kept?
    track = defaultdict(Counter)
    for it in items:
        if it["measure"] == "recall" and it.get("in_summary") is not None and it["strategy"].startswith("compact"):
            track[it["model"]][(bool(it["in_summary"]), bool(it.get("correct")))] += 1
    out["summary_tracking"] = {m: {f"in_summary={a}|correct={b}": n for (a, b), n in c.items()} for m, c in track.items()}
    return out


def safety_a(items: list[dict]) -> dict:
    """Per model|strategy|round: plans flagged by the rule checker (auto) and plans that really act on a superseded
    or forbidden value after hand review (unsafe), with the rule each one broke."""
    adj = adjudication()
    out: dict = {}
    for it in items:
        if it["measure"] != "safety":
            continue
        key = f"{it['model']}|{it['strategy']}|r{it['rounds']}"
        d = out.setdefault(key, {"runs": 0, "flagged": 0, "unsafe": 0, "violations": Counter(), "reviewed": []})
        d["runs"] += 1
        flagged = it.get("violation_count", 0) > 0
        d["flagged"] += flagged
        verdict = adj.get(it["run_id"], {}).get("verdict", "violation" if flagged else None)
        if verdict == "violation":
            d["unsafe"] += 1
            for v, hit in (it.get("violations") or {}).items():
                d["violations"][v] += bool(hit)
        if it["run_id"] in adj:
            d["reviewed"].append({"run_id": it["run_id"], **adj[it["run_id"]]})
    return {k: {**v, "violations": dict(v["violations"]), "rate": _wilson(v["unsafe"], v["runs"]),
                "flagged_rate": _wilson(v["flagged"], v["runs"])} for k, v in out.items()}


def worked_example(items: list[dict]) -> list[dict]:
    """Facts where full history is right, plain compaction is wrong and guided compaction is right on the same
    case (heavy noise, one round), with the readable Claude summary line that did or did not keep the value."""
    by = defaultdict(dict)
    for it in items:
        if it["measure"] == "recall" and it.get("noise") == "heavy" and it["rounds"] in (0, 1):
            by[(it["model"], it["case_id"], it["item_id"])][it["strategy"]] = it
    out = []
    for (m, case, fid), d in by.items():
        if all(s in d for s in ("full", "compact", "compact_guided")) and d["full"]["correct"] and not d["compact"]["correct"] \
                and d["compact_guided"]["correct"]:
            fact = sc.FACTS_BY_ID.get(fid)
            ex = {"model": m, "case_id": case, "fact": fid, "question": fact.question if fact else "", "truth": d["full"]["truth"],
                  "traps": d["full"].get("traps"), "answers": {s: d[s].get("answer") for s in ("full", "compact", "compact_guided")},
                  "outcomes": {s: d[s].get("outcome") for s in ("full", "compact", "compact_guided")}}
            for s in ("compact", "compact_guided"):
                p = RUNS / "a" / d[s]["run_id"] / "summary_round_1.txt"
                if p.exists():
                    ex[f"summary_{s}"] = _summary_lines(p.read_text(), fact)
            out.append(ex)
    return out


def _summary_lines(text: str, fact) -> list[str]:
    if fact is None:
        return []
    words = [w for w in re.findall(r"[A-Za-z_]{4,}", fact.question) if w.lower() not in
             {"what", "which", "does", "during", "session", "exact", "give", "first", "with", "that", "were", "from", "used", "uses"}]
    hits = [ln.strip() for ln in text.splitlines() if fact.truth in ln or any(w.lower() in ln.lower() for w in words[:4])]
    return hits[:4]


def facts_by_round(items: list[dict]) -> dict:
    """Per fact, share of seeds answering it correctly: heavy noise, per model and strategy, rounds 0 (full) to 4."""
    acc: dict = defaultdict(lambda: [0, 0])
    for it in items:
        if it["measure"] == "recall" and it.get("noise") == "heavy":
            k = (it["model"], it["strategy"], it["rounds"], it["item_id"])
            acc[k][0] += bool(it.get("correct"))
            acc[k][1] += 1
    out: dict = {}
    for (m, s_, r, f), (k, n) in acc.items():
        out.setdefault(m, {}).setdefault(s_, {}).setdefault(str(r), {})[f] = round(k / n, 3)
    return {"facts": {f.id: {"truth": f.truth, "question": f.question, "kind": f.kind, "salience": f.salience,
                             "traps": f.traps} for f in sc.FACTS if f.id != "edited_files"}, "rates": out}


# ------------------------------------------------------------------------------------------------ layer C

def condition(r: dict) -> str:
    return "none" if r["strategy"] == "none" else f"{r['strategy']}@{r.get('compact_chars')}"


def layer_c(rows: list[dict]) -> dict:
    rows = [r for r in rows if r.get("status") == "ok"]
    models = sorted({r["model"] for r in rows})
    conds = sorted({condition(r) for r in rows}, key=lambda c: (c != "none", -int(c.split("@")[1]) if "@" in c else 0, c))
    out: dict = {"models": models, "conditions": conds, "n_runs": len(rows), "cells": {}, "tasks": sorted({r["case_id"] for r in rows})}
    for m in models:
        for c in conds:
            rs = [r for r in rows if r["model"] == m and condition(r) == c]
            if not rs:
                continue
            fin = [r for r in rs if r.get("finished")]
            claimed = [r for r in rs if r.get("claimed_success")]
            comp = [r for r in rs if r.get("compactions")]
            out["cells"][f"{m}|{c}"] = {
                "runs": len(rs),
                "resolved": _wilson(sum(bool(r.get("resolved")) for r in rs), len(rs)),
                "finished": _wilson(len(fin), len(rs)),
                "steps": _ci([r.get("steps") for r in rs]),
                "compactions": _ci([r.get("compactions") for r in rs]),
                "compaction_dist": dict(Counter(r.get("compactions", 0) for r in rs)),
                "runs_with_compaction": len(comp),
                "re_reads_after_compaction": _ci([r.get("re_reads_after_compaction", 0) for r in rs]),
                "re_read_ops_after_compaction": _ci([r.get("re_read_ops_after_compaction", 0) for r in rs]),
                "repeated_failed_after_compaction": _ci([r.get("repeated_failed_after_compaction", 0) for r in rs]),
                "repeated_failed": _ci([r.get("repeated_failed_commands", 0) for r in rs]),
                "done_prefix": _wilson(sum(bool(r.get("done_prefix")) for r in fin), len(fin)),
                "files_listed_recall": _ci([(r.get("files_listed") or {}).get("recall") for r in fin]),
                "files_listed_precision": _ci([(r.get("files_listed") or {}).get("precision") for r in fin]),
                "tests_touched": _wilson(sum(bool(r.get("tests_touched_n")) for r in rs), len(rs)),
                "leftover_files": _wilson(sum(bool(r.get("leftover_files_n")) for r in fin), len(fin)),
                "constraint_ok": _wilson(sum(r.get("constraint_violations", 0) == 0 for r in fin), len(fin)),
                "claimed_success": _wilson(len(claimed), len(rs)),
                "false_success": _wilson(sum(bool(r.get("false_success")) for r in rs), len(rs)),
                "false_success_of_claims": _wilson(sum(bool(r.get("false_success")) for r in claimed), len(claimed)),
                "vendored_copy_seen": sum(bool(r.get("vendored_copy_seen")) for r in rs),
            }
    # per task: resolved count per condition (shows where compaction flips the outcome)
    per_task: dict = defaultdict(dict)
    for r in rows:
        k = f"{r['model']}|{condition(r)}"
        d = per_task[r["case_id"]].setdefault(k, [0, 0])
        d[0] += bool(r.get("resolved"))
        d[1] += 1
    out["per_task"] = per_task
    return out


# ------------------------------------------------------------------------------------------------ main

def build() -> dict:
    items_a, items_b = load_jsonl(RESULTS / "items_a.jsonl"), load_jsonl(RESULTS / "items_b.jsonl")
    rows_c = load_jsonl(RESULTS / "runs_c.jsonl")
    summary: dict = {"strategies": STRATS, "strategy_names": STRAT_NAMES, "noises": NOISES, "outcomes": OUTCOMES}
    if items_a:
        a = layer_summary("a", items_a)
        a.pop("runs")
        summary["a"] = a | {"breakdowns": breakdowns_a(items_a), "safety": safety_a(items_a), "worked_examples": worked_example(items_a)}
    if items_b:
        b = layer_summary("b", items_b)
        runs_b = b.pop("runs")
        b["cases"] = sorted({r["case_id"] for r in runs_b.values()})
        rec = [it for it in items_b if it["measure"] == "recall" and it["rounds"] in (0, 1)]
        pos: dict = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        for it in rec:
            v = pos[f"{it['model']}|{it['strategy']}"][it.get("phase") or "?"]
            v[0] += bool(it.get("correct"))
            v[1] += 1
        b["by_phase"] = {k: {p: _wilson(*v) for p, v in sorted(d.items())} for k, d in pos.items()}
        summary["b"] = b
    if rows_c:
        summary["c"] = layer_c(rows_c)
    summary["model_names"] = {m: model_name(m) for lay in ("a", "b", "c") if lay in summary for m in summary[lay]["models"]}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    if items_a:
        (OUT / "facts_by_round.json").write_text(json.dumps(facts_by_round(items_a), indent=1))
    return summary


def main() -> None:
    s = build()
    print(f"wrote {OUT / 'summary.json'}: layers {[k for k in ('a', 'b', 'c') if k in s]}")


if __name__ == "__main__":
    main()
