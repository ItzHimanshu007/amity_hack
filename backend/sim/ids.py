"""event_id derivation and the per-feed raw_ref grammar. CONTRACT.md §A.

The single implementation of these strings in the generator. Nothing else builds a
raw_ref by hand -- if two places format an epoch differently, every derived id diverges
and ground_truth.json dangles.

Phase 3 reimplements make_event_id rather than importing it (CONTRACT.md §A): the
normalizer has to stand on its own.
"""

import uuid
from datetime import datetime

from contract_constants import NAGARNAADI_NS


def make_event_id(raw_ref: str, category: str) -> str:
    """CONTRACT.md §A. Deterministic: same inputs, same id, in any process."""
    return str(uuid.uuid5(NAGARNAADI_NS, f"{raw_ref}|{category}"))


def epoch(dt: datetime) -> int:
    """Integer epoch seconds. No floats -- a '.0' would change the derived id."""
    return int(dt.timestamp())


# --- raw_ref builders, one per feed (CONTRACT.md §A raw_ref grammar) -----------

def ref_weather(station_id: str, ts: datetime) -> str:
    """Opening observation of an episode; the event keeps it for its whole life."""
    return f"weather_imd:{station_id}@{epoch(ts)}"


def ref_air(sensor: str, captured_iso: str) -> str:
    """captured_iso is the record's own string, Z included, byte for byte."""
    return f"air_sensors:{sensor}@{captured_iso}"


def ref_power(feeder_id: str, reported: datetime) -> str:
    """One TRIP record; yields two ids when the feeder carries signals."""
    return f"power_discom:{feeder_id}@{epoch(reported)}"


def ref_transit(trip_id: str, stop_id: str) -> str:
    """No timestamp: the id must survive the delay being revised each cycle."""
    return f"transit_gtfs:{trip_id}@{stop_id}"


def ref_complaint(complaint_id: str) -> str:
    return f"civic_complaints:{complaint_id}"
