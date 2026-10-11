"""Rehearsal engine: live panel decisions, end-of-pitch Q&A, timing and scoring."""
from __future__ import annotations

import os
import random
import time
from typing import Any

from .llm import ask_text, call_tool, parse_json
from .preread import PACING, PERSONA_HINTS, _dicts, _ints, _load, _strs, _text

LIVE_MODEL = os.environ.get("LIVE_MODEL", "claude-haiku-5-5")
SCORE_MODEL = os.environ.get("SCORE_MODEL", "claude-sonnet-5-5")

WARMUP_S = float(os.environ.get("REHEARSE_WARMUP_S", "40"))
READY_SCALE = float(os.environ.get("REHEARSE_READY_SCALE", "1"))   # tests shrink the per-slide wait
WEIGHTS = {"coverage": 0.35, "gut": 0.30, "questions": 0.20, "delivery": 0.15}


# ---------------------------------------------------------------- helpers
def _lines(v) -> list:
    """A list of prose strings (unlike _strs, never splits a sentence on spaces)."""
    v = _load(v)
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    return [_text(x).strip() for x in v if _text(x).strip()] if isinstance(v, list) else []


def mmss(t: float) -> str:
    t = max(0, int(t))
    return f"{t // 60:02d}:{t % 60:02d}"


def clamp(v: Any, lo: float, hi: float, default: float) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def slide_range(settings: dict, total: int):
    r = settings.get("range") or {}
    a = int(r.get("from") or 1)
    b = int(r.get("to") or total)
    return max(1, min(a, total)), max(1, min(b, total))


def scope(sess: dict, pr: dict) -> dict:
    """What is actually being rehearsed. With a partial slide range only the requirements and issues tied to those
    slides count; everything else belongs to slides that are not being rehearsed today."""
    total = len(sess["slides"])
    a, b = slide_range(sess["settings"], total)
    partial = (a, b) != (1, total)
    live = {r["id"] for r in pr["requirements"] if not r["ignored"]}
    if not partial:
        return {"a": a, "b": b, "total": total, "partial": False, "reqs": live,
                "gaps": {u["req_id"] for u in pr["unanswered_requirements"]}, "incons": list(pr["inconsistencies"])}
    covered = set()
    for sl in pr["slides"]:
        if a <= sl["n"] <= b:
            covered.update(sl["covers"])
    reqs = live & covered
    incons = [i for i in pr["inconsistencies"] if not i["slides"] or all(a <= n <= b for n in i["slides"])]
    return {"a": a, "b": b, "total": total, "partial": True, "reqs": reqs, "gaps": set(), "incons": incons}


def live_thinking(model: str):
    # Haiku 5.5 accepts thinking disabled (fast, cheap). Other models error on it, so omit there.
    return {"type": "disabled"} if "haiku" in model else None


def personas_by_id(settings: dict) -> dict:
    return {p["id"]: p for p in settings.get("personas", [])}


def script_lines(events: list, settings: dict, titles: dict) -> str:
    pers = personas_by_id(settings)
    out = []
    for e in sorted(events, key=lambda e: e.get("t", 0)):
        t = mmss(e.get("t", 0))
        k = e.get("type")
        if k == "speech":
            tag = "Q&A ANSWER" if e.get("phase") == "qa" else (e.get("speaker") or "Presenter")
            out.append(f"[{t}] (slide {e.get('slide')}) {tag}: {e.get('text', '')}")
        elif k == "panel":
            p = pers.get(e.get("persona_id")) or {"name": e.get("speaker_name") or "Client", "title": "client"}
            tgt = e.get("target")
            to = f" to {tgt}" if tgt and tgt != "room" else ""
            kind = {"qa": "end Q&A question", "reply": "reply to the presenter's clarifying question"}.get(
                e.get("kind"), "interjection")
            out.append(f"[{t}] PANEL - {p.get('name', '?')} ({p.get('title', '')}) {kind}{to}: {e.get('text', '')}")
        elif k == "slide":
            out.append(f"[{t}] -- slide {e.get('slide')}: {titles.get(e.get('slide'), '')} --")
    return "\n".join(out)


# ---------------------------------------------------------------- shared context (cached block)
def build_context(sess: dict, pr: dict) -> str:
    st = sess["settings"]
    ov = sess.get("pacing_overrides", {})
    titles = {s["n"]: s["title"] for s in sess["slides"]}
    total = len(sess["slides"])
    a, b = slide_range(st, total)
    lines = [
        f"PITCH: {st.get('title') or 'Untitled'} | channel: {st.get('channel')} | difficulty {st.get('difficulty', 3)}/5 "
        f"(1 gentle, 5 brutal) | out of scope: {st.get('ignore_topics') or 'none'}",
        f"Rehearsing slides {a}-{b} of {total}. Presenters: "
        + (", ".join(f"{p['name']} (slides {p.get('from') or '?'}-{p.get('to') or '?'})" for p in st.get("presenters", [])) or "single presenter"),
        "",
        "PANEL MEMBERS (id | name, title | how they behave | question appetite 0-10 | key decision-maker?):",
    ]
    for p in st.get("personas", []):
        lines.append(f"- {p['id']} | {p['name']}, {p['title']} | {p.get('style', '')} | {p.get('questions', 5)} | "
                     f"{'YES' if p.get('key_decision_maker') else 'no'}")
    sc = scope(sess, pr)
    if sc["partial"]:
        lines += ["", f"IMPORTANT: this session rehearses ONLY slides {a}-{b} of {total}. Slides outside that range do not exist for this "
                      "session: never ask about them, refer to them, or judge the presenter on them. Only the requirements listed below "
                      "are relevant today; the rest of the brief belongs to slides that are not being rehearsed."]
    lines += ["", "THE CLIENT'S REQUIREMENTS" + (" RELEVANT TO THE SLIDES BEING REHEARSED" if sc["partial"] else " (from the brief)") + ":"]
    for r in pr["requirements"]:
        if sc["partial"] and r["id"] not in sc["reqs"]:
            continue
        lines.append(f"- {r['id']} [{r['priority']}]{' (OUT OF SCOPE)' if r['ignored'] else ''} {r['text']}")
    lines += ["", "THE DECK (analysis from the pre-read):"]
    for s in pr["slides"]:
        if not (a <= s["n"] <= b):
            continue
        pace = ov.get(str(s["n"])) or s["pacing"]
        qs = " / ".join(q["question"] for q in s["likely_questions"][:3])
        lines.append(f"{s['n']}. {titles.get(s['n'], '')} | purpose: {s['purpose']} | key message: {s['key_message']} | "
                     f"pace: {pace} | evidence {s['evidence_strength']}: {s['evidence_note']}"
                     + (f" | likely questions: {qs}" if qs else ""))
    unans = "; ".join(f"{u['req_id']} ({u['note']})" for u in pr["unanswered_requirements"] if not sc["partial"] or u["req_id"] in sc["gaps"])
    if unans:
        lines += ["", "KNOWN GAPS: " + unans]
    if sc["incons"]:
        lines += ["KNOWN INCONSISTENCIES: " + "; ".join(i["description"] for i in sc["incons"])]
    g = (st.get("guidance") or "").strip()
    if g:
        lines += ["", "PRESENTER'S GUIDANCE ON HOW TO ASSESS: " + g[:2000]]
    return "\n".join(lines)


def _blocks(ctx: str, dynamic: str) -> list:
    return [{"type": "text", "text": ctx, "cache_control": {"type": "ephemeral"}}, {"type": "text", "text": dynamic}]


# ---------------------------------------------------------------- live interjections
LIVE_SYSTEM = """You are the client panel in a live rehearsal of a business pitch. You hear the presenter speak and decide, \
right now, whether one panel member would naturally speak up, and what they would say.

Rules:
- Interject only when a real stakeholder would: a claim with no evidence, something that contradicts the brief or another slide, \
a requirement being skipped, a risk, or a number that doesn't add up. Otherwise stay silent (interject=false). Silence is normal.
- Speak as that person, in one or two short spoken sentences (under 40 words). Plain conversational Australian English, no lists, no preamble.
- Let the presenter finish making their point first. Only ask about what they have actually SAID so far; never ask about something \
the slide shows that they have not yet got to, because they may be about to cover it. If they are mid-explanation, stay silent.
- Mostly ask about the slide on screen. Occasionally (about one in five) circle back to something from a few slides earlier.
- If the context says only some slides are being rehearsed, only ever ask about those slides and requirements.
- Choose the persona whose role and appetite fit. Weight by question appetite, avoid the same person twice in a row, and don't ignore key decision-makers.
- Never repeat or rephrase a question already asked. If the presenter has just deferred a question ("coming up later"), do not ask it again until that slide.
- Match tone to difficulty: gentle = curious and polite; brutal = blunt, sceptical, pushes back on vague answers.
- Out-of-scope topics must never be raised.
- If the presenter is a named person for the slide, you may address them by name; otherwise address the room.
Reply with ONLY a JSON object, no other text:
{"interject": true|false, "persona_id": "<id>", "text": "<what they say>", "target": "room"|"<presenter name>", "about_slide": <number>, "reason": "<few words>"}"""


def gate(level: int, asked_times: list, now: float):
    """Deterministic pacing limits so the slider controls frequency regardless of what the model feels like."""
    if level <= 0:
        return "off"
    if now < WARMUP_S:
        return "warmup"
    gap = max(15.0, 300.0 / level)
    if asked_times and now - max(asked_times) < gap:
        return "min_gap"
    if len([t for t in asked_times if now - t <= 600]) >= level:
        return "cap"
    return None


# A slide must have been on screen this long, with this many words spoken on it, before the panel may ask about it.
READY = {"quick": (15.0, 25), "normal": (30.0, 60), "weighty": (45.0, 90)}


def slide_ready(pace: str, dwell: float, words: int):
    need_s, need_w = READY.get(pace, READY["normal"])
    need_s, need_w = need_s * READY_SCALE, need_w * READY_SCALE
    return dwell >= need_s and words >= need_w


def live_turn(client, ctx: str, sess: dict, state: dict) -> dict:
    st = sess["settings"]
    level = int(state.get("interjection", st.get("interjection", 5)))
    now = float(state.get("elapsed", 0))
    asked = [a for a in state.get("asked", []) if a.get("kind") == "live"]
    why = gate(level, [float(a.get("t", 0)) for a in asked], now)
    if why:
        return {"interject": False, "gated": why}
    titles = {s["n"]: s["title"] for s in sess["slides"]}
    slide = int(state.get("slide", 1))
    pace = (sess.get("pacing_overrides", {}).get(str(slide))
            or next((s["pacing"] for s in sess["preread"]["slides"] if s["n"] == slide), "normal"))
    dwell = float(state.get("slide_dwell", 9999))
    swords = int(state.get("slide_words", 9999))
    if not slide_ready(pace, dwell, swords):
        return {"interject": False, "gated": "slide_not_ready"}
    last = asked[-1] if asked else None
    dyn = "\n".join([
        f"NOW: {mmss(now)} elapsed. Slide on screen: {slide} - {titles.get(slide, '')} (intended pace: {pace}). "
        f"The presenter has been on it {int(dwell)}s and has spoken {swords} words about it.",
        f"Interjection setting {level}/10. Questions asked so far: "
        + ("; ".join(f"[{mmss(a['t'])}] {a['persona_id']} on slide {a.get('slide')}: {a['text']}" for a in asked) or "none"),
        f"Last asked by: {last['persona_id'] if last else 'nobody'}.",
        "",
        "RECENT TRANSCRIPT:",
        script_lines(state.get("recent", []), st, titles) or "(nothing yet)",
        "",
        "Decide now. JSON only.",
    ])
    text, _, _ = ask_text(client, LIVE_MODEL, LIVE_SYSTEM, _blocks(ctx, dyn), 500, live_thinking(LIVE_MODEL))
    try:
        j = parse_json(text)
    except ValueError:
        return {"interject": False, "error": "unreadable reply"}
    pers = personas_by_id(st)
    pid = j.get("persona_id")
    if pid not in pers:
        pid = next((k for k, p in pers.items() if p["name"].lower() == _text(pid).lower()), None)
    said = _text(j.get("text")).strip()
    if not j.get("interject") or not pid or not said:
        return {"interject": False}
    words = said.split()
    if len(words) > 60:
        said = " ".join(words[:60])
    return {"interject": True, "persona_id": pid, "text": said, "target": _text(j.get("target")) or "room",
            "about_slide": int(_ints([j.get("about_slide")])[0]) if _ints([j.get("about_slide")]) else slide,
            "reason": _text(j.get("reason"))}


# ---------------------------------------------------------------- natural back-and-forth
REPLY_SYSTEM = """You are the client panel in a live pitch rehearsal. A panel member has just asked the presenter a question, and the \
presenter has started to respond. Decide whether what the presenter just said is a CLARIFYING QUESTION or request back to the panel \
(e.g. "when you say regional, do you mean state or national?", "are you asking about year one or the whole term?", "can you repeat that?") \
rather than an answer.

- If it is a clarification: reply as the panel member who asked (or a colleague, if it is clearly about their area), in character, \
one or two short spoken sentences (under 35 words), plain Australian English. Answer what was asked using the brief; if the client would not \
know, say so naturally and tell them to use their judgement or state their assumption. Do NOT answer the original question for them \
and do not hint at the 'right' answer. Stay in the persona's tone for the difficulty setting.
- If it is an answer, a statement, a filler like "good question", or anything else: clarifying=false.
Reply with ONLY a JSON object: {"clarifying": true|false, "persona_id": "<id>", "text": "<the spoken reply>"}"""


def reply_turn(client, ctx: str, sess: dict, body: dict) -> dict:
    st = sess["settings"]
    titles = {s["n"]: s["title"] for s in sess["slides"]}
    pers = personas_by_id(st)
    asker = body.get("persona_id")
    dyn = "\n".join([
        f"The question was asked by {asker} (" + (pers.get(asker, {}).get("name", "?")) + f"): {_text(body.get('question'))}",
        f"Slide on screen: {body.get('slide')}.",
        "",
        "RECENT TRANSCRIPT:",
        script_lines(body.get("recent", []), st, titles) or "(nothing)",
        "",
        f"THE PRESENTER JUST SAID: {_text(body.get('said'))}",
        "JSON only.",
    ])
    text, _, _ = ask_text(client, LIVE_MODEL, REPLY_SYSTEM, _blocks(ctx, dyn), 400, live_thinking(LIVE_MODEL))
    try:
        j = parse_json(text)
    except ValueError:
        return {"clarifying": False}
    said = _text(j.get("text")).strip()
    pid = j.get("persona_id")
    if pid not in pers:
        pid = next((k for k, p in pers.items() if p["name"].lower() == _text(pid).lower()), None) or asker
    if not j.get("clarifying") or not said or pid not in pers:
        return {"clarifying": False}
    return {"clarifying": True, "persona_id": pid, "text": " ".join(said.split()[:55])}


# ---------------------------------------------------------------- end-of-pitch Q&A
QA_SYSTEM = """You are the client panel at the end of a pitch rehearsal. Write the questions the panel would now ask.

Rules:
- Draw on: questions that were deferred or left unanswered during the pitch, thin evidence, unanswered requirements and inconsistencies in the context, \
and follow-ups to what the presenter actually said (use the transcript).
- Every key decision-maker asks at least one. Spread the rest by question appetite and role.
- Do not repeat questions already asked during the pitch. Make them different each run (use the variation token).
- Spoken style, one or two sentences, under 40 words, in character, tone matched to difficulty. Most important first.
- Never raise out-of-scope topics.
- If the context says only some slides are being rehearsed, ask ONLY about those slides and their requirements. Never mention others.
Reply with ONLY a JSON object: {"questions":[{"persona_id":"<id>","text":"<question>","about_slide":<number>,"why":"<few words>"}]}"""


def qa_count(settings: dict) -> int:
    total = sum(int(p.get("questions", 5)) for p in settings.get("personas", []))
    return int(max(3, min(10, round(total / 3))))


def qa_questions(client, ctx: str, sess: dict, events: list, qa_only: bool = False, n: int | None = None) -> list:
    st = sess["settings"]
    titles = {s["n"]: s["title"] for s in sess["slides"]}
    n = max(1, min(12, int(n))) if n else qa_count(st)
    a, b = slide_range(st, len(sess["slides"]))
    rng = f"Only ask about slides {a}-{b}: nothing outside that range was rehearsed. " if (a, b) != (1, len(sess["slides"])) else ""
    if qa_only:
        dyn = (f"Write {n} questions. Variation token: {random.randint(1000, 9999)}.\n\n{rng}"
               "This is a QUESTION-TIME-ONLY session: the presenter has NOT pitched. The panel has just seen the deck and now puts its questions: "
               "thin evidence, unanswered requirements, claims to be proven, risks and the likely questions from the deck analysis. "
               "Do not refer to anything the presenter said, because they have said nothing yet.\n\nJSON only.")
    else:
        dyn = (f"Write {n} questions. Variation token: {random.randint(1000, 9999)}.\n\n{rng}FULL TRANSCRIPT SO FAR:\n"
               + (script_lines(events, st, titles) or "(the presenter said very little)") + "\n\nJSON only.")
    text, _, _ = ask_text(client, SCORE_MODEL, QA_SYSTEM, _blocks(ctx, dyn), 4000, {"type": "between_tools"})
    j = parse_json(text)
    pers = personas_by_id(st)
    out = []
    for q in _dicts(j.get("questions")):
        pid = q.get("persona_id")
        if pid not in pers:
            pid = next((k for k, p in pers.items() if p["name"].lower() == _text(pid).lower()), None)
        said = _text(q.get("text")).strip()
        if pid and said:
            nums = _ints([q.get("about_slide")])
            if nums and not (a <= nums[0] <= b):
                continue                                  # a question about a slide that was not rehearsed
            out.append({"persona_id": pid, "text": said, "about_slide": nums[0] if nums else None,
                        "why": _text(q.get("why"))})
    if not out:
        raise RuntimeError("The panel did not produce any questions. Try again.")
    return out[:n]


# ---------------------------------------------------------------- timing
def timing_rows(sess: dict, pr: dict, events: list) -> list:
    st = sess["settings"]
    titles = {s["n"]: s["title"] for s in sess["slides"]}
    total = len(sess["slides"])
    a, b = slide_range(st, total)
    ov = sess.get("pacing_overrides", {})
    end_t = max([e.get("t", 0) for e in events] + [0])
    slides_ev = sorted([e for e in events if e.get("type") == "slide"], key=lambda e: e["t"])
    if not slides_ev or slides_ev[0]["t"] > 0.5:
        slides_ev.insert(0, {"t": 0, "slide": a})
    panel_time = [(e["t"], e["t"] + float(e.get("dur") or 0)) for e in events if e.get("type") == "panel" and e.get("phase", "pitch") != "qa" and e.get("kind") != "qa"]
    secs = {n: 0.0 for n in range(1, total + 1)}
    visits = {n: 0 for n in secs}
    pitch_end = max([e.get("t", 0) for e in events if e.get("phase") != "qa" and e.get("kind") != "qa"] + [0])
    for i, e in enumerate(slides_ev):
        n = int(e["slide"])
        t0 = e["t"]
        t1 = slides_ev[i + 1]["t"] if i + 1 < len(slides_ev) else pitch_end
        t1 = max(t1, t0)
        dwell = t1 - t0
        for p0, p1 in panel_time:  # don't count time the panel was talking
            dwell -= max(0.0, min(t1, p1) - max(t0, p0))
        if n in secs:
            secs[n] += max(0.0, dwell)
            visits[n] += 1
    words = {n: 0 for n in secs}
    for e in events:
        if e.get("type") == "speech" and e.get("phase") != "qa" and e.get("slide") in words:
            words[e["slide"]] += len(_text(e.get("text")).split())
    pace_by = {s["n"]: (ov.get(str(s["n"])) or s["pacing"]) for s in pr["slides"]}
    rows = []
    for n in range(a, b + 1):
        s = secs[n]
        rows.append({"n": n, "title": titles.get(n, ""), "pacing": pace_by.get(n, "normal"),
                     "seconds": int(round(s)), "words": words[n], "visits": visits[n],
                     "wpm": int(round(words[n] / (s / 60.0))) if s >= 8 else None})
    return rows


# ---------------------------------------------------------------- scoring
SCORE_SYSTEM = """You are an experienced new-business pitch coach and a panel of client stakeholders, assessing a rehearsal of a pitch.
You have: the client's requirements, the deck analysis, a timestamped transcript (presenter speech, panel interjections, end Q&A) \
and per-slide timing.

Rules:
- Base every judgement on the transcript and the requirements. Quote or closely paraphrase what was actually said as evidence. \
If something was not said aloud it does not count as covered, even if it is on a slide, but you may note that it is on the slide.
- BRIEF COVERAGE: one item per in-scope requirement (skip out-of-scope ones): met = yes / partial / no. Score 0-100 = (yes + 0.5*partial) / count.
- GUT FEEL: separate 0-100 score on whether the solution felt compelling and addressed the client's real problem. Say why.
- QUESTION HANDLING: rate every live interjection and end-Q&A question 0-100 on how well it was answered (directly, with evidence, concisely). \
A deliberate, credible deferral ("coming up on slide 12") is acceptable if the point was then actually covered. Unanswered = low.
- SLIDE DELIVERY: judge each slide against its intended pace. A quick slide is NOT marked down for brevity. Only weighty slides are flagged for \
under-delivery. Verdicts: clear, ok, rushed, waffled, skipped (skipped only if the slide was in range and never discussed). Rating 0-100.
- WIN LIKELIHOOD: the panel's own independent read as a percentage, with reasons. Be honest, not flattering.
- TOP ACTIONS: three to five specific changes to the deck or delivery that would most improve the pitch.
- PRESENTERS: if more than one presenter spoke, give each a short breakdown; otherwise one entry.
- Out-of-scope topics are never counted against the presenter.
- If the context says only some slides were rehearsed, judge ONLY those slides and the requirements listed for them. Requirements and slides outside \
the range must not appear in your answer and never count against the presenter.
Call the submit_score tool."""

SCORE_TOOL = {
    "name": "submit_score",
    "description": "Submit the rehearsal assessment.",
    "input_schema": {
        "type": "object",
        "required": ["brief_coverage", "gut_feel", "question_handling", "slides", "win_likelihood", "top_actions"],
        "properties": {
            "brief_coverage": {"type": "object", "required": ["score", "items"], "properties": {
                "score": {"type": "number"}, "summary": {"type": "string"},
                "items": {"type": "array", "items": {"type": "object", "required": ["req_id", "met", "evidence", "note"], "properties": {
                    "req_id": {"type": "string"}, "met": {"type": "string", "enum": ["yes", "partial", "no"]},
                    "evidence": {"type": "string"}, "note": {"type": "string"}}}}}},
            "gut_feel": {"type": "object", "required": ["score", "rationale"], "properties": {
                "score": {"type": "number"}, "rationale": {"type": "string"}}},
            "question_handling": {"type": "object", "required": ["score", "items"], "properties": {
                "score": {"type": "number"},
                "items": {"type": "array", "items": {"type": "object", "required": ["asker", "question", "answer_summary", "rating", "note"], "properties": {
                    "asker": {"type": "string"}, "question": {"type": "string"}, "answer_summary": {"type": "string"},
                    "kind": {"type": "string", "enum": ["live", "qa"]}, "rating": {"type": "number"}, "note": {"type": "string"}}}}}},
            "slides": {"type": "array", "items": {"type": "object", "required": ["n", "verdict", "rating", "note"], "properties": {
                "n": {"type": "integer"}, "verdict": {"type": "string", "enum": ["clear", "ok", "rushed", "waffled", "skipped"]},
                "rating": {"type": "number"}, "note": {"type": "string"}}}},
            "win_likelihood": {"type": "object", "required": ["panel_read_pct", "panel_rationale"], "properties": {
                "panel_read_pct": {"type": "number"}, "panel_rationale": {"type": "string"}}},
            "top_actions": {"type": "array", "items": {"type": "string"}},
            "presenters": {"type": "array", "items": {"type": "object", "required": ["name"], "properties": {
                "name": {"type": "string"}, "slides": {"type": "string"},
                "strengths": {"type": "array", "items": {"type": "string"}},
                "improve": {"type": "array", "items": {"type": "string"}}}}},
        },
    },
}

VERDICTS = ["clear", "ok", "rushed", "waffled", "skipped"]


def normalise_score(raw: Any, rows: list, sess: dict, pr: dict, minutes: float, timed: bool = True, qa_only: bool = False) -> dict:
    raw = _load(raw)
    if not isinstance(raw, dict):
        raise RuntimeError("The assessment was unreadable. Try again.")
    in_scope = scope(sess, pr)["reqs"]
    bc = _load(raw.get("brief_coverage")) or {}
    items = []
    for i in _dicts(bc.get("items")):
        rid = _text(i.get("req_id")).strip()
        if rid in in_scope:
            met = i.get("met") if i.get("met") in ("yes", "partial", "no") else "no"
            items.append({"req_id": rid, "met": met, "evidence": _text(i.get("evidence")), "note": _text(i.get("note"))})
    seen = {i["req_id"] for i in items}
    for rid in sorted(in_scope - seen):  # anything the model skipped counts as not met, visibly
        items.append({"req_id": rid, "met": "no", "evidence": "", "note": "Not assessed or not mentioned."})
    if items:  # recompute from the items so the headline always matches the checklist
        cov = 100.0 * sum({"yes": 1, "partial": 0.5, "no": 0}[i["met"]] for i in items) / len(items)
    else:
        cov = clamp(bc.get("score"), 0, 100, 0)
    gut = _load(raw.get("gut_feel")) or {}
    qh = _load(raw.get("question_handling")) or {}
    qitems = [{"asker": _text(i.get("asker")), "question": _text(i.get("question")),
               "answer_summary": _text(i.get("answer_summary")), "kind": i.get("kind") if i.get("kind") in ("live", "qa") else "live",
               "rating": int(clamp(i.get("rating"), 0, 100, 0)), "note": _text(i.get("note"))}
              for i in _dicts(qh.get("items"))]
    q_score = round(sum(i["rating"] for i in qitems) / len(qitems)) if qitems else int(clamp(qh.get("score"), 0, 100, 0))
    by_n = {}
    for s in _dicts(raw.get("slides")):
        nums = _ints([s.get("n")])
        if nums:
            by_n[nums[0]] = s
    slides = []
    for r in rows:
        s = by_n.get(r["n"], {})
        verdict = s.get("verdict") if s.get("verdict") in VERDICTS else ("skipped" if r["seconds"] == 0 and r["words"] == 0 else "ok")
        rating = int(clamp(s.get("rating"), 0, 100, 50))
        slides.append({**r, "verdict": verdict, "rating": rating, "note": _text(s.get("note"))})
    # quick slides that were skipped/short don't drag the delivery score down
    scored = [s["rating"] for s in slides if not (s["pacing"] == "quick" and s["verdict"] in ("skipped", "rushed"))]
    delivery = round(sum(scored) / len(scored)) if scored else 0
    if not timed:                                   # no timestamps: no delivery score, no time columns
        for s in slides:
            s["seconds"], s["wpm"] = None, None
    cov_i, gut_i = int(round(cov)), int(round(clamp(gut.get("score"), 0, 100, 0)))
    if qa_only:                                     # no pitch was given: only questions and gut feel can be judged
        items, slides, delivery, timed = [], [], 0, False
        calc = round(0.6 * q_score + 0.4 * gut_i)
        weights = {"questions": 0.6, "gut": 0.4}
    elif timed:
        calc = round(WEIGHTS["coverage"] * cov_i + WEIGHTS["gut"] * gut_i + WEIGHTS["questions"] * q_score + WEIGHTS["delivery"] * delivery)
        weights = WEIGHTS
    else:
        weights = {k: round(v / (1 - WEIGHTS["delivery"]), 3) for k, v in WEIGHTS.items() if k != "delivery"}
        calc = round(weights["coverage"] * cov_i + weights["gut"] * gut_i + weights["questions"] * q_score)
    wl = _load(raw.get("win_likelihood")) or {}
    pres = [{"name": _text(p.get("name")), "slides": _text(p.get("slides")), "strengths": _lines(p.get("strengths")),
             "improve": _lines(p.get("improve"))} for p in _dicts(raw.get("presenters"))]
    return {
        "brief_coverage": {"score": None if qa_only else cov_i, "summary": "" if qa_only else _text(bc.get("summary")), "items": items},
        "gut_feel": {"score": gut_i, "rationale": _text(gut.get("rationale"))},
        "question_handling": {"score": q_score, "items": qitems},
        "delivery_score": delivery if timed else None,
        "slides": slides,
        "total": round((q_score + gut_i) / 2) if qa_only else round((cov_i + gut_i) / 2),
        "win_likelihood": {"panel_read_pct": int(clamp(wl.get("panel_read_pct"), 0, 100, 0)),
                           "panel_rationale": _text(wl.get("panel_rationale")), "calculated_pct": int(calc)},
        "top_actions": _lines(raw.get("top_actions")),
        "presenters": pres,
        "meta": {"minutes": round(minutes, 1) if timed else None, "weights": weights, "timed": timed, "qa_only": qa_only},
    }


def score(client, ctx: str, sess: dict, pr: dict, events: list, debug_path=None, note: str = "", timed: bool = True, qa_only: bool = False) -> dict:
    st = sess["settings"]
    titles = {s["n"]: s["title"] for s in sess["slides"]}
    rows = timing_rows(sess, pr, events)
    if not timed:
        for r in rows:
            r["seconds"], r["wpm"] = 0, None
    end_t = max([e.get("t", 0) for e in events] + [0])
    if timed:
        table = "\n".join(
            f"slide {r['n']} ({r['title'][:40]}): intended {r['pacing']}, {r['seconds']}s on screen, {r['words']} words spoken"
            + (f", {r['wpm']} wpm" if r["wpm"] else "") + f", visits {r['visits']}" for r in rows)
        head = "TIMING PER SLIDE (panel speaking time excluded; guide only, judge by what was said):\n"
    else:
        table = "\n".join(f"slide {r['n']} ({r['title'][:40]}): {r['words']} words by the presenters" for r in rows)
        head = "WORDS SPOKEN PER SLIDE (there is no usable timing; judge content only):\n"
    if qa_only:
        note = ("This was a QUESTION-TIME-ONLY session: the presenter did not pitch; the transcript is the panel's questions and the presenter's answers. "
                "Judge only QUESTION HANDLING and GUT FEEL (do these answers reassure this panel?), plus win likelihood and top actions. "
                "For brief_coverage return score 0 and no items; for slides return an empty array.\n\n" + note)
    dyn = ((note + "\n\n" if note else "") + head + table
           + "\n\nFULL TRANSCRIPT:\n" + (script_lines(events, st, titles) or "(no speech recorded)")
           + "\n\nAssess now by calling submit_score.")
    raw, _ = call_tool(client, SCORE_MODEL, SCORE_SYSTEM, SCORE_TOOL,
                       [{"role": "user", "content": _blocks(ctx, dyn)}], 32000)
    if debug_path is not None:
        try:
            import json
            debug_path.write_text(json.dumps(raw, indent=2, default=str), encoding="utf-8")
        except Exception:
            pass
    return normalise_score(raw, rows, sess, pr, end_t / 60.0, timed, qa_only)
