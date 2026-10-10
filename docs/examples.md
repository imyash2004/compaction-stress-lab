# Worked examples, one per layer

Real inputs and outputs from the runs. The full data for each run is in `runs/` after you run the study.

## Layer 1 · one fact, four memories

Claude Haiku 5.5, lab session at heavy noise (seed 13), one compaction. **Question:** *On which Kubernetes node
was the OOMKilled pod running?* **Truth:** `aks-payments-28471936-vmss00000c`. It appears once, in a
`kubectl describe pod` output, and is never mentioned again.

| Strategy | Answer | Scored |
|---|---|---|
| Full history | `aks-payments-28471936-vmss00000c` | CORRECT |
| Drop old tool outputs | `UNKNOWN` | UNKNOWN |
| Default compaction | `UNKNOWN` | UNKNOWN |
| Keep-exact compaction | `aks-payments-28471936-vmss00000c` | CORRECT |

**Default summary, lines about the crash** (Claude's summary is readable; 7,244 characters in total):

```text
5. **ledger-worker OOMKilled** at 512Mi (exit 137, CrashLoopBackOff). `deploy/k8s/ledger-worker.yaml` memory limit set to 768Mi and applied. The namespace LimitRange maximum is 800Mi, so 1Gi was rejected.
```

**Keep-exact summary, lines about the crash** (Claude's summary is readable; 12,540 characters in total):

```text
- `ledger-worker` OOMKilled (exit 137), restart count 14. Old pod `ledger-worker-7d9f8b6c5-xk2lp` on node `aks-payments-28471936-vmss00000c`.
- **`kubectl apply -f deploy/k8s/ledger-worker.yaml`** (no context given, presumably production `payments`). Pods `ledger-worker-6c4b9d7f8-f8694` and `ledger-worker-6c4b9d7f8-c3613` are Running. This is live and bypasses Helm. Root cause of the OOM is unknown.
```

**A confirmed wrong action.** GPT-6 Luna, keep-exact compaction, 2 compactions: asked for the rollout plan, it
named revision 42 as the rollback target. Revision 42 is the faulty deploy; 41 is the last good one.

```text
"rollback_revision": "42",
"if [ \"${ROLLBACK:-NO}\" = YES ]; then helm -n payments rollback payments 42; fi"
```

## Layer 2 · a real session

`CODAIT/exchange-metadata-converter`, task 38: *Non-compliant metadata.name in the generated DLF YAML*. The original agent took
45 actions; we cut the recording at 80% (24,068 tokens kept).

**The first steps, as the models see them** (each agent command is a `bash` tool call, each output a tool result):

| Command | First line of the output |
|---|---|
| `ls -a` | .git .github .gitignore .travis.yml LICENSE MANIFEST.in README.md dax-data-set-descriptors |
| `ls -a templates` | __init__.py dlf_out.yaml openaihub_out.yaml  bash-$ |
| `open templates/dlf_out.yaml` | [File: /exchange-metadata-converter/templates/dlf_out.yaml (23 lines total)] 1:# Copyright |
| `search_dir "name = " metadata_converter` | No matches found for "name = " in /exchange-metadata-converter/metadata_converter  bash-$ |

**The exact-value questions** (answers with full history vs after one default compaction):

| Question | Truth | Haiku full | Haiku compacted | Luna full | Luna compacted |
|---|---|---|---|---|---|
| When you first listed the templates directory, which filename identified the DLF output template? | `dlf_out.yaml` | `dlf_out.yaml` | `templates/dlf_out.yaml` | `dlf_out.yaml` | `dlf_out.yaml` |
| When you first opened the DLF output template, how many total lines did the viewer report? | `23` | `23` | `UNKNOWN` | `23` | `UNKNOWN` |
| When you first opened the DLF output template, on which line was the name placeholder assigned? | `17` | `17` | `UNKNOWN` | `17` | `UNKNOWN` |
| When your first validation edit was rejected, which exception type did the syntax checker report? | `IndentationError` | `IndentationError` | `UNKNOWN` | `IndentationError` | `UNKNOWN` |
| After you replaced only the function-definition line with the full validation implementation, how many total lines did apply.py have? | `271` | `271` | `UNKNOWN` | `271` | `271` |
| When you first ran generate.py without arguments, which placeholder-file path did its usage message show? | `/path/to/placeholder/yaml` | `/path/to/placeholder/yaml` | `UNKNOWN` | `/path/to/placeholder/yaml` | `UNKNOWN` |
| When you added the first missing placeholder to config.yaml, what version value did you supply? | `1.0.0` | `1.0.0` | `UNKNOWN` | `1.0.0` | `1.0.0` |
| When you ran generate.py immediately after adding the repository URL, which placeholder was reported missing? | `repository.mime_type` | `repository.mime_type` | `UNKNOWN` | `repository.mime_type` | `UNKNOWN` |
| The first time you successfully printed generated DLF YAML, what normalized metadata name appeared in the YAML output? | `finance-proposition-bank` | `finance-proposition-bank` | `finance-proposition-bank` | `finance-proposition-bank` | `finance-proposition-bank` |
| When you ran generate.py immediately after adding an id to config.yaml, which placeholder was reported missing? | `description` | `description` | `UNKNOWN` | `description` | `UNKNOWN` |

**Next step.** The real next action was `python metadata_converter/generate.py config.yaml`.

| Model | Memory | Proposed next step | Judge |
|---|---|---|---|
| Claude Haiku 5.5 | full history | {"action": "run", "command": "python metadata_converter/generate.py config.yaml", "file": "config.yaml"} | 2/2 |
| Claude Haiku 5.5 | default compaction | {"action": "open", "command": "read_file metadata_converter/apply.py", "file": "metadata_converter/apply.py"} | 0/2 |
| GPT-6 Luna | full history | {"action": "run", "command": "python metadata_converter/generate.py config.yaml", "file": "config.yaml"} | 2/2 |
| GPT-6 Luna | default compaction | {"action": "run", "command": "git checkout -- metadata_converter/apply.py", "file": "metadata_converter/apply. | 0/2 |

## Layer 3 · a live run

GPT-6 Luna fixing pytest issue #7569 (`caplog` doesn't restore the log level) with harsh compaction (every
6,000 characters). Result: **fixed** in 20 steps with 6
compactions; after them it re-opened `src/_pytest/logging.py` 8 times.

| Step | Tool calls |
|---|---|
| 1 | `bash(git status --short); bash(rg "class LogCaptureFixture\|set_level\|caplog" src)` |
| 2 | `bash(sed -n '1,230p' src/_pytest/logging.py && sed -n '…)` |
| 3 | `bash(sed -n '335,415p' src/_pytest/logging.py; rg "_fin…)` |
| 3 | **── compaction ──** |
| 4 | `bash(git status --short && grep -n "class LogCaptureFix…); bash(ls testing \| grep -E 'log\|cap'); read_file(src/_pytest/logging.py)` |
| 5 | `edit_file(src/_pytest/logging.py)` |
| 5 | **── compaction ──** |
| 6 | `bash(sed -n '330,435p' src/_pytest/logging.py && git st…)` |
| 7 | `bash(sed -n '435,490p' src/_pytest/logging.py)` |
| 8 | `edit_file(src/_pytest/logging.py)` |
| 8 | **── compaction ──** |
| 9 | `read_file(src/_pytest/logging.py); bash(git status --short && git diff --); bash(find testing -maxdepth 2 -type f \| head -50)` |
| 10 | `read_file(src/_pytest/logging.py)` |
| 10 | **── compaction ──** |
| 11 | `bash(git status --short && git diff -- src/_pytest/logg…); read_file(src/_pytest/logging.py)` |
| 12 | `edit_file(src/_pytest/logging.py)` |
| 12 | **── compaction ──** |
| 13 | `bash(git status --short && git diff -- src/_pytest/logg…); bash(ls)` |
| 14 | `bash(ls testing \| grep -i log; grep -R "set_level\\|init…)` |
| 15 | `bash(pytest -q testing/logging/test_fixture.py)` |
| 16 | `bash(pytest -q testing/logging); bash(git diff --check)` |
| 17 | `read_file(src/_pytest/logging.py)` |
| 18 | `read_file(src/_pytest/logging.py)` |
| 18 | **── compaction ──** |
| 19 | `bash(git status --short && git diff --check && git diff…)` |
| 20 | (final message) |

**Final message:**

```text
DONE: Fixed `caplog.set_level()` to restore the capture handler’s original level during fixture teardown.

Changed file:
src/_pytest/logging.py

Validation: `pytest -q testing/logging` — 57 passed; `git diff --check` succeeded.
```

**The agent's change** (`diff.patch`):

```diff
diff --git a/src/_pytest/logging.py b/src/_pytest/logging.py
index 11031f2..3c56f8c 100644
--- a/src/_pytest/logging.py
+++ b/src/_pytest/logging.py
@@ -346,6 +346,7 @@ class LogCaptureFixture:
         self._item = item
         # dict of log name -> log level
         self._initial_logger_levels = {}  # type: Dict[Optional[str], int]
+        self._initial_handler_level = None  # type: Optional[int]
 
     def _finalize(self) -> None:
         """Finalizes the fixture.
@@ -356,6 +357,8 @@ class LogCaptureFixture:
         for logger_name, level in self._initial_logger_levels.items():
             logger = logging.getLogger(logger_name)
             logger.setLevel(level)
+        if self._initial_handler_level is not None:
+            self.handler.setLevel(self._initial_handler_level)
 
     @property
     def handler(self) -> LogCaptureHandler:
@@ -433,6 +436,8 @@ class LogCaptureFixture:
         logger_obj = logging.getLogger(logger)
         # save the original log-level to restore it during teardown
         self._initial_logger_levels.setdefault(logger, logger_obj.level)
+        if self._initial_handler_level is None:
+            self._initial_handler_level = self.handler.level
         logger_obj.setLevel(level)
         self.handler.setLevel(level)
```

**Hidden tests:** 1/1 target test passing, 14/14 existing tests still passing.
