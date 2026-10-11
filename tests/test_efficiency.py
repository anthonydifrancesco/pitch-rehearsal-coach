"""Cache settings, slide image for the live panel, merged transcript, usage tally, prior weak spots. No network."""
import json, os, sys, tempfile, types
from pathlib import Path
tmp = tempfile.mkdtemp(); os.environ["DATA_DIR"] = tmp
os.environ.pop("APP_PASSWORD", None); os.environ.pop("RENDER", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
import app.main as m
from app import rehearse as R, llm as L

# ---- block layout: cached context first (1h for live), slide image next, changing text last
b = R._blocks("CTX", "DYN", "1h", ["IMG1"])
assert b[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"} and b[-1] == {"type": "text", "text": "DYN"}
assert b[2]["type"] == "image" and b[2]["cache_control"] == {"type": "ephemeral"}
assert R._blocks("CTX", "DYN")[0]["cache_control"] == {"type": "ephemeral"} and len(R._blocks("CTX", "DYN")) == 2

# ---- fake client that records calls, can refuse the ttl option once
class Usage: input_tokens = 100; output_tokens = 20; cache_read_input_tokens = 900; cache_creation_input_tokens = 0
class Stream:
    def __init__(s, msg): s.msg = msg
    def __enter__(s): return s
    def __exit__(s, *a): return False
    def get_final_message(s): return s.msg
class Fake:
    def __init__(s, refuse_ttl=False): s.messages = s; s.calls = []; s.refuse = refuse_ttl
    def stream(s, **kw):
        s.calls.append(json.loads(json.dumps(kw)))
        if s.refuse and any("ttl" in (blk.get("cache_control") or {}) for blk in kw["messages"][0]["content"] if isinstance(blk, dict)):
            raise RuntimeError("invalid ttl in cache_control")
        return Stream(types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text='{"interject": false}')], usage=Usage(), stop_reason="end_turn"))

sess = {"id": "a1b2c3d4e5", "settings": {"interjection": 10, "personas": [{"id": "p", "name": "Pat", "title": "CEO"}]},
        "slides": [{"n": 1, "title": "One"}], "preread": {"slides": [{"n": 1, "pacing": "quick"}]}}
state = {"interjection": 10, "elapsed": 400, "slide": 1, "slide_dwell": 999, "slide_words": 999, "asked": [], "recent": []}

f = Fake(); R.live_turn(f, "CTX", sess, state, ["IMG1"], "fast")
kw = f.calls[0]; assert kw["model"] == R.LIVE_MODEL and kw["messages"][0]["content"][0]["cache_control"]["ttl"] == "1h"
assert any(x.get("type") == "image" for x in kw["messages"][0]["content"])
f = Fake(); R.live_turn(f, "CTX", sess, state, None, "sharp"); assert f.calls[0]["model"] == R.SCORE_MODEL and "thinking" not in f.calls[0]
f = Fake(refuse_ttl=True); R.live_turn(f, "CTX", sess, state); assert len(f.calls) == 2 and "ttl" not in json.dumps(f.calls[1])   # falls back quietly
f = Fake(); R.live_turn(f, "CTX", sess, state, [], None); assert not any(x.get("type") == "image" for x in f.calls[0]["messages"][0]["content"])

# ---- transcript: fragments by one speaker on one slide are joined, other speakers and panel lines break the run
ev = [{"type": "speech", "t": 1, "slide": 1, "speaker": "Ann", "text": "We start"}, {"type": "speech", "t": 6, "slide": 1, "speaker": "Ann", "text": "with the brief."},
      {"type": "panel", "t": 9, "persona_id": "p", "text": "Why?", "kind": "live"}, {"type": "speech", "t": 12, "slide": 1, "speaker": "Ann", "text": "Because."},
      {"type": "speech", "t": 14, "slide": 1, "speaker": "Ben", "text": "Agreed."}, {"type": "speech", "t": 90, "slide": 1, "speaker": "Ben", "text": "Later."}]
out = R.script_lines(ev, sess["settings"], {1: "One"}).splitlines()
assert out[0] == "[00:01] (slide 1) Ann: We start with the brief." and len(out) == 5 and out[2].endswith("Ann: Because.") and "Later." in out[4], out

# ---- usage tally per rehearsal (through the real server paths)
c = TestClient(m.app)
sid, rid = "a1b2c3d4e5", "0000000001"
(m.SESSIONS / sid).mkdir(); m._write(sid, {"id": sid, "created": "x", "settings": sess["settings"], "slides": sess["slides"], "rfp_text": ""})
assert c.get(f"/api/sessions/{sid}/rehearsals/{rid}/usage").json()["calls"] == 0
L.track(sid, rid); L.ask_text(Fake(), "claude-haiku-5-5", "s", "hi", 10); L.ask_text(Fake(), "claude-haiku-5-5", "s", "hi", 10)
u = c.get(f"/api/sessions/{sid}/rehearsals/{rid}/usage").json()
assert u["calls"] == 2 and u["input"] == 200 and u["cache_read"] == 1800 and u["output"] == 40 and u["cache_hit_pct"] == 90, u
assert c.get(f"/api/sessions/{sid}/rehearsals/../usage").status_code in (404, 400, 422)

# ---- weak spots from earlier scored rehearsals reach question time, but never from the current one
def reh(r, started, actions):
    m._reh_write(sid, r, {"id": r, "started": started, "events": [{"type": "speech"}], "qa": [], "score": {"total": 50, "top_actions": actions}})
reh("0000000002", "2026-10-01 10:00", ["Prove the 15 chef claim", "Add reporting slide"])
reh("0000000003", "2026-10-02 10:00", ["Prove the 15 chef claim", "Explain Perth cover"])
reh("0000000004", "2026-10-03 10:00", ["CURRENT RUN ONLY"])
p = m._prior_gaps(sid, "0000000004")
assert "Perth cover" in p and "Add reporting slide" in p and "CURRENT" not in p and p.lower().count("prove the 15 chef claim") == 1, p
f = Fake(); f.messages = types.SimpleNamespace(stream=lambda **kw: Stream(types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text='{"questions":[{"persona_id":"p","text":"Q?","about_slide":1}]}')], usage=None, stop_reason="end_turn")))
captured = {}
orig = L.ask_text
def spy(client, model, system, content, mt, thinking=None):
    captured["c"] = content; return orig(client, model, system, content, mt, thinking)
R.ask_text = spy
R.qa_questions(f, "CTX", sess, ev, False, 1, p); assert "WEAK SPOTS FLAGGED" in captured["c"][-1]["text"] and "Perth cover" in captured["c"][-1]["text"]
R.qa_questions(f, "CTX", sess, ev, False, 1, ""); assert "WEAK SPOTS" not in captured["c"][-1]["text"]
print("EFFICIENCY OK")
