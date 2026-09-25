"""Loose dict -> canonical event dict. Owns time->UTC, severity, confidence, event_id.

CONTRACT.md §A. Every number comes from contract_constants; nothing is re-typed here.

`make_event_id` and the raw_ref builders are REIMPLEMENTED from the contract rule rather
than imported from backend/sim/ids.py. CONTRACT.md §A requires it: in live mode there is
no simulator to import, so the normalizer has to stand on its own. If these two
implementations ever disagree, ground truth dangles -- verify_ingest.py check (g) exists
to catch exactly that.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from contract_constants import (CITY_TZ, CONFIDENCE, FEEDS, NAGARNAADI_NS,
                                crosses_floor, scale_severity)

IST = ZoneInfo(CITY_TZ)

# How a location was pinned down. Drives the confidence multiplier and is carried on the
# event so the data room can show it and Phase 5 can reason about it.
RESOLUTION_DIRECT = "direct"       # the feed gave us coordinates
RESOLUTION_REGISTRY = "registry"   # feeder_id / stop_id -> registry lookup
RESOLUTION_LANDMARK = "landmark"   # free-text landmark name -> gazetteer

_RESOLUTION_MULT = {
    RESOLUTION_DIRECT: 1.0,
    RESOLUTION_REGISTRY: CONFIDENCE["registry_resolved_mult"],
    RESOLUTION_LANDMARK: CONFIDENCE["landmark_resolved_mult"],
}


# --------------------------------------------------------------- event ids ----

def make_event_id(raw_ref: str, category: str) -> str:
    """CONTRACT.md §A. uuid5 over "<raw_ref>|<category>", single pipe, no whitespace."""
    return str(uuid.uuid5(NAGARNAADI_NS, f"{raw_ref}|{category}"))


def epoch(dt: datetime) -> int:
    """Integer epoch seconds. A float would stringify with '.0' and change the id."""
    return int(dt.timestamp())


# --- raw_ref grammar, CONTRACT.md §A. Granularity differs because fan-out differs. ---

def ref_weather(station_id: str, ts: datetime) -> str:
    return f"weather_imd:{station_id}@{epoch(ts)}"


def ref_air(sensor: str, captured_iso: str) -> str:
    return f"air_sensors:{sensor}@{captured_iso}"


def ref_power(feeder_id: str, reported: datetime) -> str:
    return f"power_discom:{feeder_id}@{epoch(reported)}"


def ref_drain(rtu_id: str, polled: datetime) -> str:
    """The poll that first crossed the overflow floor; the episode keeps it."""
    return f"drain_scada:{rtu_id}@{epoch(polled)}"


def ref_complaint(complaint_id: str) -> str:
    return f"civic_complaints:{complaint_id}"


# -------------------------------------------------------------- timestamps ----

def iso(dt: datetime) -> str:
    """ISO8601 UTC with a literal Z, seconds precision. CONTRACT.md §H."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_epoch(ts) -> datetime:
    return datetime.fromtimestamp(int(ts), timezone.utc)


def from_iso_z(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def from_ist_naive_iso(s: str) -> datetime:
    """CONTRACT.md §D.3. Looks ISO-shaped but carries no offset and is NOT UTC."""
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=IST).astimezone(
        timezone.utc)


def from_ist_complaint(s: str) -> datetime:
    """CONTRACT.md §D.2: 'DD/MM/YYYY h:mm AM/PM' in IST. Day first, hour not padded."""
    return datetime.strptime(s.strip(), "%d/%m/%Y %I:%M %p").replace(
        tzinfo=IST).astimezone(timezone.utc)


def received_at_for(feed: str, start: datetime, observed: datetime = None) -> datetime:
    """When ingest saw the record.

    Replaying files, there is no real arrival clock. Where a feed states its own publish
    time we use it. Otherwise we model the
    worst-case polling latency: a record can sit for at most one update interval before
    the next poll picks it up, so received_at = start + that feed's interval (§D).
    """
    if observed is not None:
        return max(observed, start)
    return start + timedelta(seconds=FEEDS[feed]["interval_sec"])


# ------------------------------------------------------- severity/confidence --

def _round2(x: float) -> float:
    """Half-up, so 0.70 x 0.85 gives the 0.60 CONTRACT.md §A's worked example states
    (Python's bankers' rounding would return 0.59)."""
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def confidence_for(base: float, resolution: str, extra_mult: float = 1.0) -> float:
    """CONTRACT.md §A confidence table: base by source class, then the resolution
    multiplier, then any feed-specific penalty. Rounded to 2dp, floored at 0.30."""
    value = base * _RESOLUTION_MULT[resolution] * extra_mult
    return max(CONFIDENCE["floor"], _round2(value))


def severity_for(category: str, measure: float, cap: float = None) -> float:
    """CONTRACT.md §A ramp. Never returns None -- a below-floor measure is 0.0, and the
    caller is the one that decides whether a below-floor reading becomes an event."""
    sev = scale_severity(category, float(measure))
    if cap is not None:
        sev = min(sev, cap)
    return round(sev, 4)


# ------------------------------------------------------------ event builder ---

def build_event(*, source, category, raw_ref, start, lat, lon, h3_cell, measure,
                confidence, resolution, end=None, observed=None, severity_cap=None):
    """Assemble one canonical §A event. The only place this dict shape is constructed."""
    received = received_at_for(source, start, observed)
    return {
        "event_id": make_event_id(raw_ref, category),
        "source": source,
        "category": category,
        "h3_cell": h3_cell,
        "lat": round(float(lat), 5),
        "lon": round(float(lon), 5),
        "start_utc": iso(start),
        "end_utc": iso(end) if end else None,
        "severity": severity_for(category, measure, severity_cap),
        "confidence": confidence,
        "received_at": iso(received),
        "freshness_sec": max(0, int((received - start).total_seconds())),
        "is_simulated": True,
        "raw_ref": raw_ref,
        # Extensions beyond the §A core, both documented in §A:
        "resolution": resolution,   # how lat/lon was determined
        "measure": round(float(measure), 3),   # the raw quantity severity was scaled from
    }


__all__ = [
    "make_event_id", "build_event", "confidence_for", "severity_for", "iso",
    "from_epoch", "from_iso_z", "from_ist_naive_iso", "from_ist_complaint",
    "received_at_for", "crosses_floor",
    "ref_weather", "ref_air", "ref_power", "ref_drain", "ref_complaint",
    "RESOLUTION_DIRECT", "RESOLUTION_REGISTRY", "RESOLUTION_LANDMARK",
]
