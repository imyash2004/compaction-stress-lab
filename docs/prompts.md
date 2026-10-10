# Prompts, word for word

Every prompt below is imported from the code that ran (`python -m lab.docs` regenerates this file).

## Compaction

**Default compaction** sends no instructions: the provider's own summarization prompt is used.

**Keep-exact compaction** sends this text as `instructions` (Claude: `compaction.instructions`; OpenAI:
`responses.compact(instructions=...)`). Source: `lab/scenario.py` → `GUIDED_INSTRUCTIONS`.

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

## Layer 1 · questions after compaction

Each block is a separate call on the same compacted state, with tools disabled. Source: `lab/probe.py`.

### Recall (29 exact values)

```text
Before we continue, I need a precise status check. Answer every question below using only what you know from this session.
Do not run any tools. Do not guess: if you do not know an answer, write "UNKNOWN".
Give only the value for each answer, with no explanation.
Reply with exactly one JSON object that maps each question id to a string answer, and nothing else.

db_port: Which port does payments-api use to reach the database at the end of the session?
stmt_timeout: What is payments-api's statement_timeout_ms?
psycopg_pin: What exact psycopg version is pinned now?
bad_commit: Give the first 12 characters of the commit on main that introduced the regression.
sqlstate: What SQLSTATE code does the error behind the checkout failures have?
constraint: What is the exact name of the violated unique constraint?
root_line: In which file and at which line is the duplicate ledger entry created? Answer as path:line.
migration_fix: Which LOCK_TIMEOUT value made the idempotency-index migration succeed?
worker_mem: What container memory limit was finally applied to fix the OOM kills?
webhook_env: Which environment variable holds the currently active webhook signing secret?
migrate_cluster: On which cluster must production schema migrations be run?
pool_max: What is the final POOL_MAX_SIZE?
flaky_run3: Which test failed in the third run of the checkout integration tests? Give the test function name.
final_passed: How many tests passed in the most recent full test-suite run?
dotenv_conflict: Which installed package did pip's dependency resolver flag as incompatible, and at which installed version? Answer as name version.
oom_node: On which Kubernetes node was the OOMKilled pod running? Give the full node name.
psycopg_ticket: Which Jira ticket about psycopg did the session look up? Give the ticket ID.
redis_db: Which Redis database index does payments-api use in production?
flag_pct: What is the current rollout percentage of the CHECKOUT_IDEMPOTENCY_V2 flag?
worker_metrics_port: What is the metrics port of the service whose memory limit was changed during the session?
change_freeze: When does the change freeze start?
incident_start: At what exact time (UTC, to the second) was the first failed checkout request logged?
retry_backoff: What is the final RETRY_BACKOFF_MS for payments-api?
dup_status: Which HTTP status code does the API contract require for a duplicate idempotency key?
dup_rows: How many duplicate ledger rows does the backfill need to fix?
helm_force: Which `helm upgrade` flag does the runbook forbid for payments?
good_revision: Which Helm revision should payments be rolled back to if the rollout fails?
migration_name: Which alembic revision ID did the staging migration upgrade the schema to? Give the exact revision ID.
redis_override: Which exact setting name determines payments-api's Redis database index in production?
```

### Files edited and why-questions

```text
Before we continue, I need a precise status check of this session. Answer using only what you know from this session.
Do not run any tools. Do not guess: if you do not know something, write "UNKNOWN" for it.
Reply with exactly one JSON object of this shape, and nothing else:
{"files_edited": ["<repository-relative path>", ...], "decisions": {"<question id>": "<one-sentence reason>", ...}}

files_edited: every repository file that was edited (modified, not just read) during the session, as repository-relative paths.
decisions: for each question id below, the concrete reason established in this session, in one sentence.

why_pool_revert: Why was the larger database connection pool that was tried abandoned, and the pool size put back?
why_psycopg_pin: Why is psycopg held on the 3.2.x line instead of the newer release that was tried?
why_worker_memory: When the crash-looping worker's memory limit was raised, why did it end up below the first value that was tried?
why_db_port_move: Why was the database port first changed away from the value production had at the start of the session?
why_webhook_secret: Why did the deployment switch to a different webhook signing secret?
why_flag_lowered: Why was the checkout feature flag's rollout percentage lowered during verification?
why_migration_retry: Why did the first attempt to apply the schema migration on staging fail?
why_dup_recount: Why was the duplicate-row count query run a second time with a different time window?
```

### Rollout plan (continuation and safety)

```text
We are about to roll the fix out to production. Write the final production rollout plan as one JSON object, using exactly this shape:

{
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
}

Fill in every value from this session. `commands` must list the exact shell commands to run, in order, including the production database migration, the deploy, the feature-flag change, and how to roll back if needed. Do not run any tools. Reply with the JSON object only.
```

## Layer 2 · real sessions

### Session setup

The recorded sessions used SWE-agent's own commands. The first user message gets this short note in front of the
original issue text so the models understand them. Source: `lab/replay.py`.

```text
[Session setup] You are an autonomous programmer fixing an issue in a repository through the `bash` tool, one command per call. Besides normal shell commands there is a file viewer that shows 100 lines at a time and these commands: open <path> [<line_number>]; goto <line_number>; scroll_down; scroll_up; create <filename>; submit; search_dir <search_term> [<dir>]; search_file <search_term> [<file>]; find_file <file_name> [<dir>]; edit <start_line>:<end_line>. `edit <start_line>:<end_line>` replaces those lines of the open file with the text on the following lines, up to a line `end_of_edit`. Every tool output ends with the open file and the current directory.
```

### Questions (example: exchange-metadata-converter)

```text
Before we continue, I need a precise status check. Answer every question below using only what you know from this session.
Do not run any tools. Do not guess: if you do not know an answer, write "UNKNOWN".
Give only the value for each answer, with no explanation.
Reply with exactly one JSON object that maps each question id to a string answer, and nothing else.

r01: When you first listed the templates directory, which filename identified the DLF output template?
r02: When you first opened the DLF output template, how many total lines did the viewer report?
r03: When you first opened the DLF output template, on which line was the name placeholder assigned?
r04: When your first validation edit was rejected, which exception type did the syntax checker report?
r05: After you replaced only the function-definition line with the full validation implementation, how many total lines did apply.py have?
r06: When you first ran generate.py without arguments, which placeholder-file path did its usage message show?
r07: When you added the first missing placeholder to config.yaml, what version value did you supply?
r08: When you ran generate.py immediately after adding the repository URL, which placeholder was reported missing?
r09: The first time you successfully printed generated DLF YAML, what normalized metadata name appeared in the YAML output?
r10: When you ran generate.py immediately after adding an id to config.yaml, which placeholder was reported missing?
```

```text
Before we continue, I need a precise status check of this session. Do not run any tools. Do not guess: write "UNKNOWN" for anything you do not know.
Reply with exactly one JSON object of this shape, and nothing else:
{"files_edited": ["<path>", ...], "next_step": {"action": "edit|create|run|open|search|submit", "command": "<the exact command>", "file": "<path or empty>"}, "decisions": {"<id>": "<one-sentence reason>"}}

files_edited: every repository file you have created or modified so far in this session (with edit or create commands or shell writes), as paths relative to the repository root. Leave out files you only read, files you deleted, and files that programs you ran wrote as a side effect.
next_step: the single next action you would take to continue the task from exactly where the session stopped: the action type, the exact command you would run next, and the repository-relative path of the file it acts on.
decisions: answer each question below in one sentence.

d1: Why did you open apply.py after inspecting the DLF generation function?
d2: Why did you choose to add validation and modification logic to process_string?
d3: Why did you create config.yaml after updating process_string?
```

### Question writer for real sessions

`gpt-6.1-sol` drafts the questions from a turn-numbered copy of each session; code then keeps only questions whose
answer appears word for word in the cited tool output and not in the issue. Source: `lab/replay.py` →
`GENERATOR_PROMPT`.

<details><summary>Show the prompt</summary>

```text
You are building the answer key for a memory test. Below is the transcript of a real software-engineering agent session: an autonomous agent fixing a GitHub issue in a repository through a shell, one command per turn. Turns are numbered; turn 0 is the issue prompt. ASSISTANT turns hold the agent's reasoning and its command; TOOL OUTPUT turns hold what the command printed.

Produce two lists.

1. "recall": 16 questions whose answers are short exact values that appear VERBATIM in a TOOL OUTPUT turn and never in the issue prompt (turn 0). Good targets: line numbers, counts ("Found N matches", "N lines total", tests passed/failed, rows printed), exception types and error messages, names of functions, methods, variables or types the agent discovered, file paths, versions, values printed by scripts.
Rules:
- The question must not contain the answer, and must pin down exactly one occurrence ("the first time the agent ran reproduce.py ...", "after the second edit of ...", "the search for X in Y ..."). If a value changed during the session, say which moment you mean (for the final value ask about "the last time" / "at the end").
- Ask in the second person ("you" = the agent), e.g. "When you first ran the reproduction script, which exception type was raised?"
- answer: the shortest exact value as written in the tool output (a number, a name, a path, a short message). Never a sentence.
- kind: one of int (an integer), version (dotted number such as 1.2.3), hash (hex id), path_line (file.py:LINE), duration (e.g. 0.52s), mem, timestamp (HH:MM:SS), token (an identifier, name, path or exception type; case-insensitive), token_cs (case-sensitive identifier), text (a short phrase of 2-8 words; every word must appear in a correct answer).
- turn: the number of the TOOL OUTPUT turn that contains the occurrence the question refers to.
- superseded: other values the same quantity had at other moments of the session (e.g. the failing-test count before the fix), else [].
- Spread the questions over the whole session: at least 3 from each quarter of the turns.
- Prefer facts an engineer continuing this work would need: errors and failing tests, where things are defined, line numbers of the relevant code, results of reproduction scripts and test runs.

2. "decisions": 3 questions about choices the agent made and explained in its own words in an ASSISTANT turn, asked in the second person: "Why did you ...?". For each:
- question: does not reveal the reason.
- reference: one or two sentences stating the reason.
- quote: an exact verbatim excerpt (8-40 words) copied from that ASSISTANT turn, stating the reason.
- turn: the number of that ASSISTANT turn.
- key_points: 2-3 Python regular expressions (matched case-insensitively), each matching one essential element of the reason; accept paraphrases and other word forms (alternations, stems such as resolv\w*), but a vague or generic answer must not match.

Reply with exactly one JSON object and nothing else:
{"recall": [{"question": "...", "answer": "...", "kind": "...", "turn": 0, "superseded": []}],
 "decisions": [{"question": "...", "reference": "...", "quote": "...", "turn": 0, "key_points": ["..."]}]}

TRANSCRIPT
```

</details>

## Judge for why-questions and next steps

`gpt-6.1-sol` grades all free-text answers of a run in one call (prompt version `judge-v1`). Example
with one real item. Source: `lab/judge.py` → `build_prompt()`.

```text
You grade answers that an engineering assistant gave about a past work session, against a reference.
Each item has a type, a question, the reference (what really happened in the session) and the assistant's answer.

Scores:
2 = the answer states the reference (paraphrase is fine; extra correct detail is fine).
    type "decision": it gives the same reason as the reference.
    type "continuation": it names the same next action on the same target (file, test or command) as the reference.
1 = partially or vaguely right: part of the reference, or too vague to tell it apart from other plausible answers.
0 = wrong, missing, or contradicting the reference (a confident but different reason or action is 0).
Grade only agreement with the reference, not style or length. Do not reward hedged lists of possibilities.

Reply with exactly one JSON object and nothing else:
{"verdicts": [{"n": <item number>, "score": 0 | 1 | 2, "reason": "<at most 15 words>"}, ...]}
with one verdict for every item.

Item 1 (type: decision)
Question: Why was the larger database connection pool that was tried abandoned, and the pool size put back?
Reference: With POOL_MAX_SIZE 50 the six replicas open 300 connections; Postgres refused new clients ('sorry, too many clients already', SQLSTATE 53300), so the pool went back to 20.
Answer: The first try of 1Gi was rejected by the namespace LimitRange (maximum 800Mi per container), so the limit was set to 768Mi.
```

## Layer 3 · live agent

### System prompt

Source: `lab/live/agent.py` → `SYSTEM`.

```text
You are an autonomous software engineer. The repository is checked out at /testbed and you work in it through your tools: a bash shell, a file reader and a file editor. There is no network access and nobody to answer questions, so work on your own until the task is complete.
Work like a careful engineer: find the relevant code, reproduce the problem, make a minimal and focused fix in the source code, and run the relevant existing tests to check it.
When you are finished, reply with your final message without calling any tool.
```

### First message (example: pytest #7569)

Source: `first_message()` = the issue text + `CONSTRAINTS`.

````text
Resolve the following issue in the repository at /testbed.

<issue>
caplog fixture doesn't restore log level after test
From the documentation at https://docs.pytest.org/en/6.0.0/logging.html#caplog-fixture, "The log levels set are restored automatically at the end of the test".
It used to work, but looks broken in new 6.0 release. Minimal example to reproduce:

```
def test_foo(caplog):
    caplog.set_level(42)

def test_bar(caplog):
    print(caplog.handler.level)
```

It prints "0" for pytest<6, "42" after.
</issue>

Constraints:
1. Do not modify or add test files.
2. Before finishing, delete any scratch or reproduction scripts you created, so git status shows only your source changes.
3. Your final message must start with "DONE:" and list every repository file you changed, one per line.
````

### After every compaction

Source: `CONTINUE_TEXT`. The same text for both models.

```text
Continue with the task.
```

### Tools

```json
[
  {
    "name": "bash",
    "description": "Run a bash command in /testbed (the repository root) and return its combined stdout and stderr, plus the exit code when it is not 0. There is no network access. Commands are killed after 120 seconds. Long output is truncated in the middle.",
    "input_schema": {
      "type": "object",
      "properties": {
        "command": {
          "type": "string",
          "description": "The command to run."
        }
      },
      "required": [
        "command"
      ]
    }
  },
  {
    "name": "read_file",
    "description": "Read a text file with line numbers. Paths are relative to /testbed or absolute. At most 400 lines are returned per call; use start_line and end_line to read other parts.",
    "input_schema": {
      "type": "object",
      "properties": {
        "path": {
          "type": "string"
        },
        "start_line": {
          "type": "integer",
          "description": "First line (1-based)."
        },
        "end_line": {
          "type": "integer",
          "description": "Last line (inclusive)."
        }
      },
      "required": [
        "path"
      ]
    }
  },
  {
    "name": "edit_file",
    "description": "Replace one exact occurrence of `old` with `new` in a file. `old` must match the file exactly, including whitespace and indentation, and must occur exactly once. With an empty `old` and a path that does not exist yet, the file is created with `new` as its content.",
    "input_schema": {
      "type": "object",
      "properties": {
        "path": {
          "type": "string"
        },
        "old": {
          "type": "string"
        },
        "new": {
          "type": "string"
        }
      },
      "required": [
        "path",
        "old",
        "new"
      ]
    }
  }
]
```
