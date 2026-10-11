"""Realistic-voice plumbing with the vendors mocked: provider choice, voice choice, instructions, endpoint and failure."""
import os, sys, tempfile
from pathlib import Path
os.environ["DATA_DIR"] = tempfile.mkdtemp()
for k in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "TTS_PROVIDER"): os.environ.pop(k, None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
import app.main as m
from app import tts

c = TestClient(m.app)
assert tts.provider() == "" and c.get("/api/config").json()["tts"] == ""
assert c.post("/api/tts", json={"text": "hi"}).status_code == 503                       # no key: page uses browser voices
os.environ["OPENAI_API_KEY"] = "sk-test"; assert tts.provider() == "openai" and c.get("/api/config").json()["tts"] == "openai"
calls = []
class R:
    def __init__(s, content=b"", js=None, code=200): s.content, s._js, s.code = content, js, code
    def raise_for_status(s):
        if s.code >= 400: raise RuntimeError("bad")
    def json(s): return s._js
def post(url, **kw): calls.append((url, kw)); return R(b"ID3audio")
tts.httpx.post = post
p = {"id": "a", "name": "Tracey", "title": "Field Ops", "gender": "female", "age": 48, "accent": "Australian", "style": "Tough and practical"}
r = c.post("/api/tts", json={"text": "Walk me through the plan.", "persona": p, "slot": 1})
assert r.status_code == 200 and r.content == b"ID3audio" and r.headers["content-type"] == "audio/mpeg"
body = calls[-1][1]["json"]; assert body["voice"] == "shimmer" and "Australian accent" in body["instructions"] and "48-year-old female" in body["instructions"] and "Tough" in body["instructions"]
assert tts.openai_voice({"gender": "male", "age": 55}, 0) == "onyx" and tts.openai_voice({"gender": "male", "age": 30}, 0) == "echo"
assert len({tts.openai_voice({"gender": "female", "age": 40}, i) for i in range(4)}) == 4        # distinct voices per slot
assert len(calls[-1][1]["json"]["input"]) <= tts.MAX_CHARS
tts.httpx.post = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
assert c.post("/api/tts", json={"text": "hi", "persona": p}).status_code == 502         # failure: page falls back
# ElevenLabs picks by gender / accent / age from the account's voice list
os.environ.pop("OPENAI_API_KEY"); os.environ["ELEVENLABS_API_KEY"] = "xi"; assert tts.provider() == "elevenlabs"
tts.httpx.get = lambda *a, **k: R(js={"voices": [
    {"voice_id": "m_us", "labels": {"gender": "male", "accent": "american", "age": "middle aged"}},
    {"voice_id": "m_au", "labels": {"gender": "male", "accent": "australian", "age": "middle aged"}},
    {"voice_id": "f_au", "labels": {"gender": "female", "accent": "australian", "age": "young"}}]})
assert tts.elevenlabs_voice({"gender": "male", "accent": "Australian", "age": 50}, 0) == "m_au"
assert tts.elevenlabs_voice({"gender": "female", "accent": "Australian", "age": 30}, 0) == "f_au"
os.environ["TTS_PROVIDER"] = "off"; assert tts.provider() == ""
print("TTS OK")
