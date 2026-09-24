"""Raw transit feed: GTFS-realtime-like TripUpdate messages. CONTRACT.md §D.4.

Nested, keyed by stop_id with no coordinates, and one message fans out to many events.
Delays are seconds and can be negative -- buses running early are in here on purpose,
so Phase 3 has something to ignore.
"""

import json
from datetime import timedelta

from contract_constants import FEEDS, crosses_floor
from sim import config
from sim.ids import make_event_id, ref_transit
from sim.index import entry
from sim.profiles import ist_hour, rng


def write(world, specs, seed: int = config.SEED):
    r = rng("transit_writer", seed)
    path = config.DATA_DIR / config.OUTPUT_FILES["transit_gtfs"]
    interval = FEEDS["transit_gtfs"]["interval_sec"]

    delays = sorted([s for s in specs if s.category == "transit.delay"],
                    key=lambda s: s.start)
    if not delays:
        path.write_text("", encoding="utf-8")
        return 0, []

    index, count = [], 0
    opened = set()
    cursor = 0
    active = []

    with path.open("w", encoding="utf-8") as fh:
        t = config.HISTORY_START
        while t <= config.SIM_END:
            h = ist_hour(t)
            if not (config.SERVICE_START_HOUR_IST <= h < config.SERVICE_END_HOUR_IST):
                t += timedelta(seconds=interval)
                continue

            while cursor < len(delays) and delays[cursor].start <= t:
                active.append(delays[cursor])
                cursor += 1
            active = [s for s in active if (s.end or s.start) > t]

            if not active:
                t += timedelta(seconds=interval)
                continue

            by_trip = {}
            for s in active:
                by_trip.setdefault(s.extra["trip_id"], []).append(s)

            entities = []
            for i, (trip_id, group) in enumerate(sorted(by_trip.items())):
                updates = []
                for s in group:
                    stop = world["stops"][s.entity]
                    # Ramp in over the first three minutes, then hold with jitter.
                    age = (t - s.start).total_seconds()
                    ramp = min(1.0, max(0.15, age / 180.0))
                    delay = int(s.measure * ramp + r.gauss(0, 18))
                    sched = s.start
                    updates.append((s, stop, delay, {
                        "stop_id": s.entity,
                        "stop_sequence": int(stop["name"].rsplit(" ", 1)[-1])
                        if stop["name"].rsplit(" ", 1)[-1].isdigit() else 1,
                        "arrival": {"delay": delay,
                                    "time": int(sched.timestamp()) + delay},
                    }))

                # Some buses are early. Contract says ignore delay <= 0; they still arrive.
                if r.random() < 0.18:
                    route_id = group[0].extra["route_id"]
                    others = sorted(sid for sid, st in world["stops"].items()
                                    if route_id in st["routes"] and sid != group[0].entity)
                    if others:
                        sid2 = others[r.randrange(len(others))]
                        neg = -r.randint(20, 180)
                        updates.append((None, None, neg, {
                            "stop_id": sid2,
                            "stop_sequence": 1,
                            "arrival": {"delay": neg, "time": int(t.timestamp()) - neg},
                        }))

                entities.append({
                    "id": f"e{i + 1}",
                    "trip_update": {
                        "trip": {
                            "trip_id": trip_id,
                            "route_id": group[0].extra["route_id"],
                            "route_name": _route_name(world, group[0].extra["route_id"]),
                        },
                        "stop_time_update": [u[3] for u in updates],
                    },
                })

                for s, stop, delay, _u in updates:
                    if s is None or not crosses_floor("transit.delay", delay):
                        continue
                    raw_ref = ref_transit(trip_id, s.entity)
                    if raw_ref in opened:
                        continue
                    opened.add(raw_ref)
                    eid = make_event_id(raw_ref, "transit.delay")
                    s.raw_ref, s.event_id = raw_ref, eid
                    index.append(entry(eid, raw_ref, "transit.delay", "transit_gtfs",
                                       s.start, stop["h3_cell"], stop["lat"], stop["lon"],
                                       delay, s.truth_id, s.decoy_id))

            msg = {
                "header": {"gtfs_realtime_version": "2.0", "timestamp": int(t.timestamp())},
                "entity": entities,
            }
            fh.write(json.dumps(msg, ensure_ascii=False) + "\n")
            count += 1
            t += timedelta(seconds=interval)

    return count, index


def _route_name(world, route_id: str) -> str:
    from sim.registries import ROUTES
    for rid, a, b in ROUTES:
        if rid == route_id:
            return f"{a} – {b}"
    return route_id
