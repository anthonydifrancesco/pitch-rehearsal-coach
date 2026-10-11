"""Starter personas (editable and saved to data/personas.json)."""
from __future__ import annotations

import json
import os
from pathlib import Path

DATA = Path(os.environ.get("DATA_DIR") or (Path(__file__).resolve().parent.parent / "data"))

STARTERS = [
    {
        "id": "finance", "persona_hint": "finance",
        "name": "Graham", "title": "Finance Director", "colour": "#d4a017",
        "voice": {"age": 55, "gender": "male", "accent": "Australian", "locked": False},
        "style": "Budget-focused. Wants numbers, payback, risk and what happens if volumes change.",
        "questions": 5, "key_decision_maker": True,
    },
    {
        "id": "operations", "persona_hint": "operations",
        "name": "Tracey", "title": "Field Operations Manager", "colour": "#2e8b57",
        "voice": {"age": 48, "gender": "female", "accent": "Australian", "locked": False},
        "style": "Tough and practical, protective of her people. Probes scheduling, travel, cover, compliance and what breaks on a bad day.",
        "questions": 6, "key_decision_maker": False,
    },
    {
        "id": "skeptic", "persona_hint": "skeptic",
        "name": "Daniel", "title": "Client Contact", "colour": "#c0392b",
        "voice": {"age": 40, "gender": "male", "accent": "Australian", "locked": False},
        "style": "Skeptical. Has seen big promises before. Asks for proof, references and what you'd do differently from the incumbent.",
        "questions": 6, "key_decision_maker": True,
    },
    {
        "id": "insights", "persona_hint": "insights",
        "name": "Priya", "title": "Insights & Reporting Lead", "colour": "#3b7dd8",
        "voice": {"age": 34, "gender": "female", "accent": "Australian", "locked": False},
        "style": "Insights enthusiast. Wants to know what is measured, how often, how it links to sales, and who acts on it.",
        "questions": 4, "key_decision_maker": False,
    },
    {
        "id": "channel_expert", "persona_hint": "channel_expert",
        "name": "Liam", "title": "Channel Expert", "colour": "#8e44ad",
        "voice": {"age": 45, "gender": "male", "accent": "Australian", "locked": False},
        "style": "Knows the channel and the retailers. Wants proof not claims, and plans that are actionable and realistic in store.",
        "questions": 5, "key_decision_maker": False,
    },
]


def load_personas() -> list[dict]:
    p = DATA / "personas.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return STARTERS


def save_personas(personas: list[dict]) -> None:
    DATA.mkdir(exist_ok=True)
    (DATA / "personas.json").write_text(json.dumps(personas, indent=2), encoding="utf-8")


import re as _re
_HEX = _re.compile(r"^#[0-9a-fA-F]{6}$")
_PALETTE = ["#d4a017", "#2e8b57", "#e5484d", "#3b82f6", "#a259ff", "#14b8a6", "#f97316", "#ec4899"]


def sanitize(personas) -> list[dict]:
    """Make user-edited panel members safe and complete: unique ids, sane lengths, valid colours."""
    out, seen = [], set()
    for i, p in enumerate(personas if isinstance(personas, list) else []):
        if not isinstance(p, dict) or len(out) >= 10:
            continue
        pid = _re.sub(r"[^a-z0-9_]", "", str(p.get("id") or "").lower())[:24] or f"p{i + 1}"
        while pid in seen:
            pid += "x"
        seen.add(pid)
        v = p.get("voice") if isinstance(p.get("voice"), dict) else {}

        def num(x, lo, hi, d):
            try:
                return max(lo, min(hi, int(x)))
            except (TypeError, ValueError):
                return d
        q = {
            "id": pid, "persona_hint": str(p.get("persona_hint") or pid)[:24],
            "name": (str(p.get("name") or "").strip() or f"Member {i + 1}")[:40],
            "title": str(p.get("title") or "").strip()[:60],
            "colour": p.get("colour") if _HEX.match(str(p.get("colour") or "")) else _PALETTE[i % len(_PALETTE)],
            "voice": {"age": num(v.get("age"), 18, 90, 40),
                      "gender": "female" if str(v.get("gender", "")).lower().startswith("f") else "male",
                      "accent": str(v.get("accent") or "Australian")[:30], "locked": bool(v.get("locked"))},
            "style": str(p.get("style") or "").strip()[:800],
            "questions": num(p.get("questions"), 0, 10, 5),
            "key_decision_maker": bool(p.get("key_decision_maker")),
        }
        if p.get("include") is False:
            q["include"] = False
        out.append(q)
    return out


def reset_personas() -> None:
    f = DATA / "personas.json"
    if f.exists():
        f.unlink()
