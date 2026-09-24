"""Phase 4: flags a (h3_cell, category, 60-min window) as unusual versus its baseline.

Vectorized: builds one row per (h3_cell, category) with any activity in the detection
window, slides a 60-minute window in 5-minute steps across it, and runs the Poisson
tail test on every step at once via numpy/scipy rather than nested Python loops.

CONTRACT.md §E.1 is the schema this produces. Flags when p_value < ANOMALY_P_THRESHOLD
AND observed_count >= ANOMALY_MIN_COUNT -- both from contract_constants.py.
"""

import hashlib
from datetime import timedelta

import numpy as np
import pandas as pd
from scipy import stats

from contract_constants import (ANOMALY_MIN_COUNT, ANOMALY_P_THRESHOLD,
                                ANOMALY_RARE_MIN_COUNT, ANOMALY_RARE_P_THRESHOLD,
                                ANOMALY_RARE_ELIGIBLE_CATEGORIES,
                                ANOMALY_RARE_MIN_SEVERITY,
                                ANOMALY_RARE_TRUSTED_LEVELS, ANOMALY_WINDOW_SEC)
from engine.baseline import lambda_for
from ingest.normalize import IST, iso

STEP_SEC = 300     # slide the 60-min window in 5-minute steps


def _anomaly_id(h3_cell: str, category: str, window_start_iso: str) -> str:
    """CONTRACT.md §E.1: derived, not random -- same window always gets the same id."""
    digest = hashlib.sha1(f"{h3_cell}|{category}|{window_start_iso}".encode()).hexdigest()
    return f"ANOM-{digest[:8]}"


def detect(detection_df: pd.DataFrame, baseline: dict, category_feeds: dict,
          degraded_feeds: set, window_sec: int = ANOMALY_WINDOW_SEC,
          p_threshold: float = ANOMALY_P_THRESHOLD, min_count: int = ANOMALY_MIN_COUNT,
          rare_p_threshold: float = None, rare_min_severity: float = None):
    """Returns a list of anomaly dicts (CONTRACT.md §E.1) for every (cell, category)
    combination present in `detection_df`.

    `category_feeds`: {category: {feed ids that emit it}} (from CONTRACT.md §B), used
    to resolve which feeds are relevant to a category's `degraded_by_stale_feeds`.
    `degraded_feeds`: the set of feed ids Phase 3's health snapshot reports as
    stale/error (see engine.health) -- applied uniformly to every anomaly, not
    time-windowed. See engine/health.py's docstring for why.
    """
    if rare_p_threshold is None:
        rare_p_threshold = ANOMALY_RARE_P_THRESHOLD
    if rare_min_severity is None:
        rare_min_severity = ANOMALY_RARE_MIN_SEVERITY
    if detection_df.empty:
        return []

    df = detection_df.copy()
    df["start_ts"] = pd.to_datetime(df["start_utc"], format="%Y-%m-%dT%H:%M:%SZ", utc=True)
    df["hour_ist"] = df["start_ts"].dt.tz_convert(IST).dt.hour

    window = timedelta(seconds=window_sec)
    step = timedelta(seconds=STEP_SEC)
    t0, t1 = df["start_ts"].min(), df["start_ts"].max()

    # Every 5-minute step from the first possible window start to the last.
    starts = pd.date_range(t0.floor("5min"), (t1 - window).ceil("5min") if t1 > t0 + window
                           else t0.floor("5min"), freq=step)
    if len(starts) == 0:
        starts = pd.DatetimeIndex([t0.floor("5min")])

    anomalies = []
    seen_windows = set()   # (h3_cell, category, window_start) -- a flagged window only
                            # gets emitted once even if two step-offsets land on the
                            # exact same boundary (can't happen with a fixed step, but
                            # keeps this loop safe if STEP_SEC is ever changed to not
                            # evenly divide window_sec).

    for (cell, category), group in df.groupby(["h3_cell", "category"]):
        g = group.sort_values("start_ts")
        ts = g["start_ts"].values.astype("datetime64[ns]")
        hours = g["hour_ist"].values
        event_ids = g["event_id"].values
        sources = g["source"].values
        severities = g["severity"].values

        for w_start in starts:
            w_start_np = np.datetime64(w_start.tz_convert("UTC").tz_localize(None))
            w_end_np = w_start_np + np.timedelta64(window_sec, "s")
            mask = (ts >= w_start_np) & (ts < w_end_np)
            observed = int(mask.sum())
            if observed < min(min_count, ANOMALY_RARE_MIN_COUNT):
                continue

            # lambda for this bucket is keyed on the hour-of-day the WINDOW starts in.
            w_hour = pd.Timestamp(w_start_np, tz="UTC").tz_convert(IST).hour
            lam, level = lambda_for(baseline, cell, category, int(w_hour))
            expected = lam * (window_sec / 3600.0)

            p = float(stats.poisson.sf(observed - 1, expected))


            # Two ways to be unusual. A cascade is one outage and one rain onset --
            # it can never reach a count of 3 -- so volume alone would make the
            # headline scenario undetectable no matter how good the linker is.
            sev_weighted = round(float(severities[mask].sum()), 4)
            volume = observed >= min_count and p < p_threshold
            rare = (observed >= ANOMALY_RARE_MIN_COUNT
                    and p < rare_p_threshold
                    and level in ANOMALY_RARE_TRUSTED_LEVELS
                    and category in ANOMALY_RARE_ELIGIBLE_CATEGORIES
                    and sev_weighted >= rare_min_severity)
            if not (volume or rare):
                continue
            trigger = "volume" if volume else "rare"

            w_start_iso = iso(pd.Timestamp(w_start_np, tz="UTC").to_pydatetime())
            key = (cell, category, w_start_iso)
            if key in seen_windows:
                continue
            seen_windows.add(key)

            w_end_iso = iso(pd.Timestamp(w_end_np, tz="UTC").to_pydatetime())
            contributing = event_ids[mask].tolist()
            src_feeds = sorted(set(sources[mask].tolist()))

            relevant_feeds = category_feeds.get(category, set(src_feeds))
            degraded = relevant_feeds & degraded_feeds

            anomalies.append({
                "anomaly_id": _anomaly_id(cell, category, w_start_iso),
                "h3_cell": cell,
                "category": category,
                "window_start_utc": w_start_iso,
                "window_end_utc": w_end_iso,
                "observed_count": observed,
                "expected_count": round(float(expected), 4),
                "p_value": round(p, 6),
                "trigger": trigger,
                "severity_weighted": sev_weighted,
                "contributing_event_ids": sorted(contributing),
                "source_feeds": src_feeds,
                "degraded_by_stale_feeds": sorted(degraded),
            })

    anomalies.sort(key=lambda a: (a["window_start_utc"], a["h3_cell"], a["category"]))
    return anomalies
