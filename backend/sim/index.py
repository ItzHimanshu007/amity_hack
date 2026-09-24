"""The event index: one line per raw record Phase 1 expects to cross a severity floor.

CONTRACT.md §H. A Phase 1 debug artifact and the basis for the ground-truth integrity
check. Phase 3 must never read it -- it derives every id itself.
"""


def iso(dt) -> str:
    """ISO8601 UTC with a literal Z, seconds precision. CONTRACT.md §H."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def entry(event_id, raw_ref, category, source, start, h3_cell, lat, lon, measure,
          truth_id=None, decoy_id=None) -> dict:
    return {
        "event_id": event_id,
        "raw_ref": raw_ref,
        "category": category,
        "source": source,
        "start_utc": iso(start),
        "h3_cell": h3_cell,
        "lat": lat,
        "lon": lon,
        "measure": round(float(measure), 3),
        "truth_id": truth_id,
        "decoy_id": decoy_id,
    }
