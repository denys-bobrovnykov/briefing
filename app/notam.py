"""Deterministic NOTAM parsing (ICAO format) and time-window checks."""
from __future__ import annotations

import re
from datetime import datetime, timezone

SPLIT = re.compile(r"(?=^\s*[A-Z]\d{3,4}/\d{2}\s+NOTAM[NRC]\b)", re.M)
HEAD = re.compile(r"^\s*([A-Z]\d{3,4}/\d{2})\s+NOTAM([NRC])(?:\s+([A-Z]\d{3,4}/\d{2}))?", re.M)


def _dt(s: str | None):
    if not s:
        return None
    s = re.sub(r"\s*EST\s*$", "", s.strip())
    if s.upper().startswith("PERM"):
        return datetime(2999, 1, 1, tzinfo=timezone.utc)
    m = re.match(r"(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", s)
    if not m:
        return None
    yy, mo, dd, hh, mi = map(int, m.groups())
    try:
        return datetime(2000 + yy, mo, dd, hh, mi, tzinfo=timezone.utc)
    except ValueError:
        return None


def _field(block: str, tag: str) -> str | None:
    m = re.search(rf"(?:^|\s){tag}\)\s*(.*?)(?=\s+[A-G]\)\s|\Z)", block, re.S)
    return m.group(1).strip() if m else None


def parse_notams(text: str) -> list[dict]:
    out = []
    for blk in SPLIT.split(text):
        h = HEAD.search(blk)
        if not h:
            continue
        flat = re.sub(r"[ \t]*\n[ \t]*", " ", blk)
        q = _field(flat, "Q")
        b, c = _dt(_field(flat, "B")), _dt(_field(flat, "C"))
        a = _field(flat, "A")
        e = _field(flat, "E")
        lo = hi = None
        if q:
            parts = [x.strip() for x in q.split("/")]
            if len(parts) >= 8:
                try:
                    lo, hi = int(parts[5]), int(parts[6])
                except ValueError:
                    pass
        out.append({
            "id": h.group(1), "type": h.group(2), "replaces": h.group(3),
            "q": q, "location": a, "valid_from": b.isoformat() if b else None,
            "valid_to": c.isoformat() if c else None,
            "fl_lower": lo, "fl_upper": hi, "text": e, "raw": blk.strip()[:1500],
        })
    return out


def overlaps(n: dict, start: datetime, end: datetime) -> bool | None:
    """True/False if the NOTAM validity overlaps [start, end]; None if unknown."""
    try:
        vf = datetime.fromisoformat(n["valid_from"]) if n.get("valid_from") else None
        vt = datetime.fromisoformat(n["valid_to"]) if n.get("valid_to") else None
    except ValueError:
        return None
    if vf is None or vt is None:
        return None
    return vf <= end and vt >= start
