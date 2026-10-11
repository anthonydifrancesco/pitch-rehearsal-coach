"""End-to-end test with a FAKE Claude client (no API key, no network).
Usage: python tests/test_end_to_end.py <deck.pptx> <rfp.pdf>
Checks extraction, request construction, output normalisation and every HTTP endpoint."""
import json, sys, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
import app.main as m
from app import preread

captured = {}

class FakeStream:
    def __init__(self, n): self.n = n
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get_final_message(self):
        n = self.n
        raw = {
            "deck_summary": "fake summary",
            "requirements": [
                {"id": "R1", "text": "Recruit 15 chefs", "category": "people", "priority": "must", "ignored": False},
                {"id": "R2", "text": "Provide rate card", "category": "commercial", "priority": "must", "ignored": True},
                {"id": "R3", "text": "Weekly issue report", "category": "reporting", "priority": "must", "ignored": False},
                {"id": "R1", "text": "duplicate id should be dropped", "category": "x", "priority": "bogus", "ignored": False},
            ],
            # deliberately skip slide 2 and use bad enums on slide 3 to test normalise()
            "slides": [{"n": i, "purpose": f"p{i}", "key_message": "k", "pacing": "weighty" if i == 1 else "BAD",
                        "pacing_reason": "r", "covers": ["R1", "R999"] if i == 1 else [],
                        "evidence_strength": "strong" if i == 1 else "weird", "evidence_note": "e",
                        "likely_questions": [{"question": "q?", "persona_hint": "nope"}]}
                       for i in range(1, n + 1) if i != 2],
            "unanswered_requirements": [{"req_id": "R3", "note": "n"}, {"req_id": "R404", "note": "dropped"}],
            "inconsistencies": [{"description": "d", "slides": [1]}],
        }
        block = types.SimpleNamespace(type="tool_use", input=raw)
        usage = types.SimpleNamespace(input_tokens=1, output_tokens=2, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        return types.SimpleNamespace(content=[block], usage=usage, stop_reason="tool_use")

class ProseStream(FakeStream):
    def get_final_message(self):
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text="Here is my analysis...")],
                                     usage=None, stop_reason="end_turn")

class FakeClient:
    """First call answers in prose (to exercise the retry), later calls use the tool."""
    def __init__(self, n, prose_first=False): self.messages = self; self.n = n; self.calls = 0; self.prose_first = prose_first
    def stream(self, **kw):
        self.calls += 1; captured.update(kw)
        return ProseStream(self.n) if (self.prose_first and self.calls == 1) else FakeStream(self.n)

deck, rfp = sys.argv[1], sys.argv[2]
c = TestClient(m.app)

cfg = c.get("/api/config").json(); assert len(cfg["personas"]) == 5
settings = {"title": "AEG test", "channel": "Appliances / home", "ignore_topics": "pricing", "difficulty": 4}
r = c.post("/api/sessions", data={"settings": json.dumps(settings), "rfp_text": ""},
           files={"deck": (Path(deck).name, open(deck, "rb")), "rfp_file": (Path(rfp).name, open(rfp, "rb"))})
assert r.status_code == 200, r.text
sid, n = r.json()["id"], r.json()["slide_count"]; print("session", sid, "slides", n, "rfp chars", r.json()["rfp_chars"])

# bad inputs
assert c.post("/api/sessions", data={"settings": "{}", "rfp_text": "x"*300}, files={"deck": ("a.txt", b"x")}).status_code == 400
assert c.post("/api/sessions", data={"settings": json.dumps(settings), "rfp_text": "short"}, files={"deck": (Path(deck).name, open(deck, "rb"))}).status_code == 400
assert c.get("/api/sessions/../etc").status_code in (400, 404)

# run preread through the real code path with a fake client
sess = m._read(sid)
fc = FakeClient(n, prose_first=True)
res = preread.run_preread(sess["rfp_text"], sess["slides"], sess["settings"], client=fc)
assert fc.calls == 2, fc.calls
(m._dir(sid) / "preread.json").write_text(json.dumps(res))
assert len(res["slides"]) == n and res["missing_slides"] == [2]
assert res["slides"][1]["pacing"] == "normal" and res["slides"][1]["evidence_strength"] == "ok"
assert res["slides"][0]["covers"] == ["R1"], res["slides"][0]["covers"]
assert [r["id"] for r in res["requirements"]] == ["R1", "R2", "R3"]
assert [u["req_id"] for u in res["unanswered_requirements"]] == ["R3"]
assert res["slides"][0]["likely_questions"][0]["persona_hint"] == "skeptic"
msgs = captured["messages"][0]["content"]
assert msgs[0]["cache_control"] == {"type": "ephemeral"} and "<rfp>" in msgs[0]["text"] and "SLIDE 1" in msgs[0]["text"]
assert "pricing" in msgs[1]["text"] and "tool_choice" not in captured
print("prompt chars:", len(msgs[0]["text"]), "(~%d tokens)" % (len(msgs[0]["text"]) // 4))

# endpoints
g = c.get(f"/api/sessions/{sid}").json(); assert g["preread"] and "rfp_text" not in g
assert c.patch(f"/api/sessions/{sid}/pacing", json={"3": "quick"}).json() == {"3": "quick"}
assert c.patch(f"/api/sessions/{sid}/pacing", json={"3": None}).json() == {}
assert c.patch(f"/api/sessions/{sid}/pacing", json={"3": "slow"}).status_code == 400
assert c.put(f"/api/sessions/{sid}/settings", json=settings).json()["ok"]
assert any(s["id"] == sid for s in c.get("/api/sessions").json())
# no key -> clear error
import os; os.environ.pop("ANTHROPIC_API_KEY", None)
e = c.post(f"/api/sessions/{sid}/preread"); assert e.status_code == 400 and "ANTHROPIC_API_KEY" in e.text, e.text
assert c.get("/").status_code == 200

# messy model output: nested values as JSON strings, strings in lists, numbers as text
import json as _j
messy = {
    "deck_summary": ["not", "a", "string"],
    "requirements": _j.dumps([{"id": "R1", "text": "a", "category": "c", "priority": "must", "ignored": "false"}, "junk", 5]),
    "slides": _j.dumps([{"n": "1", "purpose": "p", "key_message": "k", "pacing": "quick", "pacing_reason": "r",
                         "covers": "R1, R9", "evidence_strength": "thin", "evidence_note": "e",
                         "likely_questions": _j.dumps([{"question": "q", "persona_hint": "finance"}, "oops"])}, "bad"]),
    "unanswered_requirements": ["R1 is missing", {"req_id": "R1", "note": "n"}],
    "inconsistencies": {"a": {"description": "d", "slides": "1, 3"}},
}
nm = preread.normalise(messy, 3)
assert nm["requirements"][0]["id"] == "R1" and nm["slides"][0]["covers"] == ["R1"], nm
assert nm["slides"][0]["likely_questions"][0]["persona_hint"] == "finance" and nm["slides"][0]["pacing"] == "quick"
assert nm["inconsistencies"] == [{"description": "d", "slides": [1, 3]}] and nm["missing_slides"] == [2, 3]
assert preread.normalise(_j.dumps(messy), 3)["slides"][0]["n"] == 1
for junk in ({}, {"slides": "garbage", "requirements": None}):
    assert len(preread.normalise(junk, 2)["slides"]) == 2
print("ALL OK")
