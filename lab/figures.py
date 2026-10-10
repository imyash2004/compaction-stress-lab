"""Charts: reports/data/summary.json -> reports/charts/*.png (2x, 1600 px wide) + *.svg, data/*.csv, index.md.

    python -m lab.figures                       # every chart the summary has data for
    python -m lab.figures --summary S --out D   # any summary file / output folder

Measured data only. Titles say what is plotted; every number in a subtitle, footer or caption is computed from the
summary at render time. Each chart function returns (file name, one-line caption), or None when its data is missing,
so a layer that has not run yet (layer B) is skipped cleanly and appears as soon as summary.json has it. Every chart
also writes its plotted values to data/<name>.csv (the table view of the chart).

Colors: the validated reference palette from lab.report. Strategy colors are fixed per strategy. Bars and legends use
the order full, compact, compact_guided, mask (not the summary order): the palette validator fails yellow (mask) next
to orange (compact) on the normal-vision floor (dE 13.7 < 15), so the two are never neighbours. In line charts, where
any two series can touch, mask is drawn as a neutral grey reference line and the remaining three colors pass the
all-pairs check.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, to_rgb  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch, PathPatch  # noqa: E402
from matplotlib.path import Path as MPath  # noqa: E402
from matplotlib.ticker import FuncFormatter, MaxNLocator  # noqa: E402

from lab import scenario as sc  # noqa: E402
from lab import stats  # noqa: E402
from lab.report import BLUE_RAMP, GRID, INK, INK_2, OUTCOME_COLORS, SERIES, SURFACE  # noqa: E402
from lab.transcript import ROOT  # noqa: E402

SUMMARY = ROOT / "reports" / "data" / "summary.json"
CHARTS = ROOT / "reports" / "charts"

DPI = 200            # 2x: an 8-inch figure is 1600 px wide
W = 8.0
MARGIN = 0.24        # inches of side margin for header and footer text
MUTED, BASELINE = "#898781", "#c3c2b7"   # palette chrome: muted ink, axis baseline
FONTS = ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"]
BAR_R = 2.9          # points: 4 CSS px rounded data end at 2x
BAR_MAX = 18.0       # points: bars are at most 24 CSS px thick

STRATS = ["full", "mask", "compact", "compact_guided"]
PLOT_ORDER = ["full", "compact", "compact_guided", "mask"]
COMPACTION = ["compact", "compact_guided"]
STRAT_COLOR = {"full": SERIES[2], "mask": SERIES[3], "compact": SERIES[1], "compact_guided": SERIES[0]}
LINE_REF_COLOR = {"full": SERIES[2], "mask": MUTED}
STRAT_NAME = {"full": "Full history", "mask": "Drop old tool outputs", "compact": "Native compaction",
              "compact_guided": "Compaction + keep-exact instructions"}
STRAT_SHORT = {"full": "Full history", "mask": "Drop old tool outputs", "compact": "Native compaction",
               "compact_guided": "Compaction + keep-exact"}

OUTCOMES = ["CORRECT", "UNKNOWN", "STALE", "DECOY", "INVENTED", "OTHER_WRONG"]
OUTCOME_NAME = {"CORRECT": "Correct", "UNKNOWN": "Said it does not know", "STALE": "Stale value", "DECOY": "Decoy value",
                "INVENTED": "Invented value", "OTHER_WRONG": "Other wrong answer"}
WRONG = ["STALE", "DECOY", "INVENTED", "OTHER_WRONG"]

MEASURE_NAME = {"recall": "Fact recall", "artifact": "Edited-files list", "continuation": "Next step",
                "continuation_exact": "Next step\n(exact file + action)", "decision": "Decision reasons",
                "safety": "Rollout plan\nbreaks no rule"}
MEASURE_NAME_B = {"continuation": "Next step\n(judged)"}
SALIENCE_NAME = {"acknowledged": "Acknowledged\nby the agent", "silent": "Silent\n(only in tool output)",
                 "user": "Stated\nby the user"}
PHASE_NAME = {"P1": "P1\nInvestigate", "P2": "P2\nFailed attempts", "P3": "P3\nFix", "P4": "P4\nVerify, plan rollout"}
SIZE_NAME = {"light": "Light noise", "heavy": "Heavy noise", "extreme": "Extreme noise",
             "short": "Short sessions", "medium": "Medium sessions", "long": "Long sessions"}
VIOLATION_NAME = {"V1_revoked_secret": "revoked secret", "V2_migrate_prod_eu_2": "migration on prod-eu-2",
                  "V3_helm_force": "helm --force", "V4_pool_over_20": "pool above 20", "V5_psycopg_3_3": "psycopg 3.3",
                  "V6_rollback_42": "rollback to rev 42", "V7_wrong_db_port": "old DB port"}
LAYER_NAME = {"a": "Controlled lab", "b": "Replayed real sessions", "c": "Live agent"}

RC = {
    "font.family": FONTS, "font.size": 8, "text.color": INK,
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": BASELINE, "axes.linewidth": 0.8, "axes.labelcolor": INK_2, "axes.labelsize": 8,
    "axes.titlesize": 9.5, "axes.titleweight": "bold", "axes.titlelocation": "left", "axes.titlecolor": INK,
    "axes.titlepad": 6, "axes.spines.top": False, "axes.spines.right": False, "axes.grid": False,
    "xtick.color": INK_2, "ytick.color": INK_2, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
    "xtick.major.size": 0, "ytick.major.size": 0, "xtick.major.pad": 4, "ytick.major.pad": 4,
    "legend.frameon": False, "svg.fonttype": "path", "hatch.color": SURFACE,
}


# ------------------------------------------------------------------------------------------------ small helpers

def _isnum(x) -> bool:
    return isinstance(x, (int, float)) and not (isinstance(x, float) and math.isnan(x))


def _pc(x) -> str:
    return f"{100 * x:.0f}%" if _isnum(x) else "n/a"


def _m(c: dict | None):
    return c.get("mean") if isinstance(c, dict) and _isnum(c.get("mean")) else None


def _rk(d: dict, k):
    return d.get(str(k), d.get(k)) if isinstance(d, dict) else None


def _mname(S: dict, m: str) -> str:
    return (S.get("model_names") or {}).get(m, m)


def _short(S: dict, m: str) -> str:
    return _mname(S, m).removeprefix("Claude ")


def _sname(S: dict, s: str) -> str:
    return (S.get("strategy_names") or {}).get(s, STRAT_NAME.get(s, s))


def _strats(present) -> list[str]:
    return [s for s in PLOT_ORDER if s in present]


def _nrange(cells) -> str:
    ns = sorted({c.get("n") for c in cells if isinstance(c, dict) and c.get("n")})
    if not ns:
        return "n/a"
    return str(ns[0]) if ns[0] == ns[-1] else f"{ns[0]}–{ns[-1]}"


def _join(parts: list[str]) -> str:
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _floor_text(who: str, vals, unit: str) -> str:
    vals = [v for v in vals if _isnum(v)]
    if not vals:
        return ""
    lo = min(vals)
    return f" {who}: {_pc(lo)} on every {unit}." if lo >= 0.995 else f" {who}: at least {_pc(lo)} on every {unit}."


def _lum(color) -> float:
    def f(u):
        return u / 12.92 if u <= 0.04045 else ((u + 0.055) / 1.055) ** 2.4
    r, g, b = to_rgb(color)
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _ink_on(fill) -> str:
    """White or ink text on a colored fill, whichever has the higher contrast."""
    lf = _lum(fill)
    return "#ffffff" if 1.05 / (lf + 0.05) >= (lf + 0.05) / (_lum(INK) + 0.05) else INK


def _table(out: Path, name: str, header: list[str], rows: list[list]) -> None:
    d = out / "data"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{name}.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(["" if v is None else (round(v, 4) if isinstance(v, float) else v) for v in r])


# ------------------------------------------------------------------------------------------------ figure frame

def _wrap(text: str, width_in: float, size: float) -> list[str]:
    if not text:
        return []
    cpl = max(24, int((width_in - 2 * MARGIN) * 72 / (0.52 * size)))
    lines: list[str] = []
    for para in text.split("\n"):
        lines += textwrap.wrap(para, cpl, break_on_hyphens=False) or [""]
    return lines


def _legend_grid(labels: list[str], avail_in: float, size: float, handle: float) -> tuple[int, int]:
    """Largest column count whose (column-major, like matplotlib) layout fits the width."""
    ws = [len(lb) * 0.53 * size / 72 + handle * size / 72 + 0.5 * size / 72 for lb in labels]
    for ncol in range(len(labels), 0, -1):
        nrows = math.ceil(len(labels) / ncol)
        cols = [ws[i * nrows:(i + 1) * nrows] for i in range(ncol)]
        total = sum(max(c) for c in cols if c) + (ncol - 1) * 1.6 * size / 72
        if total <= avail_in:
            return ncol, nrows
    return 1, len(labels)


def _frame(title: str, subtitle: str, footer: str, plot_h: float, legend: list | None = None, scale: float = 1.0,
           extra_top: float = 0.0, width: float = W):
    """Figure = header (title, subtitle, legend) + constrained plot area of plot_h inches + footer."""
    ts, ss, fs, ls = 13 * scale, 8.8 * scale, 7.2 * scale, 8.2 * scale
    lh = lambda s: s * 1.32 / 72  # noqa: E731  line height in inches
    sub, foot = _wrap(subtitle, width, ss), _wrap(footer, width, fs)
    handle = 1.6 if legend and isinstance(legend[0][0], Line2D) else 1.0
    ncol, nrows = _legend_grid([lb for _, lb in legend], width - 2 * MARGIN, ls, handle) if legend else (0, 0)
    top = 0.2 * scale
    header = top + lh(ts) + (0.05 + lh(ss) * len(sub) if sub else 0) + (0.1 + lh(ls) * 1.18 * nrows if legend else 0) \
        + 0.14 * scale + extra_top
    foot_h = 0.1 + lh(fs) * len(foot) + 0.12 if foot else 0.14
    H = header + plot_h + foot_h
    fig = plt.figure(figsize=(width, H), dpi=DPI, layout="constrained")
    left = (MARGIN - 0.07) / width
    fig.get_layout_engine().set(rect=(left, foot_h / H, 1 - 2 * left, plot_h / H), w_pad=3 / 72, h_pad=3 / 72,
                                hspace=0.04, wspace=0.03)
    x, y = MARGIN / width, H - top
    fig.text(x, y / H, title, ha="left", va="top", fontsize=ts, fontweight="bold", color=INK)
    y -= lh(ts) + 0.05
    for line in sub:
        fig.text(x, y / H, line, ha="left", va="top", fontsize=ss, color=INK_2)
        y -= lh(ss)
    if legend:
        y -= 0.1
        fig.legend([h for h, _ in legend], [lb for _, lb in legend], loc="upper left", bbox_to_anchor=(x, y / H),
                   ncol=ncol, fontsize=ls, handlelength=handle, handleheight=0.8, handletextpad=0.5,
                   columnspacing=1.6, borderaxespad=0, borderpad=0, labelspacing=0.18 * 1.0, labelcolor=INK)
    y_foot = 0.1 + lh(fs) * len(foot)
    for i, line in enumerate(foot):
        fig.text(x, (y_foot - i * lh(fs)) / H, line, ha="left", va="top", fontsize=fs, color=INK_2)
    fig._viz = {"bars": [], "post": [], "H": H, "plot_top": (foot_h + plot_h) / H}
    return fig


def _style(ax, grid: str = "y") -> None:
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    if grid:
        ax.grid(axis=grid, color=GRID, linewidth=0.7, zorder=0)
        ax.set_axisbelow(True)
    if grid == "x":
        ax.spines["bottom"].set_visible(False)
        ax.spines["left"].set_visible(True)
        ax.spines["left"].set_color(BASELINE)


def _pct(ax, axis: str = "y", top: float = 1.0, headroom: float = 0.0, step: float = 0.25) -> None:
    ticks = [i * step for i in range(int(round(top / step)) + 1)]
    fmt = FuncFormatter(lambda v, _: f"{100 * v:.0f}%")
    if axis == "y":
        ax.set_ylim(0, top + headroom)
        ax.set_yticks(ticks)
        ax.yaxis.set_major_formatter(fmt)
    else:
        ax.set_xlim(0, top + headroom)
        ax.set_xticks(ticks)
        ax.xaxis.set_major_formatter(fmt)


def _bar(fig, ax, pos: float, value, width: float, color: str, horizontal: bool = False, base: float = 0.0,
         zorder: float = 2) -> None:
    """Queue a bar; it is drawn after layout with a rounded data end and a square baseline end."""
    if _isnum(value):
        fig._viz["bars"].append((ax, pos, width, base, value, color, horizontal, zorder))


def _whisker(ax, pos: float, lo, hi, horizontal: bool = False, color: str = INK_2, lw: float = 0.9) -> None:
    if not (_isnum(lo) and _isnum(hi)) or hi - lo < 1e-9:
        return
    if horizontal:
        ax.plot([lo, hi], [pos, pos], color=color, lw=lw, solid_capstyle="butt", zorder=4)
    else:
        ax.plot([pos, pos], [lo, hi], color=color, lw=lw, solid_capstyle="butt", zorder=4)


def _round_path(x0, y0, x1, y1, r, horizontal):
    """Display-space outline of a bar: baseline end square at (x0|y0), data end at (x1|y1) rounded with radius r."""
    C = MPath
    codes = [C.MOVETO, C.LINETO, C.CURVE3, C.CURVE3, C.LINETO, C.CURVE3, C.CURVE3, C.LINETO, C.CLOSEPOLY]
    if not horizontal:
        xa, xb = sorted((x0, x1))
        s = 1 if y1 >= y0 else -1
        r = min(r, (xb - xa) / 2, abs(y1 - y0))
        v = [(xa, y0), (xa, y1 - s * r), (xa, y1), (xa + r, y1), (xb - r, y1), (xb, y1), (xb, y1 - s * r), (xb, y0), (xa, y0)]
    else:
        ya, yb = sorted((y0, y1))
        s = 1 if x1 >= x0 else -1
        r = min(r, (yb - ya) / 2, abs(x1 - x0))
        v = [(x0, ya), (x1 - s * r, ya), (x1, ya), (x1, ya + r), (x1, yb - r), (x1, yb), (x1 - s * r, yb), (x0, yb), (x0, ya)]
    return np.array(v), codes


def _draw_bars(fig, renderer) -> None:
    r, cap = renderer.points_to_pixels(BAR_R), renderer.points_to_pixels(BAR_MAX)
    for ax, pos, width, base, value, color, horizontal, z in fig._viz["bars"]:
        if horizontal:
            (x0, y0), (x1, y1) = ax.transData.transform([(base, pos - width / 2), (value, pos + width / 2)])
            if abs(x1 - x0) < 0.5:
                continue
            if abs(y1 - y0) > cap:
                mid = (y0 + y1) / 2
                y0, y1 = mid - cap / 2, mid + cap / 2
        else:
            (x0, y0), (x1, y1) = ax.transData.transform([(pos - width / 2, base), (pos + width / 2, value)])
            if abs(y1 - y0) < 0.5:
                continue
            if abs(x1 - x0) > cap:
                mid = (x0 + x1) / 2
                x0, x1 = mid - cap / 2, mid + cap / 2
        v, codes = _round_path(x0, y0, x1, y1, r, horizontal)
        ax.add_patch(PathPatch(MPath(ax.transData.inverted().transform(v), codes), facecolor=color, edgecolor="none",
                               linewidth=0, zorder=z))


def _save(fig, out: Path, name: str) -> str:
    out.mkdir(parents=True, exist_ok=True)
    fig.canvas.draw()                 # constrained layout runs here ...
    fig.set_layout_engine("none")     # ... and is frozen, so marks placed below land where they were measured
    renderer = fig.canvas.get_renderer()
    _draw_bars(fig, renderer)
    for fn in fig._viz["post"]:
        fn(renderer)
    fig.savefig(out / f"{name}.png", dpi=DPI)
    fig.savefig(out / f"{name}.svg")
    plt.close(fig)
    return f"{name}.png"


def _strategy_legend(S: dict, strats: list[str], short: bool = False) -> list:
    names = STRAT_SHORT if short else {s: _sname(S, s) for s in strats}
    return [(Patch(facecolor=STRAT_COLOR[s], edgecolor="none"), names[s]) for s in strats]


def _line_points(ax, lines, step: float = 3.0) -> list[tuple[float, float]]:
    """Display-space samples along each line, plus a ring of points around every marker."""
    pts: list[tuple[float, float]] = []
    for ln in lines:
        xs, ys = ln.get_data()
        xy = ax.transData.transform(np.column_stack([np.asarray(xs, float), np.asarray(ys, float)]))
        if ln.get_marker() not in (None, "None", "", " "):
            rad = (ln.get_markersize() / 2 + ln.get_markeredgewidth()) * ax.figure.dpi / 72
            for cx, cy in xy:
                pts += [(cx + rad * math.cos(a), cy + rad * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 12)]
        if len(xy) == 1:
            pts.append(tuple(xy[0]))
        for (x0, y0), (x1, y1) in zip(xy[:-1], xy[1:]):
            n = max(2, int(math.hypot(x1 - x0, y1 - y0) / step))
            pts += [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(n + 1)]
    return pts


def _place(ax, renderer, text: str, anchors: list, pts: list, avoid: list, **kw):
    """Put `text` at the anchor (x, y, dx_pt, dy_pt, ha, va) that covers the fewest obstacle points, overlaps no
    placed label and stays inside the axes; returns the Text and records its box in `avoid`."""
    best = None
    axb = ax.get_window_extent(renderer)
    for x, y, dx, dy, ha, va in anchors:
        t = ax.annotate(text, (x, y), xytext=(dx, dy), textcoords="offset points", ha=ha, va=va, annotation_clip=False, **kw)
        bb = t.get_window_extent(renderer)
        pb = bb.padded(renderer.points_to_pixels(1.5))
        penalty = sum(1 for px, py in pts if pb.x0 <= px <= pb.x1 and pb.y0 <= py <= pb.y1)
        penalty += 40 * sum(1 for b in avoid if pb.overlaps(b))
        if bb.x0 < axb.x0 - 2 or bb.x1 > axb.x1 + 2 or bb.y0 < axb.y0 - 2 or bb.y1 > axb.y1 + 2:
            penalty += 200
        if best is None or penalty < best[0]:
            if best:
                best[1].remove()
            best = (penalty, t, bb)
        else:
            t.remove()
    avoid.append(best[2])
    return best[1]


HALO = {"boxstyle": "square,pad=0.12", "facecolor": SURFACE, "edgecolor": "none"}


# ------------------------------------------------------------------------------------------------ 1. headline

def headline(S: dict, layer: str, out: Path):
    L = S.get(layer) or {}
    hd = L.get("headline") or {}
    models = [m for m in L.get("models", []) if m in hd]
    measures = [ms for ms in L.get("measures", []) if ms != "honesty"]
    if not models or not measures:
        return None
    strats = _strats({s for m in models for s in hd[m]})
    names = {**MEASURE_NAME, **(MEASURE_NAME_B if layer == "b" else {})}
    cells = [hd[m][s][ms] for m in models for s in strats for ms in measures if ms in hd[m].get(s, {})]

    def rec(s):
        return " / ".join(_pc(_m(hd[m].get(s, {}).get("recall"))) for m in models)
    sub = (f"Fact recall after one round ({' / '.join(_mname(S, m) for m in models)}): native compaction {rec('compact')}, "
           f"compaction + keep-exact instructions {rec('compact_guided')}, drop old tool outputs {rec('mask')}, "
           f"full history {rec('full')}.")
    foot = (f"{LAYER_NAME[layer]}. Bar: mean over runs (n = {_nrange(cells)} runs per bar); whisker: 95% bootstrap interval "
            "over runs. Full history and drop old tool outputs never compact; compaction strategies are scored after one round.")
    fig = _frame(f"{LAYER_NAME[layer]}: five measures by context strategy" if len(measures) == 5 else
                 f"{LAYER_NAME[layer]}: measures by context strategy", sub, foot, 2.3 * len(models) + 0.45,
                 legend=_strategy_legend(S, strats))
    axes = fig.subplots(len(models), 1, sharex=True, squeeze=False)[:, 0]
    k = len(strats)
    slot = 0.8 / k
    rows = []
    for ax, m in zip(axes, models):
        _style(ax)
        _pct(ax, headroom=0.13)
        for gi, ms in enumerate(measures):
            for j, s in enumerate(strats):
                c = hd[m].get(s, {}).get(ms) or {}
                x = gi - 0.4 + slot * (j + 0.5)
                v = _m(c)
                _bar(fig, ax, x, v, slot * 0.84, STRAT_COLOR[s])
                _whisker(ax, x, c.get("lo"), c.get("hi"))
                if v is None:
                    ax.text(x, 0.02, "n/a", ha="center", va="bottom", fontsize=6.5, color=MUTED, rotation=90)
                elif ms == "recall":
                    ax.text(x, max(v, c.get("hi") or v) + 0.025, f"{100 * v:.0f}", ha="center", va="bottom",
                            fontsize=6.8, color=INK_2)
                rows.append([_mname(S, m), s, ms, v, c.get("lo"), c.get("hi"), c.get("n")])
        ax.set_xlim(-0.55, len(measures) - 0.45)
        ax.set_xticks(range(len(measures)), [names.get(ms, ms) for ms in measures])
        ax.set_title(_mname(S, m))
    _table(out, f"headline_{layer}", ["model", "strategy", "measure", "mean", "lo", "hi", "n_runs"], rows)
    name = _save(fig, out, f"headline_{layer}")
    return name, f"{LAYER_NAME[layer]}, all measures after one compaction round. {sub}"


# ------------------------------------------------------------------------------------------------ 2. breaking curve

def _ret_text(r) -> str:
    if not _isnum(r):
        return ""
    return "no loss per round in the fit" if r >= 0.995 else f"keeps {100 * r:.0f}% per round"


def rounds(S: dict, layer: str, out: Path):
    L = S.get(layer) or {}
    rd = L.get("rounds") or {}
    models = [m for m in L.get("models", []) if m in rd and any(s in rd[m] for s in COMPACTION)]
    measures = [ms for ms in ("recall", "decision") if ms in L.get("measures", [])]
    if not models or not measures:
        return None
    ret = L.get("retention") or {}
    ks = [1, 2, 3, 4]

    def recall_pts(m, s):
        pts = [(k, _m((_rk(rd[m].get(s, {}), k) or {}).get("recall"))) for k in ks]
        return [(k, v) for k, v in pts if v is not None]
    parts = []
    for m in models:
        txt = []
        for s in COMPACTION:
            pts = recall_pts(m, s)
            if not pts:
                continue
            (_, v0), (k1, v1) = pts[0], pts[-1]
            r = (ret.get(m) or {}).get(s, {}).get("r")
            lab = "native" if s == "compact" else "keep-exact"
            txt.append(f"{lab} {_pc(v0)} → {_pc(v1)} by round {k1}" + (f" ({_ret_text(r)})" if _isnum(r) else ""))
        parts.append(f"{_mname(S, m)}: " + "; ".join(txt))
    sub = "Fact recall from the first to the last compaction round. " + ". ".join(parts) + "."
    cells = [(_rk(rd[m].get(s, {}), k) or {}).get(ms) for m in models for s in COMPACTION for k in ks for ms in measures]
    noise = "Heavy noise only, so only the number of rounds changes. " if layer == "a" else ""
    foot = (f"{LAYER_NAME[layer]}. {noise}Point: mean over runs (n = {_nrange(cells)} per point); band: 95% bootstrap "
            "interval. Dashed lines: strategies that never compact. Retention: least-squares fit of recall = a·r^round.")
    legend = [(Line2D([], [], color=STRAT_COLOR[s], lw=1.6, marker="o", ms=5, mec=SURFACE, mew=1.2), _sname(S, s))
              for s in COMPACTION] + \
             [(Line2D([], [], color=LINE_REF_COLOR[s], lw=1.4, ls=(0, (4, 2.5))), _sname(S, s)) for s in ("full", "mask")]
    fig = _frame(f"{LAYER_NAME[layer]}: recall and decision reasons after 1 to 4 compaction rounds"
                 if "decision" in measures else f"{LAYER_NAME[layer]}: recall after 1 to 4 compaction rounds",
                 sub, foot, 2.3 * len(measures) + 0.45, legend=legend)
    axes = fig.subplots(len(measures), len(models), sharex=True, sharey=True, squeeze=False)
    rows = []
    for i, ms in enumerate(measures):
        for j, m in enumerate(models):
            ax = axes[i][j]
            _style(ax)
            _pct(ax, headroom=0.1)
            ax.set_xlim(0.72, 5.45)
            ax.set_xticks(ks, [str(k) for k in ks])
            if i == len(measures) - 1:
                ax.set_xlabel("Compaction rounds", loc="left")
            ax.set_title(f"{_mname(S, m)} · {MEASURE_NAME[ms].replace(chr(10), ' ')}")
            refs, curves, ends = [], [], []
            for s in ("full", "mask"):
                v = _m((rd[m].get(s) or {}).get(ms))
                if v is not None:
                    ln, = ax.plot([0.75, 4.25], [v, v], color=LINE_REF_COLOR[s], lw=1.4, ls=(0, (4, 2.5)),
                                  zorder=2.2 if s == "full" else 2)
                    refs.append((s, v, ln))
                    rows.append([_mname(S, m), s, ms, 0, v, None, None])
            for s in COMPACTION:
                pts = [(k, (_rk(rd[m].get(s, {}), k) or {}).get(ms) or {}) for k in ks]
                pts = [(k, c) for k, c in pts if _m(c) is not None]
                for k, c in pts:
                    rows.append([_mname(S, m), s, ms, k, c["mean"], c.get("lo"), c.get("hi")])
                if not pts:
                    continue
                xs = [k for k, _ in pts]
                ax.fill_between(xs, [c.get("lo", c["mean"]) for _, c in pts], [c.get("hi", c["mean"]) for _, c in pts],
                                color=STRAT_COLOR[s], alpha=0.12, linewidth=0, zorder=1)
                ln, = ax.plot(xs, [c["mean"] for _, c in pts], color=STRAT_COLOR[s], lw=1.6, marker="o", ms=6,
                              mec=SURFACE, mew=1.4, zorder=5, solid_capstyle="round", solid_joinstyle="round")
                curves.append(ln)
                r = (ret.get(m) or {}).get(s, {}).get("r") if ms == "recall" else None
                ends.append((xs[-1], pts[-1][1]["mean"], _ret_text(r)))
            fig._viz["post"].append(lambda rr, ax=ax, refs=refs, curves=curves, ends=ends: _round_labels(ax, rr, refs, curves, ends))
    _table(out, f"rounds_{layer}", ["model", "strategy", "measure", "rounds", "mean", "lo", "hi"], rows)
    name = _save(fig, out, f"rounds_{layer}")
    return name, f"{LAYER_NAME[layer]}, breaking curve. {sub}"


def _round_labels(ax, renderer, refs, curves, ends) -> None:
    avoid: list = []
    # end labels: value (bold) and, on recall panels, the fitted per-round retention; nudged apart if they collide
    disp = sorted(((ax.transData.transform((x, y))[1], x, y, rt) for x, y, rt in ends), key=lambda t: t[0])
    gap = renderer.points_to_pixels(19 if any(rt for *_, rt in disp) else 10)
    placed_y: list[float] = []
    for dy_px, x, y, rt in disp:
        target = dy_px
        if placed_y and target - placed_y[-1] < gap:
            target = placed_y[-1] + gap
        placed_y.append(target)
        off = (target - dy_px) * 72 / ax.figure.dpi
        t = ax.annotate(f"{100 * y:.0f}%", (x, y), xytext=(7, off + (1 if rt else 0)), textcoords="offset points",
                        ha="left", va="bottom" if rt else "center", fontsize=8.5, fontweight="bold", color=INK,
                        bbox=HALO, zorder=7)
        avoid.append(t.get_window_extent(renderer))
        if rt:
            t2 = ax.annotate(rt, (x, y), xytext=(7, off - 1), textcoords="offset points", ha="left", va="top",
                             fontsize=6.8, color=INK_2, bbox=HALO, zorder=7)
            avoid.append(t2.get_window_extent(renderer))
    pts = _line_points(ax, curves)
    if len(refs) == 2 and abs(refs[0][1] - refs[1][1]) < 0.045:
        refs = [("both", refs[0][1], refs[0][2])]
    for s, v, ln in refs:
        label = "Full history and drop old tool outputs" if s == "both" else STRAT_NAME[s]
        others = _line_points(ax, [r[2] for r in refs if r[2] is not ln])
        anchors = [a for x in (0.78, 4.22) for a in ((x, v, 0, 4, "left" if x < 2 else "right", "bottom"),
                                                     (x, v, 0, -4, "left" if x < 2 else "right", "top"))]
        anchors += [(x, v, 0, dy, "center", "bottom" if dy > 0 else "top")
                    for x in np.linspace(1.4, 3.6, 12) for dy in (4, -4)]
        _place(ax, renderer, f"{label} {100 * v:.0f}%", anchors, pts + others, avoid, fontsize=7, color=INK_2,
               bbox=HALO, zorder=6)


# ------------------------------------------------------------------------------------------------ 3. size

def size(S: dict, layer: str, out: Path):
    L = S.get(layer) or {}
    sz = L.get("size") or {}
    models = [m for m in L.get("models", []) if sz.get(m)]
    if not models:
        return None
    buckets = ["light", "heavy", "extreme"] if layer == "a" else ["short", "medium", "long"]
    buckets = [b for b in buckets if any(b in sz[m].get(s, {}) for m in models for s in sz[m])]
    if not buckets:
        return None
    strats = _strats({s for m in models for s in sz[m]})

    def val(m, s, b):
        return (sz[m].get(s, {}).get(b) or {}).get("recall") or {}
    parts = [f"{_short(S, m)} " + " → ".join(_pc(_m(val(m, 'compact', b))) for b in buckets) for m in models]
    what = "noise volume" if layer == "a" else "session length"
    sub = f"Native compaction recall from {SIZE_NAME[buckets[0]].lower()} to {SIZE_NAME[buckets[-1]].lower()}: " \
          + "; ".join(parts) + ". Keep-exact: " + "; ".join(
              f"{_short(S, m)} " + " → ".join(_pc(_m(val(m, 'compact_guided', b))) for b in buckets) for m in models) + "."
    cells = [val(m, s, b) for m in models for s in strats for b in buckets]
    foot = (f"{LAYER_NAME[layer]}. Bar: mean over runs (n = {_nrange(cells)} per bar); whisker: 95% bootstrap interval. "
            "Compaction strategies after one round." + (" Same session and facts; only the volume of tool-output noise "
                                                        "changes." if layer == "a" else ""))
    fig = _frame(f"{LAYER_NAME[layer]}: fact recall by {what}", sub, foot, 2.75, legend=_strategy_legend(S, strats))
    axes = fig.subplots(1, len(models), sharey=True, squeeze=False)[0]
    k = len(strats)
    slot = 0.8 / k
    rows = []
    for ax, m in zip(axes, models):
        _style(ax)
        _pct(ax, headroom=0.05)
        for gi, b in enumerate(buckets):
            for j, s in enumerate(strats):
                c = val(m, s, b)
                x = gi - 0.4 + slot * (j + 0.5)
                _bar(fig, ax, x, _m(c), slot * 0.84, STRAT_COLOR[s])
                _whisker(ax, x, c.get("lo"), c.get("hi"))
                rows.append([_mname(S, m), s, b, _m(c), c.get("lo"), c.get("hi"), c.get("n")])
        ax.set_xlim(-0.55, len(buckets) - 0.45)
        ax.set_xticks(range(len(buckets)), [SIZE_NAME.get(b, b) for b in buckets])
        ax.set_title(_mname(S, m))
    _table(out, f"size_{layer}", ["model", "strategy", "bucket", "mean", "lo", "hi", "n_runs"], rows)
    name = _save(fig, out, f"size_{layer}")
    return name, f"{LAYER_NAME[layer]}, recall by {what}. {sub}"


# ------------------------------------------------------------------------------------------------ 4. traps heatmap

def traps(S: dict, layer: str, out: Path):
    L = S.get(layer) or {}
    tb = (L.get("breakdowns") or {}).get("traps") or {}
    models = [m for m in L.get("models", []) if any(k.startswith(m + "|") for k in tb)]
    if not models:
        return None
    present = {t for d in tb.values() for t in d}
    cols = [t for t in sc.TRAP_NAMES if t in present] + sorted(present - set(sc.TRAP_NAMES))
    strats = _strats({k.split("|", 1)[1] for k in tb})
    ncol = {t: max((tb[k].get(t) or {}).get("n", 0) for k in tb) for t in cols}

    def v(m, s, t):
        return _m((tb.get(f"{m}|{s}") or {}).get(t))
    comp = [(t, [v(m, "compact", t) for m in models]) for t in cols]
    comp = [(t, vs) for t, vs in comp if all(x is not None for x in vs)]
    comp.sort(key=lambda tv: sum(tv[1]) / len(tv[1]))
    low = [f"{sc.TRAP_NAMES.get(t, t)} ({', '.join(_pc(x) for x in vs)})" for t, vs in comp[:3]]
    sub = (f"Lowest recall under native compaction ({', '.join(_short(S, m) for m in models)}): {_join(low)}."
           + _floor_text("Full history", [v(m, "full", t) for m in models for t in cols], "trap"))
    foot = (f"{LAYER_NAME[layer]}. Cell: % of answers correct for planted facts carrying that trap, pooled over seeds and "
            "noise levels, compaction strategies after one round (a fact can carry several traps). n after each trap "
            "name: answers per cell (largest row).")
    rows_h = 0.3 * len(strats)
    fig = _frame(f"{LAYER_NAME[layer]}: fact recall by trap type", sub, foot, (rows_h + 0.45) * len(models) + 1.25)
    axes = fig.subplots(len(models), 1, sharex=True, squeeze=False)[:, 0]
    cmap = LinearSegmentedColormap.from_list("blue", BLUE_RAMP[:6]).with_extremes(bad=GRID)  # ends at step 500
    rows = []
    for ax, m in zip(axes, models):
        data = np.array([[v(m, s, t) if v(m, s, t) is not None else np.nan for t in cols] for s in strats], float)
        ax.imshow(np.ma.masked_invalid(data), cmap=cmap, vmin=0, vmax=1, aspect="auto", interpolation="nearest")
        for i, s in enumerate(strats):
            for j, t in enumerate(cols):
                x = data[i, j]
                ax.text(j, i, "–" if np.isnan(x) else f"{100 * x:.0f}", ha="center", va="center", fontsize=7.2,
                        color=INK_2 if np.isnan(x) else _ink_on(cmap(x)))
                c = (tb.get(f"{m}|{s}") or {}).get(t) or {}
                rows.append([_mname(S, m), s, t, sc.TRAP_NAMES.get(t, t), c.get("mean"), c.get("lo"), c.get("hi"), c.get("n")])
        ax.set_yticks(range(len(strats)), [STRAT_SHORT[s] for s in strats])
        ax.set_xticks(range(len(cols)), [f"{sc.TRAP_NAMES.get(t, t)} (n={ncol[t]})" for t in cols], rotation=40, ha="right",
                      rotation_mode="anchor")
        ax.set_xticks([x - 0.5 for x in range(1, len(cols))], minor=True)
        ax.set_yticks([y - 0.5 for y in range(1, len(strats))], minor=True)
        ax.grid(which="minor", color=SURFACE, linewidth=2.2)
        ax.tick_params(which="both", length=0)
        for sp in ax.spines.values():
            sp.set_visible(False)
        ax.set_title(_mname(S, m))
    _table(out, f"traps_{layer}", ["model", "strategy", "trap", "trap_name", "mean", "lo", "hi", "n"], rows)
    name = _save(fig, out, f"traps_{layer}")
    return name, f"{LAYER_NAME[layer]}, recall by trap type. {sub}"


# ------------------------------------------------------------------------------------------------ 5. salience and phase

def _group_bars(fig, ax, groups: list[str], strats: list[str], cell, labels: dict, value_labels: bool, rows: list, tag):
    _style(ax)
    _pct(ax, headroom=0.12 if value_labels else 0.04)
    k = len(strats)
    slot = 0.8 / k
    for gi, g in enumerate(groups):
        for j, s in enumerate(strats):
            c = cell(s, g) or {}
            x = gi - 0.4 + slot * (j + 0.5)
            v = _m(c)
            _bar(fig, ax, x, v, slot * 0.84, STRAT_COLOR[s])
            _whisker(ax, x, c.get("lo"), c.get("hi"))
            if value_labels and v is not None:
                ax.text(x, max(v, c.get("hi") or v) + 0.025, f"{100 * v:.0f}", ha="center", va="bottom", fontsize=6.8,
                        color=INK_2)
            rows.append([*tag, s, g, v, c.get("lo"), c.get("hi"), c.get("n")])
    ax.set_xlim(-0.55, len(groups) - 0.45)
    ax.set_xticks(range(len(groups)), [labels.get(g, g) for g in groups])


def salience_phase(S: dict, layer: str, out: Path):
    L = S.get(layer) or {}
    bd = L.get("breakdowns") or {}
    sal, ph = bd.get("salience") or {}, bd.get("phase") or {}
    models = [m for m in L.get("models", []) if any(f"{m}|{s}" in sal for s in COMPACTION)]
    if not models:
        return None
    strats = [s for s in COMPACTION if any(f"{m}|{s}" in sal for m in models)]
    sal_g = [g for g in ("acknowledged", "silent", "user") if any(g in sal.get(f"{m}|{s}", {}) for m in models for s in strats)]
    ph_g = sorted({g for m in models for s in strats for g in ph.get(f"{m}|{s}", {})})

    def cs(m):
        return lambda s, g: (sal.get(f"{m}|{s}") or {}).get(g)

    def cp(m):
        return lambda s, g: (ph.get(f"{m}|{s}") or {}).get(g)
    silent = {s: [f"{_pc(_m(cs(m)(s, 'silent')))} ({_short(S, m)})" for m in models] for s in strats}
    full = [_m((sal.get(f"{m}|full") or {}).get(g)) for m in models for g in sal_g]
    full = [x for x in full if x is not None]
    sub = (f"Facts that appeared only in tool output and were never mentioned: native compaction kept "
           f"{_join(silent.get('compact', []))}; keep-exact {_join(silent.get('compact_guided', []))}."
           + _floor_text("Full history", full, "group"))
    foot = (f"{LAYER_NAME[layer]}. Bar: % of recall answers correct after one compaction round, pooled over seeds and noise "
            "levels; whisker: 95% Wilson interval. Phase = where in the session the fact was planted (P1 earliest).")
    fig = _frame(f"{LAYER_NAME[layer]}: fact recall by how the fact appeared and when", sub, foot, 2.3 * len(models) + 0.6,
                 legend=_strategy_legend(S, strats))
    gs = fig.add_gridspec(len(models), 2, width_ratios=[max(1, len(sal_g)), max(1, len(ph_g))])
    rows: list = []
    first = None
    for i, m in enumerate(models):
        a1 = fig.add_subplot(gs[i, 0], sharey=first)
        first = first or a1
        a2 = fig.add_subplot(gs[i, 1], sharey=first)
        _group_bars(fig, a1, sal_g, strats, cs(m), SALIENCE_NAME, True, rows, (_mname(S, m), "salience"))
        _group_bars(fig, a2, ph_g, strats, cp(m), PHASE_NAME, False, rows, (_mname(S, m), "phase"))
        a1.set_title(f"{_mname(S, m)} · by salience")
        a2.set_title(f"{_mname(S, m)} · by session phase")
        a2.tick_params(labelleft=False)
    _table(out, f"salience_phase_{layer}", ["model", "split", "strategy", "group", "mean", "lo", "hi", "n"], rows)
    name = _save(fig, out, f"salience_phase_{layer}")
    return name, f"{LAYER_NAME[layer]}, recall by salience and phase. {sub}"


def phase_b(S: dict, out: Path):
    L = S.get("b") or {}
    bp = L.get("by_phase") or {}
    models = [m for m in L.get("models", []) if any(k.startswith(m + "|") for k in bp)]
    if not models:
        return None
    strats = _strats({k.split("|", 1)[1] for k in bp})
    groups = sorted({g for d in bp.values() for g in d})
    worst = []
    for m in models:
        d = bp.get(f"{m}|compact") or {}
        vals = [(g, _m(d.get(g))) for g in groups if _m(d.get(g)) is not None]
        if vals:
            g, x = min(vals, key=lambda t: t[1])
            worst.append(f"{_short(S, m)} {_pc(x)} ({g})")
    sub = "Lowest native-compaction recall by phase: " + (_join(worst) or "n/a") + "."
    foot = (f"{LAYER_NAME['b']}. Bar: % of recall answers correct (full history and drop old tool outputs without "
            "compaction, compaction strategies after one round); whisker: 95% Wilson interval.")
    fig = _frame(f"{LAYER_NAME['b']}: fact recall by session phase", sub, foot, 2.3 * len(models) + 0.5,
                 legend=_strategy_legend(S, strats))
    axes = fig.subplots(len(models), 1, sharex=True, sharey=True, squeeze=False)[:, 0]
    rows: list = []
    for ax, m in zip(axes, models):
        _group_bars(fig, ax, groups, strats, lambda s, g, m=m: (bp.get(f"{m}|{s}") or {}).get(g), PHASE_NAME, False, rows,
                    (_mname(S, m),))
        ax.set_title(_mname(S, m))
    _table(out, "phase_b", ["model", "strategy", "phase", "mean", "lo", "hi", "n"], rows)
    name = _save(fig, out, "phase_b")
    return name, f"{LAYER_NAME['b']}, recall by phase. {sub}"


# ------------------------------------------------------------------------------------------------ 6. outcomes

def outcomes(S: dict, layer: str, out: Path):
    L = S.get(layer) or {}
    oc = L.get("outcomes") or {}
    models = [m for m in L.get("models", []) if oc.get(m)]
    if not models:
        return None
    strats = _strats({s for m in models for s in oc[m] if sum((oc[m][s].get("all") or {}).values())})
    if not strats:
        return None
    order = [o for o in OUTCOMES if any((oc[m].get(s, {}).get("all") or {}).get(o) for m in models for s in strats)] or ["CORRECT"]
    parts = []
    for m in models:
        c = {o: sum((oc[m].get(s, {}).get("all") or {}).get(o, 0) for s in COMPACTION) for o in OUTCOMES}
        miss = sum(c[o] for o in OUTCOMES if o != "CORRECT")
        if miss:
            parts.append(f"{_short(S, m)} {_pc(c['UNKNOWN'] / miss)} of {miss}")
    sub = ("Share of compaction misses where the model said it did not know instead of giving a wrong value: "
           + (_join(parts) or "n/a") + ". Confident wrong answers are stale, decoy, invented or other values.")
    foot = (f"{LAYER_NAME[layer]}. Every recall answer, all compaction rounds pooled (1 to 4); full history and drop old "
            "tool outputs never compact. Labels inside segments are shares of that row; n = answers in the row.")
    fig = _frame(f"{LAYER_NAME[layer]}: what the recall answers were", sub, foot,
                 (0.34 * len(strats) + 0.42) * len(models) + 0.3,
                 legend=[(Patch(facecolor=OUTCOME_COLORS[o], edgecolor="none"), OUTCOME_NAME[o]) for o in order])
    axes = fig.subplots(len(models), 1, sharex=True, squeeze=False)[:, 0]
    rows = []
    for ax, m in zip(axes, models):
        _style(ax, grid="")
        ax.spines["bottom"].set_visible(False)
        ax.set_xlim(0, 1)
        ax.set_ylim(len(strats) - 0.5, -0.5)
        ax.set_yticks(range(len(strats)), [STRAT_SHORT[s] for s in strats])
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{100 * v:.0f}%"))
        ax.set_xticks([0, 0.25, 0.5, 0.75, 1.0])
        ax.set_title(_mname(S, m))
        segs = []
        for i, s in enumerate(strats):
            c = oc[m][s].get("all") or {}
            tot = sum(c.get(o, 0) for o in OUTCOMES) or 1
            left = 0.0
            for o in order:
                w = c.get(o, 0) / tot
                if w > 0:
                    ax.barh(i, w, left=left, height=0.56, color=OUTCOME_COLORS[o], edgecolor=SURFACE, linewidth=1.3, zorder=2)
                    segs.append((left, w, i, o))
                left += w
                rows.append([_mname(S, m), s, o, c.get(o, 0), c.get(o, 0) / tot])
            ax.text(1.012, i, f"n = {tot}", ha="left", va="center", fontsize=7, color=INK_2, transform=ax.get_yaxis_transform())
        fig._viz["post"].append(lambda rr, ax=ax, segs=segs: _segment_labels(ax, rr, segs))
    _table(out, f"outcomes_{layer}", ["model", "strategy", "outcome", "count", "share"], rows)
    name = _save(fig, out, f"outcomes_{layer}")
    return name, f"{LAYER_NAME[layer]}, kinds of recall answers. {sub}"


def _segment_labels(ax, renderer, segs) -> None:
    """Share labels inside stacked segments, only where the rendered text fits with padding."""
    for left, w, i, o in segs:
        t = ax.text(left + w / 2, i, f"{100 * w:.0f}%", ha="center", va="center", fontsize=6.8,
                    color=_ink_on(OUTCOME_COLORS[o]), zorder=3)
        (x0, _), (x1, _) = ax.transData.transform([(left, i), (left + w, i)])
        if t.get_window_extent(renderer).width + renderer.points_to_pixels(6) > abs(x1 - x0):
            t.remove()


# ------------------------------------------------------------------------------------------------ 7. safety

def safety(S: dict, layer: str, out: Path):
    L = S.get(layer) or {}
    sf = L.get("safety") or {}
    if not sf:
        return None
    keys = [k.split("|") for k in sf]
    models = [m for m in L.get("models", []) if any(k[0] == m for k in keys)]
    strats = _strats({k[1] for k in keys})
    if not models or not strats:
        return None

    def pooled(m, s, first_only):
        runs = unsafe = 0
        viol: dict = {}
        for k, d in sf.items():
            mm, ss, rr = k.split("|")
            if mm != m or ss != s:
                continue
            r = int(rr.lstrip("r") or 0)
            if first_only and r > 1:
                continue
            runs += d.get("runs", 0)
            unsafe += d.get("unsafe", 0)
            for v, n in (d.get("violations") or {}).items():
                viol[v] = viol.get(v, 0) + n
        p, lo, hi = stats.wilson(unsafe, runs)
        return {"runs": runs, "unsafe": unsafe, "mean": None if runs == 0 else p, "lo": lo, "hi": hi,
                "violations": {v: n for v, n in viol.items() if n}}
    total = {"runs": 0, "unsafe": 0}
    allv: dict = {}
    for m in models:
        for s in strats:
            d = pooled(m, s, False)
            total["runs"] += d["runs"]
            total["unsafe"] += d["unsafe"]
            for v, n in d["violations"].items():
                allv[v] = allv.get(v, 0) + n
    seen = ", ".join(f"{VIOLATION_NAME.get(v, v)} ×{n}" for v, n in sorted(allv.items(), key=lambda t: -t[1]))
    sub = (f"{total['unsafe']} of {total['runs']} rollout plans broke at least one stated rule, all strategies and rounds "
           f"together." + (f" Rules broken: {seen}." if seen else ""))
    foot = (f"{LAYER_NAME[layer]}. A plan counts once however many rules it breaks. Whisker: 95% Wilson interval. "
            "Full history and drop old tool outputs never compact, so both panels show the same plans for them.")
    fig = _frame(f"{LAYER_NAME[layer]}: rollout plans that broke a stated rule", sub, foot,
                 (0.3 * len(strats) + 0.5) * len(models) + 0.35, legend=_strategy_legend(S, strats))
    axes = fig.subplots(len(models), 2, sharex=True, sharey=True, squeeze=False)
    rows = []
    for i, m in enumerate(models):
        for j, (first, label) in enumerate(((True, "after one round"), (False, "all rounds pooled"))):
            ax = axes[i][j]
            _style(ax, grid="x")
            _pct(ax, axis="x", step=0.5)
            ax.set_ylim(len(strats) - 0.5, -0.5)
            ax.set_yticks(range(len(strats)), [STRAT_SHORT[s] for s in strats])
            ax.set_title(f"{_mname(S, m)} · {label}")
            for k, s in enumerate(strats):
                d = pooled(m, s, first)
                _bar(fig, ax, k, d["mean"], 0.56, STRAT_COLOR[s], horizontal=True)
                if d["runs"]:
                    _whisker(ax, k, d["lo"], d["hi"], horizontal=True)
                vs = ", ".join(f"{VIOLATION_NAME.get(v, v)}" + (f" ×{n}" if n > 1 else "") for v, n in d["violations"].items())
                ax.text(1.04, k, f"{d['unsafe']} of {d['runs']}" + (f" · {vs}" if vs else ""), ha="left", va="center",
                        fontsize=6.8, color=INK_2, transform=ax.get_yaxis_transform())
                rows.append([_mname(S, m), s, label, d["unsafe"], d["runs"], d["mean"], d["lo"], d["hi"],
                             "; ".join(f"{v}={n}" for v, n in d["violations"].items())])
    _table(out, f"safety_{layer}", ["model", "strategy", "scope", "unsafe", "runs", "rate", "lo", "hi", "violations"], rows)
    name = _save(fig, out, f"safety_{layer}")
    return name, f"{LAYER_NAME[layer]}, rule violations in the rollout plan. {sub}"


# ------------------------------------------------------------------------------------------------ 8. live agent

def _cond_strategy(c: str) -> str:
    return "full" if c == "none" else c.split("@")[0]


def _cond_name(c: str, short: bool = True) -> str:
    if c == "none":
        return "No compaction"
    s, _, th = c.partition("@")
    base = {"compact": "Native compaction", "compact_guided": "Keep-exact compaction"}.get(s, s)
    if th.isdigit():
        n = int(th)
        return f"{base} · {n // 1000}k" if n % 1000 == 0 else f"{base} · {n:,}"
    return base


LIVE_COLS = [("resolved", "Resolved\n(hidden tests pass)", "rate", "Outcome"),
             ("re_reads_after_compaction", "Files read again\nafter compaction", "count", "Per run, after a compaction"),
             ("repeated_failed_after_compaction", "Failed command\nrepeated", "count", "Per run, after a compaction"),
             ("constraint_ok", "Task constraints\nkept", "rate", "Rules and honesty"),
             ("false_success", "Claimed success,\ntests fail", "rate", "Rules and honesty")]


def _cond_brief(c: str) -> str:
    if c == "none":
        return "no compaction"
    s, _, th = c.partition("@")
    base = {"compact": "native", "compact_guided": "keep-exact"}.get(s, s)
    return f"{base} at {int(th) // 1000}k" if th.isdigit() and int(th) % 1000 == 0 else f"{base} at {th}" if th else base


def live(S: dict, out: Path):
    C = S.get("c") or {}
    cells = C.get("cells") or {}
    models = [m for m in C.get("models", []) if any(k.startswith(m + "|") for k in cells)]
    conds = [c for c in C.get("conditions", []) if any(f"{m}|{c}" in cells for m in models)]
    if not models or not conds:
        return None
    strat_of = {c: _cond_strategy(c) for c in conds}
    leg_strats = [s for s in PLOT_ORDER if s in set(strat_of.values())]
    legend = [(Patch(facecolor=STRAT_COLOR[s], edgecolor="none"),
               {"full": "No compaction", "compact": "Native compaction",
                "compact_guided": "Compaction + keep-exact instructions"}[s]) for s in leg_strats]
    res = [f"{_mname(S, m)} " + " / ".join(_pc(_m((cells.get(f"{m}|{c}") or {}).get("resolved"))) for c in conds)
           for m in models]
    comp_conds = [c for c in conds if c != "none"]
    comp = [f"{_short(S, m)} " + ", ".join(f"{(cells.get(f'{m}|{c}') or {}).get('runs_with_compaction', 0)}/"
                                         f"{(cells.get(f'{m}|{c}') or {}).get('runs', 0)}" for c in comp_conds)
            for m in models]
    sub = (f"Resolved ({' / '.join(_cond_brief(c) for c in conds)}): " + "; ".join(res) + "."
           + (f" Runs that compacted at least once ({', '.join(_cond_brief(c) for c in comp_conds)}): "
              + "; ".join(comp) + "." if comp_conds else ""))
    ns = sorted({cells[k].get("runs", 0) for k in cells})
    nr = str(ns[0]) if ns[0] == ns[-1] else f"{ns[0]}–{ns[-1]}"
    foot = (f"{LAYER_NAME['c']} on {len(C.get('tasks', []))} SWE-bench Verified tasks (n = {nr} runs per row). "
            "Rates: 95% Wilson interval; per-run counts: mean with 95% bootstrap interval, averaged over all runs including "
            "runs that never compacted. 24k / 12k: context size in characters that triggers compaction.")
    nrows = len(models) * (len(conds) + 1)
    fig = _frame(f"{LAYER_NAME['c']}: SWE-bench outcomes and behavior after compaction", sub, foot,
                 0.255 * nrows + 0.8, legend=legend, extra_top=0.24)
    axes = fig.subplots(1, len(LIVE_COLS), sharey=True, gridspec_kw={"width_ratios": [1.1, 1, 1, 1.1, 1.1]})
    ypos, labels, heads = {}, [], []
    y = 0
    for m in models:
        heads.append((y, _mname(S, m)))
        y += 1
        for c in conds:
            ypos[(m, c)] = y
            labels.append((y, _cond_name(c)))
            y += 1
    rows, titles = [], []
    for ax, (key, title, kind, _) in zip(axes, LIVE_COLS):
        _style(ax, grid="x")
        ax.set_ylim(y - 0.5, -0.5)
        his = [(cells.get(f"{m}|{c}", {}).get(key) or {}).get("hi") for m in models for c in conds]
        top = max([h for h in his if _isnum(h)] + [0.1])
        if kind == "rate":
            _pct(ax, axis="x", step=0.5)
        else:
            ax.set_xlim(0, top * 1.05)
            ax.xaxis.set_major_locator(MaxNLocator(3, steps=[1, 2, 5, 10], integer=top >= 2))
            ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
        titles.append(ax.set_title(title, fontsize=7.8, linespacing=1.15))
        for (m, c), yy in ypos.items():
            d = cells.get(f"{m}|{c}")
            cd = (d or {}).get(key) or {}
            v = _m(cd)
            na = d is None or (kind == "count" and c == "none")
            if na:
                ax.text(0.01 * (ax.get_xlim()[1]), yy, "n/a", ha="left", va="center", fontsize=6.6, color=MUTED)
                continue
            _bar(fig, ax, yy, v, 0.62, STRAT_COLOR[strat_of[c]], horizontal=True)
            _whisker(ax, yy, cd.get("lo"), cd.get("hi"), horizontal=True)
            txt = _pc(v) if kind == "rate" else ("0" if v == 0 else f"{v:.2f}" if v < 1 else f"{v:.1f}")
            xr = max(v or 0, cd.get("hi") or 0)
            ax.annotate(txt, (xr, yy), xytext=(3, 0), textcoords="offset points", ha="left", va="center", fontsize=6.6,
                        color=INK_2, annotation_clip=False)
            rows.append([_mname(S, m), c, key, v, cd.get("lo"), cd.get("hi"), cd.get("n"), cd.get("k")])
    axes[0].set_yticks([yy for yy, _ in labels], [lb for _, lb in labels])
    for yy, name in heads:
        axes[0].annotate(name, (0, yy), xycoords=("axes fraction", "data"), xytext=(-4, 0), textcoords="offset points",
                         ha="right", va="center", fontsize=8, fontweight="bold", color=INK, annotation_clip=False)
    fig._viz["post"].append(lambda rr: _block_heads(fig, axes, titles, rr))
    _table(out, "live_c", ["model", "condition", "metric", "value", "lo", "hi", "n", "k"], rows)
    name = _save(fig, out, "live_c")
    return name, f"{LAYER_NAME['c']} on SWE-bench tasks. {sub}"


def _block_heads(fig, axes, titles, renderer) -> None:
    """Block headers above groups of columns, with a hairline under each."""
    inv = fig.transFigure.inverted()
    top = max(t.get_window_extent(renderer).y1 for t in titles)
    blocks: dict = {}
    for ax, (*_, block) in zip(axes, LIVE_COLS):
        bb = ax.get_window_extent(renderer)
        lo, hi = blocks.get(block, (bb.x0, bb.x1))
        blocks[block] = (min(lo, bb.x0), max(hi, bb.x1))
    pad = renderer.points_to_pixels(5)
    for block, (x0, x1) in blocks.items():
        (fx0, fy), (fx1, _) = inv.transform([(x0, top + pad), (x1, top + pad)])
        fig.add_artist(Line2D([fx0, fx1], [fy, fy], color=BASELINE, lw=0.7, transform=fig.transFigure))
        fy2 = inv.transform((0, top + pad + renderer.points_to_pixels(3)))[1]
        fig.text(fx0, fy2, block, ha="left", va="bottom", fontsize=7.4, color=INK_2, fontweight="bold")


# ------------------------------------------------------------------------------------------------ 9. hero

def hero(S: dict, out: Path):
    layer = next((lay for lay in ("a", "b") if (S.get(lay) or {}).get("rounds")), None)
    if layer is None:
        return None
    L = S[layer]
    rd = L["rounds"]
    models = [m for m in L.get("models", []) if any(s in rd.get(m, {}) for s in COMPACTION)]
    if not models:
        return None
    ks = [1, 2, 3, 4]

    def series(m, s):
        pts = [(k, (_rk(rd[m].get(s, {}), k) or {}).get("recall") or {}) for k in ks]
        return [(k, c) for k, c in pts if _m(c) is not None]
    bits = []
    for m in models:
        d, g = series(m, "compact"), series(m, "compact_guided")
        t = f"{_mname(S, m)}: {_pc(d[0][1]['mean'])} → {_pc(d[-1][1]['mean'])}" if d else ""
        if g:
            t += f" by default, {_pc(g[0][1]['mean'])} → {_pc(g[-1][1]['mean'])} with keep-exact instructions"
        bits.append(t)
    nfacts = len(next(iter(((L.get("breakdowns") or {}).get("facts") or {}).values()), {})) or None
    what = f"Share of {nfacts} planted facts" if nfacts else "Share of planted facts"
    sub = f"{what} the agent still recalls after each compaction round. " + ". ".join(bits) + "."
    cells = [c for m in models for s in COMPACTION for _, c in series(m, s)]
    noise = " heavy tool-output noise," if layer == "a" else ""
    foot = (f"{LAYER_NAME[layer]}: one incident-debugging session,{noise} n = {_nrange(cells)} runs per point; "
            "band = 95% bootstrap interval.")
    legend = [(Line2D([], [], color=STRAT_COLOR["compact"], lw=3, marker="o", ms=8, mec=SURFACE, mew=2), "Native compaction (default)"),
              (Line2D([], [], color=STRAT_COLOR["compact_guided"], lw=3, marker="o", ms=8, mec=SURFACE, mew=2),
               "Compaction + keep-exact instructions"),
              (Line2D([], [], color=STRAT_COLOR["full"], lw=2.2, ls=(0, (4, 2.5))), "Full history (no compaction)")]
    fig = _frame("Fact recall after repeated context compaction", sub, foot, 5.0, legend=legend, scale=1.55)
    axes = fig.subplots(1, len(models), sharey=True, squeeze=False)[0]
    rows = []
    for ax, m in zip(axes, models):
        _style(ax)
        _pct(ax, headroom=0.12, step=0.25)
        ax.tick_params(labelsize=13)
        ax.set_xlim(0.05, 4.95)
        ax.set_xticks(ks, [str(k) for k in ks])
        ax.set_xlabel("Compaction rounds", fontsize=13, loc="left")
        ax.set_title(_mname(S, m), fontsize=16, pad=10)
        fv = _m((rd[m].get("full") or {}).get("recall"))
        if fv is not None:
            ax.plot([0.6, 4.4], [fv, fv], color=STRAT_COLOR["full"], lw=2.2, ls=(0, (4, 2.5)), zorder=2)
            ax.annotate(f"Full history {_pc(fv)}", (0.62, fv), xytext=(0, 4), textcoords="offset points", ha="left",
                        va="bottom", fontsize=12.5, color=INK_2)
            rows.append([_mname(S, m), "full", 0, fv, None, None])
        ends = []
        for s in COMPACTION:
            pts = series(m, s)
            if not pts:
                continue
            xs = [k for k, _ in pts]
            ys = [c["mean"] for _, c in pts]
            ax.fill_between(xs, [c.get("lo", c["mean"]) for _, c in pts], [c.get("hi", c["mean"]) for _, c in pts],
                            color=STRAT_COLOR[s], alpha=0.12, linewidth=0, zorder=1)
            ax.plot(xs, ys, color=STRAT_COLOR[s], lw=3, marker="o", ms=10, mec=SURFACE, mew=2.2, zorder=5,
                    solid_capstyle="round", solid_joinstyle="round")
            ends.append((xs[0], ys[0], "start"))
            ends.append((xs[-1], ys[-1], "end"))
            rows += [[_mname(S, m), s, k, c["mean"], c.get("lo"), c.get("hi")] for k, c in pts]
        fig._viz["post"].append(lambda rr, ax=ax, ends=ends: _hero_labels(ax, rr, ends))
    _table(out, "hero", ["model", "strategy", "rounds", "recall", "lo", "hi"], rows)
    name = _save(fig, out, "hero")
    return name, f"Hero: {sub}"


def _hero_labels(ax, renderer, ends) -> None:
    gap = renderer.points_to_pixels(17)
    for side in ("start", "end"):
        pts = sorted(((ax.transData.transform((x, y))[1], x, y) for x, y, sd in ends if sd == side), key=lambda t: t[0])
        prev = None
        for py, x, y in pts:
            target = py if prev is None or py - prev >= gap else prev + gap
            prev = target
            off = (target - py) * 72 / ax.figure.dpi
            ax.annotate(f"{100 * y:.0f}%", (x, y), xytext=(-10 if side == "start" else 10, off), textcoords="offset points",
                        ha="right" if side == "start" else "left", va="center", fontsize=15, fontweight="bold",
                        color=INK, bbox=HALO, zorder=7)


# ------------------------------------------------------------------------------------------------ build

def build(summary: dict | str | Path = SUMMARY, out: Path = CHARTS) -> list[tuple[str, str]]:
    S = summary if isinstance(summary, dict) else json.loads(Path(summary).read_text())
    out = Path(out)
    plt.rcParams.update(RC)
    jobs = [lambda: headline(S, "a", out), lambda: headline(S, "b", out), lambda: rounds(S, "a", out),
            lambda: rounds(S, "b", out), lambda: size(S, "a", out), lambda: size(S, "b", out), lambda: traps(S, "a", out),
            lambda: salience_phase(S, "a", out), lambda: phase_b(S, out), lambda: outcomes(S, "a", out),
            lambda: outcomes(S, "b", out), lambda: safety(S, "a", out), lambda: live(S, out), lambda: hero(S, out)]
    made = [r for r in (job() for job in jobs) if r]
    lines = ["# Charts", "",
             "Generated by `python -m lab.figures` from `reports/data/summary.json`. Each chart is a PNG at 2x (1600 px wide) "
             "with an SVG copy; the plotted values are in `data/<chart>.csv`. Layers without data are skipped.", ""]
    for name, cap in made:
        stem = name.removesuffix(".png")
        lines.append(f"- [{name}]({name}) ([svg]({stem}.svg), [data](data/{stem}.csv)): {cap}")
    lines.append("")
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.md").write_text("\n".join(lines))
    return made


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--summary", default=str(SUMMARY))
    ap.add_argument("--out", default=str(CHARTS))
    a = ap.parse_args()
    made = build(a.summary, Path(a.out))
    print(f"wrote {len(made)} charts to {a.out}: {', '.join(n for n, _ in made)}")


if __name__ == "__main__":
    main()
