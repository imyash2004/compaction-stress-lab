#!/bin/sh
# Re-run layer C attempts that died on Foundry rate limits (resume skips finished runs), after the first pass.
cd "$(dirname "$0")"
while pgrep -f run_layer_c.sh > /dev/null; do sleep 30; done
for i in 1 2 3; do
  .venv/bin/python -m lab.live.agent run --strategy none compact compact_guided --compact-chars 24000 --reps 2 --workers 2
  .venv/bin/python -m lab.live.agent run --strategy compact compact_guided --compact-chars 12000 --reps 2 --workers 2
done
.venv/bin/python -m lab.live.agent collect
