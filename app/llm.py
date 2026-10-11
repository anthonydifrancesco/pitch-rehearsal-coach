"""Small shared helpers for talking to Claude."""
from __future__ import annotations

import contextvars
import json
import os
import re
import threading
from pathlib import Path

# ---- usage tally: every call adds its token counts to <session>/usage/<name>.json (name = rehearsal id or "preread")
USAGE_ROOT: Path | None = None          # set by main to the sessions folder
_KEY = contextvars.ContextVar("usage_key", default=None)
_LOCK = threading.Lock()


def track(sid: str, name: str) -> None:
    """Attribute the AI calls made from here on (in this request) to a rehearsal."""
    _KEY.set((sid, name))


def usage_path(sid: str, name: str):
    return USAGE_ROOT / sid / "usage" / f"{name}.json" if USAGE_ROOT else None


def record(model: str, usage) -> None:
    key = _KEY.get()
    if not key or usage is None or USAGE_ROOT is None:
        return
    g = lambda k: int(getattr(usage, k, 0) or 0)
    try:
        f = usage_path(*key)
        with _LOCK:
            f.parent.mkdir(parents=True, exist_ok=True)
            d = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
            m = d.setdefault(model, {"calls": 0, "input": 0, "cache_read": 0, "cache_write": 0, "output": 0})
            m["calls"] += 1
            m["input"] += g("input_tokens"); m["cache_read"] += g("cache_read_input_tokens")
            m["cache_write"] += g("cache_creation_input_tokens"); m["output"] += g("output_tokens")
            f.write_text(json.dumps(d), encoding="utf-8")
    except Exception:
        pass   # a tally must never break a rehearsal


def _strip_ttl(content):
    if isinstance(content, list):
        for b in content:
            cc = isinstance(b, dict) and b.get("cache_control")
            if cc and "ttl" in cc:
                cc.pop("ttl")


def _stream(client, kw):
    """One streamed call. If the API objects to the 1-hour cache option, retry once with the standard cache."""
    try:
        with client.messages.stream(**kw) as stream:
            return stream.get_final_message()
    except Exception as e:
        if "ttl" not in str(e).lower() and "cache_control" not in str(e).lower():
            raise
        for m in kw["messages"]:
            _strip_ttl(m["content"])
        with client.messages.stream(**kw) as stream:
            return stream.get_final_message()


def get_client():
    import anthropic

    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError("No ANTHROPIC_API_KEY set. Copy .env.example to .env, paste your key, and restart.")
    return anthropic.Anthropic(api_key=key)


def ask_text(client, model, system, content, max_tokens, thinking=None):
    """One streamed call that returns (text, usage). `content` is a string or a list of content blocks."""
    kw = dict(model=model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": content}])
    if thinking:
        kw["thinking"] = thinking
    msg = _stream(client, kw)
    record(model, getattr(msg, "usage", None))
    text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
    return text, getattr(msg, "usage", None), getattr(msg, "stop_reason", None)


def parse_json(text):
    """Find and parse the first JSON object in a model reply (tolerates code fences and chatter)."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("no JSON object in reply")
    return json.loads(t[a : b + 1])


def call_tool(client, model, system, tool, messages, max_tokens, thinking=None):
    """Offer a tool and ask for it (forced tool_choice is rejected by newer models). Retries once.
    Returns (tool_input, message)."""
    msgs = list(messages)
    msg = None
    for _ in range(2):
        kw = dict(model=model, max_tokens=max_tokens, system=system, tools=[tool], messages=msgs)
        if thinking:
            kw["thinking"] = thinking
        msg = _stream(client, kw)
        record(model, getattr(msg, "usage", None))
        if getattr(msg, "stop_reason", None) == "max_tokens":
            raise RuntimeError("The answer was cut off (too long). Try a shorter session.")
        raw = next((b.input for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
        if raw is not None:
            return raw, msg
        msgs = msgs + [
            {"role": "assistant", "content": [b for b in msg.content if getattr(b, "type", "") == "text"]
             or [{"type": "text", "text": "(no tool call)"}]},
            {"role": "user", "content": f"Deliver the result by calling the {tool['name']} tool now."},
        ]
    raise RuntimeError("The model did not return a result. Try again.")
