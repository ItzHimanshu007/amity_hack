"""air_sensors: JSON Lines, one object per sensor. CONTRACT.md §D.5.

Cheap hardware, so three things have to be handled honestly:
  - coordinates are nested at loc.latitude / loc.longitude, unlike every other feed
  - pm25 arrives as null or the sentinel -1 when the sensor is faulty: the record is
    dropped, and it breaks the series, so any open episode closes
  - sensors drop out for stretches. A GAP IS ABSENCE OF DATA, NOT A ZERO READING -- a
    missing record leaves the episode untouched rather than closing it

`captured` carries up to 90 s of clock drift, so it is never used for sequencing. File
order is arrival order, and that is what decides who opened an episode.
"""

from collections import defaultdict

from contract_constants import CONFIDENCE, crosses_floor
from ingest.normalize import (RESOLUTION_DIRECT, build_event, confidence_for,
                              from_iso_z, ref_air)
from ingest.parsers import read_jsonl

FEED = "air_sensors"
CATEGORY = "air.pm25"
LOW_BATTERY_PCT = 15


def extract(resolver, data_dir=None):
    stats = {"records": 0, "candidates": 0, "dropped_faulty": 0,
             "dropped_unresolved": 0, "episodes": 0, "unhealthy_sensors": set()}
    out = []
    open_ep = {}
    by_sensor = defaultdict(list)

    for _n, rec in read_jsonl(FEED, data_dir):
        stats["records"] += 1
        by_sensor[rec["sensor"]].append(rec)

    for sensor, records in by_sensor.items():
        for rec in records:
            pm = rec.get("pm25")
            faulty = pm is None or float(pm) == -1
            captured = rec["captured"]
            ts = from_iso_z(captured)

            if faulty:
                stats["dropped_faulty"] += 1
                stats["unhealthy_sensors"].add(sensor)
                ep = open_ep.pop(sensor, None)
                if ep is not None:
                    out.append(_event(resolver, rec, ep, ep["peak"], end=ts, stats=stats))
                continue

            pm = float(pm)
            above = crosses_floor(CATEGORY, pm)
            ep = open_ep.get(sensor)

            if above:
                if ep is None:
                    ep = {"ref": ref_air(sensor, captured), "start": ts, "peak": pm}
                    open_ep[sensor] = ep
                    stats["episodes"] += 1
                ep["peak"] = max(ep["peak"], pm)
                ev = _event(resolver, rec, ep, pm, stats=stats)
                if ev:
                    out.append(ev)
            elif ep is not None:
                ev = _event(resolver, rec, ep, ep["peak"], end=ts, stats=stats)
                if ev:
                    out.append(ev)
                del open_ep[sensor]

    stats["unhealthy_sensors"] = sorted(stats["unhealthy_sensors"])
    return out, stats


def _event(resolver, rec, ep, measure, end=None, stats=None):
    loc = rec["loc"]
    try:
        lat, lon, cell = resolver.direct(loc["latitude"], loc["longitude"])
    except Exception:
        if stats is not None:
            stats["dropped_unresolved"] += 1
        return None

    base = (CONFIDENCE["sensor_calibrated"] if rec.get("calibrated")
            else CONFIDENCE["sensor_uncalibrated"])
    extra = (CONFIDENCE["low_battery_mult"]
             if float(rec.get("battery_pct", 100)) < LOW_BATTERY_PCT else 1.0)

    if stats is not None:
        stats["candidates"] += 1
    return build_event(
        source=FEED, category=CATEGORY, raw_ref=ep["ref"], start=ep["start"],
        lat=lat, lon=lon, h3_cell=cell, measure=measure, end=end,
        confidence=confidence_for(base, RESOLUTION_DIRECT, extra),
        resolution=RESOLUTION_DIRECT)
