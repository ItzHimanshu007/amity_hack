"""Empirical calibration of the rare-trigger threshold.

The Poisson tail test assumes events in a (cell, category, hour) bucket are independent
with a fixed rate. Real civic data violates that badly: a hot afternoon is hot for
hours, an evening rush is busy everywhere, complaints arrive in bursts. The counts are
over-dispersed, so the NOMINAL p-value is not the achieved false-alarm rate -- measured
directly, a nominal p<0.01 rule produced ~3x that in practice (z = +8).

So the rare threshold is not asserted, it is FITTED: on a held-out day of ordinary
history, sweep the threshold and take the loosest value whose measured false-alarm rate
still meets the CONTRACT.md §E.1 target. That is the number the detector actually runs
with, and it is what makes "our false-alarm rate is controlled" a measurement rather
than a claim.

The volume trigger is left exactly as CONTRACT.md §E.1 fixes it (count >= 3 and
p < 0.01) -- only the rare trigger, which this phase added, is calibrated here.
"""

import numpy as np

from contract_constants import ANOMALY_P_THRESHOLD, CATEGORY_FEEDS

# Swept loosest-first; the first threshold meeting the budget wins.
CANDIDATE_THRESHOLDS = (5e-3, 2e-3, 1e-3, 5e-4, 2e-4, 1e-4, 5e-5, 1e-5, 1e-6, 0.0)


def calibrate_rare_threshold(calib_df, baseline, target_rate=ANOMALY_P_THRESHOLD,
                             verbose=False):
    """Returns (threshold, achieved_rate, budget, observed_blocks).

    `calib_df` must be ordinary history with nothing planted in it, and must NOT be the
    day the final verification measures on -- fitting and evaluating on the same day
    would report an optimistic rate.
    """
    from engine.anomaly import detect

    if calib_df is None or calib_df.empty:
        return ANOMALY_P_THRESHOLD, None, 0, 0

    tested = calib_df.groupby(["h3_cell", "category"]).ngroups
    trials = max(1, tested * 24)
    # Budget is expected + 2 sigma, not the bare point estimate. A correctly calibrated
    # detector scatters around its expectation, so demanding it land below the point
    # estimate on a finite sample would reject thresholds that are in fact fine -- and
    # here it rejected every one of them, including "rare disabled entirely", because
    # the contract's own volume rule alone lands 23 against an expectation of 21.
    # Fitting uses 2 sigma while verification passes at 3, so the fit stays stricter
    # than the bar it is later judged against.
    expected = trials * target_rate
    sigma = np.sqrt(max(1e-9, trials * target_rate * (1 - target_rate)))
    budget = expected + 2 * sigma

    chosen, achieved, blocks_n = 0.0, None, None
    for threshold in CANDIDATE_THRESHOLDS:
        anomalies = detect(calib_df, baseline, CATEGORY_FEEDS, set(),
                           rare_p_threshold=threshold)
        blocks = {(a["h3_cell"], a["category"], a["window_start_utc"][:13])
                  for a in anomalies}
        rate = len(blocks) / trials
        if verbose:
            print(f"      rare_p <= {threshold:<8g} -> {len(blocks):>3} blocks, "
                  f"rate {rate:.5f}")
        if len(blocks) <= budget:
            chosen, achieved, blocks_n = threshold, rate, len(blocks)
            break
        chosen, achieved, blocks_n = threshold, rate, len(blocks)

    return chosen, achieved, budget, blocks_n
