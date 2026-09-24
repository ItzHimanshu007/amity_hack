"""Self-verification for the generated data. Reads the files back off disk.

Six checks (a)-(f). (b) bbox containment and (d) ground-truth integrity are hard
failures: the first catches transposed lat/lon, the second catches a broken event_id
chain, and both are silent-but-fatal further downstream.

    python -m sim.verify
"""

import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contract_constants import CITY_BBOX                       # noqa: E402
from ingest.zones import in_bbox                               # noqa: E402
from sim import config                                          # noqa: E402
from sim.ids import make_event_id                               # noqa: E402
from sim.profiles import IST_OFFSET                             # noqa: E402

OK, FAIL, WARN = "  ok  ", " FAIL ", " warn "


def _jsonl(name):
    path = config.DATA_DIR / config.OUTPUT_FILES[name]
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def _load_json(name):
    return json.loads((config.DATA_DIR / config.OUTPUT_FILES[name]).read_text("utf-8"))


def _complaint_rows():
    path = config.DATA_DIR / config.OUTPUT_FILES["civic_complaints"]
    with path.open(encoding="utf-8", newline="") as fh:
        yield from csv.DictReader(fh)


def _parse_utc(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------- checks ---

def check_a_counts():
    print("(a) record counts")
    start, end = config.SIM_START, config.SIM_END
    rows = []

    wx = list(_jsonl("weather_imd"))
    demo = sum(1 for r in wx if start <= datetime.fromtimestamp(r["ts"], timezone.utc) <= end)
    rows.append(("weather_imd", len(wx), demo))

    air = list(_jsonl("air_sensors"))
    demo = sum(1 for r in air if start <= _parse_utc(r["captured"]) <= end)
    rows.append(("air_sensors", len(air), demo))

    pw = list(_jsonl("power_discom"))
    demo = sum(1 for r in pw
               if start <= datetime.strptime(r["reported_time"], "%Y-%m-%dT%H:%M:%S")
               .replace(tzinfo=timezone.utc) - IST_OFFSET <= end)
    rows.append(("power_discom", len(pw), demo))

    tr = list(_jsonl("transit_gtfs"))
    demo = sum(1 for m in tr
               if start <= datetime.fromtimestamp(m["header"]["timestamp"], timezone.utc) <= end)
    rows.append(("transit_gtfs", len(tr), demo))

    cp = list(_complaint_rows())
    rows.append(("civic_complaints", len(cp), None))

    idx = list(_jsonl("event_index"))
    ok = True
    print(f"      {'feed':<18}{'total':>9}{'history':>10}{'demo':>8}")
    for name, total, demo in rows:
        hist = (total - demo) if demo is not None else None
        h = f"{hist:>10}" if hist is not None else f"{'-':>10}"
        d = f"{demo:>8}" if demo is not None else f"{'-':>8}"
        print(f"      {name:<18}{total:>9}{h}{d}")
        if demo is not None and hist is not None and hist <= demo:
            ok = False
    print(f"      event index: {len(idx)} events "
          f"({sum(1 for e in idx if e['truth_id'])} planted, "
          f"{sum(1 for e in idx if e['decoy_id'])} decoy)")
    print(f"{OK if ok else FAIL} history dominates the demo window in every feed")
    return ok


def check_b_bbox():
    print("(b) bbox containment  [hard fail]")
    sw_lat, sw_lon, ne_lat, ne_lon = CITY_BBOX
    escapes = []

    def test(src, lat, lon, ref):
        if not in_bbox(lat, lon):
            escapes.append((src, lat, lon, ref))

    for r in _jsonl("weather_imd"):
        test("weather_imd", r["lat"], r["lon"], r["station_id"])
    for r in _jsonl("air_sensors"):
        test("air_sensors", r["loc"]["latitude"], r["loc"]["longitude"], r["sensor"])
    for fid, f in _load_json("feeder_registry").items():
        test("feeder_registry", f["lat"], f["lon"], fid)
    for sid, s in _load_json("stop_registry").items():
        test("stop_registry", s["lat"], s["lon"], sid)
    for e in _jsonl("event_index"):
        test("event_index", e["lat"], e["lon"], e["event_id"][:8])

    print(f"      bbox {sw_lat},{sw_lon} .. {ne_lat},{ne_lon}")
    if escapes:
        for src, lat, lon, ref in escapes[:8]:
            print(f"      ESCAPED {src} {ref}: {lat},{lon}")
        print(f"{FAIL} {len(escapes)} coordinates outside the bbox "
              f"(transposed lat/lon?)")
        return False
    print(f"{OK} every coordinate in every feed and registry is inside the bbox")
    return True


def check_c_registries():
    print("(c) registry integrity")
    feeders = set(_load_json("feeder_registry"))
    stops = set(_load_json("stop_registry"))

    missing_f = {r["feeder_id"] for r in _jsonl("power_discom")} - feeders
    missing_s = set()
    for m in _jsonl("transit_gtfs"):
        for ent in m["entity"]:
            for u in ent["trip_update"]["stop_time_update"]:
                if u["stop_id"] not in stops:
                    missing_s.add(u["stop_id"])

    print(f"      power references {len(feeders - missing_f)} known feeders, "
          f"{len(missing_f)} unknown")
    print(f"      transit references stops, {len(missing_s)} unknown")
    ok = not missing_f and not missing_s
    print(f"{OK if ok else FAIL} every feeder_id and stop_id resolves through a registry")
    return ok


def check_d_ground_truth():
    print("(d) ground-truth integrity  [hard fail]")
    gt = _load_json("ground_truth")
    index = {e["event_id"]: e for e in _jsonl("event_index")}

    # raw_ref sets, built from the raw files themselves
    refs = set()
    for r in _jsonl("weather_imd"):
        refs.add(f"weather_imd:{r['station_id']}@{r['ts']}")
    for r in _jsonl("air_sensors"):
        refs.add(f"air_sensors:{r['sensor']}@{r['captured']}")
    for r in _jsonl("power_discom"):
        ts = int((datetime.strptime(r["reported_time"], "%Y-%m-%dT%H:%M:%S")
                  .replace(tzinfo=timezone.utc) - IST_OFFSET).timestamp())
        refs.add(f"power_discom:{r['feeder_id']}@{ts}")
    for m in _jsonl("transit_gtfs"):
        for ent in m["entity"]:
            tid = ent["trip_update"]["trip"]["trip_id"]
            for u in ent["trip_update"]["stop_time_update"]:
                refs.add(f"transit_gtfs:{tid}@{u['stop_id']}")
    for row in _complaint_rows():
        refs.add(f"civic_complaints:{row['complaint_id']}")

    dangling, mismatched, total = [], [], 0
    groups = ([(s["truth_id"], s["member_event_ids"]) for s in gt["planted_situations"]]
              + [(d["decoy_id"], d["member_event_ids"]) for d in gt["decoys"]])
    for gid, members in groups:
        for eid in members:
            total += 1
            e = index.get(eid)
            if e is None:
                dangling.append((gid, eid, "not in event index"))
                continue
            if e["raw_ref"] not in refs:
                dangling.append((gid, eid, f"raw_ref absent from raw feed: {e['raw_ref']}"))
                continue
            # Re-derive independently: the chain raw_ref -> id must reproduce.
            if make_event_id(e["raw_ref"], e["category"]) != eid:
                mismatched.append((gid, eid, e["raw_ref"]))

    print(f"      {total} member ids across {len(groups)} planted situations and decoys")
    print(f"      {len(refs)} distinct raw_refs found in the raw feeds")
    if dangling or mismatched:
        for gid, eid, why in (dangling + mismatched)[:8]:
            print(f"      {gid} {eid[:8]}: {why}")
        print(f"{FAIL} {len(dangling)} dangling, {len(mismatched)} derivation mismatches")
        return False
    print(f"{OK} every member id resolves to a real raw record and re-derives correctly")
    return True


def check_e_stationarity():
    print("(e) history stationarity")
    idx = [e for e in _jsonl("event_index")
           if _parse_utc(e["start_utc"]) < config.SIM_START]

    planted_in_history = [e for e in idx if e["truth_id"] or e["decoy_id"]]
    per_day = Counter(e["start_utc"][:10] for e in idx)
    days = sorted(per_day)
    counts = [per_day[d] for d in days]
    # The first and last partial days are not comparable; drop them.
    body = counts[1:-1] if len(counts) > 2 else counts
    mean = statistics.mean(body)
    cv = statistics.pstdev(body) / mean if mean else 0.0
    peak = max(body) / statistics.median(body) if body else 0

    by_hour = defaultdict(list)
    for e in idx:
        by_hour[_parse_utc(e["start_utc"]).hour].append(e)

    print(f"      {len(idx)} baseline events over {len(days)} days, "
          f"mean {mean:.0f}/day")
    print(f"      day-to-day CV {cv:.3f} (want < 0.35), "
          f"peak/median {peak:.2f} (want < 2.0)")
    print(f"      planted or decoy events inside the history window: "
          f"{len(planted_in_history)} (want 0)")
    ok = cv < 0.35 and peak < 2.0 and not planted_in_history
    print(f"{OK if ok else FAIL} history is stationary noise with no planted structure")
    return ok


def check_f_signal_provenance():
    print("(f) signal_down provenance")
    idx = list(_jsonl("event_index"))
    sd = [e for e in idx if e["category"] == "traffic.signal_down"]
    by_source = Counter(e["source"] for e in sd)
    print(f"      {len(sd)} traffic.signal_down events: {dict(by_source)}")

    gt = _load_json("ground_truth")
    by_id = {e["event_id"]: e for e in idx}
    pairs_ok = True
    for s in gt["planted_situations"]:
        members = [by_id[m] for m in s["member_event_ids"] if m in by_id]
        sdm = [m for m in members if m["category"] == "traffic.signal_down"]
        if not sdm:
            continue
        sources = {m["source"] for m in sdm}
        ids = {m["event_id"] for m in sdm}
        print(f"      {s['truth_id']}: {len(sdm)} signal_down, sources {sorted(sources)}, "
              f"{len(ids)} distinct ids")
        if len(sdm) >= 2 and (len(sources) < 2 or len(ids) < len(sdm)):
            pairs_ok = False

    both = {"power_discom", "civic_complaints"} <= set(by_source)
    ok = both and pairs_ok
    print(f"{OK if ok else FAIL} signal_down arrives from both feeds, never merged")
    return ok


def run_all() -> bool:
    print("verification")
    results = [
        ("a counts", check_a_counts(), False),
        ("b bbox", check_b_bbox(), True),
        ("c registries", check_c_registries(), False),
        ("d ground truth", check_d_ground_truth(), True),
        ("e stationarity", check_e_stationarity(), False),
        ("f signal provenance", check_f_signal_provenance(), False),
    ]
    print()
    hard_failed = [n for n, ok, hard in results if not ok and hard]
    soft_failed = [n for n, ok, hard in results if not ok and not hard]
    if hard_failed:
        print(f"HARD FAIL: {', '.join(hard_failed)} -- data is not usable, fix and regenerate")
        return False
    if soft_failed:
        print(f"passed with warnings: {', '.join(soft_failed)} need a look")
        return True
    print("all six checks passed")
    return True


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
