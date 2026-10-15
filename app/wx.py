"""Live weather (aviationweather.gov) and terrain elevation (Open-Meteo)."""
from __future__ import annotations

import httpx

AWC = "https://aviationweather.gov/api/data"
TIMEOUT = 20


def fetch_metar_taf(icaos: list[str]) -> dict:
    ids = ",".join(i.upper() for i in icaos)
    out: dict = {"metar": [], "taf": [], "errors": []}
    with httpx.Client(timeout=TIMEOUT) as c:
        for kind in ("metar", "taf"):
            try:
                r = c.get(f"{AWC}/{kind}", params={"ids": ids, "format": "json", "hours": 3} if kind == "metar" else {"ids": ids, "format": "json"})
                if r.status_code == 200 and r.text.strip():
                    out[kind] = r.json()
                elif r.status_code not in (200, 204):
                    out["errors"].append(f"{kind}: HTTP {r.status_code}")
            except Exception as e:  # network or parse failure
                out["errors"].append(f"{kind}: {e}")
    # keep payload small for the LLM
    out["metar"] = [{"icao": m.get("icaoId"), "raw": m.get("rawOb"), "time": m.get("reportTime")} for m in out["metar"]]
    out["taf"] = [{"icao": t.get("icaoId"), "raw": t.get("rawTAF"), "issued": t.get("issueTime")} for t in out["taf"]]
    return out


def fetch_terrain_ft(coords: list[tuple[float, float]]) -> list[float | None]:
    """Terrain elevation (ft) for lat/lon pairs. Returns None where unavailable."""
    res: list[float | None] = []
    with httpx.Client(timeout=TIMEOUT) as c:
        for i in range(0, len(coords), 100):
            chunk = coords[i:i + 100]
            try:
                r = c.get("https://api.open-meteo.com/v1/elevation", params={
                    "latitude": ",".join(f"{a:.4f}" for a, _ in chunk),
                    "longitude": ",".join(f"{b:.4f}" for _, b in chunk)})
                r.raise_for_status()
                res += [round(m * 3.28084) for m in r.json()["elevation"]]
            except Exception:
                res += [None] * len(chunk)
    return res
