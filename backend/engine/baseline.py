"""Phase 4: rolling per-cell, per-category baselines that "unusual" is measured against.

Learns lambda (expected events per 60-minute window) per (h3_cell, category,
hour-of-day IST) from the HISTORY portion of canonical events only -- never the
detection window itself, or a real anomaly would shrink its own baseline.

Hour-of-day is IST, not UTC: human and civic activity (Phase 1's own diurnal profiles,
sim/profiles.py) follows the local clock, and learning on the wrong clock would misplace
every peak by 5.5 hours.

Fallback hierarchy so a thin cell/category/hour bucket never divides by zero or gets
silently skipped (CONTRACT.md §E.1):
  1. this (h3_cell, category, hour) bucket's own rate, if it has enough support
  2. this category's city-wide rate at this hour
  3. this category's city-wide rate across all hours
  4. MIN_LAMBDA, so a truly unseen combination still gets a (tiny) defined rate
"""

from datetime import timedelta

import numpy as np
import pandas as pd

from engine.config import MIN_BUCKET_SUPPORT, MIN_LAMBDA, PRIOR_STRENGTH_DAYS
from ingest.normalize import IST


def _ist_hour_series(start_utc: pd.Series) -> pd.Series:
    """Vectorized hour-of-day in IST from a Series of UTC ISO8601 strings."""
    ts = pd.to_datetime(start_utc, format="%Y-%m-%dT%H:%M:%SZ", utc=True)
    return (ts.dt.tz_convert(IST)).dt.hour


def split_history_detection(events: list, lookback_hours: float):
    """CONTRACT.md has no notion of a fixed demo window -- this derives the split from
    the data's own timestamps. Returns (history_df, detection_df, detection_start_utc)."""
    if not events:
        raise ValueError("no canonical events -- run ingest.run first (README §c)")

    df = pd.DataFrame(events)
    df["start_ts"] = pd.to_datetime(df["start_utc"], format="%Y-%m-%dT%H:%M:%SZ", utc=True)
    t_max = df["start_ts"].max()
    detection_start = t_max - timedelta(hours=lookback_hours)

    history = df[df["start_ts"] < detection_start].copy()
    detection = df[df["start_ts"] >= detection_start].copy()
    return history, detection, detection_start


def learn_baseline(history: pd.DataFrame, n_history_days: float) -> dict:
    """Returns {(h3_cell, category, hour): lambda_per_hour}, plus the fallback tables
    needed to answer for a bucket that was never seen in history at all.

    n_history_days is passed in rather than inferred from the data, so a category/hour
    combination with ZERO history occurrences still gets a correctly-scaled fallback
    rate instead of silently vanishing from every table groupby produces.
    """
    if history.empty:
        return {"cell": {}, "cell_category": {}, "category_hour": {}, "category": {},
            "n_days": n_history_days, "active_cells": 0}

    h = history.copy()
    h["hour"] = _ist_hour_series(h["start_utc"])

    # Level 1: per (cell, category, hour).
    cell_counts = (h.groupby(["h3_cell", "category", "hour"]).size()
                   .rename("count").reset_index())
    # "lambda" is a Python keyword -- itertuples() cannot expose it as an attribute,
    # so the rate column is named "lam" instead.
    cell_counts["lam"] = cell_counts["count"] / n_history_days
    cell_map = {}
    for row in cell_counts.itertuples(index=False):
        cell_map[(row.h3_cell, row.category, int(row.hour))] = (float(row.lam), int(row.count))

    # All-hours per (cell, category). The hour-of-day tables above hold only ~14
    # observations per bucket on a 14-day history, so most are empty and a lone event
    # in one looks "rare" purely from thin support. The rare trigger asks a different,
    # coarser question -- does this category happen in this cell AT ALL -- which has 24x
    # the support and is far more stable.
    cell_cat_counts = (h.groupby(["h3_cell", "category"]).size()
                       .rename("count").reset_index())
    cell_cat_map = {}
    for row in cell_cat_counts.itertuples(index=False):
        cell_cat_map[(row.h3_cell, row.category)] = (
            float(row.count) / (n_history_days * 24.0), int(row.count))

    # Level 2: per (category, hour), city-wide -- averaged over the cells that could
    # plausibly see this category, so a quiet cell doesn't drag down a busy one's
    # fallback. We approximate "plausible cells" as the cells that saw ANY event at
    # all in history, which is what a real deployment would also have to assume.
    active_cells = h["h3_cell"].nunique()
    cat_hour_counts = (h.groupby(["category", "hour"]).size()
                       .rename("count").reset_index())
    cat_hour_map = {}
    for row in cat_hour_counts.itertuples(index=False):
        denom = max(1, active_cells) * n_history_days
        cat_hour_map[(row.category, int(row.hour))] = float(row.count) / denom

    # Level 3: per category, all hours, city-wide.
    cat_counts = h.groupby("category").size().rename("count").reset_index()
    cat_map = {}
    for row in cat_counts.itertuples(index=False):
        denom = max(1, active_cells) * 24 * n_history_days
        cat_map[row.category] = float(row.count) / denom

    return {"cell": cell_map, "cell_category": cell_cat_map,
            "category_hour": cat_hour_map, "category": cat_map,
            "n_days": n_history_days, "active_cells": active_cells}


def lambda_for(baseline: dict, h3_cell: str, category: str, hour: int):
    """Expected events/hour for one bucket. Returns (lambda_per_hour, level).

    Gamma-Poisson (Bayesian) shrinkage rather than a hard fallback ladder:

        lambda = (count_in_bucket + alpha * lambda_prior) / (n_days + alpha)

    A hard ladder was the first implementation and it mis-calibrated badly. Its
    city-wide rung divided a category's total by EVERY active cell, averaging one busy
    cell against hundreds of quiet ones, so an ordinary event in a busy cell was judged
    against an artificially tiny lambda and looked rare. Shrinkage fixes that at the
    root: a cell with real history is judged mostly on its own rate, a cell with none
    falls back smoothly to the city-wide prior instead of to near-zero, and every
    bucket gets a defined, non-zero lambda without a floor hack.

    `level` reports how much of the estimate came from the cell's own history, which is
    what the rare trigger gates on.
    """
    key = (h3_cell, category, hour)
    count, support = 0.0, 0
    val = baseline["cell"].get(key)
    if val is not None:
        count, support = val[0] * baseline["n_days"], val[1]

    prior = baseline["category_hour"].get((category, hour))
    if prior is None or prior <= 0:
        prior = baseline["category"].get(category) or MIN_LAMBDA

    n_days = max(1e-9, baseline["n_days"])
    alpha = PRIOR_STRENGTH_DAYS
    lam = (count + alpha * prior) / (n_days + alpha)
    lam = max(lam, MIN_LAMBDA)

    level = "cell_category_hour" if support >= MIN_BUCKET_SUPPORT else "shrunk_to_prior"
    return lam, level


def lambda_rare_for(baseline: dict, h3_cell: str, category: str):
    """Expected events/hour for the RARE trigger: this cell's all-hours rate for this
    category, shrunk toward the city-wide rate.

    Deliberately coarser than lambda_for. "Is this hour unusually busy" (volume) and
    "does this ever happen here at all" (rare) are different questions, and answering
    the second on hour-of-day buckets -- ~14 observations each, most of them empty --
    made every singleton look rare and blew the false-positive rate to z=+8.
    """
    rate, _support = baseline.get("cell_category", {}).get((h3_cell, category), (0.0, 0))
    count = rate * baseline["n_days"] * 24.0

    prior = baseline.get("category", {}).get(category)
    if not prior or prior <= 0:
        prior = MIN_LAMBDA

    n_obs = max(1e-9, baseline["n_days"] * 24.0)
    alpha = PRIOR_STRENGTH_DAYS * 24.0        # same prior weight, in hours
    lam = (count + alpha * prior) / (n_obs + alpha)
    return max(lam, MIN_LAMBDA)
