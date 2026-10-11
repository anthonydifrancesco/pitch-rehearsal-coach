"""Server-side rehearsal tests with a FAKE Claude client (no key, no network)."""
import json, sys, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
import app.main as m
from app import rehearse as R
from app.preread import normalise
from app.personas import STARTERS

calls = []

class Stream:
    def __init__(self, msg): self.msg = msg
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get_final_message(self): return self.msg

def text_msg(t): return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=t)], usage=None, stop_reason="end_turn")
def tool_msg(d): return types.SimpleNamespace(content=[types.SimpleNamespace(type="tool_use", input=d)], usage=None, stop_reason="tool_use")

class FakeClient:
    def __init__(self): self.messages = self
    def stream(self, **kw):
        calls.append(kw)
        sysm = kw["system"]
        if sysm.startswith("You are a senior new-business adviser"):
            return Stream(tool_msg(json.loads('''{"summary": "Mostly met the brief.", "biggest_gaps": ["Perth cover unclear"], "next_steps": ["Send case studies"],
 "requirements": [{"req_id": "R1", "deck": {"strength": "strong", "slides": [2, 9], "note": "slide 2 says 15 chefs"}, "conversation": {"status": "delivered", "evidence": "we recruit fifteen chefs", "speaker": "Anthony", "note": ""}, "relevance": "pitch", "recommendation": "None needed"},
                  {"req_id": "R3", "deck": {"strength": "thin", "slides": [3], "note": "vague"}, "conversation": {"status": "not_discussed"}, "relevance": "contract", "recommendation": "Add reporting slide"}],
 "themes": [{"name": "Store coverage", "req_ids": ["R1", "R9"], "summary": "Stores covered."}],
 "client_questions": [{"asker": "Sam", "question": "How do you cover a dropout in Perth?", "kind": "question", "slide": 2, "req_id": "R1", "answered": "yes", "answer_summary": "Bench of backups", "quality": "strong", "note": "", "follow_up": "Send bench numbers"},
                      {"asker": "Sam", "question": "junk", "answered": "weird"}],
 "commitments": [{"what": "Send case studies", "who": "Anthony", "due": "Friday"}]}''')))
        if sysm.startswith("You are preparing a recorded transcript"):
            return Stream(tool_msg({"runs": [
                {"from_seg": 1, "to_seg": 1, "role": "chatter", "slide": 0},
                {"from_seg": 2, "to_seg": 3, "role": "presenter", "slide": 1, "speaker": "Anthony"},
                {"from_seg": 4, "to_seg": 4, "role": "client", "slide": 1, "speaker": "Sam"},
                {"from_seg": 5, "to_seg": 99, "role": "presenter", "slide": 2}], "qa_from_seg": 0}))
        if sysm.startswith("You are the client panel in a live pitch rehearsal. A panel"):
            return Stream(text_msg(json.dumps({"clarifying": "do you mean" in kw["messages"][0]["content"][1]["text"], "persona_id": "Graham", "text": "The whole term."})))
        if sysm.startswith("You are the client panel in a live"):
            return Stream(text_msg('Sure:\n```json\n{"interject": true, "persona_id": "Graham", "text": "What does that cost per demo?", "target": "room", "about_slide": 3, "reason": "no numbers"}\n```'))
        if sysm.startswith("You are the client panel at the end"):
            return Stream(text_msg(json.dumps({"questions": [{"persona_id": "finance", "text": "q1?", "about_slide": 2}, {"persona_id": "ghost", "text": "dropped"}, {"persona_id": "skeptic", "text": "q2?"}]})))
        return Stream(tool_msg({
            "brief_coverage": {"score": 99, "items": [{"req_id": "R1", "met": "partial", "evidence": "said 15 chefs", "note": "n"}, {"req_id": "R2", "met": "yes", "evidence": "x", "note": "ignored req, must be dropped"}]},
            "gut_feel": {"score": 70, "rationale": "ok"},
            "question_handling": {"score": 10, "items": [{"asker": "Graham", "question": "q", "answer_summary": "a", "rating": 80, "note": "n"}, {"asker": "Daniel", "question": "q2", "answer_summary": "a2", "rating": "60", "note": "n"}]},
            "slides": json.dumps([{"n": 1, "verdict": "skipped", "rating": 0, "note": "quick title slide"}, {"n": 2, "verdict": "clear", "rating": 90, "note": "good"}]),
            "win_likelihood": {"panel_read_pct": 55, "panel_rationale": "r"},
            "top_actions": ["Add numbers", "Cut slide 5"],
            "presenters": [{"name": "Anthony", "slides": "1-3", "strengths": "Clear and warm", "improve": ["Slow down"]}],
        }))

raw_pr = {"deck_summary": "s",
  "requirements": [{"id": "R1", "text": "Recruit 15 chefs", "category": "people", "priority": "must", "ignored": False},
                   {"id": "R2", "text": "Rate card", "category": "commercial", "priority": "must", "ignored": True},
                   {"id": "R3", "text": "Weekly report", "category": "reporting", "priority": "must", "ignored": False}],
  "slides": [{"n": 1, "purpose": "open", "key_message": "k", "pacing": "quick", "pacing_reason": "r", "covers": [], "evidence_strength": "ok", "evidence_note": "", "likely_questions": []},
             {"n": 2, "purpose": "proof", "key_message": "k", "pacing": "weighty", "pacing_reason": "r", "covers": ["R1"], "evidence_strength": "thin", "evidence_note": "vague", "likely_questions": [{"question": "How?", "persona_hint": "skeptic"}]},
             {"n": 3, "purpose": "plan", "key_message": "k", "pacing": "normal", "pacing_reason": "r", "covers": ["R3"], "evidence_strength": "strong", "evidence_note": "", "likely_questions": []}],
  "unanswered_requirements": [{"req_id": "R3", "note": "n"}], "inconsistencies": [{"description": "d", "slides": [2]}]}

# ---- pure functions
assert R.gate(0, [], 100) == "off" and R.gate(5, [], 20) == "warmup" and R.gate(5, [], 100) is None
assert R.gate(5, [90], 120) == "min_gap" and R.gate(5, [10, 50, 90, 130, 170], 400) == "cap" and R.gate(10, [100], 140) is None
assert R.qa_count({"personas": STARTERS}) == 9 and R.qa_count({"personas": []}) == 3
assert R.live_thinking("claude-haiku-5-5") == {"type": "disabled"} and R.live_thinking("claude-sonnet-5-5") is None
assert R.parse_json if hasattr(R, "parse_json") else True

# ---- app + session fixture
c = TestClient(m.app)
deck = Path(sys.argv[1]); rfp = Path(sys.argv[2])
settings = {"title": "T", "channel": "x", "difficulty": 3, "interjection": 5, "ignore_topics": "pricing",
            "range": {"from": None, "to": None}, "personas": STARTERS, "presenters": [{"name": "Anthony", "from": 1, "to": 3}]}
r = c.post("/api/sessions", data={"settings": json.dumps(settings), "rfp_text": ""}, files={"deck": (deck.name, open(deck, "rb")), "rfp_file": (rfp.name, open(rfp, "rb"))})
sid = r.json()["id"]; n_real = r.json()["slide_count"]
sess = m._read(sid)
sess["slides"] = sess["slides"][:3]; m._write(sid, sess)           # shrink to the 3-slide fake preread
(m._dir(sid) / "preread.json").write_text(json.dumps(normalise(raw_pr, 3)))
sess = m._with_preread(sid)
ctx = R.build_context(sess, sess["preread"])
assert "PANEL MEMBERS" in ctx and "R2 [must] (OUT OF SCOPE)" in ctx and "3. " in ctx and "KNOWN GAPS" in ctx

# ---- live turn: gating (no model call), then a real interjection with name->id mapping and fenced JSON
fc = FakeClient(); n0 = len(calls)
assert R.live_turn(fc, ctx, sess, {"slide": 2, "elapsed": 10, "asked": [], "recent": []}) == {"interject": False, "gated": "warmup"}
assert len(calls) == n0
out = R.live_turn(fc, ctx, sess, {"slide": 2, "elapsed": 200, "asked": [], "recent": [{"type": "speech", "t": 150, "slide": 2, "text": "we deliver", "speaker": "Anthony"}]})
assert out["interject"] and out["persona_id"] == "finance" and out["about_slide"] == 3, out
kw = calls[-1]; assert kw["model"] == R.LIVE_MODEL and kw["thinking"] == {"type": "disabled"} and kw["messages"][0]["content"][0]["cache_control"]
assert "we deliver" in kw["messages"][0]["content"][1]["text"]
# not ready: slide only just shown / too few words -> no model call, no interjection
n1 = len(calls)
g = R.live_turn(fc, ctx, sess, {"slide": 2, "elapsed": 200, "asked": [], "recent": [], "slide_dwell": 8, "slide_words": 120})
assert g == {"interject": False, "gated": "slide_not_ready"} and len(calls) == n1
assert R.live_turn(fc, ctx, sess, {"slide": 2, "elapsed": 200, "asked": [], "recent": [], "slide_dwell": 90, "slide_words": 10})["gated"] == "slide_not_ready"
assert R.slide_ready("weighty", 44, 200) is False and R.slide_ready("weighty", 46, 91) and R.slide_ready("quick", 16, 26)
assert R.live_turn(fc, ctx, sess, {"slide": 2, "elapsed": 200, "asked": [], "recent": [], "slide_dwell": 60, "slide_words": 100})["interject"]
# clarifying question back from the presenter
rp = R.reply_turn(fc, ctx, sess, {"persona_id": "finance", "question": "What does it cost?", "said": "do you mean per demo or total?", "slide": 2, "recent": []})
assert rp == {"clarifying": True, "persona_id": "finance", "text": "The whole term."}, rp
assert R.reply_turn(fc, ctx, sess, {"persona_id": "finance", "question": "q", "said": "we charge per demo", "slide": 2, "recent": []}) == {"clarifying": False}
# level 0 holds everything for the end
s0 = dict(sess); s0["settings"] = dict(settings, interjection=0)
assert R.live_turn(fc, ctx, s0, {"slide": 2, "elapsed": 900, "asked": [], "recent": []})["gated"] == "off"

# ---- QA
qs = R.qa_questions(fc, ctx, sess, [])
assert [q["persona_id"] for q in qs] == ["finance", "skeptic"] and calls[-1]["thinking"] == {"type": "between_tools"}

# ---- timing: 3 slides, panel talks 20s on slide 2, qa words don't count
ev = [{"type": "slide", "t": 0, "slide": 1}, {"type": "speech", "t": 5, "slide": 1, "text": "hello everyone welcome", "phase": "pitch"},
      {"type": "slide", "t": 30, "slide": 2}, {"type": "panel", "t": 60, "persona_id": "finance", "text": "q", "kind": "live", "dur": 20},
      {"type": "speech", "t": 70, "slide": 2, "text": " ".join(["word"] * 120), "phase": "pitch"},
      {"type": "slide", "t": 130, "slide": 3}, {"type": "speech", "t": 150, "slide": 3, "text": "end", "phase": "pitch"},
      {"type": "speech", "t": 300, "slide": 3, "text": "qa answer words", "phase": "qa"}]
rows = R.timing_rows(sess, sess["preread"], ev)
assert [r["seconds"] for r in rows] == [30, 80, 20], rows           # slide 2: 100s - 20s panel
assert rows[1]["words"] == 120 and rows[1]["wpm"] == 90 and rows[2]["words"] == 1 and rows[0]["pacing"] == "quick"

# ---- score normalisation
sc = R.normalise_score(tool_msg({}).content[0].input, rows, sess, sess["preread"], 5) if False else None
raw_score, _ = __import__("app.llm", fromlist=["call_tool"]).call_tool(fc, R.SCORE_MODEL, R.SCORE_SYSTEM, R.SCORE_TOOL, [{"role": "user", "content": "x"}], 100)
sc = R.normalise_score(raw_score, rows, sess, sess["preread"], 5.0)
items = {i["req_id"]: i["met"] for i in sc["brief_coverage"]["items"]}
assert items == {"R1": "partial", "R3": "no"}, items                # R2 out of scope dropped, R3 unassessed -> no
assert sc["brief_coverage"]["score"] == 25                          # (0.5 + 0) / 2 recomputed, model's 99 ignored
assert sc["question_handling"]["score"] == 70                       # mean of 80 and "60"->60
assert sc["delivery_score"] == 70                                   # quick+skipped slide 1 excluded; mean(90, default 50 for slide 3)
assert sc["total"] == 48 and sc["win_likelihood"]["calculated_pct"] == 54 and sc["win_likelihood"]["panel_read_pct"] == 55
assert sc["top_actions"] == ["Add numbers", "Cut slide 5"] and sc["presenters"][0]["strengths"] == ["Clear and warm"]
assert [x["verdict"] for x in sc["slides"]] == ["skipped", "clear", "ok"]

# ---- endpoints end to end (client patched)
m.get_client = lambda: fc
rid = c.post(f"/api/sessions/{sid}/rehearsals").json()["rid"]
assert c.put(f"/api/sessions/{sid}/rehearsals/{rid}/log", json={"events": ev}).json()["events"] == len(ev)
t = c.post(f"/api/sessions/{sid}/rehearsals/{rid}/turn", json={"slide": 2, "elapsed": 300, "asked": [], "recent": ev[:3]}).json()
assert t["interject"] and t["persona_id"] == "finance"
assert c.post(f"/api/sessions/{sid}/rehearsals/{rid}/qa", json={"events": ev}).json()["questions"][0]["text"] == "q1?"
out = c.post(f"/api/sessions/{sid}/rehearsals/{rid}/score", json={"events": ev}).json()
assert out["total"] == 48
assert c.get(f"/api/sessions/{sid}/rehearsals/{rid}").json()["score"]["total"] == 48
assert c.get(f"/api/sessions/{sid}/rehearsals").json()[0]["total"] == 48
d = c.get(f"/api/sessions/{sid}/rehearsals/{rid}/export.docx"); assert d.status_code == 200 and d.content[:2] == b"PK"
d2 = c.get(f"/api/sessions/{sid}/export.docx"); assert d2.status_code == 200 and d2.content[:2] == b"PK"
assert c.get("/api/slide").json()["available"] is False             # not Windows here
assert c.get("/api/sessions/zzzz/rehearsals").status_code == 400
assert c.get(f"/api/sessions/{sid}/rehearsals/../x").status_code in (400, 404)
print("REHEARSAL OK")

# ---- past pitch from a transcript
from app import pastpitch as PP
tr = ("[00:00:05] Anthony: Thanks everyone for having us today, great to be here, we are really excited about this program. " * 1
      + "\n[00:00:40] Anthony: " + "We recruit and train fifteen chefs across seven states and we manage them through a weekly report. " * 6
      + "\n[00:03:10] Anthony: " + "Our approach is built on proven training that we have used with other brands for many years now. " * 5
      + "\n[00:05:00] Sam: How do you handle a chef who drops out in Perth at short notice? Who covers that and how fast?"
      + "\n[00:05:30] Anthony: " + "We hold a bench of trained backups in every state and can have cover within forty eight hours. " * 5)
p1 = PP.parse_transcript(tr)
assert p1["has_times"] and p1["speakers"] == ["Anthony", "Sam"], p1["speakers"]
assert p1["segments"][-1]["t"] > 300 and all("t" in s for s in p1["segments"])
p2 = PP.parse_transcript("Me: hello there\nThem: hi how are you\nMe: " + "words and more words. " * 40)
assert not p2["has_times"] and p2["speakers"] == ["Me", "Them"] and p2["segments"][-1]["t"] > 0
p3 = PP.parse_transcript("WEBVTT\n\n1\n00:00:01.000 --> 00:00:04.000\nJust some text without labels\n\n2\n00:00:09.000 --> 00:00:12.000\nAnd more text.")
assert p3["has_times"] and len(p3["segments"]) >= 1
mp = PP.map_transcript(fc, ctx, p1, 3, "Anthony", None)
ev = PP.build_events(p1, mp, "Presenter", 3)
kinds = [e["type"] for e in ev]
assert "slide" in kinds and "speech" in kinds and "panel" in kinds, kinds
pn = [e for e in ev if e["type"] == "panel"][0]
assert pn["speaker_name"] == "Sam" and pn["persona_id"] == "client"
assert "Sam" in R.script_lines(ev, settings, {1: "a", 2: "b"})
r = c.post(f"/api/sessions/{sid}/assess", data={"transcript_text": tr, "our_side": "Anthony", "outcome": "Lost"})
assert r.status_code == 200, r.text
rid2 = r.json()["rid"]; assert not r.json()["timing_estimated"]
sc = c.post(f"/api/sessions/{sid}/rehearsals/{rid2}/score", json={}).json()
assert sc["total"] is not None
assert "REAL PITCH" in [k for k in calls if k["system"].startswith("You are an experienced")][-1]["messages"][0]["content"][1]["text"]
got = c.get(f"/api/sessions/{sid}/rehearsals/{rid2}").json(); assert got["past"]["outcome"] == "Lost"
lst = c.get(f"/api/sessions/{sid}/rehearsals").json(); assert any(x["past"] and x["outcome"] == "Lost" for x in lst)
assert c.get(f"/api/sessions/{sid}/rehearsals/{rid2}/export.docx").content[:2] == b"PK"
assert c.post(f"/api/sessions/{sid}/assess", data={"transcript_text": "too short"}).status_code == 400
print("PAST PITCH OK")

# ---- untimed transcript: no delivery score, weights renormalised, labels respected
tr2 = "Me: " + "We recruit and train fifteen chefs across seven states and manage them weekly. " * 8 + "\nThem: How do you cover a dropout in Perth?\nMe: " + "We hold a bench of backups in every state ready within two days. " * 8
r = c.post(f"/api/sessions/{sid}/assess", data={"transcript_text": tr2})
assert r.status_code == 200 and r.json()["timing_estimated"] and r.json()["minutes"] is None, r.text
rid3 = r.json()["rid"]
sc3 = c.post(f"/api/sessions/{sid}/rehearsals/{rid3}/score", json={}).json()
assert sc3["delivery_score"] is None and sc3["meta"]["timed"] is False and abs(sum(sc3["meta"]["weights"].values()) - 1) < 0.01, sc3["meta"]
assert all(x["seconds"] is None for x in sc3["slides"])
last = [k for k in calls if k["system"].startswith("You are an experienced")][-1]["messages"][0]["content"][1]["text"]
assert "NO usable timestamps" in last and "WORDS SPOKEN PER SLIDE" in last and "s on screen" not in last
assert c.get(f"/api/sessions/{sid}/rehearsals/{rid3}/export.docx").content[:2] == b"PK"
print("UNTIMED OK")

# ---- post-pitch review
rv = c.post(f"/api/sessions/{sid}/rehearsals/{rid3}/review").json()
cnt = rv["counts"]
assert cnt["requirements"] == 2 and cnt["covered"] == 1 and cnt["gap"] == 1, cnt          # R2 is out of scope; R3 thin + not discussed = gap
assert cnt["conv_addressed"] == 1 and cnt["deck_supported"] == 1 and cnt["pitch_gaps"] == 0 and cnt["contract_gaps"] == 1, cnt   # R3's gap is contract-level
assert cnt["by_priority"]["must"]["total"] == 2 and cnt["by_priority"]["must"]["covered"] == 1
th = rv["themes"]; assert th[0]["name"] == "Store coverage" and th[0]["req_ids"] == ["R1"] and th[1]["req_ids"] == ["R3"] and th[1]["name"] == "Other requirements", th    # R9 unknown dropped; R3 unplaced -> Other
assert th[0]["status"] == "strong" and th[1]["status"] == "weak"
assert rv["requirements"][0]["deck"]["slides"] == [2] and rv["requirements"][0]["verdict"] == "covered"   # slide 9 doesn't exist in the 3-slide fake
assert rv["client_questions"][1]["answered"] == "no" and cnt["q_yes"] == 1 and cnt["questions"] == 2
assert "senior new-business adviser" in calls[-1]["system"] and "do not comment on timing" in calls[-1]["messages"][0]["content"][1]["text"]
assert "CLIENT (" in calls[-1]["messages"][0]["content"][1]["text"]
assert c.get(f"/api/sessions/{sid}/rehearsals/{rid3}/review.docx").content[:2] == b"PK"
assert any(x["review"] for x in c.get(f"/api/sessions/{sid}/rehearsals").json())
assert c.post(f"/api/sessions/{sid}/rehearsals/{rid}/review").status_code == 400    # a live rehearsal can't be reviewed
from app.review import verdict
assert verdict("strong", "delivered") == "covered" and verdict("thin", "delivered") == "conversation_only" and verdict("partial", "not_discussed") == "deck_only" and verdict("none", "not_discussed") == "gap" and verdict("partial", "delivered") == "covered" and verdict("strong", "partial") == "partly"
print("REVIEW OK")
