"""Aggregate runs/ into results/summary.csv, reports/report.md and reports/charts/*.png. Measured data only."""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

from lab import scenario as sc  # noqa: E402
from lab import score  # noqa: E402
from lab.transcript import ROOT  # noqa: E402

RUNS = ROOT / "runs"
RESULTS = ROOT / "results"
REPORTS = ROOT / "reports"
CHARTS = REPORTS / "charts"

# Reference palette (dataviz skill): categorical slots in fixed order, blue sequential ramp, quiet ink.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
BLUE_RAMP = ["#f4f8fd", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
# CORRECT takes the green slot; the five failure kinds take slots 1-5 in order (stack order validated as a set)
OUTCOME_COLORS = dict(zip(score.OUTCOMES, [SERIES[5]] + SERIES[:5]))
STRATEGY_ORDER = ["full", "mask", "compact", "compact_guided", "compact_threshold"]
NOISE_ORDER = ["light", "heavy", "extreme"]

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE, "axes.edgecolor": GRID,
    "axes.labelcolor": INK_2, "xtick.color": INK_2, "ytick.color": INK_2, "text.color": INK, "font.size": 9,
    "axes.titlesize": 11, "axes.titleweight": "bold", "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": False, "legend.frameon": False,
})


def load_runs() -> list[dict]:
    runs = []
    for d in sorted(RUNS.glob("*")):
        mp = d / "meta.json"
        if not mp.exists():
            continue
        meta = json.loads(mp.read_text())
        sp = d / "scores.json"
        meta["scores"] = json.loads(sp.read_text()) if sp.exists() else None
        target = meta["run_id"].split(f"-{meta['strategy']}-")[0]
        meta["target"] = target
        runs.append(meta)
    return runs


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def _mean_sd(xs: list[float]) -> str:
    if not xs:
        return "not run"
    if len(xs) == 1:
        return _pct(xs[0])
    return f"{_pct(statistics.mean(xs))} ± {100 * statistics.stdev(xs):.0f}"


def _group(r: dict) -> str:
    s = r["strategy"] + (f" r{r['rounds']}" if r["rounds"] > 1 else "")
    return f"{r['target']} · {s}"


def write_csv(runs: list[dict]) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    cols = ["run_id", "target", "model", "strategy", "noise", "rounds", "rep", "fact", "traps", "salience", "position", "outcome",
            "in_summary", "answer", "truth"]
    with open(RESULTS / "summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in runs:
            if not r["scores"]:
                continue
            pos = {n["fact"]: n["position"] for n in r["scores"].get("needles", [])}
            for row in r["scores"]["quiz"]:
                w.writerow([r["run_id"], r["target"], r.get("model"), r["strategy"], r["noise"], r["rounds"], r["rep"], row["fact"],
                            "|".join(row["traps"]), row["salience"], pos.get(row["fact"], ""), row["outcome"], row.get("in_summary", ""),
                            str(row["answer"])[:200], row["truth"]])


# ------------------------------------------------------------------------------------------------ charts

def _save(fig, name: str) -> str:
    CHARTS.mkdir(parents=True, exist_ok=True)
    fig.savefig(CHARTS / name, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return f"charts/{name}"


def _heatmap(matrix: list[list[float | None]], rows: list[str], cols: list[str], title: str, name: str) -> str:
    cmap = LinearSegmentedColormap.from_list("blue", BLUE_RAMP)
    fig, ax = plt.subplots(figsize=(max(6, 0.9 * len(cols) + 3), max(3, 0.32 * len(rows) + 1.5)))
    data = [[(v if v is not None else float("nan")) for v in row] for row in matrix]
    ax.imshow(data, cmap=cmap, vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(cols)), cols, rotation=35, ha="right")
    ax.set_yticks(range(len(rows)), rows)
    ax.tick_params(length=0)
    for i, row in enumerate(matrix):
        for j, v in enumerate(row):
            ax.text(j, i, "–" if v is None else f"{100 * v:.0f}", ha="center", va="center", fontsize=8,
                    color=INK_2 if v is None else ("#ffffff" if v >= 0.55 else INK))
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xticks([x - 0.5 for x in range(1, len(cols))], minor=True)
    ax.set_yticks([y - 0.5 for y in range(1, len(rows))], minor=True)
    ax.grid(which="minor", color=SURFACE, linewidth=2)
    ax.tick_params(which="minor", length=0)
    ax.set_title(title, loc="left")
    return _save(fig, name)


def chart_traps(main: list[dict]) -> str:
    groups = sorted({_group(r) for r in main})
    traps = list(sc.TRAP_NAMES)
    agg = defaultdict(list)
    for r in main:
        for row in r["scores"]["quiz"]:
            for t in row["traps"]:
                agg[(t, _group(r))].append(row["outcome"] == "CORRECT")
    m = [[(sum(agg[(t, g)]) / len(agg[(t, g)])) if agg[(t, g)] else None for g in groups] for t in traps]
    return _heatmap(m, [f"{t} · {sc.TRAP_NAMES[t]}" for t in traps], groups, "% correct by trap type", "heatmap_traps.png")


def chart_facts(main: list[dict]) -> str:
    groups = sorted({_group(r) for r in main})
    agg = defaultdict(list)
    for r in main:
        for row in r["scores"]["quiz"]:
            agg[(row["fact"], _group(r))].append(row["outcome"] == "CORRECT")
    m = [[(sum(agg[(f.id, g)]) / len(agg[(f.id, g)])) if agg[(f.id, g)] else None for g in groups] for f in sc.FACTS]
    return _heatmap(m, [f.id for f in sc.FACTS], groups, "% correct by fact", "heatmap_facts.png")


def chart_outcomes(main: list[dict]) -> str:
    groups = sorted({_group(r) for r in main})
    counts = {g: defaultdict(int) for g in groups}
    for r in main:
        for row in r["scores"]["quiz"]:
            counts[_group(r)][row["outcome"]] += 1
    fig, ax = plt.subplots(figsize=(8, max(2.5, 0.42 * len(groups) + 1.2)))
    for i, g in enumerate(groups):
        total = sum(counts[g].values()) or 1
        left = 0.0
        for o in score.OUTCOMES:
            w = counts[g][o] / total
            if w:
                ax.barh(i, w, left=left, height=0.62, color=OUTCOME_COLORS[o], edgecolor=SURFACE, linewidth=2)
                if w >= 0.08:
                    ax.text(left + w / 2, i, f"{100 * w:.0f}", ha="center", va="center", fontsize=7,
                            color="#ffffff" if o in ("CORRECT", "STALE", "DECOY") else INK)
            left += w
    ax.set_yticks(range(len(groups)), groups)
    ax.invert_yaxis()
    ax.set_xlim(0, 1)
    ax.xaxis.set_major_formatter(lambda x, _: f"{100 * x:.0f}%")
    ax.tick_params(length=0)
    handles = [plt.Rectangle((0, 0), 1, 1, color=OUTCOME_COLORS[o]) for o in score.OUTCOMES]
    ax.legend(handles, score.OUTCOMES, ncol=6, loc="upper left", bbox_to_anchor=(0, 1.12), fontsize=8, handlelength=1)
    ax.set_title("What kind of answer each strategy produced", loc="left", pad=28)
    return _save(fig, "outcomes_stacked.png")


def _small_multiples(runs: list[dict], x_key: str, x_order: list, panels: list[str], title: str, name: str, xlabel: str) -> str | None:
    targets = sorted({r["target"] for r in runs})[: len(SERIES)]
    color = {t: SERIES[i] for i, t in enumerate(targets)}
    panels = [p for p in panels if any(r["strategy"] == p for r in runs)]
    if not panels:
        return None
    fig, axes = plt.subplots(1, len(panels), figsize=(3.6 * len(panels), 3.2), sharey=True, squeeze=False)
    for ax, p in zip(axes[0], panels):
        for t in targets:
            xs, ys = [], []
            for xv in x_order:
                acc = [r["scores"]["quiz_summary"]["accuracy"] for r in runs if r["strategy"] == p and r["target"] == t and r[x_key] == xv]
                if acc:
                    xs.append(x_order.index(xv))
                    ys.append(statistics.mean(acc))
            if xs:
                ax.plot(xs, ys, color=color[t], linewidth=2, marker="o", markersize=5, markeredgecolor=SURFACE, markeredgewidth=1.5, label=t)
        ax.set_xticks(range(len(x_order)), [str(x) for x in x_order])
        ax.set_ylim(0, 1)
        ax.yaxis.set_major_formatter(lambda y, _: f"{100 * y:.0f}%")
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_title(p, loc="left", fontsize=10)
        ax.set_xlabel(xlabel)
        ax.tick_params(length=0)
    axes[0][0].legend(loc="lower left", fontsize=7)
    fig.suptitle(title, x=0.01, ha="left", fontweight="bold")
    fig.tight_layout()
    return _save(fig, name)


def chart_position(main: list[dict]) -> str:
    order = ["short", "head", "middle", "tail"]
    strategies = [s for s in STRATEGY_ORDER if any(r["strategy"] == s for r in main)]
    agg = defaultdict(list)
    for r in main:
        pos = {n["fact"]: n["position"] for n in r["scores"].get("needles", [])}
        for row in r["scores"]["quiz"]:
            p = pos.get(row["fact"])
            if p in order:
                agg[(p, r["strategy"])].append(row["outcome"] == "CORRECT")
    fig, ax = plt.subplots(figsize=(7, 3.2))
    w = 0.8 / max(1, len(strategies))
    for j, s in enumerate(strategies):
        ys = [(sum(agg[(p, s)]) / len(agg[(p, s)])) if agg[(p, s)] else 0 for p in order]
        ax.bar([i + j * w - 0.4 + w / 2 for i in range(len(order))], ys, width=w, color=SERIES[j], edgecolor=SURFACE, linewidth=2, label=s)
    ax.set_xticks(range(len(order)), ["short output", "head of long output", "middle of long output", "tail of long output"])
    ax.set_ylim(0, 1)
    ax.yaxis.set_major_formatter(lambda y, _: f"{100 * y:.0f}%")
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.tick_params(length=0)
    ax.legend(ncol=4, loc="upper left", bbox_to_anchor=(0, 1.15), fontsize=8)
    ax.set_title("% correct by where the fact sat in its tool output", loc="left", pad=24)
    return _save(fig, "position.png")


def chart_violations(main: list[dict]) -> str:
    groups = sorted({_group(r) for r in main})
    vals = []
    for g in groups:
        v = [r["scores"]["action"]["violation_count"] for r in main if _group(r) == g]
        vals.append(statistics.mean(v) if v else 0)
    fig, ax = plt.subplots(figsize=(7, max(2.5, 0.38 * len(groups) + 1)))
    ax.barh(range(len(groups)), vals, height=0.6, color=SERIES[7], edgecolor=SURFACE, linewidth=2)
    for i, v in enumerate(vals):
        ax.text(v + 0.05, i, f"{v:.1f}", va="center", fontsize=8, color=INK_2)
    ax.set_yticks(range(len(groups)), groups)
    ax.invert_yaxis()
    ax.set_xlim(0, len(sc.VIOLATIONS))
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.tick_params(length=0)
    ax.set_title(f"Rule violations in the rollout plan (mean per run, max {len(sc.VIOLATIONS)})", loc="left")
    return _save(fig, "violations.png")


# ------------------------------------------------------------------------------------------------ markdown

def build() -> None:
    runs = load_runs()
    REPORTS.mkdir(parents=True, exist_ok=True)
    write_csv(runs)
    scored = [r for r in runs if r["scores"] and r.get("status") == "ok"]
    if not scored:
        (REPORTS / "report.md").write_text("# Compaction Stress Lab — report\n\nNo scored runs yet.\n")
        print("no scored runs yet")
        return
    main = [r for r in scored if r["rounds"] <= 1]
    # charts compare like with like: per strategy, keep only noise levels that every target running it has
    cover = defaultdict(set)
    for r in main:
        cover[(r["strategy"], r["target"])].add(r["noise"])
    common = {}
    for strat in {r["strategy"] for r in main}:
        sets = [v for (s_, _), v in cover.items() if s_ == strat]
        common[strat] = set.intersection(*sets) if sets else set()
    chart_main = [r for r in main if r["noise"] in common[r["strategy"]]]
    md = ["# Compaction Stress Lab — report", "",
          f"Runs found: {len(runs)} · completed: {len(scored)} · failed or incomplete: {len(runs) - len(scored)} (listed at the end). "
          "Every number below is computed from `runs/*/scores.json`. Cells with no runs show `not run`.", ""]

    md += ["## Accuracy by target and strategy", "", "Mean ± standard deviation over reps; main table uses single-round compaction.", "",
           "| target | strategy | noise | runs | recall accuracy | stale | decoy | invented | unknown | in summary | action values | violations / run |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    keys = sorted({(r["target"], r["strategy"], r["noise"], r["rounds"]) for r in scored},
                  key=lambda k: (k[0], STRATEGY_ORDER.index(k[1]), NOISE_ORDER.index(k[2]), k[3]))
    for tgt, strat, noise, rounds in keys:
        rs = [r for r in scored if (r["target"], r["strategy"], r["noise"], r["rounds"]) == (tgt, strat, noise, rounds)]
        qs = [r["scores"]["quiz_summary"] for r in rs]
        ins = [q["in_summary_rate"] for q in qs if "in_summary_rate" in q]
        md.append("| " + " | ".join([
            tgt, strat + (f" r{rounds}" if rounds > 1 else ""), noise, str(len(rs)),
            _mean_sd([q["accuracy"] for q in qs]),
            f"{statistics.mean(q['stale'] for q in qs):.1f}", f"{statistics.mean(q['decoy'] for q in qs):.1f}",
            f"{statistics.mean(q['invented'] for q in qs):.1f}", f"{statistics.mean(q['unknown'] for q in qs):.1f}",
            _mean_sd(ins) if ins else "n/a (opaque)" if strat.startswith("compact") else "–",
            _mean_sd([r["scores"]["action"]["value_accuracy"] for r in rs]),
            f"{statistics.mean(r['scores']['action']['violation_count'] for r in rs):.1f}",
        ]) + " |")
    md.append("")

    charts = [("Trap types", chart_traps(chart_main)), ("Every fact", chart_facts(chart_main)),
              ("Kinds of answers", chart_outcomes(chart_main)), ("Position inside tool output", chart_position(chart_main)),
              ("Rule violations", chart_violations(chart_main))]
    nc = _small_multiples(main, "noise", NOISE_ORDER, STRATEGY_ORDER, "Accuracy as tool-output noise grows", "noise_curve.png", "noise level")
    if nc:
        charts.append(("Noise", nc))
    comp = [r for r in scored if r["strategy"] in ("compact", "compact_guided", "compact_threshold")]
    rounds = sorted({r["rounds"] for r in comp})
    if rounds:  # only noise levels measured at every round count, so the curve shows rounds, not noise
        by_r = [{r["noise"] for r in comp if r["rounds"] == k} for k in rounds]
        same_noise = set.intersection(*by_r)
        comp = [r for r in comp if r["noise"] in same_noise]
        rounds = sorted({r["rounds"] for r in comp})
    if len(rounds) > 1:
        rc = _small_multiples(comp, "rounds", rounds, ["compact", "compact_guided", "compact_threshold"],
                              f"Accuracy after repeated compaction (noise: {', '.join(sorted({r['noise'] for r in comp}))})",
                              "rounds_curve.png", "compaction rounds")
        if rc:
            charts.append(("Repeated compaction", rc))
    md += ["Charts below use, per strategy, only noise levels that every target running that strategy has: "
           + "; ".join(f"{k}: {', '.join(sorted(v)) or 'none'}" for k, v in sorted(common.items())), ""]
    for title, path in charts:
        md += [f"## {title}", "", f"![{title}]({path})", ""]

    md += ["## Failure examples", "", "Confident wrong answers (STALE, DECOY, INVENTED), first 40.", "",
           "| run | fact | traps | answer | truth | outcome |", "|---|---|---|---|---|---|"]
    n = 0
    for r in scored:
        for row in r["scores"]["quiz"]:
            if row["outcome"] in ("STALE", "DECOY", "INVENTED") and n < 40:
                md.append(f"| {r['run_id']} | {row['fact']} | {' '.join(row['traps'])} | `{str(row['answer'])[:60]}` | `{row['truth'][:60]}` | {row['outcome']} |")
                n += 1
    md.append("")

    md += ["## Action-task violations", "", "| run | violations |", "|---|---|"]
    for r in scored:
        v = [k for k, hit in r["scores"]["action"]["violations"].items() if hit]
        if v:
            md.append(f"| {r['run_id']} | {', '.join(v)} |")
    md.append("")

    md += ["## Inside the Claude compaction summary", "",
           "For readable summaries, `in summary` = the current value is written in the summary text. "
           "A fact that is in the summary but still answered wrong is a recall failure, not a compaction loss.", ""]
    claude_comp = [r for r in scored if r["strategy"].startswith("compact") and r.get("provider") == "claude"]
    if claude_comp:
        md += ["| run | in summary | answered correctly | in summary but wrong | lost by compactor |", "|---|---|---|---|---|"]
        for r in claude_comp:
            q = r["scores"]["quiz"]
            md.append(f"| {r['run_id']} | {sum(x.get('in_summary', False) for x in q)} | {sum(x['outcome'] == 'CORRECT' for x in q)} | "
                      f"{sum(x.get('in_summary', False) and x['outcome'] != 'CORRECT' for x in q)} | "
                      f"{sum(not x.get('in_summary', False) for x in q)} |")
    else:
        md.append("not run")
    md.append("")
    openai_comp = [r for r in scored if r["strategy"].startswith("compact") and r.get("provider") == "openai"]
    if openai_comp:
        md += ["OpenAI compaction state is encrypted; only its size and item types can be shown:", "",
               "| run | item types after final compaction | encrypted chars |", "|---|---|---|"]
        for r in openai_comp:
            calls = json.loads((RUNS / r["run_id"] / "usage.json").read_text())
            last = [c for c in calls if c["call"].startswith("compaction")][-1:]
            if last:
                m = last[0].get("meta", {})
                types = m.get("item_types", [])
                md.append(f"| {r['run_id']} | {', '.join(f'{t}×{types.count(t)}' for t in dict.fromkeys(types))} | {m.get('encrypted_chars', '')} |")
        md.append("")

    failed = [r for r in runs if r not in scored]
    if failed:
        md += ["## Runs not counted (failed, incomplete or probe problems)", "", "| run | status | reason |", "|---|---|---|"]
        for r in failed:
            md.append(f"| {r['run_id']} | {r['status']} | {str(r.get('failure', ''))[:160]} |")
        md.append("")
    (REPORTS / "report.md").write_text("\n".join(md))
    print(f"wrote {REPORTS / 'report.md'} and {len(charts)} charts; summary at {RESULTS / 'summary.csv'}")
