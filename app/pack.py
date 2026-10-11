"""Pitch Pack: the file that carries a finished pitch analysis between tools.

The analysis tool (no live voice, no credits for rehearsal) exports a Pitch Pack. This tool imports it and
rehearses against it without running its own pre-read. This tool can also export a pack from its own pre-read,
so both directions use the same format. Format spec: PITCH_PACK.md.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from .preread import normalise

FORMAT = "pitch-pack"
VERSION = 1


def deck_fingerprint(slides: list[dict]) -> str:
    """Stable hash of the deck's slide titles and on-slide text (not notes), so a changed deck can be spotted."""
    h = hashlib.sha256()
    for s in slides:
        h.update(f"{s.get('n')}|{(s.get('title') or '').strip()}|{(s.get('text') or '').strip()}\n".encode("utf-8"))
    return h.hexdigest()[:16]


def build_pack(sess: dict, pr: dict, rehearsals: list[dict] | None = None, include_rfp: bool = True) -> dict:
    st = sess.get("settings", {})
    ov = sess.get("pacing_overrides", {})
    analysis = json.loads(json.dumps(pr))  # copy
    analysis.pop("meta", None)
    for s in analysis["slides"]:  # bake the user's pacing overrides in, so the pack is self-contained
        if ov.get(str(s["n"])):
            s["pacing"] = ov[str(s["n"])]
    pack: dict[str, Any] = {
        "format": FORMAT,
        "version": VERSION,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": {"tool": "pitch-rehearsal-coach", "model": (pr.get("meta") or {}).get("model", "")},
        "pitch": {
            "title": st.get("title", ""),
            "channel": st.get("channel", ""),
            "ignore_topics": st.get("ignore_topics", ""),
            "guidance": st.get("guidance", ""),
            "presenters": st.get("presenters", []),
        },
        "deck_fingerprint": deck_fingerprint(sess["slides"]),
        "slides": [{"n": s["n"], "title": s["title"]} for s in sess["slides"]],
        "analysis": analysis,
    }
    if include_rfp and sess.get("rfp_text"):
        pack["rfp_text"] = sess["rfp_text"]
    if rehearsals:
        pack["rehearsals"] = rehearsals
    return pack


def rehearsal_summary(reh: dict) -> dict | None:
    """Compact, shareable result of one scored rehearsal (no transcript)."""
    sc = reh.get("score")
    if not sc:
        return None
    return {
        "id": reh.get("id"), "started": reh.get("started"),
        "brief_coverage": sc["brief_coverage"]["score"], "gut_feel": sc["gut_feel"]["score"],
        "question_handling": sc["question_handling"]["score"], "delivery": sc.get("delivery_score"),
        "total": sc["total"], "win_panel_pct": sc["win_likelihood"]["panel_read_pct"],
        "win_calculated_pct": sc["win_likelihood"]["calculated_pct"],
        "slides": [{"n": s["n"], "verdict": s["verdict"], "rating": s["rating"]} for s in sc["slides"]],
        "top_actions": sc["top_actions"],
        "unmet_requirements": [i["req_id"] for i in sc["brief_coverage"]["items"] if i["met"] != "yes"],
    }


def read_pack(raw: Any) -> dict:
    """Validate a pack and turn it into what a session needs. Raises ValueError with a plain-English reason."""
    if isinstance(raw, (bytes, str)):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise ValueError("That file is not valid JSON, so it can't be a Pitch Pack.")
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        raise ValueError("That file is not a Pitch Pack (it should have \"format\": \"pitch-pack\").")
    ver = raw.get("version")
    if not isinstance(ver, int) or ver < 1:
        raise ValueError("The Pitch Pack has no version number.")
    if ver > VERSION:
        raise ValueError(f"This Pitch Pack is version {ver} but this tool only understands up to version {VERSION}. Update the rehearsal tool.")
    slides_in = raw.get("slides")
    if not isinstance(slides_in, list) or not slides_in:
        raise ValueError("The Pitch Pack has no slide list.")
    slides = []
    for i, s in enumerate(slides_in, 1):
        if not isinstance(s, dict):
            raise ValueError("The Pitch Pack's slide list is malformed.")
        slides.append({"n": i, "title": str(s.get("title") or f"Slide {i}"), "text": str(s.get("text") or ""),
                       "notes": str(s.get("notes") or ""), "pictures": int(s.get("pictures") or 0)})
    analysis = raw.get("analysis")
    if not isinstance(analysis, dict) or not analysis.get("requirements") or not analysis.get("slides"):
        raise ValueError("The Pitch Pack has no analysis (it needs requirements and per-slide analysis).")
    pr = normalise(analysis, len(slides))  # tolerant of loose output; guarantees every slide and valid enums
    warnings = []
    if pr["missing_slides"]:
        warnings.append(f"The analysis had nothing for slide(s) {', '.join(map(str, pr['missing_slides']))}; they were treated as normal pace.")
    if not any(not r["ignored"] for r in pr["requirements"]):
        warnings.append("Every requirement in the pack is marked out of scope, so brief coverage can't be scored.")
    pitch = raw.get("pitch") if isinstance(raw.get("pitch"), dict) else {}
    return {
        "pitch": pitch, "slides": slides, "preread": pr, "rfp_text": str(raw.get("rfp_text") or ""),
        "fingerprint": str(raw.get("deck_fingerprint") or ""), "warnings": warnings,
        "source": raw.get("source") if isinstance(raw.get("source"), dict) else {},
    }
