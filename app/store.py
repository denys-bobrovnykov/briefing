"""PDF ingestion, OFP model, and in-memory session store."""
from __future__ import annotations

import io
import uuid
from dataclasses import dataclass, field

from pypdf import PdfReader

from .isa import Point, enrich_point, high_segments
from .wx import fetch_terrain_ft


@dataclass
class Doc:
    doc_id: str
    name: str
    pages: list[str]
    raw: bytes
    scanned: bool

    @property
    def text(self) -> str:
        return "\n\f\n".join(self.pages)


@dataclass
class Session:
    sid: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    docs: dict[str, Doc] = field(default_factory=dict)
    flight: dict = field(default_factory=dict)
    points: list[Point] = field(default_factory=list)
    segments: list[dict] = field(default_factory=list)
    notams: list[dict] = field(default_factory=list)
    briefing: dict | None = None
    chat: list[dict] = field(default_factory=list)


SESSIONS: dict[str, Session] = {}


def get_session(sid: str) -> Session:
    if sid not in SESSIONS:
        raise KeyError(sid)
    return SESSIONS[sid]


def add_pdf(s: Session, name: str, data: bytes) -> Doc:
    reader = PdfReader(io.BytesIO(data))
    pages = [(p.extract_text() or "") for p in reader.pages]
    scanned = sum(len(p.strip()) for p in pages) < 80 * max(1, len(pages))
    d = Doc(uuid.uuid4().hex[:8], name, pages, data, scanned)
    s.docs[d.doc_id] = d
    return d


def load_points(s: Session, flight: dict, waypoints: list[dict], terrain_lookup: bool = True) -> None:
    """Install structured OFP data (flight header + waypoint list) and compute ISA metrics."""
    s.flight = flight
    pts = []
    for w in waypoints:
        pts.append(Point(
            name=str(w.get("name", "?")), lat=w.get("lat"), lon=w.get("lon"),
            fl=int(w["fl"]) if w.get("fl") not in (None, "") else None,
            eto=w.get("eto"), oat_c=_f(w.get("oat_c")), wind=w.get("wind"),
            terrain_ft=_f(w.get("terrain_ft"))))
    if terrain_lookup:
        need = [i for i, p in enumerate(pts) if p.terrain_ft is None and p.lat is not None and p.lon is not None]
        if need:
            vals = fetch_terrain_ft([(pts[i].lat, pts[i].lon) for i in need])
            for i, v in zip(need, vals):
                pts[i].terrain_ft = v
    s.points = [enrich_point(p) for p in pts]
    s.segments = high_segments(s.points, 100)


def _f(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None
