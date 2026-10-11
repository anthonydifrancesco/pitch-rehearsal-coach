"""Run the real server on a port with a FAKE Claude client and a ready-made session. For browser tests only.
Usage: python tests/serve_fake.py <port> <deck.pptx> <rfp.pdf>   (prints the session id, then serves)"""
import json, os, sys, types
from pathlib import Path
os.environ["REHEARSE_WARMUP_S"] = "2"
os.environ["REHEARSE_READY_SCALE"] = "0.05"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import uvicorn
from fastapi.testclient import TestClient
import app.main as m
from app.preread import normalise
from app.personas import STARTERS

class Stream:
    def __init__(self, msg): self.msg = msg
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get_final_message(self): return self.msg
def text_msg(t): return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=t)], usage=None, stop_reason="end_turn")
def tool_msg(d): return types.SimpleNamespace(content=[types.SimpleNamespace(type="tool_use", input=d)], usage=None, stop_reason="tool_use")
class Fake:
    def __init__(self): self.messages = self
    def stream(self, **kw):
        s = kw["system"]
        if s.startswith("You are a senior new-business adviser"):
            return Stream(tool_msg(json.loads('''{"summary": "Mostly met the brief.", "biggest_gaps": ["Perth cover unclear"], "next_steps": ["Send case studies"],
 "requirements": [{"req_id": "R1", "deck": {"strength": "strong", "slides": [2, 9], "note": "slide 2 says 15 chefs"}, "conversation": {"status": "delivered", "evidence": "we recruit fifteen chefs", "speaker": "Anthony", "note": ""}, "relevance": "pitch", "recommendation": "None needed"},
                  {"req_id": "R3", "deck": {"strength": "thin", "slides": [3], "note": "vague"}, "conversation": {"status": "not_discussed"}, "relevance": "contract", "recommendation": "Add reporting slide"}],
 "themes": [{"name": "Store coverage", "req_ids": ["R1", "R9"], "summary": "Stores covered."}],
 "client_questions": [{"asker": "Sam", "question": "How do you cover a dropout in Perth?", "kind": "question", "slide": 2, "req_id": "R1", "answered": "yes", "answer_summary": "Bench of backups", "quality": "strong", "note": "", "follow_up": "Send bench numbers"},
                      {"asker": "Sam", "question": "junk", "answered": "weird"}],
 "commitments": [{"what": "Send case studies", "who": "Anthony", "due": "Friday"}]}''')))
        if s.startswith("You are preparing a recorded transcript"):
            return Stream(tool_msg({"runs": [{"from_seg": 1, "to_seg": 2, "role": "presenter", "slide": 1}, {"from_seg": 3, "to_seg": 3, "role": "client", "slide": 1, "speaker": "Sam"},
                                             {"from_seg": 4, "to_seg": 99, "role": "presenter", "slide": 2}], "qa_from_seg": 0}))
        if s.startswith("You are the client panel in a live pitch rehearsal. A panel"):
            said = kw["messages"][0]["content"][1]["text"]
            if "do you mean" in said:
                return Stream(text_msg(json.dumps({"clarifying": True, "persona_id": "finance", "text": "I mean the whole contract term, per demo."})))
            return Stream(text_msg(json.dumps({"clarifying": False})))
        if s.startswith("You are the client panel in a live"):
            return Stream(text_msg(json.dumps({"interject": True, "persona_id": "finance", "text": "What does that cost per demo?", "target": "room", "about_slide": 2, "reason": "no numbers"})))
        if s.startswith("You are the client panel at the end"):
            return Stream(text_msg(json.dumps({"questions": [{"persona_id": "skeptic", "text": "Why should we believe the ten week timeline?", "about_slide": 3}, {"persona_id": "operations", "text": "Who covers a sick chef in Perth?", "about_slide": 2}]})))
        return Stream(tool_msg({
            "brief_coverage": {"summary": "Mostly covered.", "items": [{"req_id": "R1", "met": "partial", "evidence": "said 15 chefs", "note": "no timeline"}, {"req_id": "R3", "met": "no", "evidence": "", "note": "never mentioned"}]},
            "gut_feel": {"score": 72, "rationale": "Warm and credible."},
            "question_handling": {"items": [{"asker": "Graham", "question": "What does that cost per demo?", "answer_summary": "Deferred", "kind": "live", "rating": 60, "note": "ok"}]},
            "slides": [{"n": 1, "verdict": "clear", "rating": 85, "note": "good open"}, {"n": 2, "verdict": "waffled", "rating": 45, "note": "long"}, {"n": 3, "verdict": "ok", "rating": 70, "note": ""}],
            "win_likelihood": {"panel_read_pct": 58, "panel_rationale": "Close, needs numbers."},
            "top_actions": ["Add a cost per demo", "Tighten slide 2"],
            "presenters": [{"name": "Anthony", "slides": "1-3", "strengths": ["Warm"], "improve": ["Be concise"]}]}))
m.get_client = lambda: Fake()

raw = {"deck_summary": "A summary.", "requirements": [
  {"id": "R1", "text": "Recruit 15 chefs", "category": "people", "priority": "must", "ignored": False},
  {"id": "R3", "text": "Weekly report", "category": "reporting", "priority": "must", "ignored": False}],
  "slides": [{"n": i, "purpose": "p", "key_message": f"key {i}", "pacing": ["quick", "weighty", "normal"][i - 1], "pacing_reason": "r", "covers": ["R1"] if i == 2 else [], "evidence_strength": "ok", "evidence_note": "", "likely_questions": []} for i in (1, 2, 3)],
  "unanswered_requirements": [], "inconsistencies": []}
m.run_preread = lambda rfp_text, slides, settings, client=None, debug_path=None, visuals=None: normalise(raw, len(slides))
c = TestClient(m.app)
settings = {"title": "Browser test pitch", "channel": "x", "difficulty": 3, "interjection": 10, "ignore_topics": "", "guidance": "",
            "range": {"from": None, "to": None}, "personas": STARTERS, "presenters": [{"name": "Anthony", "from": 1, "to": 3}]}
deck, rfp = Path(sys.argv[2]), Path(sys.argv[3])
r = c.post("/api/sessions", data={"settings": json.dumps(settings), "rfp_text": ""}, files={"deck": (deck.name, open(deck, "rb")), "rfp_file": (rfp.name, open(rfp, "rb"))})
sid = r.json()["id"]
sess = m._read(sid); sess["slides"] = sess["slides"][:3]; m._write(sid, sess)
(m._dir(sid) / "preread.json").write_text(json.dumps(normalise(raw, 3)))
print("SESSION", sid, flush=True)
uvicorn.run(m.app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
