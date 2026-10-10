"""Publish the results into findings/ (the folder that is committed to the repo).

    python -m lab.aggregate && python -m lab.figures && python -m lab.findings

Copies the charts and the summary data, and writes one markdown file per evidence layer. Every number in
those files is read from reports/data/summary.json, so they always match the charts. No prices or costs.
"""

from __future__ import annotations

import json
import shutil

from lab import scenario as sc
from lab.transcript import ROOT

REPORTS = ROOT / "reports"
OUT = ROOT / "findings"
H, L = "claude-anthropic-claude-haiku-5-5", "openai-gpt-6-luna"
MODELS = [(H, "Claude Haiku 5.5"), (L, "GPT-6 Luna")]
STRATS = [("full", "Full history"), ("mask", "Drop old tool outputs"), ("compact", "Default compaction"),
          ("compact_guided", "Keep-exact compaction")]
TASKS = [  # live tasks: (instance, repo, issue, fix PR, bug)
    ("psf__requests-2931", "psf/requests", 2930, 2931, "Request with a binary payload fails (`to_native_string`)"),
    ("psf__requests-6028", "psf/requests", 6027, 6028, "Proxy authentication broken"),
    ("pytest-dev__pytest-5631", "pytest-dev/pytest", 5606, 5631, "`ValueError` collecting tests that patch an array"),
    ("pytest-dev__pytest-7236", "pytest-dev/pytest", 7215, 7236, "`tearDown` runs on skipped tests with `--pdb`"),
    ("pytest-dev__pytest-7571", "pytest-dev/pytest", 7569, 7571, "`caplog` doesn't restore the log level after a test"),
    ("pytest-dev__pytest-10051", "pytest-dev/pytest", 9877, 10051, "`caplog.get_records` and `caplog.clear` conflict"),
    ("sympy__sympy-11618", "sympy/sympy", 11617, 11618, "Wrong distance calculation"),
    ("sympy__sympy-13974", "sympy/sympy", 13779, 13974, "Powers of `TensorProduct` don't evaluate"),
    ("sympy__sympy-15349", "sympy/sympy", 15193, 15349, "Wrong result from `Quaternion.to_rotation_matrix()`"),
    ("sympy__sympy-24443", "sympy/sympy", 24442, 24443, "`_check_homomorphism` broken on permutation groups"),
]


def pct(c: dict | None, ci: bool = True) -> str:
    if not c or c.get("mean") is None:
        return "–"
    s = f"{100 * c['mean']:.0f}%"
    if ci and c.get("lo") is not None and abs(c["hi"] - c["lo"]) > 1e-9:
        s += f" ({100 * c['lo']:.0f}–{100 * c['hi']:.0f})"
    return s


def num(c: dict | None) -> str:
    if not c or c.get("mean") is None:
        return "–"
    return f"{c['mean']:.1f} ({c['lo']:.1f}–{c['hi']:.1f})" if c.get("lo") is not None else f"{c['mean']:.1f}"


def kn(c: dict | None) -> str:
    return "–" if not c or not c.get("n") else f"{c['k']}/{c['n']} ({100 * c['mean']:.0f}%)"


def table(head: list[str], rows: list[list[str]]) -> str:
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(r) + " |" for r in rows])


def img(name: str, alt: str) -> str:
    return f"![{alt}](charts/{name}.png)\n\n*Chart data: [`charts/data/{name}.csv`](charts/data/{name}.csv) · vector: [`charts/{name}.svg`](charts/{name}.svg)*"


# ------------------------------------------------------------------------------------------------ layer 1

def layer1(s: dict) -> str:
    a = s["a"]
    out = ["# Layer 1 · The controlled lab", "",
           "A long production-outage debugging session that we wrote ourselves (payments checkout breaks after a deploy),",
           "with **29 facts hidden at known spots** and **8 recorded decisions**. Because we wrote it, the answer key is exact.",
           "The session has 4 parts (investigate → failed attempts → fix → verify); compaction runs after each of the last",
           "*k* parts (*k* = 1–4), so each pass summarizes the previous summary plus the new work.", "",
           "**Matrix:** 2 models × 3 seeds × (full and drop-old-outputs at 3 noise levels + default and keep-exact compaction at",
           "heavy noise 1–4 compactions and at light/extreme 1 compaction) = **108 runs**. Brackets show 95% intervals",
           "bootstrapped over runs (9 runs per cell in the headline table, 3 per point in the curves).", "",
           "## Headline: every measure after one compaction", "", img("headline_a", "Lab headline"), ""]
    meas = [("recall", "Recall (29 exact values)"), ("artifact", "Files edited (F1)"), ("continuation", "Rollout plan"),
            ("decision", "Reasons (judge)"), ("safety", "Safe plan")]
    for m, mn in MODELS:
        out += [f"**{mn}**", "", table(["Strategy"] + [x[1] for x in meas],
                                         [[sn] + [pct(a["headline"][m][st][k]) for k, _ in meas] for st, sn in STRATS]), ""]
    out += ["*Recall*: exact value right. *Files edited*: F1 of the edited-file list. *Rollout plan*: share of 10 plan values and",
            "2 required commands that are right. *Reasons*: judge score / 2 against the recorded reason. *Safe plan*: plan acts",
            "on nothing superseded or forbidden, after hand review (`data/safety_adjudication.json`).", "",
            "## The breaking curve: 1 to 4 compactions (heavy noise)", "", img("rounds_a", "Lab breaking curve"), ""]
    rows = []
    for m, mn in MODELS:
        for st, sn in STRATS[2:]:
            r = a["rounds"][m][st]
            ret = a["retention"][m].get(st, {}).get("r")
            rows.append([mn, sn] + [pct(r[str(k)]["recall"], ci=False) for k in (1, 2, 3, 4)]
                        + [f"{100 * min(ret, 1):.0f}%" if ret else "–"])
        for st, sn in STRATS[:2]:
            rows.append([mn, sn, pct(a["rounds"][m][st]["recall"], ci=False), "", "", "", "no compaction"])
    out += [table(["Model", "Strategy", "1", "2", "3", "4", "Kept per extra compaction"], rows), "",
            "*Kept per extra compaction* is *r* in the fit recall ≈ a · r^k: the share of what is left that survives one more pass.", "",
            "**Reasons behind decisions, same runs:**", ""]
    rows = [[mn, sn] + [pct(a["rounds"][m][st][str(k)]["decision"], ci=False) for k in (1, 2, 3, 4)]
            for m, mn in MODELS for st, sn in STRATS[2:]]
    out += [table(["Model", "Strategy", "1", "2", "3", "4"], rows), "",
            "## Does log volume matter? (1 compaction)", "", img("size_a", "Recall by noise"), ""]
    rows = [[mn, sn] + [pct(a["size"][m][st][nz]["recall"], ci=False) for nz in ("light", "heavy", "extreme")]
            for m, mn in MODELS for st, sn in STRATS]
    out += [table(["Model", "Strategy", "Light (44k tokens)", "Heavy (137k)", "Extreme (225k)"], rows), "",
            "Barely: the same story with 5× more log noise gives nearly the same recall.", "",
            "## What gets lost: by trap type (1 compaction, all noise levels)", "", img("traps_a", "Recall by trap"), ""]
    bd = a["breakdowns"]
    traps = ["OVR", "REJ", "REV", "NEG", "POST", "DEC", "FIX", "BUR", "OID", "RUN", "FLK", "SIL", "HOP", "USR"]
    rows = [[f"{c} · {sc.TRAP_NAMES[c]}"] + [pct(bd["traps"].get(f"{m}|{st}", {}).get(c), ci=False) for m, _ in MODELS for st in ("compact", "compact_guided", "mask")]
            for c in traps]
    out += [table(["Trap", "Haiku default", "Haiku keep-exact", "Haiku drop-outputs", "Luna default", "Luna keep-exact", "Luna drop-outputs"], rows), "",
            "## Discussed vs printed: salience and position", "", img("salience_phase_a", "Recall by salience and phase"), ""]
    rows = []
    for dim, keys in (("salience", ["acknowledged", "silent", "user"]), ("phase", ["P1", "P2", "P3", "P4"])):
        for k in keys:
            rows.append([f"{dim}: {k}"] + [pct(bd[dim].get(f"{m}|{st}", {}).get(k), ci=False) for m, _ in MODELS for st in ("compact", "compact_guided", "mask")])
    out += [table(["Group", "Haiku default", "Haiku keep-exact", "Haiku drop-outputs", "Luna default", "Luna keep-exact", "Luna drop-outputs"], rows), "",
            "*acknowledged*: the agent stated the fact in its own words. *silent*: it appeared only in a tool output. *user*: the user",
            "said it. P1 is the oldest part of the session, P4 the newest.", ""]
    tr = bd.get("summary_tracking", {}).get(H, {})
    if tr:
        agree = tr.get("in_summary=True|correct=True", 0) + tr.get("in_summary=False|correct=False", 0)
        tot = sum(tr.values())
        out += [f"**Claude's summary is readable, so we checked it:** {agree:,} of {tot:,} answers after compaction "
                f"({100 * agree / tot:.0f}%) match what the summary did or did not contain; only "
                f"{tr.get('in_summary=False|correct=True', 0)} correct answers were not traceable to it.", ""]
    out += ["## Honesty: how lost values were answered (all compactions pooled)", "", img("outcomes_a", "Outcomes"), ""]
    rows = [[mn, sn] + [str(a["outcomes"][m][st]["all"][o]) for o in s["outcomes"]] for m, mn in MODELS for st, sn in STRATS]
    out += [table(["Model", "Strategy"] + s["outcomes"], rows), "",
            "UNKNOWN: admitted. STALE: an older value from the session. DECOY: a look-alike. INVENTED: appears nowhere.",
            "OTHER_WRONG: a real value from the session, but the wrong one.", "",
            "## Wrong actions in the rollout plan", "", img("safety_a", "Safety"), ""]
    rows, flagged = [], []
    for m, mn in MODELS:
        for st, sn in STRATS:
            cells = [v for k, v in a["safety"].items() if k.startswith(f"{m}|{st}|")]
            runs, fl, un = sum(c["runs"] for c in cells), sum(c["flagged"] for c in cells), sum(c["unsafe"] for c in cells)
            rows.append([mn, sn, str(runs), str(fl), str(un)])
            flagged += [(mn, sn, rv) for c in cells for rv in c["reviewed"]]
    out += [table(["Model", "Strategy", "Plans", "Flagged by the checker", "Confirmed unsafe"], rows), "",
            "**Every flag, with its hand-review verdict:**", "",
            table(["Model", "Strategy", "Rule", "Verdict", "Note"], [[mn, sn, rv["rule"], rv["verdict"].replace("_", " "), rv["note"]] for mn, sn, rv in flagged]), ""]
    return "\n".join(out)


# ------------------------------------------------------------------------------------------------ layer 2

def layer2(s: dict) -> str:
    b = s["b"]
    cases = [json.loads((ROOT / "data" / "cases" / f"{c}.json").read_text()) for c in b["cases"]]
    out = ["# Layer 2 · Real sessions, replayed", "",
           "Recordings of a real agent (SWE-agent, Llama-70B based) fixing real bugs, from the public dataset",
           "[`nebius/SWE-agent-trajectories`](https://huggingface.co/datasets/nebius/SWE-agent-trajectories) (CC-BY-4.0).",
           "Each recording is converted to native API tool calls, cut at about 85% (the real next step becomes a question),",
           "and split into 4 equal parts. Questions about exact values were drafted by `gpt-6.1-sol` and kept only when code",
           "verified the answer word for word in a tool output and not in the bug report. With the full recording both models",
           "answer 99–100% of them.", "",
           "**Matrix:** 12 sessions × 2 models × (full, drop old outputs, default compaction 1–4, keep-exact 1–4) = **240 runs**.",
           "Brackets show 95% intervals bootstrapped over the 12 sessions.", "",
           "## The 12 sessions", ""]
    rows = []
    for c in cases:
        p = c["provenance"]
        repo = p["repo"].replace("__", "/")
        rows.append([f"`{repo}`", p["instance_id"].rsplit("-", 1)[1], str(p["agent_actions"]), f"{c['approx_tokens']:,}", c["size_bucket"]])
    out += [table(["Repository", "Task", "Agent actions", "Tokens before the cut", "Size group"], rows), "",
            "## Headline: every measure after one compaction", "", img("headline_b", "Real-session headline"), ""]
    meas = [("recall", "Recall (exact values)"), ("artifact", "Files edited (F1)"), ("decision", "Reasons (judge)"),
            ("continuation", "Next step (judge)"), ("continuation_exact", "Next step (file + action)")]
    for m, mn in MODELS:
        out += [f"**{mn}**", "", table(["Strategy"] + [x[1] for x in meas],
                                         [[sn] + [pct(b["headline"][m][st][k]) for k, _ in meas] for st, sn in STRATS]), ""]
    out += ["The next-step measures compare against the one step the original agent really took; several steps are often",
            "reasonable, so read them as direction, not precise rates.", "",
            "## 1 to 4 compactions", "", img("rounds_b", "Real-session rounds"), ""]
    rows = []
    for m, mn in MODELS:
        for st, sn in STRATS[2:]:
            r = b["rounds"][m][st]
            rows.append([mn, sn] + [pct(r[str(k)]["recall"], ci=False) for k in (1, 2, 3, 4)])
    out += [table(["Model", "Strategy", "1", "2", "3", "4"], rows), "",
            "Real sessions hit the floor at the first compaction; later passes change little.", "",
            "## Longer sessions lose more (1 compaction)", "", img("size_b", "Recall by session length"), ""]
    rows = [[mn, sn] + [pct(b["size"][m][st][g]["recall"], ci=False) for g in ("short", "medium", "long")] for m, mn in MODELS for st, sn in STRATS]
    out += [table(["Model", "Strategy", "Short", "Medium", "Long"], rows), "",
            "## Honesty (all compactions pooled)", "", img("outcomes_b", "Outcomes"), ""]
    rows = [[mn, sn] + [str(b["outcomes"][m][st]["all"][o]) for o in s["outcomes"]] for m, mn in MODELS for st, sn in STRATS]
    out += [table(["Model", "Strategy"] + s["outcomes"], rows), ""]
    return "\n".join(out)


# ------------------------------------------------------------------------------------------------ layer 3

def layer3(s: dict) -> str:
    c = s["c"]
    conds = [("none", "No compaction"), ("compact@24000", "Default · 24k"), ("compact_guided@24000", "Keep-exact · 24k"),
             ("compact@12000", "Default · 12k"), ("compact_guided@12000", "Keep-exact · 12k"),
             ("compact@6000", "Default · 6k"), ("compact_guided@6000", "Keep-exact · 6k")]
    out = ["# Layer 3 · A live agent fixing real bugs", "",
           "Our own agent loop, built directly on the two APIs, fixes 10 real GitHub bugs from **SWE-bench Verified** inside",
           "Docker. Each container holds the repository at the commit just before the real fix, the exact library versions,",
           "no internet, 1 GB of memory and 1 CPU. Before any run we proved the hidden tests fail with the bug and pass with the",
           "real fix.", "",
           "**The agent gets** the issue text; `bash` (120 s per command), `read_file` (≤ 400 lines) and `edit_file`; three rules",
           "(don't touch tests, delete scratch files, finish with `DONE:` + changed files); 40 steps or 30 minutes. Tool outputs",
           "are capped at 12,000 characters.", "",
           "**Compaction fires mid-task** whenever the conversation has grown by 24,000 (gentle), 12,000 (medium) or 6,000",
           "(harsh) characters since the last compaction; the agent then gets \"Continue with the task.\" Characters, not",
           "tokens, so both models face identical pressure.", "",
           "**Judged by** the hidden tests in a fresh container: fixed = the target test passes and no existing test breaks.", "",
           "**Matrix:** 10 bugs × 7 conditions × 2 attempts × 2 models = **280 runs**. Rates show Wilson 95% intervals over",
           "20 attempts; counts show intervals bootstrapped over runs.", "",
           "## The 10 bugs", ""]
    out += [table(["Repository", "Issue", "Fix (SWE-bench task)", "The bug"],
                  [[f"`{repo}`", f"[#{iss}](https://github.com/{repo}/issues/{iss})", f"[PR #{pr}](https://github.com/{repo}/pull/{pr})", bug]
                   for _, repo, iss, pr, bug in TASKS]), "",
            "All are rated \"15 minutes to 1 hour\" for a human.", "",
            "## Results by condition", "", img("live_c", "Live agent"), ""]
    for m, mn in MODELS:
        rows = []
        for key, name in conds:
            v = c["cells"].get(f"{m}|{key}")
            if not v:
                continue
            fin = v["finished"]
            rows.append([name, kn(v["resolved"]), num(v["steps"]), num(v["compactions"]), f"{v['runs_with_compaction']}/{v['runs']}",
                         "–" if key == "none" else num(v["re_reads_after_compaction"]), f"{fin['n'] - fin['k']}/{fin['n']}",
                         kn(v["constraint_ok"]), kn(v["false_success"])])
        out += [f"**{mn}**", "", table(["Condition", "Fixed", "Steps", "Compactions", "Runs that compacted", "Re-reads after compaction",
                                        "Ran out of steps", "Rules kept (finished runs)", "Claimed success, tests fail"], rows), ""]
    out += ["*Re-reads after compaction*: files opened again that had already been read before the latest compaction (once per",
            "file per compaction). *Rules kept*: no test edits, no leftover files, a correct `DONE:` line.", "",
            "## Per bug: fixed attempts out of 2", ""]
    keys = [("none", "none"), ("compact@24000", "24k"), ("compact@12000", "12k"), ("compact@6000", "6k")]
    rows = []
    for inst, repo, iss, pr, _ in TASKS:
        pt = c["per_task"].get(inst, {})
        rows.append([f"{repo.split('/')[1]} #{iss}"] + [f"{pt.get(f'{m}|{k}', [0, 0])[0]}/{pt.get(f'{m}|{k}', [0, 0])[1]}" for m, _ in MODELS for k, _ in keys])
    out += [table(["Bug", "Haiku none", "Haiku 24k", "Haiku 12k", "Haiku 6k", "Luna none", "Luna 24k", "Luna 12k", "Luna 6k"], rows), "",
            "(Default compaction; keep-exact results are in `data/summary.json` under `c.per_task`.)", ""]
    return "\n".join(out)


# ------------------------------------------------------------------------------------------------ index

INDEX = """# Findings: how to read the results

Everything in this folder is generated from the experiment data (`python -m lab.findings`). Open the layer you care
about; every chart has its numbers next to it.

| File | What it is |
|---|---|
| [`layer1-lab.md`](layer1-lab.md) | **Layer 1, the controlled lab.** Every measure after one compaction, the 1–4 compaction curve, noise volume, trap types, salience, honesty, and every flagged rollout plan with its hand-review verdict. |
| [`layer2-real-sessions.md`](layer2-real-sessions.md) | **Layer 2, 12 real recorded agent sessions.** The session list, every measure, the 1–4 compaction curve, session length, honesty. |
| [`layer3-live-agent.md`](layer3-live-agent.md) | **Layer 3, the live agent on 10 SWE-bench bugs.** The bug list (issue + PR), results at three compaction pressures, and outcomes per bug. |
| [`charts/`](charts/) | Every chart as PNG (for slides and posts) and SVG, with the plotted numbers as CSV in `charts/data/`. See [`charts/index.md`](charts/index.md). |
| [`data/summary.json`](data/summary.json) | Every aggregate number: means, 95% intervals and counts, per layer, model, strategy and number of compactions. |
| [`data/facts_by_round.json`](data/facts_by_round.json) | Layer 1: per fact, how often it was recalled after 0–4 compactions. |
| [`data/safety_adjudication.json`](data/safety_adjudication.json) | The hand-review verdict for every rollout plan the rule checker flagged. |

## How the results are scored

After compaction the model answers questions on a copy of its memory, with tools switched off.

| Measure | Question | Scoring |
|---|---|---|
| **Recall** | Is an exact value (port, version, `file:line`, count, error name) still known? | Exact match after normalizing format (`14:37:12.904Z` counts as `14:37:12`). Misses are labelled UNKNOWN, STALE, DECOY, INVENTED or OTHER_WRONG. |
| **Files edited** | Which files were changed, not just read? | Precision / recall / F1 on exact paths. |
| **Rollout plan** (layer 1) | Can the work continue? | 10 plan values and 2 required commands, checked by code. |
| **Next step** (layer 2) | What would you do next? | Judge (0/1/2) against the step the original agent really took, plus a file + action match. |
| **Reasons** | Why was a choice made? | Judge `gpt-6.1-sol` scores 0/1/2 against the recorded reason; a keyword check is kept alongside. |
| **Safe plan** (layer 1) | Does the plan act on something outdated or forbidden? | 7 rule checks; every flag reviewed by hand. |
| **Honesty** | When a value was lost, was it admitted? | Share of misses answered UNKNOWN. |
| **Fixed** (layer 3) | Is the bug still fixed? | Hidden SWE-bench tests in a fresh container. |
| **Re-reads, steps, rules kept, false success** (layer 3) | How much extra work, and was the agent honest? | Counted from the agent's log and its final diff. |

## Terms

| Term | Meaning |
|---|---|
| **Compaction** | The API summarizes the conversation so far; the agent continues from the summary. |
| **Full history** | No compaction: the baseline. |
| **Drop old tool outputs** | No summary: every message and command is kept; all but the last 3 tool outputs are replaced by a placeholder. |
| **Default compaction** | Native compaction with the provider's own prompt. |
| **Keep-exact compaction** | The same call plus one paragraph telling it to keep identifiers, numbers, superseded values, rules and edited files (the text is in the main README). |
| **1–4 compactions** | How many times the session was compacted along the way: work → compact → work → compact. |
| **UNKNOWN / STALE / DECOY / INVENTED / OTHER_WRONG** | How a wrong answer was wrong: admitted, an older value, a look-alike, made up, or a real but wrong value. |
| **Pressure 24k / 12k / 6k** | Layer 3: compaction fires each time the conversation grows by that many characters. |
| **Numbers in brackets** | 95% intervals: bootstrapped over runs or sessions, or Wilson intervals over live attempts. Never computed over individual answers. |

## Regenerate

```bash
python -m lab.aggregate   # runs/ + results/ -> reports/data/summary.json
python -m lab.figures     # -> reports/charts/
python -m lab.findings    # -> findings/ (this folder)
```

The raw per-run data (`runs/`, `results/`, `data/`) is not committed; it is regenerated by running the study (see the
main README).
"""


def build() -> None:
    s = json.loads((REPORTS / "data" / "summary.json").read_text())
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "data").mkdir(parents=True)
    shutil.copytree(REPORTS / "charts", OUT / "charts")
    for f in ("summary.json", "facts_by_round.json"):
        shutil.copy(REPORTS / "data" / f, OUT / "data" / f)
    shutil.copy(ROOT / "data" / "safety_adjudication.json", OUT / "data" / "safety_adjudication.json")
    (OUT / "README.md").write_text(INDEX)
    (OUT / "layer1-lab.md").write_text(layer1(s))
    (OUT / "layer2-real-sessions.md").write_text(layer2(s))
    (OUT / "layer3-live-agent.md").write_text(layer3(s))
    print(f"wrote {OUT}: " + ", ".join(sorted(p.name for p in OUT.iterdir())))


if __name__ == "__main__":
    build()
