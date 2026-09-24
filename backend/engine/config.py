"""Engine-only settings. Not CONTRACT.md-fixed: nothing here needs cross-lane agreement,
unlike contract_constants.py, which is why it lives in this directory rather than there.
"""

from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
EVENTS_FILE = "events.jsonl"
ANOMALIES_FILE = "anomalies.jsonl"

# How the engine tells "recent, to be scanned for anomalies" apart from "history, to
# learn a baseline from" -- WITHOUT importing backend/sim. In live mode there is no
# sim.config with a hardcoded demo window; a real deployment would always be asking
# "is anything unusual in the last few hours" against everything older. So the split is
# derived from the canonical events' own timestamps: the detection window is the last
# DETECTION_LOOKBACK_HOURS of data, and everything strictly before that is history.
# In this simulated run that lands exactly on sim.config's 3-hour demo window, without
# the engine ever depending on the simulator to know it.
DETECTION_LOOKBACK_HOURS = 3

# A (h3_cell, category, hour-of-day) bucket needs at least this many historical
# occurrences before its own rate is trusted; below it we fall back to a coarser
# estimate (CONTRACT.md §E.1 baseline-learning fallback rule).
MIN_BUCKET_SUPPORT = 5

# Strength of the city-wide prior in the Gamma-Poisson shrinkage estimator, in
# "pseudo-days". Higher = trust the city-wide rate more when a cell's own history is
# thin; lower = let a quiet cell look quieter. See engine.baseline.lambda_for.
PRIOR_STRENGTH_DAYS = 5.0

# Numerical guard only: poisson.sf needs lambda > 0. With the shrinkage estimator in
# engine.baseline this is nearly vestigial (lambda is already > 0 whenever the city-wide
# prior is), so it must be a true epsilon. It was originally 0.02, which was not a guard
# but an assertion that every cell sees ~1 event per 50 hours -- that clamped genuinely
# rare buckets UPWARD and made a lone power outage look ordinary (p = 1 - e^-0.02 =
# 0.0198, just above both thresholds), silently capping recall on exactly the singleton
# events a cascade is made of.
MIN_LAMBDA = 1e-6
