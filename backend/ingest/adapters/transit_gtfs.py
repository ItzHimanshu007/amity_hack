"""transit_gtfs: GTFS-realtime-like TripUpdate messages. CONTRACT.md §D.4.

Nested, keyed by stop_id with no coordinates, and ONE raw record fans out to many
events -- one per delayed stop above the floor.

  - `arrival.delay` is seconds and can be negative. A bus running early is not an event.
  - `header.timestamp` is the feed publish time -> received_at.
  - `arrival.time - arrival.delay` is the scheduled arrival -> start_utc.
  - the same (trip_id, stop_id) reappears every cycle with a revised delay, so raw_ref
    deliberately omits the timestamp and the event keeps one id across all of them.
"""

from contract_constants import CONFIDENCE, crosses_floor
from ingest.geocode import Unresolved
from ingest.normalize import (RESOLUTION_REGISTRY, build_event, confidence_for,
                              from_epoch, iso, ref_transit)
from ingest.parsers import read_jsonl

FEED = "transit_gtfs"
CATEGORY = "transit.delay"


def extract(resolver, data_dir=None):
    stats = {"records": 0, "stop_updates": 0, "candidates": 0, "early_ignored": 0,
             "below_floor": 0, "dropped_unresolved": 0, "last_raw_received_at": None}
    last_raw = None
    out = []

    for _n, msg in read_jsonl(FEED, data_dir):
        stats["records"] += 1
        # header.timestamp IS the publish time, so it is the arrival marker directly --
        # every message proves liveness, even one with no delayed stop in it.
        published = from_epoch(msg["header"]["timestamp"])
        if last_raw is None or published > last_raw:
            last_raw = published

        for ent in msg.get("entity", []):
            tu = ent.get("trip_update") or {}
            trip = tu.get("trip") or {}
            trip_id = trip.get("trip_id")
            if not trip_id:
                continue

            for upd in tu.get("stop_time_update", []):
                stats["stop_updates"] += 1
                arrival = upd.get("arrival") or {}
                delay = arrival.get("delay")
                if delay is None:
                    continue
                delay = int(delay)

                if delay <= 0:
                    stats["early_ignored"] += 1
                    continue
                if not crosses_floor(CATEGORY, delay):
                    stats["below_floor"] += 1
                    continue

                stop_id = upd.get("stop_id")
                try:
                    lat, lon, cell, resolution = resolver.stop(stop_id)
                except Unresolved:
                    stats["dropped_unresolved"] += 1
                    continue

                start = from_epoch(int(arrival["time"]) - delay)
                out.append(build_event(
                    source=FEED, category=CATEGORY,
                    raw_ref=ref_transit(trip_id, stop_id), start=start,
                    lat=lat, lon=lon, h3_cell=cell, measure=delay,
                    observed=published,
                    confidence=confidence_for(CONFIDENCE["sensor_calibrated"],
                                              RESOLUTION_REGISTRY),
                    resolution=resolution))
                stats["candidates"] += 1

    stats["last_raw_received_at"] = iso(last_raw) if last_raw else None
    return out, stats
