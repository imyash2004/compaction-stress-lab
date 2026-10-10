# Inputs

The exact inputs behind the published results, so you can inspect them without re-running anything.

| Path | What it is |
|---|---|
| [`real-sessions/`](real-sessions/) | The 12 real agent sessions used in layer 2, one JSON file each. Every file has the session converted to API tool calls (`phases`), the hidden remainder after the cut (`hidden`), the answer key (`key`: edited files, the real next step, the exact-value questions with their answers and where they appear, the why-questions with reference reasons) and where it came from (`provenance`). |
| [`live-tasks.json`](live-tasks.json) | The 10 SWE-bench Verified bugs used in layer 3: repository, issue text, hidden test patch, the tests that must start / keep passing, the test command and the Docker image name. |
| [`safety_adjudication.json`](safety_adjudication.json) | The hand-review verdict for each of the 11 rollout plans the rule checker flagged in layer 1 (7 confirmed, 4 not counted), with a note on each. |

**Reuse them.** To run layer 2 on exactly these sessions and questions, copy `real-sessions/*.json` to `data/cases/`
(this skips the question-writing step). To run layer 3 on these bugs, copy `live-tasks.json` to `data/live/tasks.json`,
then build the images with `python -m lab.live.tasks build`.

**Attribution.** The sessions in `real-sessions/` are derived from
[`nebius/SWE-agent-trajectories`](https://huggingface.co/datasets/nebius/SWE-agent-trajectories) (CC-BY-4.0): they
were converted to API tool-call format, cut before the end, and extended with our questions. The bugs in
`live-tasks.json` come from [SWE-bench Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified);
issue texts and test patches belong to their original projects (psf/requests, pytest-dev/pytest, sympy/sympy) under
those projects' licenses.
