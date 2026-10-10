# compaction-stress-lab

**What context compaction makes AI coding agents forget, measured at the API layer.**

Long-running agents outgrow their context window, so the API summarizes the conversation and the agent continues
from the summary. Anthropic and OpenAI both offer this natively as **compaction**. This repo measures what survives
it: exact values, files, reasons, next steps, and whether the agent still acts correctly. We tested two popular
small models, **Claude Haiku 5.5** and **GPT-6 Luna**, across **628 runs** in three layers of evidence.

📄 **Article:** [What Compaction Forgets](https://claude.ai/artifact/9FxuhEtazFTvDag4ZHLpp5)
📊 **Results:** [`findings/`](findings/README.md), with one explained page per layer, every chart and all the data.
📚 **New here?** [`docs/`](docs/README.md) explains where the data came from, how the code fits together, the real
bugs the agent fixed, every prompt word for word, and one worked example per layer.

![Facts remembered after repeated compaction](findings/charts/hero.png)

---

## Key findings

| # | Finding | Evidence |
|---|---|---|
| 1 | **One compaction keeps about 1 in 5 exact details.** Line numbers, counts and error names the tools printed: 99–100% with full history, **19–20%** after one compaction. | 12 real agent sessions |
| 2 | **Every extra compaction compounds the loss.** GPT-6 Luna: 67% → 48% → 30% → **23%** over 4 compactions, losing about a third of what was left each time. | Controlled lab |
| 3 | **Summaries keep what was discussed, not what was printed.** Facts the agent stated: 79–91% kept. Facts only in tool output: **24–31%**. | Controlled lab |
| 4 | **One paragraph of instructions is the biggest lever.** "Keep exact values" roughly doubled exact recall on real sessions (19–20% → **41–48%**) and slowed the decay: the share Luna kept per extra compaction rose from 69% to 89%. | Layers 1–2 (in layer 3 it cut steps, not failures) |
| 5 | **Agents rarely lie, but forgotten facts become wrong actions.** 90–97% of lost values were answered "UNKNOWN", yet when asked to write the production rollout plan, **7 of 72** plans written after compaction used an outdated value (old DB port, revoked secret, broken release). Without compaction (full history or dropped outputs): 0 of 36. | Controlled lab, hand-reviewed |
| 6 | **A live agent still fixes the bug, but works harder.** Fix rates did not change measurably on 10 SWE-bench bugs, but under harsh compaction (every 6,000 characters) Luna took **56% more steps**, re-read files ~9 times per task and ran out of steps in 5 of 20 attempts. | Live agent in Docker |

---

## How native compaction works

| | Anthropic (Claude) | OpenAI (GPT) |
|---|---|---|
| Call | Messages API with `compaction={"type": "summarize"}` (beta `compact-2026-09-04`) | `POST /responses/compact` |
| Returns | One **readable** summary block (signed) | Your user messages **verbatim** + one **encrypted** compaction item (`gAAAAB…`) |
| Inspectable? | Yes, plain text | No: only the model can read it |
| Custom instructions | `instructions` | `instructions` |
| Automatic mode | `context_management` edit `compact_20260112` | `context_management: [{"type": "compaction", "compact_threshold": N}]` |

On OpenAI, on-demand and automatic compaction gave the same quality when triggered at the same point (72% vs 71% in an
earlier 10-model run with one repeat; see [`DESIGN.md`](DESIGN.md) §14).

## What we tested

| Layer | What it is | Why it's there | Runs |
|---|---|---|---|
| **1 · Controlled lab** | A long, noisy production-outage session we wrote ourselves, with 29 facts hidden at known spots, compacted 1–4 times | Exact answer key; isolates *why* things break | 108 |
| **2 · Real sessions** | 12 recorded SWE-agent sessions on 12 GitHub repos ([`nebius/SWE-agent-trajectories`](https://huggingface.co/datasets/nebius/SWE-agent-trajectories)), questions verified against real tool outputs | Real noise; nothing designed to fail | 240 |
| **3 · Live agent** | Our own API agent fixing 10 real [SWE-bench Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified) bugs in Docker, with compaction firing mid-task | Does the work still get done? | 280 |

**Strategies compared on the same history:**

- **Full history.**
- **Drop old tool outputs.**
- **Default compaction.**
- **Keep-exact compaction:** default compaction plus one paragraph of instructions.

*In the code, layers 1/2/3 are `a`/`b`/`c` and the strategies are `full`, `mask`, `compact` and `compact_guided`.
Every term is defined in [`findings/README.md`](findings/README.md#terms).*

**Measures:**

- recall (exact match);
- files edited;
- next step and rollout plan;
- the reasons behind decisions (graded 0/1/2 by `gpt-6.1-sol` against the recorded reason);
- safety (rule checks, hand-reviewed);
- honesty (admitted vs wrong);
- live: bug fixed (hidden tests), re-reads, steps, rules kept.

**The live bugs:**

| Repository | Issues |
|---|---|
| `psf/requests` | #2930, #6027 |
| `pytest-dev/pytest` | #5606, #7215, #7569, #9877 |
| `sympy/sympy` | #11617, #13779, #15193, #24442 |

Full details and links are in [`findings/layer3-live-agent.md`](findings/layer3-live-agent.md).

## Which strategy, and where to compact

| Strategy | Keeps | Loses | Use it when |
|---|---|---|---|
| Full history | Everything | Nothing | The session fits |
| Drop old tool outputs | Everything the agent said and did | Facts only a tool printed; rules inside tool output | Simple, no extra call; beat default compaction on real sessions (37–51% vs 19–20%) |
| Default compaction | Story, decisions, files edited | Most exact values; many reasons | Rarely on its own |
| **Keep-exact compaction** | ~2× more exact values; slower decay | Still ~half of details on real sessions | **Whenever you compact** |

**When and where to compact:**

1. **Compact late and rarely.** Losses multiply with every pass.
2. **Compact at a task boundary, after the agent has stated its results.** Not right after a large tool output.
3. **Write exact values to a notes file first.** That means IDs, ports, `file:line` and decisions with their reasons.
4. **Re-check the source of truth before risky actions** (deploys, rollbacks, secrets).
5. **Leave headroom in step budgets** for re-reading after compaction.

**The keep-exact instructions** (the same text for both APIs, passed as `instructions`):

```text
Summarize this engineering session so another engineer can continue it without the raw history.
Do not call any tools; reply with the summary text only.
Always preserve, exactly as written: identifiers (paths with line numbers, commit hashes, ticket IDs, node names, env var names, constraint and migration names), numbers with units, ports, versions, error codes and error messages, timestamps, and counts.
For any value that changed during the session, give only the latest value and list the superseded values explicitly as "superseded: ...".
Record every rule or prohibition ("do not ...") verbatim.
Record every approach that was tried and rejected, with the reason.
Record which files were edited (not just read).
Record secrets that were revoked and which one replaced them.
If you must cut, cut narrative before cutting any of the above.
```

---

## Where the results are

Everything you need to read the results is in [`findings/`](findings/README.md):

| Path | Contents |
|---|---|
| [`findings/README.md`](findings/README.md) | **How to read the results:** file index, how each measure is scored, every term explained |
| [`findings/layer1-lab.md`](findings/layer1-lab.md) | Lab results: every table, trap types, salience, honesty, all flagged rollout plans with verdicts |
| [`findings/layer2-real-sessions.md`](findings/layer2-real-sessions.md) | Real-session results and the list of sessions |
| [`findings/layer3-live-agent.md`](findings/layer3-live-agent.md) | Live-agent results, the bug list with issue and PR links, outcomes per bug |
| [`findings/charts/`](findings/charts/index.md) | All charts as PNG and SVG, with the plotted numbers as CSV |
| [`findings/data/`](findings/data/) | `summary.json` (every aggregate with 95% intervals) and the hand-review verdicts |

## Repository layout

```text
compaction-stress-lab/
├── README.md                ← you are here
├── DESIGN.md                ← architecture, sequence diagrams, scenario, scoring, fairness rules
├── findings/                ← results: one explained page per layer, charts, data
├── docs/                    ← data sources, code map, the 10 real bugs, every prompt, worked examples
├── lab/
│   ├── scenario.py          ← lab story, 29 facts and traps, decisions, rollout-plan rules
│   ├── noise.py             ← realistic noisy tool output (logs, tracebacks, pip, kubectl, pytest…)
│   ├── transcript.py        ← builds the lab session at 3 noise levels
│   ├── providers.py         ← Claude (Anthropic API) and OpenAI (Microsoft Foundry) adapters: compact, ask, agent step
│   ├── probe.py · score.py  ← questions and exact-match scoring
│   ├── judge.py · stats.py  ← 0/1/2 judge for reasons; intervals and retention fit
│   ├── study.py             ← runner for layers 1 and 2: plan | run | score | status
│   ├── replay.py            ← layer 2: fetch, convert, cut and build verified questions from real sessions
│   ├── live/                ← layer 3: tasks.py (SWE-bench → Docker images), sandbox.py, agent.py (live loop)
│   ├── aggregate.py · figures.py · findings.py · docs.py   ← numbers → charts → findings/ and docs/
│   ├── run.py · report.py                        ← runner for the earlier 10-model comparison
│   └── extract.py                                ← side experiment: probing what an encrypted OpenAI summary holds
├── tests/                   ← offline tests (no API calls)
├── inputs/                  ← the exact inputs: 12 real sessions + questions, 10 live bugs, safety verdicts
├── run_layer_ab.sh          ← layer 1 retries, all layer 2 runs, scoring
├── run_layer_c*.sh          ← layer 3 at 24k/12k (c), 6k (c_6k), and re-runs of rate-limited attempts (c_retry)
└── run_stage_a.sh           ← the earlier 10-model comparison
```

## Reproduce

**Requirements:**

- Python 3.13.
- Docker, for layer 3. Images are built locally from public GitHub repos (network needed; about 400 MB each, ~4 GB for
  the 10 bugs). The Dockerfiles target `python:3.9-slim`, so they build on arm64 and x86.
- API access to:
  - Anthropic, for `claude-haiku-5-5` (`ANTHROPIC_API_KEY`, `CLAUDE_BACKEND=anthropic`);
  - an Azure OpenAI / Microsoft Foundry resource with deployments named exactly `gpt-6-luna` and `gpt-6.1-sol`
    (`AZURE_OPENAI_ENDPOINT`, `AZURE_API_KEY`). The deployment name is used as the model name.

**No API keys?** You can still read everything in `findings/`, `docs/` and `inputs/`, run the offline tests, and redraw
every chart from the committed numbers:

```bash
python -m lab.figures --summary findings/data/summary.json --out /tmp/charts   # after installing requirements.txt
```

**Full reproduction:**

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # fill in ANTHROPIC_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_API_KEY
.venv/bin/python -m unittest discover -s tests      # offline tests

# Layer 1 (lab) and Layer 2 (real sessions)
.venv/bin/python -m lab.replay fetch && .venv/bin/python -m lab.replay build   # build the 12 real-session cases
.venv/bin/python -m lab.study plan  --layer all
.venv/bin/python -m lab.study run   --layer a --workers 4
.venv/bin/python -m lab.study run   --layer b --workers 4
.venv/bin/python -m lab.study score --layer a && .venv/bin/python -m lab.study score --layer b

# Layer 3 (live agent in Docker)
.venv/bin/python -m lab.live.tasks select && .venv/bin/python -m lab.live.tasks build && .venv/bin/python -m lab.live.tasks validate
./run_layer_c.sh && ./run_layer_c_6k.sh                  # 24k, 12k and 6k pressure, 2 attempts each

# Numbers, charts, findings, docs
.venv/bin/python -m lab.aggregate && .venv/bin/python -m lab.figures && .venv/bin/python -m lab.findings && .venv/bin/python -m lab.docs
```

**Notes:**

- **Exact inputs:** `lab.replay build` asks `gpt-6.1-sol` to write the layer 2 questions, so a rebuild gives different
  questions. To reuse the exact sessions and questions we used, copy `inputs/real-sessions/*.json` to `data/cases/`
  instead. `inputs/live-tasks.json` is the layer 3 bug list; `inputs/safety_adjudication.json` holds the hand-review
  verdicts for flagged rollout plans (new runs need a new review).
- **Time:** layers 1 and 2 take about 1–2 hours with 4 workers; layer 3 (280 runs) takes about 3 hours with 3 workers.
- **Resume and retries:** runs resume where they stopped; rate-limited calls are retried.
- **The last line** (aggregate → figures → findings → docs) only works after a full local run. It refuses to touch
  `findings/` or `docs/` otherwise.
- **Not committed:** the raw downloads and per-run outputs (`data/`, `runs/`, `results/`).

## Limits

- **Two small models.** Larger models compact better (in the earlier 10-model run, `gpt-6.1-sol` with instructions
  kept about 99%; see [`DESIGN.md`](DESIGN.md) §14).
- **OpenAI's `instructions` parameter** for `responses.compact` is not documented as steering compaction, though it
  measurably did.
- **Live bugs are short** (7–15 steps), so compaction was forced early with low triggers. Longer tasks will likely
  show larger effects.
- **Real-session questions were drafted by an AI,** then verified by code and gated with full-history answers. The
  question writer and the judge are OpenAI models.
- **Results are for these model versions** (October 2026).

## Credits

- **Real sessions:** [`nebius/SWE-agent-trajectories`](https://huggingface.co/datasets/nebius/SWE-agent-trajectories)
  (CC-BY-4.0).
- **Live tasks:** [SWE-bench Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified).
- **Probe design** builds on Factory's
  [*Evaluating Context Compression for AI Agents*](https://factory.com/news/evaluating-compression).
