"""Raw DISCOM feed: JSON Lines keyed by feeder_id, no coordinates. CONTRACT.md §D.3.

Two traps for Phase 3, both deliberate:
  - `reported_time` looks ISO-shaped but is naive LOCAL time (IST), not UTC.
  - there are no coordinates at all; feeder_id resolves through /data/feeder_registry.json.

One TRIP record on a signal-bearing feeder produces TWO events -- a power.outage and a
traffic.signal_down -- from the same raw_ref, distinguished by the |category suffix.
That pair is half of the corroboration the §D dedupe carve-out protects.
"""

import json
from datetime import timedelta

from contract_constants import crosses_floor
from sim import config
from sim.ids import make_event_id, ref_power
from sim.index import entry
from sim.profiles import IST_OFFSET


def _ist_naive(dt) -> str:
    """No offset marker. This is the trap in this feed."""
    return (dt + IST_OFFSET).strftime("%Y-%m-%dT%H:%M:%S")


def write(world, specs, seed: int = config.SEED):
    path = config.DATA_DIR / config.OUTPUT_FILES["power_discom"]

    outages = sorted([s for s in specs if s.category == "power.outage"],
                     key=lambda s: s.start)
    # Signal specs that ride along on a power record rather than owning one.
    riders = {}
    for s in specs:
        if s.category == "traffic.signal_down" and s.extra.get("from_power_record"):
            riders[(s.entity, s.start)] = s

    index = []
    lines = []   # (timestamp, record) so TRIPs and RESTOREs interleave in time order

    for s in outages:
        f = world["feeders"][s.entity]
        carries = f["carries_signals"]
        if s.extra.get("force_signals"):
            carries = True
        if s.extra.get("force_no_signals"):
            carries = False
        junctions = s.extra.get("signal_junctions") or f["signal_junctions"] or 1

        rec = {
            "feeder_id": s.entity,
            "substation": f["name"],
            "event": "SCHEDULED_CUT" if s.extra.get("scheduled") else "TRIP",
            "reported_time": _ist_naive(s.start),
            "est_restore_min": int(s.extra.get("est_restore_min", 60)),
            "affected_connections": int(s.measure),
            "carries_signals": carries,
            "cause": s.extra.get("cause", "UNKNOWN"),
        }
        if carries:
            rec["signal_junctions"] = int(junctions)
        lines.append((s.start, rec))

        raw_ref = ref_power(s.entity, s.start)
        s.raw_ref = raw_ref

        if crosses_floor("power.outage", s.measure):
            eid = make_event_id(raw_ref, "power.outage")
            s.event_id = eid
            index.append(entry(eid, raw_ref, "power.outage", "power_discom", s.start,
                               f["h3_cell"], f["lat"], f["lon"], s.measure,
                               s.truth_id, s.decoy_id))

        if carries:
            sd_eid = make_event_id(raw_ref, "traffic.signal_down")
            rider = riders.get((s.entity, s.start))
            if rider is not None:
                rider.raw_ref, rider.event_id = raw_ref, sd_eid
            index.append(entry(
                sd_eid, raw_ref, "traffic.signal_down", "power_discom", s.start,
                f["h3_cell"], f["lat"], f["lon"], junctions,
                truth_id=(rider.truth_id if rider else s.truth_id),
                decoy_id=(rider.decoy_id if rider else s.decoy_id)))

        # A RESTORE closes the outage. It never mints an id (CONTRACT.md §A).
        if s.end:
            lines.append((s.end, {
                "feeder_id": s.entity,
                "substation": f["name"],
                "event": "RESTORE",
                "reported_time": _ist_naive(s.end),
                "est_restore_min": 0,
                "affected_connections": 0,
                "carries_signals": carries,
                "cause": "RESTORED",
            }))

    lines.sort(key=lambda x: x[0])
    with path.open("w", encoding="utf-8") as fh:
        for _, rec in lines:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    return len(lines), index
