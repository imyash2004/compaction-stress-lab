"""Offline tests for the charts (lab/figures.py): build every chart from a tiny synthetic summary."""

from __future__ import annotations

import re
import struct
import tempfile
import unittest
from pathlib import Path

from lab import figures

MODELS = ["claude-test-haiku", "openai-test-luna"]
STRATS = ["full", "mask", "compact", "compact_guided"]
OUTCOMES = ["CORRECT", "UNKNOWN", "STALE", "DECOY", "INVENTED", "OTHER_WRONG"]


def ci(mean: float, n: int = 3) -> dict:
    return {"mean": mean, "lo": max(0.0, mean - 0.1), "hi": min(1.0, mean + 0.1), "n": n}


def wil(k: int, n: int) -> dict:
    return {"mean": k / n, "lo": max(0.0, k / n - 0.2), "hi": min(1.0, k / n + 0.2), "k": k, "n": n}


def base(s: str) -> float:
    return {"full": 1.0, "mask": 0.7, "compact": 0.65, "compact_guided": 0.85}[s]


def layer(measures: list[str], buckets: list[str], with_lab_extras: bool) -> dict:
    L: dict = {"models": MODELS, "measures": measures, "n_runs": 8, "headline": {}, "rounds": {}, "retention": {},
               "size": {}, "outcomes": {}}
    for m in MODELS:
        L["headline"][m] = {s: {ms: ci(base(s)) for ms in measures} for s in STRATS}
        L["rounds"][m] = {s: {ms: ci(base(s)) for ms in measures} for s in ("full", "mask")}
        for s in ("compact", "compact_guided"):
            # string keys, as after a JSON round trip; round 4 missing for one curve
            L["rounds"][m][s] = {str(k): {ms: ci(base(s) * 0.8 ** (k - 1), n=1) for ms in measures}
                                 for k in (1, 2, 3, 4) if not (s == "compact_guided" and k == 4)}
            L["retention"].setdefault(m, {})[s] = {"a": base(s), "r": 0.8, "points": {}}
        L["size"][m] = {s: {b: {ms: ci(base(s)) for ms in measures} for b in buckets} for s in STRATS}
        counts = {s: [20, 6, 1, 1, 0, 2] if s != "full" else [30, 0, 0, 0, 0, 0] for s in STRATS}
        L["outcomes"][m] = {s: {sc: dict(zip(OUTCOMES, counts[s])) for sc in ("r1", "all")} for s in STRATS}
    if with_lab_extras:
        keys = [f"{m}|{s}" for m in MODELS for s in STRATS]
        L["breakdowns"] = {
            "traps": {k: {"OVR": wil(4, 5), "SIL": wil(0, 5), "DEC": wil(3, 5)} for k in keys},
            "salience": {k: {"acknowledged": wil(8, 10), "silent": wil(2, 10), "user": wil(1, 1)} for k in keys},
            "phase": {k: {p: wil(3, 5) for p in ("P1", "P2", "P3", "P4")} for k in keys},
            "facts": {k: {f"f{i}": wil(1, 2) for i in range(5)} for k in keys},
        }
        L["safety"] = {f"{m}|{s}|r{r}": {"runs": 2, "unsafe": 1 if s == "compact" else 0,
                                         "violations": {"V7_wrong_db_port": 1 if s == "compact" else 0}, "rate": wil(1, 2)}
                       for m in MODELS for s in STRATS for r in ((0,) if s in ("full", "mask") else (1, 2))}
    else:
        L["by_phase"] = {f"{m}|{s}": {"early": wil(3, 4), "late": wil(1, 4)} for m in MODELS for s in STRATS}
    return L


def summary(layers: str = "abc") -> dict:
    S: dict = {"strategies": STRATS, "outcomes": OUTCOMES,
               "model_names": {MODELS[0]: "Claude Test Haiku", MODELS[1]: "Test Luna"}}
    if "a" in layers:
        S["a"] = layer(["recall", "artifact", "continuation", "decision", "safety"], ["light", "heavy", "extreme"], True)
    if "b" in layers:
        S["b"] = layer(["recall", "artifact", "continuation", "continuation_exact", "decision"], ["short", "medium", "long"], False)
    if "c" in layers:
        conds = ["none", "compact@24000", "compact_guided@24000", "compact@12000", "compact_guided@12000"]
        cell = {"runs": 4, "runs_with_compaction": 2, "resolved": wil(3, 4), "constraint_ok": wil(4, 4),
                "false_success": wil(1, 4), "re_reads_after_compaction": {"mean": 1.5, "lo": 0.5, "hi": 2.5, "n": 4},
                "repeated_failed_after_compaction": {"mean": 0.25, "lo": 0.0, "hi": 0.75, "n": 4}}
        S["c"] = {"models": MODELS, "conditions": conds, "tasks": ["t1", "t2"],
                  "cells": {f"{m}|{c}": dict(cell) for m in MODELS for c in conds}}
    return S


def png_size(p: Path) -> tuple[int, int]:
    head = p.read_bytes()[:24]
    return struct.unpack(">II", head[16:24])


class FiguresTest(unittest.TestCase):
    def test_all_layers(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            made = figures.build(summary("abc"), out)
            names = {n for n, _ in made}
            expected = {"headline_a.png", "headline_b.png", "rounds_a.png", "rounds_b.png", "size_a.png", "size_b.png",
                        "traps_a.png", "salience_phase_a.png", "phase_b.png", "outcomes_a.png", "outcomes_b.png",
                        "safety_a.png", "live_c.png", "hero.png"}
            self.assertEqual(names, expected)
            for n in expected:
                png = out / n
                self.assertTrue(png.stat().st_size > 10_000, n)
                self.assertTrue((out / n.replace(".png", ".svg")).exists(), n)
                self.assertTrue((out / "data" / n.replace(".png", ".csv")).exists(), n)
                w, _ = png_size(png)
                self.assertEqual(w, 1600, n)
            index = (out / "index.md").read_text()
            for n in expected:
                self.assertIn(n, index)
            self.assertIsNone(re.search(r"\$|\bcosts?\b|\bprices?\b|\bstudents?\b|\bteachers?\b|\bclass(es)?\b", index, re.I))

    def test_missing_layers_skip_cleanly(self):
        with tempfile.TemporaryDirectory() as d:
            made = figures.build(summary("c"), Path(d))
            self.assertEqual([n for n, _ in made], ["live_c.png"])
            made = figures.build(summary("a"), Path(d) / "a_only")
            names = {n for n, _ in made}
            self.assertNotIn("headline_b.png", names)
            self.assertNotIn("live_c.png", names)
            self.assertIn("hero.png", names)
            made = figures.build({"strategies": STRATS}, Path(d) / "empty")
            self.assertEqual(made, [])

    def test_captions_quote_computed_numbers(self):
        with tempfile.TemporaryDirectory() as d:
            caps = dict(figures.build(summary("a"), Path(d)))
            self.assertIn("65%", caps["headline_a.png"])   # compact recall in the synthetic data
            self.assertIn("keeps 80% per round", caps["rounds_a.png"])


if __name__ == "__main__":
    unittest.main()
