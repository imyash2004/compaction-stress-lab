"""Provider adapters: Claude (OpenRouter by default, or Anthropic / Foundry) and OpenAI models on Microsoft Foundry.

Each adapter converts neutral turns to its wire format, runs native compaction, and answers probe prompts.
Calls are sequential and every response is returned raw so run.py can store it.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

from lab import scenario as sc

ON_DEMAND_BETA = "compact-2026-09-04"
THRESHOLD_BETA = "compact-2026-01-12"
THRESHOLD_MIN_TOKENS = 50_000
# Sent on requests that carry an earlier threshold-compaction block: the API accepts such a block only alongside a
# compact_20260112 edit, and OpenRouter only attaches the beta when the body uses the feature. Never fires in practice.
CARRY_EDIT = {"type": "compact_20260112", "trigger": {"type": "input_tokens", "value": 900_000}}
CHECKPOINT_TEXT = "Checkpoint: the session continues after this point."

# USD per 1M tokens: (input, cached input, output). Azure Global Standard retail prices (eastus2, Oct 2026).
OPENAI_PRICES = {
    "gpt-6.1-sol": (2.00, 0.10, 10.00),
    "gpt-6-sol": (2.00, 0.20, 10.00),
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-5.6-sol": (4.00, 0.40, 20.00),
    "gpt-5.6-terra": (2.00, 0.20, 12.00),
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
    "gpt-5.4": (2.50, 0.25, 15.00),
}
OPENAI_LONG_CONTEXT_TOKENS = 272_000
# Above any compacted state (a few k tokens) and below every transcript, so it fires exactly once per request.
# At the 1,000 minimum the server compacted twice in one request (state + reply crossed it again).
OPENAI_THRESHOLD = 20_000  # above this, input x2 and output x1.5 for the whole request
# Claude list prices (input, output); cache writes 1.25x input, cache reads 0.1x input (verify on OpenRouter).
CLAUDE_PRICES = {"opus-5.5": (4.00, 20.00), "opus-5-5": (4.00, 20.00), "sonnet-5.5": (2.00, 10.00), "sonnet-5-5": (2.00, 10.00),
                 "haiku-5.5": (0.10, 0.50), "haiku-5-5": (0.10, 0.50), "fable-5-1": (10.00, 50.00), "fable-5.1": (10.00, 50.00),
                 "opus-5": (5.00, 25.00), "sonnet-5": (2.00, 10.00)}


@dataclass
class CallResult:
    ok: bool
    text: str = ""
    state: list | None = None          # new context state (compaction only)
    summary: str | None = None          # readable compaction summary (Claude only)
    usage: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    latency_s: float = 0.0
    stop: str = ""                      # stop_reason / status
    error: str = ""
    raw: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)


def _claude_tools(tools: list[dict]) -> list[dict]:
    return [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in tools]


def _openai_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "name": t["name"], "description": t["description"], "parameters": t["input_schema"]} for t in tools]


def _parse_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw or "{}")
        return val if isinstance(val, dict) else {"_raw": raw}
    except json.JSONDecodeError:
        return {"_raw": raw}


def _dump(obj) -> dict:
    try:
        return obj.model_dump(exclude_none=True)
    except AttributeError:
        return dict(obj)


# ================================================================================================ Claude

class ClaudeAdapter:
    provider = "claude"

    def __init__(self, model: str | None = None, compaction_mode: str | None = None):
        import anthropic

        self.backend = os.environ.get("CLAUDE_BACKEND", "openrouter").lower()
        timeout = float(os.environ.get("REQUEST_TIMEOUT_S", "900"))
        if self.backend == "openrouter":
            key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPEN_ROUTER_API_KEY")
            if not key:
                raise RuntimeError("OPENROUTER_API_KEY (or OPEN_ROUTER_API_KEY) is not set")
            # an ANTHROPIC_API_KEY in the environment would also be sent (as x-api-key) and confuse OpenRouter
            os.environ.pop("ANTHROPIC_API_KEY", None)
            self.client = anthropic.Anthropic(base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api"),
                                              auth_token=key, max_retries=int(os.environ.get("LAB_MAX_RETRIES", "10")), timeout=timeout)
            self.model = model or os.environ.get("CLAUDE_MODEL", "anthropic/claude-opus-5.5")
            provider_pin = os.environ.get("OPENROUTER_PROVIDER", "anthropic")
            # pin first-party Anthropic (compaction support differs by provider) and switch off OpenRouter's own
            # middle-out "context-compression" plugin so the only context change is the one under test
            self.extra_body = {"provider": {"only": [provider_pin], "allow_fallbacks": False},
                               "plugins": [{"id": "context-compression", "enabled": False}]}
        elif self.backend == "foundry":
            self.client = anthropic.AnthropicFoundry(max_retries=int(os.environ.get("LAB_MAX_RETRIES", "10")), timeout=timeout)
            self.model = model or os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
            self.extra_body = {}
        else:
            self.client = anthropic.Anthropic(max_retries=int(os.environ.get("LAB_MAX_RETRIES", "10")), timeout=timeout)
            self.model = model or os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
            self.extra_body = {}
        mode = (compaction_mode or os.environ.get("CLAUDE_COMPACTION_MODE", "auto")).lower()
        if mode == "auto":
            checked = _checked_mode(self.label)  # None means `check` found no working mode
            mode = ("threshold" if self.backend == "openrouter" else "on_demand") if checked == "unchecked" else checked
        self.compaction_mode = mode
        self.tools = _claude_tools(sc.TOOLS)
        # Prompt caching changes cost and latency only, never the reply, so it is on by default.
        self.cache = os.environ.get("LAB_PROMPT_CACHE", "1") != "0"
        # set explicitly: defaults differ between Anthropic (medium) and OpenRouter's model metadata (high)
        effort = os.environ.get("LAB_CLAUDE_EFFORT", "medium")  # not CLAUDE_EFFORT: Claude Code exports that name
        self.output_config = {"effort": effort} if effort != "default" else None

    def set_tools(self, tools: list[dict]) -> None:
        """Neutral tool definitions ({name, description, input_schema}) used for every later call."""
        self.tools = _claude_tools(tools)

    @property
    def label(self) -> str:
        backend = "" if self.backend == "openrouter" else f"{self.backend}-"
        return "claude-" + backend + self.model.split("/")[-1].replace(".", "-")

    @property
    def run_label(self) -> str:
        """Identifies the full configuration in run ids, so different settings never share results."""
        effort = (self.output_config or {}).get("effort", "default")
        return f"{self.label}-{self.compaction_mode or 'nocompact'}-{effort}"

    def describe(self) -> dict:
        return {"provider": "claude", "backend": self.backend, "model": self.model, "compaction_mode": self.compaction_mode,
                "betas": [ON_DEMAND_BETA if self.compaction_mode == "on_demand" else THRESHOLD_BETA],
                "provider_pin": self.extra_body.get("provider"), "effort": self.output_config}

    # ------------------------------------------------------------------ format

    @staticmethod
    def convert(turns: list[dict]) -> list[dict]:
        msgs = []
        for t in turns:
            if t["role"] == "user":
                msgs.append({"role": "user", "content": [{"type": "text", "text": t["text"]}]})
            elif t["role"] == "assistant":
                content = [{"type": "text", "text": t["text"]}] if t["text"] else []
                content += [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]} for c in t["calls"]]
                msgs.append({"role": "assistant", "content": content})
            else:
                blocks = []
                for r in t["results"]:
                    b = {"type": "tool_result", "tool_use_id": r["id"], "content": r["output"]}
                    if r["is_error"]:
                        b["is_error"] = True
                    blocks.append(b)
                msgs.append({"role": "user", "content": blocks})
        return msgs

    @staticmethod
    def has_compaction(state: list[dict]) -> bool:
        return any(isinstance(b, dict) and b.get("type") == "compaction" for m in state for b in m["content"] if isinstance(m["content"], list))

    def _betas(self, state: list[dict]) -> list[str]:
        """The beta that matches the carried block. On-demand blocks are signed; threshold blocks are not, and the
        API applies on-demand validation (signature required) whenever the on-demand beta is present."""
        blocks = [b for m in state if isinstance(m.get("content"), list) for b in m["content"]
                  if isinstance(b, dict) and b.get("type") == "compaction"]
        if not blocks:
            return []
        return [ON_DEMAND_BETA] if any(b.get("signature") for b in blocks) else [THRESHOLD_BETA]

    def _headers(self, betas: list[str]) -> dict:
        if self.backend != "openrouter":
            return {}
        h = {"X-OpenRouter-Metadata": "enabled"}  # response then says which provider served it
        if betas:
            h["x-anthropic-beta"] = ",".join(betas)  # OpenRouter's documented pass-through header
        return h

    def _stream(self, stream: bool = True, **params):
        betas = params.pop("betas", [])
        kw = {"betas": betas} if betas else {}
        if self.extra_body:
            kw["extra_body"] = self.extra_body
        hdr = self._headers(betas)
        if hdr:
            kw["extra_headers"] = hdr
        if self.output_config:
            params["output_config"] = self.output_config
        t0 = time.time()
        if stream:
            with self.client.beta.messages.stream(model=self.model, tools=self.tools, **params, **kw) as s:
                msg = s.get_final_message()
        else:
            msg = self.client.beta.messages.create(model=self.model, tools=self.tools, **params, **kw)
        extra = getattr(msg, "model_extra", None) or {}
        if msg.content is None or extra.get("error"):  # OpenRouter can send HTTP 200 with an error body
            raise RuntimeError(f"error body from gateway: {extra.get('error') or 'empty content'}")
        return msg, time.time() - t0

    @staticmethod
    def _served_by(msg) -> dict:
        extra = getattr(msg, "model_extra", None) or {}
        meta = extra.get("openrouter_metadata") or {}
        return {k: v for k, v in {"served_by": extra.get("provider"), "attempts": meta.get("attempt")}.items() if v is not None}

    # ------------------------------------------------------------------ calls

    def compact(self, state: list[dict], instructions: str | None, mode: str | None = None, threshold: int | None = None) -> CallResult:
        """mode: None = the adapter's checked mode; "threshold" forces threshold compaction (compact_threshold strategy).
        threshold: trigger in input tokens (default: the 50k minimum)."""
        try:
            if (mode or self.compaction_mode) == "on_demand":
                return self._compact_on_demand(state, instructions)
            return self._compact_threshold(state, instructions, threshold)
        except Exception as e:  # noqa: BLE001 - every API failure is a recorded outcome
            return CallResult(ok=False, error=f"{type(e).__name__}: {e}")

    def _finish_compaction(self, msg, latency: float, extra_meta: dict, prefix: list[dict] | None = None) -> CallResult:
        blocks = [b for b in msg.content if b.type == "compaction"]
        usage = _dump(msg.usage)
        res = CallResult(ok=False, usage=usage, cost_usd=self.cost(usage), latency_s=latency, stop=msg.stop_reason or "",
                         raw=_dump(msg), meta={**extra_meta, **self._served_by(msg)})
        if msg.stop_reason != "compaction" or not blocks or not blocks[0].content:
            res.error = f"no compaction summary (stop_reason={msg.stop_reason}, blocks={[b.type for b in msg.content]})"
            return res
        block = blocks[0].model_dump(exclude_none=True)
        res.ok = True
        res.summary = blocks[0].content
        res.state = (prefix or []) + [{"role": "assistant", "content": [block]}]
        res.meta["signature_present"] = bool(block.get("signature"))
        return res

    def _compact_on_demand(self, state: list[dict], instructions: str | None) -> CallResult:
        cfg = {"type": "summarize"}
        if instructions:
            cfg["instructions"] = instructions
        max_tokens = 16000
        attempts = []
        for _ in range(2):
            msg, dt = self._stream(messages=state, max_tokens=max_tokens, tool_choice={"type": "none"},
                                   compaction=cfg, betas=[ON_DEMAND_BETA])
            attempts.append((msg, dt))
            if msg.stop_reason != "max_tokens":
                break
            max_tokens = 64000  # long guided summaries plus thinking can exceed 32k on 200k-token inputs
        res = self._finish_compaction(msg, sum(d for _, d in attempts), {"mode": "on_demand", "attempts": len(attempts)})
        for m, _ in attempts[:-1]:  # a retry after max_tokens still billed the first attempt
            u = _dump(m.usage)
            res.cost_usd = round(res.cost_usd + self.cost(u), 6)
            res.meta.setdefault("earlier_attempt_usage", []).append(u)
        return res

    def _compact_threshold(self, state: list[dict], instructions: str | None, threshold: int | None = None) -> CallResult:
        trigger = max(THRESHOLD_MIN_TOKENS, threshold or THRESHOLD_MIN_TOKENS)
        edit = {"type": "compact_20260112", "trigger": {"type": "input_tokens", "value": trigger},
                "pause_after_compaction": True}
        if instructions:
            edit["instructions"] = instructions
        checkpoint = {"role": "user", "content": [{"type": "text", "text": CHECKPOINT_TEXT}]}
        # Not streamed: anthropic 1.13.0's stream accumulator keeps `signature_delta` only for thinking blocks, so a
        # streamed threshold-compaction block loses its signature and the next request is rejected.
        msg, dt = self._stream(stream=False, messages=state + [checkpoint], max_tokens=16000, tool_choice={"type": "none"},
                               context_management={"edits": [edit]}, betas=[THRESHOLD_BETA])
        # Everything before the block is ignored by the API; keep only the checkpoint so the history still opens
        # with a user turn, followed by the block.
        res = self._finish_compaction(msg, dt, {"mode": "threshold", "trigger": trigger}, prefix=[checkpoint])
        if not res.ok and msg.stop_reason == "end_turn":
            res.error = f"threshold compaction did not fire (input below {trigger} tokens?) " + res.error
        return res

    @staticmethod
    def append_user(state: list[dict], text: str) -> list[dict]:
        """state + a user text turn; merged into a trailing user turn (e.g. tool results) so roles keep alternating."""
        block = {"type": "text", "text": text}
        if state and state[-1]["role"] == "user" and isinstance(state[-1]["content"], list):
            return state[:-1] + [{"role": "user", "content": state[-1]["content"] + [block]}]
        return state + [{"role": "user", "content": [block]}]

    @staticmethod
    def tool_results(results: list[dict]) -> list[dict]:
        """[{id, output, is_error}] -> the user turn that answers the previous assistant turn's tool calls."""
        blocks = []
        for r in results:
            b = {"type": "tool_result", "tool_use_id": r["id"], "content": r["output"]}
            if r.get("is_error"):
                b["is_error"] = True
            blocks.append(b)
        return [{"role": "user", "content": blocks}]

    def step(self, state: list[dict], system: str | None = None, max_tokens: int = 16000) -> CallResult:
        """One agent turn with tools enabled. res.state = state + the assistant turn (blocks kept verbatim, so
        thinking signatures survive); res.meta["calls"] = [{id, name, args}]."""
        extra = {"system": system} if system else {}
        if self.cache:
            extra["cache_control"] = {"type": "ephemeral"}
        try:
            msg, dt = self._stream(messages=state, max_tokens=max_tokens, betas=self._betas(state), **extra)
        except Exception as e:  # noqa: BLE001
            return CallResult(ok=False, error=f"{type(e).__name__}: {e}")
        blocks = [b.model_dump(exclude_none=True) for b in msg.content]
        calls = [{"id": b.id, "name": b.name, "args": _parse_args(b.input)} for b in msg.content if b.type == "tool_use"]
        text = "".join(b.text for b in msg.content if b.type == "text")
        usage = _dump(msg.usage)
        ok = msg.stop_reason in ("end_turn", "tool_use", "stop_sequence")
        return CallResult(ok=ok, text=text, state=state + [{"role": "assistant", "content": blocks}], usage=usage,
                          cost_usd=self.cost(usage), latency_s=dt, stop=msg.stop_reason or "", raw={"id": msg.id},
                          meta={"calls": calls, **self._served_by(msg)}, error="" if ok else f"stop_reason={msg.stop_reason}")

    @staticmethod
    def _cache_state_end(state: list[dict]) -> list[dict]:
        """Breakpoint on the last block of the shared state (not on the probe prompt), so the other probes on the
        same state read it from cache. Compaction blocks are left unmarked."""
        if not state or not isinstance(state[-1].get("content"), list) or not state[-1]["content"]:
            return state
        last = state[-1]["content"][-1]
        if not isinstance(last, dict) or last.get("type") not in ("text", "tool_result", "tool_use"):
            return state
        return state[:-1] + [{**state[-1], "content": state[-1]["content"][:-1] + [{**last, "cache_control": {"type": "ephemeral"}}]}]

    def ask(self, state: list[dict], prompt: str, max_tokens: int = 32000) -> CallResult:
        messages = self.append_user(self._cache_state_end(state) if self.cache else state, prompt)
        extra = {}
        if self._betas(state) == [THRESHOLD_BETA]:
            # a threshold block is only accepted alongside a compact_20260112 edit (and OpenRouter needs it to add the beta)
            extra["context_management"] = {"edits": [CARRY_EDIT]}
        try:
            msg, dt = self._stream(messages=messages, max_tokens=max_tokens, tool_choice={"type": "none"},
                                   betas=self._betas(state), **extra)
        except Exception as e:  # noqa: BLE001
            return CallResult(ok=False, error=f"{type(e).__name__}: {e}")
        text = "".join(b.text for b in msg.content if b.type == "text")
        usage = _dump(msg.usage)
        return CallResult(ok=msg.stop_reason in ("end_turn", "stop_sequence"), text=text, usage=usage, cost_usd=self.cost(usage),
                          latency_s=dt, stop=msg.stop_reason or "", raw=_dump(msg), meta=self._served_by(msg),
                          error="" if msg.stop_reason in ("end_turn", "stop_sequence") else f"stop_reason={msg.stop_reason}")

    def tiny(self) -> CallResult:
        return self.ask([], "Reply with the single word: ready", max_tokens=2000)

    # ------------------------------------------------------------------ cost

    def _prices(self) -> tuple[float, float]:
        if os.environ.get("CLAUDE_PRICE_IN"):
            return float(os.environ["CLAUDE_PRICE_IN"]), float(os.environ["CLAUDE_PRICE_OUT"])
        for k, v in sorted(CLAUDE_PRICES.items(), key=lambda kv: -len(kv[0])):
            if k in self.model:
                return v
        return 5.00, 25.00

    def cost(self, usage: dict) -> float:
        # Always from token counts: a `cost` key on a message accumulated from a stream is not the final billed figure.
        p_in, p_out = self._prices()
        read_mult = 0.05 if "5.5" in self.model or "5-5" in self.model else 0.1
        parts = usage.get("iterations") or [usage]
        total = 0.0
        for u in parts:
            total += (u.get("input_tokens", 0) * p_in + u.get("cache_creation_input_tokens", 0) * p_in * 1.25
                      + u.get("cache_read_input_tokens", 0) * p_in * read_mult + u.get("output_tokens", 0) * p_out) / 1e6
        return round(total, 6)

    def estimate(self, input_tokens: int, output_tokens: int) -> float:
        p_in, p_out = self._prices()
        return (input_tokens * p_in + output_tokens * p_out) / 1e6


# ================================================================================================ OpenAI on Foundry

class OpenAIAdapter:
    provider = "openai"

    def __init__(self, deployment: str):
        from openai import OpenAI

        base = os.environ.get("AZURE_OPENAI_BASE_URL") or os.environ.get("AZURE_OPENAI_ENDPOINT")
        key = os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("AZURE_API_KEY")
        if not base or not key:
            raise RuntimeError("set AZURE_OPENAI_ENDPOINT (https://<resource>.openai.azure.com/openai/v1) and AZURE_API_KEY")
        base = base.rstrip("/")
        if not base.endswith("/openai/v1"):
            base += "/openai/v1"
        base += "/"
        self.client = OpenAI(base_url=base, api_key=key, max_retries=int(os.environ.get("LAB_MAX_RETRIES", "10")), timeout=float(os.environ.get("REQUEST_TIMEOUT_S", "900")))
        self.model = deployment
        self.tools = _openai_tools(sc.TOOLS)
        effort = os.environ.get("LAB_OPENAI_EFFORT", "medium")  # explicit, recorded in meta.json
        self.reasoning = {"effort": effort} if effort != "default" else None

    def set_tools(self, tools: list[dict]) -> None:
        self.tools = _openai_tools(tools)

    @staticmethod
    def append_user(state: list[dict], text: str) -> list[dict]:
        return state + [{"type": "message", "role": "user", "content": text}]

    @staticmethod
    def tool_results(results: list[dict]) -> list[dict]:
        return [{"type": "function_call_output", "call_id": r["id"], "output": r["output"]} for r in results]

    def step(self, state: list[dict], system: str | None = None, max_tokens: int = 16000) -> CallResult:
        """One agent turn with tools enabled, stateless (store=False): reasoning items come back encrypted and are
        carried in res.state so the next turn can use them. res.meta["calls"] = [{id, name, args}]."""
        kw = {"reasoning": self.reasoning} if self.reasoning else {}
        if system:
            kw["instructions"] = system
        t0 = time.time()
        try:
            raw = self.client.responses.with_raw_response.create(
                model=self.model, input=state, tools=self.tools, store=False, max_output_tokens=max_tokens,
                include=["reasoning.encrypted_content"], **kw)
            data = raw.http_response.json()
        except Exception as e:  # noqa: BLE001
            return CallResult(ok=False, error=f"{type(e).__name__}: {e}", latency_s=time.time() - t0)
        dt = time.time() - t0
        items = [self._sanitize(o)[0] for o in data.get("output", [])]
        items = [{k: v for k, v in i.items() if not (k == "id" and i.get("type") == "function_call")} for i in items]
        calls = [{"id": i["call_id"], "name": i["name"], "args": _parse_args(i.get("arguments"))}
                 for i in items if i.get("type") == "function_call"]
        text = "".join(p.get("text", "") for i in items if i.get("type") == "message"
                       for p in (i.get("content") or []) if isinstance(p, dict))
        usage = data.get("usage") or {}
        reason = (data.get("incomplete_details") or {}).get("reason")
        ok = data.get("status") == "completed"
        return CallResult(ok=ok, text=text, state=state + items, usage=usage, cost_usd=self.cost(usage), latency_s=dt,
                          stop=(data.get("status") or "") + (f":{reason}" if reason else ""), raw={"id": data.get("id")},
                          meta={"calls": calls}, error="" if ok else f"status={data.get('status')} reason={reason}")

    @property
    def label(self) -> str:
        return "openai-" + self.model.replace(".", "-")

    @property
    def run_label(self) -> str:
        return f"{self.label}-{(self.reasoning or {}).get('effort', 'default')}"

    def describe(self) -> dict:
        return {"provider": "openai", "backend": "foundry", "model": self.model, "compaction_mode": "responses_compact",
                "reasoning": self.reasoning}

    @staticmethod
    def convert(turns: list[dict]) -> list[dict]:
        items = []
        for t in turns:
            if t["role"] == "user":
                items.append({"type": "message", "role": "user", "content": t["text"]})
            elif t["role"] == "assistant":
                if t["text"]:
                    items.append({"type": "message", "role": "assistant", "content": t["text"]})
                items += [{"type": "function_call", "call_id": c["id"], "name": c["name"], "arguments": json.dumps(c["args"])}
                          for c in t["calls"]]
            else:
                items += [{"type": "function_call_output", "call_id": r["id"], "output": r["output"]} for r in t["results"]]
        return items

    @staticmethod
    def _sanitize(item: dict) -> tuple[dict, int]:
        """Pass compacted output back unchanged except what a stateless request can't accept.

        Drops server bookkeeping (ids of unstored items, status, created_by). Some models (seen with gpt-5.4) retain
        assistant messages whose content parts are typed `input_text`, which the same API rejects on input; those
        parts are re-typed `output_text` with the text untouched. Returns (item, number of re-typed parts).
        """
        item = {k: v for k, v in item.items() if k not in ("status", "created_by")}
        if item.get("type") in ("message", "compaction"):
            item.pop("id", None)
        fixed = 0
        if item.get("type") == "message" and item.get("role") == "assistant" and isinstance(item.get("content"), list):
            parts = []
            for part in item["content"]:
                if isinstance(part, dict) and part.get("type") == "input_text":
                    part = {**part, "type": "output_text", "annotations": part.get("annotations", [])}
                    fixed += 1
                parts.append(part)
            item["content"] = parts
        return item, fixed

    def compact(self, state: list[dict], instructions: str | None, mode: str | None = None, threshold: int | None = None) -> CallResult:
        if mode == "threshold":
            return self._compact_server_side(state, threshold or OPENAI_THRESHOLD)
        kw = {"instructions": instructions} if instructions else {}
        t0 = time.time()
        try:
            raw = self.client.responses.with_raw_response.compact(model=self.model, input=state, **kw)
            data = raw.http_response.json()  # the server's own JSON, not re-serialized SDK models
        except Exception as e:  # noqa: BLE001
            return CallResult(ok=False, error=f"{type(e).__name__}: {e}", latency_s=time.time() - t0)
        dt = time.time() - t0
        pairs = [self._sanitize(o) for o in data.get("output", [])]
        items = [i for i, _ in pairs]
        usage = data.get("usage") or {}
        comp = [i for i in items if i.get("type") == "compaction"]
        types = [i.get("type") + (f":{i['role']}" if i.get("role") else "") for i in items]
        res = CallResult(ok=bool(comp), state=items if comp else None, usage=usage, cost_usd=self.cost(usage), latency_s=dt,
                         stop="compacted" if comp else "no_compaction_item", raw={"id": data.get("id"), "usage": usage, "item_types": types},
                         meta={"item_types": types, "encrypted_chars": sum(len(i.get("encrypted_content", "")) for i in comp),
                               "retyped_parts": sum(n for _, n in pairs)})
        if not comp:
            res.error = "compact returned no compaction item"
        return res

    def _compact_server_side(self, state: list[dict], threshold: int) -> CallResult:
        """Threshold mode: a normal request with context_management; the server compacts when the input crosses
        the threshold, emits a compaction item and continues. The same "Checkpoint" user turn as Claude's
        threshold mode closes the phase; the new state starts at the compaction item (earlier items are dropped,
        as the compaction guide allows)."""
        checkpoint = {"type": "message", "role": "user", "content": CHECKPOINT_TEXT}
        kw = {"reasoning": self.reasoning} if self.reasoning else {}
        t0 = time.time()
        try:
            raw = self.client.responses.with_raw_response.create(
                model=self.model, input=state + [checkpoint], tools=self.tools, tool_choice="none", store=False,
                max_output_tokens=4000, context_management=[{"type": "compaction", "compact_threshold": threshold}], **kw)
            data = raw.http_response.json()
        except Exception as e:  # noqa: BLE001
            return CallResult(ok=False, error=f"{type(e).__name__}: {e}", latency_s=time.time() - t0)
        dt = time.time() - t0
        pairs = [self._sanitize(o) for o in data.get("output", [])]
        items = [i for i, _ in pairs]
        usage = data.get("usage") or {}
        comp_idx = [n for n, i in enumerate(items) if i.get("type") == "compaction"]
        idx = comp_idx[-1] if comp_idx else None  # keep from the most recent compaction item (compaction guide)
        types = [i.get("type") + (f":{i['role']}" if i.get("role") else "") for i in items]
        res = CallResult(ok=idx is not None, state=(items[idx:] if idx is not None else None), usage=usage, cost_usd=self.cost(usage),
                         latency_s=dt, stop=data.get("status", ""), raw={"id": data.get("id"), "usage": usage, "item_types": types},
                         meta={"mode": "server_side", "threshold": threshold, "item_types": types, "compactions": len(comp_idx),
                               "encrypted_chars": sum(len(i.get("encrypted_content", "")) for i in items if i.get("type") == "compaction"),
                               "retyped_parts": sum(n for _, n in pairs)})
        if idx is None:
            res.error = f"server-side compaction did not fire (output items: {types})"
        return res

    def ask(self, state: list[dict], prompt: str, max_tokens: int = 32000) -> CallResult:
        kw = {"reasoning": self.reasoning} if self.reasoning else {}
        t0 = time.time()
        try:
            resp = self.client.responses.create(
                model=self.model, input=state + [{"type": "message", "role": "user", "content": prompt}],
                tools=self.tools, tool_choice="none", store=False, max_output_tokens=max_tokens, **kw)
        except Exception as e:  # noqa: BLE001
            return CallResult(ok=False, error=f"{type(e).__name__}: {e}", latency_s=time.time() - t0)
        dt = time.time() - t0
        usage = _dump(resp.usage) if resp.usage else {}
        reason = getattr(resp.incomplete_details, "reason", None) if resp.incomplete_details else None
        ok = resp.status == "completed"
        return CallResult(ok=ok, text=resp.output_text or "", usage=usage, cost_usd=self.cost(usage), latency_s=dt,
                          stop=resp.status + (f":{reason}" if reason else ""), raw=_dump(resp),
                          error="" if ok else f"status={resp.status} reason={reason}")

    def tiny(self) -> CallResult:
        return self.ask([], "Reply with the single word: ready", max_tokens=2000)

    def _prices(self) -> tuple[float, float, float]:
        if self.model in OPENAI_PRICES:
            return OPENAI_PRICES[self.model]
        if os.environ.get("OPENAI_PRICE_IN"):
            p_in = float(os.environ["OPENAI_PRICE_IN"])
            return p_in, p_in / 10, float(os.environ["OPENAI_PRICE_OUT"])
        return 4.00, 0.40, 20.00

    def cost(self, usage: dict) -> float:
        p_in, p_cached, p_out = self._prices()
        tin = usage.get("input_tokens", 0)
        cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0) or 0
        tout = usage.get("output_tokens", 0)
        mult_in, mult_out = (2.0, 1.5) if tin > OPENAI_LONG_CONTEXT_TOKENS else (1.0, 1.0)
        return round(((tin - cached) * p_in * mult_in + cached * p_cached * mult_in + tout * p_out * mult_out) / 1e6, 6)

    def estimate(self, input_tokens: int, output_tokens: int) -> float:
        p_in, _, p_out = self._prices()
        mult_in, mult_out = (2.0, 1.5) if input_tokens > OPENAI_LONG_CONTEXT_TOKENS else (1.0, 1.0)
        return (input_tokens * p_in * mult_in + output_tokens * p_out * mult_out) / 1e6


# ================================================================================================ helpers

def _checked_mode(label: str) -> str | None:
    """Compaction mode proven by `lab.run check`: a mode name, None (checked, nothing works) or "unchecked"."""
    from lab.transcript import ROOT

    p = ROOT / "results" / "check.json"
    if not p.exists():
        return "unchecked"
    try:
        entry = json.loads(p.read_text()).get(label)
    except json.JSONDecodeError:
        return "unchecked"
    if not entry or "compaction_mode" not in entry:
        return "unchecked"
    return entry["compaction_mode"]


def openai_deployments() -> list[str]:
    raw = os.environ.get("OPENAI_DEPLOYMENTS", "gpt-6.1-sol,gpt-6-sol,gpt-6-luna,gpt-5.6-sol,gpt-5.6-terra,gpt-5.6-luna,gpt-5.4")
    return [d.strip() for d in raw.split(",") if d.strip()]


def claude_models() -> list[str]:
    raw = os.environ.get("CLAUDE_MODELS") or os.environ.get("CLAUDE_MODEL") or ""
    return [m.strip() for m in raw.split(",") if m.strip()] or [None]


def make_targets(spec: list[str]) -> list:
    """`spec` items: 'claude' (all CLAUDE_MODELS), 'claude:<model>', 'openai' (all OPENAI_DEPLOYMENTS),
    'openai:<deployment>', or 'all'."""
    out = []
    for s in spec:
        if s in ("claude", "all"):
            out += [("claude", m) for m in claude_models()]
        if s.startswith("claude:"):
            out.append(("claude", s.split(":", 1)[1]))
        if s in ("openai", "all"):
            out += [("openai", d) for d in openai_deployments()]
        if s.startswith("openai:"):
            out.append(("openai", s.split(":", 1)[1]))
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def build_adapter(kind: str, model: str | None):
    return ClaudeAdapter(model=model) if kind == "claude" else OpenAIAdapter(model)
