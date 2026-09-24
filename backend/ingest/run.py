"""Orchestrates all five adapters into canonical output. CONTRACT.md §H.

Pipeline: read raw -> adapt -> dedupe (source-scoped) -> rescale complaint clusters ->
sort by start_utc -> write /data/events.jsonl.

Standalone:
    python -m ingest.run
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contract_constants import FEEDS                            # noqa: E402
from ingest.adapters import ADAPTERS                             # noqa: E402
from ingest.cluster import apply_complaint_clusters              # noqa: E402
from ingest.dedupe import assert_carveout_intact, fold           # noqa: E402
from ingest.geocode import Resolver                              # noqa: E402
from ingest.health import from_raw_arrivals                      # noqa: E402
from ingest.normalize import iso                                 # noqa: E402
from ingest.pii import FREE_TEXT_FIELDS                          # noqa: E402

OUTPUT_FILE = "events.jsonl"


def run(data_dir: Path = None, verbose: bool = True, now_iso: str = None):
    data_dir = Path(data_dir) if data_dir else Path(__file__).resolve().parents[2] / "data"
    resolver = Resolver(data_dir)

    all_candidates = []
    per_feed = {}

    for feed, module in ADAPTERS.items():
        candidates, stats = module.extract(resolver, data_dir)
        per_feed[feed] = {"candidates": candidates, "stats": stats}
        all_candidates += candidates
        if verbose:
            print(f"  {feed:<18}{len(candidates):>7} candidates   {stats}")

    events, dedupe_stats = fold(all_candidates)
    cluster_stats = apply_complaint_clusters(events)
    events.sort(key=lambda e: (e["start_utc"], e["event_id"]))

    # Feed health is always "as of" some instant, and a batch run cannot guess it:
    # the latest record in the file may be an outlier tail (a power RESTORE hours after
    # the window everyone else stopped reporting in), which would make every other feed
    # look stale by comparison. Callers that know their clock -- Phase 6's sim_time_utc,
    # Phase 4's detection-window end -- pass it in. The default is the newest arrival.
    last_raw_at = {f: per_feed[f]["stats"].get("last_raw_received_at") for f in FEEDS}
    arrivals = [v for v in last_raw_at.values() if v]
    if now_iso is None:
        now_iso = max(arrivals) if arrivals else (
            events[-1]["received_at"] if events else iso(
                __import__("datetime").datetime.now(__import__("datetime").timezone.utc)))
    health_rows = from_raw_arrivals(
        last_raw_at, now_iso,
        totals={f: per_feed[f]["stats"].get("records", 0) for f in FEEDS},
    )

    out_path = data_dir / OUTPUT_FILE
    with out_path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")

    return {
        "events": events,
        "per_feed": per_feed,
        "dedupe_stats": dedupe_stats,
        "cluster_stats": cluster_stats,
        "signal_down_split": assert_carveout_intact(events),
        "health_rows": health_rows,
        "resolver_stats": resolver.stats,
        "out_path": out_path,
    }


def main():
    t0 = time.time()
    print("nagar naadi ingest -- normalizing five raw feeds into canonical events\n")
    result = run()
    dt = time.time() - t0

    print(f"\ndedupe: removed {result['dedupe_stats']['removed_total']} "
          f"({result['dedupe_stats']['removed_per_source']})")
    print(f"complaint clusters: rescaled {result['cluster_stats']['rescaled']} events "
          f"across {result['cluster_stats']['groups']} (cell, category) groups, "
          f"peak cluster {result['cluster_stats']['peak_cluster']}")
    print(f"signal_down split: {result['signal_down_split']}")
    print(f"resolver: {result['resolver_stats']}")
    print(f"\nwrote {len(result['events'])} canonical events to {result['out_path']} "
          f"in {dt:.2f}s")


if __name__ == "__main__":
    main()
