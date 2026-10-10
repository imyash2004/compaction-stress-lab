#!/bin/sh
# Layer C: live agent on 10 SWE-bench Verified tasks, both models, 2 reps; compaction at two pressure levels.
cd "$(dirname "$0")"
.venv/bin/python -m lab.live.agent run --strategy none compact compact_guided --compact-chars 24000 --reps 2 --workers 3
.venv/bin/python -m lab.live.agent run --strategy compact compact_guided --compact-chars 12000 --reps 2 --workers 3
.venv/bin/python -m lab.live.agent collect
