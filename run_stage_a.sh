#!/bin/sh
# Stage A: every model, every strategy, every noise level, 1 rep; then repeated compaction on heavy.
cd "$(dirname "$0")"
.venv/bin/python -W ignore -m lab.run run --targets claude openai --strategy all --noise light heavy extreme --rounds 1 --reps 1 --workers 8 --max-usd 70
.venv/bin/python -W ignore -m lab.run run --targets claude openai --strategy compact compact_guided --noise heavy --rounds 2 3 --reps 1 --workers 8 --max-usd 35
.venv/bin/python -W ignore -m lab.run report
