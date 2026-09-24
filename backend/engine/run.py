"""Orchestrates baseline learning + rolling detection into /data/anomalies.jsonl.

Pipeline: load canonical events -> split history/detection by timestamp (no sim.config
dependency) -> learn baseline from history -> run Phase 3's own health snapshot for
degraded-feed awareness -> detect anomalies over the detection window -> write, sorted.

Standalone:
    python -m engine.run
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contract_constants import CATEGORY_FEEDS                    # noqa: E402
from engine.anomaly import detect                                  # noqa: E402
from engine.baseline import learn_baseline, split_history_detection  # noqa: E402
from engine.calibrate import calibrate_rare_threshold                # noqa: E402
from engine.config import ANOMALIES_FILE, DATA_DIR, DETECTION_LOOKBACK_HOURS  # noqa: E402
from engine.health import degraded_feeds_snapshot                  # noqa: E402
from ingest import run as ingest_run                                # noqa: E402
from ingest.normalize import iso                                    # noqa: E402


def load_canonical_events(data_dir: Path = None) -> list:
    """Reads Phase 3's output. Detection stands on canonical events only -- never a
    raw feed file, never the answer key."""
    data_dir = Path(data_dir) if data_dir else DATA_DIR
    path = data_dir / "events.jsonl"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing -- run `python -m ingest.run` first (Phase 3)")
    events = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def run(data_dir: Path = None, verbose: bool = True):
    data_dir = Path(data_dir) if data_dir else DATA_DIR
    events = load_canonical_events(data_dir)
    if verbose:
        print(f"  loaded {len(events)} canonical events from {data_dir / 'events.jsonl'}")

    history, detection, detection_start = split_history_detection(
        events, DETECTION_LOOKBACK_HOURS)
    n_history_days = max(
        1e-6, (detection_start - history["start_ts"].min()).total_seconds() / 86400
    ) if len(history) else 0.0
    if verbose:
        print(f"  history: {len(history)} events over {n_history_days:.2f} days "
              f"(ending {detection_start})")
        print(f"  detection window: {len(detection)} events from "
              f"{detection_start} onward")

    baseline = learn_baseline(history, n_history_days)
    if verbose:
        print(f"  baseline buckets: {len(baseline['cell'])} (cell,category,hour), "
              f"{len(baseline['category_hour'])} (category,hour) fallback, "
              f"{len(baseline['category'])} category fallback")

    # Phase 3's own end-of-run health snapshot, computed exactly as ingest.run does it.
    # This RE-RUNS ingest so the snapshot reflects the same data being detected on --
    # a live deployment would instead read Phase 3's current /state, not recompute it.
    # "Now", for anomaly purposes, is the end of the detection window -- the instant
    # these anomalies are being evaluated as of.
    now_iso = iso(detection["start_ts"].max().to_pydatetime()) if len(detection) else None
    ingest_result = ingest_run.run(data_dir, verbose=False, now_iso=now_iso)
    degraded = degraded_feeds_snapshot(ingest_result["health_rows"])
    if verbose:
        print(f"  feed health snapshot: degraded feeds = {sorted(degraded) or 'none'}")

    # Fit the rare threshold on an ordinary held-out day. Deliberately the
    # SECOND-TO-LAST history day: verification measures the false-alarm rate on the
    # last one, and fitting and measuring on the same day would report a flattered
    # number.
    rare_p, achieved, budget, blocks = (None, None, None, None)
    if len(history):
        day = history["start_ts"].dt.floor("D")
        days = sorted(day.unique())
        if len(days) >= 3:
            calib_day = days[-2]
            calib_df = history[day == calib_day].copy()
            train = history[day < calib_day]
            n_train = max(1e-6, (calib_day - train["start_ts"].min()).total_seconds() / 86400)
            rare_p, achieved, budget, blocks = calibrate_rare_threshold(
                calib_df, learn_baseline(train, n_train))
            if verbose:
                print(f"  rare threshold calibrated on {str(calib_day)[:10]}: "
                      f"p < {rare_p:g} (achieved {achieved:.5f}, "
                      f"{blocks} blocks vs budget {budget:.1f})")

    anomalies = detect(detection, baseline, CATEGORY_FEEDS, degraded,
                       rare_p_threshold=rare_p)
    if verbose:
        print(f"  flagged {len(anomalies)} anomalies")

    out_path = data_dir / ANOMALIES_FILE
    with out_path.open("w", encoding="utf-8") as fh:
        for a in anomalies:
            fh.write(json.dumps(a, ensure_ascii=False) + "\n")

    return {
        "events": events, "history": history, "detection": detection,
        "detection_start": detection_start, "n_history_days": n_history_days,
        "baseline": baseline, "degraded_feeds": degraded,
        "anomalies": anomalies, "out_path": out_path,
        "rare_p_threshold": rare_p, "calibration_achieved_rate": achieved,
    }


def main():
    t0 = time.time()
    print("nagar naadi anomaly detection -- learning baselines, scanning for the unusual\n")
    result = run()
    dt = time.time() - t0
    print(f"\nwrote {len(result['anomalies'])} anomalies to {result['out_path']} "
          f"in {dt:.2f}s")


if __name__ == "__main__":
    main()
