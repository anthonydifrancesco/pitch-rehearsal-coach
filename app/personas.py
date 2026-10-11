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
