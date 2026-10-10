"""CLI for compaction-stress-lab.

    python -m lab.run build   [--noise light heavy extreme] [--seed 7]
    python -m lab.run check   [--targets claude openai]
    python -m lab.run run     --targets claude openai --strategy all --noise heavy [--rounds 1 2] [--reps 3] [--dry-run]
    python -m lab.run report
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from lab import probe, providers, score
from lab import scenario as sc
from lab import transcript as tr

try:
    from dotenv import load_dotenv
except ImportError:  # optional
    load_dotenv = None

RUNS = tr.ROOT / "runs"
RESULTS = tr.ROOT / "results"
STRATEGIES = ["full", "mask", "compact", "compact_guided", "compact_threshold"]
COMPACT = {"compact", "compact_guided", "compact_threshold"}
NOISES = list(sc.LEVELS)
EST_OUT = {"compaction": 8000, "quiz": 6000, "action": 6000}
SUMMARY_TOKENS_EST = 5000


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tokens_of(obj) -> int:
    return round(len(obj if isinstance(obj, str) else json.dumps(obj)) / tr.CHARS_PER_TOKEN)


class BudgetExceeded(Exception):
    pass


class Budget:
    """Reserve each call's estimate before it starts, settle with the real cost after; safe across worker threads."""

    def __init__(self, max_usd: float):
        self.max_usd = max_usd
        self.spent = 0.0
        self.pending = 0.0
        self.lock = threading.Lock()
        self.stop = threading.Event()

    def reserve(self, est: float, what: str) -> float:
        with self.lock:
            if self.stop.is_set():
                raise BudgetExceeded(f"{what}: stopped")
            if self.spent + self.pending + est > self.max_usd:
                raise BudgetExceeded(f"{what}: estimated ${est:.2f} would exceed budget "
                                     f"(${self.spent:.2f} spent + ${self.pending:.2f} in flight of ${self.max_usd:.2f})")
            self.pending += est
            return est

    def settle(self, est: float, usd: float) -> None:
        with self.lock:
            self.pending -= est
            self.spent += usd

    def add(self, usd: float) -> None:
        self.settle(0.0, usd)


def write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj, indent=1, default=str))


# ================================================================================================ one run

def run_id(adapter, strategy: str, t: dict, rounds: int, rep: int) -> str:
    r = rounds if strategy in COMPACT else 0
    return f"{getattr(adapter, 'run_label', adapter.label)}-{strategy}-{t['noise']}-r{r}-s{t['seed']}-rep{rep}"


THRESHOLD_MARGIN = 100


def ondemand_trigger(adapter, t: dict, rounds: int, k: int) -> int | None:
    """Input-token count at which the matching on-demand run compacted (round k), minus a small margin, so a
    threshold run compacts at the same point and on the same amount of context."""
    label = getattr(adapter, "run_label", adapter.label)
    d = RUNS / f"{label}-compact-{t['noise']}-r{rounds}-s{t['seed']}-rep1"
    try:
        u = json.loads((d / f"compaction_round_{k}.json").read_text())["usage"]
    except (OSError, KeyError, json.JSONDecodeError):
        return None
    it = u.get("iterations") or []
    n = sum(i.get("input_tokens", 0) for i in it if i.get("type") == "compaction") if it else u.get("input_tokens", 0)
    return max(1_000, n - THRESHOLD_MARGIN) if n else None


def _status(d: Path) -> str | None:
    try:
        return json.loads((d / "meta.json").read_text()).get("status")
    except (OSError, json.JSONDecodeError):
        return None


def run_one(adapter, t: dict, strategy: str, rounds: int, rep: int, budget: Budget, force: bool) -> dict:
    rid = run_id(adapter, strategy, t, rounds, rep)
    d = RUNS / rid
    if not force and _status(d) == "ok":
        return {"run_id": rid, "status": "skipped (already done)"}
    backup = RUNS / ".previous" / rid
    if d.exists():  # keep the old result until the new run has produced something
        shutil.rmtree(backup, ignore_errors=True)
        backup.parent.mkdir(parents=True, exist_ok=True)
        d.rename(backup)
    meta = {"run_id": rid, "started_at": now(), "strategy": strategy, "noise": t["noise"], "seed": t["seed"],
            "rounds": rounds if strategy in COMPACT else 0, "rep": rep, "scenario": sc.SCENARIO_ID,
            "transcript_chars": t["chars"], "transcript_approx_tokens": t["approx_tokens"], **adapter.describe(),
            "sdk": _sdk_versions(), "status": "running"}
    write(d / "meta.json", meta)
    calls, errors = [], []

    def record(name: str, res: providers.CallResult, est: float) -> None:
        budget.settle(est, res.cost_usd)
        calls.append({"call": name, "ok": res.ok, "stop": res.stop, "latency_s": round(res.latency_s, 2),
                      "cost_usd": res.cost_usd, "usage": res.usage, "error": res.error, "meta": res.meta})
        if res.error:
            errors.append({"call": name, "error": res.error})

    def finish(status: str) -> dict:
        meta.update(status=status, finished_at=now(), cost_usd=round(sum(c["cost_usd"] for c in calls), 4),
                    latency_s=round(sum(c["latency_s"] for c in calls), 1))
        write(d / "meta.json", meta)
        write(d / "usage.json", calls)
        write(d / "errors.json", errors)
        if status != "budget_stopped":
            shutil.rmtree(backup, ignore_errors=True)
        return meta

    try:
        phases = t["phases"]
        summary = None
        if strategy == "full":
            state = adapter.convert(tr.all_turns(t))
        elif strategy == "mask":
            state = adapter.convert(tr.mask_old_tool_outputs(tr.all_turns(t)))
        else:
            instructions = sc.GUIDED_INSTRUCTIONS if strategy == "compact_guided" else None
            points = set(range(len(phases) - rounds, len(phases)))
            state = []
            for i, ph in enumerate(phases):
                state = state + adapter.convert(ph["turns"])
                if i not in points:
                    continue
                k = len([p for p in points if p <= i])
                before = tokens_of(state)
                est = budget.reserve(adapter.estimate(before, EST_OUT["compaction"]), f"{rid} compaction {k}")
                if strategy == "compact_threshold":
                    trig = ondemand_trigger(adapter, t, rounds, k)
                    if trig is None:
                        meta["failure"] = "no matching on-demand run to take the trigger size from (run strategy 'compact' first)"
                        return finish("harness_error")
                    meta.setdefault("thresholds", []).append(trig)
                    res = adapter.compact(state, None, mode="threshold", threshold=trig)
                else:
                    res = adapter.compact(state, instructions)
                record(f"compaction_round_{k}", res, est)
                write(d / f"compaction_round_{k}.json", {"after_phase": ph["id"], "approx_tokens_before": before, "ok": res.ok,
                                                          "stop": res.stop, "error": res.error, "usage": res.usage,
                                                          "meta": res.meta, "raw": res.raw})
                if not res.ok:
                    meta["failure"] = f"compaction round {k} after {ph['id']}: {res.error}"
                    return finish("compaction_failed")
                if res.summary is not None:
                    summary = res.summary
                    write(d / f"summary_round_{k}.txt", res.summary)
                state = res.state
                meta.setdefault("compactions", []).append({"round": k, "after_phase": ph["id"], "approx_tokens_before": before,
                                                           "approx_tokens_after": tokens_of(state)})
        meta["probe_state_approx_tokens"] = tokens_of(state)
        text = tr.full_text(t)

        est = budget.reserve(adapter.estimate(tokens_of(state), EST_OUT["quiz"]), f"{rid} quiz")
        q = adapter.ask(state, probe.quiz_prompt())
        record("probe_quiz", q, est)
        write(d / "probe_quiz.json", {"ok": q.ok, "stop": q.stop, "error": q.error, "text": q.text, "usage": q.usage, "raw": q.raw})
        answers = probe.parse_json(q.text) or {}
        write(d / "answers.json", answers)

        est = budget.reserve(adapter.estimate(tokens_of(state), EST_OUT["action"]), f"{rid} action")
        a = adapter.ask(state, probe.action_prompt())
        record("probe_action", a, est)
        write(d / "probe_action.json", {"ok": a.ok, "stop": a.stop, "error": a.error, "text": a.text, "usage": a.usage, "raw": a.raw})
        plan = probe.parse_json(a.text)

        rows = score.score_quiz(answers, text, summary=summary)
        scores = {"quiz": rows, "quiz_summary": score.summarize(rows), "quiz_parsed": bool(answers),
                  "quiz_ok": q.ok, "action": score.score_action(plan, a.text), "action_ok": a.ok,
                  "needles": t["needles"]}
        write(d / "scores.json", scores)
        if q.ok and a.ok and answers:
            return finish("ok")
        meta["failure"] = q.error or a.error or ("quiz reply was not parseable JSON" if not answers else "probe problem")
        return finish("probe_problem")
    except BudgetExceeded as e:
        if not calls and backup.exists():  # nothing new was spent: put the previous result back
            shutil.rmtree(d, ignore_errors=True)
            backup.rename(d)
        else:
            meta["failure"] = str(e)
            finish("budget_stopped")
        raise
    except Exception as e:  # noqa: BLE001
        errors.append({"call": "harness", "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()})
        meta["failure"] = f"{type(e).__name__}: {e}"
        return finish("harness_error")


def _sdk_versions() -> dict:
    import anthropic
    import openai
    return {"anthropic": anthropic.__version__, "openai": openai.__version__}


# ================================================================================================ estimate (dry run)

def estimate_run(adapter, t: dict, strategy: str, rounds: int) -> float:
    phases = t["phases"]
    ph_tokens = [tokens_of(tr.full_text({"phases": [p]})) for p in phases]
    total = 0.0
    if strategy == "full":
        state = sum(ph_tokens)
    elif strategy == "mask":
        state = tokens_of(json.dumps(tr.mask_old_tool_outputs(tr.all_turns(t))))
    else:
        points = set(range(len(phases) - rounds, len(phases)))
        carried = 0
        for i, n in enumerate(ph_tokens):
            carried += n
            if i in points:
                total += adapter.estimate(carried, EST_OUT["compaction"])
                carried = SUMMARY_TOKENS_EST
        state = carried
    total += adapter.estimate(state, EST_OUT["quiz"]) + adapter.estimate(state, EST_OUT["action"])
    return total


# ================================================================================================ commands

def cmd_build(args) -> None:
    for n in args.noise:
        t = tr.build(n, args.seed)
        problems = tr.validate(t)
        p = tr.save(t)
        print(f"{n:<8} {t['chars']:>9,} chars  ~{t['approx_tokens']:>7,} tokens  phases={tr.phase_chars(t)}  -> {p.relative_to(tr.ROOT)}")
        if problems:
            print("   PROBLEMS:", *problems, sep="\n   - ")
            sys.exit(1)


def _toy_turns() -> list[dict]:
    return [
        {"role": "user", "text": "Find the service port in the config and remember it."},
        {"role": "assistant", "text": "Reading the config.", "calls": [{"id": "call_001", "name": "read_file", "args": {"path": "config.yaml"}}]},
        {"role": "tool", "results": [{"id": "call_001", "output": "service:\n  port: 7311\n  region: eu-north-9", "is_error": False}]},
        {"role": "assistant", "text": "The service listens on port 7311 in region eu-north-9.", "calls": []},
    ]


def cmd_check(args) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS / "check.json"
    results = json.loads(out_path.read_text()) if out_path.exists() else {}
    for kind, dep in providers.make_targets(args.targets):
        try:
            ad = providers.build_adapter(kind, dep)
        except Exception as e:  # noqa: BLE001
            print(f"[{kind}:{dep}] cannot create client: {e}")
            continue
        r = {"checked_at": now(), **ad.describe()}
        r["compaction_mode"] = None  # set only when a mode is proven to work
        tiny = ad.tiny()
        r["reachable"] = tiny.ok
        r["reachable_detail"] = tiny.error or tiny.text[:80]
        print(f"[{ad.label}] reachable: {tiny.ok} {tiny.error or tiny.text[:60]!r}")
        if not tiny.ok:
            results[ad.label] = r
            continue
        toy = ad.convert(_toy_turns())
        modes = ["on_demand", "threshold"] if kind == "claude" else ["responses_compact"]
        for mode in modes:
            state = toy
            if kind == "claude":
                ad.compaction_mode = mode
                if mode == "threshold":  # needs >= 50k input tokens: use a real transcript prefix
                    t = tr.load_or_build("heavy", 7)
                    state = ad.convert(t["phases"][0]["turns"] + t["phases"][1]["turns"])
            c = ad.compact(state, None)
            ok = c.ok
            detail = c.error or f"summary={bool(c.summary)} meta={c.meta}"
            follow = None
            if ok:
                f = ad.ask(c.state, "In one line: what port and region did we find? If you do not know, say UNKNOWN.", max_tokens=4000)
                follow = {"ok": f.ok, "text": f.text[:200], "error": f.error}
                ok = f.ok
            r[f"compaction_{mode}"] = {"ok": ok, "detail": detail, "follow_up": follow, "cost_usd": c.cost_usd}
            print(f"[{ad.label}] compaction {mode}: {'OK' if ok else 'FAILED'}  {detail[:160]}")
            if follow:
                print(f"[{ad.label}]   follow-up after compaction: {follow['text'][:120]!r} {follow['error']}")
            if ok:
                r["compaction_mode"] = mode
                break
        if r["compaction_mode"] is None:
            print(f"[{ad.label}] no working compaction mode: compact strategies will be skipped")
        for noise in NOISES:
            t = tr.load_or_build(noise, 7)
            ctx = 1_000_000 if kind == "claude" else 922_000
            r.setdefault("fits", {})[noise] = t["approx_tokens"] + 40_000 < ctx
        results[ad.label] = r
    write(out_path, results)
    print(f"saved {out_path.relative_to(tr.ROOT)}")


def cmd_run(args) -> None:
    strategies = list(dict.fromkeys(STRATEGIES if "all" in args.strategy else args.strategy))
    noises = list(dict.fromkeys(NOISES if "all" in args.noise else args.noise))
    rounds_list = sorted(set(args.rounds))
    targets = providers.make_targets(args.targets)
    adapters = []
    for kind, dep in targets:
        try:
            adapters.append(providers.build_adapter(kind, dep))
        except Exception as e:  # noqa: BLE001
            print(f"skip {kind}:{dep}: {e}")
    transcripts = {n: tr.load_or_build(n, args.seed) for n in noises}
    jobs = []
    for ad in adapters:
        for strategy in strategies:
            if strategy in COMPACT and getattr(ad, "compaction_mode", "x") is None:
                print(f"skip {ad.label} {strategy}: no working compaction mode (run `check`)")
                continue
            for n in noises:
                for rounds in (rounds_list if strategy in COMPACT else [0]):
                    if rounds > len(transcripts[n]["phases"]):
                        continue
                    for rep in range(1, args.reps + 1):
                        jobs.append((ad, transcripts[n], strategy, rounds, rep))
    # interleave models so parallel workers spread load across providers' rate limits
    by_model: dict[str, list] = {}
    for j in jobs:
        by_model.setdefault(j[0].label, []).append(j)
    queues = list(by_model.values())
    jobs = [q[i] for i in range(max((len(q) for q in queues), default=0)) for q in queues if i < len(q)]
    est = sum(estimate_run(ad, t, s, r) for ad, t, s, r, _ in jobs)
    print(f"{len(jobs)} runs planned, estimated cost ${est:,.2f} (budget ${args.max_usd:,.2f})")
    if args.dry_run:
        by = {}
        for ad, t, s, r, _ in jobs:
            by[ad.label] = by.get(ad.label, 0) + estimate_run(ad, t, s, r)
        for k, v in sorted(by.items()):
            print(f"  {k:<28} ${v:,.2f}")
        return
    budget = Budget(args.max_usd)
    t0 = time.time()

    def go(job):
        ad, t, s, r, rep = job
        return run_one(ad, t, s, r, rep, budget, args.force)

    stopped = False
    ex = ThreadPoolExecutor(max_workers=args.workers)
    futs = {ex.submit(go, j): j for j in jobs}
    try:
        for fut in as_completed(futs):
            try:
                m = fut.result()
            except BudgetExceeded as e:
                if not stopped:
                    print(f"BUDGET STOP: {e}")
                stopped = True
                continue
            line = f"{m['run_id']:<60} {m['status']}"
            if m.get("cost_usd") is not None:
                sc_path = RUNS / m["run_id"] / "scores.json"
                acc = json.loads(sc_path.read_text())["quiz_summary"]["accuracy"] if sc_path.exists() else None
                line += f"  acc={acc}  ${m['cost_usd']:.3f}  {m.get('latency_s', 0):.0f}s"
                if m.get("failure"):
                    line += f"  ({m['failure'][:100]})"
            print(line, flush=True)
    except KeyboardInterrupt:
        print("interrupted: cancelling queued runs (in-flight calls finish, nothing new starts)", flush=True)
        budget.stop.set()
        ex.shutdown(wait=False, cancel_futures=True)
        raise
    ex.shutdown(wait=True)
    print(f"done in {time.time() - t0:.0f}s, spent ${budget.spent:,.2f}")


def cmd_report(args) -> None:
    from lab import report
    report.build()


def main(argv: list[str] | None = None) -> None:
    if load_dotenv:
        load_dotenv(tr.ROOT / ".env", override=True)
    p = argparse.ArgumentParser(prog="lab.run", description="compaction-stress-lab")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="generate transcripts (no API calls)")
    b.add_argument("--noise", nargs="+", default=NOISES, choices=NOISES)
    b.add_argument("--seed", type=int, default=7)
    c = sub.add_parser("check", help="verify credentials and compaction support with tiny calls")
    c.add_argument("--targets", nargs="+", default=["claude", "openai"])
    r = sub.add_parser("run", help="run the experiment matrix")
    r.add_argument("--targets", nargs="+", default=["claude", "openai"],
                   help="claude | openai (all OPENAI_DEPLOYMENTS) | openai:<deployment> | all")
    r.add_argument("--strategy", nargs="+", default=["all"], choices=STRATEGIES + ["all"])
    r.add_argument("--noise", nargs="+", default=["heavy"], choices=NOISES + ["all"])
    r.add_argument("--rounds", nargs="+", type=int, default=[1], choices=range(1, 5), metavar="{1,2,3,4}")
    r.add_argument("--reps", type=int, default=1)
    r.add_argument("--seed", type=int, default=7)
    r.add_argument("--workers", type=int, default=1)
    r.add_argument("--max-usd", type=float, default=None)
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--force", action="store_true", help="re-run runs that already have scores")
    sub.add_parser("report", help="charts + reports/report.md from runs/")
    args = p.parse_args(argv)
    if args.cmd == "run" and args.max_usd is None:
        import os
        args.max_usd = float(os.environ.get("MAX_USD_PER_EXPERIMENT", "50"))
    {"build": cmd_build, "check": cmd_check, "run": cmd_run, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
