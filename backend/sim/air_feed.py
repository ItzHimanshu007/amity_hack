"""Raw air feed: JSON Lines, one object per sensor. CONTRACT.md §D.5.

Cheap hardware: sensors drop out for stretches, report null or -1 when faulty, drift up
to 90 s, and only some are calibrated. Coordinates are nested under `loc` with full-word
keys -- deliberately unlike every other feed.
"""

import json
from datetime import timedelta

from contract_constants import crosses_floor
from sim import config
from sim.ids import make_event_id, ref_air
from sim.index import entry
from sim.profiles import AIR, ist_hour, rng


def write(world, specs, seed: int = config.SEED):
    r = rng("air_writer", seed)
    path = config.DATA_DIR / config.OUTPUT_FILES["air_sensors"]
    interval = 180

    pm_specs = [s for s in specs if s.category == "air.pm25"]
    by_sensor = {}
    for s in pm_specs:
        by_sensor.setdefault(s.entity, []).append(s)

    index, count = [], 0
    open_state = {}

    with path.open("w", encoding="utf-8") as fh:
        for sn in world["sensors"]:
            name = sn["sensor"]
            base = r.uniform(26.0, 46.0)          # this sensor's clean-air floor
            battery = r.uniform(35, 100)

            # Dropout stretches: the sensor simply stops reporting.
            dropouts = []
            span_days = (config.SIM_END - config.HISTORY_START).total_seconds() / 86400
            for _ in range(int(span_days * config.SENSOR_DROPOUT_PER_DAY)):
                start = config.HISTORY_START + timedelta(
                    seconds=r.randrange(int((config.SIM_END - config.HISTORY_START).total_seconds())))
                dropouts.append((start, start + timedelta(minutes=r.randint(*config.SENSOR_DROPOUT_MIN))))

            t = config.HISTORY_START
            while t <= config.SIM_END:
                if any(a <= t < b for a, b in dropouts):
                    t += timedelta(seconds=interval)
                    continue

                pm = base * AIR[ist_hour(t)] * r.uniform(0.85, 1.2)
                active = None
                for s in by_sensor.get(name, []):
                    if s.start <= t < (s.end or s.start + timedelta(minutes=60)):
                        span = max(1.0, (s.end - s.start).total_seconds())
                        frac = (t - s.start).total_seconds() / span
                        bump = s.measure * (1.0 - abs(frac - 0.35) / 0.65) ** 0.8
                        if bump > pm:
                            pm = bump
                        active = s
                pm = round(max(4.0, pm), 1)

                battery = max(6.0, battery - r.uniform(0.0, 0.02))
                faulty = r.random() < config.SENSOR_FAULT_RATE
                drift = r.randint(-90, 90)
                captured = (t + timedelta(seconds=drift)).strftime("%Y-%m-%dT%H:%M:%SZ")

                rec = {
                    "sensor": name,
                    "captured": captured,
                    "pm25": None if (faulty and r.random() < 0.5) else (-1 if faulty else pm),
                    "pm10": None if faulty else round(pm * r.uniform(1.25, 1.7), 1),
                    "loc": {"latitude": sn["lat"], "longitude": sn["lon"]},
                    "calibrated": sn["calibrated"],
                    "battery_pct": round(battery),
                }
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                count += 1

                key = (name, "air.pm25")
                above = (not faulty) and crosses_floor("air.pm25", pm)
                if above and not open_state.get(key):
                    raw_ref = ref_air(name, captured)
                    eid = make_event_id(raw_ref, "air.pm25")
                    if active is not None:
                        active.raw_ref, active.event_id = raw_ref, eid
                    index.append(entry(
                        eid, raw_ref, "air.pm25", "air_sensors", t,
                        sn["h3_cell"], sn["lat"], sn["lon"], pm,
                        truth_id=active.truth_id if active else None,
                        decoy_id=active.decoy_id if active else None))
                open_state[key] = above

                t += timedelta(seconds=interval)

    return count, index
