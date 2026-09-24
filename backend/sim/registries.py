"""Feeder and stop registries. CONTRACT.md §D.3 and §D.4.

The power and transit feeds report opaque ids and no coordinates, exactly like the real
things. These files are how Phase 3 turns feeder_id / stop_id back into a place -- which
is what the x0.80 'registry-resolved' confidence multiplier in §A pays for.

Generated before the feeds that reference them, from the same seed, so the ids always
agree.
"""

from contract_constants import CITY_BBOX
from ingest.zones import cell_of, load_landmarks, nearest_landmark
from sim import config
from sim.profiles import rng

# Route corridors, each a pair of landmark names. Route 22A is named exactly as
# CONTRACT.md §D.4's example shows it.
ROUTES = [
    ("22A", "Sindhi Camp", "Mansarovar"),
    ("8",   "Jaipur Junction", "Amer Fort"),
    ("15",  "Vaishali Nagar", "Hawa Mahal"),
    ("30B", "Malviya Nagar", "Sindhi Camp"),
    ("4",   "Mansarovar", "Albert Hall Museum"),
    ("12A", "Jaipur Junction", "Malviya Nagar"),
    ("19",  "Vaishali Nagar", "Albert Hall Museum"),
    ("26",  "Hawa Mahal", "Jal Mahal"),
    ("33",  "Sindhi Camp", "Amer Fort"),
    ("7C",  "Mansarovar", "Vaishali Nagar"),
]


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


def build_stop_registry(seed: int = config.SEED) -> dict:
    """~180 stops laid along landmark-to-landmark corridors, hubs shared between routes."""
    r = rng("stops", seed)
    by_name = _by_name()
    reg = {}
    hub_stop = {}      # landmark name -> stop_id, so routes share their terminals
    n = 0

    def new_stop(lat, lon, route_id, seq):
        nonlocal n
        n += 1
        lat, lon = _clamp_into_bbox(lat, lon)
        sid = f"JAI-STP-{n:04d}"
        reg[sid] = {
            "lat": lat,
            "lon": lon,
            "h3_cell": cell_of(lat, lon),
            "name": f"{nearest_landmark(lat, lon)['name']} stop {seq}",
            "routes": [route_id],
        }
        return sid

    for route_id, a_name, b_name in ROUTES:
        a, b = by_name[a_name], by_name[b_name]
        for seq in range(config.STOPS_PER_ROUTE):
            f = seq / (config.STOPS_PER_ROUTE - 1)
            lat = a["lat"] + (b["lat"] - a["lat"]) * f + r.gauss(0, 0.0016)
            lon = a["lon"] + (b["lon"] - a["lon"]) * f + r.gauss(0, 0.0016)

            terminal = a_name if seq == 0 else (b_name if seq == config.STOPS_PER_ROUTE - 1 else None)
            if terminal is not None and terminal in hub_stop:
                sid = hub_stop[terminal]
                reg[sid]["routes"].append(route_id)
                continue

            sid = new_stop(lat, lon, route_id, seq)
            if terminal is not None:
                hub_stop[terminal] = sid

    return reg


def route_stop_sequence(stop_registry: dict, route_id: str) -> list:
    """Stop ids for one route, in registry order (which is corridor order)."""
    return [sid for sid, s in stop_registry.items() if route_id in s["routes"]]


def feeders_in_cells(feeder_registry: dict, cells) -> list:
    cells = set(cells)
    return [fid for fid, f in feeder_registry.items() if f["h3_cell"] in cells]


def stops_in_cells(stop_registry: dict, cells) -> list:
    cells = set(cells)
    return [sid for sid, s in stop_registry.items() if s["h3_cell"] in cells]
