"""Small shared helpers for talking to Claude."""
from __future__ import annotations

import json
import os
import re


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
    with client.messages.stream(**kw) as stream:
        msg = stream.get_final_message()
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
        with client.messages.stream(**kw) as stream:
            msg = stream.get_final_message()
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
