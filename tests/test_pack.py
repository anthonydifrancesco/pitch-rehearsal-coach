"""Pitch Pack round trip with NO AI client at all: import must work with zero API calls.
Usage: python tests/test_pack.py <deck.pptx> <rfp.pdf>"""
import json, sys, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
import app.main as m
from app import pack as PK, rehearse as R

# Any attempt to call the AI during import is a failure.
def _boom(*a, **k): raise AssertionError("import must not call the AI")
m.get_client = _boom

deck, rfp = sys.argv[1], sys.argv[2]
c = TestClient(m.app)

# --- a pack as an external tool would write it (loose on purpose: bad enums, a missing slide, no meta)
pack = {
    "format": "pitch-pack", "version": 1,
    "pitch": {"title": "Pack Pitch", "channel": "Pharmacy", "ignore_topics": "pricing", "guidance": "Be strict."},
    "slides": [{"title": f"Slide {i}"} for i in range(1, 5)],
    "analysis": {
        "deck_summary": "s",
        "requirements": [{"id": "R1", "text": "Recruit chefs", "category": "people", "priority": "must", "ignored": False},
                         {"id": "R2", "text": "Price list", "category": "commercial", "priority": "bogus", "ignored": True}],
        "slides": [{"n": 1, "purpose": "p", "key_message": "k", "pacing": "weighty", "pacing_reason": "r",
                    "covers": ["R1", "R404"], "evidence_strength": "strong", "evidence_note": "e",
                    "likely_questions": [{"question": "q?", "persona_hint": "nope"}]},
                   {"n": 3, "purpose": "p3", "key_message": "k3", "pacing": "WRONG"}],
        "unanswered_requirements": [], "inconsistencies": [],
    },
}

def imp(obj, settings=None, deck_path=None):
    files = {"pack": ("x.pitchpack.json", json.dumps(obj).encode())}
    if deck_path: files["deck"] = (Path(deck_path).name, open(deck_path, "rb"))
    return c.post("/api/packs/import", data={"settings": json.dumps(settings or {})}, files=files)

r = imp(pack, {"personas": c.get("/api/config").json()["personas"], "interjection": 6, "title": "", "presenters": []})
assert r.status_code == 200, r.text
j = r.json(); sid = j["id"]
assert j["slide_count"] == 4 and j["requirements"] == 2, j
assert any("slide(s) 2, 4" in w for w in j["warnings"]), j["warnings"]

s = c.get(f"/api/sessions/{sid}").json()
assert s["settings"]["title"] == "Pack Pitch" and s["settings"]["ignore_topics"] == "pricing"   # from the pack
assert s["settings"]["channel"] == "Pharmacy"
assert s["settings"]["interjection"] == 6 and s["settings"]["mode"] == "rehearse"                # from the form
pr = s["preread"]
assert [x["pacing"] for x in pr["slides"]] == ["weighty", "normal", "normal", "normal"]
assert pr["slides"][0]["covers"] == ["R1"] and pr["slides"][0]["likely_questions"][0]["persona_hint"] == "skeptic"
assert pr["requirements"][1]["priority"] == "should"

# the rehearsal can start straight away, and the live-panel context builds, with no AI
r = c.post(f"/api/sessions/{sid}/rehearsals"); assert r.status_code == 200, r.text
assert [x["title"] for x in r.json()["slides"]] == ["Slide 1", "Slide 2", "Slide 3", "Slide 4"]
sess = m._with_preread(sid); ctx = R.build_context(sess, sess["preread"])
assert "R1" in ctx and "Recruit chefs" in ctx and "Slide 1" in ctx

# a pack without the RFP can't re-run the pre-read, and says why
r = c.post(f"/api/sessions/{sid}/preread"); assert r.status_code == 400 and "Pitch Pack" in r.json()["detail"], r.text

# --- export, then re-import: same analysis comes back
r = c.get(f"/api/sessions/{sid}/pack.json?results=1"); assert r.status_code == 200
out = r.json()
assert out["format"] == "pitch-pack" and out["version"] == 1 and out["deck_fingerprint"]
assert len(out["slides"]) == 4 and "meta" not in out["analysis"]
r2 = imp(out); assert r2.status_code == 200, r2.text
pr2 = c.get(f"/api/sessions/{r2.json()['id']}").json()["preread"]
assert pr2["requirements"] == pr["requirements"] and [x["pacing"] for x in pr2["slides"]] == [x["pacing"] for x in pr["slides"]]

# pacing overrides are baked into the exported pack
c.patch(f"/api/sessions/{sid}/pacing", json={"2": "quick"})
out = c.get(f"/api/sessions/{sid}/pack.json").json()
assert out["analysis"]["slides"][1]["pacing"] == "quick"

# --- optional deck check: wrong slide count warns, right deck is quiet
r = imp(pack, deck_path=deck)           # the real deck has 6 slides, the pack says 4
assert r.status_code == 200 and any("slides but the pack" in w for w in r.json()["warnings"]), r.json()
from app.extract import extract_deck
real = extract_deck(open(deck, "rb").read())
good = dict(pack, slides=[{"title": s["title"], "text": s["text"]} for s in real], deck_fingerprint=PK.deck_fingerprint(real))
good["analysis"] = dict(pack["analysis"], slides=[dict(x, n=x["n"]) for x in pack["analysis"]["slides"]])
r = imp(good, deck_path=deck); assert r.status_code == 200, r.text
assert not any("differs" in w or "slides but" in w for w in r.json()["warnings"]), r.json()["warnings"]
bad = dict(good, deck_fingerprint="0000000000000000")
r = imp(bad, deck_path=deck); assert any("differs" in w for w in r.json()["warnings"]), r.json()

# --- rejects, in plain words
for obj, frag in [({"format": "other"}, "not a Pitch Pack"),
                  ({"format": "pitch-pack", "version": 9, "slides": [{}], "analysis": {}}, "version 9"),
                  ({"format": "pitch-pack", "version": 1, "slides": []}, "no slide list"),
                  ({"format": "pitch-pack", "version": 1, "slides": [{}], "analysis": {}}, "no analysis")]:
    r = imp(obj); assert r.status_code == 400 and frag in r.json()["detail"], (obj, r.text)
r = c.post("/api/packs/import", data={"settings": "{}"}, files={"pack": ("x.json", b"not json")})
assert r.status_code == 400 and "not valid JSON" in r.json()["detail"]
print("PACK OK")
