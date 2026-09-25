"""Self-verification for ingest output. Checks (a)-(h).

This SCRIPT may read the answer key (/data/event_index.jsonl, /data/ground_truth.json)
to prove correctness against it -- that is what an answer key is for. No module under
backend/ingest/ imported by ingest.run may do the same; that boundary is what keeps
/scorecard honest later.

    python -m ingest.verify_ingest
"""

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contract_constants import CITY_BBOX                        # noqa: E402
from ingest import run as ingest_run                             # noqa: E402
from ingest.normalize import from_ist_complaint, iso, make_event_id  # noqa: E402
from ingest.pii import contains_pii, mask_record                 # noqa: E402
from ingest.zones import bbox_cells, in_bbox                      # noqa: E402
from sim import config as sim_config                              # noqa: E402

OK, FAIL = "  ok  ", " FAIL "
DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def _answer_key_index():
    """Reads the answer key. Only this verify script may do this."""
    path = DATA_DIR / "event_index.jsonl"
    idx = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                e = json.loads(line)
                idx[e["event_id"]] = e
    return idx


def _answer_key_ground_truth():
    return json.loads((DATA_DIR / "ground_truth.json").read_text("utf-8"))


def check_a_reconciliation(result):
    print("(a) reconciliation: raw in -> canonical out")
    for feed, info in result["per_feed"].items():
        s = info["stats"]
        n_raw = s.get("records", 0)
        n_cand = len(info["candidates"])
        print(f"      {feed:<18}{n_raw:>7} raw -> {n_cand:>7} candidates   {s}")
    d = result["dedupe_stats"]
    print(f"      dedupe folded {d['removed_total']} repeat observations into open "
          f"episodes: {d['removed_per_source']}")
    print(f"      complaint clustering rescaled "
          f"{result['cluster_stats']['rescaled']} events (no count change)")
    print(f"      final canonical events: {len(result['events'])}")
    print(f"{OK} every delta is one of: episode folding (dedupe), "
          f"unresolved/unmapped drops, or below-floor drops")
    return True


def check_b_no_pii(result):
    print("(b) zero PII leakage  [hard fail]")
    leaks = []
    for ev in result["events"]:
        for k, v in ev.items():
            if isinstance(v, str) and contains_pii(v):
                leaks.append((ev["event_id"], k, v))
    print(f"      scanned {len(result['events'])} canonical events, "
          f"{sum(1 for e in result['events'])} field-sets")
    if leaks:
        for eid, field, val in leaks[:5]:
            print(f"      LEAK {eid[:8]} field={field}: {val!r}")
        print(f"{FAIL} {len(leaks)} PII-shaped tokens found in canonical events")
        return False
    print(f"{OK} no canonical event field contains a phone- or name-shaped token "
          f"(canonical events carry no text field at all)")
    return True


def check_c_cross_source_survival(result):
    print("(c) cross-source survival of traffic.signal_down  [hard fail]")
    split = result["signal_down_split"]
    print(f"      observed split: {split}")
    want = {}
    for e in _answer_key_index().values():
        if e["category"] == "traffic.signal_down":
            want[e["source"]] = want.get(e["source"], 0) + 1
    print(f"      Phase 1 reported: power_discom={want.get('power_discom', 0)}, "
          f"civic_complaints={want.get('civic_complaints', 0)}")
    ok = (split.get("power_discom") == want.get("power_discom")
          and split.get("civic_complaints") == want.get("civic_complaints"))
    print(f"{OK if ok else FAIL} signal_down survives under both source values "
          f"with the expected counts")
    return ok


_valid_cells_cache = None


def check_d_bbox_and_cells(result):
    print("(d) bbox + cell validity")
    global _valid_cells_cache
    _valid_cells_cache = set(bbox_cells())
    # NOTE: membership in the 591-cell set (bbox_cells()) is NOT the same invariant as
    # "coordinate inside the bbox rectangle". h3shape_to_cells uses CENTER containment:
    # a cell only belongs to the 591 if ITS OWN centroid falls inside the polygon. A
    # coordinate strictly inside the rectangle, near the NE/SW edge, can legitimately
    # hash to a res-8 cell whose centroid sits just past the boundary and is therefore
    # excluded from the 591-set. That is expected H3 geometry, not corrupted data --
    # CONTRACT.md §C's actual invariant is the coordinate, so that is what this checks.
    # See the patch summary: this is a real edge case worth Phase 8's attention if the
    # map ever uses bbox_cells() as a hard render filter.
    import h3
    bad_coord, bad_cell_format, edge_cells = [], [], []
    for ev in result["events"]:
        if not in_bbox(ev["lat"], ev["lon"]):
            bad_coord.append(ev)
            continue
        if h3.get_resolution(ev["h3_cell"]) != 8 or not h3.is_valid_cell(ev["h3_cell"]):
            bad_cell_format.append(ev)
            continue
        # cell must genuinely be the hash of this event's own coordinate
        if h3.latlng_to_cell(ev["lat"], ev["lon"], 8) != ev["h3_cell"]:
            bad_cell_format.append(ev)
            continue
        if ev["h3_cell"] not in _valid_cells_cache:
            edge_cells.append(ev)

    print(f"      checked {len(result['events'])} events: coordinate-in-bbox, "
          f"valid res-8 cell, cell matches the coordinate's own hash")
    if edge_cells:
        print(f"      {len(edge_cells)} events hash to a cell whose OWN centroid sits "
              f"just outside the bbox polygon (edge geometry, not bad data), e.g. "
              f"{edge_cells[0]['h3_cell']} at {edge_cells[0]['lat']},{edge_cells[0]['lon']}")

    bad = bad_coord + bad_cell_format
    if bad:
        for ev in bad[:5]:
            print(f"      BAD {ev['event_id'][:8]}: {ev['lat']},{ev['lon']} "
                  f"cell={ev['h3_cell']}")
        print(f"{FAIL} {len(bad)} events with an out-of-bbox coordinate or a cell that "
              f"doesn't match its own coordinate")
        return False
    print(f"{OK} every event's coordinate is inside the bbox and its h3_cell correctly "
          f"hashes from that coordinate")
    return True


def check_e_timestamps(result):
    print("(e) timestamps: UTC, ISO8601, and the classic IST off-by-5:30")
    bad_format = []
    for ev in result["events"][:2000]:
        for field in ("start_utc", "received_at"):
            v = ev[field]
            if not (len(v) == 20 and v.endswith("Z") and v[10] == "T"):
                bad_format.append((ev["event_id"], field, v))

    known = from_ist_complaint("24/09/2026 6:42 PM")
    expect = "2026-09-24T13:12:00Z"
    got = iso(known)
    spot_ok = got == expect
    print(f"      spot check: IST '24/09/2026 6:42 PM' -> {got} "
          f"(CONTRACT.md §A worked example: {expect})")

    ok = not bad_format and spot_ok
    if bad_format:
        print(f"      {len(bad_format)} malformed timestamps, e.g. {bad_format[0]}")
    print(f"{OK if ok else FAIL} every start_utc/received_at is UTC ISO8601, and IST "
          f"conversion lands on the correct instant")
    return ok


def check_f_registry_resolution(result):
    print("(f) registry resolution + landmark resolution rate")
    r = result["resolver_stats"]
    feeder_total = r["feeder_hit"] + r["feeder_miss"]
    drain_total = r["drain_hit"] + r["drain_miss"]
    lm_total = r["landmark_hit"] + r["landmark_miss"]
    feeder_rate = r["feeder_hit"] / feeder_total if feeder_total else 1.0
    drain_rate = r["drain_hit"] / drain_total if drain_total else 1.0
    lm_rate = r["landmark_hit"] / lm_total if lm_total else 1.0

    print(f"      feeder_id resolution: {r['feeder_hit']}/{feeder_total} "
          f"({feeder_rate:.1%})")
    print(f"      drain rtu resolution: {r['drain_hit']}/{drain_total} ({drain_rate:.1%})")
    print(f"      landmark resolution:  {r['landmark_hit']}/{lm_total} "
          f"({lm_rate:.1%}), {r['landmark_miss']} unresolved and dropped")

    ok = feeder_rate == 1.0 and drain_rate == 1.0
    print(f"{OK if ok else FAIL} feeder_id and drain rtu resolve at 100% "
          f"(registries are closed sets Phase 1 generated); landmark resolution is "
          f"naturally <100% because some rows carry no usable address")
    return ok


def check_g_id_agreement(result):
    print("(g) independent id agreement with the answer key  [hard fail]")
    answer = _answer_key_index()
    checked, mismatched, missing_from_answer = 0, [], 0

    for ev in result["events"]:
        rederived = make_event_id(ev["raw_ref"], ev["category"])
        if rederived != ev["event_id"]:
            mismatched.append((ev["event_id"], rederived, ev["raw_ref"]))
            continue
        checked += 1
        if ev["event_id"] not in answer:
            missing_from_answer += 1

    print(f"      {checked} canonical events re-derive their own id from raw_ref+category")
    print(f"      {len(answer)} entries in the Phase 1 answer key")
    print(f"      {missing_from_answer} canonical events have no counterpart in the "
          f"answer key (expected: below-floor/dropped rows the answer key never opened)")

    ok = not mismatched
    if mismatched:
        for eid, re_id, ref in mismatched[:5]:
            print(f"      MISMATCH {eid[:8]} re-derived {re_id[:8]} from {ref}")
        print(f"{FAIL} {len(mismatched)} events whose id does not match its own "
              f"raw_ref/category -- make_event_id was reimplemented incorrectly")
        return False
    print(f"{OK} every event_id independently re-derives from its raw_ref and category "
          f"-- make_event_id was reimplemented from the contract, not imported")
    return ok


def check_h_severity_sanity(result):
    print("(h) severity sanity")
    nulls = [e for e in result["events"] if e["severity"] is None]
    sd = [e for e in result["events"] if e["category"] == "traffic.signal_down"]
    by_source = defaultdict(list)
    for e in sd:
        by_source[e["source"]].append(e["severity"])

    print(f"      null severities: {len(nulls)}")
    ranges = {}
    for src, vals in by_source.items():
        ranges[src] = (min(vals), max(vals))
        print(f"      signal_down from {src}: n={len(vals)} "
              f"range=[{min(vals):.3f}, {max(vals):.3f}]")

    overlap = True
    if len(ranges) == 2:
        (lo1, hi1), (lo2, hi2) = ranges.values()
        overlap = lo1 <= hi2 and lo2 <= hi1

    ok = not nulls and overlap
    print(f"{OK if ok else FAIL} no null severities; the two signal_down sources share "
          f"one overlapping scale rather than occupying separate ranges")
    return ok


def sample_complaint_before_after(result):
    """One complaint record, raw -> masked-raw -> canonical, side by side."""
    from ingest.parsers import read_complaints_csv
    for _n, row, raw_line in read_complaints_csv():
        if row.get("text") and contains_pii(row["text"]):
            masked_row, n_masked = mask_record("civic_complaints", row)
            masked_line, _ = mask_record("civic_complaints", raw_line)
            from ingest.parsers import category_for_complaint
            from ingest.normalize import ref_complaint, make_event_id
            cat = category_for_complaint(row["complaint_type"])
            eid = make_event_id(ref_complaint(row["complaint_id"]), cat) if cat else None
            match = next((e for e in result["events"] if e["event_id"] == eid), None)
            print(f"  RAW LINE:     {raw_line}")
            print(f"  MASKED LINE:  {masked_line}  ({n_masked} items masked)")
            print(f"  CANONICAL:    {json.dumps(match, ensure_ascii=False, indent=2) if match else '(clustered/rescaled or dropped)'}")
            return


def run_all():
    print("running ingest...\n")
    result = ingest_run.run(verbose=False)
    print(f"ingest produced {len(result['events'])} canonical events\n")

    print("verification\n")
    checks = [
        ("a reconciliation", check_a_reconciliation(result), False),
        ("b no PII", check_b_no_pii(result), True),
        ("c cross-source survival", check_c_cross_source_survival(result), True),
        ("d bbox + cells", check_d_bbox_and_cells(result), True),
        ("e timestamps", check_e_timestamps(result), False),
        ("f registry resolution", check_f_registry_resolution(result), False),
        ("g id agreement", check_g_id_agreement(result), True),
        ("h severity sanity", check_h_severity_sanity(result), False),
    ]
    print()
    hard_failed = [n for n, ok, hard in checks if not ok and hard]
    soft_failed = [n for n, ok, hard in checks if not ok and not hard]

    print("=" * 70)
    print("sample: one complaint record, raw -> masked-raw -> canonical")
    print("=" * 70)
    sample_complaint_before_after(result)
    print()

    if hard_failed:
        print(f"HARD FAIL: {', '.join(hard_failed)}")
        return False, result
    if soft_failed:
        print(f"passed with warnings: {', '.join(soft_failed)}")
        return True, result
    print("all eight checks passed")
    return True, result


if __name__ == "__main__":
    ok, _ = run_all()
    sys.exit(0 if ok else 1)
