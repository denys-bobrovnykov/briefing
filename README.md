# FlightBrief

Agentic web app that analyses NOTAMs, weather and OFP data for long-haul flights.

## Run

```bash
cd flightbrief
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp _env.example .env      # then put your ANTHROPIC_API_KEY in .env
uvicorn app.main:app --reload
```

Open http://localhost:8000.

## Inputs
- **PDFs**: NOTAM packs, weather packs (METAR/TAF/SIGMET/SIGWX/winds-temps), and/or the OFP PDF. Scanned PDFs are sent to Claude as documents (vision).
- **OFP JSON** (optional, most reliable): see `sample_ofp.json`. If you upload only the OFP PDF, the agent extracts the waypoint table itself.

## How it works
- `isa.py` – ISA deviation above FL100, temperature at terrain, cold-temperature altimeter effect (~4 ft/1000 ft/°C). Assumes ISA deviation constant with height down to terrain.
- `notam.py` – deterministic ICAO NOTAM parser and validity-window overlap.
- `wx.py` – live METAR/TAF (aviationweather.gov) and terrain elevation (Open-Meteo).
- `agent.py` – Claude tool-use loop: reads docs, extracts OFP, parses NOTAMs, checks NOTAM validity against ETOs, fetches weather, and submits a structured route-ordered, risk-ranked briefing. The same tools power the chat.
- `main.py` – FastAPI endpoints; `static/index.html` – UI.

## Notes
- Sessions are in memory (lost on restart). Add a database for production.
- Terrain at route points is sampled at waypoints only; it is not a full terrain-clearance check.
- Decision support only; verify against official sources.
