"""Offline tests for layer B (lab/replay.py): no network, no API keys."""

from __future__ import annotations

import json
import unittest

from lab import replay as rp
from lab import transcript as tr

SYSTEM = """SETTING: You are an autonomous programmer.
The special interface consists of a file editor that shows you 100 lines of a file at a time.
COMMANDS:
open:
  signature: open <path> [<line_number>]
edit:
  signature: edit <start_line>:<end_line>
<replacement_text>
end_of_edit
submit:
  signature: submit
"""


def obs(body: str, open_file: str = "n/a", cwd: str = "/demo") -> str:
    return f"{body}\n\n(Open file: {open_file})\n(Current directory: {cwd})\nbash-$"


def ai(disc: str, cmd: str | None) -> dict:
    return {"role": "ai", "text": f"DISCUSSION\n{disc}\n\n```\n{cmd}\n```\n" if cmd else disc}


def synthetic_row(pad: int = 400) -> dict:
    """A small SWE-agent session: search, open, failed edit, edit, create a script, run it (traceback), sed -i, rm."""
    filler = "\n".join(f"{i}:    x = {i}" for i in range(1, pad))
    t = [{"role": "system", "text": "", "system_prompt": SYSTEM},
         {"role": "user", "text": obs("ISSUE:\nparse() crashes on empty input.\nINSTRUCTIONS: fix it.")}]
    seq = [
        ("Let's search for the parser.", "search_dir \"def parse\"", obs("Found 3 matches for \"def parse\" in /demo:\n/demo/src/parser.py (2 matches)\n/demo/docs/notes.py (1 matches)\nEnd of matches")),
        ("Open the parser.", "open src/parser.py 40", obs(f"[File: /demo/src/parser.py (212 lines total)]\n{filler}", "/demo/src/parser.py")),
        ("Read the docs helper too.", "open docs/notes.py", obs("[File: /demo/docs/notes.py (12 lines total)]\n1:x", "/demo/docs/notes.py")),
        ("Back to the parser.", "open src/parser.py", obs(f"[File: /demo/src/parser.py (212 lines total)]\n{filler}", "/demo/src/parser.py")),
        ("Try a fix.", "edit 41:41\n    if not text: return []\nend_of_edit",
         obs("Your proposed edit has introduced new syntax error(s).\nYour changes have NOT been applied.", "/demo/src/parser.py")),
        ("Fix the indentation, because the guard must sit inside parse().", "edit 41:41\n        if not text:\n            return []\nend_of_edit",
         obs(f"[File: /demo/src/parser.py (213 lines total)]\n{filler}\nFile updated. Please review the changes.", "/demo/src/parser.py")),
        ("Create a reproduction script.", "create reproduce.py", obs("[File: /demo/reproduce.py (1 lines total)]\n1:", "/demo/reproduce.py")),
        ("Write it.", "edit 1:1\nfrom src.parser import parse\nprint(parse(''))\nend_of_edit",
         obs("[File: /demo/reproduce.py (2 lines total)]\nFile updated.", "/demo/reproduce.py")),
        ("Run it.", "python reproduce.py",
         obs("Traceback (most recent call last):\n  File \"/demo/src/parser.py\", line 57, in parse\nValueError: empty token stream", "/demo/reproduce.py")),
        ("Look at line 57.", "goto 57", obs(f"[File: /demo/src/parser.py (213 lines total)]\n{filler}", "/demo/src/parser.py")),
        ("Patch the config with sed.", "sed -i 's/strict = True/strict = False/' setup.cfg", obs("", "/demo/src/parser.py")),
        ("Write a scratch file.", "echo 'tmp' > scratch.txt && ls", obs("scratch.txt\nsrc", "/demo/src/parser.py")),
        ("Remove the scratch file.", "rm scratch.txt", obs("", "/demo/src/parser.py")),
        ("Search the tests.", "search_dir \"parse(\" tests", obs("Found 7 matches for \"parse(\" in /demo/tests:\n/demo/tests/test_parser.py (7 matches)\nEnd of matches")),
        ("Run the tests.", "python -m pytest tests -q", obs("..F\n1 failed, 41 passed in 0.52s", "/demo/src/parser.py")),
        ("Open the test file.", "open tests/test_parser.py", obs("[File: /demo/tests/test_parser.py (80 lines total)]\n1:import pytest", "/demo/tests/test_parser.py")),
        ("Fix the failing assertion.", "edit 10:10\n    assert parse('') == []\nend_of_edit", obs("[File: /demo/tests/test_parser.py (80 lines total)]\nFile updated.", "/demo/tests/test_parser.py")),
        ("Run the tests again.", "python -m pytest tests -q", obs("42 passed in 0.50s", "/demo/tests/test_parser.py")),
        ("Thinking out loud without a command.", None, None),
        ("Clean up the script.", "rm reproduce.py", obs("", "/demo/tests/test_parser.py")),
        ("Edit the parser docstring.", "edit 1:1\n\"\"\"Parser.\"\"\"\nend_of_edit", obs("[File: /demo/src/parser.py (213 lines total)]\nFile updated.", "/demo/src/parser.py")),
        ("Run the full suite.", "python -m pytest -q", obs("42 passed in 0.61s", "/demo/src/parser.py")),
        ("Done.", "submit", obs("diff --git a/src/parser.py b/src/parser.py")),
    ]
    for disc, cmd, out in seq:
        t.append(ai(disc, cmd))
        t.append({"role": "user", "text": out if out is not None else "Your output was not formatted correctly."})
    return {"instance_id": "demo__demo-1", "model_name": "swe-agent-test", "target": True, "exit_status": "submitted", "trajectory": t}


class ParseTests(unittest.TestCase):
    def test_split_action_last_block(self):
        disc, cmd = rp.split_action("DISCUSSION\nFirst a snippet:\n```python\nx = 1\n```\nNow run:\n```\npython a.py\n```\n")
        self.assertEqual(cmd, "python a.py")
        self.assertIn("First a snippet", disc)
        self.assertNotIn("DISCUSSION", disc)
        self.assertEqual(rp.split_action("no command here"), ("no command here", None))
        _, cmd = rp.split_action("Edit:\n```\nedit 1:2\nfoo\nend_of_edit\n```")
        self.assertEqual(cmd, "edit 1:2\nfoo\nend_of_edit")

    def test_trailer_and_rel(self):
        self.assertEqual(rp.trailer(obs("x", "/demo/a.py")), ("/demo/a.py", "/demo"))
        self.assertEqual(rp.trailer(obs("x")), (None, "/demo"))
        self.assertEqual(rp.rel("src/a.py", "/demo", "/demo"), "src/a.py")
        self.assertEqual(rp.rel("/demo/demo/a.py", "/demo", "/demo"), "demo/a.py")
        self.assertEqual(rp.rel("../b.py", "/demo/src", "/demo"), "b.py")

    def test_action_types_and_writes(self):
        cases = {"edit 1:2\nx\nend_of_edit": "edit", "create r.py": "create", "python r.py": "run", "pytest -x tests": "run",
                 "open a.py 10": "open", "search_dir foo": "search", "find_file a.py": "search", "ls -la": "search",
                 "submit": "submit", "sed -i 's/a/b/' x.cfg": "edit", "cd src": "other", "rm a.py": "other",
                 "cd /demo && python -m pytest": "run"}
        for cmd, want in cases.items():
            self.assertEqual(rp.action_type(cmd), want, cmd)
        self.assertEqual(rp.bash_writes("echo 'a > b' > out.txt 2>&1"), (["out.txt"], []))
        self.assertEqual(rp.bash_writes("cat > t.py << EOF\nif a > b: pass\nEOF"), (["t.py"], []))
        self.assertEqual(rp.bash_writes("mv a.py b.py"), (["b.py"], ["a.py"]))
        self.assertEqual(rp.bash_writes("python x.py > /dev/null"), ([], []))


class ConvertCutPhaseTests(unittest.TestCase):
    def setUp(self):
        self.row = synthetic_row()
        self.conv = rp.convert_row(self.row)

    def test_convert(self):
        turns = self.conv["turns"]
        self.assertEqual(self.conv["root"], "/demo")
        self.assertTrue(turns[0]["text"].startswith("[Session setup]"))
        self.assertIn("parse() crashes", turns[0]["text"])
        self.assertIn("open <path> [<line_number>]", turns[0]["text"])
        a = turns[1]
        self.assertEqual(a["calls"], [{"id": "t2", "name": "bash", "args": {"command": "search_dir \"def parse\""}}])
        self.assertEqual(a["text"], "Let's search for the parser.")
        self.assertEqual(turns[2]["role"], "tool")
        self.assertEqual(turns[2]["results"][0]["id"], "t2")
        plain = [i for i, t in enumerate(turns) if t["role"] == "assistant" and not t["calls"]]
        self.assertEqual(len(plain), 1)
        self.assertEqual(turns[plain[0] + 1]["role"], "user")  # the format-error message stays a user turn
        for x, y in zip(turns, turns[1:]):
            self.assertNotEqual(x["role"], y["role"])
            if x["role"] == "assistant" and x["calls"]:
                self.assertEqual(y["role"], "tool")
                self.assertEqual(y["results"][0]["id"], x["calls"][0]["id"])

    def test_cut_and_files(self):
        cut = rp.choose_cut(self.conv)
        st = rp.steps(self.conv)
        self.assertIsNotNone(cut)
        self.assertTrue(rp.substantive(st[cut["step"]]["command"]))
        self.assertEqual(self.conv["turns"][cut["turn"] - 1]["role"], "tool")
        self.assertGreaterEqual(cut["frac"], 0.75)
        files = rp.track_files(st[:cut["step"]], "/demo")
        self.assertEqual(set(files["edited"]), {"src/parser.py", "setup.cfg", "tests/test_parser.py", "reproduce.py"})
        self.assertIn("docs/notes.py", files["read_only"])
        self.assertNotIn("src/parser.py", files["read_only"])
        self.assertEqual(files["deleted"], ["scratch.txt"])  # written, then removed
        later = rp.track_files(st, "/demo")  # the whole session: reproduce.py is removed before the end
        self.assertNotIn("reproduce.py", later["edited"])

    def test_cut_skips_retracted_action(self):
        cut = rp.choose_cut(self.conv)
        row = json.loads(json.dumps(self.row))
        st = rp.steps(self.conv)
        nxt = st[cut["step"] + 1]["turn"]  # the agent's next turn says the cut action was a mistake
        traj_i = self.conv["idx"][nxt]
        row["trajectory"][traj_i]["text"] = row["trajectory"][traj_i]["text"].replace("DISCUSSION\n", "DISCUSSION\nI edited the wrong file. ")
        conv = rp.convert_row(row)
        new = rp.choose_cut(conv)
        self.assertIsNotNone(new)
        self.assertGreater(new["step"], cut["step"])

    def test_failed_edit_not_counted(self):
        st = rp.steps(self.conv)
        files = rp.track_files(st[:5], "/demo")  # up to and including the failed edit
        self.assertEqual(files["edited"], [])

    def test_phases(self):
        cut = rp.choose_cut(self.conv)
        prefix = self.conv["turns"][:cut["turn"]]
        phases = rp.split_phases(prefix)
        self.assertEqual([p["id"] for p in phases], ["P1", "P2", "P3", "P4"])
        self.assertEqual(phases[0]["turns"][0]["role"], "user")
        for p in phases[1:]:
            self.assertEqual(p["turns"][0]["role"], "assistant")
        for p in phases:
            self.assertEqual(p["turns"][-1]["role"], "tool")
        self.assertEqual([t for p in phases for t in p["turns"]], prefix)

    def test_build_case_without_llm(self):
        case = rp.build_case(1234, self.row, use_gen=False)
        self.assertEqual(case["case_id"], "b-demo__demo-1")
        self.assertEqual(case["layer"], "b")
        self.assertEqual(case["chars"], len(tr.full_text(case)))
        self.assertEqual(case["provenance"]["row_offset"], 1234)
        self.assertEqual(case["provenance"]["license"], "CC-BY-4.0")
        self.assertEqual(len(case["hidden"]["actions"]), 3)
        self.assertEqual(case["key"]["continuation"]["type"], case["hidden"]["actions"][0]["type"])
        json.dumps(case)


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.row = synthetic_row()
        self.case = rp.build_case(1, self.row, use_gen=False)
        conv = rp.convert_row(self.row)
        self.turns = conv["turns"][:rp.choose_cut(conv)["turn"]]
        self.phases = self.case["phases"]
        self.tool_turn = {t["results"][0]["output"].split("\n")[0][:12]: i for i, t in enumerate(self.turns) if t["role"] == "tool"}

    def _turn_with(self, text: str) -> int:
        return next(i for i, t in enumerate(self.turns) if t["role"] == "tool" and text in t["results"][0]["output"])

    def test_verify_recall(self):
        ti = self._turn_with("Found 3 matches")
        ok, why = rp.verify_recall({"question": "How many matches did your first search for the parse function find?",
                                    "answer": "3", "kind": "int", "turn": ti, "superseded": []}, self.turns, self.phases, "/demo")
        self.assertEqual(why, "ok")
        self.assertEqual(ok["truth"], "3")
        self.assertIn("7", ok["decoys"])  # "Found 7 matches" later: same template, other value
        self.assertEqual(ok["phase"], rp.phase_of(self.phases, ti))
        tb = self._turn_with("ValueError")
        ok, why = rp.verify_recall({"question": "Which exception did the reproduction script raise?", "answer": "ValueError",
                                    "kind": "token", "turn": tb}, self.turns, self.phases, "/demo")
        self.assertEqual(why, "ok")
        ok, why = rp.verify_recall({"question": "Where was it raised?", "answer": "src/parser.py:57", "kind": "path_line",
                                    "turn": tb}, self.turns, self.phases, "/demo")
        self.assertEqual(why, "ok", why)
        ok, why = rp.verify_recall({"question": "q?", "answer": "ValueError", "kind": "token", "turn": ti}, self.turns, self.phases, "/demo")
        self.assertEqual(why, "not in that tool output")
        ok, why = rp.verify_recall({"question": "q?", "answer": "parse", "kind": "token", "turn": ti}, self.turns, self.phases, "/demo")
        self.assertEqual(why, "in the issue prompt")
        ok, why = rp.verify_recall({"question": "Did ValueError occur?", "answer": "ValueError", "kind": "token", "turn": tb},
                                   self.turns, self.phases, "/demo")
        self.assertEqual(why, "answer in the question")
        tests = self._turn_with("1 failed, 41 passed")
        ok, why = rp.verify_recall({"question": "How many tests passed in your first test run?", "answer": "41", "kind": "int",
                                    "turn": tests, "superseded": ["42"]}, self.turns, self.phases, "/demo")
        self.assertEqual(why, "ok")
        self.assertEqual(ok["stale"], ["42"])
        self.assertIn("OVR", ok["traps"])

    def test_verify_decision(self):
        ai_turn = next(i for i, t in enumerate(self.turns) if t["role"] == "assistant" and "inside parse()" in t["text"])
        d = {"question": "Why did you re-indent the guard?", "reference": "The guard has to be inside parse().",
             "quote": "because the guard must sit inside parse()", "turn": ai_turn, "key_points": [r"inside|within", r"parse", r"(("]}
        ok, why = rp.verify_decision(d, self.turns, self.phases)
        self.assertEqual(why, "ok")
        self.assertEqual(ok["key_points"], [r"inside|within", r"parse"])
        bad = {**d, "quote": "a sentence the agent never wrote anywhere at all"}
        self.assertEqual(rp.verify_decision(bad, self.turns, self.phases)[1], "quote not found in the agent's text")

    def _with_key(self) -> dict:
        tb = self._turn_with("ValueError")
        ti = self._turn_with("Found 3 matches")
        r1, _ = rp.verify_recall({"question": "Which exception did the reproduction script raise?", "answer": "ValueError",
                                  "kind": "token", "turn": tb}, self.turns, self.phases, "/demo")
        r2, _ = rp.verify_recall({"question": "How many matches did your first search find?", "answer": "3", "kind": "int",
                                  "turn": ti}, self.turns, self.phases, "/demo")
        r1["id"], r2["id"] = "r01", "r02"
        case = json.loads(json.dumps(self.case))
        case["key"]["recall"] = [r1, r2]
        case["key"]["decisions"] = [{"id": "d1", "question": "Why did you re-indent the guard?", "reference": "It must be inside parse().",
                                     "quote": "x", "turn": 3, "phase": "P1", "key_points": [r"inside|within", r"parse"]}]
        return case

    def test_quiz_scoring(self):
        case = self._with_key()
        pr = rp.ReplayProbes(case)
        self.assertEqual(pr.names, ["quiz", "state"])
        prompt = pr.prompt("quiz")
        self.assertIn("r01: Which exception", prompt)
        self.assertIn("UNKNOWN", prompt)
        ctx = {"transcript_text": tr.full_text(case), "summary": "The script raised ValueError."}
        res = pr.score("quiz", json.dumps({"r01": "ValueError", "r02": "7"}), ctx)
        self.assertTrue(res["parsed"])
        out = {it["id"]: it for it in res["items"]}
        self.assertEqual(out["r01"]["outcome"], "CORRECT")
        self.assertEqual(out["r01"]["score"], 1.0)
        self.assertTrue(out["r01"]["in_summary"])
        self.assertEqual(out["r02"]["outcome"], "DECOY")
        self.assertFalse(out["r02"]["in_summary"])
        res = pr.score("quiz", "garbage", {"transcript_text": "", "summary": None})
        self.assertFalse(res["parsed"])
        self.assertTrue(all(it["outcome"] == "UNKNOWN" and "in_summary" not in it for it in res["items"]))

    def test_state_scoring(self):
        case = self._with_key()
        pr = rp.ReplayProbes(case)
        self.assertIn("d1: Why did you re-indent", pr.prompt("state"))
        key = case["key"]
        cont = key["continuation"]
        reply = {"files_edited": [f"/demo/{p}" for p in key["files_edited"]] + ["docs/notes.py"],
                 "next_step": {"action": cont["type"], "command": cont["command"], "file": cont["file"]},
                 "decisions": {"d1": "Because the guard has to sit inside parse()."}}
        res = pr.score("state", "```json\n" + json.dumps(reply) + "\n```", {"transcript_text": "", "summary": None})
        items = {it["id"]: it for it in res["items"]}
        art = items["files_edited"]
        n = len(key["files_edited"])
        self.assertEqual(art["recall"], 1.0)
        self.assertAlmostEqual(art["precision"], round(n / (n + 1), 3))
        self.assertEqual(art["decoys_reported"], ["docs/notes.py"])
        self.assertFalse(art["correct"])
        self.assertEqual(items["next_step"]["score"], 1.0)
        judge = {j["id"]: j for j in res["judge_items"]}
        self.assertEqual(judge["d1"]["measure"], "decision")
        self.assertEqual(judge["d1"]["keyterm"], 1.0)
        self.assertEqual(judge["next_step_judge"]["measure"], "continuation")
        self.assertIn(cont["command"][:20], judge["next_step_judge"]["reference"])
        wrong = {"files_edited": key["files_edited"], "next_step": {"action": "search", "command": "ls", "file": ""}, "decisions": {}}
        res = pr.score("state", json.dumps(wrong), {"transcript_text": "", "summary": None})
        items = {it["id"]: it for it in res["items"]}
        self.assertTrue(items["files_edited"]["correct"])
        self.assertEqual(items["next_step"]["score"], 0.0)
        self.assertEqual({j["id"]: j["answer"] for j in res["judge_items"]}["d1"], "")

    def test_state_scoring_edge_cases(self):
        case = self._with_key()
        key = case["key"]
        key["continuation"].update(type="run", file="reproduce.py", files=["reproduce.py"], command="python reproduce.py")
        pr = rp.ReplayProbes(case)
        ctx = {"transcript_text": "", "summary": None}

        def items(reply):
            return {it["id"]: it for it in pr.score("state", json.dumps(reply), ctx)["items"]}

        # the exact command with an empty file field: the file its command names counts
        got = items({"files_edited": [], "next_step": {"action": "run", "command": "python reproduce.py", "file": ""}})
        self.assertEqual(got["next_step"]["score"], 1.0)
        # next_step given as a plain command string
        self.assertEqual(items({"next_step": "python reproduce.py"})["next_step"]["score"], 1.0)
        # a bare file name counts only when it names one path in the session (parser.py is unique, __init__.py is not)
        key["continuation"].update(type="edit", file="src/parser.py", files=["src/parser.py"], command="edit 1:1")
        self.assertTrue(items({"next_step": {"action": "edit", "file": "parser.py"}})["next_step"]["file_ok"])
        case["phases"][-1]["turns"][-1]["results"][0]["output"] += "\n/demo/src/__init__.py\n/demo/tests/__init__.py"
        key["continuation"].update(file="src/__init__.py", files=["src/__init__.py"])
        pr = rp.ReplayProbes(case)
        self.assertFalse(items({"next_step": {"action": "edit", "file": "__init__.py"}})["next_step"]["file_ok"])
        # nothing edited and nothing claimed is a perfect artifact answer
        key["files_edited"] = []
        art = items({"files_edited": [], "next_step": {}})["files_edited"]
        self.assertEqual((art["f1"], art["correct"]), (1.0, True))

    def test_decoys_skip_bare_viewer_prefix(self):
        out = "464:            type_factory = getattr(obj)\n465:            return type_factory(x)\n466:            nb = 1"
        self.assertEqual(rp._decoys("token_cs", "type_factory", out, out, set()), [])
        out = "Found 3 matches for x\nFound 7 matches for y"
        self.assertEqual(rp._decoys("int", "3", out, out, {"3"}), ["7"])
        self.assertEqual(rp._norm_answer("text", "Missing argument 'URL'.", "/demo"), "Missing argument URL")

    def test_gate_sha_tracks_prefix(self):
        case = json.loads(json.dumps(self.case))
        sha = rp.phases_sha(case)
        case["phases"][-1]["turns"][-1]["results"][0]["output"] += "x"
        self.assertNotEqual(sha, rp.phases_sha(case))

    def test_merge_roles(self):
        msgs = [{"role": "user", "content": [1]}, {"role": "assistant", "content": [2]}, {"role": "assistant", "content": [3]},
                {"role": "user", "content": [4]}]
        self.assertEqual(rp.merge_roles(msgs), [{"role": "user", "content": [1]}, {"role": "assistant", "content": [2, 3]},
                                                {"role": "user", "content": [4]}])
        flat = [{"type": "message", "role": "user", "content": "a"}, {"type": "message", "role": "user", "content": "b"}]
        self.assertEqual(rp.merge_roles(flat), flat)


if __name__ == "__main__":
    unittest.main()
