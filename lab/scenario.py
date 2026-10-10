"""The stress scenario: a production incident on `payments-api`, with 30 planted facts.

This module is the hidden answer key. Nothing in here is ever sent to a model except
the story (via transcript.py) and the questions (via probe.py).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from lab import noise as nz

SCENARIO_ID = "payments-checkout-incident"

BAD_COMMIT = "9f3c2e1a7b44d0c6e8f15a2b3c4d5e6f7a8b9c0d"
CHERRY_PICK = "4be71d09c2a58e3f6b1d7c90a2e4f6081b3d5c7e"
NEAR_COMMIT = "9f3c2e7d1e0b6a4c8f2d9e1b3a5c7d9e0f1a2b3c"
OOM_NODE = "aks-payments-28471936-vmss00000c"
NEAR_NODES = [f"aks-payments-28471936-vmss00000{c}" for c in "abdef"]
EDITED_FILES = [
    "deploy/helm/payments/values-prod.yaml",
    "requirements.txt",
    "migrations/versions/0042_add_idempotency_index.py",
    "src/payments/retry.py",
    "src/payments/idempotency.py",
    "deploy/k8s/ledger-worker.yaml",
]
READ_ONLY_FILES = [
    "docker-compose.yml", "config/payments.yaml", "config/ledger-worker.yaml", "RUNBOOK.md", "README.md",
    "src/payments/settings.py",
]

UTC = timezone.utc
T = lambda h, m, s, ms=0: datetime(2026, 10, 8, h, m, s, ms * 1000, tzinfo=UTC)  # noqa: E731


@dataclass
class Fact:
    id: str
    question: str
    truth: str
    kind: str  # int | version | hash | path_line | duration | mem | token | token_cs | text | timestamp | list
    traps: list[str]
    phase: str
    salience: str  # acknowledged | silent | user
    stale: list[str] = field(default_factory=list)
    decoys: list[str] = field(default_factory=list)
    locator: str = ""  # regex that marks where the truth is planted (for position analysis)
    alts: list[str] = field(default_factory=list)  # other spellings accepted as correct
    summary_rx: str = ""  # stricter "is it written in this text" check for short or common values


FACTS: list[Fact] = [
    Fact("db_port", "Which port does payments-api use to reach the database at the end of the session?", "6432", "int",
         ["OVR", "DEC"], "P3", "acknowledged", stale=["5432", "5433"], locator=r"pgbouncer\s+ClusterIP.*6432/TCP"),
    Fact("stmt_timeout", "What is payments-api's statement_timeout_ms?", "4200", "int",
         ["DEC"], "P1", "silent", decoys=["4020"], locator=r"statement_timeout_ms: 4200",
         summary_rx=r"(?i)statement[_ ]timeout[^\n]{0,40}\b4200\b|\b4200\s*ms"),
    Fact("psycopg_pin", "What exact psycopg version is pinned now?", "3.2.9", "version",
         ["REJ", "OVR"], "P2", "acknowledged", stale=["3.2.1", "3.3.0"], locator=r"psycopg\[binary\]==3\.2\.9"),
    Fact("bad_commit", "Give the first 12 characters of the commit on main that introduced the regression.", BAD_COMMIT[:12], "hash",
         ["OID", "DEC", "BUR"], "P1", "acknowledged", decoys=[CHERRY_PICK[:12], NEAR_COMMIT[:12]], locator=rf"commit {BAD_COMMIT}"),
    Fact("sqlstate", "What SQLSTATE code does the error behind the checkout failures have?", "23505", "token",
         ["DEC", "BUR"], "P1", "acknowledged", decoys=["23503", "53300", "55P03"], locator=r"sqlstate=23505"),
    Fact("constraint", "What is the exact name of the violated unique constraint?", "ledger_entries_idempotency_key_key", "token",
         ["DEC"], "P1", "acknowledged", decoys=["ledger_entries_idempotency_key_idx", "refund_items_payment_id_fkey"],
         locator=r"constraint=ledger_entries_idempotency_key_key"),
    Fact("root_line", "In which file and at which line is the duplicate ledger entry created? Answer as path:line.",
         "src/payments/retry.py:218", "path_line", ["BUR", "DEC"], "P1", "acknowledged",
         decoys=["src/payments/retry.py:281", "tests/payments/test_retry.py:218"], locator=r'retry\.py", line 218'),
    Fact("migration_fix", "Which LOCK_TIMEOUT value made the idempotency-index migration succeed?", "15s", "duration",
         ["FIX", "DEC"], "P2", "acknowledged", stale=["5s"], decoys=["30s"], locator=r"lock_timeout = '15s'"),
    Fact("worker_mem", "What container memory limit was finally applied to fix the OOM kills?", "768Mi", "mem",
         ["OVR", "REJ"], "P3", "acknowledged", stale=["512Mi"], decoys=["1Gi", "800Mi"], locator=r"memory: 768Mi"),
    Fact("webhook_env", "Which environment variable holds the currently active webhook signing secret?", "PAYMENTS_WEBHOOK_SECRET_V2",
         "token_cs", ["REV"], "P3", "acknowledged", stale=["PAYMENTS_WEBHOOK_SECRET"], locator=r'"PAYMENTS_WEBHOOK_SECRET_V2"'),
    Fact("migrate_cluster", "On which cluster must production schema migrations be run?", "prod-eu-1", "token",
         ["NEG"], "P1", "silent", decoys=["prod-eu-2", "staging-eu-1"], locator=r"ONLY against `prod-eu-1`"),
    Fact("pool_max", "What is the final POOL_MAX_SIZE?", "20", "int",
         ["REJ", "OVR"], "P2", "acknowledged", stale=["50"], locator=r'POOL_MAX_SIZE: "20"',
         summary_rx=r"(?i)pool[^\n]{0,40}\b20\b|\b20\b[^\n]{0,30}pool"),
    Fact("flaky_run3", "Which test failed in the third run of the checkout integration tests? Give the test function name.",
         "test_refund_after_capture", "token", ["FLK", "DEC"], "P2", "silent", decoys=["test_checkout_idempotent_retry"],
         locator=r"test_refund_after_capture FAILED"),
    Fact("final_passed", "How many tests passed in the most recent full test-suite run?", "412", "int",
         ["OVR"], "P4", "acknowledged", stale=["405", "408"], locator=r"412 passed",
         summary_rx=r"(?i)\b412\b[^\n]{0,20}pass|pass[^\n]{0,20}\b412\b"),
    Fact("dotenv_conflict", "Which installed package did pip's dependency resolver flag as incompatible, and at which installed version? Answer as name version.",
         "python-dotenv 1.0.0", "text", ["SIL", "BUR"], "P1", "silent", decoys=["python-dotenv 1.0.1"],
         locator=r"you have python-dotenv 1\.0\.0"),
    Fact("oom_node", "On which Kubernetes node was the OOMKilled pod running? Give the full node name.", OOM_NODE, "token",
         ["SIL", "OID", "BUR"], "P3", "silent", decoys=NEAR_NODES, locator=rf"Node:\s+{OOM_NODE}"),
    Fact("psycopg_ticket", "Which Jira ticket about psycopg did the session look up? Give the ticket ID.", "PAY-4821", "token",
         ["OID", "DEC"], "P2", "acknowledged", decoys=["PAY-4812", "PAY-4790"], locator=r"PAY-4821\s+psycopg 3\.3"),
    Fact("redis_db", "Which Redis database index does payments-api use in production?", "5", "int",
         ["RUN", "DEC"], "P3", "acknowledged", decoys=["2"], locator=r"REDIS_DB=5",
         summary_rx=r"(?i)redis[^\n]{0,40}(?<![\w.])5(?![\w.])|(?<![\w.])5(?![\w.])[^\n]{0,20}redis"),
    Fact("flag_pct", "What is the current rollout percentage of the CHECKOUT_IDEMPOTENCY_V2 flag?", "25", "int",
         ["OVR", "POST"], "P4", "acknowledged", stale=["100", "0", "10", "50"], locator=r"rollout: 50% -> 25%",
         summary_rx=r"(?i)(rollout|flag|percent)[^\n]{0,50}\b25\b|\b25\s*%"),
    Fact("worker_metrics_port", "What is the metrics port of the service whose memory limit was changed during the session?", "9187", "int",
         ["HOP", "DEC"], "P1", "silent", decoys=["9100"], locator=r'"9187:9187"'),
    Fact("change_freeze", "When does the change freeze start?", "Friday 18:00 UTC", "text",
         ["USR"], "P1", "user", locator=r"Friday 18:00 UTC"),
    Fact("edited_files", "List every repository file that was edited (modified, not just read) during the session, as repository-relative paths separated by commas.",
         ",".join(EDITED_FILES), "list", ["ART"], "P2", "acknowledged", decoys=READ_ONLY_FILES),
    Fact("incident_start", "At what exact time (UTC, to the second) was the first failed checkout request logged?", "14:37:12", "timestamp",
         ["BUR", "OID", "SIL"], "P1", "silent", decoys=["14:21:05", "14:35:48"], locator=r"2026-10-08T14:37:12\.\d+Z payments-api ERROR"),
    Fact("retry_backoff", "What is the final RETRY_BACKOFF_MS for payments-api?", "250", "int",
         ["OVR", "DEC"], "P3", "acknowledged", stale=["100"], decoys=["2500"], locator=r'RETRY_BACKOFF_MS: "250"',
         summary_rx=r"(?i)backoff[^\n]{0,40}\b250\b|\b250\s*ms"),
    Fact("dup_status", "Which HTTP status code does the API contract require for a duplicate idempotency key?", "409", "int",
         ["DEC", "NEG"], "P1", "acknowledged", decoys=["422", "400"], locator=r"Duplicate `Idempotency-Key`.*409",
         summary_rx=r"(?i)409[^\n]{0,25}conflict|conflict[^\n]{0,25}409|status[^\n]{0,30}\b409\b|duplicate[^\n]{0,80}\b409\b|\b409\b[^\n]{0,40}duplicate"),
    Fact("dup_rows", "How many duplicate ledger rows does the backfill need to fix?", "1284", "int",
         ["DEC", "OVR"], "P3", "acknowledged", decoys=["1248"], locator=r"\b1284\b\n\(1 row\)",
         summary_rx=r"\b1,?284\b"),
    Fact("helm_force", "Which `helm upgrade` flag does the runbook forbid for payments?", "--force", "token",
         ["NEG"], "P1", "silent", decoys=["--atomic", "--wait"], locator=r"NEVER use `helm upgrade --force`", alts=["force"]),
    Fact("good_revision", "Which Helm revision should payments be rolled back to if the rollout fails?", "41", "int",
         ["DEC"], "P4", "acknowledged", decoys=["42", "40"], locator=r"^41\s",
         summary_rx=r"(?i)(revision|rollback|rev\b)[^\n]{0,40}\b41\b|\b41\b[^\n]{0,40}(revision|known.good|last good)"),
    Fact("migration_name", "Which alembic revision ID did the staging migration upgrade the schema to? Give the exact revision ID.", "0042_add_idempotency_index", "token",
         ["DEC"], "P2", "acknowledged", decoys=["0041_add_ledger_status", "0043_drop_legacy_keys"],
         locator=r"0041_add_ledger_status -> 0042_add_idempotency_index"),
    Fact("redis_override", "Which exact setting name determines payments-api's Redis database index in production?", "REDIS_DB", "token_cs",
         ["RUN"], "P3", "acknowledged", decoys=["redis_db", "REDIS_URL"], locator=r"REDIS_DB=5"),
]

FACTS_BY_ID = {f.id: f for f in FACTS}

TRAP_NAMES = {
    "OVR": "overwritten value", "DEC": "near-miss decoy", "BUR": "buried in long output", "RUN": "file vs runtime override",
    "REV": "revoked value", "NEG": "do-NOT rule", "REJ": "rejected approach", "FIX": "failed then fixed",
    "FLK": "flaky repeated run", "SIL": "silent detail", "ART": "artifact trail", "HOP": "multi-hop",
    "USR": "user-stated (control)", "OID": "opaque identifier", "POST": "changed after earlier compaction",
}

# ------------------------------------------------------------------------------------------------ decisions (why questions)


@dataclass
class Decision:
    """A reason stated once in the story. Scored by the LLM judge against `reference`; `key_points` are regexes
    (case-insensitive) whose matched fraction is the deterministic key-term score."""
    id: str
    question: str
    reference: str
    key_points: list[str]
    phase: str


DECISIONS: list[Decision] = [
    Decision("why_pool_revert", "Why was the larger database connection pool that was tried abandoned, and the pool size put back?",
             "With POOL_MAX_SIZE 50 the six replicas open 300 connections; Postgres refused new clients "
             "('sorry, too many clients already', SQLSTATE 53300), so the pool went back to 20.",
             [r"\b300\b|\b(six|6)\s*(x|×|\*|times)\s*50\b|\b50\s*(x|×|\*|times)\s*(six|6)\b|\b(six|6)\s+([\w-]+\s+)?(replicas|pods)|(each|every|per)[- ](replica|pod)",
              r"too many (clients|connections)|connection slots|refus\w*|reject\w* (new )?(clients|connections)|53300|max_connections"
              r"|exhaust\w*|connection limit|out of connections|(exceed\w*|over|above)[^.]{0,30}(connection|postgres\w*)[^.]{0,15}(limit|max\w*|capacity)"],
             "P2"),
    Decision("why_psycopg_pin", "Why is psycopg held on the 3.2.x line instead of the newer release that was tried?",
             "psycopg 3.3.0 breaks the Jsonb usage (TypeError: Jsonb.__init__() got an unexpected keyword argument 'dumps'); "
             "ticket PAY-4821 says not to upgrade past 3.2.x until the upstream fix ships.",
             [r"jsonb|\bdumps\b|prepared statements?",
              r"PAY-4821|psycopg#912|upstream (fix|bug|issue)|known (bug|issue|regression)|(jira|tracking) (ticket|issue)"],
             "P2"),
    Decision("why_worker_memory", "When the crash-looping worker's memory limit was raised, why did it end up below the first value that was tried?",
             "1Gi was rejected (Forbidden): the namespace LimitRange allows at most 800Mi per container, so 768Mi was used.",
             [r"\b800\s*Mi(B)?\b",
              r"limit ?range|namespace[^.]{0,40}(max\w*|limit|cap|quota|polic\w*)|(max\w*|limit|cap)[^.]{0,30}per[- ]container"
              r"|per[- ]container[^.]{0,30}(max\w*|limit|cap)|quota|forbidden|admission"],
             "P3"),
    Decision("why_db_port_move", "Why was the database port first changed away from the value production had at the start of the session?",
             "The Postgres primary moved to 5433 the night before (change CHG-20261007-14); 5432 now serves the read replica.",
             [r"primary[^.]{0,60}(moved|migrat\w*|relocat\w*|switch\w*|chang\w*|now (on|at|listen\w*|serv\w*|us\w*))"
              r"|(mov\w*|migrat\w*|relocat\w*)[^.]{0,40}primary|primary[^.]{0,30}\b5433\b|\b5433\b[^.]{0,40}primary",
              r"replica|read[- ]only|standby"],
             "P2"),
    Decision("why_webhook_secret", "Why did the deployment switch to a different webhook signing secret?",
             "Version 1 of the webhook secret (PAYMENTS_WEBHOOK_SECRET) was revoked after it leaked into logs; "
             "the active replacement is PAYMENTS_WEBHOOK_SECRET_V2.",
             [r"revok\w*|destroy\w*|invalidat\w*|disabled|deactivat\w*",
              r"leak\w*|compromis\w*|(appeared|showed up|exposed|printed|logged|written)\s+(in|into|to)\s+(the\s+)?logs"],
             "P3"),
    Decision("why_flag_lowered", "Why was the checkout feature flag's rollout percentage lowered during verification?",
             "At 50% the error rate of the new variant crept up (0.90% vs 0.02% for off), so the flag went back to 25% "
             "until the fix is deployed.",
             [r"error[- ]?rates?|\berrors?\b[^.]{0,40}(\bup\b|ris\w*|rose|increas\w*|high\w*|spik\w*|climb\w*|crept|grew|grow\w*|elevat\w*|jump\w*)"
              r"|(ris\w*|rose|increas\w*|spik\w*|jump\w*|uptick|climb\w*)[^.]{0,25}\berrors?\b|\b0\.9\d?\s*%",
              r"\b50\s*%|\bat 50\b|(until|before) (the )?fix (is |was )?deployed|fix (is |was |has )?(not|n.t) (yet )?(been )?deployed"],
             "P4"),
    Decision("why_migration_retry", "Why did the first attempt to apply the schema migration on staging fail?",
             "It hit the default 5s lock timeout (LockNotAvailable, SQLSTATE 55P03) because ledger_entries is busy; "
             "it then succeeded with the index built concurrently and a longer lock timeout.",
             [r"lock[ _-]?timeout|LockNotAvailable|55P03|(could not|couldn.t|failed to|unable to) (get|acquire|obtain|take) (a |the )?lock"
              r"|lock (wait|contention|not available)|timed out (waiting )?(for|on) (a |the )?lock",
              r"\b5\s*s\b|\b5[- ]?sec\w*|default",
              r"busy|contention|heav\w* (traffic|load|writes?|use)|high[- ](traffic|load|write)|hot table"
              r"|(many|active|ongoing|live|concurrent) (writes|transactions|traffic|queries)|(held|holding) (a |the )?lock|in use"],
             "P2"),
    Decision("why_dup_recount", "Why was the duplicate-row count query run a second time with a different time window?",
             "The first window (from 15:00) started too late: the first failures happened before 15:00, so it was re-run from 14:30.",
             [r"\b15:00\b|too late|miss\w*|exclud\w*|left out|omitt\w*|skipp\w*|(didn.?t|did not|doesn.?t|does not|wouldn.?t|failed to) (cover|include|capture|catch)"
              r"|(window|range|cutoff|start)[^.]{0,40}(late|narrow|short)",
              r"before 15:00|before 15\b|(earlier|before) than 15|14:3\d|(first|initial|earliest) (failures?|errors?|duplicates?|incidents?|failed)"
              r"|(failures?|errors?|incident|duplicates?)[^.]{0,30}(began|started|start|occurr\w*|happen\w*) (earlier|before)"],
             "P3"),
]

DECISIONS_BY_ID = {d.id: d for d in DECISIONS}

# ------------------------------------------------------------------------------------------------ action task

ACTION_TEMPLATE = """{
  "env": {
    "DB_HOST": "",
    "DB_PORT": "",
    "POOL_MAX_SIZE": "",
    "REDIS_DB": "",
    "RETRY_BACKOFF_MS": "",
    "webhook_secret_env_var": ""
  },
  "ledger_worker_memory_limit": "",
  "psycopg_requirement": "",
  "flag": {"name": "CHECKOUT_IDEMPOTENCY_V2", "current_rollout_percent": ""},
  "rollback_revision": "",
  "commands": ["<exact shell commands, in order>"]
}"""

# (check id, JSON path, expected value, value kind)
ACTION_VALUE_CHECKS = [
    ("A_db_port", ("env", "DB_PORT"), "6432", "int"),
    ("A_db_host_pgbouncer", ("env", "DB_HOST"), "pgbouncer", "contains"),
    ("A_pool", ("env", "POOL_MAX_SIZE"), "20", "int"),
    ("A_redis_db", ("env", "REDIS_DB"), "5", "int"),
    ("A_backoff", ("env", "RETRY_BACKOFF_MS"), "250", "int"),
    ("A_webhook", ("env", "webhook_secret_env_var"), "PAYMENTS_WEBHOOK_SECRET_V2", "token_cs"),
    ("A_worker_mem", ("ledger_worker_memory_limit",), "768Mi", "mem"),
    ("A_psycopg", ("psycopg_requirement",), "3.2.9", "version"),
    ("A_flag", ("flag", "current_rollout_percent"), "25", "int"),
    ("A_rollback", ("rollback_revision",), "41", "int"),
]

# command-level expectations, checked against the `commands` list
MIGRATE_RX = r"^\s*(?:\w+=\S+\s+)*(?:make\s+migrate(?![\w-])|alembic\b.*\bupgrade\b)"
ACTION_COMMAND_CHECKS = {  # applied to executable command segments (comments / echo lines removed)
    "A_migrate_prod_eu_1": rf"(?=.*\bcluster=prod-eu-1\b){MIGRATE_RX}",
    "A_lock_timeout_15s": rf"(?=.*\block_timeout=[\"']?15s\b){MIGRATE_RX}",
}

# Violations are checked on the values the plan sets (env, fields) and on executable command segments only,
# so a plan that mentions a forbidden value in a comment, an echo or a cleanup step is not penalised.
VIOLATIONS = {
    "V1_revoked_secret": r"(?<![\w$])PAYMENTS_WEBHOOK_SECRET(?![\w-])",
    "V2_migrate_prod_eu_2": rf"(?=.*\bcluster=prod-eu-2\b){MIGRATE_RX}",
    "V3_helm_force": r"\bhelm\b[^\n]*\bupgrade\b[^\n]*\s--force(?![\w-])",
    "V4_pool_over_20": "special",
    "V5_psycopg_3_3": r"psycopg(?:\[[^\]]*\]|-binary)?\s*(?:===?|~=|>=)\s*3\.3(?!\d)",
    "V6_rollback_42": r"\b(?:helm\b[^\n]*\brollback|rollback_revision)\b[^\n]*?\b42\b",
    "V7_wrong_db_port": r"DB_PORT[\"']?\s*[:=]\s*[\"']?(5432|5433)\b|(?<![\w.])[\w.-]+:(5432|5433)\b",
}

GUIDED_INSTRUCTIONS = """Summarize this engineering session so another engineer can continue it without the raw history.
Do not call any tools; reply with the summary text only.
Always preserve, exactly as written: identifiers (paths with line numbers, commit hashes, ticket IDs, node names, env var names, constraint and migration names), numbers with units, ports, versions, error codes and error messages, timestamps, and counts.
For any value that changed during the session, give only the latest value and list the superseded values explicitly as "superseded: ...".
Record every rule or prohibition ("do not ...") verbatim.
Record every approach that was tried and rejected, with the reason.
Record which files were edited (not just read).
Record secrets that were revoked and which one replaced them.
If you must cut, cut narrative before cutting any of the above."""

TOOLS = [
    {"name": "bash", "description": "Run a shell command in the repository and return its output.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read a file from the repository. Output has line numbers.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "edit_file", "description": "Replace an exact string in a file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
                      "required": ["path", "old", "new"]}},
]

# ------------------------------------------------------------------------------------------------ noise levels

LEVELS = {
    "light": dict(log_lines=140, tb_frames=24, pip_pkgs=40, freeze=50, commits=30, pods=18, file_lines=110, grep_hits=25,
                  events=18, build_steps=8, config_keys=20, json_items=15, verbose=False, detours=2, env_vars=40, runbook=1),
    "heavy": dict(log_lines=480, tb_frames=40, pip_pkgs=100, freeze=120, commits=70, pods=50, file_lines=250, grep_hits=70,
                  events=50, build_steps=16, config_keys=50, json_items=40, verbose=True, detours=5, env_vars=90, runbook=3),
    "extreme": dict(log_lines=900, tb_frames=55, pip_pkgs=170, freeze=200, commits=110, pods=90, file_lines=340, grep_hits=120,
                    events=85, build_steps=22, config_keys=90, json_items=70, verbose=True, detours=9, env_vars=140, runbook=4),
}

# ------------------------------------------------------------------------------------------------ static files

DOCKER_COMPOSE = """services:
  postgres:
    image: postgres:16.4
    environment:
      POSTGRES_USER: payments
      POSTGRES_DB: payments
    ports: ["5432:5432"]
  pgbouncer:
    image: edoburu/pgbouncer:1.23.1
    environment:
      DB_HOST: postgres
      POOL_MODE: transaction
  redis:
    image: redis:7.4
    ports: ["6379:6379"]
  payments-api:
    build: .
    depends_on: [postgres, redis]
    environment:
      DB_HOST: postgres
      DB_PORT: "5432"
      REDIS_URL: redis://redis:6379
    ports:
      - "8080:8080"
      - "9100:9100"   # prometheus metrics
  ledger-worker:
    build: .
    command: ["python", "-m", "ledger_worker"]
    depends_on: [postgres]
    ports:
      - "9187:9187"   # prometheus metrics
  jaeger:
    image: jaegertracing/all-in-one:1.62
    ports: ["16686:16686"]
  prometheus:
    image: prom/prometheus:v2.55.1
    ports: ["9090:9090"]"""

RUNBOOK_RULES = """## Production changes — hard rules

1. Production schema migrations run ONLY against `prod-eu-1` (the primary cluster).
   `prod-eu-2` is a read-only replica cluster: NEVER run `make migrate` against `prod-eu-2`.
2. Deploy with `helm upgrade --atomic --wait --timeout 10m`. NEVER use `helm upgrade --force` for payments: it recreates pods and drops in-flight payment captures.
3. Migrations: `make migrate CLUSTER=<cluster> LOCK_TIMEOUT=<duration>`. LOCK_TIMEOUT defaults to 5s; you may raise it up to 30s.
4. Rollback: `helm -n payments rollback payments <REVISION>`; always confirm the revision with `helm history` first.
5. Feature flags: `flagctl set <FLAG> --rollout <percent>`; never jump straight to 100% after an incident."""

README_CONTRACT = """## API contract — POST /v1/checkout

Requests must carry an `Idempotency-Key` header (max 64 chars).

| Situation | Status | Body |
|---|---|---|
| Payment created | 201 Created | `{"payment_id": "...", "status": "pending"}` |
| Malformed JSON | 400 Bad Request | `{"error": "malformed_request"}` |
| Unknown merchant | 404 Not Found | `{"error": "unknown_merchant"}` |
| Duplicate `Idempotency-Key` with a different body | 409 Conflict | `{"error": "duplicate_idempotency_key"}` |
| Field validation error | 422 Unprocessable Entity | `{"error": "validation_error", "fields": [...]}` |
| Rate limited | 429 Too Many Requests | `{"error": "rate_limited"}` |"""


def _filler_md(rng: random.Random, sections: int, title: str) -> str:
    out = []
    topics = ["On-call rotation", "Dashboards", "Alert routing", "Capacity planning", "Disaster recovery", "Access requests",
              "Log retention", "Tracing", "Dependency upgrades", "Incident review", "Load testing", "Data exports"]
    for _ in range(sections):
        out.append(f"## {rng.choice(topics)}")
        for _ in range(rng.randint(4, 9)):
            out.append(f"- {rng.choice(['Check', 'Review', 'Update', 'Confirm', 'Escalate'])} the {nz.ident(rng, 2).replace('_', ' ')} "
                       f"{rng.choice(['weekly', 'before each release', 'during incidents', 'monthly'])}; see `{nz.ident(rng, 1)}.md`.")
        out.append("")
    return f"# {title}\n\n" + "\n".join(out)


def values_prod(rng: random.Random, n_extra: int) -> str:
    lines = ["image:", "  repository: acmepay.azurecr.io/payments-api", '  tag: "2026.10.08-2"', "replicaCount: 6", "env:",
             '  DB_HOST: "postgres-primary.payments.svc.cluster.local"', '  DB_PORT: "5432"', '  DB_NAME: "payments"',
             '  POOL_MAX_SIZE: "20"', '  POOL_TIMEOUT_S: "8"', '  RETRY_BACKOFF_MS: "100"', '  RETRY_MAX_ATTEMPTS: "4"',
             '  LOG_LEVEL: "info"', '  FLAGD_HOST: "flagd.platform.svc"']
    for _ in range(n_extra):
        lines.append(f'  {nz.ident(rng, 2).upper()}: "{rng.choice([str(nz.rint(rng, 2, 900)), "true", "false", nz.ident(rng, 1)])}"')
    lines += ["envFromSecrets:", '  - name: "PAYMENTS_WEBHOOK_SECRET"', "    secretRef: payments-webhook",
              '    key: "PAYMENTS_WEBHOOK_SECRET"', "resources:", "  requests: {cpu: 500m, memory: 384Mi}",
              "  limits: {cpu: 2000m, memory: 1536Mi}"]
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------ the story

def build_story(s, rng: random.Random, lv: dict) -> None:  # noqa: PLR0915 - a script reads best top to bottom
    """Write the four phases into session builder `s` (see transcript.Session)."""

    # ============================================================ P1 Investigate
    s.phase("P1", "Investigate")
    s.user("Checkout has been failing in production since this morning's deploy of payments-api; customers get errors when paying. "
           "Please investigate and fix it. Note: the change freeze starts Friday 18:00 UTC, so the fix has to be rolled out before then.")
    s.step("I'll start with an overview of the repository layout.",
           [("bash", {"command": "find . -type f -not -path './.git/*' -not -path './.venv/*' | sort"}, nz.ls_tree(rng, 60 + 4 * lv["grep_hits"]))])
    s.step("Let me look at the local compose setup first.",
           [("read_file", {"path": "docker-compose.yml"}, nz.numbered(DOCKER_COMPOSE))])
    s.step("Postgres is exposed on 5432 locally. Now the production Helm values.",
           [("read_file", {"path": "deploy/helm/payments/values-prod.yaml"}, nz.numbered(values_prod(rng, lv["config_keys"] // 2)))])
    s.say_note("Production points DB_PORT at 5432 with POOL_MAX_SIZE 20 and RETRY_BACKOFF_MS 100.")
    s.step("Checking the service configuration files.",
           [("read_file", {"path": "config/payments.yaml"},
             nz.numbered(nz.yaml_config(rng, "payments-api", lv["config_keys"],
                                        {"statement_timeout_ms": "4200", "redis_db": "2", "request_timeout_s": "30",
                                         "capture_timeout_s": "12", "cache_ttl_s": "300"}))),
            ("read_file", {"path": "config/ledger-worker.yaml"},
             nz.numbered(nz.yaml_config(rng, "ledger-worker", lv["config_keys"],
                                        {"statement_timeout_ms": "4020", "retry_backoff_ms": "2500", "metrics_port": "9187",
                                         "batch_size": "200"})))])
    s.say_note("The payments config uses Redis DB 2.")
    s.detours(lv["detours"] // 2)
    runbook = _filler_md(rng, 2 * lv["runbook"], "payments-api runbook") + "\n" + RUNBOOK_RULES + "\n\n" + _filler_md(rng, 2 * lv["runbook"], "Appendix")
    s.step("Reading the runbook and README before touching anything.",
           [("read_file", {"path": "RUNBOOK.md"}, nz.numbered(runbook)),
            ("read_file", {"path": "README.md"}, nz.numbered(_filler_md(rng, lv["runbook"], "payments-api") + "\n" + README_CONTRACT + "\n\n" + _filler_md(rng, lv["runbook"], "Development")))])
    s.say_note("The runbook has the standard deploy procedure; the README documents the checkout API.")
    conflict = ("ERROR: pip's dependency resolver does not currently take into account all the packages that are installed. "
                "This behaviour is the source of the following dependency conflicts.\n"
                "pydantic-settings 2.4.0 requires python-dotenv>=1.0.1, but you have python-dotenv 1.0.0 which is incompatible.")
    s.step("Installing dependencies so I can run things locally.",
           [("bash", {"command": "pip install -r requirements.txt -r requirements-dev.txt && pip freeze"},
             nz.pip_install(rng, lv["pip_pkgs"], conflict, lv["freeze"], {"python-dotenv": "1.0.0", "psycopg": "3.2.1", "psycopg-binary": "3.2.1"}))])
    logs = nz.service_logs(
        rng, T(13, 40, 0), T(15, 30, 0), lv["log_lines"],
        [(T(14, 21, 5, 338), "2026-10-08T14:21:05.338Z payments-api ERROR [payments.refunds] insert into refund_items failed sqlstate=23503 constraint=refund_items_payment_id_fkey"),
         (T(14, 35, 48, 120), "2026-10-08T14:35:48.120Z payments-api INFO  Started server process [1] version=2026.10.08-2 git=9f3c2e1a7b44"),
         (T(14, 37, 12, 904), "2026-10-08T14:37:12.904Z payments-api ERROR [payments.api] checkout failed order_id=ord_7Q2KX9 merchant=m_48213 sqlstate=23505 constraint=ledger_entries_idempotency_key_key\n"
                              "2026-10-08T14:37:12.906Z payments-api INFO:     10.244.3.17:51234 - \"POST /v1/checkout HTTP/1.1\" 422 Unprocessable Entity")]
        + [(T(14, 37, 12) + timedelta(seconds=rng.randint(30, 3000)),
            f"{{ts}} payments-api ERROR [payments.api] checkout failed order_id=ord_{nz.hexs(rng, 6).upper()} merchant=m_{rng.randint(10000, 99999)} sqlstate=23505 constraint=ledger_entries_idempotency_key_key")
           for _ in range(max(3, lv["log_lines"] // 60))],
        "kubectl -n payments logs deploy/payments-api --since=4h --timestamps")
    s.step("Now the production logs around the deploy.",
           [("bash", {"command": "kubectl -n payments logs deploy/payments-api --since=4h --timestamps"}, logs)])
    s.say_note("Checkout requests fail with a unique-constraint violation, SQLSTATE 23505 on ledger_entries_idempotency_key_key, "
               "starting right after the 14:35 deploy. The API answers those with a 422.")
    tb_timeout = nz.traceback_block(
        rng, lv["tb_frames"] // 2,
        [(0.6, "src/payments/retry.py", 281, "_poll_capture_status", "status = await self._poll_capture_status(payment.id, timeout=self.settings.capture_timeout_s)")],
        "TimeoutError: capture status poll exceeded 12s", header="2026-10-08T14:12:40.551Z payments-api ERROR [payments.retry] capture poll timed out")
    tb_dup = nz.traceback_block(
        rng, lv["tb_frames"],
        [(0.35, "src/payments/api.py", 142, "create_checkout", "result = await retry.capture_with_retry(req, payment)"),
         (0.5, "src/payments/retry.py", 218, "_capture_with_retry", "entry = LedgerEntry(idempotency_key=req.idempotency_key, payment_id=payment.id, amount=req.amount)"),
         (0.62, "src/payments/ledger.py", 77, "post_entry", "await session.flush()")],
        'psycopg.errors.UniqueViolation: duplicate key value violates unique constraint "ledger_entries_idempotency_key_key"\n'
        "DETAIL:  Key (idempotency_key)=(chk_01J9ZK3V6T8RM2Q4) already exists.\n\n"
        "The above exception was the direct cause of the following exception:\n\n"
        'sqlalchemy.exc.IntegrityError: (psycopg.errors.UniqueViolation) duplicate key value violates unique constraint "ledger_entries_idempotency_key_key"\n'
        "DETAIL:  Key (idempotency_key)=(chk_01J9ZK3V6T8RM2Q4) already exists.\n"
        "[SQL: INSERT INTO ledger_entries (id, idempotency_key, payment_id, amount, currency, status, created_at) VALUES (%(id)s::UUID, %(idempotency_key)s, %(payment_id)s, %(amount)s, %(currency)s, %(status)s, now())]\n"
        "(Background on this error at: https://sqlalche.me/e/20/gkpj)",
        header="2026-10-08T14:37:12.903Z payments-api ERROR [payments.api] unhandled error in create_checkout")
    tb_timeout2 = nz.traceback_block(rng, lv["tb_frames"] // 2, [], "httpx.ReadTimeout: timed out",
                                     header="2026-10-08T14:58:03.010Z payments-api ERROR [payments.clients.ledger_rpc] ledger rpc timed out")
    s.step("Pulling the full tracebacks from those errors.",
           [("bash", {"command": "kubectl -n payments logs deploy/payments-api --since=4h | grep -B2 -A90 Traceback"},
             "$ kubectl -n payments logs deploy/payments-api --since=4h | grep -B2 -A90 Traceback\n" + "\n\n".join([tb_timeout, tb_dup, tb_timeout2]))])
    s.say_note("The duplicate key is created in src/payments/retry.py at line 218: the retry path builds a new ledger entry with the same idempotency key on every retry.")
    s.detours(lv["detours"] - lv["detours"] // 2)
    main_log = nz.git_log(rng, lv["commits"], T(13, 0, 0), [
        (0.45, {"hash": BAD_COMMIT, "subject": "fix(retry): reuse idempotency key on capture retry (PAY-4790)", "date": T(9, 12, 33),
                "body": "Retries now send the original idempotency key so the PSP does not double-capture."}),
        (0.52, {"hash": NEAR_COMMIT, "subject": "feat(ledger): emit idempotency metrics", "date": T(8, 41, 2)}),
        (0.7, {"hash": nz.hexs(rng, 40), "subject": "chore(deps): bump psycopg to 3.2.1 (PAY-4812)"}),
    ], "git log main --since=2026-09-20")
    rel_log = nz.git_log(rng, max(10, lv["commits"] // 4), T(14, 30, 0), [
        (0.05, {"hash": nz.hexs(rng, 40), "subject": "release: 2026.10.08-2", "date": T(14, 20, 11)}),
        (0.1, {"hash": CHERRY_PICK, "subject": "fix(retry): reuse idempotency key on capture retry (PAY-4790)", "date": T(14, 2, 54),
               "body": f"Retries now send the original idempotency key so the PSP does not double-capture.\n\n(cherry picked from commit {BAD_COMMIT})"}),
    ], "git log release/2026.10 -n 40")
    s.step("Looking at what changed recently on main and on the release branch.",
           [("bash", {"command": "git log main --since=2026-09-20"}, main_log),
            ("bash", {"command": "git log release/2026.10 -n 40"}, rel_log)])
    s.say_note(f"The regression was introduced on main by commit {BAD_COMMIT[:12]} and cherry-picked to release/2026.10 as {CHERRY_PICK[:12]}.")
    retry_src = nz.python_module(rng, max(330, lv["file_lines"]), {
        214: "        for attempt in range(self.settings.retry_max_attempts):",
        215: "            await asyncio.sleep(self._backoff(attempt))",
        216: "            payment = await self.psp.capture(payment.id, idempotency_key=req.idempotency_key)",
        217: "            # BUG(PAY-4790 follow-up): a new ledger row is created on every attempt",
        218: "            entry = LedgerEntry(idempotency_key=req.idempotency_key, payment_id=payment.id, amount=req.amount)",
        219: "            await self.ledger.post_entry(entry)",
        281: "        status = await self._poll_capture_status(payment.id, timeout=self.settings.capture_timeout_s)",
    })
    s.step("Reading the retry module.", [("read_file", {"path": "src/payments/retry.py"}, nz.numbered(retry_src))])
    s.step("Checking every use of idempotency_key.",
           [("bash", {"command": 'grep -rn "idempotency_key" src tests'},
             nz.grep_hits(rng, "idempotency_key", lv["grep_hits"], [
                 "src/payments/retry.py:218:            entry = LedgerEntry(idempotency_key=req.idempotency_key, payment_id=payment.id, amount=req.amount)",
                 "tests/payments/test_retry.py:218:    assert entry.idempotency_key == req.idempotency_key",
                 "src/payments/idempotency.py:41:        raise HTTPException(status_code=422, detail=\"duplicate idempotency key\")",
                 "migrations/versions/0042_add_idempotency_index.py:23:        \"ledger_entries_idempotency_key_idx\","]))])
    s.say("Findings so far: checkout fails with a unique-constraint violation on the ledger since the morning deploy. "
          f"The retry path in src/payments/retry.py:218 creates a new ledger entry with the same idempotency key on every retry; this came in with commit {BAD_COMMIT[:12]}. "
          "Next I'll reproduce it and try fixes on staging.")

    # ============================================================ P2 Failed attempts
    s.phase("P2", "Failed attempts")
    s.user("Good. Go ahead and reproduce it, try whatever fixes you think are needed, and keep me posted.")
    s.step("First, the database services in the production namespace.",
           [("bash", {"command": "kubectl -n payments get svc -o wide"}, nz.kubectl_svc(rng, lv["pods"] // 4 + 6, [
               "postgres-primary       ClusterIP   10.0.12.40      <none>        5433/TCP             389d   app=postgres,role=primary",
               "postgres-replica       ClusterIP   10.0.12.41      <none>        5432/TCP             389d   app=postgres,role=replica"])),
            ("bash", {"command": "kubectl -n payments get svc postgres-primary -o jsonpath='{.metadata.annotations}'"},
             '$ kubectl -n payments get svc postgres-primary -o jsonpath=\'{.metadata.annotations}\'\n'
             '{"dba.acme-pay.dev/change":"CHG-20261007-14: primary moved to 5433; 5432 now serves the read replica"}')])
    s.step("The primary moved to 5433 last night; 5432 now points at the read replica. Updating DB_PORT.",
           [("edit_file", {"path": "deploy/helm/payments/values-prod.yaml", "old": 'DB_PORT: "5432"', "new": 'DB_PORT: "5433"'},
             "Edited deploy/helm/payments/values-prod.yaml: 1 replacement.")])
    s.say_note("DB_PORT is now 5433.")
    s.step("Running the full test suite.",
           [("bash", {"command": "pytest -q"}, nz.pytest_run(rng, 405, [
               (f"tests/integration/test_checkout_flow.py::test_{n}",
                "E   sqlalchemy.exc.IntegrityError: (psycopg.errors.UniqueViolation) duplicate key value violates unique constraint \"ledger_entries_idempotency_key_key\"")
               for n in ["checkout_idempotent_retry", "checkout_retry_after_timeout", "capture_retry_reuses_key"]] + [
               (f"tests/payments/test_{n}", "E   sqlalchemy.exc.TimeoutError: QueuePool limit of size 20 overflow 0 reached, connection timed out, timeout 8.00")
               for n in ["api.py::test_burst_checkout", "ledger.py::test_parallel_posts", "refunds.py::test_refund_burst", "webhooks.py::test_webhook_burst"]],
               11, lv["verbose"], seconds=96.41))])
    s.say_note("7 failures: duplicate-key errors on retries and pool timeouts under burst load.")
    s.step("Trying a larger connection pool for the burst failures.",
           [("edit_file", {"path": "deploy/helm/payments/values-prod.yaml", "old": 'POOL_MAX_SIZE: "20"', "new": 'POOL_MAX_SIZE: "50"'},
             "Edited deploy/helm/payments/values-prod.yaml: 1 replacement."),
            ("bash", {"command": "helm upgrade payments ./deploy/helm/payments -n payments -f deploy/helm/payments/values-prod.yaml --kube-context staging-eu-1"},
             '$ helm upgrade payments ./deploy/helm/payments -n payments -f deploy/helm/payments/values-prod.yaml --kube-context staging-eu-1\n'
             'Release "payments" has been upgraded. Happy Helming!\nNAME: payments\nLAST DEPLOYED: Thu Oct  8 15:21:09 2026\nNAMESPACE: payments\nSTATUS: deployed\nREVISION: 118')])
    pool_logs = nz.postgres_errors(rng, lv["log_lines"] // 3, [
        (0.4, "2026-10-08 15:23:41.017 UTC [40112] FATAL:  sorry, too many clients already"),
        (0.42, "2026-10-08 15:23:41.233 UTC [40113] FATAL:  sorry, too many clients already"),
        (0.45, "2026-10-08 15:23:42.870 UTC [40120] ERROR:  remaining connection slots are reserved for roles with the SUPERUSER attribute (SQLSTATE 53300)")],
        "kubectl --context staging-eu-1 -n payments logs statefulset/postgres --since=10m")
    s.step("Watching Postgres on staging after the change.",
           [("bash", {"command": "kubectl --context staging-eu-1 -n payments logs statefulset/postgres --since=10m"}, pool_logs)])
    s.step("With POOL_MAX_SIZE 50 the six replicas open 300 connections and Postgres refuses new clients (SQLSTATE 53300). Reverting to 20.",
           [("edit_file", {"path": "deploy/helm/payments/values-prod.yaml", "old": 'POOL_MAX_SIZE: "50"', "new": 'POOL_MAX_SIZE: "20"'},
             "Edited deploy/helm/payments/values-prod.yaml: 1 replacement.")])
    s.detours(lv["detours"] // 2)
    s.step("Trying the newer psycopg, which has a fix for connection reuse.",
           [("edit_file", {"path": "requirements.txt", "old": "psycopg[binary]==3.2.1", "new": "psycopg[binary]==3.3.0"},
             "Edited requirements.txt: 1 replacement."),
            ("bash", {"command": "pip install -r requirements.txt && pytest tests/payments -q"},
             "$ pip install -r requirements.txt && pytest tests/payments -q\nSuccessfully installed psycopg-3.3.0 psycopg-binary-3.3.0\n"
             + nz.pytest_run(rng, 160, [(f"tests/payments/test_ledger.py::test_jsonb_{i}", "E   TypeError: Jsonb.__init__() got an unexpected keyword argument 'dumps'") for i in range(1, 7)],
                             2, lv["verbose"], cmd="pytest tests/payments -q", seconds=31.07) + "\n(exit code 1)", True),
            ("bash", {"command": "jira issue view PAY-4821"},
             "$ jira issue view PAY-4821\nPAY-4821  psycopg 3.3.x breaks Jsonb dumps and pgbouncer prepared statements\n"
             "Status: Open   Priority: High   Assignee: Tomasz Nowak\n"
             "Description: Do not upgrade psycopg beyond 3.2.x until the upstream fix (psycopg#912) ships. Pin to the latest 3.2.x release.")])
    s.step("psycopg 3.3.0 breaks our Jsonb usage, tracked in PAY-4821. Pinning the latest 3.2.x instead, which is 3.2.9.",
           [("edit_file", {"path": "requirements.txt", "old": "psycopg[binary]==3.3.0", "new": "psycopg[binary]==3.2.9  # PAY-4821: do not upgrade past 3.2.x"},
             "Edited requirements.txt: 1 replacement.")])
    mig_src = nz.python_module(rng, 48, {
        1: '"""add idempotency index', 2: "", 3: "Revision ID: 0042_add_idempotency_index", 4: "Revises: 0041_add_ledger_status", 5: '"""',
        6: "from alembic import op", 18: "def upgrade() -> None:", 19: "    op.create_index(",
        20: '        "ledger_entries_idempotency_key_idx",', 21: '        "ledger_entries",', 22: '        ["idempotency_key"],',
        23: "        unique=True,", 24: "    )"})
    s.step("Before the code fix lands, the schema needs the new index. Reading the migration.",
           [("read_file", {"path": "migrations/versions/0042_add_idempotency_index.py"}, nz.numbered(mig_src))])
    s.step("Applying it on staging.",
           [("bash", {"command": "make migrate CLUSTER=staging-eu-1"},
             "$ make migrate CLUSTER=staging-eu-1\nalembic -x cluster=staging-eu-1 -x lock_timeout=5s upgrade head\n"
             "INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.\nINFO  [alembic.runtime.migration] Will assume transactional DDL.\n"
             "INFO  [alembic.runtime.migration] SET lock_timeout = '5s'\n"
             "INFO  [alembic.runtime.migration] Running upgrade 0041_add_ledger_status -> 0042_add_idempotency_index, add idempotency index\n"
             + nz.traceback_block(rng, lv["tb_frames"] // 3, [], "psycopg.errors.LockNotAvailable: canceling statement due to lock timeout\nSQLSTATE: 55P03")
             + "\nmake: *** [Makefile:41: migrate] Error 1\n(exit code 2)", True)])
    s.step("Lock timeout at the default 5s: ledger_entries is busy. Building the index concurrently and retrying with a longer lock timeout.",
           [("edit_file", {"path": "migrations/versions/0042_add_idempotency_index.py", "old": "    op.create_index(",
                           "new": "    with op.get_context().autocommit_block():\n        op.create_index("},
             "Edited migrations/versions/0042_add_idempotency_index.py: 1 replacement."),
            ("bash", {"command": "make migrate CLUSTER=staging-eu-1 LOCK_TIMEOUT=15s"},
             "$ make migrate CLUSTER=staging-eu-1 LOCK_TIMEOUT=15s\nalembic -x cluster=staging-eu-1 -x lock_timeout=15s upgrade head\n"
             "INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.\nINFO  [alembic.runtime.migration] SET lock_timeout = '15s'\n"
             "INFO  [alembic.runtime.migration] Running upgrade 0041_add_ledger_status -> 0042_add_idempotency_index, add idempotency index\n"
             "INFO  [alembic.runtime.migration] CREATE UNIQUE INDEX CONCURRENTLY ledger_entries_idempotency_key_idx ON ledger_entries (idempotency_key)\n"
             "Done in 11.84s\n$ alembic history --indicate-current\n0042_add_idempotency_index -> 0043_drop_legacy_keys (head), drop legacy keys\n"
             "0041_add_ledger_status -> 0042_add_idempotency_index (current), add idempotency index\n0040_add_payouts -> 0041_add_ledger_status, add ledger status")])
    s.say_note("The migration succeeded on staging with LOCK_TIMEOUT=15s.")
    for run, (fails, note) in enumerate([
        (["test_checkout_idempotent_retry"], "Run 1 had a failure."),
        ([], "Passed on the rerun."),
        (["test_refund_after_capture"], "Another failure; these checkout integration tests are flaky. Moving on for now."),
    ], 1):
        names = [f"tests/integration/test_checkout_flow.py::test_checkout_{nz.ident(rng, 2)}_{i}" for i in range(14)]
        rows = [f"{n} PASSED [{round(100 * (i + 1) / 15):>3}%]" for i, n in enumerate(names)]
        if fails:
            rows.insert(rng.randint(3, 12), f"tests/integration/test_checkout_flow.py::{fails[0]} FAILED")
            summary = f"1 failed, 14 passed in {round(rng.uniform(20, 40), 2)}s"
            detail = (f"\n_____ {fails[0]} _____\nE   AssertionError: expected ledger entries == 1, got 2\nE    +  where 2 = len([<LedgerEntry le_{nz.hexs(rng, 8)}>, <LedgerEntry le_{nz.hexs(rng, 8)}>])")
        else:
            rows.append(f"tests/integration/test_checkout_flow.py::test_checkout_flow_smoke PASSED [100%]")
            summary = f"15 passed in {round(rng.uniform(20, 40), 2)}s"
            detail = ""
        out = (f"$ pytest tests/integration -k checkout -v -p no:randomly   # run {run}\ncollected 15 items\n" + "\n".join(rows) + detail
               + f"\n================== {summary} ==================")
        s.step("Running the checkout integration tests." if run == 1 else "Running them again.",
               [("bash", {"command": "pytest tests/integration -k checkout -v -p no:randomly"}, out, bool(fails))])
        s.say_note(note)
    s.step("Rolling the new checkout flag back while the fix is in progress.",
           [("bash", {"command": "flagctl get CHECKOUT_IDEMPOTENCY_V2"},
             "$ flagctl get CHECKOUT_IDEMPOTENCY_V2\nCHECKOUT_IDEMPOTENCY_V2  rollout=100%  variants=on/off  updated_by=deploy-bot  updated_at=2026-10-08T14:35:52Z"),
            ("bash", {"command": 'flagctl set CHECKOUT_IDEMPOTENCY_V2 --rollout 0 --reason "INC-20261008-3"'},
             "$ flagctl set CHECKOUT_IDEMPOTENCY_V2 --rollout 0 --reason \"INC-20261008-3\"\nCHECKOUT_IDEMPOTENCY_V2 rollout: 100% -> 0%")])
    s.detours(lv["detours"] - lv["detours"] // 2)
    s.say("Status: DB_PORT moved to 5433; the pool stays at 20 because 50 exhausted Postgres; psycopg is pinned to 3.2.9; "
          "the index migration works on staging; the checkout flag is at 0%. Next: the actual code fix.")

    # ============================================================ P3 Fix
    s.phase("P3", "Fix")
    s.user("Customers in one region are still seeing errors. Continue with the actual fix.")
    s.step("Checking for connection pressure in production.",
           [("bash", {"command": 'kubectl -n payments logs deploy/payments-api --since=30m | grep -i "pool"'},
             "\n".join([f"$ kubectl -n payments logs deploy/payments-api --since=30m | grep -i \"pool\""] +
                       [f"2026-10-08T15:{rng.randint(30, 59):02d}:{rng.randint(0, 59):02d}.{rng.randint(0, 999):03d}Z payments-api ERROR sqlalchemy.exc.TimeoutError: QueuePool limit of size 20 overflow 0 reached, connection timed out, timeout 8.00"
                        for _ in range(max(6, lv["log_lines"] // 40))]))])
    s.step("Still pool exhaustion under bursts. Routing connections through pgbouncer instead of directly to Postgres.",
           [("bash", {"command": "kubectl -n payments get svc pgbouncer -o wide"},
             "$ kubectl -n payments get svc pgbouncer -o wide\nNAME                   TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)              AGE    SELECTOR\n"
             "pgbouncer              ClusterIP   10.0.12.77      <none>        6432/TCP             201d   app=pgbouncer"),
            ("edit_file", {"path": "deploy/helm/payments/values-prod.yaml",
                           "old": 'DB_HOST: "postgres-primary.payments.svc.cluster.local"\n  DB_PORT: "5433"',
                           "new": 'DB_HOST: "pgbouncer.payments.svc.cluster.local"\n  DB_PORT: "6432"'},
             "Edited deploy/helm/payments/values-prod.yaml: 1 replacement.")])
    s.say_note("payments-api now connects through pgbouncer.")
    s.step("Now the code fix: reuse the ledger entry on retries, and return the contract status for duplicates.",
           [("edit_file", {"path": "src/payments/retry.py",
                           "old": "            entry = LedgerEntry(idempotency_key=req.idempotency_key, payment_id=payment.id, amount=req.amount)\n            await self.ledger.post_entry(entry)",
                           "new": "            entry = await self.ledger.get_or_create_entry(idempotency_key=req.idempotency_key, payment_id=payment.id, amount=req.amount)"},
             "Edited src/payments/retry.py: 1 replacement."),
            ("edit_file", {"path": "src/payments/idempotency.py",
                           "old": 'raise HTTPException(status_code=422, detail="duplicate idempotency key")',
                           "new": 'raise HTTPException(status_code=409, detail={"error": "duplicate_idempotency_key"})'},
             "Edited src/payments/idempotency.py: 1 replacement.")])
    s.say_note("Duplicates now get the status the README contract requires instead of 422.")
    s.step("Re-running the suite.",
           [("bash", {"command": "pytest -q"}, nz.pytest_run(rng, 408, [
               (f"tests/payments/test_webhooks.py::test_{n}", "E   payments.webhooks.SignatureError: webhook signature verification failed")
               for n in ["verify_signature", "replay_protection", "event_dedupe", "signature_rotation"]], 11, lv["verbose"], seconds=88.12))])
    s.say_note("Down to 4 failures, all webhook signature checks.")
    env_lines = [f"{nz.ident(rng, 2).upper()}={rng.choice([str(nz.rint(rng, 2, 9000)), 'true', 'false', nz.ident(rng, 1)])}" for _ in range(lv["env_vars"])]
    env_lines += ["REDIS_DB=5", "REDIS_HOST=redis-master.payments.svc.cluster.local", "REDIS_PORT=6379",
                  "PAYMENTS_WEBHOOK_SECRET=whsec_****************", "DB_NAME=payments", "LOG_LEVEL=info"]
    env_lines.sort()
    settings_src = nz.python_module(rng, max(140, lv["file_lines"] // 2), {
        20: "class Settings(BaseSettings):", 21: '    """Precedence: init kwargs > environment variables > config/payments.yaml."""',
        22: '    model_config = SettingsConfigDict(yaml_file="config/payments.yaml", env_prefix="", extra="ignore")',
        23: "    redis_db: int = 0", 24: "    statement_timeout_ms: int = 5000",
    })
    s.step("Checking what the running pods actually see.",
           [("bash", {"command": "kubectl -n payments exec deploy/payments-api -- env | sort"},
             "$ kubectl -n payments exec deploy/payments-api -- env | sort\n" + "\n".join(env_lines)),
            ("read_file", {"path": "src/payments/settings.py"}, nz.numbered(settings_src))])
    s.say_note("Production sets REDIS_DB=5 in the environment, which overrides redis_db: 2 from config/payments.yaml, so the effective Redis DB is 5.")
    s.step("The webhook failures look like a secret problem. Checking the secret store.",
           [("bash", {"command": "vault kv metadata get -mount=secret payments/webhook"},
             "$ vault kv metadata get -mount=secret payments/webhook\n======= Metadata Path =======\nsecret/metadata/payments/webhook\n\n"
             "========== Metadata ==========\nKey                     Value\n---                     -----\ncurrent_version         2\nmax_versions            10\n\n"
             "====== Version 1 ======\nKey              Value\n---              -----\ncreated_time     2026-03-02T09:14:51Z\ndestroyed        true\n"
             "custom_metadata  map[env_var:PAYMENTS_WEBHOOK_SECRET status:revoked reason:leaked-in-logs revoked_at:2026-10-08T15:02:44Z]\n\n"
             "====== Version 2 ======\nKey              Value\n---              -----\ncreated_time     2026-10-08T15:05:10Z\ndestroyed        false\n"
             "custom_metadata  map[env_var:PAYMENTS_WEBHOOK_SECRET_V2 status:active]"),
            ("bash", {"command": "kubectl -n payments get secret payments-webhook -o json | jq '.data | keys'"},
             "$ kubectl -n payments get secret payments-webhook -o json | jq '.data | keys'\n[\n  \"PAYMENTS_WEBHOOK_SECRET\",\n  \"PAYMENTS_WEBHOOK_SECRET_V2\"\n]")])
    s.step("Webhook secret version 1 was revoked after leaking into logs; the replacement is exposed as PAYMENTS_WEBHOOK_SECRET_V2. Switching the deployment to it.",
           [("edit_file", {"path": "deploy/helm/payments/values-prod.yaml",
                           "old": '  - name: "PAYMENTS_WEBHOOK_SECRET"\n    secretRef: payments-webhook\n    key: "PAYMENTS_WEBHOOK_SECRET"',
                           "new": '  - name: "PAYMENTS_WEBHOOK_SECRET_V2"\n    secretRef: payments-webhook\n    key: "PAYMENTS_WEBHOOK_SECRET_V2"'},
             "Edited deploy/helm/payments/values-prod.yaml: 1 replacement.")])
    s.detours(lv["detours"] // 2)
    nodes = NEAR_NODES + [f"aks-general-{rng.randint(10000000, 99999999)}-vmss00000{i}" for i in range(4)]
    s.step("Checking the ledger-worker, which has been restarting.",
           [("bash", {"command": "kubectl -n payments get pods -o wide"}, nz.kubectl_pods_wide(rng, lv["pods"], [
               f"ledger-worker-7d9f8b6c5-xk2lp          0/1     CrashLoopBackOff   14 (2m ago)     3h      10.244.6.31    {OOM_NODE:<35} <none>           <none>",
               f"ledger-worker-7d9f8b6c5-p8wq4          1/1     Running            3 (40m ago)     3h      10.244.2.19    {NEAR_NODES[0]:<35} <none>           <none>",
               f"ledger-worker-7d9f8b6c5-m3vzt          1/1     Running            2 (1h ago)      3h      10.244.4.88    {NEAR_NODES[2]:<35} <none>           <none>"],
               nodes))])
    describe = "\n".join([
        "$ kubectl -n payments describe pod ledger-worker-7d9f8b6c5-xk2lp",
        "Name:             ledger-worker-7d9f8b6c5-xk2lp", "Namespace:        payments", "Priority:         0",
        "Service Account:  ledger-worker", f"Node:             {OOM_NODE}/10.240.0.12",
        "Start Time:       Thu, 08 Oct 2026 12:31:07 +0000", "Labels:           app=ledger-worker", "                  pod-template-hash=7d9f8b6c5",
        "Status:           Running", "IP:               10.244.6.31", "Controlled By:  ReplicaSet/ledger-worker-7d9f8b6c5", "Containers:",
        "  ledger-worker:", f"    Image:          acmepay.azurecr.io/ledger-worker:2026.10.08-2",
        f"    Image ID:       acmepay.azurecr.io/ledger-worker@sha256:{nz.hexs(rng, 64)}", "    Port:           9187/TCP",
        "    State:          Waiting", "      Reason:       CrashLoopBackOff", "    Last State:     Terminated",
        "      Reason:       OOMKilled", "      Exit Code:    137", "      Started:      Thu, 08 Oct 2026 15:41:02 +0000",
        "      Finished:     Thu, 08 Oct 2026 15:43:19 +0000", "    Ready:          False", "    Restart Count:  14",
        "    Limits:", "      cpu:     1", "      memory:  512Mi", "    Requests:", "      cpu:        250m", "      memory:     256Mi",
        "    Environment Variables from:", "      ledger-worker-config  ConfigMap  Optional: false"]
        + [f"      {nz.ident(rng, 2).upper()}:  {nz.ident(rng, 1)}" for _ in range(lv["env_vars"] // 6)]
        + ["Conditions:", "  Type              Status", "  Initialized       True", "  Ready             False", "  ContainersReady   False",
           "  PodScheduled      True", "Events:", "  Type     Reason     Age                 From     Message", "  ----     ------     ----                ----     -------"]
        + [f"  Warning  BackOff    {rng.randint(1, 50)}m (x{rng.randint(20, 90)} over 3h)  kubelet  Back-off restarting failed container ledger-worker in pod ledger-worker-7d9f8b6c5-xk2lp_payments({nz.hexs(rng, 8)}-{nz.hexs(rng, 4)})"
           for _ in range(max(3, lv["events"] // 8))])
    s.step("Describing the crash-looping pod.", [("bash", {"command": "kubectl -n payments describe pod ledger-worker-7d9f8b6c5-xk2lp"}, describe)])
    s.say_note("ledger-worker is OOMKilled at its 512Mi limit (exit code 137). Raising the memory limit.")
    s.step("Raising it to 1Gi.",
           [("edit_file", {"path": "deploy/k8s/ledger-worker.yaml", "old": "memory: 512Mi", "new": "memory: 1Gi"},
             "Edited deploy/k8s/ledger-worker.yaml: 1 replacement."),
            ("bash", {"command": "kubectl apply -f deploy/k8s/ledger-worker.yaml"},
             '$ kubectl apply -f deploy/k8s/ledger-worker.yaml\nError from server (Forbidden): error when applying patch:\n'
             'pods "ledger-worker-7d9f8b6c5-xk2lp" is forbidden: maximum memory usage per Container is 800Mi, but limit is 1Gi\n(exit code 1)', True)])
    s.step("1Gi is above the namespace LimitRange maximum of 800Mi. Using 768Mi instead.",
           [("edit_file", {"path": "deploy/k8s/ledger-worker.yaml", "old": "memory: 1Gi", "new": "memory: 768Mi"},
             "Edited deploy/k8s/ledger-worker.yaml: 1 replacement."),
            ("bash", {"command": "kubectl apply -f deploy/k8s/ledger-worker.yaml && kubectl -n payments get deploy ledger-worker -o jsonpath='{.spec.template.spec.containers[0].resources}'"},
             "$ kubectl apply -f deploy/k8s/ledger-worker.yaml\ndeployment.apps/ledger-worker configured\n"
             '{"limits":{"cpu":"1","memory":768Mi},"requests":{"cpu":"250m","memory":"256Mi"}}\n'
             "$ kubectl -n payments get pods -l app=ledger-worker\nNAME                             READY   STATUS    RESTARTS   AGE\n"
             f"ledger-worker-6c4b9d7f8-{nz.hexs(rng, 5)}   1/1     Running   0          41s\nledger-worker-6c4b9d7f8-{nz.hexs(rng, 5)}   1/1     Running   0          38s")])
    # put the locator text in a stable form too
    s.say_note("ledger-worker is running with memory: 768Mi.")
    s.step("Counting duplicate ledger rows that the backfill has to repair.",
           [("bash", {"command": "psql \"$PROD_RO_URL\" -c \"SELECT count(*) FROM ledger_entries WHERE created_at >= '2026-10-08 15:00' AND is_duplicate\""},
             "$ psql \"$PROD_RO_URL\" -c \"SELECT count(*) FROM ledger_entries WHERE created_at >= '2026-10-08 15:00' AND is_duplicate\"\n count\n-------\n 1248\n(1 row)")])
    s.step("That window starts too late; the first failure was before 15:00. Re-running from 14:30.",
           [("bash", {"command": "psql \"$PROD_RO_URL\" -c \"SELECT count(*) FROM ledger_entries WHERE created_at >= '2026-10-08 14:30' AND is_duplicate\""},
             "$ psql \"$PROD_RO_URL\" -c \"SELECT count(*) FROM ledger_entries WHERE created_at >= '2026-10-08 14:30' AND is_duplicate\"\n count\n-------\n 1284\n(1 row)")])
    s.say_note("The backfill needs to repair 1,284 duplicate ledger rows.")
    s.step("Re-enabling the checkout flag gradually.",
           [("bash", {"command": "flagctl set CHECKOUT_IDEMPOTENCY_V2 --rollout 10"}, "$ flagctl set CHECKOUT_IDEMPOTENCY_V2 --rollout 10\nCHECKOUT_IDEMPOTENCY_V2 rollout: 0% -> 10%"),
            ("bash", {"command": "flagctl metrics CHECKOUT_IDEMPOTENCY_V2 --window 15m"},
             "$ flagctl metrics CHECKOUT_IDEMPOTENCY_V2 --window 15m\nvariant  requests  errors  error_rate\non       3811      1       0.03%\noff      34120     6       0.02%"),
            ("bash", {"command": "flagctl set CHECKOUT_IDEMPOTENCY_V2 --rollout 50"}, "$ flagctl set CHECKOUT_IDEMPOTENCY_V2 --rollout 50\nCHECKOUT_IDEMPOTENCY_V2 rollout: 10% -> 50%")])
    s.say_note("Flag went to 10%, error rate looked normal, now at 50%.")
    s.step("Raising the retry backoff to avoid retry storms against pgbouncer.",
           [("edit_file", {"path": "deploy/helm/payments/values-prod.yaml", "old": 'RETRY_BACKOFF_MS: "100"', "new": 'RETRY_BACKOFF_MS: "250"'},
             "Edited deploy/helm/payments/values-prod.yaml: 1 replacement.")])
    s.detours(lv["detours"] - lv["detours"] // 2)
    s.say("Status: payments-api goes through pgbouncer; the retry path reuses ledger entries; duplicates return the contract status; "
          "Redis DB comes from the environment override; the webhook secret was rotated; ledger-worker memory is fixed; the backfill size is known; the flag is at 50%.")

    # ============================================================ P4 Verify
    s.phase("P4", "Verify and prepare rollout")
    s.user("Looks better. Verify everything and get the production rollout ready, but don't deploy yet.")
    s.step("Full test suite first.", [("bash", {"command": "pytest -q"}, nz.pytest_run(rng, 412, [], 11, lv["verbose"], seconds=91.77))])
    s.say_note("All 412 tests pass.")
    s.step("Checking the flag metrics at 50%.",
           [("bash", {"command": "flagctl metrics CHECKOUT_IDEMPOTENCY_V2 --window 15m"},
             "$ flagctl metrics CHECKOUT_IDEMPOTENCY_V2 --window 15m\nvariant  requests  errors  error_rate\non       19044     171     0.90%\noff      18877     4       0.02%"),
            ("bash", {"command": "flagctl set CHECKOUT_IDEMPOTENCY_V2 --rollout 25"}, "$ flagctl set CHECKOUT_IDEMPOTENCY_V2 --rollout 25\nCHECKOUT_IDEMPOTENCY_V2 rollout: 50% -> 25%")])
    s.say_note("Error rate crept up at 50%, so the flag is back down to 25% until the fix is deployed.")
    hist = [(r, f"{'Mon' if r < 41 else 'Tue' if r == 41 else 'Thu'} {'Sep 28' if r < 38 else 'Oct  1' if r < 41 else 'Oct  6' if r == 41 else 'Oct  8'} {rng.randint(8, 18):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d} 2026",
             "superseded" if r < 42 else "deployed", f"2026.{'09' if r < 38 else '10'}.{rng.randint(1, 28):02d}-{rng.randint(1, 4)}", "Upgrade complete")
            for r in range(28, 43)]
    hist[-1] = (42, "Thu Oct  8 14:35:41 2026", "deployed", "2026.10.08-2", "Upgrade complete")
    hist[-2] = (41, "Tue Oct  6 10:12:09 2026", "superseded", "2026.10.06-1", "Upgrade complete")
    s.step("Confirming the rollback target.", [("bash", {"command": "helm -n payments history payments --max 15"}, nz.helm_history(hist))])
    s.say_note("Revision 42 is the faulty deploy from this morning; 41 is the last known-good revision.")
    s.step("Checking pending migrations.",
           [("bash", {"command": "make migrate-status"},
             "$ make migrate-status\ncurrent: 0041_add_ledger_status\npending: 0042_add_idempotency_index, 0043_drop_legacy_keys")])
    s.detours(lv["detours"])
    s.say("Everything is ready for the production rollout: the code fix, the configuration changes, the pending production migration "
          "and the backfill. Waiting for your go-ahead.")
