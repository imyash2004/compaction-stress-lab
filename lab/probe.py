"""Probe prompts sent after the context strategy has been applied, and parsing of the replies."""

from __future__ import annotations

import json
import re

from lab import scenario as sc


def quiz_prompt(facts: list | None = None) -> str:
    lines = [
        "Before we continue, I need a precise status check. Answer every question below using only what you know from this session.",
        "Do not run any tools. Do not guess: if you do not know an answer, write \"UNKNOWN\".",
        "Give only the value for each answer, with no explanation.",
        "Reply with exactly one JSON object that maps each question id to a string answer, and nothing else.",
        "",
    ]
    lines += [f"{f.id}: {f.question}" for f in (sc.FACTS if facts is None else facts)]
    return "\n".join(lines)


def lab_state_prompt() -> str:
    lines = [
        "Before we continue, I need a precise status check of this session. Answer using only what you know from this session.",
        "Do not run any tools. Do not guess: if you do not know something, write \"UNKNOWN\" for it.",
        "Reply with exactly one JSON object of this shape, and nothing else:",
        '{"files_edited": ["<repository-relative path>", ...], "decisions": {"<question id>": "<one-sentence reason>", ...}}',
        "",
        "files_edited: every repository file that was edited (modified, not just read) during the session, as repository-relative paths.",
        "decisions: for each question id below, the concrete reason established in this session, in one sentence.",
        "",
    ]
    lines += [f"{d.id}: {d.question}" for d in sc.DECISIONS]
    return "\n".join(lines)


def action_prompt() -> str:
    return (
        "We are about to roll the fix out to production. Write the final production rollout plan as one JSON object, "
        "using exactly this shape:\n\n" + sc.ACTION_TEMPLATE + "\n\n"
        "Fill in every value from this session. `commands` must list the exact shell commands to run, in order, "
        "including the production database migration, the deploy, the feature-flag change, and how to roll back if needed. "
        "Do not run any tools. Reply with the JSON object only."
    )


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_json(text: str) -> dict | None:
    """Best-effort JSON object extraction: whole text, fenced blocks, outermost braces, then every '{' position."""
    if not text:
        return None
    candidates = [text.strip()]
    candidates += [m.group(1).strip() for m in _FENCE.finditer(text)]
    if "{" in text and "}" in text:
        candidates.append(text[text.index("{"): text.rindex("}") + 1])
    for c in candidates:
        try:
            obj = json.loads(c)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    best = None
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = dec.raw_decode(text, i)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict) and (best is None or len(obj) >= len(best)):
            best = obj
    return best
