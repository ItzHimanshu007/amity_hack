"""CONTRACT.md §F.1 -- how much more often a plausible pair actually co-occurs than
chance, measured on the 14-day history.

The question this answers, per plausible pair (A -> B, window W):

    Of the times A happened, how often did a B follow it nearby within W?
    If B events were scattered over the same areas and the same fortnight with no
    relationship to A at all, how often would that have happened anyway?

    lift = observed co-occurrences / chance-expected co-occurrences

Chance is not "uniform over the 591 city cells" -- pollution sensors and bus stops are
not uniformly distributed, and pretending they are inflates every lift. The expectation
is localized instead: for each A event, count how many B events the history holds in
A's own 7-cell neighbourhood, and spread them uniformly over the history span. So a B
that is common *right here* is correctly unsurprising *right here*.

    lambda_local = (B events in disk-1 of A's cell over the whole history) * W / T
    P(at least one B by chance) = 1 - exp(-lambda_local)

summed over A events to give the expected count. Smoothing of LIFT_SMOOTHING on both
sides keeps a pair that has never co-occurred from reporting lift 0 or infinity on a
sample this small.

READ THIS BEFORE USING LIFT AS A GATE. Phase 1 generates the history as deliberately
stationary noise with no planted structure -- sim.verify check (e) asserts exactly that
and fails the build otherwise. So most genuinely plausible pairs measure BELOW 1.0
there -- 0.44 for `complaint.waterlogging -> power.outage`, 0.89 for
`traffic.signal_down -> transit.delay`, both real legs of GT-001's own cascade. A lift
threshold anywhere near 1.0 would veto legs of the headline scenario. (The one pair
that IS structurally deterministic -- `power.outage -> traffic.signal_down`,
CONTRACT.md §D.3's single raw record emitting both at the same instant -- measures a
lift near 44 even in stationary noise, exactly because a co-located, co-timed pair is
what a chance model does not predict; that one was never at risk from a lift gate. It
is the low-lift real pairs that make gating on lift the wrong call here.) Lift is
scored and reported; it never vetoes. LINK_MIN_LIFT exists, is wired in, and ships at
0.0 for this reason -- see CONTRACT.md §F.1 and contract_constants.py.
"""

import math
from collections import defaultdict

from contract_constants import LIFT_SMOOTHING
from engine.plausibility import directed_pairs
from ingest.zones import neighbors


def _epoch(ts_iso: str, parse) -> float:
    return parse(ts_iso).timestamp()


def build_lift_table(history_events: list, parse_utc) -> dict:
    """{(cause, effect): {...}} over every pair in the plausibility table.

    `history_events` are canonical event dicts from the history portion only -- never
    the detection window, for the same reason the Poisson baseline excludes it
    (CONTRACT.md §E.1): a real cascade would otherwise inflate its own baseline.
    """
    if not history_events:
        return {}

    times = {}
    by_cat = defaultdict(list)
    by_cell_cat = defaultdict(list)
    for e in history_events:
        t = parse_utc(e["start_utc"]).timestamp()
        times[e["event_id"]] = t
        by_cat[e["category"]].append((t, e["h3_cell"]))
        by_cell_cat[(e["h3_cell"], e["category"])].append(t)

    t_min = min(times.values())
    t_max = max(times.values())
    span = max(1.0, t_max - t_min)

    table = {}
    for cause, effect, min_sec, max_sec in directed_pairs():
        causes = sorted(by_cat.get(cause, []))
        observed = 0
        expected = 0.0
        lags = []
        for t, cell in causes:
            local = []
            for c in sorted(neighbors(cell, 1)):
                local.extend(by_cell_cat.get((c, effect), ()))
            hits = [u - t for u in local if min_sec <= (u - t) <= max_sec]
            if hits:
                observed += 1
                lags.append(min(hits))
            # Chance: the same neighbourhood's B events, spread flat over the history.
            lam = len(local) * (max_sec - min_sec) / span
            expected += 1.0 - math.exp(-lam)

        value = (observed + LIFT_SMOOTHING) / (expected + LIFT_SMOOTHING)
        hours = span / 3600.0
        table[(cause, effect)] = {
            "pair": [cause, effect],
            "window_sec": max_sec - min_sec,
            "min_gap_sec": min_sec,
            "max_gap_sec": max_sec,
            "n_cause": len(causes),
            "n_effect": len(by_cat.get(effect, [])),
            "cooccurrences": observed,
            "expected_cooccurrences": round(expected, 4),
            "observed_rate_per_hour": round(observed / hours, 6),
            "baseline_rate_per_hour": round(expected / hours, 6),
            "value": round(value, 3),
            "median_lag_sec": int(sorted(lags)[len(lags) // 2]) if lags else None,
            "history_hours": round(hours, 2),
        }
    return table


def lift_for(table: dict, cause: str, effect: str) -> dict:
    """The row for a directed pair, or a neutral row when the pair was never measured."""
    row = table.get((cause, effect))
    if row is not None:
        return row
    return {"pair": [cause, effect], "window_sec": 0, "min_gap_sec": 0, "max_gap_sec": 0,
            "n_cause": 0, "n_effect": 0, "cooccurrences": 0,
            "expected_cooccurrences": 0.0, "observed_rate_per_hour": 0.0,
            "baseline_rate_per_hour": 0.0, "value": 1.0, "median_lag_sec": None,
            "history_hours": 0.0}
