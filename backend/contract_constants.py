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
