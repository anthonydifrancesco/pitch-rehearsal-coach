"""Assess a pitch that has already happened, from a meeting transcript (e.g. Granola)."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .llm import call_tool
from .preread import _dicts, _ints, _load, _text
from .rehearse import SCORE_MODEL, _blocks, mmss

TS = r"(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d+)?"
LINE_RES = [
    # [00:01:23] Name: text   |   (00:01:23) Name: text   |   00:01:23 Name: text
    re.compile(r"^\s*[\[(]?(?P<ts>" + TS + r")[\])]?\s*[-–—]?\s*(?P<sp>[^:\n]{1,40}?)\s*:\s*(?P<tx>.+)$"),
    # Name [00:01:23]: text   |   Name (00:01:23): text   |   Name  00:01:23: text
    re.compile(r"^\s*(?P<sp>[^:\[(\n]{1,40}?)\s*[\[(]?(?P<ts>" + TS + r")[\])]?\s*:?\s*(?P<tx>.+)$"),
    # Name: text
    re.compile(r"^\s*(?P<sp>[A-Za-z][^:\n]{0,38}?)\s*:\s*(?P<tx>.+)$"),
]
BARE_TS = re.compile(r"^\s*[\[(]?(?P<ts>" + TS + r")[\])]?\s*$")
SRT_TIME = re.compile(r"^(?P<ts>\d{1,2}:\d{2}:\d{2})[.,]\d+\s*-->")
WORDS_PER_SEC = 2.5  # ~150 wpm, used only when the transcript has no timestamps


def _secs(ts: str) -> float:
    parts = [float(p.replace(",", ".")) for p in ts.split(":")]
    s = 0.0
    for p in parts:
        s = s * 60 + p
    return s


def _clean(s: str) -> str:
    return re.sub(r"[*_`#]+", "", s).strip()


def parse_transcript(text: str, max_words: int = 80) -> Dict[str, Any]:
    """Tolerant parser. Returns {segments:[{i,t,speaker,text}], has_times, speakers}.
    Long paragraphs are split at sentence ends so each piece can be assigned to a slide."""
    raw: List[dict] = []
    cur_t: Optional[float] = None
    last: Optional[dict] = None
    for line in (text or "").replace("\r", "").split("\n"):
        line = line.strip()
        if not line or line.upper() == "WEBVTT" or re.fullmatch(r"\d+", line):
            continue
        m = SRT_TIME.match(line)
        if m:
            cur_t = _secs(m.group("ts"))
            continue
        m = BARE_TS.match(line)
        if m:
            cur_t = _secs(m.group("ts"))
            continue
        line = _clean(line)
        got = None
        for rx in LINE_RES:
            m = rx.match(line)
            if m and len(m.group("tx").split()) >= 1:
                got = m
                break
        if got:
            ts = got.groupdict().get("ts")
            t = _secs(ts) if ts else cur_t
            last = {"t": t, "speaker": got.group("sp").strip(), "text": got.group("tx").strip()}
            raw.append(last)
            cur_t = t if t is not None else cur_t
        elif last is not None and not line.startswith("<"):
            last["text"] += " " + line          # continuation of the same turn
        else:
            last = {"t": cur_t, "speaker": "", "text": line}
            raw.append(last)
    # merge consecutive turns by the same speaker, then split long ones
    merged: List[dict] = []
    for r in raw:
        if merged and merged[-1]["speaker"] == r["speaker"] and (r["t"] is None or merged[-1]["t"] is None or r["t"] - merged[-1]["t"] < 20):
            merged[-1]["text"] += " " + r["text"]
        else:
            merged.append(dict(r))
    segs: List[dict] = []
    for r in merged:
        sents = re.split(r"(?<=[.!?])\s+", r["text"].strip())
        buf: List[str] = []
        n = 0
        pieces: List[str] = []
        for s in sents:
            w = len(s.split())
            if buf and n + w > max_words:
                pieces.append(" ".join(buf))
                buf, n = [], 0
            buf.append(s)
            n += w
        if buf:
            pieces.append(" ".join(buf))
        total_w = max(1, sum(len(p.split()) for p in pieces))
        done = 0
        for p in pieces:
            t = r["t"]
            segs.append({"i": len(segs) + 1, "t": t, "speaker": r["speaker"], "text": p, "_off": done})
            done += len(p.split())
    has_times = sum(1 for s in segs if s["t"] is not None) >= max(1, (len(segs) + 1) // 2)
    if has_times:                                # interpolate missing / split-piece times
        known = [s["t"] for s in segs if s["t"] is not None]
        base = min(known)
        prev = 0.0
        for s in segs:
            s["t"] = (s["t"] - base) if s["t"] is not None else prev
            prev = s["t"]
    else:
        t = 0.0
        for s in segs:
            s["t"] = t
            t += len(s["text"].split()) / WORDS_PER_SEC
    for s in segs:
        s.pop("_off", None)
    speakers = sorted({s["speaker"] for s in segs if s["speaker"]})
    return {"segments": segs, "has_times": has_times, "speakers": speakers}


# ---------------------------------------------------------------- slide / role mapping
MAP_SYSTEM = """You are preparing a recorded transcript of a REAL client pitch for assessment. You are given the pitch deck analysis and the \
requirements (above) and a numbered transcript. The transcript comes from a meeting recorder: speaker labels may be missing or wrong \
(in-person meetings are often one blob), and words may be misheard.

Divide the transcript into contiguous RUNS covering every segment number from first to last, in order, and for each run say:
- role: "presenter" (our side, pitching, answering) | "client" (the client's questions, comments, reactions) | "chatter" (small talk, \
setting up, tech problems, goodbyes: not part of the pitch).
- slide: the slide number being talked about (a presenter run: the slide the content belongs to; a client run: the slide their question is about). \
Slides are normally covered in order, but the presenter may jump back. Use the deck analysis, not guesses. 0 only for chatter.
- speaker: a name if one can be told from labels or from how people address each other, else "".
Also give qa_from_seg: the first segment of the closing questions-and-discussion phase, if the presentation clearly ended and a general \
Q&A began (otherwise 0). Questions the client asked during the presentation are NOT Q&A.
If the transcript HAS speaker labels, they are the truth about who spoke: decide once, per label, whether that speaker is on our side or the \
client's (speaker_roles; use how they are addressed, what they say and the user's hint; "Me" is usually our side and "Them" the client, but \
colleagues of ours may also sit under "Them"), and then never change a labelled segment's role from that. Use runs mainly to assign SLIDES. \
If labels are missing or useless (e.g. one blob, "Speaker 1"), work out the role of each stretch from the content instead.
Prefer fewer, longer runs, but switch whenever the speaker role changes. If the user said which names are on their side, respect that.
Call the submit_mapping tool."""

MAP_TOOL = {
    "name": "submit_mapping",
    "description": "Submit the run structure of the transcript.",
    "input_schema": {
        "type": "object",
        "required": ["runs"],
        "properties": {
            "runs": {"type": "array", "items": {"type": "object", "required": ["from_seg", "to_seg", "role", "slide"], "properties": {
                "from_seg": {"type": "integer"}, "to_seg": {"type": "integer"},
                "role": {"type": "string", "enum": ["presenter", "client", "chatter"]},
                "slide": {"type": "integer"}, "speaker": {"type": "string"}}}},
            "qa_from_seg": {"type": "integer"},
            "speaker_roles": {"type": "array", "items": {"type": "object", "required": ["label", "role"], "properties": {
                "label": {"type": "string"}, "role": {"type": "string", "enum": ["presenter", "client"]}}}},
            "presenters": {"type": "array", "items": {"type": "string"}},
            "clients": {"type": "array", "items": {"type": "string"}},
        },
    },
}


def map_transcript(client, ctx: str, parsed: dict, total_slides: int, our_side: str, debug_path=None) -> dict:
    segs = parsed["segments"]
    body = "\n".join(f"[{s['i']}] " + (f"{s['speaker']}: " if s["speaker"] else "") + s["text"] for s in segs)
    dyn = (f"Total slides: {total_slides}. People on our (presenting) side, per the user: {our_side.strip() or 'not said'}.\n"
           f"Speaker labels in the transcript: {', '.join(parsed['speakers']) or 'none'}.\n\nTRANSCRIPT:\n{body}\n\nCall submit_mapping.")
    raw, _ = call_tool(client, SCORE_MODEL, MAP_SYSTEM, MAP_TOOL, [{"role": "user", "content": _blocks(ctx, dyn)}], 16000)
    if debug_path is not None:
        try:
            import json
            debug_path.write_text(json.dumps(raw, indent=2, default=str), encoding="utf-8")
        except Exception:
            pass
    raw = _load(raw)
    if not isinstance(raw, dict):
        raise RuntimeError("The transcript could not be matched to the slides. Try again.")
    n = len(segs)
    role = [None] * (n + 1)
    slide = [0] * (n + 1)
    spk = [""] * (n + 1)
    for r in _dicts(_load(raw.get("runs"))):
        a, b = _ints([r.get("from_seg")]), _ints([r.get("to_seg")])
        if not a or not b:
            continue
        ro = r.get("role") if r.get("role") in ("presenter", "client", "chatter") else "presenter"
        sl = _ints([r.get("slide")])
        sl = sl[0] if sl and 0 <= sl[0] <= total_slides else 0
        for i in range(max(1, a[0]), min(n, b[0]) + 1):
            role[i], slide[i], spk[i] = ro, sl, _text(r.get("speaker")).strip()
    if not any(role[1:]):
        raise RuntimeError("The transcript could not be matched to the slides. Try again.")
    # trust the transcript's own speaker labels over per-run guesses
    lab = {}
    for r in _dicts(_load(raw.get("speaker_roles"))):
        if r.get("role") in ("presenter", "client") and _text(r.get("label")).strip():
            lab[_text(r.get("label")).strip().lower()] = r["role"]
    if len(parsed["speakers"]) >= 2 and lab:
        for sg in segs:
            k = sg["speaker"].strip().lower()
            if k in lab and role[sg["i"]] is not None and role[sg["i"]] != "chatter":
                role[sg["i"]] = lab[k]
            elif k in lab and role[sg["i"]] is None:
                role[sg["i"]] = lab[k]
    for i in range(1, n + 1):                     # fill gaps from the previous segment
        if role[i] is None:
            role[i], slide[i], spk[i] = (role[i - 1], slide[i - 1], spk[i - 1]) if i > 1 else ("chatter", 0, "")
    qa = _ints([raw.get("qa_from_seg")])
    return {"role": role, "slide": slide, "speaker": spk, "qa_from": qa[0] if qa and 1 <= qa[0] <= n else 0}


def build_events(parsed: dict, mp: dict, default_presenter: str, total_slides: int) -> List[dict]:
    """Turn the mapped transcript into the same events a live rehearsal records."""
    events: List[dict] = []
    segs = parsed["segments"]
    cur_slide = 0
    qa_from = mp["qa_from"]
    for s in segs:
        i = s["i"]
        ro = mp["role"][i]
        if ro == "chatter":
            continue
        qa = bool(qa_from and i >= qa_from)
        sl = mp["slide"][i] or cur_slide or 1
        t = round(float(s["t"]), 1)
        if sl != cur_slide and not qa:
            events.append({"type": "slide", "t": t, "slide": sl})
            cur_slide = sl
        name = mp["speaker"][i] or (s["speaker"] if s["speaker"] and s["speaker"].lower() not in ("me", "them", "speaker") else "")
        if ro == "presenter":
            events.append({"type": "speech", "t": t, "slide": sl, "speaker": name or default_presenter,
                           "text": s["text"], "phase": "qa" if qa else "pitch"})
        else:
            events.append({"type": "panel", "t": t, "persona_id": "client", "speaker_name": name or "Client",
                           "text": s["text"], "target": "room", "kind": "qa" if qa else "live", "slide": sl,
                           "dur": round(len(s["text"].split()) / WORDS_PER_SEC, 1), "phase": "qa" if qa else "pitch"})
    return events


SCORE_NOTE = """THIS IS A REAL PITCH THAT ALREADY TOOK PLACE, recorded by a meeting tool, not a rehearsal.
- Lines marked PANEL are the REAL client's own questions, comments and reactions (not AI). Treat their reactions as strong evidence for gut feel \
and for your win-likelihood read. Rate how well the presenters answered each real question.
- The transcript may contain mishearings and wrong speaker attribution; do not penalise obvious transcription noise.
- {timing}
- Who said what is known from the transcript, so judge CONTENT thoroughly: what was covered, how well, what evidence was given, and how each \
real question, concern, objection or reaction from the client was handled (rate every one, including statements that were not phrased as questions).
- Judge on the evidence in the transcript only.{notes}"""


def score_note(past: dict) -> str:
    timing = ("The recording has NO usable timestamps. Ignore timing and pace completely: do not comment on speed, seconds, time spent or "
              "running over. For each slide judge only the content: was its message communicated, with what evidence, how clearly." if past.get("timing_estimated")
              else "Timestamps come from the recording and are reliable.")
    notes = (" User notes: " + past["notes"].strip()) if past.get("notes", "").strip() else ""
    return SCORE_NOTE.format(timing=timing, notes=notes)
