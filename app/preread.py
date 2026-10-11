"""Pre-read: turn an RFP + deck into a structured slide-and-brief map (one Claude call)."""
from __future__ import annotations

import json
import os
import re
from typing import Any

from .extract import deck_as_prompt_text

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_RFP_CHARS = 120_000

SYSTEM_PROMPT = """You are a pitch analyst preparing a rehearsal for a new-business pitch. \
You read the client's RFP/brief and the presenter's deck BEFORE the rehearsal and produce a structured map \
that a live AI client panel will use to ask realistic questions and to score the pitch.

Rules:
- Use only the documents provided. Never invent facts, figures or requirements. Cite slide numbers.
- SLIDE PURPOSE is the job a slide does in the argument (e.g. "proves credibility", "answers the client's compliance requirement", "transition"), \
not a description of its content. KEY MESSAGE is the one sentence the audience should leave with.
- PACING per slide: "quick" (agenda, dividers, transitions, title, thank-you/contact), "normal", or "weighty" \
(carries the argument, evidence, differentiation, or answers a key requirement). Judge from content and speaker notes. \
Give a short reason.
- Speaker notes are presenter prompts. They are NOT shown to the audience, so facts that appear only in notes are not evidence the panel has seen. \
Use notes to infer intent, pacing and which presenter owns the slide (presenter_hint, e.g. a first name at the top of the notes; empty if none).
- REQUIREMENTS: extract every distinct thing the RFP asks bidders to provide, do, comply with, or that the client signals matters \
(objectives, challenges, compliance, reporting, formats, timelines). Keep each atomic. Give each a short id (R1, R2...), a category, and priority \
(must = explicitly required, should = strongly signalled, nice = implied).
- COVERAGE: for each slide list the requirement ids it addresses. Rate evidence_strength: strong (specific, quantified, sourced or demonstrated), \
ok, thin (assertion, vague, no baseline/timeframe/source), none (no evidence offered).
- UNANSWERED: requirements no slide addresses, or addresses only in passing. Say which.
- INCONSISTENCIES: contradictions between slides, or between the deck and the RFP (numbers, dates, scope). Check arithmetic yourself and say what doesn't add up.
- LIKELY QUESTIONS: realistic, specific questions panel members would ask at that slide, each tagged with the persona type most likely to ask: \
finance, operations, skeptic, insights, channel_expert. Prefer questions that probe thin evidence, unanswered requirements and inconsistencies. \
Do not ask generic questions.
- IGNORED TOPICS: the user may list topics to leave out of scope for this rehearsal. Still list matching requirements with ignored=true, \
but do not count them as unanswered, thin evidence, inconsistencies, or questions.
- Unless slide images are attached, you can only see text. If a slide is mostly visual and you cannot see it, say what you cannot assess in evidence_note rather than guessing.
- Be concise: one or two sentences per field.
Deliver your answer ONLY by calling the submit_preread tool with the complete result. Do not write the answer as prose."""

PACING = ["quick", "normal", "weighty"]
STRENGTH = ["strong", "ok", "thin", "none"]
PERSONA_HINTS = ["finance", "operations", "skeptic", "insights", "channel_expert"]

TOOL = {
    "name": "submit_preread",
    "description": "Submit the structured pre-read of the RFP and deck.",
    "input_schema": {
        "type": "object",
        "required": ["deck_summary", "requirements", "slides", "unanswered_requirements", "inconsistencies"],
        "properties": {
            "deck_summary": {"type": "string", "description": "Two or three sentences: the deck's overall argument."},
            "requirements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "text", "category", "priority", "ignored"],
                    "properties": {
                        "id": {"type": "string"},
                        "text": {"type": "string"},
                        "category": {"type": "string"},
                        "priority": {"type": "string", "enum": ["must", "should", "nice"]},
                        "ignored": {"type": "boolean"},
                    },
                },
            },
            "slides": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "n", "purpose", "key_message", "pacing", "pacing_reason",
                        "covers", "evidence_strength", "evidence_note", "likely_questions",
                    ],
                    "properties": {
                        "n": {"type": "integer"},
                        "purpose": {"type": "string"},
                        "key_message": {"type": "string"},
                        "pacing": {"type": "string", "enum": PACING},
                        "pacing_reason": {"type": "string"},
                        "presenter_hint": {"type": "string"},
                        "covers": {"type": "array", "items": {"type": "string"}},
                        "evidence_strength": {"type": "string", "enum": STRENGTH},
                        "evidence_note": {"type": "string"},
                        "likely_questions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["question", "persona_hint"],
                                "properties": {
                                    "question": {"type": "string"},
                                    "persona_hint": {"type": "string", "enum": PERSONA_HINTS},
                                },
                            },
                        },
                    },
                },
            },
            "unanswered_requirements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["req_id", "note"],
                    "properties": {"req_id": {"type": "string"}, "note": {"type": "string"}},
                },
            },
            "inconsistencies": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["description", "slides"],
                    "properties": {
                        "description": {"type": "string"},
                        "slides": {"type": "array", "items": {"type": "integer"}},
                    },
                },
            },
        },
    },
}


def build_messages(rfp_text: str, slides: list[dict], settings: dict, visuals: list | None = None) -> list[dict]:
    rfp = rfp_text[:MAX_RFP_CHARS]
    ignore = (settings.get("ignore_topics") or "").strip()
    context_bits = [
        f"Pitch: {settings.get('title') or 'Untitled'}",
        f"Channel/industry: {settings.get('channel') or 'not specified'}",
        f"Difficulty: {settings.get('difficulty', 3)}/5 (1 gentle, 5 brutal)",
        f"Topics out of scope for this rehearsal: {ignore or 'none'}",
    ]
    guidance = (settings.get("guidance") or "").strip()
    if guidance:
        context_bits.append("Extra guidance from the presenter on how to assess (follow it, "
                            "but never invent facts): " + guidance[:2000])
    doc_block = (
        "<rfp>\n" + rfp + "\n</rfp>\n\n<deck slide_count=\"%d\">\n" % len(slides)
        + deck_as_prompt_text(slides) + "\n</deck>"
    )
    content: list = [
        # The big, reusable block is cached so re-runs and later live-panel calls are cheap.
        {"type": "text", "text": doc_block, "cache_control": {"type": "ephemeral"}},
    ]
    note = ""
    if visuals:
        content[0].pop("cache_control")
        for v in visuals:
            content.append({"type": "text", "text": f"Slide {v['n']} visual" + (f" ({v['k']})" if v.get("k", 1) > 1 else "") + ":"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": v["b64"]}})
        content[-1]["cache_control"] = {"type": "ephemeral"}
        note = ("\n\nImages of the slides are attached above, each labelled with its slide number. Treat what they show (charts, "
                "photos, screenshots, diagrams, product shots) as part of the slide's evidence: use it for the slide's purpose, evidence "
                "strength and evidence_note, and for questions a client would ask about the visual. Never invent detail you cannot see.")
    content.append({"type": "text", "text": "Rehearsal settings:\n" + "\n".join(context_bits)
                    + f"\n\nProduce the pre-read for all {len(slides)} slides." + note})
    return [{"role": "user", "content": content}]


def _load(v):
    """Models sometimes return a nested value as a JSON string instead of an object/array."""
    if isinstance(v, str):
        t = v.strip()
        if t[:1] in "[{":
            try:
                return json.loads(t)
            except ValueError:
                return None
    return v


def _dicts(v):
    v = _load(v)
    if isinstance(v, dict):  # a single object, or an {"1": {...}} style mapping
        v = list(v.values()) if v and all(isinstance(x, dict) for x in v.values()) else [v]
    out = []
    for x in v if isinstance(v, list) else []:
        x = _load(x)
        if isinstance(x, dict):
            out.append(x)
    return out


def _strs(v):
    v = _load(v)
    if isinstance(v, str):
        v = [p for p in re.split(r"[,;\s]+", v) if p]
    return [str(x).strip() for x in v] if isinstance(v, list) else []


def _ints(v):
    out = []
    for x in _strs(v):
        m = re.search(r"\d+", x)
        if m:
            out.append(int(m.group()))
    return out


def _text(v):
    return v if isinstance(v, str) else ("" if v is None else str(v))


def normalise(raw: dict[str, Any], slide_count: int) -> dict[str, Any]:
    """Make the model output safe to render: every slide present, enums valid, ids consistent."""
    raw = _load(raw)
    if not isinstance(raw, dict):
        raise RuntimeError("The model returned an unreadable pre-read. Try again.")
    reqs = []
    seen = set()
    for r in _dicts(raw.get("requirements")):
        rid = _text(r.get("id")).strip()
        if not rid or rid in seen:
            continue
        seen.add(rid)
        reqs.append({
            "id": rid,
            "text": _text(r.get("text")),
            "category": _text(r.get("category")),
            "priority": r.get("priority") if r.get("priority") in ("must", "should", "nice") else "should",
            "ignored": r.get("ignored") is True or _text(r.get("ignored")).lower() == "true",
        })
    by_n = {}
    for s_ in _dicts(raw.get("slides")):
        nums = _ints([s_.get("n")])
        if nums:
            by_n[nums[0]] = s_
    slides = []
    for n in range(1, slide_count + 1):
        s = by_n.get(n, {})
        pacing = s.get("pacing") if s.get("pacing") in PACING else "normal"
        strength = s.get("evidence_strength") if s.get("evidence_strength") in STRENGTH else "ok"
        qs = []
        for q in _dicts(s.get("likely_questions")):
            hint = q.get("persona_hint")
            qs.append({
                "question": _text(q.get("question")),
                "persona_hint": hint if hint in PERSONA_HINTS else "skeptic",
            })
        slides.append({
            "n": n,
            "purpose": _text(s.get("purpose")),
            "key_message": _text(s.get("key_message")),
            "pacing": pacing,
            "pacing_reason": _text(s.get("pacing_reason")),
            "presenter_hint": _text(s.get("presenter_hint")).strip(),
            "covers": [c for c in _strs(s.get("covers")) if c in seen],
            "evidence_strength": strength,
            "evidence_note": _text(s.get("evidence_note")),
            "likely_questions": qs,
        })
    return {
        "deck_summary": _text(raw.get("deck_summary")),
        "requirements": reqs,
        "slides": slides,
        "unanswered_requirements": [
            {"req_id": _text(u.get("req_id")), "note": _text(u.get("note"))}
            for u in _dicts(raw.get("unanswered_requirements")) if _text(u.get("req_id")) in seen
        ],
        "inconsistencies": [
            {"description": _text(i.get("description")), "slides": _ints(i.get("slides"))}
            for i in _dicts(raw.get("inconsistencies"))
        ],
        "missing_slides": [n for n in range(1, slide_count + 1) if n not in by_n],
    }


def run_preread(rfp_text: str, slides: list[dict], settings: dict, client=None, debug_path=None, visuals: list | None = None) -> dict:
    if client is None:
        import anthropic

        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise RuntimeError(
                "No ANTHROPIC_API_KEY set. Copy .env.example to .env, paste your key, and restart."
            )
        client = anthropic.Anthropic(api_key=key)
    model = os.environ.get("PREREAD_MODEL", DEFAULT_MODEL)
    messages = build_messages(rfp_text, slides, settings, visuals)
    raw = None
    msg = None
    for attempt in range(2):
        # Newer models reject a forced tool_choice, so the tool is offered and the prompt asks for it.
        with client.messages.stream(
            model=model,
            max_tokens=32000,
            system=SYSTEM_PROMPT,
            tools=[TOOL],
            messages=messages,
        ) as stream:
            msg = stream.get_final_message()
        if getattr(msg, "stop_reason", None) == "max_tokens":
            raise RuntimeError("The pre-read was cut off because the deck is very large. Try a slide range or split the deck.")
        raw = next((b.input for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
        if raw is not None:
            break
        # The model replied in prose instead of calling the tool: ask once more.
        messages = messages + [
            {"role": "assistant", "content": [b for b in msg.content if getattr(b, "type", "") in ("text",)] or [{"type": "text", "text": "(no tool call)"}]},
            {"role": "user", "content": "You must deliver the pre-read by calling the submit_preread tool with the complete result. Do that now."},
        ]
    if raw is None:
        raise RuntimeError("The model did not return a pre-read. Try again.")
    if debug_path is not None:  # keep the model's untouched answer so problems can be inspected
        try:
            debug_path.write_text(json.dumps(raw, indent=2, default=str), encoding="utf-8")
        except Exception:
            pass
    result = normalise(raw, len(slides))
    u = getattr(msg, "usage", None)
    result["meta"] = {
        "model": model,
        "input_tokens": getattr(u, "input_tokens", None),
        "output_tokens": getattr(u, "output_tokens", None),
        "cache_read_tokens": getattr(u, "cache_read_input_tokens", None),
        "cache_write_tokens": getattr(u, "cache_creation_input_tokens", None),
        "stop_reason": getattr(msg, "stop_reason", None),
    }
    return result
