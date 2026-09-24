"""Phase 1 generation settings. One master seed; everything else derives from it."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 42

# /data sits beside /backend, not inside it.
DATA_DIR = Path(__file__).resolve().parents[2] / "data"

# --- the window ---------------------------------------------------------------
# History ends exactly where the demo window starts, so there is no gap between them.
SIM_START = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)   # 17:30 IST
SIM_END = datetime(2026, 9, 24, 15, 0, 0, tzinfo=timezone.utc)     # 20:30 IST
HISTORY_DAYS = 14
HISTORY_START = SIM_START - timedelta(days=HISTORY_DAYS)
HISTORY_END = SIM_START

# --- entity counts ------------------------------------------------------------
N_WEATHER_STATIONS = 6
N_AIR_SENSORS = 8
N_FEEDERS = 120
FEEDERS_PER_LANDMARK = 6          # the rest are scattered uniformly
FRACTION_FEEDERS_WITH_SIGNALS = 0.35
N_ROUTES = 10
STOPS_PER_ROUTE = 18

# Share of city cells that flood first. Waterlogging complaints prefer these.
FRACTION_LOW_LYING = 0.18

# --- baseline rates (events per hour, city-wide, before the diurnal multiplier) -
# Tuned so 14 days produces a stable baseline without burying the demo window.
BASELINE_RATES = {
    "complaint.waterlogging": 0.25,
    "complaint.garbage":      0.9,
    "complaint.streetlight":  0.6,
    "complaint.road_damage":  0.45,
    "complaint.smoke":        0.2,
    "traffic.signal_down":    0.08,   # resident-reported dark junctions
    "power.outage":           0.22,
    "transit.delay":          2.4,
    "weather.rain":           0.06,   # episodes, not observations
}

# --- transit service day (IST hours) -------------------------------------------
SERVICE_START_HOUR_IST = 5
SERVICE_END_HOUR_IST = 23

# --- how long things last ------------------------------------------------------
RAIN_EPISODE_MIN = (25, 90)
OUTAGE_MIN = (20, 180)
TRANSIT_DELAY_MIN = (12, 45)
PM25_EPISODE_MIN = (40, 180)

# --- air sensor realism ---------------------------------------------------------
SENSOR_DROPOUT_PER_DAY = 0.7        # expected dropout stretches per sensor per day
SENSOR_DROPOUT_MIN = (20, 240)      # how long a dropout lasts
SENSOR_FAULT_RATE = 0.004           # per-reading chance of null / -1 sentinel
FRACTION_SENSORS_UNCALIBRATED = 0.375

OUTPUT_FILES = {
    "weather_imd": "raw_weather_imd.jsonl",
    "civic_complaints": "raw_civic_complaints.csv",
    "power_discom": "raw_power_discom.jsonl",
    "transit_gtfs": "raw_transit_gtfs.jsonl",
    "air_sensors": "raw_air_sensors.jsonl",
    "feeder_registry": "feeder_registry.json",
    "stop_registry": "stop_registry.json",
    "ground_truth": "ground_truth.json",
    "event_index": "event_index.jsonl",
}
