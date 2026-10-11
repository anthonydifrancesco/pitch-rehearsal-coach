"""Password gate, fail-closed on Render, DATA_DIR, and the remote slide helper. No AI, no network."""
import base64, os, sys, tempfile, time
from pathlib import Path
tmp = tempfile.mkdtemp()
os.environ["DATA_DIR"] = tmp
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
import app.main as m

c = TestClient(m.app)
assert m.DATA == Path(tmp) and (Path(tmp) / "sessions").exists()          # data lives where DATA_DIR says
auth = lambda pw: {"Authorization": "Basic " + base64.b64encode(f"anyone:{pw}".encode()).decode()}

# open locally when nothing is set
os.environ.pop("APP_PASSWORD", None); os.environ.pop("RENDER", None)
assert c.get("/api/config").status_code == 200

# on Render with no password: locked, but the health check still passes
os.environ["RENDER"] = "true"
assert c.get("/api/config").status_code == 503 and c.get("/").status_code == 503
assert c.get("/healthz").status_code == 200

# with a password: no/wrong password is refused with a sign-in prompt, the right one gets in (every route, incl. static)
os.environ["APP_PASSWORD"] = "s3cret: with colon"
r = c.get("/api/config"); assert r.status_code == 401 and "Basic" in r.headers["www-authenticate"]
assert c.get("/api/config", headers=auth("nope")).status_code == 401
assert c.get("/", headers={"Authorization": "Basic !!!"}).status_code == 401
for path in ("/api/config", "/", "/rehearse", "/static/index.html"):
    assert c.get(path, headers=auth("s3cret: with colon")).status_code == 200, path
assert c.post("/api/slide", json={"slide": 2}).status_code == 401
assert c.get("/healthz").status_code == 200

# remote slide helper (Linux here, so local detection is unavailable)
H = auth("s3cret: with colon")
r = c.get("/api/slide", headers=H).json(); assert r["available"] is False
assert c.post("/api/slide", headers=H, json={"slide": 4, "total": 20, "name": "deck.pptx", "mode": "slideshow"}).status_code == 200
r = c.get("/api/slide", headers=H).json()
assert r == {"slide": 4, "total": 20, "name": "deck.pptx", "mode": "slideshow", "available": True}, r
assert c.post("/api/slide", headers=H, json={"nonsense": 1}).status_code == 400
m._helper["t"] = time.time() - 30                                          # helper went quiet
r = c.get("/api/slide", headers=H).json(); assert r["available"] is False and "stopped" in r["reason"]
print("HOSTING OK")
