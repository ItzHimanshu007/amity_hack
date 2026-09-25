"""Raw drain feed: storm-drain (nala) level telemetry, SCADA style. CONTRACT.md §D.4.

JDA/JMC-style level gauges on Jaipur's storm drains. Deliberately unlike every other
feed:
  - the record names an RTU id and nothing else about place; the channel's location
    and design depth live in drain_sensor_registry.json
  - `polled` is IST wall-clock text, day first ("24/09/2026 18:32"), no zone marker
  - readings are a list of tagged channels, not fields: LVL_CM is the water level in
    centimetres above the channel bed, BATT_V the RTU battery
  - a faulty gauge reports -999 on LVL_CM, and sometimes drops the channel entirely

A level means nothing without the channel's depth, so the event measure is the level
as a percentage of design capacity. Over 85% the drain is surcharging.
"""

import json
from datetime import timedelta

from contract_constants import FEEDS, crosses_floor
from sim import config
from sim.ids import make_event_id, ref_drain
from sim.index import entry
from sim.profiles import rng

IST = timedelta(hours=5, minutes=30)
FAULT_RATE = 0.003


def polled_text(t) -> str:
    return (t + IST).strftime("%d/%m/%Y %H:%M")


def level_pct(level_cm: float, capacity_cm: float) -> float:
    """The one formula both the writer and ingest use, on the ROUNDED level."""
    return round(level_cm / capacity_cm * 100.0, 1)


def write(world, specs, seed: int = config.SEED):
    r = rng("drain_writer", seed)
    path = config.DATA_DIR / config.OUTPUT_FILES["drain_scada"]
    interval = FEEDS["drain_scada"]["interval_sec"]

    by_rtu = {}
    for s in specs:
        if s.category == "drain.overflow":
            by_rtu.setdefault(s.entity, []).append(s)

    index, count = [], 0
    with path.open("w", encoding="utf-8") as fh:
        for rid, d in world["drains"].items():
            cap = d["capacity_cm"]
            base = r.uniform(0.12, 0.30)          # dry-weather flow, share of depth
            batt = r.uniform(12.2, 13.4)
            is_open = False
            t = config.HISTORY_START
            while t <= config.SIM_END:
                frac = base * r.uniform(0.85, 1.15)
                active = None
                for s in by_rtu.get(rid, []):
                    end = s.end or s.start + timedelta(minutes=60)
                    if s.start <= t < end:
                        span = max(1.0, (end - s.start).total_seconds())
                        x = (t - s.start).total_seconds() / span
                        # Fills fast, stays surcharged a while, drains slowly. The flat
                        # top outlasts a poll interval, so the gauge always sees the peak.
                        if x < 0.2:
                            shape = x / 0.2
                        elif x < 0.5:
                            shape = 1.0
                        else:
                            shape = (1.0 - (x - 0.5) / 0.5) ** 0.6
                        bump = (s.measure / 100.0) * max(0.0, shape)
                        # the first poll of an episode must already be over the floor,
                        # so the opening record is the spec's own start
                        if x < 0.2:
                            bump = max(bump, 0.86 + 0.02 * r.random())
                        if bump > frac:
                            frac = bump
                            active = s
                level = round(frac * cap, 1)
                batt = max(10.8, batt - r.uniform(0.0, 0.0004))
                faulty = r.random() < FAULT_RATE

                channels = []
                if not (faulty and r.random() < 0.4):
                    channels.append({"tag": "LVL_CM", "v": -999 if faulty else level})
                channels.append({"tag": "BATT_V", "v": round(batt, 2)})
                rec = {"rtu": rid, "polled": polled_text(t), "ch": channels}
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                count += 1

                if faulty:
                    # A fault breaks the series: any open episode closes.
                    is_open = False
                    t += timedelta(seconds=interval)
                    continue

                pct = level_pct(level, cap)
                above = crosses_floor("drain.overflow", pct)
                if above and not is_open:
                    raw_ref = ref_drain(rid, t)
                    eid = make_event_id(raw_ref, "drain.overflow")
                    if active is not None and active.raw_ref is None:
                        active.raw_ref, active.event_id = raw_ref, eid
                    index.append(entry(
                        eid, raw_ref, "drain.overflow", "drain_scada", t,
                        d["h3_cell"], d["lat"], d["lon"], pct,
                        truth_id=active.truth_id if active else None,
                        decoy_id=active.decoy_id if active else None))
                is_open = above
                t += timedelta(seconds=interval)

    return count, index
