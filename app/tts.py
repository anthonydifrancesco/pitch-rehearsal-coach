"""Optional realistic voices. If a TTS key is set the panel speaks with a neural voice made on the server;
with no key (or on any failure) the browser's own voices are used instead.
  OPENAI_API_KEY      -> OpenAI gpt-4o-mini-tts: accent, age and manner are steered by an instruction (recommended)
  ELEVENLABS_API_KEY  -> ElevenLabs: picks stock voices by gender / accent / age
  TTS_PROVIDER=openai|elevenlabs|off   (optional; default: first key found)"""
from __future__ import annotations

import os
import time

import httpx

OPENAI_MALE = ["onyx", "echo", "ash", "ballad", "sage"]          # deepest first
OPENAI_FEMALE = ["nova", "shimmer", "coral", "alloy", "sage"]
MAX_CHARS = 1200


def provider() -> str:
    p = os.environ.get("TTS_PROVIDER", "").strip().lower()
    if p == "off":
        return ""
    if p in ("openai", "elevenlabs"):
        return p if os.environ.get("OPENAI_API_KEY" if p == "openai" else "ELEVENLABS_API_KEY", "").strip() else ""
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return "openai"
    if os.environ.get("ELEVENLABS_API_KEY", "").strip():
        return "elevenlabs"
    return ""


def _gender(p: dict) -> str:
    return "female" if str(p.get("gender", "")).lower().startswith("f") else "male"


def _age(p: dict) -> int:
    try:
        return max(18, min(90, int(p.get("age") or 40)))
    except (TypeError, ValueError):
        return 40


def openai_voice(p: dict, slot: int) -> str:
    pool = OPENAI_FEMALE if _gender(p) == "female" else OPENAI_MALE
    if _age(p) < 38 and _gender(p) == "male":
        pool = ["echo", "ash", "ballad", "onyx", "sage"]
    return pool[int(slot or 0) % len(pool)]


def instructions(p: dict) -> str:
    acc = (p.get("accent") or "Australian").strip()
    age = _age(p)
    who = f"{p.get('name', '')}, {p.get('title', '')}".strip(", ")
    return (f"You are {who}, a {age}-year-old {_gender(p)} client executive in a business meeting. Speak with a natural, authentic {acc} accent "
            f"(not exaggerated). Sound like a real person talking, not reading: conversational pace, natural pauses, occasional "
            f"emphasis, no announcer voice. Manner: {str(p.get('style', ''))[:300]}")


def _openai(text: str, p: dict, slot: int) -> bytes:
    r = httpx.post("https://api.openai.com/v1/audio/speech", timeout=45,
                   headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"].strip()},
                   json={"model": os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"), "voice": openai_voice(p, slot),
                         "input": text, "instructions": instructions(p), "response_format": "mp3"})
    r.raise_for_status()
    return r.content


_EL_VOICES: dict = {"t": 0, "v": []}


def _el_voices() -> list:
    if time.time() - _EL_VOICES["t"] > 3600 or not _EL_VOICES["v"]:
        r = httpx.get("https://api.elevenlabs.io/v1/voices", timeout=20, headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"].strip()})
        r.raise_for_status()
        _EL_VOICES.update(t=time.time(), v=r.json().get("voices", []))
    return _EL_VOICES["v"]


def elevenlabs_voice(p: dict, slot: int) -> str:
    want, acc, age = _gender(p), (p.get("accent") or "").lower(), _age(p)
    scored = []
    for v in _el_voices():
        lab = v.get("labels") or {}
        if (lab.get("gender") or "").lower() != want:
            continue
        s = (3 if acc and acc in (lab.get("accent") or "").lower() else 0)
        s += {"young": 1 if age < 35 else 0, "middle aged": 1 if 35 <= age < 58 else 0, "old": 1 if age >= 58 else 0}.get((lab.get("age") or "").lower(), 0)
        scored.append((s, v["voice_id"]))
    if not scored:
        raise RuntimeError("No ElevenLabs voice matched")
    scored.sort(key=lambda x: -x[0])
    top = [v for s, v in scored if s == scored[0][0]] + [v for s, v in scored if s != scored[0][0]]
    return top[int(slot or 0) % len(top)]


def _elevenlabs(text: str, p: dict, slot: int) -> bytes:
    vid = elevenlabs_voice(p, slot)
    r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{vid}?output_format=mp3_44100_64", timeout=45,
                   headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"].strip()},
                   json={"text": text, "model_id": os.environ.get("ELEVENLABS_MODEL", "eleven_turbo_v2_5")})
    r.raise_for_status()
    return r.content


def speak(text: str, persona: dict, slot: int = 0) -> bytes:
    prov = provider()
    if not prov:
        raise RuntimeError("No text-to-speech key is set")
    text = (text or "").strip()[:MAX_CHARS]
    if not text:
        raise RuntimeError("Nothing to say")
    return _openai(text, persona, slot) if prov == "openai" else _elevenlabs(text, persona, slot)
