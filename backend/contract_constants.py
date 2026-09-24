"""Every number CONTRACT.md fixes, transcribed literally, in one place.

Four lanes import this module instead of each hardcoding the same figures. If a value
here disagrees with the CONTRACT.md section it came from, the section wins and this
module is the bug.
"""

import uuid

# --- CONTRACT.md §A: event_id derivation -------------------------------------
NAGARNAADI_NS = uuid.UUID("1f0a7b2c-3d4e-4f50-9a61-7b8c9d0e1f20")

# --- CONTRACT.md §C: zone model ----------------------------------------------
H3_RES = 8
CITY_BBOX = (26.79, 75.69, 26.99, 75.89)   # (sw_lat, sw_lon, ne_lat, ne_lon)
CITY_TZ = "Asia/Kolkata"
CITY_CENTRE = (26.9124, 75.7873)
EXPECTED_BBOX_CELLS = 591                   # asserted at generation time

# --- CONTRACT.md §B: the eleven categories -----------------------------------
CATEGORIES = (
    "weather.rain",
    "weather.heat",
    "air.pm25",
    "power.outage",
    "traffic.signal_down",
    "transit.delay",
    "complaint.waterlogging",
    "complaint.garbage",
    "complaint.streetlight",
    "complaint.road_damage",
    "complaint.smoke",
)

COMPLAINT_CATEGORIES = tuple(c for c in CATEGORIES if c.startswith("complaint."))

# category -> the set of feed ids that emit it. traffic.signal_down is the only
# multi-source category (CONTRACT.md §D dedupe carve-out) -- two independent feeds
# reporting the same category on purpose, never merged.
CATEGORY_FEEDS = {
    "weather.rain": {"weather_imd"},
    "weather.heat": {"weather_imd"},
    "air.pm25": {"air_sensors"},
    "power.outage": {"power_discom"},
    "traffic.signal_down": {"power_discom", "civic_complaints"},
    "transit.delay": {"transit_gtfs"},
    "complaint.waterlogging": {"civic_complaints"},
    "complaint.garbage": {"civic_complaints"},
    "complaint.streetlight": {"civic_complaints"},
    "complaint.road_damage": {"civic_complaints"},
    "complaint.smoke": {"civic_complaints"},
}

# --- CONTRACT.md §D: feeds ----------------------------------------------------
FEEDS = {
    "weather_imd":      {"interval_sec": 300, "format": "jsonl", "file": "raw_weather_imd.jsonl"},
    "civic_complaints": {"interval_sec":  60, "format": "csv",   "file": "raw_civic_complaints.csv"},
    "power_discom":     {"interval_sec": 120, "format": "jsonl", "file": "raw_power_discom.jsonl"},
    "transit_gtfs":     {"interval_sec":  30, "format": "jsonl", "file": "raw_transit_gtfs.jsonl"},
    "air_sensors":      {"interval_sec": 180, "format": "jsonl", "file": "raw_air_sensors.jsonl"},
}

# --- CONTRACT.md §A: severity scaling ----------------------------------------
# category -> (floor, ceiling). Below the floor, no event is emitted at all.
SEVERITY_RAMPS = {
    "weather.rain":        (5.0, 40.0),     # mm in 15 min
    "weather.heat":        (38.0, 50.0),    # heat index degC
    "air.pm25":            (60.0, 300.0),   # ug/m3
    "power.outage":        (200.0, 8000.0), # affected_connections
    "traffic.signal_down": (1.0, 6.0),      # junctions dark
    "transit.delay":       (300.0, 2700.0), # seconds
    # every complaint.* shares one ramp: open complaints in the cell, 30 min window
    "complaint.*":         (1.0, 12.0),
}

# --- CONTRACT.md §A: confidence ----------------------------------------------
CONFIDENCE = {
    "sensor_calibrated": 0.90,
    "sensor_uncalibrated": 0.60,
    "resident_complaint": 0.70,
    "landmark_resolved_mult": 0.85,
    "registry_resolved_mult": 0.80,
    "low_battery_mult": 0.50,
    "floor": 0.30,
}

# --- CONTRACT.md §F: pulse score -> alert level -------------------------------
PULSE_THRESHOLDS = ((0, "green"), (25, "yellow"), (50, "orange"), (75, "red"))
ALERT_LEVELS = ("green", "yellow", "orange", "red")

# --- CONTRACT.md §E -----------------------------------------------------------
ACTIVE_WINDOW_SEC = 1800

# --- CONTRACT.md §E.1: anomaly detection --------------------------------------
ANOMALY_WINDOW_SEC = 3600            # rolling 60-minute window

# Volume trigger: a cluster of the same category in one cell.
ANOMALY_P_THRESHOLD = 0.01           # flag when p_value < this
ANOMALY_MIN_COUNT = 3                # AND observed_count >= this

# Rare trigger: a category that essentially never happens in this cell at this hour,
# firing at all. The count>=3 floor exists to stop an unreliable lambda (estimated from
# thin history) calling a single event significant -- so the rare path keeps the strict
# statistics and instead requires the lambda to be well-supported. Without this, a
# cascade is undetectable by construction: it is one power outage, one rain onset and
# two dark junctions, none of which can ever reach a count of 3.
ANOMALY_RARE_P_THRESHOLD = 0.005     # stricter than the volume path
ANOMALY_RARE_MIN_COUNT = 1
# A rare-triggered anomaly must also be CONSEQUENTIAL, not merely statistically odd.
# Measured on held-out data, planted cascade events carry median severity_weighted 0.50
# while rare-trigger false positives sit at median 0.06 -- severity separates the two
# populations far better than the p-value alone, because ordinary civic noise is
# low-magnitude by nature. This gate is what lets the p-threshold stay loose enough to
# catch a lone power outage without dragging in every quiet complaint.
ANOMALY_RARE_MIN_SEVERITY = 0.40
# lambda rungs (engine.baseline.lambda_for) trusted enough for the rare trigger:
ANOMALY_RARE_TRUSTED_LEVELS = ("cell_category_hour", "shrunk_to_prior")

# The rare trigger applies only to DISCRETE-INCIDENT categories. weather.heat and
# air.pm25 are sustained sensor conditions: a hot afternoon is hot for hours and a
# polluted evening is polluted city-wide, so their counts are strongly correlated
# day-to-day and badly over-dispersed relative to Poisson. A single threshold crossing
# there is not surprising and flagging one produced the large majority of all false
# positives (heat + pm25 were 127 of 168 in measurement). They can still be flagged by
# the VOLUME trigger, which is what a genuine heat or pollution cluster looks like.
ANOMALY_RARE_ELIGIBLE_CATEGORIES = frozenset(
    c for c in CATEGORIES if c not in ("weather.heat", "air.pm25"))


# --- CONTRACT.md §F.1: linking (Phase 5) --------------------------------------
# Two anomalies may be linked only when their cells are the same or adjacent.
# CONTRACT.md §C: "spatially related when grid_distance <= 1 ... spatially nearby when
# <= 2. Anything further apart is not one situation."
LINK_MAX_GRID_DISTANCE = 1
# How far out the near-miss scan looks for pairs it will then REJECT. §C's "nearby".
NEARBY_MAX_GRID_DISTANCE = 2
# A near-miss is only worth showing the resident if the two things were close in time
# as well as space. 90 min is the widest window in the plausibility table.
REJECTED_CANDIDATE_MAX_GAP_SEC = 5400

# Lift smoothing: added to both the observed and the chance-expected co-occurrence
# count so a pair that never co-occurred in 14 days reports a finite number instead of
# 0 or infinity.
LIFT_SMOOTHING = 0.5
# Minimum lift for a link to be ACCEPTED. Ships at 0.0 -- i.e. lift never vetoes a
# link -- and that is a measured decision, not an oversight. Phase 1 generates the
# history as stationary noise with no planted structure (sim.verify check (e) hard-
# fails otherwise), so most plausible pairs measure BELOW 1.0 there: measured lift is
# 0.44 for complaint.waterlogging -> power.outage and 0.89 for
# traffic.signal_down -> transit.delay -- both real legs of GT-001's own cascade. Any
# threshold near or above 1.0 would veto legs of the headline scenario. (The one
# pair that IS structurally certain -- power.outage -> traffic.signal_down,
# CONTRACT.md §D.3's single raw record emitting both at once -- measures a lift of
# ~44 even in stationary history, since a co-located, co-timed pair is exactly what a
# chance model does not predict; it was never at risk from this gate. It is the
# LOW-lift real pairs above that make LINK_MIN_LIFT=0.0 the correct setting.) Lift is
# scored into link strength and reported as evidence; the hand-written plausibility
# table is what decides whether a link is allowed at all. The gate stays wired so a
# deployment with real history can turn it on.
LINK_MIN_LIFT = 0.0
# Above this, a pair is worth CLAIMING as "more often than chance" in resident-facing
# text; below it the situation card says the link rests on mechanism, not frequency.
LIFT_STRONG = 1.5
# ...and only when the history actually held this many co-occurrences. Two events
# lining up twice in a fortnight is not a pattern worth putting on a screen.
LIFT_MIN_COOCCURRENCES_TO_CITE = 3

# A Situation may consist of a SINGLE anomaly with no link partner, for the case where
# the downstream step is real but undetectable by a count-based test (CONTRACT.md §E.1
# "known limitation" -- GT-003's magnitude-only air.pm25). Three gates, all required:
#   1. the category is root-capable in the plausibility table (it can start something);
#   2. the category is a discrete incident, not a sustained sensor condition -- the
#      same ANOMALY_RARE_ELIGIBLE_CATEGORIES exclusion Phase 4 uses, and for the same
#      reason: a hot afternoon or a polluted evening is a city-wide condition, not a
#      situation localized to one hex;
#   3. severity_weighted clears this floor.
# Measured on the 14-day history, these three gates together produce 0.14 standalone
# situations per day -- see engine/verify_linker.py check (i).
STANDALONE_MIN_SEVERITY_WEIGHTED = 0.25
# A single anomaly has no independent corroboration by construction, so its confidence
# is capped here however strong it looks on the other axes.
STANDALONE_MAX_CONFIDENCE = "med"

# CONTRACT.md §F confidence table, boundary resolved: "all gaps under 30 min" for high
# and "one gap over 30 min" for med left exactly 1800s undefined. Inclusive: a gap of
# exactly 30 minutes is still `high`. GT-002 lands on this boundary exactly.
CONFIDENCE_HIGH_MAX_GAP_SEC = 1800
CONFIDENCE_HIGH_MIN_SOURCES = 3
CONFIDENCE_MED_MIN_SOURCES = 2
CONFIDENCE_LOW_MEMBER_CONFIDENCE = 0.5
CONFIDENCE_ORDER = ("low", "med", "high")

# Link scoring weights. A link's strength is the product of how close in space, how
# well the gap fits the pair's window, and how much the pair co-occurs above chance.
LINK_W_SPACE = 0.4
LINK_W_TIME = 0.4
LINK_W_LIFT = 0.2

# CONTRACT.md §E.1 carries two trigger types and they are NOT interchangeable evidence.
# A `rare` anomaly is one consequential event (an outage, two dark junctions): its
# weight comes from severity, because there is no count to speak of. A `volume`
# anomaly is a cluster: its weight comes from how far the observed count overshot the
# expected one. Averaging the two onto a single scale would let five trivial complaints
# outvote a substation failure.
EVIDENCE_RARE_SEVERITY_SCALE = 1.0      # severity_weighted -> 0..1 via /(x + this)
EVIDENCE_VOLUME_EXCESS_SCALE = 4.0      # (observed-expected) -> 0..1 via /(x + this)


# --- Phase 6: replay server constants ----------------------------------------

# Allowed replay speeds. CONTRACT.md §E lists 1..16; the prompt requires up to ~200x
# so the 3-hour window completes in ~54 real seconds for the pitch demo.
REPLAY_ALLOWED_SPEEDS = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 100.0, 200.0)

# Bookmarks: named moments in the monsoon_evening scenario.
# Each maps to a simulated UTC timestamp. POST /control jump_to(bookmark) jumps the
# clock directly there, and the caller recomputes served state as-of that instant.
REPLAY_BOOKMARKS = {
    "window_start":         "2026-09-24T12:00:00Z",
    "storm_onset":          "2026-09-24T12:55:00Z",
    "first_situation":      "2026-09-24T13:20:00Z",
    "feed_kill_demo_point": "2026-09-24T13:35:00Z",
    "gt002_onset":          "2026-09-24T13:20:00Z",
    "gt003_onset":          "2026-09-24T13:05:00Z",
    "peak_activity":        "2026-09-24T14:00:00Z",
    "window_end":           "2026-09-24T15:00:00Z",
}

# Chaos-control kill_feed: serve-time confidence penalty.
# "One level down" maps high->med, med->low, low->low.
CHAOS_CONFIDENCE_PENALTY = {
    "high": "med",
    "med":  "low",
    "low":  "low",
}
# Pulse-score serve-time penalty when a confidence drop is applied.
CHAOS_PULSE_PENALTY = 5

# Maximum replay speed the system supports.
REPLAY_MAX_SPEED = 200.0

# Tick interval in real seconds — one tick per real second regardless of speed.
TICK_INTERVAL_SEC = 1.0

# CONTRACT.md §E POST /control: the full action set.
CONTROL_ACTIONS = (
    "play", "pause", "speed", "kill_feed", "resume_feed",
    "set_scenario", "jump_to",
    "delay_feed", "inject_duplicate",
)


def severity_ramp(category: str) -> tuple:
    """Floor/ceiling for a category. All complaint.* share one ramp."""
    if category.startswith("complaint."):
        return SEVERITY_RAMPS["complaint.*"]
    return SEVERITY_RAMPS[category]


def scale_severity(category: str, measure: float) -> float:
    """CONTRACT.md §A: linear ramp, clamped to 0.0-1.0. Below floor is still 0.0 --
    callers decide whether a below-floor reading becomes an event at all."""
    floor, ceiling = severity_ramp(category)
    if measure <= floor:
        return 0.0
    return round(min(1.0, (measure - floor) / (ceiling - floor)), 4)


def crosses_floor(category: str, measure: float) -> bool:
    """True when a raw measurement is high enough to become an event."""
    floor, _ = severity_ramp(category)
    return measure >= floor


def alert_level_for(pulse_score: int) -> str:
    """CONTRACT.md §F. Applies thresholds only -- the is_decoy yellow cap is the
    engine's job, applied after this."""
    level = "green"
    for cutoff, name in PULSE_THRESHOLDS:
        if pulse_score >= cutoff:
            level = name
    return level
