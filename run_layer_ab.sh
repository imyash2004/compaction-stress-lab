#!/bin/sh
# Layers A (retry the rate-limited runs) and B (240 runs on 12 real sessions), then score both with the judge.
cd "$(dirname "$0")"
.venv/bin/python -m lab.study run --layer a --workers 2
.venv/bin/python -m lab.study run --layer b --workers 4
.venv/bin/python -m lab.study run --layer b --workers 2
.venv/bin/python -m lab.study score --layer a
.venv/bin/python -m lab.study score --layer b
