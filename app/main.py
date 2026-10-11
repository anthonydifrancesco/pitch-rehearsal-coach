"""Pitch Rehearsal Coach: local server (step 1: setup screen + pre-read)."""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .export import build_preread_docx, build_rehearsal_docx, build_review_docx
from .extract import extract_deck, extract_document
from . import rehearse as R
from . import pastpitch as PP
from . import pack as PK
from . import visuals as VIS
from . import tts as TTS
from . import review as RV
from .llm import get_client
from . import llm as LLM
from .personas import load_personas, reset_personas, sanitize, save_personas
from .slidewatch import current_slide
from .preread import DEFAULT_MODEL, PACING, run_preread

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("DATA_DIR") or (ROOT / "data"))
SESSIONS = DATA / "sessions"
STATIC = ROOT / "static"
ID_RE = re.compile(r"^[a-f0-9]{10}$")


def _load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()
SESSIONS.mkdir(parents=True, exist_ok=True)
LLM.USAGE_ROOT = SESSIONS
app = FastAPI(title="Pitch Rehearsal Coach")


# ----------------------------------------------------------------- hosting: password gate
# Locally nothing is set and the app is open on 127.0.0.1. On a host, set APP_PASSWORD: every request then needs it
# (the browser shows a sign-in box; any user name works). On Render (RENDER=true) a missing password fails closed.
@app.middleware("http")
async def password_gate(request, call_next):
    import base64
    import secrets

    if request.url.path == "/healthz":
        return Response("ok")
    pw = os.environ.get("APP_PASSWORD", "")
    if not pw:
        if os.environ.get("RENDER") == "true":
            return Response("APP_PASSWORD is not set on the server, so the app is locked.", status_code=503)
        return await call_next(request)
    sent = ""
    h = request.headers.get("authorization", "")
    if h.lower().startswith("basic "):
        try:
            sent = base64.b64decode(h[6:]).decode("utf-8", "replace").split(":", 1)[1]
        except Exception:
            sent = ""
    if secrets.compare_digest(sent.encode(), pw.encode()):
        return await call_next(request)
    return Response("Sign in required.", status_code=401, headers={"WWW-Authenticate": 'Basic realm="Pitch Rehearsal Coach"'})


def _dir(sid: str) -> Path:
    if not ID_RE.match(sid):
        raise HTTPException(400, "Bad session id")
    d = SESSIONS / sid
    if not d.exists():
        raise HTTPException(404, "Session not found")
    return d


def _read(sid: str) -> dict:
    return json.loads((_dir(sid) / "session.json").read_text(encoding="utf-8"))


def _write(sid: str, obj: dict) -> None:
    (_dir(sid) / "session.json").write_text(json.dumps(obj, indent=2), encoding="utf-8")


def _preread(sid: str) -> dict | None:
    p = _dir(sid) / "preread.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


@app.get("/api/config")
def config():
    return {
        "has_key": bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
        "model": os.environ.get("PREREAD_MODEL", DEFAULT_MODEL),
        "personas": load_personas(),
        "tts": TTS.provider(),
    }


@app.post("/api/tts")
async def tts(body: dict):
    """Neural voice for one line the panel says. 503 when no key is set (the page then uses browser voices)."""
    if not TTS.provider():
        raise HTTPException(503, "No text-to-speech key is set")
    persona = body.get("persona") if isinstance(body.get("persona"), dict) else {}
    try:
        audio = await run_in_threadpool(TTS.speak, str(body.get("text") or ""), persona, body.get("slot") or 0)
    except Exception as e:  # noqa: BLE001  the page falls back to browser voices
        raise HTTPException(502, f"Voice service failed: {type(e).__name__}")
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


@app.put("/api/personas")
async def put_personas(personas: List[dict]):
    clean = sanitize(personas)
    if not clean:
        raise HTTPException(400, "The panel needs at least one person")
    save_personas(clean)
    return {"ok": True, "personas": clean}


@app.delete("/api/personas")
async def delete_personas():
    reset_personas()
    return {"ok": True}


@app.get("/api/sessions")
def list_sessions():
    out = []
    for d in sorted(SESSIONS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        f = d / "session.json"
        if f.exists():
            s = json.loads(f.read_text(encoding="utf-8"))
            out.append({"id": s["id"], "title": s["settings"].get("title", ""), "created": s["created"],
                        "slides": len(s["slides"]), "has_preread": (d / "preread.json").exists(),
                        "mode": s["settings"].get("mode", "rehearse")})
    return out


@app.post("/api/sessions")
async def create_session(
    settings: str = Form(...),
    deck: UploadFile = File(...),
    rfp_file: Optional[UploadFile] = File(None),
    rfp_text: str = Form(""),
    deck_pdf: Optional[UploadFile] = File(None),
    read_visuals: str = Form(""),
):
    try:
        cfg = json.loads(settings)
    except json.JSONDecodeError:
        raise HTTPException(400, "Settings were not valid JSON")
    if isinstance(cfg.get("personas"), list):
        cfg["personas"] = sanitize(cfg["personas"])
    if not deck.filename or not deck.filename.lower().endswith(".pptx"):
        raise HTTPException(400, "Please attach the deck as a .pptx file")
    deck_bytes = await deck.read()
    try:
        slides = extract_deck(deck_bytes)
    except Exception as e:  # corrupt or password-protected files
        raise HTTPException(400, f"Could not read the deck: {e}")
    text = rfp_text.strip()
    if rfp_file is not None and rfp_file.filename:
        try:
            text = (text + "\n\n" + extract_document(rfp_file.filename, await rfp_file.read())).strip()
        except Exception as e:
            raise HTTPException(400, f"Could not read the RFP file: {e}")
    if len(text) < 200:
        raise HTTPException(400, "The RFP/scope looks empty. Paste it or upload a PDF/DOCX.")
    sid = uuid.uuid4().hex[:10]
    (SESSIONS / sid).mkdir()
    sess = {
        "id": sid, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "settings": cfg, "rfp_text": text, "slides": slides, "pacing_overrides": {},
    }
    _write(sid, sess)
    vis = {"images": 0, "source": "", "warning": ""}
    if read_visuals.lower() in ("1", "true", "on", "yes"):
        try:
            if deck_pdf is not None and deck_pdf.filename:
                pages = await run_in_threadpool(VIS.render_pdf, await deck_pdf.read())
                vis["source"] = "pdf"
                if len(pages) != len(slides):
                    vis["warning"] = f"The PDF has {len(pages)} pages but the deck has {len(slides)} slides, so images may not line up with slides."
                vis["images"] = VIS.save(_dir(sid) / "visuals", {i + 1: [b] for i, b in enumerate(pages)})
            else:
                pics = await run_in_threadpool(VIS.pptx_pictures, deck_bytes)
                vis["source"] = "pptx"
                vis["images"] = VIS.save(_dir(sid) / "visuals", pics)
                if not vis["images"]:
                    vis["warning"] = "No usable pictures were found inside the .pptx. Charts drawn in PowerPoint are read as data. For a full picture of each slide, also attach the deck as a PDF."
        except Exception as e:
            vis["warning"] = f"Could not read the slide visuals ({type(e).__name__}: {e}). Carrying on with text only."
    return {"id": sid, "slide_count": len(slides), "rfp_chars": len(text), "visuals": vis}


# ---- background jobs: long AI calls can outlive a single browser connection (proxies, redeploys), so the
# browser may ask for "?bg=1": the call runs server-side and the browser polls /api/jobs/{id}.
import asyncio
JOBS: Dict[str, dict] = {}


async def _maybe_bg(bg: int, fn, *args):
    if not bg:
        return await fn(*args)
    jid = uuid.uuid4().hex[:12]
    JOBS[jid] = {"state": "running", "t": time.time()}
    for k in [k for k, v in JOBS.items() if time.time() - v["t"] > 3600]:
        JOBS.pop(k, None)

    async def run():
        try:
            JOBS[jid].update(state="done", result=await fn(*args))
        except HTTPException as e:
            JOBS[jid].update(state="error", status=e.status_code, detail=e.detail)
        except Exception as e:  # noqa: BLE001
            JOBS[jid].update(state="error", status=500, detail=f"{type(e).__name__}: {e}")
    asyncio.get_running_loop().create_task(run())
    return JSONResponse({"job": jid}, status_code=202)


@app.get("/api/jobs/{jid}")
def job_status(jid: str):
    j = JOBS.get(jid)
    if not j:
        raise HTTPException(404, "That job is gone (the server may have restarted). Please run it again.")
    return {k: v for k, v in j.items() if k != "t"}


@app.post("/api/sessions/{sid}/preread")
async def do_preread(sid: str, bg: int = 0):
    return await _maybe_bg(bg, _do_preread, sid)


async def _do_preread(sid: str):
    sess = _read(sid)
    if len(sess.get("rfp_text", "")) < 200:
        raise HTTPException(400, "This session came from a Pitch Pack that doesn't include the RFP text, so the "
                                 "pre-read can't be re-run here. Re-run the analysis in the analysis tool and import a fresh pack.")
    LLM.track(sid, "preread")
    try:
        result = await run_in_threadpool(run_preread, sess["rfp_text"], sess["slides"], sess["settings"],
                                          None, _dir(sid) / "raw_preread.json", VIS.load(_dir(sid) / "visuals"))
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"The AI service call failed: {type(e).__name__}: {e}")
    (_dir(sid) / "preread.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


@app.get("/api/sessions/{sid}")
def get_session(sid: str):
    s = _read(sid)
    s.pop("rfp_text", None)  # large; fetch separately if ever needed
    s["preread"] = _preread(sid)
    return s


@app.get("/api/sessions/{sid}/export.docx")
def export_docx(sid: str):
    sess = _read(sid)
    pr = _preread(sid)
    if not pr:
        raise HTTPException(400, "Run the pre-read first")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", sess["settings"].get("title") or "pitch-review").strip("_") or "pitch-review"
    return Response(build_preread_docx(sess, pr),
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="{name}_pre-pitch-review.docx"'})


@app.patch("/api/sessions/{sid}/pacing")
async def patch_pacing(sid: str, overrides: Dict[str, Optional[str]]):
    sess = _read(sid)
    for k, v in overrides.items():
        if not k.isdigit():
            raise HTTPException(400, "Slide numbers only")
        if v is None:
            sess["pacing_overrides"].pop(k, None)
        elif v in PACING:
            sess["pacing_overrides"][k] = v
        else:
            raise HTTPException(400, f"Pacing must be one of {PACING}")
    _write(sid, sess)
    return sess["pacing_overrides"]


@app.put("/api/sessions/{sid}/settings")
async def put_settings(sid: str, settings: dict):
    sess = _read(sid)
    sess["settings"] = settings
    _write(sid, sess)
    return {"ok": True}


# ----------------------------------------------------------------- pitch packs
@app.post("/api/packs/import")
async def import_pack(
    pack: UploadFile = File(...),
    settings: str = Form("{}"),
    deck: Optional[UploadFile] = File(None),
):
    """Create a ready-to-rehearse session from a Pitch Pack. No AI call: the analysis is already in the pack.
    Optionally attach the deck (.pptx) too; it is only used to warn if the deck has changed since the analysis."""
    try:
        cfg = json.loads(settings or "{}")
        if isinstance(cfg.get("personas"), list):
            cfg["personas"] = sanitize(cfg["personas"])
    except json.JSONDecodeError:
        raise HTTPException(400, "Settings were not valid JSON")
    try:
        p = PK.read_pack(await pack.read())
    except ValueError as e:
        raise HTTPException(400, str(e))
    warnings = list(p["warnings"])
    slides = p["slides"]
    if deck is not None and deck.filename:
        if not deck.filename.lower().endswith(".pptx"):
            raise HTTPException(400, "Please attach the deck as a .pptx file")
        try:
            real = extract_deck(await deck.read())
        except Exception as e:
            raise HTTPException(400, f"Could not read the deck: {e}")
        if len(real) != len(slides):
            warnings.append(f"The deck has {len(real)} slides but the pack was made from a deck with {len(slides)}. "
                            "The analysis may no longer match. Re-export the pack from the analysis tool.")
        elif p["fingerprint"] and PK.deck_fingerprint(real) != p["fingerprint"]:
            warnings.append("The deck's text differs from the version this pack was analysed from. "
                            "The analysis may be out of date. Re-export the pack if the deck has changed.")
        if len(real) == len(slides):
            slides = real  # richer slide text, same numbering
    pitch = p["pitch"]
    # The pack supplies the pitch details; the form only overrides them where it has something to say.
    keys = ("title", "client", "channel", "ignore_topics", "guidance", "presenters")
    merged = {**{k: pitch[k] for k in keys if pitch.get(k)}, **{k: v for k, v in cfg.items() if k not in keys or v}}
    merged["mode"] = "rehearse"
    sid = uuid.uuid4().hex[:10]
    (SESSIONS / sid).mkdir()
    sess = {"id": sid, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "settings": merged, "rfp_text": p["rfp_text"],
            "slides": slides, "pacing_overrides": {}, "source": {"kind": "pitch-pack", **p["source"]}}
    _write(sid, sess)
    (_dir(sid) / "preread.json").write_text(json.dumps(p["preread"], indent=2), encoding="utf-8")
    return {"id": sid, "slide_count": len(slides), "requirements": len(p["preread"]["requirements"]), "warnings": warnings}


@app.get("/api/sessions/{sid}/pack.json")
def export_pack(sid: str, results: int = 0, rfp: int = 1):
    """Download this session's analysis as a Pitch Pack (optionally with rehearsal results and the RFP text)."""
    sess = _read(sid)
    pr = _preread(sid)
    if not pr:
        raise HTTPException(400, "Run the pre-read first")
    summaries = []
    rd = _dir(sid) / "rehearsals"
    if results and rd.exists():
        for f in sorted(rd.glob("*.json"), key=lambda p: p.stat().st_mtime):
            if f.name.count(".") > 1:  # raw_*.json debug files
                continue
            r = json.loads(f.read_text(encoding="utf-8"))
            if not r.get("past"):
                s = PK.rehearsal_summary(r)
                if s:
                    summaries.append(s)
    pack = PK.build_pack(sess, pr, summaries, include_rfp=bool(rfp))
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", sess["settings"].get("title") or "pitch").strip("_") or "pitch"
    return Response(json.dumps(pack, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}.pitchpack.json"'})



# ----------------------------------------------------------------- rehearsal
def _reh_file(sid: str, rid: str) -> Path:
    if not ID_RE.match(rid):
        raise HTTPException(400, "Bad rehearsal id")
    return _dir(sid) / "rehearsals" / (rid + ".json")


def _reh_read(sid: str, rid: str) -> dict:
    f = _reh_file(sid, rid)
    if not f.exists():
        raise HTTPException(404, "Rehearsal not found")
    return json.loads(f.read_text(encoding="utf-8"))


def _reh_write(sid: str, rid: str, obj: dict) -> None:
    f = _reh_file(sid, rid)
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def _with_preread(sid: str) -> dict:
    sess = _read(sid)
    pr = _preread(sid)
    if not pr:
        raise HTTPException(400, "Run the pre-read first")
    sess["preread"] = pr
    return sess


_helper = {"t": 0.0, "state": None}   # last slide report from the optional PowerShell helper on a PC with PowerPoint


@app.get("/api/slide")
def api_slide():
    local = current_slide()
    if local.get("available"):
        return local
    st = _helper["state"]
    if st and time.time() - _helper["t"] < 6:
        return {**st, "available": True, "mode": st.get("mode") or "helper"}
    if st:
        return {"available": False, "reason": "The slide helper stopped reporting. Is it still running on the PC with PowerPoint?"}
    return local


@app.post("/api/slide")
async def post_slide(body: dict):
    """Called every second or so by tools/slide_helper.ps1 running next to PowerPoint."""
    try:
        _helper["state"] = {"slide": int(body["slide"]), "total": int(body["total"]) if body.get("total") else None,
                            "name": str(body.get("name", ""))[:200], "mode": str(body.get("mode", "helper"))[:20]}
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "Expected {slide, total, name, mode}")
    _helper["t"] = time.time()
    return {"ok": True}


@app.post("/api/sessions/{sid}/rehearsals")
async def start_rehearsal(sid: str):
    sess = _with_preread(sid)
    rid = uuid.uuid4().hex[:10]
    st = sess["settings"]
    _reh_write(sid, rid, {"id": rid, "started": time.strftime("%Y-%m-%d %H:%M"), "events": [], "qa": [], "score": None})
    total = len(sess["slides"])
    a, b = R.slide_range(st, total)
    ov = sess.get("pacing_overrides", {})
    slides = [{"n": s["n"], "title": sess["slides"][s["n"] - 1]["title"], "pacing": ov.get(str(s["n"])) or s["pacing"],
               "purpose": s["purpose"], "key_message": s["key_message"]} for s in sess["preread"]["slides"]]
    return {"rid": rid, "settings": st, "slides": slides, "range": [a, b], "ready_scale": R.READY_SCALE}


@app.put("/api/sessions/{sid}/rehearsals/{rid}/log")
async def save_log(sid: str, rid: str, body: dict):
    reh = _reh_read(sid, rid)
    reh["events"] = body.get("events", [])
    m = body.get("meta")
    if isinstance(m, dict):
        old = reh.get("meta") or {}
        st = m.get("session_type")
        rng = m.get("range")
        reh["meta"] = {
            "session_type": st if st in ("full", "quiet", "qa") else old.get("session_type", "full"),
            "presenter": str(m.get("presenter") or old.get("presenter") or "")[:80].strip(),
            "range": [int(rng[0]), int(rng[1])] if isinstance(rng, list) and len(rng) == 2 and all(isinstance(x, (int, float)) for x in rng) else old.get("range"),
        }
    _reh_write(sid, rid, reh)
    return {"ok": True, "events": len(reh["events"])}


@app.post("/api/sessions/{sid}/assess")
async def assess_past(
    sid: str,
    transcript_file: Optional[UploadFile] = File(None),
    transcript_text: str = Form(""),
    our_side: str = Form(""),
    outcome: str = Form(""),
    notes: str = Form(""),
    label: str = Form(""),
):
    """Step 1 of assessing a real, past pitch: read the transcript and line it up with the slides.
    The caller then POSTs .../rehearsals/{rid}/score (no body needed) to get the assessment."""
    sess = _with_preread(sid)
    text = transcript_text.strip()
    if transcript_file is not None and transcript_file.filename:
        try:
            text = (text + "\n" + extract_document(transcript_file.filename, await transcript_file.read())).strip()
        except Exception as e:
            raise HTTPException(400, f"Could not read the transcript file: {e}")
    parsed = PP.parse_transcript(text)
    if sum(len(s["text"].split()) for s in parsed["segments"]) < 150:
        raise HTTPException(400, "That transcript looks too short to assess. Paste the whole meeting transcript.")
    ctx = R.build_context(sess, sess["preread"])
    rid = uuid.uuid4().hex[:10]
    LLM.track(sid, rid)
    dbg = _dir(sid) / "rehearsals" / (rid + ".raw_map.json")
    dbg.parent.mkdir(exist_ok=True)
    total = len(sess["slides"])

    def run():
        return PP.map_transcript(get_client(), ctx, parsed, total, our_side, dbg)

    try:
        mp = await run_in_threadpool(run)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {e}")
    default_name = ((sess["settings"].get("presenters") or [{}])[0].get("name")) or "Presenter"
    events = PP.build_events(parsed, mp, default_name, total)
    if not events:
        raise HTTPException(400, "No pitch content was found in that transcript.")
    when = time.strftime("%Y-%m-%d %H:%M")
    past = {"outcome": outcome.strip(), "notes": notes.strip(), "label": label.strip(), "timing_estimated": not parsed["has_times"],
            "our_side": our_side.strip(), "segments": len(parsed["segments"])}
    _reh_write(sid, rid, {"id": rid, "started": when, "events": events, "qa": [], "score": None, "past": past})
    return {"rid": rid, "events": len(events), "timing_estimated": past["timing_estimated"],
            "minutes": round(max(e["t"] for e in events) / 60.0, 1) if parsed["has_times"] else None}


@app.post("/api/sessions/{sid}/rehearsals/{rid}/review")
async def make_review(sid: str, rid: str, bg: int = 0):
    return await _maybe_bg(bg, _make_review, sid, rid)


async def _make_review(sid: str, rid: str):
    """Step 2 of reviewing a real pitch: write the requirements / conversation / client-questions report."""
    sess = _with_preread(sid)
    reh = _reh_read(sid, rid)
    if not reh.get("past"):
        raise HTTPException(400, "This is not a past-pitch record")
    ctx = R.build_context(sess, sess["preread"])
    LLM.track(sid, rid)
    dbg = _reh_file(sid, rid).with_suffix(".raw_review.json")

    def run():
        return RV.run_review(get_client(), ctx, sess, sess["preread"], reh["events"], reh["past"], dbg)

    try:
        rv = await run_in_threadpool(run)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {e}")
    reh["review"] = rv
    _reh_write(sid, rid, reh)
    return rv


@app.get("/api/sessions/{sid}/rehearsals/{rid}/review.docx")
def review_export(sid: str, rid: str):
    sess = _with_preread(sid)
    reh = _reh_read(sid, rid)
    if not reh.get("review"):
        raise HTTPException(400, "This pitch has no review yet")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", sess["settings"].get("title") or "pitch").strip("_") or "pitch"
    return Response(build_review_docx(sess, sess["preread"], reh),
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="{name}_pitch-review.docx"'})


def _speakers(events) -> list:
    seen = []
    for e in events or []:
        n = (e.get("speaker") or "").strip() if isinstance(e, dict) and e.get("type") == "speech" else ""
        if n and n not in seen:
            seen.append(n)
    return seen


def _history_rows() -> list:
    rows = []
    if not SESSIONS.exists():
        return rows
    for sd in SESSIONS.iterdir():
        sf = sd / "session.json"
        rd = sd / "rehearsals"
        if not sf.exists() or not rd.exists():
            continue
        try:
            sess = json.loads(sf.read_text(encoding="utf-8"))
        except Exception:
            continue
        st = sess.get("settings") or {}
        total_slides = len(sess.get("slides") or [])
        for f in rd.glob("*.json"):
            if f.name.endswith(".raw_score.json"):
                continue
            try:
                r = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            ev = r.get("events") or []
            sc = r.get("score") or {}
            if not ev and not sc:
                continue
            kind = "review" if r.get("review") else "real" if r.get("past") else "rehearsal"
            meta = r.get("meta") or {}
            sm = sc.get("meta") or {}
            stype = meta.get("session_type") or ("qa" if (sm.get("qa_only") or r.get("mode") == "qa_only") else "full")
            rng = meta.get("range")
            if not rng and total_slides:
                a, b = R.slide_range(st, total_slides)
                rng = [a, b]
            mins = sm.get("minutes")
            if mins is None:
                ts = [e.get("t") for e in ev if isinstance(e, dict) and isinstance(e.get("t"), (int, float))]
                mins = round(max(ts) / 60, 1) if ts else None
            names = _speakers(ev)
            if meta.get("presenter") and meta["presenter"] not in names:
                names.insert(0, meta["presenter"])
            w = sc.get("win_likelihood") or {}
            rows.append({
                "sid": sess.get("id") or sd.name, "rid": r.get("id") or f.stem, "kind": kind,
                "title": st.get("title") or "Untitled pitch", "client": (st.get("client") or "").strip(),
                "channel": st.get("channel") or "", "started": r.get("started") or "",
                "session_type": stype, "range": rng, "slides_total": total_slides, "minutes": mins,
                "presenters": names,
                "total": sc.get("total"), "question_handling": (sc.get("question_handling") or {}).get("score"),
                "coverage": None if stype == "qa" else (sc.get("brief_coverage") or {}).get("score"),
                "gut": (sc.get("gut_feel") or {}).get("score"), "delivery": None if stype == "qa" else sc.get("delivery_score"),
                "win_panel": w.get("panel_read_pct"), "win_calc": w.get("calculated_pct"),
                "scored": bool(sc), "outcome": (r.get("past") or {}).get("outcome", ""),
            })
    rows.sort(key=lambda x: x["started"], reverse=True)
    return rows


@app.get("/api/history")
def history():
    return _history_rows()


@app.get("/api/history.csv")
def history_csv():
    import csv, io
    cols = ["started", "client", "title", "channel", "kind", "session_type", "range", "minutes", "presenters", "total",
            "question_handling", "coverage", "gut", "delivery", "win_panel", "win_calc", "outcome"]
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(cols)
    for r in _history_rows():
        row = []
        for c in cols:
            v = r.get(c)
            if c == "range":
                v = f"{v[0]}-{v[1]}" if v else ""
            elif c == "presenters":
                v = "; ".join(v)
            v = "" if v is None else v
            if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
                v = "'" + v   # stop spreadsheet formula injection
            row.append(v)
        wr.writerow(row)
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="rehearsal-history.csv"'})


@app.get("/history")
def history_page():
    return FileResponse(STATIC / "history.html")


@app.get("/settings")
def settings_page():
    return FileResponse(STATIC / "settings.html")


@app.get("/review")
def review_page():
    return FileResponse(STATIC / "review.html")


@app.post("/api/sessions/{sid}/rehearsals/{rid}/turn")
async def reh_turn(sid: str, rid: str, state: dict):
    sess = _with_preread(sid)
    _reh_read(sid, rid)
    ctx = R.build_context(sess, sess["preread"])
    LLM.track(sid, rid)
    # the panel sees the slide that is on screen (only when the deck's visuals were read), not the whole deck
    try:
        n = int(state.get("slide", 0))
    except (TypeError, ValueError):
        n = 0
    imgs = [v["b64"] for v in VIS.load(_dir(sid) / "visuals") if v["n"] == n][:2]
    quality = "sharp" if state.get("quality") == "sharp" else "fast"

    def run():
        return R.live_turn(get_client(), ctx, sess, state, imgs, quality)

    try:
        return await run_in_threadpool(run)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {e}")


@app.post("/api/sessions/{sid}/rehearsals/{rid}/reply")
async def reh_reply(sid: str, rid: str, body: dict):
    sess = _with_preread(sid)
    _reh_read(sid, rid)
    ctx = R.build_context(sess, sess["preread"])
    LLM.track(sid, rid)
    quality = "sharp" if body.get("quality") == "sharp" else "fast"

    def run():
        return R.reply_turn(get_client(), ctx, sess, body, quality)

    try:
        return await run_in_threadpool(run)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {e}")


@app.post("/api/sessions/{sid}/rehearsals/{rid}/qa")
async def reh_qa(sid: str, rid: str, body: dict, bg: int = 0):
    return await _maybe_bg(bg, _reh_qa, sid, rid, body)


async def _reh_qa(sid: str, rid: str, body: dict):
    sess = _with_preread(sid)
    reh = _reh_read(sid, rid)
    reh["events"] = body.get("events", reh["events"])
    if body.get("qa_only"):
        reh["mode"] = "qa_only"
    ctx = R.build_context(sess, sess["preread"])
    LLM.track(sid, rid)
    prior = _prior_gaps(sid, rid)

    def run():
        return R.qa_questions(get_client(), ctx, sess, reh["events"], bool(body.get("qa_only")), body.get("n"), prior)

    try:
        qs = await run_in_threadpool(run)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {e}")
    reh["qa"] = qs
    _reh_write(sid, rid, reh)
    return {"questions": qs}


@app.post("/api/sessions/{sid}/rehearsals/{rid}/score")
async def reh_score(sid: str, rid: str, body: dict, bg: int = 0):
    return await _maybe_bg(bg, _reh_score, sid, rid, body)


async def _reh_score(sid: str, rid: str, body: dict):
    sess = _with_preread(sid)
    reh = _reh_read(sid, rid)
    reh["events"] = body.get("events", reh["events"])
    _reh_write(sid, rid, reh)  # keep the transcript even if scoring fails
    ctx = R.build_context(sess, sess["preread"])
    dbg = _reh_file(sid, rid).with_suffix(".raw_score.json")
    LLM.track(sid, rid)

    past = reh.get("past")
    note = PP.score_note(past) if past else ""

    def run():
        return R.score(get_client(), ctx, sess, sess["preread"], reh["events"], dbg, note, not (past or {}).get("timing_estimated", False),
                       reh.get("mode") == "qa_only" or bool(body.get("qa_only")))

    try:
        sc = await run_in_threadpool(run)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {e}")
    reh["score"] = sc
    _reh_write(sid, rid, reh)
    return sc


def _prior_gaps(sid: str, rid: str, keep: int = 2) -> str:
    """The top actions from the last couple of scored rehearsals of this pitch, so question time can check they were fixed."""
    d = _dir(sid) / "rehearsals"
    found = []
    for f in d.glob("*.json") if d.exists() else []:
        if f.name.endswith(".raw_score.json"):
            continue
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if r.get("id") == rid or r.get("review") or not (r.get("score") or {}).get("top_actions"):
            continue
        found.append((r.get("started", ""), r["score"]["top_actions"]))
    found.sort(reverse=True)
    seen, lines = set(), []
    for _, acts in found[:keep]:
        for a in acts[:4]:
            a = str(a).strip()[:220]
            if a and a.lower() not in seen:
                seen.add(a.lower()); lines.append("- " + a)
    return "\n".join(lines[:6])


def _usage_totals(sid: str, name: str) -> dict:
    f = LLM.usage_path(sid, name)
    d = json.loads(f.read_text(encoding="utf-8")) if f and f.exists() else {}
    tot = {"calls": 0, "input": 0, "cache_read": 0, "cache_write": 0, "output": 0}
    for m in d.values():
        for k in tot:
            tot[k] += m.get(k, 0)
    prompt = tot["input"] + tot["cache_read"] + tot["cache_write"]
    return {"models": d, **tot, "prompt_tokens": prompt, "cache_hit_pct": round(100 * tot["cache_read"] / prompt) if prompt else 0}


@app.get("/api/sessions/{sid}/rehearsals/{rid}/usage")
def reh_usage(sid: str, rid: str):
    _dir(sid)
    if not ID_RE.match(rid):
        raise HTTPException(400, "Bad id")
    return _usage_totals(sid, rid)


@app.get("/api/sessions/{sid}/rehearsals/{rid}")
def reh_get(sid: str, rid: str):
    return _reh_read(sid, rid)


@app.get("/api/sessions/{sid}/rehearsals")
def reh_list(sid: str):
    out = []
    d = _dir(sid) / "rehearsals"
    if d.exists():
        for f in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            if f.name.endswith(".raw_score.json"):
                continue
            r = json.loads(f.read_text(encoding="utf-8"))
            if not r.get("events") and not r.get("score"):
                continue  # opened the pre-flight screen but never started
            out.append({"id": r["id"], "started": r["started"], "events": len(r["events"]),
                        "total": (r.get("score") or {}).get("total"), "past": bool(r.get("past")), "review": bool(r.get("review")),
                        "outcome": (r.get("past") or {}).get("outcome", "")})
    return out


@app.get("/api/sessions/{sid}/rehearsals/{rid}/export.docx")
def reh_export(sid: str, rid: str):
    sess = _with_preread(sid)
    reh = _reh_read(sid, rid)
    if not reh.get("score"):
        raise HTTPException(400, "This rehearsal has no score yet")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", sess["settings"].get("title") or "pitch").strip("_") or "pitch"
    return Response(build_rehearsal_docx(sess, sess["preread"], reh),
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="{name}_rehearsal-report.docx"'})


@app.get("/rehearse")
def rehearse_page():
    return FileResponse(STATIC / "rehearse.html")


@app.exception_handler(Exception)
async def unhandled(_, exc: Exception):
    return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
