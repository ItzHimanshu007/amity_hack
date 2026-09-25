"""drain_scada: storm-drain (nala) level telemetry. CONTRACT.md §D.4.

Three things make this feed awkward, on purpose:
  - `polled` is IST wall-clock text, day first ("24/09/2026 18:32"), no zone marker
  - the record names only an RTU id; location AND channel depth come from the drain
    registry, so an unknown RTU cannot be placed or scaled and is dropped
  - readings are a list of tagged channels. LVL_CM of -999 is the fault sentinel, and
    a missing LVL_CM channel is a fault too. A fault breaks the series, so any open
    overflow episode closes -- a dead gauge is not evidence the drain has cleared.

The measure is level as a percentage of design capacity; over 85% the drain is
surcharging. Records for one RTU arrive in poll order, and the poll that first crosses
the floor opens the episode and fixes its raw_ref.
"""

from collections import defaultdict
from datetime import datetime, timezone

from contract_constants import CONFIDENCE, crosses_floor
from ingest.geocode import Unresolved
from ingest.normalize import (IST, RESOLUTION_REGISTRY, build_event, confidence_for,
                              iso, received_at_for, ref_drain)
from ingest.parsers import read_jsonl

FEED = "drain_scada"
CATEGORY = "drain.overflow"
FAULT_SENTINEL = -999
LOW_BATTERY_V = 11.2


def from_polled(text: str):
    """IST wall-clock text, day first, no zone marker -> aware UTC."""
    return datetime.strptime(text.strip(), "%d/%m/%Y %H:%M").replace(
        tzinfo=IST).astimezone(timezone.utc)


def _channel(rec, tag):
    for ch in rec.get("ch") or []:
        if ch.get("tag") == tag:
            return ch.get("v")
    return None


def extract(resolver, data_dir=None):
    stats = {"records": 0, "candidates": 0, "dropped_faulty": 0,
             "dropped_unresolved": 0, "episodes": 0, "unhealthy_rtus": set(),
             "last_raw_received_at": None}
    last_raw = None
    out = []
    by_rtu = defaultdict(list)

    for _n, rec in read_jsonl(FEED, data_dir):
        stats["records"] += 1
        by_rtu[rec["rtu"]].append(rec)
        arrived = received_at_for(FEED, from_polled(rec["polled"]))
        if last_raw is None or arrived > last_raw:
            last_raw = arrived

    for rtu, records in by_rtu.items():
        try:
            lat, lon, cell, resolution, capacity = resolver.drain(rtu)
        except Unresolved:
            stats["dropped_unresolved"] += len(records)
            continue
        ep = None
        for rec in records:
            ts = from_polled(rec["polled"])
            level = _channel(rec, "LVL_CM")
            if level is None or float(level) == FAULT_SENTINEL:
                stats["dropped_faulty"] += 1
                stats["unhealthy_rtus"].add(rtu)
                if ep is not None:
                    out.append(_event(ep, rec, lat, lon, cell, resolution, ep["peak"], ts))
                    stats["candidates"] += 1
                    ep = None
                continue

            pct = round(float(level) / capacity * 100.0, 1)
            if crosses_floor(CATEGORY, pct):
                if ep is None:
                    ep = {"ref": ref_drain(rtu, ts), "start": ts, "peak": pct}
                    stats["episodes"] += 1
                ep["peak"] = max(ep["peak"], pct)
                out.append(_event(ep, rec, lat, lon, cell, resolution, pct))
                stats["candidates"] += 1
            elif ep is not None:
                out.append(_event(ep, rec, lat, lon, cell, resolution, ep["peak"], ts))
                stats["candidates"] += 1
                ep = None

    stats["unhealthy_rtus"] = sorted(stats["unhealthy_rtus"])
    stats["last_raw_received_at"] = iso(last_raw) if last_raw else None
    return out, stats


def _event(ep, rec, lat, lon, cell, resolution, measure, end=None):
    batt = _channel(rec, "BATT_V")
    extra = (CONFIDENCE["low_battery_mult"]
             if batt is not None and float(batt) < LOW_BATTERY_V else 1.0)
    return build_event(
        source=FEED, category=CATEGORY, raw_ref=ep["ref"], start=ep["start"],
        lat=lat, lon=lon, h3_cell=cell, measure=measure, end=end,
        confidence=confidence_for(CONFIDENCE["sensor_calibrated"], resolution, extra),
        resolution=resolution)
