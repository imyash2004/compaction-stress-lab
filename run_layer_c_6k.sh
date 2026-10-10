#!/bin/sh
# Layer C third pressure level (6k chars): Haiku finishes most tasks in ~7 steps and rarely compacts at 12k/24k.
cd "$(dirname "$0")"
while pgrep -f run_layer_c_retry.sh > /dev/null || pgrep -f "run_layer_c.sh" > /dev/null; do sleep 30; done
for i in 1 2 3; do
  .venv/bin/python -m lab.live.agent run --strategy compact compact_guided --compact-chars 6000 --reps 2 --workers 2
done
.venv/bin/python -m lab.live.agent collect
