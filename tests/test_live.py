"""Offline tests for layer C (no API calls). Sandbox tests run only when Docker and the base image are available."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from lab.live import agent as ag
from lab.providers import CallResult
from lab.live import sandbox as sbx
from lab.live import tasks as tk

TEST_PATCH = """diff --git a/testing/test_a.py b/testing/test_a.py
--- a/testing/test_a.py
+++ b/testing/test_a.py
@@ -1 +1,2 @@
 x
+y
diff --git a/testing/test_new.py b/testing/test_new.py
new file mode 100644
--- /dev/null
+++ b/testing/test_new.py
@@ -0,0 +1 @@
+z
diff --git a/testing/data.json b/testing/data.json
--- a/testing/data.json
+++ b/testing/data.json
@@ -1 +1 @@
-{}
+[]
"""


def step(n, calls):
    """calls: [(name, args, output, exit_code, is_error)]"""
    return {"step": n, "calls": [{"id": f"c{n}{i}", "name": c[0], "args": c[1]} for i, c in enumerate(calls)],
            "outputs": [{"id": f"c{n}{i}", "output": c[2], "exit_code": c[3], "is_error": c[4]} for i, c in enumerate(calls)]}


class TextTests(unittest.TestCase):
    def test_truncate(self):
        self.assertEqual(sbx.truncate("abc", 10), "abc")
        s = "H" * 50 + "M" * 1000 + "T" * 50
        out = sbx.truncate(s, 100)
        self.assertTrue(out.startswith("H" * 50) and out.endswith("T" * 50))
        self.assertIn("[... 1000 chars truncated ...]", out)

    def test_step_chars(self):
        calls = [{"id": "1", "name": "bash", "args": {"command": "ls"}}]
        self.assertEqual(ag.step_chars("hi", calls, [{"output": "abcd"}]), 2 + len('{"command": "ls"}') + 4)


class ParserTests(unittest.TestCase):
    def test_pytest(self):
        log = ("PASSED testing/test_a.py::test_one\nFAILED testing/test_a.py::test_two - AssertionError: x\n"
               "ERROR testing/test_a.py::test_three\nSKIPPED [1] testing/test_a.py:9: no\nXFAIL testing/test_a.py::test_x\n")
        m = tk.parse_log_pytest(log)
        self.assertEqual(m["testing/test_a.py::test_one"], "PASSED")
        self.assertEqual(m["testing/test_a.py::test_two"], "FAILED")
        self.assertEqual(m["testing/test_a.py::test_three"], "ERROR")
        self.assertEqual(m["testing/test_a.py::test_x"], "XFAIL")

    def test_pytest_options(self):
        m = tk.parse_log_pytest_options("PASSED tests/test_u.py::test_p[/usr/lib/x/file.txt]\nPASSED tests/test_u.py::test_q[a-b]\n")
        self.assertIn("tests/test_u.py::test_p[/file.txt]", m)
        self.assertIn("tests/test_u.py::test_q[a-b]", m)

    def test_sympy(self):
        log = ("sympy/algebras/tests/test_quaternion.py[3]\n  test_quaternion_construction ok\n  test_quaternion_functions F\n"
               "  test_issue E\n___ sympy/algebras/tests/test_quaternion.py:test_quaternion_functions ___\n")
        m = tk.parse_log_sympy(log)
        self.assertEqual(m["test_quaternion_construction"], "PASSED")
        self.assertEqual(m["test_quaternion_functions"], "FAILED")
        self.assertEqual(m["test_issue"], "ERROR")

    def test_grade(self):
        st = {"a": "PASSED", "b": "FAILED", "c": "SKIPPED", "d": "XFAIL"}
        g = tk.grade(st, ["a", "d"], ["c"])
        self.assertTrue(g["resolved"])  # skipped P2P is neither pass nor fail
        g = tk.grade(st, ["a"], ["b", "missing"])
        self.assertFalse(g["resolved"])
        self.assertEqual(g["p2p_fail"], ["b", "missing"])
        self.assertFalse(tk.grade({}, [], [])["resolved"])


class PatchTests(unittest.TestCase):
    def test_files(self):
        self.assertEqual(tk.patch_files(TEST_PATCH), ["testing/test_a.py", "testing/test_new.py", "testing/data.json"])
        self.assertEqual(tk.new_files(TEST_PATCH), ["testing/test_new.py"])
        self.assertEqual(tk.test_directives(TEST_PATCH), ["testing/test_a.py", "testing/test_new.py"])
        task = {"test_patch": TEST_PATCH, "test_files": tk.patch_files(TEST_PATCH)}
        self.assertEqual(tk.reset_tests_cmd(task), "git checkout HEAD -- testing/test_a.py testing/data.json && rm -f testing/test_new.py")

    def test_is_test_path(self):
        for p in ["testing/test_x.py", "sympy/core/tests/test_basic.py", "tests/conftest.py", "test_requests.py", "a/b_test.py"]:
            self.assertTrue(tk.is_test_path(p), p)
        for p in ["src/_pytest/logging.py", "sympy/core/basic.py", "requests/utils.py", "reproduce.py"]:
            self.assertFalse(tk.is_test_path(p), p)

    def test_specs_and_dockerfile(self):
        for repo, v in [("pytest-dev/pytest", "5.4"), ("sympy/sympy", "1.1"), ("psf/requests", "2.27"), ("psf/requests", "1.1")]:
            s = tk.spec_for(repo, v)
            self.assertIn(s["parser"], tk.PARSERS)
        c = {"repo": "pytest-dev/pytest", "version": "6.0", "base_commit": "abc123", "instance_id": "pytest-dev__pytest-1"}
        df = tk.dockerfile(c)
        self.assertIn("git fetch -q --depth 1 https://github.com/pytest-dev/pytest abc123", df)
        self.assertIn("SETUPTOOLS_SCM_PRETEND_VERSION=6.0.0 pip install -e .", df)
        self.assertEqual(tk.image_tag("Psf__requests-2931"), "csl-live:psf__requests-2931")


class MetricTests(unittest.TestCase):
    def test_bash_read_paths(self):
        self.assertEqual(ag.bash_read_paths("cat src/a.py"), ["src/a.py"])
        self.assertEqual(ag.bash_read_paths("head -n 50 /testbed/src/a.py | grep x"), ["src/a.py"])
        self.assertEqual(ag.bash_read_paths("sed -n '10,40p' src/b.py && tail -20 ./c.py"), ["src/b.py", "c.py"])
        self.assertEqual(ag.bash_read_paths("nl -ba src/d.py"), ["src/d.py"])
        self.assertEqual(ag.bash_read_paths("cat > /tmp/r.py << 'EOF'\nhead = 1\ncat x\nEOF\npython /tmp/r.py"), [])
        self.assertEqual(ag.bash_read_paths("grep -n foo src/a.py; sed -i 's/a/b/' src/a.py"), [])

    def test_listed_files(self):
        final = "DONE:\n- `src/_pytest/logging.py`\n2. src/_pytest/capture.py — fixed\nAll tests pass in testing/test_x.py."
        self.assertEqual(ag.listed_files(final), {"src/_pytest/logging.py", "src/_pytest/capture.py"})
        self.assertEqual(ag.listed_files("DONE: /testbed/sympy/core/basic.py"), {"sympy/core/basic.py"})
        self.assertEqual(ag.listed_files("DONE: Fixed the bug."), set())
        # only the first list counts: test files named later (e.g. in a verification section) are not "listed"
        verbose = ("DONE:\nsrc/_pytest/compat.py\n\nThe cause was X.\n\nVerification:\n- `testing/test_compat.py` passes (8 tests).\n"
                   "- `test_requests.py`: 83 passed, 2 failed")
        self.assertEqual(ag.listed_files(verbose), {"src/_pytest/compat.py"})
        terse = "DONE: Updated mock-patch detection.\n\nChanged file:\n- `src/_pytest/compat.py`\n\nValidation: testing/x.py passed."
        self.assertEqual(ag.listed_files(terse), {"src/_pytest/compat.py"})

    def test_claims(self):
        self.assertTrue(ag.claims_success("DONE: fixed the issue, all tests pass"))
        self.assertFalse(ag.claims_success("DONE: I could not fix this; tests still fail"))
        self.assertFalse(ag.claims_success(""))
        # real final message (Haiku, unresolved run) with no stock success phrase: still a claim
        self.assertTrue(ag.claims_success("DONE:\nsympy/x.py\n\nThe issue's examples now give `1x1`. The existing quantum tests "
                                          "for tensor products pass; the one XFail is an expected failure."))
        self.assertTrue(ag.claims_success("DONE:\nsrc/a.py\nPreviously the handler failed to restore its level; now it does."))
        self.assertTrue(ag.claims_success("DONE:\nsrc/a.py\n83 passed, 2 failed (network tests, also fail on the untouched baseline)."))
        self.assertFalse(ag.claims_success("DONE:\nsrc/a.py\nTests: 3 failed, 10 passed."))
        self.assertFalse(ag.claims_success("DONE:\nsrc/a.py\nOne related case is not fixed; it is still open."))
        self.assertFalse(ag.claims_success("DONE:\nsrc/a.py\nAll tests pass.", finished=False))

    def test_constraints(self):
        changed = {"modified": ["src/a.py", "testing/test_a.py"], "added": ["repro.py"], "deleted": []}
        c = ag.constraint_checks("DONE:\nsrc/a.py", changed)
        self.assertEqual(c["tests_touched"], ["testing/test_a.py"])
        self.assertEqual(c["leftover_files"], ["repro.py"])
        self.assertTrue(c["done_prefix"])
        self.assertEqual(c["files_listed"]["precision"], 1.0)
        self.assertEqual(c["files_listed"]["recall"], round(1 / 3, 3))
        self.assertFalse(ag.constraint_checks("**DONE:** src/a.py", changed)["done_prefix"])
        self.assertTrue(ag.constraint_checks("**DONE:** src/a.py", changed)["done_prefix_lenient"])

    def test_compute_metrics(self):
        events = [
            step(1, [("read_file", {"path": "src/a.py"}, "1\tx", None, False), ("bash", {"command": "pytest t.py"}, "boom", 1, False)]),
            step(2, [("bash", {"command": "pytest t.py"}, "boom", 1, False), ("edit_file", {"path": "src/a.py", "old": "q", "new": "r"}, "Error: old string not found", None, True)]),
            {"event": "compaction", "round": 1, "step": 2, "ok": True},
            step(3, [("bash", {"command": "cat /testbed/src/a.py"}, "x", 0, False), ("read_file", {"path": "src/a.py"}, "1\tx", None, False)]),
            step(4, [("edit_file", {"path": "src/a.py", "old": "x", "new": "y"}, "Edited", None, False), ("bash", {"command": "pytest t.py"}, "ok", 0, False)]),
            step(5, [("bash", {"command": "sleep 999"}, "[command timed out]", 124, False)]),
        ]
        changed = {"modified": ["src/a.py"], "added": [], "deleted": []}
        ev = {"resolved": False, "f2p_total": 1, "p2p_total": 2, "f2p_pass": [], "p2p_pass": ["p1", "p2"], "patch_applied": True}
        m = ag.compute_metrics(events, "DONE:\nsrc/a.py\nAll tests pass.", changed, ev, "finished")
        self.assertEqual(m["steps"], 5)
        self.assertEqual(m["compactions"], 1)
        self.assertEqual(m["compaction_steps"], [2])
        self.assertEqual(m["tool_calls"], {"bash": 5, "read_file": 2, "edit_file": 2, "other": 0, "total": 9})
        self.assertEqual((m["re_reads_after_compaction"], m["re_read_ops_after_compaction"]), (1, 2))  # 2 reads, 1 path+epoch
        self.assertEqual(m["re_reads_total"], 2)
        self.assertEqual(m["repeated_failed_commands"], 2)  # steps 2 and 4 repeat the failing pytest
        self.assertEqual(m["repeated_failed_after_compaction"], 1)  # step 4: last failure was before the compaction
        self.assertEqual(m["repeated_failed_unchanged"], 1)  # step 2 only: step 4 follows a successful edit
        self.assertEqual(m["edit_errors"], 1)
        self.assertEqual(m["bash_timeouts"], 1)
        self.assertTrue(m["finished"] and m["claimed_success"] and m["false_success"])
        self.assertEqual(m["constraint_violations"], 0)
        self.assertEqual(m["f2p_pass_rate"], 0.0)
        self.assertEqual(m["p2p_pass_rate"], 1.0)
        cut = ag.compute_metrics(events, "Now all tests pass, let me clean up.", changed, ev, "max_steps")
        self.assertFalse(cut["claimed_success"] or cut["false_success"])  # mid-task narration is not a final claim
        self.assertFalse(m["vendored_copy_seen"])
        peek = [step(1, [("bash", {"command": "grep -rn prepend_scheme /usr/local/lib"}, "/usr/local/lib/python3.9/site-packages/pip/_vendor/requests/utils.py:982:def", 0, False)])]
        self.assertTrue(ag.compute_metrics(peek, "", changed, ev, "finished")["vendored_copy_seen"])

    def test_tools_and_prompt(self):
        self.assertEqual([t["name"] for t in ag.LIVE_TOOLS], ["bash", "read_file", "edit_file"])
        msg = ag.first_message({"problem_statement": "Bug X"})
        for s in ("Bug X", "Do not modify or add test files", "git status shows only your source changes", '"DONE:"'):
            self.assertIn(s, msg)
        self.assertTrue(ag.OVERFLOW_RX.search("prompt is too long: 1000123 tokens > 1000000 maximum"))
        self.assertTrue(ag.OVERFLOW_RX.search("Error code: 400 - {'code': 'context_length_exceeded'}"))


class FakeSandbox:
    def __init__(self, image):
        self.cmds = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def exec(self, cmd, timeout=120):
        self.cmds.append(cmd)
        return (1, "boom") if "fail" in cmd else (0, "ok")

    def read(self, path, start_line=None, end_line=None):
        return True, "x" * 7000

    def edit(self, path, old, new):
        return True, "Edited"

    def changed_files(self):
        return {"modified": ["src/a.py"], "added": [], "deleted": []}

    def diff(self):
        return "diff --git a/src/a.py b/src/a.py\n-x\n+y\n"


class FakeAdapter:
    label, run_label = "fake-model", "fake-model-medium"
    script = [[("read_file", {"path": "src/a.py"})], [("bash", {"command": "make fail"})], [("bash", {"command": "make fail"})]]

    def __init__(self):
        self.compactions, self.n = 0, 0

    def describe(self):
        return {"provider": "fake", "model": "fake"}

    def append_user(self, state, text):
        return state + [{"user": text}]

    def tool_results(self, results):
        return [{"results": results}]

    def compact(self, state, instructions):
        self.compactions += 1
        return CallResult(ok=True, state=[{"summary": len(state)}], summary="s")

    def step(self, state, system=None, max_tokens=16000):
        self.n += 1
        if self.n > len(self.script):
            return CallResult(ok=True, text="DONE:\nsrc/a.py\nAll tests pass.", state=state + [{"a": self.n}], meta={"calls": []})
        calls = [{"id": f"c{self.n}{i}", "name": n, "args": a} for i, (n, a) in enumerate(self.script[self.n - 1])]
        return CallResult(ok=True, state=state + [{"a": self.n}], meta={"calls": calls}, usage={"input_tokens": 10})


class RunOneTests(unittest.TestCase):
    def test_loop_with_fakes(self):
        saved = ag.Sandbox, ag.evaluate, ag.RUNS_C
        with tempfile.TemporaryDirectory() as tmp:
            ag.Sandbox, ag.RUNS_C = FakeSandbox, Path(tmp)
            ag.evaluate = lambda task, diff: {"resolved": True, "f2p_total": 1, "p2p_total": 0, "f2p_pass": ["t"], "patch_applied": True}
            try:
                task = {"instance_id": "x__y-1", "repo": "x/y", "difficulty": "15 min - 1 hour", "image": "img", "problem_statement": "Bug"}
                ad = FakeAdapter()
                r = ag.run_one(ad, task, "compact", threshold=6000)
                d = Path(tmp) / "fake-model-medium-x__y-1-compact-c6000-rep1"
                m = json.loads((d / "metrics.json").read_text())
                meta = json.loads((d / "meta.json").read_text())
                self.assertEqual((r["status"], meta["model"], meta["compact_chars"]), ("ok", "fake-model", 6000))
                # the 7000-char read is seen for one turn first: compaction comes after step 2, not after step 1
                self.assertEqual((ad.compactions, m["compaction_steps"]), (1, [2]))
                self.assertEqual((m["steps"], m["finished"], m["resolved"], m["repeated_failed_commands"]), (4, True, True, 1))
                self.assertEqual(m["repeated_failed_after_compaction"], 1)
                self.assertTrue((d / "compaction_round_1.json").exists() and (d / "diff.patch").exists())
                self.assertEqual(ag.run_one(ad, task, "compact", threshold=6000)["status"], "skipped (already done)")
                # another trigger size is a separate run, side by side
                self.assertEqual(ag.run_one(ad, task, "compact", threshold=9000)["run_id"], "fake-model-medium-x__y-1-compact-c9000-rep1")
                ad2 = FakeAdapter()
                ag.run_one(ad2, task, "none")
                self.assertEqual(ad2.compactions, 0)
                # --force moves a finished run aside instead of deleting it; collect skips it and errored runs
                ag.run_one(FakeAdapter(), task, "compact", threshold=6000, force=True)
                self.assertEqual(len(list((Path(tmp) / ".previous").iterdir())), 1)
                err = Path(tmp) / "fake-model-medium-x__y-1-compact_guided-c6000-rep1"
                err.mkdir()
                (err / "meta.json").write_text(json.dumps({**meta, "run_id": err.name, "strategy": "compact_guided", "status": "error"}))
                (err / "metrics.json").write_text(json.dumps(m))
                saved_results, ag.RESULTS = ag.RESULTS, Path(tmp) / "results"
                try:
                    rows = ag.collect()
                finally:
                    ag.RESULTS = saved_results
                self.assertEqual(sorted(r["strategy"] for r in rows), ["compact", "compact", "none"])
            finally:
                ag.Sandbox, ag.evaluate, ag.RUNS_C = saved


def _base_image() -> str | None:
    if not sbx.docker_available():
        return None
    img = tk.BASE_IMAGE.format(py="3.9")
    return img if subprocess.run(["docker", "image", "inspect", img], capture_output=True).returncode == 0 else None


@unittest.skipUnless(_base_image(), "docker or the base image is not available")
class SandboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sb = sbx.Sandbox(_base_image())
        cls.sb.exec("git init -q . && printf 'a\\nb\\nc\\n' > f.txt && printf 'x\\nx\\n' > dup.txt && git add -A && git commit -qm init")

    @classmethod
    def tearDownClass(cls):
        cls.sb.close()

    def test_exec_and_timeout(self):
        self.assertEqual(self.sb.exec("echo hi; exit 3"), (3, "hi\n"))
        code, out = self.sb.exec("sleep 10", timeout=1)
        self.assertEqual(code, 124)
        self.assertIn("timed out after 1s", out)
        code, out = self.sb.exec("python3 -c \"print('y' * 30000)\"")
        self.assertIn("chars truncated", out)
        self.assertEqual(self.sb.exec("ping -c1 -W1 1.1.1.1 >/dev/null 2>&1 || python3 -c \"import socket; socket.create_connection(('1.1.1.1', 80), 2)\"")[0] != 0, True)

    def test_read_edit_diff(self):
        ok, out = self.sb.read("f.txt", 2, 3)
        self.assertTrue(ok)
        self.assertEqual(out.split("\n")[0], "     2\tb")
        self.assertFalse(self.sb.read("nope.txt")[0])
        ok, out = self.sb.edit("dup.txt", "x", "y")
        self.assertFalse(ok)
        self.assertIn("occurs 2 times", out)
        self.assertFalse(self.sb.edit("f.txt", "zzz", "y")[0])
        self.assertTrue(self.sb.edit("f.txt", "b\n", "B 'quoted' \"x\" $HOME\n")[0])
        self.assertTrue(self.sb.edit("new/dir/n.py", "", "print(1)\n")[0])
        ch = self.sb.changed_files()
        self.assertEqual(ch["modified"], ["f.txt"])
        self.assertEqual(ch["added"], ["new/dir/n.py"])
        d = self.sb.diff()
        self.assertIn("+B 'quoted' \"x\" $HOME", d)
        self.assertIn("+++ b/new/dir/n.py", d)
        self.sb.exec("git stash -u -q")
        ok, _ = self.sb.apply_patch(d)
        self.assertTrue(ok)
        self.assertEqual(self.sb.changed_files(), ch)

    def test_inner_timeout_exit_code(self):
        code, out = self.sb.exec("timeout 1 sleep 5")  # the command's own `timeout`, not ours
        self.assertEqual(code, 124)
        self.assertNotIn("timed out after", out)

    def test_committed_and_staged_work_is_captured(self):
        with sbx.Sandbox(_base_image()) as sb:
            sb.exec("git init -q . && echo a > f.txt && git add -A && git commit -qm init")
            sb.base = sb.exec("git rev-parse HEAD")[1].strip()  # set at start-up when the image already has a repository
            sb.exec("echo b > f.txt && echo s > scratch.py && git add scratch.py")
            self.assertEqual(sb.changed_files(), {"modified": ["f.txt"], "added": ["scratch.py"], "deleted": []})
            sb.exec("git commit -qam 'agent commit'")
            ch, d = sb.changed_files(), sb.diff()
            self.assertEqual(ch, {"modified": ["f.txt"], "added": ["scratch.py"], "deleted": []})
            sb.exec(f"git reset -q --hard {sb.base}")
            self.assertTrue(sb.apply_patch(d)[0])
            self.assertEqual(sb.exec("cat f.txt scratch.py")[1], "b\ns\n")


if __name__ == "__main__":
    unittest.main()
