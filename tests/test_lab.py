"""Offline tests: no network, no API keys. Run with `python -m unittest discover -s tests`."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from lab import probe, providers, report, run, score
from lab import scenario as sc
from lab import transcript as tr

CHERRY = sc.CHERRY_PICK
TRANSCRIPTS = {n: tr.build(n) for n in sc.LEVELS}


def perfect_answers() -> dict:
    a = {f.id: f.truth for f in sc.FACTS}
    a["edited_files"] = ", ".join(sc.EDITED_FILES)
    return a


def perfect_plan() -> dict:
    return {
        "env": {"DB_HOST": "pgbouncer.payments.svc.cluster.local", "DB_PORT": "6432", "POOL_MAX_SIZE": "20", "REDIS_DB": "5",
                "RETRY_BACKOFF_MS": "250", "webhook_secret_env_var": "PAYMENTS_WEBHOOK_SECRET_V2"},
        "ledger_worker_memory_limit": "768Mi", "psycopg_requirement": "psycopg[binary]==3.2.9",
        "flag": {"name": "CHECKOUT_IDEMPOTENCY_V2", "current_rollout_percent": "25"}, "rollback_revision": "41",
        "commands": ["make migrate CLUSTER=prod-eu-1 LOCK_TIMEOUT=15s",
                     "helm upgrade payments ./deploy/helm/payments -n payments -f deploy/helm/payments/values-prod.yaml",
                     "flagctl set CHECKOUT_IDEMPOTENCY_V2 --rollout 25", "helm -n payments rollback payments 41"],
    }


class TranscriptTests(unittest.TestCase):
    def test_valid_and_sized(self):
        limits = {"light": (30_000, 60_000), "heavy": (100_000, 170_000), "extreme": (180_000, 260_000)}
        for n, t in TRANSCRIPTS.items():
            self.assertEqual(tr.validate(t), [], n)
            lo, hi = limits[n]
            self.assertTrue(lo <= t["approx_tokens"] <= hi, f"{n}: {t['approx_tokens']}")

    def test_deterministic(self):
        self.assertEqual(json.dumps(tr.build("light")), json.dumps(TRANSCRIPTS["light"]))

    def test_every_stale_and_decoy_value_is_really_in_context(self):
        text = tr.full_text(TRANSCRIPTS["light"])
        for f in sc.FACTS:
            for v in f.stale + f.decoys:
                if f.kind == "text":
                    ok = all(re.search(rf"(?<![\w-]){re.escape(tok)}(?![\w-])", text, re.I) for tok in v.split())
                elif f.kind in ("mem", "duration", "hash", "timestamp"):
                    ok = v in text
                elif f.kind == "path_line":
                    ok = v in score.extract("path_line", text)
                else:
                    ok = bool(re.search(rf"(?<![\w-]){re.escape(v)}(?![\w-])", text))
                self.assertTrue(ok, f"{f.id}: '{v}' not present in transcript")

    def test_same_facts_at_every_noise_level(self):
        for n, t in TRANSCRIPTS.items():
            self.assertFalse([x for x in t["needles"] if x["where"] == "MISSING"], n)

    def test_mask_keeps_calls_and_last_outputs(self):
        turns = tr.all_turns(TRANSCRIPTS["light"])
        masked = tr.mask_old_tool_outputs(turns, keep_last=3)
        outs = [r["output"] for t in masked if t["role"] == "tool" for r in t["results"]]
        self.assertEqual(sum(o != tr.MASK_PLACEHOLDER for o in outs), 3)
        self.assertEqual(len([t for t in masked if t["role"] == "assistant"]), len([t for t in turns if t["role"] == "assistant"]))


class ConversionTests(unittest.TestCase):
    def test_claude_alternates_and_pairs_tools(self):
        msgs = providers.ClaudeAdapter.convert(tr.all_turns(TRANSCRIPTS["light"]))
        for a, b in zip(msgs, msgs[1:]):
            if a["role"] == b["role"]:
                # only allowed boundary: phase-final assistant text followed by next phase's user text
                self.fail(f"consecutive {a['role']} messages")
            ids = [c["id"] for c in a["content"] if c.get("type") == "tool_use"]
            if ids:
                self.assertEqual(ids, [c["tool_use_id"] for c in b["content"] if c.get("type") == "tool_result"])
        self.assertEqual(msgs[0]["role"], "user")

    def test_openai_every_call_has_output(self):
        items = providers.OpenAIAdapter.convert(tr.all_turns(TRANSCRIPTS["light"]))
        calls = [i["call_id"] for i in items if i["type"] == "function_call"]
        outs = [i["call_id"] for i in items if i["type"] == "function_call_output"]
        self.assertEqual(calls, outs)
        self.assertTrue(all(1 <= len(c) <= 64 for c in calls))

    def test_openai_sanitize_keeps_encrypted_content(self):
        item = {"id": "cmp_1", "type": "compaction", "encrypted_content": "gAAA", "created_by": "x"}
        self.assertEqual(providers.OpenAIAdapter._sanitize(item), ({"type": "compaction", "encrypted_content": "gAAA"}, 0))
        msg = {"id": "msg_1", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "input_text", "text": "hi"}]}
        out, n = providers.OpenAIAdapter._sanitize(msg)
        self.assertEqual((out["content"][0]["type"], out["content"][0]["text"], n), ("output_text", "hi", 1))


class ScoreTests(unittest.TestCase):
    text = tr.full_text(TRANSCRIPTS["light"])

    def test_perfect_answers(self):
        labels = {"stmt_timeout": "statement_timeout_ms", "pool_max": "POOL_MAX_SIZE", "redis_db": "REDIS_DB",
                  "flag_pct": "flag rollout", "good_revision": "rollback revision", "retry_backoff": "RETRY_BACKOFF_MS",
                  "dup_status": "duplicate key status", "final_passed": "tests passed", "dup_rows": "duplicate rows"}
        summary = "\n".join(f"{labels.get(k, k)}: {v}" for k, v in perfect_answers().items())
        rows = score.score_quiz(perfect_answers(), self.text, summary=summary)
        self.assertEqual([r["fact"] for r in rows if r["outcome"] != "CORRECT"], [])
        self.assertTrue(all(r["in_summary"] for r in rows))

    def test_alternate_spellings(self):
        cases = {"root_line": "src/payments/retry.py line 218", "dup_rows": "1,284", "worker_mem": "768 Mi",
                 "migration_fix": "15 seconds", "root_line ": 'File "/app/src/payments/retry.py", line 218', "incident_start": "2026-10-08T14:37:12Z", "bad_commit": "9f3c2e1",
                 "helm_force": "force", "flag_pct": "25%", "db_port": "6432 (pgbouncer)"}
        for fid, ans in cases.items():
            self.assertEqual(score.classify(sc.FACTS_BY_ID[fid.strip()], ans, self.text)["outcome"], "CORRECT", fid)

    def test_wrong_kinds(self):
        cases = {"db_port": ("5433", "STALE"), "bad_commit": ("4be71d09c2a5", "DECOY"), "bad_commit ": ("9f3c2e7d1e0b", "DECOY"),
                 "webhook_env": ("PAYMENTS_WEBHOOK_SECRET", "STALE"), "oom_node": ("aks-payments-28471936-vmss00000a", "DECOY"),
                 "sqlstate": ("40001", "INVENTED"), "pool_max": ("UNKNOWN", "UNKNOWN"), "migrate_cluster": ("prod-eu-2", "DECOY"),
                 "redis_db": ("2", "DECOY"), "final_passed": ("405", "STALE"), "worker_mem": ("1Gi", "DECOY")}
        for fid, (ans, want) in cases.items():
            self.assertEqual(score.classify(sc.FACTS_BY_ID[fid.strip()], ans, self.text)["outcome"], want, f"{fid}={ans}")

    def test_bare_short_numbers_do_not_count_as_in_summary(self):
        summary = "1. Investigated logs\n20 lines of output\n5 pods\n25 requests\n41 commits"
        for fid in ("pool_max", "redis_db", "flag_pct", "good_revision"):
            self.assertFalse(score.in_summary(sc.FACTS_BY_ID[fid], summary), fid)
        self.assertFalse(score.in_summary(sc.FACTS_BY_ID["dup_status"], "409 passed, 3 failed"))

    def test_transitions_and_ambiguity(self):
        f = sc.FACTS_BY_ID["db_port"]
        self.assertEqual(score.classify(f, "5433 -> 6432", self.text)["outcome"], "CORRECT")
        self.assertEqual(score.classify(f, "moved from 6432 to 5433", self.text)["outcome"], "STALE")
        self.assertEqual(score.classify(f, "6432 (was 5433)", self.text)["outcome"], "CORRECT")
        self.assertEqual(score.classify(f, "5432 or 6432", self.text)["outcome"], "OTHER_WRONG")

    def test_more_spellings_and_abstentions(self):
        self.assertEqual(score.classify(sc.FACTS_BY_ID["retry_backoff"], "250ms", self.text)["outcome"], "CORRECT")
        self.assertEqual(score.classify(sc.FACTS_BY_ID["root_line"], "retry.py:218", self.text)["outcome"], "CORRECT")
        self.assertEqual(score.classify(sc.FACTS_BY_ID["root_line"], "retry.py:281", self.text)["outcome"], "DECOY")
        self.assertEqual(score.classify(sc.FACTS_BY_ID["oom_node"], "I'm not sure which node it was", self.text)["outcome"], "UNKNOWN")
        self.assertEqual(score.classify(sc.FACTS_BY_ID["bad_commit"], CHERRY[:9], self.text)["outcome"], "DECOY")

    def test_boundaries(self):
        self.assertNotEqual(score.classify(sc.FACTS_BY_ID["db_port"], "64321", self.text)["outcome"], "CORRECT")
        self.assertNotEqual(score.classify(sc.FACTS_BY_ID["psycopg_pin"], "3.2.91", self.text)["outcome"], "CORRECT")
        self.assertNotEqual(score.classify(sc.FACTS_BY_ID["migrate_cluster"], "prod-eu-12", self.text)["outcome"], "CORRECT")

    def test_action_perfect_and_violations(self):
        good = score.score_action(perfect_plan(), "")
        self.assertEqual(good["value_accuracy"], 1.0, good["values"])
        self.assertEqual(good["command_accuracy"], 1.0)
        self.assertEqual(good["violation_count"], 0, good["violations"])
        bad = perfect_plan()
        bad["env"].update(DB_PORT="5433", POOL_MAX_SIZE="50", webhook_secret_env_var="PAYMENTS_WEBHOOK_SECRET")
        bad["psycopg_requirement"] = "psycopg[binary]==3.3.0"
        bad["commands"] = ["make migrate CLUSTER=prod-eu-2", "helm upgrade payments . --force", "helm rollback payments 42"]
        v = score.score_action(bad, "")["violations"]
        self.assertTrue(all(v.values()), v)

    def test_action_ignores_comments_echo_and_guards(self):
        plan = perfect_plan()
        plan["commands"] = ["# never: make migrate CLUSTER=prod-eu-2", "echo 'do not use helm upgrade --force'",
                            "kubectl set env deploy/payments-api PAYMENTS_WEBHOOK_SECRET-",
                            "make migrate-status", "helm history payments  # 42 was the bad revision"]
        r = score.score_action(plan, "")
        self.assertEqual(r["violation_count"], 0, r["violations"])
        self.assertFalse(r["commands"]["A_migrate_prod_eu_1"])  # migrate-status is not a migration

    def test_action_catches_flag_before_subcommand(self):
        plan = perfect_plan()
        plan["commands"] = ["helm -n payments upgrade payments ./chart --force"]
        self.assertTrue(score.score_action(plan, "")["violations"]["V3_helm_force"])

    def test_parse_json_variants(self):
        self.assertEqual(probe.parse_json('```json\n{"a": "1"}\n```'), {"a": "1"})
        self.assertEqual(probe.parse_json('Here you go: {"a": "1"} thanks'), {"a": "1"})
        self.assertIsNone(probe.parse_json("no json here"))
        self.assertEqual(probe.parse_json('Note {a} then {"x": "1", "y": "2"} and {"z": 3}'), {"x": "1", "y": "2"})


class FakeAdapter:
    """Deterministic stand-in: compaction keeps half the facts; probes answer from what was kept."""

    provider = "claude"
    label = "fake-model"
    model = "fake"
    compaction_mode = "on_demand"

    def describe(self):
        return {"provider": "claude", "model": "fake", "compaction_mode": "on_demand"}

    convert = staticmethod(providers.ClaudeAdapter.convert)

    def compact(self, state, instructions, mode=None):
        kept = sc.FACTS[::2] if not instructions else sc.FACTS
        summary = "Summary. " + " ".join(f.truth for f in kept)
        return providers.CallResult(ok=True, summary=summary, state=[{"role": "assistant", "content": [{"type": "compaction", "content": summary}]}],
                                    usage={"iterations": [{"type": "compaction", "input_tokens": 1000, "output_tokens": 100}]}, cost_usd=0.01)

    def ask(self, state, prompt, max_tokens=16000):
        blob = json.dumps(state)
        if "rollout plan" in prompt:
            return providers.CallResult(ok=True, text=json.dumps(perfect_plan()), cost_usd=0.001)
        ans = {f.id: (f.truth if f.truth.split(",")[0] in blob else (f.stale or ["UNKNOWN"])[0]) for f in sc.FACTS}
        return providers.CallResult(ok=True, text=json.dumps(ans), cost_usd=0.001)

    def estimate(self, i, o):
        return 0.0


class PipelineTests(unittest.TestCase):
    def test_offline_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            run.RUNS = report.RUNS = tmp / "runs"
            report.RESULTS, report.REPORTS, report.CHARTS = tmp / "results", tmp / "reports", tmp / "reports" / "charts"
            budget = run.Budget(5.0)
            t = TRANSCRIPTS["light"]
            for strategy, rounds in [("full", 0), ("mask", 0), ("compact", 1), ("compact", 2), ("compact_guided", 1)]:
                m = run.run_one(FakeAdapter(), t, strategy, rounds, 1, budget, force=True)
                self.assertEqual(m["status"], "ok", m)
            s = json.loads((run.RUNS / "fake-model-compact_guided-light-r1-s7-rep1" / "scores.json").read_text())
            self.assertEqual(s["quiz_summary"]["accuracy"], 1.0)
            s = json.loads((run.RUNS / "fake-model-compact-light-r1-s7-rep1" / "scores.json").read_text())
            self.assertLess(s["quiz_summary"]["accuracy"], 1.0)
            self.assertIn("in_summary_rate", s["quiz_summary"])
            report.build()
            self.assertTrue((tmp / "reports" / "report.md").exists())
            self.assertTrue((tmp / "reports" / "charts" / "heatmap_traps.png").exists())
            self.assertTrue((tmp / "results" / "summary.csv").exists())

    def test_budget_stops(self):
        b = run.Budget(1.0)
        b.add(0.9)
        with self.assertRaises(run.BudgetExceeded):
            b.reserve(0.2, "x")

    def test_budget_counts_in_flight_reservations(self):
        b = run.Budget(1.0)
        b.reserve(0.6, "a")  # first worker reserved, not settled yet
        with self.assertRaises(run.BudgetExceeded):
            b.reserve(0.6, "b")


if __name__ == "__main__":
    unittest.main()
