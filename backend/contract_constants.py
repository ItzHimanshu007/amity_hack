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
