"""ISA and high-altitude segment calculations (deterministic, no LLM)."""
from __future__ import annotations

from dataclasses import dataclass, asdict

TROPOPAUSE_FT = 36089.0


def isa_temp_c(alt_ft: float) -> float:
    """ISA temperature (deg C) at pressure altitude in ft (valid to 65,617 ft)."""
    if alt_ft <= TROPOPAUSE_FT:
        return 15.0 - 1.98 * (alt_ft / 1000.0)
    return -56.5


def isa_dev(oat_c: float, pressure_alt_ft: float) -> float:
    return oat_c - isa_temp_c(pressure_alt_ft)


def true_alt_error_ft(dev_c: float, height_above_source_ft: float) -> float:
    """Approx. altimeter error: ~4 ft per 1000 ft per deg C of ISA deviation.

    Negative result = aircraft is LOWER than indicated (cold air, ISA dev < 0).
    """
    return 0.004 * dev_c * height_above_source_ft


@dataclass
class Point:
    name: str
    lat: float | None
    lon: float | None
    fl: int | None
    eto: str | None
    oat_c: float | None
    wind: str | None
    terrain_ft: float | None = None

    # computed
    isa_dev_c: float | None = None
    terrain_temp_c: float | None = None
    terrain_isa_dev_c: float | None = None
    height_above_terrain_ft: float | None = None
    true_alt_error_ft: float | None = None
    clearance_after_error_ft: float | None = None


def enrich_point(p: Point) -> Point:
    if p.fl is None or p.oat_c is None:
        return p
    pa = p.fl * 100.0
    p.isa_dev_c = round(isa_dev(p.oat_c, pa), 1)
    if p.terrain_ft is not None:
        # Assumption: ISA deviation is constant with height below the aircraft,
        # so temperature at terrain = ISA(terrain) + deviation.
        p.terrain_isa_dev_c = p.isa_dev_c
        p.terrain_temp_c = round(isa_temp_c(p.terrain_ft) + p.isa_dev_c, 1)
        p.height_above_terrain_ft = round(pa - p.terrain_ft)
        err = true_alt_error_ft(p.isa_dev_c, p.height_above_terrain_ft)
        p.true_alt_error_ft = round(err)
        p.clearance_after_error_ft = round(p.height_above_terrain_ft + min(err, 0))
    return p


def high_segments(points: list[Point], min_fl: int = 100) -> list[dict]:
    """Group consecutive points above min_fl (default FL100) into segments."""
    segs: list[list[Point]] = []
    cur: list[Point] = []
    for p in points:
        if p.fl is not None and p.fl > min_fl:
            cur.append(p)
        elif cur:
            segs.append(cur)
            cur = []
    if cur:
        segs.append(cur)

    out = []
    for i, s in enumerate(segs, 1):
        devs = [p.isa_dev_c for p in s if p.isa_dev_c is not None]
        terr = [p for p in s if p.terrain_ft is not None]
        worst_clr = min(
            (p for p in terr if p.clearance_after_error_ft is not None),
            key=lambda p: p.clearance_after_error_ft,
            default=None,
        )
        highest_terrain = max(terr, key=lambda p: p.terrain_ft, default=None)
        out.append({
            "segment": i,
            "from": s[0].name, "to": s[-1].name,
            "from_eto": s[0].eto, "to_eto": s[-1].eto,
            "max_fl": max(p.fl for p in s), "min_fl": min(p.fl for p in s),
            "isa_dev_min_c": min(devs) if devs else None,
            "isa_dev_max_c": max(devs) if devs else None,
            "highest_terrain_ft": highest_terrain.terrain_ft if highest_terrain else None,
            "highest_terrain_at": highest_terrain.name if highest_terrain else None,
            "terrain_temp_c_at_highest": highest_terrain.terrain_temp_c if highest_terrain else None,
            "worst_clearance_after_error_ft": worst_clr.clearance_after_error_ft if worst_clr else None,
            "worst_clearance_at": worst_clr.name if worst_clr else None,
            "points": [asdict(p) for p in s],
        })
    return out
