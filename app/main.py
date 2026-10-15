from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from . import agent  # noqa: E402
from .store import SESSIONS, Session, add_pdf, get_session, load_points  # noqa: E402

app = FastAPI(title="FlightBrief")
STATIC = Path(__file__).parent / "static"


def _s(sid: str) -> Session:
    try:
        return get_session(sid)
    except KeyError:
        raise HTTPException(404, "Unknown session")


@app.post("/api/session")
def new_session():
    s = Session()
    SESSIONS[s.sid] = s
    return {"sid": s.sid}


@app.post("/api/upload")
async def upload(sid: str = Form(...), files: list[UploadFile] = File(...)):
    s = _s(sid)
    out = []
    for f in files:
        data = await f.read()
        name = f.filename or "file"
        if name.lower().endswith(".json"):
            j = json.loads(data)
            load_points(s, j.get("flight", {}), j.get("waypoints", []))
            out.append({"name": name, "kind": "ofp-json", "points": len(s.points)})
        elif name.lower().endswith(".pdf"):
            d = add_pdf(s, name, data)
            out.append({"name": name, "kind": "pdf", "doc_id": d.doc_id, "pages": len(d.pages), "scanned": d.scanned})
        else:
            raise HTTPException(400, f"Unsupported file type: {name}")
    return {"uploaded": out}


class AnalyzeReq(BaseModel):
    sid: str
    notes: str = ""


@app.post("/api/analyze")
def analyze(req: AnalyzeReq):
    s = _s(req.sid)
    if not s.docs and not s.points:
        raise HTTPException(400, "Upload at least one PDF or OFP JSON first")
    try:
        brief = agent.analyze(s, req.notes)
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")
    return {"briefing": brief, "segments": s.segments, "flight": s.flight}


class ChatReq(BaseModel):
    sid: str
    message: str


@app.post("/api/chat")
def chat(req: ChatReq):
    s = _s(req.sid)
    try:
        return {"reply": agent.chat(s, req.message)}
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
