"""Claude tool-use agents: briefing analysis and chat over the flight data."""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone

import anthropic

from .notam import overlaps, parse_notams
from .store import Session, load_points
from .wx import fetch_metar_taf

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
MAX_TURNS = 25

SYSTEM = """You are a flight-dispatch briefing analyst for long-haul operations.
You analyse NOTAMs, weather (METAR/TAF/SIGMET/SIGWX/winds-temps) and Operational Flight Plan (OFP) data.

Rules:
- Ground every finding in the provided documents or tool results. Quote NOTAM IDs / station identifiers. Never invent data.
- Use tools for facts (document text, NOTAM parsing, validity overlap, ISA/terrain numbers, live weather). Do not do arithmetic for ISA yourself; use get_high_segments.
- Judge NOTAM relevance by location AND time: compare validity with the aircraft's ETO/ETA window at that location (use notams_overlapping with +-1h around ETA for airports, the overflight window for FIRs/airways).
- Severity: CRITICAL (could prevent or change the operation: closures, runway/nav aid outages affecting planned approach, restricted/danger areas on route, severe weather below minima),
  SIGNIFICANT (needs crew awareness or planning action), INFO (nice to know).
- High-altitude segments: report ISA deviation above FL100 and, where terrain is known, the temperature/ISA deviation at terrain elevation and the cold-temperature altimeter effect on terrain clearance. State assumptions (constant ISA deviation with height).
- Flag data gaps (missing OFP fields, scanned/unreadable pages, stale weather) explicitly.
- You are decision support only; the commander and dispatcher remain responsible. Say so briefly in the briefing.
"""

TOOLS = [
    {"name": "list_documents", "description": "List uploaded PDF documents with id, name, page count and a short preview.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "read_document", "description": "Read text of a document page range (1-based, inclusive). Max 15 pages per call.",
     "input_schema": {"type": "object", "properties": {"doc_id": {"type": "string"}, "page_from": {"type": "integer"}, "page_to": {"type": "integer"}}, "required": ["doc_id"]}},
    {"name": "set_ofp_data", "description": "Store structured OFP data you extracted. flight: {callsign, dep, dest, alternates[], enroute_alternates[], etd, eta (ISO UTC)}. waypoints: ordered list of {name, lat, lon, fl, eto (ISO UTC), oat_c, wind, terrain_ft?}. Terrain is looked up automatically if lat/lon given. Returns computed ISA metrics.",
     "input_schema": {"type": "object", "properties": {"flight": {"type": "object"}, "waypoints": {"type": "array", "items": {"type": "object"}}}, "required": ["flight", "waypoints"]}},
    {"name": "parse_notams", "description": "Deterministically parse a document's NOTAMs (ICAO format) into structured records and add them to the session. Returns a compact list.",
     "input_schema": {"type": "object", "properties": {"doc_id": {"type": "string"}}, "required": ["doc_id"]}},
    {"name": "notams_overlapping", "description": "Filter parsed NOTAMs by ICAO location substring and whether validity overlaps [start,end] (ISO UTC).",
     "input_schema": {"type": "object", "properties": {"location": {"type": "string", "description": "ICAO/FIR code substring, optional"}, "start": {"type": "string"}, "end": {"type": "string"}}, "required": ["start", "end"]}},
    {"name": "get_high_segments", "description": "Return route segments above FL100 with ISA deviation, terrain elevation, temperature at terrain, and cold-temperature altimeter clearance effect.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "fetch_weather", "description": "Fetch live METAR and TAF from aviationweather.gov for ICAO airports.",
     "input_schema": {"type": "object", "properties": {"icaos": {"type": "array", "items": {"type": "string"}}}, "required": ["icaos"]}},
]

SUBMIT = {"name": "submit_briefing", "description": "Submit the final structured briefing. Call exactly once when analysis is complete.",
          "input_schema": {"type": "object", "properties": {
              "summary": {"type": "string", "description": "5-8 line executive summary"},
              "overall_risk": {"type": "string", "enum": ["LOW", "MODERATE", "HIGH"]},
              "findings": {"type": "array", "items": {"type": "object", "properties": {
                  "phase": {"type": "string", "enum": ["DEPARTURE", "CLIMB/ENROUTE", "OCEANIC/ENROUTE", "DESCENT/ARRIVAL", "DESTINATION", "ALTERNATE", "ENROUTE_ALTERNATE", "GENERAL"]},
                  "order": {"type": "integer", "description": "Position along route, 1 = first"},
                  "location": {"type": "string"},
                  "severity": {"type": "string", "enum": ["CRITICAL", "SIGNIFICANT", "INFO"]},
                  "source": {"type": "string", "description": "NOTAM id, METAR/TAF station, SIGMET, OFP, etc."},
                  "title": {"type": "string"}, "detail": {"type": "string"},
                  "validity": {"type": "string"}, "relevance_window": {"type": "string"},
                  "recommended_action": {"type": "string"}},
                  "required": ["phase", "order", "location", "severity", "source", "title", "detail"]}},
              "high_altitude_notes": {"type": "string", "description": "Interpretation of FL100+ segments: ISA deviation, terrain temp, clearance concerns, assumptions"},
              "data_gaps": {"type": "array", "items": {"type": "string"}}},
              "required": ["summary", "overall_risk", "findings", "high_altitude_notes", "data_gaps"]}}


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def run_tool(s: Session, name: str, args: dict) -> str:
    try:
        if name == "list_documents":
            return json.dumps([{"doc_id": d.doc_id, "name": d.name, "pages": len(d.pages), "scanned": d.scanned,
                                "preview": d.text[:300]} for d in s.docs.values()])
        if name == "read_document":
            d = s.docs[args["doc_id"]]
            a = max(1, args.get("page_from", 1))
            b = min(len(d.pages), args.get("page_to", a + 14), a + 14)
            body = "\n".join(f"--- page {i} ---\n{d.pages[i - 1]}" for i in range(a, b + 1))
            return body[:60000] + ("\n[truncated]" if len(body) > 60000 else "")
        if name == "set_ofp_data":
            load_points(s, args["flight"], args["waypoints"])
            return json.dumps({"points": len(s.points), "high_segments": s.segments}, default=str)[:30000]
        if name == "parse_notams":
            parsed = parse_notams(s.docs[args["doc_id"]].text)
            known = {n["id"] for n in s.notams}
            s.notams += [n for n in parsed if n["id"] not in known]
            return json.dumps([{k: n[k] for k in ("id", "type", "location", "valid_from", "valid_to", "fl_lower", "fl_upper", "text")} for n in parsed])[:60000]
        if name == "notams_overlapping":
            st = datetime.fromisoformat(args["start"].replace("Z", "+00:00"))
            en = datetime.fromisoformat(args["end"].replace("Z", "+00:00"))
            st = st if st.tzinfo else st.replace(tzinfo=timezone.utc)
            en = en if en.tzinfo else en.replace(tzinfo=timezone.utc)
            loc = (args.get("location") or "").upper()
            res = []
            for n in s.notams:
                if loc and loc not in (n.get("location") or "").upper():
                    continue
                o = overlaps(n, st, en)
                if o is not False:
                    res.append({**{k: n[k] for k in ("id", "location", "valid_from", "valid_to", "fl_lower", "fl_upper", "text")}, "overlaps": o})
            return json.dumps(res)[:60000]
        if name == "get_high_segments":
            return json.dumps(s.segments, default=str)[:60000] if s.segments else "No OFP data loaded or no points above FL100."
        if name == "fetch_weather":
            return json.dumps(fetch_metar_taf(args["icaos"]))[:40000]
        return f"Unknown tool {name}"
    except Exception as e:
        return f"Tool error: {type(e).__name__}: {e}"


def _scanned_blocks(s: Session) -> list[dict]:
    return [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
             "data": base64.b64encode(d.raw).decode()}, "title": d.name}
            for d in s.docs.values() if d.scanned]


def _loop(s: Session, messages: list, tools: list, system: str, final_tool: str | None = None):
    client = _client()
    for _ in range(MAX_TURNS):
        resp = client.messages.create(model=MODEL, max_tokens=8000, system=system, tools=tools, messages=messages)
        messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in resp.content]})
        calls = [b for b in resp.content if b.type == "tool_use"]
        if not calls:
            return "".join(b.text for b in resp.content if b.type == "text"), None
        results, final = [], None
        for c in calls:
            if final_tool and c.name == final_tool:
                final = c.input
                results.append({"type": "tool_result", "tool_use_id": c.id, "content": "Briefing received."})
            else:
                results.append({"type": "tool_result", "tool_use_id": c.id, "content": run_tool(s, c.name, c.input)})
        messages.append({"role": "user", "content": results})
        if final is not None:
            return "", final
    raise RuntimeError("Agent exceeded maximum turns")


def analyze(s: Session, user_notes: str = "") -> dict:
    now = datetime.now(timezone.utc).isoformat(timespec="minutes")
    intro = (f"Current UTC time: {now}. Documents are uploaded (use list_documents). "
             f"{'OFP data is already loaded.' if s.points else 'OFP data is not structured yet: find the OFP in the documents, extract it and call set_ofp_data.'} "
             "Workflow: 1) identify documents, 2) ensure OFP data, 3) parse NOTAMs, 4) read weather docs, 5) fetch live METAR/TAF for departure, destination, alternates and enroute alternates, "
             "6) get_high_segments, 7) check NOTAM validity against ETO windows, 8) submit_briefing ordered along the route. "
             f"Dispatcher notes: {user_notes or 'none'}")
    msgs = [{"role": "user", "content": _scanned_blocks(s) + [{"type": "text", "text": intro}]}]
    _, brief = _loop(s, msgs, TOOLS + [SUBMIT], SYSTEM, "submit_briefing")
    if brief is None:
        raise RuntimeError("Agent did not submit a briefing")
    brief["findings"].sort(key=lambda f: (f.get("order", 99), {"CRITICAL": 0, "SIGNIFICANT": 1, "INFO": 2}[f["severity"]]))
    s.briefing = brief
    return brief


def chat(s: Session, question: str) -> str:
    ctx = {"flight": s.flight, "high_segments": s.segments, "briefing": s.briefing,
           "documents": [{"doc_id": d.doc_id, "name": d.name, "pages": len(d.pages)} for d in s.docs.values()],
           "parsed_notams": len(s.notams)}
    system = SYSTEM + "\nYou are now answering follow-up questions. Context:\n" + json.dumps(ctx, default=str)[:40000]
    s.chat.append({"role": "user", "content": question})
    msgs = list(s.chat)
    text, _ = _loop(s, msgs, TOOLS, system)
    s.chat.append({"role": "assistant", "content": text})
    return text
