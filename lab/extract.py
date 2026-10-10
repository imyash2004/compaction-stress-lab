"""How much of OpenAI's encrypted compacted memory can be recovered by asking the model?

The compaction item is encrypted (Fernet-style `gAAAA…` token, server-side key) and rejected if edited, so it
cannot be decrypted. This module estimates its content behaviourally: compact once, save that exact state, run
many different elicitation probes against it, and record which planted facts each probe brings back. A fact
recovered by ANY open-recall probe was in the memory; a fact no probe recovers is very likely gone.

    python -m lab.extract compact --model gpt-6-luna --strategy compact --noise heavy
    python -m lab.extract probe   --probes results/extract/probes.json --workers 8 --max-usd 15
    python -m lab.extract analyze
"""

from __future__ import annotations

import argparse
import json
import re
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from lab import providers, score
from lab import scenario as sc
from lab import transcript as tr

OUT = tr.ROOT / "results" / "extract"

def present(fact: sc.Fact, text: str) -> bool:
    """Same check as the compaction-summary analysis (keyword-anchored for short or common values)."""
    return score.in_summary(fact, text)


def forbidden_values() -> list[str]:
    vals = set()
    for f in sc.FACTS:
        for v in [f.truth, *f.stale, *f.decoys, *f.alts]:
            for part in v.split(","):
                part = part.strip()
                if len(part) >= 3 and part not in ("--wait",):
                    vals.add(part)
    return sorted(vals, key=len, reverse=True)


def leaks(prompt: str) -> list[str]:
    low = prompt.lower()
    return [v for v in forbidden_values() if v.lower() in low]


# ------------------------------------------------------------------------------------------------ commands

def cmd_compact(a) -> None:
    t = tr.load_or_build(a.noise, a.seed)
    ad = providers.OpenAIAdapter(a.model)
    state = ad.convert(tr.all_turns(t))
    res = ad.compact(state, sc.GUIDED_INSTRUCTIONS if a.strategy == "compact_guided" else None)
    if not res.ok:
        raise SystemExit(f"compaction failed: {res.error}")
    d = OUT / "states" / f"{ad.label}-{a.strategy}-{a.noise}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(res.state))
    (d / "meta.json").write_text(json.dumps({"model": a.model, "strategy": a.strategy, "noise": a.noise, "seed": a.seed,
                                             "usage": res.usage, "cost_usd": res.cost_usd, **res.meta}, indent=1))
    print(f"saved {d.relative_to(tr.ROOT)}  items={res.meta['item_types']}  encrypted_chars={res.meta['encrypted_chars']}  ${res.cost_usd:.4f}")


def cmd_probe(a) -> None:
    probes = json.loads(open(a.probes).read())
    states = sorted(p for p in (OUT / "states").iterdir() if (p / "state.json").exists())
    if a.only:
        states = [s for s in states if any(o in s.name for o in a.only)]
    spent = [0.0]
    lock = threading.Lock()
    jobs = []
    for sd in states:
        meta = json.loads((sd / "meta.json").read_text())
        ad = providers.OpenAIAdapter(meta["model"])
        state = json.loads((sd / "state.json").read_text())
        for p in probes:
            out = sd / "probes" / f"{p['id']}.json"
            if not out.exists():
                jobs.append((ad, state, p, out))
    print(f"{len(jobs)} probe calls to run over {len(states)} saved states")

    def go(job):
        ad, state, p, out = job
        with lock:
            if spent[0] >= a.max_usd:
                return None
        r = ad.ask(state, p["prompt"], max_tokens=a.max_tokens)
        with lock:
            spent[0] += r.cost_usd
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"probe": p, "ok": r.ok, "stop": r.stop, "error": r.error, "text": r.text,
                                   "usage": r.usage, "cost_usd": r.cost_usd}, indent=1))
        return out

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, fut in enumerate(as_completed([ex.submit(go, j) for j in jobs]), 1):
            fut.result()
            if i % 25 == 0:
                print(f"  {i}/{len(jobs)} done, ${spent[0]:.2f}", flush=True)
    print(f"spent ${spent[0]:.2f}" + ("  (budget reached, some probes skipped)" if spent[0] >= a.max_usd else ""))


def cmd_analyze(a) -> None:
    probes = {p["id"]: p for p in json.loads(open(a.probes).read())}
    lines = ["# Recovering OpenAI's compacted memory", "",
             "Each saved compacted state was probed with every elicitation prompt. A fact counts as recovered when its "
             "current value appears in the answer (keyword-anchored for short numbers). Probes are checked so that none "
             "contains an answer value.", ""]
    summary = {}
    for sd in sorted(p for p in (OUT / "states").iterdir() if (p / "probes").exists()):
        meta = json.loads((sd / "meta.json").read_text())
        rows = [json.loads(f.read_text()) for f in sorted((sd / "probes").glob("*.json"))]
        ok = [r for r in rows if r["text"]]
        hits = {r["probe"]["id"]: {f.id for f in sc.FACTS if present(f, r["text"])} for r in ok}
        union = set().union(*hits.values()) if hits else set()
        per_fact = {f.id: sum(f.id in h for h in hits.values()) for f in sc.FACTS}
        fam = defaultdict(list)
        for pid, h in hits.items():
            fam[probes.get(pid, {}).get("family", "?")].append(len(h))
        best = sorted(hits.items(), key=lambda kv: -len(kv[1]))[:10]
        blocked = [r for r in rows if not r["text"]]
        summary[sd.name] = {"union": sorted(union), "never": [f.id for f in sc.FACTS if f.id not in union], "per_fact": per_fact,
                            "probes_run": len(rows), "empty": len(blocked)}
        lines += [f"## {sd.name}", "",
                  f"- encrypted item: {meta.get('encrypted_chars')} chars; retained items: {', '.join(meta.get('item_types', []))}",
                  f"- probes run: {len(rows)} (empty/blocked answers: {len(blocked)})",
                  f"- **recovered by at least one probe: {len(union)}/30**",
                  f"- best single probe: {len(best[0][1]) if best else 0}/30",
                  f"- **never recovered by any probe:** {', '.join(f.id for f in sc.FACTS if f.id not in union) or 'none'}",
                  "", "Average facts per probe, by technique family:", "",
                  "| family | probes | mean facts | max facts |", "|---|---|---|---|"]
        for fname, v in sorted(fam.items()):
            lines.append(f"| {fname} | {len(v)} | {sum(v) / len(v):.1f} | {max(v)} |")
        lines += ["", "Top probes:", "", "| probe | family | technique | facts |", "|---|---|---|---|"]
        for pid, h in best:
            p = probes.get(pid, {})
            lines.append(f"| {pid} | {p.get('family', '?')} | {p.get('technique', '')[:60]} | {len(h)} |")
        lines += ["", "How many probes recovered each fact:", "", "| fact | traps | probes recovering it |", "|---|---|---|"]
        for f in sc.FACTS:
            lines.append(f"| {f.id} | {' '.join(f.traps)} | {per_fact[f.id]}/{len(ok)} |")
        lines.append("")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.md").write_text("\n".join(lines))
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1))
    for name, s in summary.items():
        print(f"{name}: recovered {len(s['union'])}/30 by any probe; never: {s['never']}")
    print(f"wrote {(OUT / 'report.md').relative_to(tr.ROOT)}")


def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(tr.ROOT / ".env", override=True)
    except ImportError:
        pass
    p = argparse.ArgumentParser(prog="lab.extract")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compact")
    c.add_argument("--model", required=True)
    c.add_argument("--strategy", default="compact", choices=["compact", "compact_guided"])
    c.add_argument("--noise", default="heavy", choices=list(sc.LEVELS))
    c.add_argument("--seed", type=int, default=7)
    r = sub.add_parser("probe")
    r.add_argument("--probes", default=str(OUT / "probes.json"))
    r.add_argument("--only", nargs="*")
    r.add_argument("--workers", type=int, default=8)
    r.add_argument("--max-tokens", type=int, default=12000)
    r.add_argument("--max-usd", type=float, default=15.0)
    n = sub.add_parser("analyze")
    n.add_argument("--probes", default=str(OUT / "probes.json"))
    a = p.parse_args()
    {"compact": cmd_compact, "probe": cmd_probe, "analyze": cmd_analyze}[a.cmd](a)


if __name__ == "__main__":
    main()
