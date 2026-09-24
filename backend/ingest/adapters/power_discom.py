"""power_discom: JSON Lines keyed by feeder_id, no coordinates. CONTRACT.md §D.3.

Two traps, both deliberate:
  - `reported_time` looks ISO-shaped but is naive LOCAL time (IST). Reading it as UTC
    puts every outage 5h30m in the past.
  - there are no coordinates. feeder_id resolves through /data/feeder_registry.json,
    which is what the x0.80 registry multiplier in §A pays for.

One TRIP on a signal-bearing feeder yields TWO events -- power.outage and
traffic.signal_down -- from the same raw_ref, separated by the |category suffix. Both
carry source="power_discom". The resident-reported half of that pair comes from
civic_complaints and must never be merged with this one (§D dedupe carve-out).
"""

from contract_constants import CONFIDENCE, crosses_floor
from ingest.geocode import Unresolved
from ingest.normalize import (RESOLUTION_REGISTRY, build_event, confidence_for,
                              from_ist_naive_iso, ref_power)
from ingest.parsers import read_jsonl

FEED = "power_discom"
SCHEDULED_SEVERITY_CAP = 0.4     # CONTRACT.md §D.3: an announced cut is capped


def extract(resolver, data_dir=None):
    stats = {"records": 0, "candidates": 0, "trips": 0, "restores": 0,
             "restores_unmatched": 0, "dropped_unresolved": 0, "below_floor": 0,
             "signal_down_emitted": 0}
    out = []
    open_outage = {}        # feeder_id -> dict describing the open TRIP

    for _n, rec in read_jsonl(FEED, data_dir):
        stats["records"] += 1
        feeder_id = rec["feeder_id"]
        when = from_ist_naive_iso(rec["reported_time"])
        event_kind = rec.get("event")

        try:
            lat, lon, cell, resolution = resolver.feeder(feeder_id)
        except Unresolved:
            stats["dropped_unresolved"] += 1
            continue

        conf = confidence_for(CONFIDENCE["sensor_calibrated"], RESOLUTION_REGISTRY)

        if event_kind == "RESTORE":
            stats["restores"] += 1
            ep = open_outage.pop(feeder_id, None)
            if ep is None:
                stats["restores_unmatched"] += 1
                continue
            # A RESTORE never mints an id (CONTRACT.md §A). It closes what the TRIP
            # opened, so it re-emits that event's raw_ref with end set.
            for category, measure, cap in ep["emitted"]:
                out.append(build_event(
                    source=FEED, category=category, raw_ref=ep["ref"], start=ep["start"],
                    lat=lat, lon=lon, h3_cell=cell, measure=measure, end=when,
                    confidence=conf, resolution=resolution, severity_cap=cap))
                stats["candidates"] += 1
            continue

        # TRIP or SCHEDULED_CUT
        stats["trips"] += 1
        raw_ref = ref_power(feeder_id, when)
        connections = float(rec.get("affected_connections") or 0)
        cap = SCHEDULED_SEVERITY_CAP if event_kind == "SCHEDULED_CUT" else None
        emitted = []

        if crosses_floor("power.outage", connections):
            out.append(build_event(
                source=FEED, category="power.outage", raw_ref=raw_ref, start=when,
                lat=lat, lon=lon, h3_cell=cell, measure=connections,
                confidence=conf, resolution=resolution, severity_cap=cap))
            emitted.append(("power.outage", connections, cap))
            stats["candidates"] += 1
        else:
            stats["below_floor"] += 1

        if rec.get("carries_signals"):
            # `signal_junctions` is the junctions-dark count §A scales severity by.
            junctions = float(rec.get("signal_junctions")
                              or resolver.feeder_meta(feeder_id).get("signal_junctions")
                              or 1)
            out.append(build_event(
                source=FEED, category="traffic.signal_down", raw_ref=raw_ref,
                start=when, lat=lat, lon=lon, h3_cell=cell, measure=junctions,
                confidence=conf, resolution=resolution, severity_cap=cap))
            emitted.append(("traffic.signal_down", junctions, cap))
            stats["candidates"] += 1
            stats["signal_down_emitted"] += 1

        if emitted:
            open_outage[feeder_id] = {"ref": raw_ref, "start": when, "emitted": emitted}

    return out, stats
