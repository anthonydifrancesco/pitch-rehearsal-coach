"""History API, CSV and page, plus saving of rehearsal details. No AI, no network."""
import json, os, sys, tempfile
from pathlib import Path
tmp = tempfile.mkdtemp()
os.environ["DATA_DIR"] = tmp
os.environ.pop("APP_PASSWORD", None); os.environ.pop("RENDER", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
import app.main as m

c = TestClient(m.app)
assert c.get("/api/history").json() == []

def mk(sid, title, client, n=5):
    sess = {"id": sid, "created": "2026-01-01 00:00:00", "settings": {"title": title, "client": client, "channel": "Appliances / home"},
            "slides": [{"n": i + 1, "title": f"S{i+1}", "text": ""} for i in range(n)], "rfp_text": ""}
    (m.SESSIONS / sid).mkdir(); m._write(sid, sess)
mk("aaaaaaaaaa", "AEG Demo 2027", "AEG"); mk("bbbbbbbbbb", "=cmd Pitch", "")

def reh(sid, rid, started, ev, score, **kw):
    m._reh_write(sid, rid, {"id": rid, "started": started, "events": ev, "qa": [], "score": score, **kw})
sp = lambda who, t, ph="pitch": {"type": "speech", "t": t, "speaker": who, "phase": ph, "text": "x"}
sc = lambda tot, qh, pan, calc, **meta: {"total": tot, "brief_coverage": {"score": 70}, "gut_feel": {"score": 60}, "question_handling": {"score": qh},
                                       "delivery_score": 55, "win_likelihood": {"panel_read_pct": pan, "calculated_pct": calc}, "meta": meta}
reh("aaaaaaaaaa", "0000000001", "2026-03-01 10:00", [sp("Anna", 5), sp("Ben", 650)], sc(61, 58, 40, 45, minutes=11))
reh("aaaaaaaaaa", "0000000002", "2026-03-08 10:00", [sp("Anna", 5)], sc(72, 66, 55, 52, minutes=9), meta={"session_type": "quiet", "presenter": "Anna", "range": [1, 3]})
reh("aaaaaaaaaa", "0000000003", "2026-03-09 10:00", [sp("Anna", 4, "qa")], sc(68, 68, 50, 50, qa_only=True), mode="qa_only")
reh("aaaaaaaaaa", "0000000099", "2026-03-10 10:00", [], None)
reh("bbbbbbbbbb", "0000000004", "2026-02-01 09:00", [sp("Cy", 30)], None)

rows = c.get("/api/history").json()
assert [r["rid"] for r in rows] == ["0000000003", "0000000002", "0000000001", "0000000004"], rows           # newest first, empty one skipped
by = {r["rid"]: r for r in rows}
assert by["0000000001"]["client"] == "AEG" and by["0000000001"]["presenters"] == ["Anna", "Ben"] and by["0000000001"]["minutes"] == 11 and by["0000000001"]["session_type"] == "full"
assert by["0000000001"]["range"] == [1, 5] and by["0000000001"]["win_panel"] == 40 and by["0000000001"]["win_calc"] == 45
assert by["0000000002"]["session_type"] == "quiet" and by["0000000002"]["range"] == [1, 3]
assert by["0000000003"]["session_type"] == "qa" and by["0000000003"]["coverage"] is None and by["0000000003"]["delivery"] is None
assert by["0000000004"]["client"] == "" and by["0000000004"]["total"] is None and by["0000000004"]["scored"] is False and by["0000000004"]["minutes"] == 0.5

# log meta is saved, and bad values are ignored
sid = "aaaaaaaaaa"; m._reh_write(sid, "0000000005", {"id": "0000000005", "started": "2026-04-01 10:00", "events": [], "qa": [], "score": None})
r = c.put(f"/api/sessions/{sid}/rehearsals/0000000005/log", json={"events": [sp("Dee", 3)], "meta": {"session_type": "bogus", "presenter": "Dee", "range": [2, 4]}})
assert r.status_code == 200
assert m._reh_read(sid, "0000000005")["meta"] == {"session_type": "full", "presenter": "Dee", "range": [2, 4]}
c.put(f"/api/sessions/{sid}/rehearsals/0000000005/log", json={"events": [sp("Dee", 3)], "meta": {"session_type": "qa", "range": "x"}})
assert m._reh_read(sid, "0000000005")["meta"] == {"session_type": "qa", "presenter": "Dee", "range": [2, 4]}
c.put(f"/api/sessions/{sid}/rehearsals/0000000005/log", json={"events": [sp("Dee", 3)]})              # old clients send no meta
assert m._reh_read(sid, "0000000005")["meta"]["session_type"] == "qa"

# csv
r = c.get("/api/history.csv"); assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
lines = r.text.strip().splitlines()
assert lines[0].startswith("started,client,title") and len(lines) == 6, lines
assert "'=cmd Pitch" in r.text and "Anna; Ben" in r.text
# pages
assert "Rehearsal history" in c.get("/history").text
# pitch pack import keeps the client
assert '"client"' in Path(m.__file__).read_text().split("keys = (")[1][:120]
print("HISTORY OK")
