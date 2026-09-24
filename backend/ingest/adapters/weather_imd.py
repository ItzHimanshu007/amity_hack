"""weather_imd: JSON Lines, UTC epoch seconds. CONTRACT.md §D.1.

Station observations with no notion of an event -- we threshold the numbers. An episode
spans many 5-minute records but is ONE event, keeping the raw_ref of the record that
opened it.

Records are processed in FILE order, not sorted by `ts`: file order is arrival order,
which is what decides who opened an episode.
"""

from collections import defaultdict

from contract_constants import CONFIDENCE, crosses_floor
from ingest.normalize import (RESOLUTION_DIRECT, build_event, confidence_for,
                              from_epoch, ref_weather)
from ingest.parsers import read_jsonl

FEED = "weather_imd"


def heat_index_c(temp_c: float, rh_pct: float) -> float:
    """NWS Rothfusz regression, in Celsius. CONTRACT.md §D.1.

    Below 80F the heat index is just the air temperature. Phase 1 and Phase 3 must use
    the identical formula: it decides which observation first crosses the floor, and
    therefore which raw_ref the event_id is derived from.
    """
    t = temp_c * 9 / 5 + 32
    if t < 80:
        return temp_c
    hi = (-42.379 + 2.04901523 * t + 10.14333127 * rh_pct - 0.22475541 * t * rh_pct
          - 0.00683783 * t * t - 0.05481717 * rh_pct * rh_pct
          + 0.00122874 * t * t * rh_pct + 0.00085282 * t * rh_pct * rh_pct
          - 0.00000199 * t * t * rh_pct * rh_pct)
    return (hi - 32) * 5 / 9


def extract(resolver, data_dir=None):
    stats = {"records": 0, "candidates": 0, "dropped_unresolved": 0, "episodes": 0}
    out = []
    # (station, category) -> {"ref":, "start":, "peak":}
    open_ep = {}
    by_station = defaultdict(list)

    for _n, rec in read_jsonl(FEED, data_dir):
        stats["records"] += 1
        by_station[rec["station_id"]].append(rec)

    for station, records in by_station.items():
        for rec in records:
            ts = from_epoch(rec["ts"])
            try:
                lat, lon, cell = resolver.direct(rec["lat"], rec["lon"])
            except Exception:
                stats["dropped_unresolved"] += 1
                continue

            measures = {
                "weather.rain": float(rec.get("rain_mm_15min") or 0.0),
                "weather.heat": heat_index_c(float(rec["temp_c"]), float(rec["rh_pct"])),
            }
            for category, measure in measures.items():
                key = (station, category)
                above = crosses_floor(category, measure)
                ep = open_ep.get(key)

                if above:
                    if ep is None:
                        ep = {"ref": ref_weather(station, ts), "start": ts, "peak": measure}
                        open_ep[key] = ep
                        stats["episodes"] += 1
                    ep["peak"] = max(ep["peak"], measure)
                    out.append(build_event(
                        source=FEED, category=category, raw_ref=ep["ref"],
                        start=ep["start"], lat=lat, lon=lon, h3_cell=cell,
                        measure=measure,
                        confidence=confidence_for(CONFIDENCE["sensor_calibrated"],
                                                  RESOLUTION_DIRECT),
                        resolution=RESOLUTION_DIRECT))
                    stats["candidates"] += 1
                elif ep is not None:
                    # Dropped back below the floor: the episode is over, and this record
                    # is what tells us when.
                    out.append(build_event(
                        source=FEED, category=category, raw_ref=ep["ref"],
                        start=ep["start"], lat=lat, lon=lon, h3_cell=cell,
                        measure=ep["peak"], end=ts,
                        confidence=confidence_for(CONFIDENCE["sensor_calibrated"],
                                                  RESOLUTION_DIRECT),
                        resolution=RESOLUTION_DIRECT))
                    stats["candidates"] += 1
                    del open_ep[key]

    return out, stats
