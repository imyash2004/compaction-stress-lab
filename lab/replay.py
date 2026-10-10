"""Layer B: real SWE-agent sessions replayed (HF nebius/SWE-agent-trajectories, CC-BY-4.0).

    python -m lab.replay fetch   [--max-pages 801]      # scan the dataset, cache candidate rows, select 12 cases + 3 spares
    python -m lab.replay build   [--force] [--no-gen]   # convert, cut, phase, answer key (LLM-generated probes, verified in code)
    python -m lab.replay list
    python -m lab.replay show <case_id>
    python -m lab.replay gate    [--cases ...] [--report] # full-history quiz + state on both models; lists probes both miss
    python -m lab.replay drop <case_id> <probe_id> <reason>

A case is a neutral transcript prefix (4 phases) cut just before a substantive agent action, plus an answer key:
recall facts read from real tool outputs, the files edited so far, the real next action, and the agent's stated reasons.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import random
import re
import shlex
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from lab import probe, score
from lab import scenario as sc
from lab import transcript as tr

ROOT = tr.ROOT
DATA = ROOT / "data"
REPLAY = DATA / "replay"
RAW = REPLAY / "raw"
GEN = REPLAY / "gen"
GATE = REPLAY / "gate"
CASES = DATA / "cases"
SCAN = REPLAY / "scan.json"
SELECTED = REPLAY / "selected.json"
EDITS = REPLAY / "edits.json"

DATASET = "nebius/SWE-agent-trajectories"
LICENSE = "CC-BY-4.0"
API = "https://datasets-server.huggingface.co"
N_CASES, N_SPARES = 12, 3
# The spec asked for 100k-400k prefix chars, but the source agents ran with a ~32k-token window: whole sessions stop
# near 120k chars, so a cut at 80-90% leaves at most ~105k. The floor is lowered to the longest sessions that exist.
MIN_PREFIX, MAX_PREFIX = 70_000, 400_000
MIN_ROW_CHARS = 80_000
CUT_WINDOW = (0.80, 0.90)
MAX_RECALL, N_DECISIONS = 10, 3
GEN_MODEL = "gpt-6.1-sol"
TOOLS = [sc.TOOLS[0]]  # bash only
ACTIONS = ["edit", "create", "run", "open", "search", "submit", "other"]
KINDS = ["int", "version", "hash", "path_line", "duration", "mem", "timestamp", "token", "token_cs", "text"]

# ================================================================================================ fetch


def _get_json(url: str, tries: int = 8) -> dict:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "compaction-stress-lab/2"})
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.loads(r.read())
            if "error" not in data:
                return data
            err = data["error"]
        except Exception as e:  # noqa: BLE001 - network hiccups and 429s are retried
            err = f"{type(e).__name__}: {e}"
        print(f"  retry {i + 1}: {err[:120]}", file=sys.stderr, flush=True)
        time.sleep(min(120, 10 * 2 ** i))
    raise RuntimeError(f"datasets-server failed: {err}")


def _rows_url(offset: int, length: int = 100) -> str:
    q = urllib.parse.urlencode({"dataset": DATASET, "config": "default", "split": "train", "offset": offset, "length": length})
    return f"{API}/rows?{q}"


def repo_of(instance_id: str) -> str:
    return instance_id.rsplit("-", 1)[0]


def row_chars(row: dict) -> int:
    return sum(len(t.get("text") or "") for t in row["trajectory"])


def _load_json(p: Path, default):
    return json.loads(p.read_text()) if p.exists() else default


def _save_json(p: Path, obj) -> None:
    """Atomic: a reader (the study runner loading cases) never sees a half-written file."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(obj, indent=1))
    os.replace(tmp, p)


def _keep_row(row: dict) -> bool:
    """Resolved runs that ended in a submit; "submitted (exit_context)" rows are kept as a fallback pool only."""
    return bool(row.get("target")) and str(row.get("exit_status", "")).startswith("submitted") and \
        MIN_ROW_CHARS <= row_chars(row) <= 700_000


def _scan_page(offset: int) -> tuple[list[tuple[int, dict]], list[int]]:
    data = _get_json(_rows_url(offset))
    out, sizes = [], []
    for r in data.get("rows", []):
        row = r["row"]
        if row.get("target") and str(row.get("exit_status", "")).startswith("submitted"):
            sizes.append([row_chars(row), row["exit_status"] == "submitted"])
        if not _keep_row(row):
            continue
        if "trajectory" in (r.get("truncated_cells") or []):
            single = _get_json(_rows_url(r["row_idx"], 1))["rows"][0]
            if "trajectory" in (single.get("truncated_cells") or []):
                continue
            row = single["row"]
        out.append((r["row_idx"], row))
    return out, sizes


def candidate_info(row_idx: int, row: dict) -> dict | None:
    """Converted, cut and checked: None when the row cannot give a valid case."""
    try:
        conv = convert_row(row)
        cut = choose_cut(conv)
    except (ValueError, IndexError, KeyError):
        return None
    if cut is None:
        return None
    prefix = conv["turns"][:cut["turn"]]
    chars = sum(_turn_chars(t) for t in prefix)
    outs = [r["output"] for t in prefix if t["role"] == "tool" for r in t["results"]]
    noise = {"tracebacks": sum("Traceback (most recent call last)" in o for o in outs),
             "searches": sum(bool(re.search(r"Found \d+ matches", o)) for o in outs),
             "tests": sum(bool(re.search(r"\b\d+ (passed|failed)\b|Ran \d+ tests?|^(FAILED|ERROR) ", o, re.M)) for o in outs)}
    return {"row_idx": row_idx, "instance_id": row["instance_id"], "repo": repo_of(row["instance_id"]),
            "model_name": row["model_name"], "exit_status": row["exit_status"], "prefix_chars": chars, "cut_frac": cut["frac"],
            "next_action": cut["action"], "noise": noise}


def valid(c: dict, strict: bool = True) -> bool:
    return (MIN_PREFIX <= c["prefix_chars"] <= MAX_PREFIX and c["noise"]["tracebacks"] >= 1 and c["noise"]["searches"] >= 1
            and (c["exit_status"] == "submitted" or not strict))


def select(cands: list[dict]) -> tuple[list[dict], list[dict]]:
    """12 cases + 3 spares: valid, one instance each, as many repos as possible, longest prefixes first. Runs that ended
    with the agent's own submit come first; resolved runs auto-submitted at the context limit only fill gaps."""
    rank = lambda c: (-(c["prefix_chars"] + 10_000 * (c["noise"]["tests"] > 0)), c["row_idx"])  # noqa: E731
    strict = sorted((c for c in cands if valid(c)), key=rank)
    fallback = sorted((c for c in cands if valid(c, strict=False) and not valid(c)), key=rank)
    picked, seen_inst, seen_repo = [], set(), set()
    for pool, distinct in ((strict, True), (fallback, True), (strict, False), (fallback, False)):
        for c in pool:
            if len(picked) < N_CASES + N_SPARES and c["instance_id"] not in seen_inst and not (distinct and c["repo"] in seen_repo):
                picked.append(c)
                seen_inst.add(c["instance_id"])
                seen_repo.add(c["repo"])
    return picked[:N_CASES], picked[N_CASES:]


def fetch(max_pages: int = 801, workers: int = 2, min_repos: int = 10) -> dict:
    scan = _load_json(SCAN, {"pages_done": [], "candidates": []})
    scan.setdefault("resolved_sizes", [])
    total = _get_json(_rows_url(0, 1)).get("num_rows_total", 80036)
    offsets = list(range(0, total, 100))
    random.Random(0).shuffle(offsets)  # spread over the dataset: rows of one instance are stored together
    todo = [o for o in offsets if o not in set(scan["pages_done"])][:max(0, max_pages - len(scan["pages_done"]))]

    def enough() -> bool:
        ok = [c for c in scan["candidates"] if valid(c)]
        return len({c["instance_id"] for c in ok}) >= N_CASES + N_SPARES and len({c["repo"] for c in ok}) >= min_repos

    with ThreadPoolExecutor(workers) as ex:
        for i in range(0, len(todo), workers):
            if enough():
                break
            batch = todo[i:i + workers]
            for off, (rows, sizes) in zip(batch, ex.map(_scan_page, batch)):
                scan["resolved_sizes"] += sizes
                for idx, row in rows:
                    info = candidate_info(idx, row)
                    if info:
                        _save_json(RAW / f"{idx}.json", row)
                        scan["candidates"].append(info)
                scan["pages_done"].append(off)
            _save_json(SCAN, scan)
            time.sleep(1.0)  # datasets-server rate-limits anonymous clients
            ok = [c for c in scan["candidates"] if valid(c, strict=False)]
            sz = scan["resolved_sizes"]
            strict = [c for c in ok if valid(c)]
            print(f"pages {len(scan['pages_done'])}  resolved {len(sz)} (>={MIN_ROW_CHARS // 1000}k chars: {sum(x >= MIN_ROW_CHARS and s for x, s in sz)} "
                  f"+ {sum(x >= MIN_ROW_CHARS and not s for x, s in sz)} exit_context)  candidates {len(scan['candidates'])}  "
                  f"valid {len(strict)} (+{len(ok) - len(strict)})  repos {len({c['repo'] for c in strict})}", flush=True)
    cases, spares = select(scan["candidates"])
    sel = {"dataset": DATASET, "license": LICENSE, "cases": cases, "spares": spares}
    _save_json(SELECTED, sel)
    return sel


# ================================================================================================ convert

_FENCE = re.compile(r"^```(\S*)\s*\n|^```\s*$", re.M)
_TRAILER = re.compile(r"\(Open file: ([^\n]*)\)\s*\n\(Current directory: ([^\n]*)\)\s*\n?bash-\$\s*$")


def split_action(text: str) -> tuple[str, str | None]:
    """SWE-agent's own thought/action parser: the last top-level fenced block is the command, the rest is discussion."""
    stack, last = [], None
    for m in _FENCE.finditer(text):
        if stack and not m.group(1):
            start = stack.pop()
            if not stack:
                last = (start, m)
        elif m.group(1) is not None:
            stack.append(m)
    if not last:
        return _clean(text), None
    s, e = last
    return _clean(text[:s.start()] + text[e.end():]), text[s.end():e.start()].strip("\n")


def _clean(text: str) -> str:
    return re.sub(r"^\s*DISCUSSION\s*\n", "", text).strip()


def trailer(obs: str) -> tuple[str | None, str | None]:
    m = _TRAILER.search(obs.rstrip())
    if not m:
        return None, None
    f = m.group(1).strip()
    return (None if f in ("n/a", "") else f), m.group(2).strip()


def interface_note(system: str) -> str:
    sigs = re.findall(r"^\s*signature: (.+)$", system or "", re.M)
    window = re.search(r"shows you (\d+) lines", system or "")
    sigs = sigs or ["open <path> [<line_number>]", "goto <line_number>", "scroll_down", "scroll_up", "create <filename>",
                    "submit", "search_dir <search_term> [<dir>]", "search_file <search_term> [<file>]",
                    "find_file <file_name> [<dir>]", "edit <start_line>:<end_line>"]
    return ("[Session setup] You are an autonomous programmer fixing an issue in a repository through the `bash` tool, "
            "one command per call. Besides normal shell commands there is a file viewer that shows "
            f"{window.group(1) if window else 100} lines at a time and these commands: " + "; ".join(s.strip() for s in sigs) +
            ". `edit <start_line>:<end_line>` replaces those lines of the open file with the text on the following lines, "
            "up to a line `end_of_edit`. Every tool output ends with the open file and the current directory.")


def convert_row(row: dict) -> dict:
    """Trajectory -> neutral turns. Each ai turn becomes an assistant turn (discussion + one bash call), each following
    observation the tool turn that answers it. Returns turns, the trajectory index of each turn, and the repo root."""
    traj = row["trajectory"]
    system = next((t.get("system_prompt") for t in traj if t["role"] == "system" and t.get("system_prompt")), "")
    items = [(i, t) for i, t in enumerate(traj) if t["role"] != "system"]
    if not items or items[0][1]["role"] != "user":
        raise ValueError("trajectory does not start with the issue")
    i0, first = items[0]
    issue = (first.get("text") or "").strip()
    _, root = trailer(issue)
    if not root:
        raise ValueError("no repository root in the issue prompt")
    turns = [{"role": "user", "text": interface_note(system) + "\n\n" + issue}]
    idx = [i0]
    for i, t in items[1:]:
        text = (t.get("text") or "").strip()
        last = turns[-1]
        if t["role"] == "ai":
            if last["role"] == "assistant" and last["calls"]:  # an action without a recorded observation
                turns.append({"role": "tool", "results": [{"id": last["calls"][0]["id"], "output": "(no output)", "is_error": False}]})
                idx.append(i)
            disc, cmd = split_action(text)
            calls = [{"id": f"t{i}", "name": "bash", "args": {"command": cmd}}] if cmd else []
            if last["role"] == "assistant" and not last["calls"]:  # two replies in a row: keep one assistant turn
                last["text"] = (last["text"] + "\n\n" + disc).strip()
                last["calls"] = calls
                continue
            turns.append({"role": "assistant", "text": disc or ("" if calls else "(no reply)"), "calls": calls})
            idx.append(i)
        else:
            if last["role"] == "assistant" and last["calls"]:
                turns.append({"role": "tool", "results": [{"id": last["calls"][0]["id"], "output": text, "is_error": False}]})
            elif last["role"] == "user":
                last["text"] += "\n\n" + text
                continue
            elif last["role"] == "tool":
                last["results"][0]["output"] += "\n\n" + text
                continue
            else:
                turns.append({"role": "user", "text": text})
            idx.append(i)
    return {"turns": turns, "idx": idx, "root": root.rstrip("/") or "/", "issue": issue, "system": system}


def _turn_chars(t: dict) -> int:
    return len(tr.full_text({"phases": [{"id": "x", "turns": [t]}]})) + 1


# ================================================================================================ actions and files

def _head(cmd: str) -> str:
    s = cmd.strip()
    return s.split(None, 1)[0] if s else ""


def _first_line(cmd: str) -> str:
    return cmd.strip().split("\n", 1)[0].split("<<", 1)[0]


def _unquote_strip(line: str) -> str:
    return re.sub(r"'[^']*'|\"[^\"]*\"", "''", line)


def bash_writes(cmd: str) -> tuple[list[str], list[str]]:
    """Files a plain shell command writes and deletes (first line only, so heredoc bodies never count)."""
    line = _unquote_strip(_first_line(cmd))
    writes, deletes = [], []
    for seg in re.split(r"&&|\|\||;|\|", line):
        seg = seg.strip()
        writes += [m.group(1) for m in re.finditer(r"(?:^|[^0-9&>])>>?\s*([^\s;&|<>()']+)", seg)]
        try:
            toks = shlex.split(seg)
        except ValueError:
            toks = seg.split()
        if not toks:
            continue
        args = [a for a in toks[1:] if not a.startswith("-") and a != "''"]
        if toks[0] == "sed" and any(a.startswith("-i") for a in toks[1:]) and args:
            writes.append(args[-1])
        elif toks[0] in ("tee", "touch") and args:
            writes += [a for a in args if ">" not in a]
        elif toks[0] in ("cp", "mv") and len(args) >= 2:
            writes.append(args[-1])
            if toks[0] == "mv":
                deletes += args[:-1]
        elif toks[0] == "rm" and args:
            deletes += args
    bad = {"/dev/null", "&1", "&2", "''"}
    return [w for w in writes if w not in bad], [d for d in deletes if d not in bad]


_RUN = re.compile(r"(?:^|[\s;&|(])(python[\d.]*|pytest|py\.test|tox|nosetests|coverage|make)\b")


def action_type(cmd: str) -> str:
    h = _head(cmd)
    if h == "edit":
        return "edit"
    if h == "create":
        return "create"
    if h == "submit":
        return "submit"
    if h in ("open", "goto", "scroll_down", "scroll_up", "cat", "head", "tail", "less", "nl"):
        return "open"
    if h in ("search_dir", "search_file", "find_file", "grep", "find", "ls", "rg", "ag", "tree", "git", "pwd", "which", "pip"):
        return "search"
    if _RUN.search(_first_line(cmd)):
        return "run"
    if h in ("sed", "echo", "printf", "tee", "touch", "cp", "mv", "patch") and bash_writes(cmd)[0]:
        return "edit"
    return "other"


def substantive(cmd: str) -> bool:
    return action_type(cmd) in ("edit", "create", "run")


def rel(path: str | None, cwd: str | None, root: str) -> str:
    if not path:
        return ""
    p = path.strip().strip("'\"`")
    if not p.startswith("/"):
        p = posixpath.join(cwd or root, p)
    p = posixpath.normpath(p)
    if root != "/" and p.startswith(root + "/"):
        return p[len(root) + 1:]
    return p


def steps(conv: dict) -> list[dict]:
    """One record per assistant turn that ran a command: where it sits, what it did, the open file and cwd before it."""
    turns, root = conv["turns"], conv["root"]
    open_f, cwd = trailer(conv["issue"])
    out = []
    for i, t in enumerate(turns):
        if t["role"] == "tool":
            f, d = trailer(t["results"][0]["output"])
            if d:
                open_f, cwd = f, d
            continue
        if t["role"] != "assistant" or not t["calls"]:
            continue
        cmd = t["calls"][0]["args"]["command"]
        obs = turns[i + 1]["results"][0]["output"] if i + 1 < len(turns) and turns[i + 1]["role"] == "tool" else ""
        out.append({"turn": i, "command": cmd, "type": action_type(cmd), "open": open_f, "cwd": cwd or root,
                    "discussion": t["text"], "obs": obs, "file": _target(cmd, open_f, cwd or root, root)})
    return out


def _target(cmd: str, open_f: str | None, cwd: str, root: str) -> str:
    kind, toks = action_type(cmd), _first_line(cmd).split()
    if kind == "edit" and _head(cmd) == "edit":
        return rel(open_f, cwd, root)
    if kind == "edit":
        w = bash_writes(cmd)[0]
        return rel(w[-1], cwd, root) if w else ""
    if kind in ("create", "open") and len(toks) > 1 and toks[0] in ("create", "open", "cat", "head", "tail", "nl", "less"):
        args = [a for a in toks[1:] if not a.startswith("-")]
        return rel(args[0], cwd, root) if args else ""
    if kind == "open":
        return rel(open_f, cwd, root)
    if kind == "run":
        m = re.search(r"([\w./-]+\.py)\b", _first_line(cmd))
        return rel(m.group(1), cwd, root) if m else ""
    if _head(cmd) == "search_file" and len(toks) > 2:
        return rel(toks[-1], cwd, root)
    return ""


def track_files(prefix_steps: list[dict], root: str) -> dict:
    """Files the agent edited or created up to the cut (minus deleted ones), and files it only read (decoys)."""
    edited, read, deleted = [], [], set()

    def add(lst, p):
        if p and p not in lst:
            lst.append(p)

    for s in prefix_steps:
        cmd, h, obs = s["command"], _head(s["command"]), s["obs"]
        if h == "edit":
            if "File updated" in obs and s["open"]:
                p = rel(s["open"], s["cwd"], root)
                add(edited, p)
                deleted.discard(p)
        elif h == "create":
            m = re.search(r"\[File: ([^\n]+?) \(\d+ lines total\)\]", obs)
            p = rel(m.group(1), s["cwd"], root) if m else s["file"]
            add(edited, p)
            deleted.discard(p)
        elif h == "open":
            m = re.search(r"\[File: ([^\n]+?) \(\d+ lines total\)\]", obs)
            if m:
                add(read, rel(m.group(1), s["cwd"], root))
        elif h in ("cat", "head", "tail", "nl") and s["file"]:
            add(read, s["file"])
        else:
            w, d = bash_writes(cmd)
            for p in w:
                p = rel(p, s["cwd"], root)
                add(edited, p)
                deleted.discard(p)
            for p in d:
                deleted.add(rel(p, s["cwd"], root))
    edited = [p for p in edited if p not in deleted]
    return {"edited": edited, "read_only": [p for p in read if p not in edited], "deleted": sorted(deleted)}


def _cmd_files(step: dict, root: str) -> list[str]:
    """Every file a command names (a run step acts on its script and on its arguments)."""
    out = [step["file"]] if step["file"] else []
    if step["type"] == "run":
        for tok in _first_line(step["command"]).split()[1:]:
            if not tok.startswith("-") and re.search(r"\.\w{1,5}$|/", tok) and not re.match(r"^\w+://", tok):
                p = rel(tok, step["cwd"], root)
                if p not in out:
                    out.append(p)
    return out


# the agent itself calls the action a mistake ("I edited the wrong file") within its next two turns
_RETRACT = re.compile(r"\b(?:wrong file|edited the wrong|mistakenly|by mistake|incorrect file)\b", re.I)


def choose_cut(conv: dict) -> dict | None:
    """The first substantive action ~80-90% of the way through the agent's actions, right after a tool output, that
    the agent does not retract right away (a retracted action is a mistake, not the next step to predict)."""
    st = steps(conv)
    n = len(st)
    if n < 12:
        return None
    for lo, hi in (CUT_WINDOW, (0.75, 0.93)):
        for k in range(int(n * lo), min(n - 1, int(n * hi) + 1)):
            s = st[k]
            prev = conv["turns"][s["turn"] - 1]
            if any(_RETRACT.search(x["discussion"]) for x in st[k + 1:k + 3]):
                continue
            if substantive(s["command"]) and prev["role"] == "tool" and s["turn"] > 4:
                return {"step": k, "turn": s["turn"], "frac": round(k / n, 3), "action": s["type"]}
    return None


def split_phases(turns: list[dict], n: int = 4) -> list[dict]:
    """n phases of roughly equal chars; boundaries right after a tool turn that is followed by an assistant turn."""
    sizes = [_turn_chars(t) for t in turns]
    total, cum, run = sum(sizes), [], 0
    for s in sizes:
        run += s
        cum.append(run)
    cands = [i for i in range(len(turns) - 1) if turns[i]["role"] == "tool" and turns[i + 1]["role"] == "assistant"]
    bounds, prev = [], -1
    for k in range(1, n):
        target = total * k / n
        left = n - k  # phases still to place after this boundary need candidates beyond it
        pool = [c for c in cands if c > prev and len([d for d in cands if d > c]) >= left - 1]
        if not pool:
            raise ValueError("not enough phase boundaries")
        b = min(pool, key=lambda c: abs(cum[c] - target))
        bounds.append(b)
        prev = b
    edges = [-1, *bounds, len(turns) - 1]
    phases = []
    for k in range(n):
        seg = turns[edges[k] + 1: edges[k + 1] + 1]
        acts = sum(1 for t in turns[:edges[k] + 1] if t["role"] == "assistant" and t["calls"])
        last = acts + sum(1 for t in seg if t["role"] == "assistant" and t["calls"])
        phases.append({"id": f"P{k + 1}", "title": f"agent actions {acts + 1}-{last}", "turns": seg})
    return phases


# ================================================================================================ answer key: generation

GENERATOR_PROMPT = """You are building the answer key for a memory test. Below is the transcript of a real software-engineering agent session: an autonomous agent fixing a GitHub issue in a repository through a shell, one command per turn. Turns are numbered; turn 0 is the issue prompt. ASSISTANT turns hold the agent's reasoning and its command; TOOL OUTPUT turns hold what the command printed.

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
- key_points: 2-3 Python regular expressions (matched case-insensitively), each matching one essential element of the reason; accept paraphrases and other word forms (alternations, stems such as resolv\\w*), but a vague or generic answer must not match.

Reply with exactly one JSON object and nothing else:
{"recall": [{"question": "...", "answer": "...", "kind": "...", "turn": 0, "superseded": []}],
 "decisions": [{"question": "...", "reference": "...", "quote": "...", "turn": 0, "key_points": ["..."]}]}

TRANSCRIPT
"""


def render(turns: list[dict]) -> str:
    out = []
    for i, t in enumerate(turns):
        if t["role"] == "user":
            out.append(f"=== [turn {i}] {'ISSUE PROMPT' if i == 0 else 'USER'} ===\n{t['text']}")
        elif t["role"] == "assistant":
            cmd = "".join(f"\n$ {c['args'].get('command', '')}" for c in t["calls"])
            out.append(f"=== [turn {i}] ASSISTANT ===\n{t['text']}{cmd}")
        else:
            out.append(f"=== [turn {i}] TOOL OUTPUT ===\n" + "\n".join(r["output"] for r in t["results"]))
    return "\n\n".join(out)


def generate(case_id: str, turns: list[dict], avoid: list[str] | None = None) -> dict:
    from lab import providers

    prompt = GENERATOR_PROMPT + render(turns)
    if avoid:
        prompt += "\n\nDo not repeat these questions (already used or rejected):\n" + "\n".join(f"- {q}" for q in avoid)
    res = providers.build_adapter("openai", GEN_MODEL).ask([], prompt, max_tokens=32000)
    if not res.ok:
        raise RuntimeError(f"generator failed: {res.error}")
    return {"model": GEN_MODEL, "text": res.text, "parsed": probe.parse_json(res.text)}


# ================================================================================================ answer key: verification

def _bnd(value: str, cs: bool = True) -> re.Pattern:
    return re.compile(rf"(?<![\w-]){re.escape(value)}(?![\w-])", 0 if cs else re.I)


def _tool_text(t: dict) -> str:
    return "\n".join(r["output"] for r in t["results"]) if t["role"] == "tool" else ""


def _norm_answer(kind: str, ans: str, root: str) -> str:
    a = ans.strip().strip("`'\"").strip()
    if kind == "text":
        return " ".join(re.findall(r"[\w.-]+", a)).strip(". -")
    if kind == "path_line":
        m = re.match(r"(.+\.py)\D+(\d+)$", a)
        if m:
            return f"{rel(m.group(1), root, root)}:{m.group(2)}"
    if kind in ("token", "token_cs") and a.startswith(root + "/"):
        return a[len(root) + 1:]
    return a


def present(kind: str, value: str, text: str) -> bool:
    """Is this exact value written in the text (a tool output, the issue, a question)?"""
    if not value or not text:
        return False
    if kind == "path_line":
        return any(score._same("path_line", value, c) or score._same("path_line", c, value) for c in score.extract("path_line", text))
    if kind == "text":
        return all(_bnd(w, False).search(text) for w in value.split())
    if kind in ("token", "text"):
        return bool(_bnd(value, False).search(text))
    return bool(_bnd(value).search(text))


def _truth(kind: str, value: str) -> str | None:
    if kind in ("token", "token_cs", "text"):
        return value
    got = score.extract(kind, value, [value])
    return got[0] if len(got) == 1 else None


def _decoys(kind: str, raw: str, out: str, all_tools: str, exclude: set) -> list[str]:
    """Same-kind values that follow the same words elsewhere in the tool outputs ("Found 3 matches" vs "Found 7 matches")."""
    if kind in ("text", "path_line"):
        return []
    i = out.find(raw)
    if i < 0:
        return []
    pre = out[max(out.rfind("\n", 0, i) + 1, i - 24): i]
    if len(pre.strip()) < 4 or len(re.sub(r"[\d\s:]", "", pre)) < 2:
        return []  # a bare viewer prefix ("464:            ") would make the first word of every line a decoy
    rx = re.compile(re.sub(r"\d+", r"\\d+", re.escape(pre)) + r"([\w.,:/-]+)")
    vals = []
    for m in rx.finditer(all_tools):
        v = m.group(1).rstrip(".,:")
        if kind not in ("token", "token_cs"):
            got = score.extract(kind, v, [v])
            v = got[0] if got else ""
        if v and v not in exclude and v not in vals:
            vals.append(v)
    return vals[:5]


def _position(out: str, raw: str) -> str:
    i = max(0, out.find(raw))
    if len(out) < 1500:
        return "short"
    f = i / len(out)
    return "head" if f < 0.33 else "middle" if f < 0.67 else "tail"


def phase_of(phases: list[dict], turn: int) -> str:
    n = 0
    for p in phases:
        n += len(p["turns"])
        if turn < n:
            return p["id"]
    return phases[-1]["id"]


def verify_recall(c: dict, turns: list[dict], phases: list[dict], root: str) -> tuple[dict | None, str]:
    """A generated recall probe survives only if its answer is verbatim in the named tool output, not in the issue or the
    question, and lab.score recovers it as CORRECT."""
    kind, q = c.get("kind"), str(c.get("question") or "").strip()
    try:
        ti = int(c.get("turn"))
    except (TypeError, ValueError):
        return None, "bad turn"
    if kind not in KINDS or not q or not 0 < ti < len(turns) or turns[ti]["role"] != "tool":
        return None, "bad kind/turn"
    raw = str(c.get("answer") or "").strip().strip("`").strip()
    value = _norm_answer(kind, raw, root)
    if not value or len(value) > 80:
        return None, "empty or long answer"
    out = _tool_text(turns[ti])
    if not present(kind, value, out):
        return None, "not in that tool output"
    if present(kind, value, turns[0]["text"]):
        return None, "in the issue prompt"
    if present(kind, value, q) or (raw and raw.lower() in q.lower()):
        return None, "answer in the question"
    truth = _truth(kind, value)
    if not truth:
        return None, "kind extractor does not recover it"
    full = render(turns)
    tools = "\n".join(_tool_text(t) for t in turns)
    stale = [s for s in (_truth(kind, _norm_answer(kind, str(v), root)) for v in c.get("superseded") or [])
             if s and s != truth and present(kind, s, tools)]
    decoys = [d for d in _decoys(kind, raw if raw in out else value, out, tools, {truth, value, *stale})
              if not score._same(kind, d, truth)]
    fact = sc.Fact("x", q, truth, kind, [], "", "", stale=stale, decoys=decoys)
    if score.classify(fact, raw, full)["outcome"] != "CORRECT":
        return None, "scorer does not accept the answer"
    later = "\n".join(t["text"] for t in turns[ti + 1:] if t["role"] == "assistant")
    base_ok = kind in ("token", "token_cs") and "/" in truth and _basename_unique(truth, tools, root)
    traps = (["OVR"] if stale else []) + (["DEC"] if decoys else []) + (["BUR"] if _position(out, raw) == "middle" else [])
    return {"question": q, "truth": truth, "answer_raw": raw, "kind": kind, "turn": ti, "stale": stale, "decoys": decoys,
            "phase": phase_of(phases, ti), "turn_frac": round(ti / len(turns), 3), "position": _position(out, raw),
            "salience": "acknowledged" if present(kind, truth, later) else "silent", "traps": traps, "base_ok": base_ok}, "ok"


def _basename_unique(path: str, tools: str, root: str) -> bool:
    """A bare file name identifies this path: every path in the tool outputs ending in that name is this path."""
    base = posixpath.basename(path)
    for m in re.finditer(rf"[\w./-]*(?<![\w-]){re.escape(base)}(?![\w-])", tools):
        p = rel(m.group(0), root, root) if m.group(0).startswith("/") else m.group(0).lstrip("./")
        if p != path and not path.endswith("/" + p) and p != base:
            return False
    return True


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def verify_decision(d: dict, turns: list[dict], phases: list[dict]) -> tuple[dict | None, str]:
    q, ref, quote = (str(d.get(k) or "").strip() for k in ("question", "reference", "quote"))
    if not q or not ref or len(quote.split()) < 5:
        return None, "missing fields"
    try:
        ti = int(d.get("turn"))
    except (TypeError, ValueError):
        ti = -1
    texts = {i: t["text"] for i, t in enumerate(turns) if t["role"] == "assistant"}
    hit = ti if ti in texts and _squash(quote) in _squash(texts[ti]) else next(
        (i for i, s in texts.items() if _squash(quote) in _squash(s)), None)
    if hit is None:
        return None, "quote not found in the agent's text"
    kps = []
    for k in d.get("key_points") or []:
        try:
            if re.search(k, ref, re.I) or re.search(k, quote, re.I):
                kps.append(k)
        except re.error:
            continue
    if not kps:
        return None, "no usable key points"
    return {"question": q, "reference": ref, "quote": quote, "turn": hit, "phase": phase_of(phases, hit), "key_points": kps}, "ok"


def _spread(items: list[dict], n: int) -> list[dict]:
    by = {}
    for it in items:
        by.setdefault(it["phase"], []).append(it)
    out = []
    while len(out) < n and any(by.values()):
        for p in sorted(by):
            if by[p] and len(out) < n:
                out.append(by[p].pop(0))
    return sorted(out, key=lambda it: it["turn"])


# ================================================================================================ build

def build_case(row_idx: int, row: dict, use_gen: bool = True) -> dict:
    conv = convert_row(row)
    cut = choose_cut(conv)
    if cut is None:
        raise ValueError("no cut point")
    st = steps(conv)
    prefix = conv["turns"][:cut["turn"]]
    phases = split_phases(prefix)
    case_id = f"b-{row['instance_id']}"
    nxt = st[cut["step"]: cut["step"] + 3]
    hidden = {"discussion": nxt[0]["discussion"],
              "actions": [{"command": s["command"], "type": s["type"], "file": s["file"], "discussion": s["discussion"]} for s in nxt]}
    files = track_files(st[:cut["step"]], conv["root"])
    key = {"root": conv["root"], "files_edited": files["edited"], "files_read_only": files["read_only"],
           "files_deleted": files["deleted"],
           "continuation": {"command": nxt[0]["command"], "type": nxt[0]["type"], "file": nxt[0]["file"],
                            "files": _cmd_files(nxt[0], conv["root"]), "discussion": nxt[0]["discussion"]},
           "recall": [], "decisions": [], "rejected": [], "dropped": []}
    case = {"case_id": case_id, "layer": "b", "phases": phases, "tools": TOOLS, "hidden": hidden, "key": key,
            "provenance": {"dataset": DATASET, "license": LICENSE, "url": f"https://huggingface.co/datasets/{DATASET}",
                           "row_offset": row_idx, "instance_id": row["instance_id"], "repo": repo_of(row["instance_id"]),
                           "model_name": row["model_name"], "exit_status": row["exit_status"], "resolved": bool(row["target"]),
                           "trajectory_turns": len(row["trajectory"]), "agent_actions": len(st),
                           "cut_action": cut["step"] + 1, "cut_fraction": cut["frac"]}}
    case["chars"] = len(tr.full_text(case))
    case["approx_tokens"] = round(case["chars"] / tr.CHARS_PER_TOKEN)
    if use_gen:
        add_probes(case, prefix)
    return case


def add_probes(case: dict, turns: list[dict]) -> None:
    key, phases = case["key"], case["phases"]
    gp = GEN / f"{case['case_id']}.json"
    gens = _load_json(gp, [])
    recall, decisions, rejected = [], [], []

    def absorb(g: dict) -> None:
        obj = g.get("parsed") or {}
        for c in obj.get("recall") or []:
            if any(_squash(str(c.get("question", ""))) == _squash(r["question"]) for r in recall):
                continue
            ok, why = verify_recall(c, turns, phases, key["root"])
            recall.append(ok) if ok else rejected.append({"question": c.get("question"), "answer": c.get("answer"), "why": why})
        for d in obj.get("decisions") or []:
            ok, why = verify_decision(d, turns, phases)
            decisions.append(ok) if ok else rejected.append({"question": d.get("question"), "why": why})

    for g in gens:
        absorb(g)
    attempts = 0
    while (len(recall) < MAX_RECALL or len(decisions) < N_DECISIONS) and attempts < 2 and len(gens) < 3:
        g = generate(case["case_id"], turns, avoid=[r["question"] for r in recall] + [r["question"] for r in rejected if r["question"]])
        gens.append(g)
        _save_json(gp, gens)
        absorb(g)
        attempts += 1
    recall = _spread(recall, MAX_RECALL)
    for i, r in enumerate(recall, 1):
        r["id"] = f"r{i:02d}"
    decisions = decisions[:N_DECISIONS]
    for i, d in enumerate(decisions, 1):
        d["id"] = f"d{i}"
    key.update(recall=recall, decisions=decisions, rejected=rejected, generator={"model": GEN_MODEL, "calls": len(gens)})


def apply_edits(case: dict) -> None:
    ed = _load_json(EDITS, {}).get(case["case_id"], {})
    key = case["key"]
    for pid, fields in ed.get("fix", {}).items():
        for r in key["recall"] + key["decisions"]:
            if r["id"] == pid:
                r.update(fields)
                r["fixed"] = True
    drops = ed.get("drop", {})
    key["dropped"] = [{**r, "reason": drops[r["id"]]} for r in key["recall"] + key["decisions"] if r["id"] in drops]
    key["recall"] = [r for r in key["recall"] if r["id"] not in drops]
    key["decisions"] = [d for d in key["decisions"] if d["id"] not in drops]


def save_case(case: dict) -> Path:
    p = CASES / f"{case['case_id']}.json"
    _save_json(p, case)
    return p


def build(force: bool = False, use_gen: bool = True, workers: int = 4) -> list[dict]:
    sel = _load_json(SELECTED, None)
    if not sel:
        raise SystemExit("run `python -m lab.replay fetch` first")
    picks = sel["cases"]

    def one(c: dict) -> dict:
        p = CASES / f"b-{c['instance_id']}.json"
        if p.exists() and not force:
            return json.loads(p.read_text())
        case = build_case(c["row_idx"], json.loads((RAW / f"{c['row_idx']}.json").read_text()), use_gen)
        apply_edits(case)
        return case

    with ThreadPoolExecutor(workers) as ex:
        cases = list(ex.map(one, picks))
    order = sorted(cases, key=lambda c: c["chars"])
    for i, c in enumerate(order):
        c["size_bucket"] = ["short", "medium", "long"][min(2, i * 3 // len(order))]
        save_case(c)
    return cases


def load_cases() -> list[dict]:
    return sorted((json.loads(p.read_text()) for p in CASES.glob("b-*.json")), key=lambda c: c["case_id"])


def load_case(case_id: str) -> dict:
    return json.loads((CASES / f"{case_id}.json").read_text())


# ================================================================================================ probes

def norm_path(p, root: str, known: set) -> str:
    s = str(p).strip().strip("`'\"").strip()
    s = re.sub(r"^\./", "", s)
    if s.startswith(root + "/"):
        s = s[len(root) + 1:]
    elif root != "/" and s.startswith(root.lstrip("/") + "/") and s not in known and s[len(root):] in known:
        s = s[len(root):]  # "<repo dir>/pkg/a.py" written for "pkg/a.py"
    return posixpath.normpath(s) if s else ""


def keyterm(answer: str, patterns: list[str]) -> float:
    if not patterns:
        return 0.0
    hits = 0
    for p in patterns:
        try:
            hits += bool(re.search(p, answer or "", re.I))
        except re.error:
            pass
    return round(hits / len(patterns), 3)


_ACTION_WORDS = [("create", r"\bcreat|\bnew file|\bwrite a new"), ("edit", r"\bedit|modif|chang|fix|patch|updat|replac|insert"),
                 ("run", r"\brun|execut|test|pytest|python"), ("search", r"search|grep|find|look for|list"),
                 ("open", r"\bopen|view|read|inspect|scroll|goto"), ("submit", r"submit")]


def _pred_action(step: dict) -> str:
    a = str(step.get("action") or "").strip().lower()
    if a in ACTIONS:
        return a
    cmd = str(step.get("command") or "").strip()
    if cmd and cmd.upper() != "UNKNOWN":
        t = action_type(cmd)
        if t != "other":
            return t
    return next((name for name, rx in _ACTION_WORDS if re.search(rx, a)), "other" if a else "")


class ReplayProbes:
    """ProbeSet for layer B: `quiz` (exact values from real tool outputs) and `state` (files edited, next step, reasons)."""

    names = ["quiz", "state"]

    def __init__(self, case: dict):
        self.case = case
        self.key = case["key"]
        self.root = self.key["root"]
        self._tools: str | None = None

    @property
    def tool_text(self) -> str:
        if self._tools is None:
            self._tools = "\n".join(_tool_text(t) for p in self.case["phases"] for t in p["turns"])
        return self._tools

    def _command_files(self, cmd: str) -> list[str]:
        if not cmd.strip() or cmd.strip().upper() == "UNKNOWN":
            return []
        st = {"file": _target(cmd, None, self.root, self.root), "type": action_type(cmd), "command": cmd, "cwd": self.root}
        return [f for f in _cmd_files(st, self.root) if f]

    def facts(self) -> list[sc.Fact]:
        return [sc.Fact(r["id"], r["question"], r["truth"], r["kind"], r.get("traps", []), r["phase"], r.get("salience", ""),
                        stale=r.get("stale", []), decoys=r.get("decoys", []), alts=r.get("alts", []))
                for r in self.key["recall"]]

    def prompt(self, name: str) -> str:
        if name == "quiz":
            lines = [
                "Before we continue, I need a precise status check. Answer every question below using only what you know from this session.",
                "Do not run any tools. Do not guess: if you do not know an answer, write \"UNKNOWN\".",
                "Give only the value for each answer, with no explanation.",
                "Reply with exactly one JSON object that maps each question id to a string answer, and nothing else.",
                "",
            ]
            return "\n".join(lines + [f"{r['id']}: {r['question']}" for r in self.key["recall"]])
        if name == "state":
            lines = [
                "Before we continue, I need a precise status check of this session. Do not run any tools. "
                "Do not guess: write \"UNKNOWN\" for anything you do not know.",
                "Reply with exactly one JSON object of this shape, and nothing else:",
                '{"files_edited": ["<path>", ...], "next_step": {"action": "edit|create|run|open|search|submit", '
                '"command": "<the exact command>", "file": "<path or empty>"}, "decisions": {"<id>": "<one-sentence reason>"}}',
                "",
                "files_edited: every repository file you have created or modified so far in this session (with edit or create "
                "commands or shell writes), as paths relative to the repository root. Leave out files you only read, files you "
                "deleted, and files that programs you ran wrote as a side effect.",
                "next_step: the single next action you would take to continue the task from exactly where the session stopped: "
                "the action type, the exact command you would run next, and the repository-relative path of the file it acts on.",
                "decisions: answer each question below in one sentence.",
                "",
            ]
            return "\n".join(lines + [f"{d['id']}: {d['question']}" for d in self.key["decisions"]])
        raise ValueError(name)

    def score(self, name: str, reply_text: str, ctx: dict) -> dict:
        obj = probe.parse_json(reply_text or "")
        if name == "quiz":
            return {"probe": name, "parsed": obj is not None, "items": self._quiz(obj or {}, ctx), "judge_items": [], "extra": {}}
        if name == "state":
            items, judge, extra = self._state(obj or {})
            return {"probe": name, "parsed": obj is not None, "items": items, "judge_items": judge, "extra": extra}
        raise ValueError(name)

    def _quiz(self, answers: dict, ctx: dict) -> list[dict]:
        text, summary = ctx.get("transcript_text") or "", ctx.get("summary")
        by_id = {r["id"]: r for r in self.key["recall"]}
        items = []
        for f in self.facts():
            ans = answers.get(f.id)
            if isinstance(ans, list):
                ans = ", ".join(str(a) for a in ans)
            r = by_id[f.id]
            if r.get("base_ok") and ans is not None and str(ans).strip().strip("`'\"") == posixpath.basename(f.truth):
                ans = f.truth  # the bare file name names the same file
            row = score.classify(f, None if ans is None else str(ans), text)
            it = {"id": f.id, "measure": "recall", "score": float(row["outcome"] == "CORRECT"), "correct": row["outcome"] == "CORRECT",
                  "outcome": row["outcome"], "answer": row["answer"], "truth": f.truth, "extracted": row["extracted"],
                  "kind": f.kind, "traps": f.traps, "phase": f.phase, "salience": f.salience, "position": r.get("position"),
                  "turn_frac": r.get("turn_frac")}
            if summary is not None:
                it["in_summary"] = score.in_summary(f, summary)
            items.append(it)
        return items

    def _state(self, obj: dict) -> tuple[list[dict], list[dict], dict]:
        key = self.key
        truth = set(key["files_edited"])
        known = truth | set(key["files_read_only"])
        raw = obj.get("files_edited")
        raw = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
        got = {norm_path(p, self.root, known) for p in raw if str(p).strip() and str(p).strip().upper() != "UNKNOWN"} - {""}
        tp = len(got & truth)
        if not truth and not got:  # nothing edited and nothing claimed: a perfect answer
            prec = rec = f1 = 1.0
        else:
            prec = tp / len(got) if got else 0.0
            rec = tp / len(truth) if truth else 0.0
            f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        items = [{"id": "files_edited", "measure": "artifact", "score": round(f1, 3), "correct": got == truth, "outcome": None,
                  "precision": round(prec, 3), "recall": round(rec, 3), "f1": round(f1, 3), "truth": sorted(truth),
                  "got": sorted(got), "decoys_reported": sorted(got & set(key["files_read_only"]))}]
        cont = key["continuation"]
        ns = obj.get("next_step")
        step = ns if isinstance(ns, dict) else {"command": ns} if isinstance(ns, str) and ns.strip() else {}
        pa = _pred_action(step)
        pf = norm_path(step.get("file") or "", self.root, known | {cont["file"]})
        if pf.upper() == "UNKNOWN":
            pf = ""
        type_ok = pa == cont["type"]
        targets = cont.get("files") or ([cont["file"]] if cont["file"] else [])
        cand = [pf] if pf else self._command_files(str(step.get("command") or ""))  # no file given: the one its command names
        file_ok = bool(cont["file"]) and any(f in targets or (posixpath.basename(f) == f and any(
            posixpath.basename(t) == f and _basename_unique(t, self.tool_text, self.root) for t in targets)) for f in cand)
        pf = pf or ",".join(cand)
        parts = [type_ok] + ([file_ok] if cont["file"] else [])
        items.append({"id": "next_step", "measure": "continuation", "score": round(sum(parts) / len(parts), 3),
                      "correct": all(parts), "outcome": None, "type_ok": type_ok, "file_ok": file_ok if cont["file"] else None,
                      "expected": {"type": cont["type"], "file": cont["file"]}, "got": {"type": pa, "file": pf}})
        answer = json.dumps(step) if step else ""
        kp = ([re.escape(posixpath.basename(cont["file"]))] if cont["file"] else []) + [dict(_ACTION_WORDS)[cont["type"]]
                                                                                       if cont["type"] in dict(_ACTION_WORDS) else re.escape(cont["type"])]
        judge = [{"id": "next_step_judge", "measure": "continuation",
                  "question": "What is the single next action to continue the task from where the session stopped?",
                  "reference": f"The agent's real next action was a `{cont['type']}` step"
                               + (f" on {cont['file']}" if cont["file"] else "") + f": `{cont['command'][:1500]}`. "
                               f"The agent's own reasoning just before it: {cont['discussion'][:1500]}",
                  "key_points": kp, "answer": answer, "keyterm": keyterm(answer, kp)}]
        decs = obj.get("decisions") if isinstance(obj.get("decisions"), dict) else {}
        for d in key["decisions"]:
            ans = str(decs.get(d["id"]) or "").strip()
            judge.append({"id": d["id"], "measure": "decision", "question": d["question"], "reference": d["reference"],
                          "key_points": d["key_points"], "answer": ans, "keyterm": keyterm(ans, d["key_points"]),
                          "phase": d["phase"]})
        return items, judge, {"files_got": sorted(got), "next_step": step}


# ================================================================================================ quality gate

def merge_roles(msgs: list) -> list:
    """Claude wants alternating roles: concatenate the content of consecutive same-role messages."""
    out = []
    for m in msgs:
        if out and "role" in m and "type" not in m and out[-1].get("role") == m["role"] and "type" not in out[-1]:
            out[-1] = {**out[-1], "content": list(out[-1]["content"]) + list(m["content"])}
        else:
            out.append(m)
    return out


def phases_sha(case: dict) -> str:
    return hashlib.sha256(json.dumps(case["phases"], sort_keys=True).encode()).hexdigest()[:16]


def full_state(adapter, case: dict) -> list:
    state = []
    for p in case["phases"]:
        state = state + adapter.convert(p["turns"])
        if adapter.provider == "claude":
            state = merge_roles(state)
    return state


def gate(targets: list[str], case_ids: list[str] | None, force: bool = False, workers: int = 3) -> dict:
    from lab import providers

    cases = [c for c in load_cases() if not case_ids or c["case_id"] in case_ids]
    adapters = [providers.build_adapter(*t.split(":", 1)) for t in targets]

    def job(args):
        ad, case = args
        p = GATE / ad.label / f"{case['case_id']}.json"
        rec = _load_json(p, {}) if not force else {}
        state, pr = full_state(ad, case), ReplayProbes(case)
        sha = phases_sha(case)
        if rec.get("phases_sha") != sha:  # the prefix was rebuilt: stored replies answer another context
            rec = {}
        rec.update(case_id=case["case_id"], model=ad.label, phases_sha=sha)
        for name in pr.names:
            prompt = pr.prompt(name)
            for attempt in range(5):
                if (rec.get(name) or {}).get("ok") and rec[name].get("prompt") == prompt:
                    break
                if attempt:
                    time.sleep(30 * attempt)  # Foundry token-rate limits (429) outlast the SDK's own retries
                res = ad.ask(state, prompt)
                rec[name] = {"prompt": prompt, "text": res.text, "ok": res.ok, "error": res.error, "usage": res.usage}
        _save_json(p, rec)
        print(f"  gate {ad.label} {case['case_id']}: " + " ".join(f"{n}={'ok' if rec[n]['ok'] else 'FAILED'}" for n in pr.names), flush=True)

    jobs = [(ad, c) for c in cases for ad in adapters]
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(job, jobs))
    rep = gate_report([ad.label for ad in adapters], cases)
    for case in cases:  # the gate result travels with the case
        case = load_case(case["case_id"])
        case["key"]["gate"] = {"strategy": "full", "recall": rep["per_case"].get(case["case_id"], {}),
                               "missed_by_all": [m["id"] for m in rep["both_missed"] if m["case_id"] == case["case_id"]]}
        save_case(case)
    if not case_ids:  # a partial gate (--cases) must not replace the summary of all cases
        _save_json(REPLAY / "gate_summary.json", rep)
    return rep


def gate_report(labels: list[str], cases: list[dict] | None = None, verbose: bool = True) -> dict:
    cases = cases or load_cases()
    summary = {lab: {"correct": 0, "n": 0, "artifact": [], "cont": []} for lab in labels}
    both_missed, per_case = [], {}
    for case in cases:
        pr, ctx = ReplayProbes(case), {"transcript_text": tr.full_text(case), "summary": None}
        per = {}
        for lab in labels:
            p = GATE / lab / f"{case['case_id']}.json"
            if not p.exists():
                continue
            rec = json.loads(p.read_text())
            if rec.get("phases_sha") != phases_sha(case):
                continue  # replies to an older prefix of this case
            q = pr.score("quiz", rec["quiz"]["text"], ctx)
            s = pr.score("state", rec["state"]["text"], ctx)
            per[lab] = {it["id"]: it for it in q["items"]}
            per_case.setdefault(case["case_id"], {})[lab] = round(sum(it["correct"] for it in q["items"]) / max(1, len(q["items"])), 3)
            summary[lab]["correct"] += sum(it["correct"] for it in q["items"])
            summary[lab]["n"] += len(q["items"])
            summary[lab]["artifact"].append(s["items"][0]["score"])
            summary[lab]["cont"].append(s["items"][1]["score"])
        if len(per) == len(labels):
            turns = [t for p in case["phases"] for t in p["turns"]]
            for r in case["key"]["recall"]:
                if all(not per[lab][r["id"]]["correct"] for lab in labels):
                    out = _tool_text(turns[r["turn"]])
                    lines = [ln.strip() for ln in out.splitlines() if r.get("answer_raw", r["truth"]) in ln or r["truth"] in ln]
                    both_missed.append({"case_id": case["case_id"], "id": r["id"], "question": r["question"], "truth": r["truth"],
                                        "kind": r["kind"], "turn": r["turn"], "context": lines[:3],
                                        "answers": {lab: per[lab][r["id"]]["answer"] for lab in labels},
                                        "outcomes": {lab: per[lab][r["id"]]["outcome"] for lab in labels}})
    out = {lab: {"recall": round(v["correct"] / v["n"], 3) if v["n"] else None, "n": v["n"],
                 "artifact_f1": round(sum(v["artifact"]) / len(v["artifact"]), 3) if v["artifact"] else None,
                 "continuation": round(sum(v["cont"]) / len(v["cont"]), 3) if v["cont"] else None} for lab, v in summary.items()}
    if verbose:
        print(json.dumps(out, indent=1))
        print(f"\n{len(both_missed)} recall probes missed by every model:")
        for m in both_missed:
            print(f"- {m['case_id']} {m['id']} [{m['kind']}, turn {m['turn']}] {m['question']}\n    truth={m['truth']!r} "
                  + "  ".join(f"{k}={v!r}({m['outcomes'][k]})" for k, v in m["answers"].items())
                  + "".join(f"\n    | {ln[:200]}" for ln in m["context"]))
    return {"summary": out, "both_missed": both_missed, "per_case": per_case}


def drop(case_id: str, probe_id: str, reason: str) -> None:
    ed = _load_json(EDITS, {})
    ed.setdefault(case_id, {}).setdefault("drop", {})[probe_id] = reason
    _save_json(EDITS, ed)
    case = load_case(case_id)
    if any(r["id"] == probe_id for r in case["key"]["recall"] + case["key"]["decisions"]):
        reason_row = next(r for r in case["key"]["recall"] + case["key"]["decisions"] if r["id"] == probe_id)
        case["key"]["dropped"].append({**reason_row, "reason": reason})
        case["key"]["recall"] = [r for r in case["key"]["recall"] if r["id"] != probe_id]
        case["key"]["decisions"] = [d for d in case["key"]["decisions"] if d["id"] != probe_id]
        save_case(case)


# ================================================================================================ CLI

def _show(case: dict) -> None:
    k = case["key"]
    print(f"{case['case_id']}  {case['size_bucket']}  {case['chars']:,} chars (~{case['approx_tokens']:,} tokens)")
    print("provenance:", json.dumps(case["provenance"]))
    for p in case["phases"]:
        print(f"  {p['id']} {p['title']}: {len(p['turns'])} turns, {tr.phase_chars({'phases': [p]})[p['id']]:,} chars")
    print("files edited:", k["files_edited"], " read only:", k["files_read_only"][:8])
    c = k["continuation"]
    print(f"next action: [{c['type']}] {c['file']}  $ {c['command'][:160]!r}")
    print(f"recall ({len(k['recall'])}, rejected {len(k['rejected'])}, dropped {len(k['dropped'])}):")
    for r in k["recall"]:
        print(f"  {r['id']} {r['phase']} [{r['kind']}] {r['question']}  -> {r['truth']!r}  stale={r['stale']} decoys={r['decoys']}")
    for d in k["decisions"]:
        print(f"  {d['id']} {d['phase']} {d['question']}\n     ref: {d['reference']}\n     kp: {d['key_points']}")


def main(argv: list[str] | None = None) -> None:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
    ap = argparse.ArgumentParser(prog="lab.replay", description="layer B: real SWE-agent sessions replayed")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--max-pages", type=int, default=801)
    f.add_argument("--workers", type=int, default=2)
    b = sub.add_parser("build")
    b.add_argument("--force", action="store_true")
    b.add_argument("--no-gen", action="store_true")
    sub.add_parser("list")
    s = sub.add_parser("show")
    s.add_argument("case_id")
    g = sub.add_parser("gate")
    g.add_argument("--targets", nargs="+", default=["claude:claude-haiku-5-5", "openai:gpt-6-luna"])
    g.add_argument("--cases", nargs="*")
    g.add_argument("--force", action="store_true")
    g.add_argument("--report", action="store_true", help="only score the stored replies")
    d = sub.add_parser("drop")
    d.add_argument("case_id")
    d.add_argument("probe_id")
    d.add_argument("reason")
    a = ap.parse_args(argv)
    if a.cmd == "fetch":
        sel = fetch(a.max_pages, a.workers)
        for c in sel["cases"] + sel["spares"]:
            print(f"{c['row_idx']:>6} {c['instance_id']:<45} {c['model_name']:<22} {c['prefix_chars']:>8,} {c['next_action']} {c['noise']}")
        print(f"{len(sel['cases'])} cases + {len(sel['spares'])} spares, {len({c['repo'] for c in sel['cases']})} repos")
    elif a.cmd == "build":
        for c in sorted(build(a.force, not a.no_gen), key=lambda c: c["case_id"]):
            k = c["key"]
            print(f"{c['case_id']:<48} {c['size_bucket']:<6} {c['chars']:>8,}  recall {len(k['recall'])}  decisions {len(k['decisions'])}  "
                  f"edited {len(k['files_edited'])}  next {k['continuation']['type']}")
    elif a.cmd == "list":
        for c in load_cases():
            k = c["key"]
            print(f"{c['case_id']:<48} {c['size_bucket']:<6} {c['chars']:>8,} ~{c['approx_tokens']:>7,} tok  recall {len(k['recall'])}  "
                  f"decisions {len(k['decisions'])}  {c['provenance']['model_name']}")
    elif a.cmd == "show":
        _show(load_case(a.case_id))
    elif a.cmd == "gate":
        if a.report:
            gate_report([_label_of(t) for t in a.targets], [c for c in load_cases() if not a.cases or c["case_id"] in a.cases])
        else:
            gate(a.targets, a.cases, a.force)
    elif a.cmd == "drop":
        drop(a.case_id, a.probe_id, a.reason)


def _label_of(target: str) -> str:
    from lab import providers

    return providers.build_adapter(*target.split(":", 1)).label


if __name__ == "__main__":
    main(sys.argv[1:])
