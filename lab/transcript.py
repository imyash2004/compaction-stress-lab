"""Builds the neutral (provider-independent) transcript from the scenario, and helpers to inspect it.

Neutral turn shapes:
    {"role": "user", "text": str}
    {"role": "assistant", "text": str, "calls": [{"id", "name", "args"}]}     # calls may be empty
    {"role": "tool", "results": [{"id", "output", "is_error"}]}
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

from lab import noise as nz
from lab import scenario as sc

ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPT_DIR = ROOT / "transcripts"
CHARS_PER_TOKEN = 3.3  # conservative for logs full of hex, timestamps and numbers
MASK_PLACEHOLDER = "[old tool output removed to save context]"


class Session:
    """Small builder that keeps the conversation valid: strict alternation, every call answered."""

    def __init__(self, rng: random.Random, level: dict):
        self.rng = rng
        self.level = level
        self.phases: list[dict] = []
        self._n = 0
        self._note = ""

    def phase(self, pid: str, title: str) -> None:
        if self._note:
            raise ValueError("pending note at phase boundary")
        self.phases.append({"id": pid, "title": title, "turns": []})

    @property
    def turns(self) -> list[dict]:
        return self.phases[-1]["turns"]

    def user(self, text: str) -> None:
        if self.turns and not (self.turns[-1]["role"] == "assistant" and not self.turns[-1]["calls"]):
            raise ValueError("a user message must follow a plain assistant message")
        self.turns.append({"role": "user", "text": text})

    def _take_note(self, text: str) -> str:
        note, self._note = self._note, ""
        return f"{note} {text}".strip() if note else text

    def step(self, text: str, calls: list[tuple]) -> None:
        """Assistant message with tool calls, followed by the tool results."""
        tc, tr = [], []
        for call in calls:
            name, args, output = call[0], call[1], call[2]
            is_error = bool(call[3]) if len(call) > 3 else False
            self._n += 1
            cid = f"call_{self._n:03d}"
            tc.append({"id": cid, "name": name, "args": args})
            tr.append({"id": cid, "output": output, "is_error": is_error})
        self.turns.append({"role": "assistant", "text": self._take_note(text), "calls": tc})
        self.turns.append({"role": "tool", "results": tr})

    def say_note(self, text: str) -> None:
        """Assistant remark; merged into the next assistant message to keep turns alternating."""
        self._note = f"{self._note} {text}".strip()

    def say(self, text: str) -> None:
        """Plain assistant message (ends a phase or precedes a user message)."""
        self.turns.append({"role": "assistant", "text": self._take_note(text), "calls": []})

    def detours(self, n: int) -> None:
        for _ in range(n):
            text, name, args, output = nz.detour(self.rng, self.level)
            self.step(text, [(name, args, output)])


def build(noise: str, seed: int = 7) -> dict:
    level = sc.LEVELS[noise]
    rng = random.Random(f"{sc.SCENARIO_ID}:{noise}:{seed}")
    s = Session(rng, level)
    sc.build_story(s, rng, level)
    t = {
        "scenario": sc.SCENARIO_ID,
        "noise": noise,
        "seed": seed,
        "tools": sc.TOOLS,
        "phases": s.phases,
    }
    t["chars"] = len(full_text(t))
    t["approx_tokens"] = round(t["chars"] / CHARS_PER_TOKEN)
    t["needles"] = locate_needles(t)
    return t


def path_for(noise: str, seed: int) -> Path:
    return TRANSCRIPT_DIR / f"{sc.SCENARIO_ID}_{noise}_s{seed}.json"


def load_or_build(noise: str, seed: int = 7) -> dict:
    p = path_for(noise, seed)
    if p.exists():
        return json.loads(p.read_text())
    t = build(noise, seed)
    save(t)
    return t


def save(t: dict) -> Path:
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    p = path_for(t["noise"], t["seed"])
    p.write_text(json.dumps(t, indent=1))
    return p


# ------------------------------------------------------------------------------------------------ inspection

def segments(t: dict) -> list[dict]:
    """Every piece of text in the transcript, tagged with where it lives."""
    out = []
    for phase in t["phases"]:
        for i, turn in enumerate(phase["turns"]):
            if turn["role"] == "user":
                out.append({"phase": phase["id"], "turn": i, "where": "user", "text": turn["text"]})
            elif turn["role"] == "assistant":
                if turn["text"]:
                    out.append({"phase": phase["id"], "turn": i, "where": "assistant", "text": turn["text"]})
                for c in turn["calls"]:
                    out.append({"phase": phase["id"], "turn": i, "where": "tool_call", "text": "\n".join(str(v) for v in c["args"].values())})
            else:
                for r in turn["results"]:
                    out.append({"phase": phase["id"], "turn": i, "where": "tool_output", "text": r["output"]})
    return out


def full_text(t: dict) -> str:
    return "\n".join(s["text"] for s in segments(t))


def locate_needles(t: dict) -> list[dict]:
    """Where each fact's truth is planted: phase, segment type, and position inside that output."""
    segs = segments(t)
    out = []
    for f in sc.FACTS:
        if not f.locator:
            out.append({"fact": f.id, "phase": f.phase, "where": "multiple", "position": "n/a"})
            continue
        rx = re.compile(f.locator, re.MULTILINE)
        hit = None
        for s in segs:
            m = rx.search(s["text"])
            if m:
                hit = (s, m)
                break
        if not hit:
            out.append({"fact": f.id, "phase": f.phase, "where": "MISSING", "position": "n/a"})
            continue
        s, m = hit
        frac = m.start() / max(1, len(s["text"]))
        pos = "head" if frac < 0.33 else "middle" if frac < 0.67 else "tail"
        if len(s["text"]) < 1500:
            pos = "short"
        out.append({"fact": f.id, "phase": s["phase"], "where": s["where"], "position": pos,
                    "output_chars": len(s["text"]), "offset": m.start()})
    return out


def phase_chars(t: dict) -> dict[str, int]:
    return {p["id"]: len("\n".join(s["text"] for s in segments({"phases": [p]}))) for p in t["phases"]}


def validate(t: dict) -> list[str]:
    """Structural problems that would make a provider reject the transcript or bias the experiment."""
    problems = []
    for p in t["phases"]:
        turns = p["turns"]
        if not turns or turns[0]["role"] != "user":
            problems.append(f"{p['id']}: must start with a user message")
        if turns[-1]["role"] != "assistant" or turns[-1]["calls"]:
            problems.append(f"{p['id']}: must end with a plain assistant message (no open tool call)")
        for a, b in zip(turns, turns[1:]):
            if a["role"] == b["role"]:
                problems.append(f"{p['id']}: two consecutive {a['role']} turns")
            if a["role"] == "assistant" and a["calls"]:
                if b["role"] != "tool" or [r["id"] for r in b["results"]] != [c["id"] for c in a["calls"]]:
                    problems.append(f"{p['id']}: tool calls not answered in order")
            if a["role"] == "tool" and b["role"] == "user":
                problems.append(f"{p['id']}: user message directly after tool results")
    for n in t.get("needles") or locate_needles(t):
        if n["where"] == "MISSING":
            problems.append(f"needle not found: {n['fact']}")
    users = " ".join(s["text"] for s in segments(t) if s["where"] == "user")
    for f in sc.FACTS:
        if f.salience == "user":
            continue
        if f.kind in ("int",):
            continue  # small integers legitimately appear in prose
        for v in [f.truth, *f.alts]:
            if v and re.search(rf"(?<![\w-]){re.escape(v)}(?![\w-])", users):
                problems.append(f"fact {f.id} leaks into a user message")
    return problems


# ------------------------------------------------------------------------------------------------ strategies on neutral turns

def mask_old_tool_outputs(turns: list[dict], keep_last: int = 3) -> list[dict]:
    """Observation masking: keep every tool call, blank all but the last `keep_last` tool outputs."""
    total = sum(len(t["results"]) for t in turns if t["role"] == "tool")
    cut = total - keep_last
    seen = 0
    out = []
    for t in turns:
        if t["role"] != "tool":
            out.append(t)
            continue
        res = []
        for r in t["results"]:
            seen += 1
            res.append({**r, "output": MASK_PLACEHOLDER} if seen <= cut else r)
        out.append({**t, "results": res})
    return out


def all_turns(t: dict) -> list[dict]:
    return [turn for p in t["phases"] for turn in p["turns"]]
