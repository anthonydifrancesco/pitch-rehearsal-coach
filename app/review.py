"""Written review of a pitch that has already happened: requirements vs deck vs what was said, plus the client's questions."""
from __future__ import annotations

import json
from typing import Any

from .llm import call_tool
from .preread import _dicts, _ints, _load, _text
from .rehearse import SCORE_MODEL, _blocks, _lines, mmss

REVIEW_SYSTEM = """You are a senior new-business adviser writing a post-pitch review of a REAL client pitch that has already happened.
You have: the client's requirements and the analysis of the pitch deck (above), and the transcript of the meeting, in which lines marked \
PRESENTER are our team and lines marked CLIENT are the client's people (who said what is known).

Write an evidence-based review. Do not score or grade; assess.

REQUIREMENTS: one entry per in-scope requirement (skip out-of-scope ones). Scope items phrased as questions the client wanted answered are \
requirements too. For each:
- deck: how well the DECK supports it. strength = strong | partial | thin | none, the slide numbers that carry it, and a one-line note on what the slides actually say or lack.
- conversation: how well the CONVERSATION delivered it. status = delivered | partial | not_discussed. Give the evidence: a short quote or close \
paraphrase of what was said and who said it. If it was not said aloud it is not_discussed, even if a slide covers it. Credit it if the client \
raised it and the presenters answered it.
- relevance: "pitch" if a client would reasonably expect this to be addressed in the pitch meeting itself, or "contract" if it is operational detail \
that is normally settled in the contract, onboarding or follow-up (e.g. uniform and PPE, toolkit contents, spare-parts lists, checklists to be supplied by the client). \
Judge by what a sensible pitch for this brief would cover, not by what happened.
- recommendation: one concrete sentence on what to change in the deck or the follow-up (or "None needed").

THEMES: group the in-scope requirements into 4 to 8 themes a busy reader can scan (e.g. store coverage, in-store tasks, reporting and technology, \
commercial and timing, onboarding and compliance). Every requirement id goes in exactly one theme. For each theme give a name, the req_ids, and one or two \
sentences on how the pitch did on that theme as a whole (what landed, what was missing).

CLIENT QUESTIONS: list every question, concern, objection, request or notable reaction from the client, in the order they happened. For each: \
who, what was asked (close to their words), kind = question | concern | request | reaction, the slide or topic it relates to, the requirement id it \
relates to (or ""), whether it was answered = yes | partly | no | deferred, a summary of the answer given, quality = strong | adequate | weak | n/a \
(direct, specific, evidenced, concise), a note, and any follow_up still owed to the client. Include a question even if it was answered well.

COMMITMENTS: promises made or follow-ups owed to the client during the meeting (e.g. "send case studies"), with who and by when if said.

SUMMARY: three to five sentences on how well the pitch met the brief overall, honest not flattering. biggest_gaps: up to five. \
next_steps: three to six specific things to do now (deck changes, follow-up email content, things to clarify).
Do not comment on timing, pace or delivery speed unless the transcript clearly has timestamps. Mishearings in the transcript are not the presenters' fault.
Call the submit_review tool."""

REVIEW_TOOL = {
    "name": "submit_review",
    "description": "Submit the post-pitch review.",
    "input_schema": {
        "type": "object",
        "required": ["summary", "requirements", "themes", "client_questions"],
        "properties": {
            "summary": {"type": "string"},
            "biggest_gaps": {"type": "array", "items": {"type": "string"}},
            "next_steps": {"type": "array", "items": {"type": "string"}},
            "requirements": {"type": "array", "items": {"type": "object", "required": ["req_id", "deck", "conversation"], "properties": {
                "req_id": {"type": "string"},
                "deck": {"type": "object", "properties": {
                    "strength": {"type": "string", "enum": ["strong", "partial", "thin", "none"]},
                    "slides": {"type": "array", "items": {"type": "integer"}}, "note": {"type": "string"}}},
                "conversation": {"type": "object", "properties": {
                    "status": {"type": "string", "enum": ["delivered", "partial", "not_discussed"]},
                    "evidence": {"type": "string"}, "speaker": {"type": "string"}, "note": {"type": "string"}}},
                "relevance": {"type": "string", "enum": ["pitch", "contract"]},
                "recommendation": {"type": "string"}}}},
            "themes": {"type": "array", "items": {"type": "object", "required": ["name", "req_ids", "summary"], "properties": {
                "name": {"type": "string"}, "req_ids": {"type": "array", "items": {"type": "string"}}, "summary": {"type": "string"}}}},
            "client_questions": {"type": "array", "items": {"type": "object", "required": ["asker", "question", "answered"], "properties": {
                "asker": {"type": "string"}, "question": {"type": "string"},
                "kind": {"type": "string", "enum": ["question", "concern", "request", "reaction"]},
                "slide": {"type": "integer"}, "req_id": {"type": "string"},
                "answered": {"type": "string", "enum": ["yes", "partly", "no", "deferred"]},
                "answer_summary": {"type": "string"},
                "quality": {"type": "string", "enum": ["strong", "adequate", "weak", "n/a"]},
                "note": {"type": "string"}, "follow_up": {"type": "string"}}}},
            "commitments": {"type": "array", "items": {"type": "object", "properties": {
                "what": {"type": "string"}, "who": {"type": "string"}, "due": {"type": "string"}}}},
        },
    },
}

STRENGTHS = ("strong", "partial", "thin", "none")
STATUSES = ("delivered", "partial", "not_discussed")


def review_script(events: list, timed: bool, titles: dict) -> str:
    out = []
    for e in sorted(events, key=lambda e: e.get("t", 0)):
        k = e.get("type")
        tag = f"[{mmss(e.get('t', 0))}] " if timed else ""
        if k == "slide":
            out.append(f"{tag}-- slide {e.get('slide')}: {titles.get(e.get('slide'), '')} --")
        elif k == "speech":
            out.append(f"{tag}PRESENTER ({e.get('speaker') or 'presenter'}; slide {e.get('slide')}{', Q&A' if e.get('phase') == 'qa' else ''}): {e.get('text', '')}")
        elif k == "panel":
            out.append(f"{tag}CLIENT ({e.get('speaker_name') or 'client'}{'; Q&A' if e.get('phase') == 'qa' else ''}): {e.get('text', '')}")
    return "\n".join(out)


def verdict(deck_strength: str, conv_status: str) -> str:
    """covered = the deck supports it (at least partly) AND the conversation delivered it.
    partly = both sides touch it but one is incomplete. deck_only / conversation_only = only one side carries it. gap = neither."""
    deck_ok = deck_strength in ("strong", "partial")
    conv_ok = conv_status in ("delivered", "partial")
    if not deck_ok and not conv_ok:
        return "gap"
    if deck_ok and not conv_ok:
        return "deck_only"
    if conv_ok and not deck_ok:
        return "conversation_only"
    return "covered" if conv_status == "delivered" else "partly"


def _slide_list(v, total):
    v = _load(v)
    nums = _ints(v if isinstance(v, list) else [v])
    return sorted({n for n in nums if 1 <= n <= total})


def normalise_review(raw: Any, sess: dict, pr: dict) -> dict:
    raw = _load(raw)
    if not isinstance(raw, dict):
        raise RuntimeError("The review was unreadable. Try again.")
    total = len(sess["slides"])
    reqs_by_id = {r["id"]: r for r in pr["requirements"]}
    in_scope = [r["id"] for r in pr["requirements"] if not r["ignored"]]
    seen = {}
    for i in _dicts(_load(raw.get("requirements"))):
        rid = _text(i.get("req_id")).strip()
        if rid not in in_scope or rid in seen:
            continue
        d = _load(i.get("deck"))
        c = _load(i.get("conversation"))
        d = d if isinstance(d, dict) else {}
        c = c if isinstance(c, dict) else {}
        ds = d.get("strength") if d.get("strength") in STRENGTHS else "none"
        cs = c.get("status") if c.get("status") in STATUSES else "not_discussed"
        seen[rid] = {"req_id": rid, "text": reqs_by_id[rid]["text"], "priority": reqs_by_id[rid].get("priority", ""),
                     "category": reqs_by_id[rid].get("category", ""),
                     "deck": {"strength": ds, "slides": _slide_list(d.get("slides"), total), "note": _text(d.get("note"))},
                     "conversation": {"status": cs, "evidence": _text(c.get("evidence")), "speaker": _text(c.get("speaker")), "note": _text(c.get("note"))},
                     "verdict": verdict(ds, cs), "relevance": "contract" if i.get("relevance") == "contract" else "pitch",
                     "recommendation": _text(i.get("recommendation"))}
    reqs = []
    for rid in in_scope:  # anything the model skipped is shown as a visible gap, not silently dropped
        reqs.append(seen.get(rid) or {
            "req_id": rid, "text": reqs_by_id[rid]["text"], "priority": reqs_by_id[rid].get("priority", ""),
            "category": reqs_by_id[rid].get("category", ""),
            "deck": {"strength": "none", "slides": [], "note": "Not assessed."},
            "conversation": {"status": "not_discussed", "evidence": "", "speaker": "", "note": "Not assessed."},
            "verdict": "gap", "relevance": "pitch", "recommendation": ""})
    qs = []
    for q in _dicts(_load(raw.get("client_questions"))):
        said = _text(q.get("question")).strip()
        if not said:
            continue
        sl = _ints([q.get("slide")])
        rid = _text(q.get("req_id")).strip()
        qs.append({"asker": _text(q.get("asker")) or "Client", "question": said,
                   "kind": q.get("kind") if q.get("kind") in ("question", "concern", "request", "reaction") else "question",
                   "slide": sl[0] if sl and 1 <= sl[0] <= total else None, "req_id": rid if rid in reqs_by_id else "",
                   "answered": q.get("answered") if q.get("answered") in ("yes", "partly", "no", "deferred") else "no",
                   "answer_summary": _text(q.get("answer_summary")),
                   "quality": q.get("quality") if q.get("quality") in ("strong", "adequate", "weak", "n/a") else "n/a",
                   "note": _text(q.get("note")), "follow_up": _text(q.get("follow_up"))})
    com = [{"what": _text(c.get("what")), "who": _text(c.get("who")), "due": _text(c.get("due"))}
           for c in _dicts(_load(raw.get("commitments"))) if _text(c.get("what")).strip()]
    counts = {k: sum(1 for r in reqs if r["verdict"] == k) for k in ("covered", "partly", "deck_only", "conversation_only", "gap")}
    counts["conv_addressed"] = sum(1 for r in reqs if r["conversation"]["status"] in ("delivered", "partial"))
    counts["conv_delivered"] = sum(1 for r in reqs if r["conversation"]["status"] == "delivered")
    counts["deck_supported"] = sum(1 for r in reqs if r["deck"]["strength"] in ("strong", "partial"))
    counts["deck_strong"] = sum(1 for r in reqs if r["deck"]["strength"] == "strong")
    counts["pitch_gaps"] = sum(1 for r in reqs if r["verdict"] == "gap" and r["relevance"] == "pitch")
    counts["contract_gaps"] = sum(1 for r in reqs if r["verdict"] == "gap" and r["relevance"] == "contract")
    by_pri = {}
    for grp in ("must", "should", "other"):
        rs = [r for r in reqs if (r["priority"].lower() if r["priority"].lower() in ("must", "should") else "other") == grp]
        by_pri[grp] = {"total": len(rs), "covered": sum(1 for r in rs if r["verdict"] == "covered"),
                       "partly": sum(1 for r in rs if r["verdict"] in ("partly", "deck_only", "conversation_only")),
                       "gap": sum(1 for r in rs if r["verdict"] == "gap"),
                       "conv_addressed": sum(1 for r in rs if r["conversation"]["status"] in ("delivered", "partial"))}
    qcounts = {"q_" + k: sum(1 for q in qs if q["answered"] == k) for k in ("yes", "partly", "deferred", "no")}
    # themes: every requirement in exactly one theme; unplaced ones go to "Other"
    by_id = {r["req_id"]: r for r in reqs}
    placed, themes = set(), []
    for t in _dicts(_load(raw.get("themes"))):
        ids = []
        for x in (_load(t.get("req_ids")) if isinstance(_load(t.get("req_ids")), list) else []):
            x = _text(x).strip()
            if x in by_id and x not in placed:
                ids.append(x)
                placed.add(x)
        if ids and _text(t.get("name")).strip():
            themes.append({"name": _text(t.get("name")).strip(), "req_ids": ids, "summary": _text(t.get("summary"))})
    rest = [r["req_id"] for r in reqs if r["req_id"] not in placed]
    if rest:
        themes.append({"name": "Other requirements" if themes else "All requirements", "req_ids": rest, "summary": ""})
    for t in themes:
        rs = [by_id[i] for i in t["req_ids"]]
        t["counts"] = {k: sum(1 for r in rs if r["verdict"] == k) for k in ("covered", "partly", "deck_only", "conversation_only", "gap")}
        t["counts"]["total"] = len(rs)
        t["counts"]["conv_addressed"] = sum(1 for r in rs if r["conversation"]["status"] in ("delivered", "partial"))
        t["counts"]["deck_supported"] = sum(1 for r in rs if r["deck"]["strength"] in ("strong", "partial"))
        share = (t["counts"]["covered"] + 0.5 * (t["counts"]["partly"] + t["counts"]["deck_only"] + t["counts"]["conversation_only"])) / len(rs)
        t["status"] = "strong" if share >= 0.7 else ("weak" if share < 0.35 else "mixed")
    return {"summary": _text(raw.get("summary")), "biggest_gaps": _lines(raw.get("biggest_gaps")),
            "next_steps": _lines(raw.get("next_steps")), "requirements": reqs, "themes": themes,
            "client_questions": qs, "commitments": com,
            "counts": {"requirements": len(reqs), **counts, "by_priority": by_pri, "questions": len(qs), **qcounts}}


def run_review(client, ctx: str, sess: dict, pr: dict, events: list, past: dict, debug_path=None) -> dict:
    titles = {s["n"]: s["title"] for s in sess["slides"]}
    timed = not past.get("timing_estimated", False)
    notes = []
    if past.get("notes"):
        notes.append("User notes: " + past["notes"])
    if past.get("our_side"):
        notes.append("People on our side: " + past["our_side"])
    dyn = ("\n".join(notes) + ("\n\n" if notes else "") + "TRANSCRIPT"
           + ("" if timed else " (no timestamps: do not comment on timing)") + ":\n"
           + (review_script(events, timed, titles) or "(empty)") + "\n\nWrite the review now by calling submit_review.")
    raw, _ = call_tool(client, SCORE_MODEL, REVIEW_SYSTEM, REVIEW_TOOL, [{"role": "user", "content": _blocks(ctx, dyn)}], 32000)
    if debug_path is not None:
        try:
            debug_path.write_text(json.dumps(raw, indent=2, default=str), encoding="utf-8")
        except Exception:
            pass
    return normalise_review(raw, sess, pr)
