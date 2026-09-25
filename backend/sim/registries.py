"""Feeder and drain-sensor registries. CONTRACT.md §D.3 and §D.4.

The power and drain feeds report opaque ids and no coordinates, exactly like the real
things. These files are how Phase 3 turns feeder_id / rtu id back into a place -- which
is what the x0.80 'registry-resolved' confidence multiplier in §A pays for.

Generated before the feeds that reference them, from the same seed, so the ids always
agree.
"""

from contract_constants import CITY_BBOX
from ingest.zones import cell_of, load_landmarks, nearest_landmark
from sim import config
from sim.profiles import rng

def _clamp_into_bbox(lat: float, lon: float) -> tuple:
    """Keep a jittered point inside the city bbox. Nothing may escape (check b)."""
    sw_lat, sw_lon, ne_lat, ne_lon = CITY_BBOX
    pad = 0.0005
    lat = min(max(lat, sw_lat + pad), ne_lat - pad)
    lon = min(max(lon, sw_lon + pad), ne_lon - pad)
    return round(lat, 5), round(lon, 5)


def _by_name() -> dict:
    return {lm["name"]: lm for lm in load_landmarks()}


def build_feeder_registry(seed: int = config.SEED) -> dict:
    """~120 feeders: a cluster around each landmark, then uniform fill."""
    r = rng("feeders", seed)
    landmarks = load_landmarks()
    reg = {}
    n = 0

    def add(lat, lon):
        nonlocal n
        n += 1
        lat, lon = _clamp_into_bbox(lat, lon)
        fid = f"JVVNL-F-{100 + n}"
        carries = r.random() < config.FRACTION_FEEDERS_WITH_SIGNALS
        near = nearest_landmark(lat, lon)
        reg[fid] = {
            "lat": lat,
            "lon": lon,
            "h3_cell": cell_of(lat, lon),
            "name": f"{near['name']} 33/11kV",
            "carries_signals": carries,
            # How many junctions go dark with this feeder. CONTRACT.md §D.3.
            "signal_junctions": r.randint(1, 5) if carries else 0,
            "connections": r.randint(350, 7200),
        }

    for lm in landmarks:
        for _ in range(config.FEEDERS_PER_LANDMARK):
            add(lm["lat"] + r.gauss(0, 0.009), lm["lon"] + r.gauss(0, 0.009))

    sw_lat, sw_lon, ne_lat, ne_lon = CITY_BBOX
    while n < config.N_FEEDERS:
        add(r.uniform(sw_lat, ne_lat), r.uniform(sw_lon, ne_lon))

    return reg


def build_drain_registry(seed: int = config.SEED) -> dict:
    """Storm-drain (nala) level gauges, a pair near each landmark.

    Like the power feed, the drain SCADA feed reports only an RTU id: the channel's
    location and design depth live here. Channel depth varies by drain -- a trunk nala
    is deeper than a colony drain -- so a raw level in cm means nothing without it.
    """
    r = rng("drains", seed)
    reg = {}
    n = 0
    for lm in load_landmarks():
        for k in range(config.DRAIN_SENSORS_PER_LANDMARK):
            n += 1
            lat, lon = _clamp_into_bbox(lm["lat"] + r.gauss(0, 0.0022),
                                        lm["lon"] + r.gauss(0, 0.0022))
            rid = f"JDA-NALA-{n:02d}"
            reg[rid] = {
                "lat": lat,
                "lon": lon,
                "h3_cell": cell_of(lat, lon),
                "name": f"{lm['name']} {'trunk' if k == 0 else 'colony'} drain",
                "capacity_cm": r.choice([120, 150, 180, 210]) if k == 0 else r.choice([60, 75, 90]),
            }
    return reg


def feeders_in_cells(feeder_registry: dict, cells) -> list:
    cells = set(cells)
    return [fid for fid, f in feeder_registry.items() if f["h3_cell"] in cells]
