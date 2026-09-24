"""What happens, and when. The feed writers decide what it looks like on the wire.

Three layers, in order:
  1. baseline  -- 14 days of stationary Poisson noise with a diurnal shape
  2. planted   -- the three cascades the engine is supposed to find
  3. decoys    -- coincidences the engine is supposed to reject

CONTRACT.md §G. Nothing here knows about JSON, CSV or GTFS.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from contract_constants import CITY_BBOX, scale_severity
from ingest.zones import bbox_cells, cell_of, centroid, nearest_landmark, neighbors
from sim import config
from sim.profiles import poisson, rate_at, rng

_spec_counter = [0]


@dataclass
class EventSpec:
    """One thing that happens. Writers fill in raw_ref/event_id once they emit it."""
    category: str
    source: str
    start: object                      # datetime, UTC
    lat: float
    lon: float
    h3_cell: str
    measure: float                     # in the category's own unit (CONTRACT.md §A)
    end: object = None
    entity: str = None                 # station / sensor / feeder_id / stop_id
    extra: dict = field(default_factory=dict)
    truth_id: str = None
    decoy_id: str = None
    spec_uid: int = 0
    raw_ref: str = None                # filled by the writer
    event_id: str = None               # filled by the writer

    def __post_init__(self):
        _spec_counter[0] += 1
        self.spec_uid = _spec_counter[0]


def reset_spec_counter():
    _spec_counter[0] = 0


# ---------------------------------------------------------------- the world ---

def build_world(feeder_registry: dict, stop_registry: dict, seed: int = config.SEED) -> dict:
    """Static per-cell character plus the sensor and station networks."""
    r = rng("world", seed)
    cells = list(bbox_cells())

    # Some areas are simply busier than others. Gives Phase 4 a baseline worth learning.
    weights = {c: max(0.05, r.lognormvariate(0.0, 0.8)) for c in cells}

    # Sorted tuple, not a set: iterating a set of strings depends on PYTHONHASHSEED,
    # which is randomised per process and would make the whole lane irreproducible.
    low_lying = tuple(sorted(r.sample(cells, int(len(cells) * config.FRACTION_LOW_LYING))))

    from ingest.zones import load_landmarks
    landmarks = load_landmarks()

    # Weather stations: one per landmark for the first N. Station 03 sits at the city
    # centre, matching CONTRACT.md §D.1's example.
    stations = []
    centre_lat, centre_lon = 26.9124, 75.7873
    for i in range(config.N_WEATHER_STATIONS):
        sid = f"IMD-JAI-{i + 1:02d}"
        if i == 2:
            lat, lon = centre_lat, centre_lon
        else:
            lm = landmarks[i % len(landmarks)]
            lat = round(lm["lat"] + r.gauss(0, 0.004), 5)
            lon = round(lm["lon"] + r.gauss(0, 0.004), 5)
        stations.append({"station_id": sid, "lat": lat, "lon": lon, "h3_cell": cell_of(lat, lon)})

    # Air sensors: AQ-JPR-07 sits at Hawa Mahal, matching CONTRACT.md §D.5's example.
    sensor_sites = ["Sindhi Camp", "Mansarovar", "Malviya Nagar", "Jaipur Junction",
                    "Albert Hall Museum", "Jal Mahal", "Hawa Mahal", "Vaishali Nagar"]
    by_name = {lm["name"]: lm for lm in landmarks}
    sensors = []
    for i, site in enumerate(sensor_sites[: config.N_AIR_SENSORS]):
        lm = by_name[site]
        lat = round(lm["lat"] + r.gauss(0, 0.003), 5)
        lon = round(lm["lon"] + r.gauss(0, 0.003), 5)
        sensors.append({
            "sensor": f"AQ-JPR-{i + 1:02d}",
            "lat": lat, "lon": lon, "h3_cell": cell_of(lat, lon),
            "calibrated": r.random() > config.FRACTION_SENSORS_UNCALIBRATED,
        })

    return {
        "cells": cells,
        "weights": weights,
        "low_lying": low_lying,
        "stations": stations,
        "sensors": sensors,
        "feeders": feeder_registry,
        "stops": stop_registry,
    }


def _weighted_cell(r, world, pool=None):
    cells = pool if pool else world["cells"]
    ws = [world["weights"][c] for c in cells]
    return r.choices(cells, weights=ws, k=1)[0]


def _point_in_cell(r, cell):
    """A jittered point inside a cell, guaranteed inside the bbox."""
    lat, lon = centroid(cell)
    sw_lat, sw_lon, ne_lat, ne_lon = CITY_BBOX
    lat = min(max(lat + r.gauss(0, 0.0012), sw_lat + 0.0005), ne_lat - 0.0005)
    lon = min(max(lon + r.gauss(0, 0.0012), sw_lon + 0.0005), ne_lon - 0.0005)
    return round(lat, 5), round(lon, 5)


def _nearest_station(world, lat, lon):
    return min(world["stations"],
               key=lambda s: (s["lat"] - lat) ** 2 + (s["lon"] - lon) ** 2)


def _nearest_sensor(world, lat, lon):
    return min(world["sensors"],
               key=lambda s: (s["lat"] - lat) ** 2 + (s["lon"] - lon) ** 2)


def _trip_id(route_id, dt):
    """JCTSL-22A-1830, the shape CONTRACT.md §D.4 shows. HHMM is the IST departure."""
    ist = dt + timedelta(hours=5, minutes=30)
    return f"JCTSL-{route_id}-{ist.strftime('%H%M')}"


# ------------------------------------------------------------- baseline noise --

def baseline_specs(world, seed: int = config.SEED) -> list:
    """14 days of stationary noise. Deliberately structureless: no cascades, nothing
    correlated across categories, so Phase 4 learns a clean 'normal'."""
    specs = []
    hours = int((config.SIM_END - config.HISTORY_START).total_seconds() // 3600)

    complaint_types = ["complaint.waterlogging", "complaint.garbage",
                       "complaint.streetlight", "complaint.road_damage",
                       "complaint.smoke"]

    for cat in complaint_types + ["traffic.signal_down"]:
        r = rng(f"baseline:{cat}", seed)
        base = config.BASELINE_RATES[cat]
        for h in range(hours):
            t0 = config.HISTORY_START + timedelta(hours=h)
            for _ in range(poisson(r, rate_at(cat, base, t0))):
                at = t0 + timedelta(seconds=r.randrange(3600))
                pool = world["low_lying"] if cat == "complaint.waterlogging" else None
                cell = _weighted_cell(r, world, pool)
                lat, lon = _point_in_cell(r, cell)
                specs.append(EventSpec(
                    category=cat, source="civic_complaints", start=at,
                    lat=lat, lon=lon, h3_cell=cell, measure=1.0,
                    extra={"landmark": nearest_landmark(lat, lon)["name"]},
                ))

    # Power outages -> the feeder registry decides whether signals go dark too.
    r = rng("baseline:power", seed)
    feeder_ids = list(world["feeders"])
    for h in range(hours):
        t0 = config.HISTORY_START + timedelta(hours=h)
        for _ in range(poisson(r, rate_at("power.outage", config.BASELINE_RATES["power.outage"], t0))):
            fid = r.choice(feeder_ids)
            f = world["feeders"][fid]
            at = t0 + timedelta(seconds=r.randrange(3600))
            dur = r.randint(*config.OUTAGE_MIN)
            specs.append(EventSpec(
                category="power.outage", source="power_discom", start=at,
                end=at + timedelta(minutes=dur),
                lat=f["lat"], lon=f["lon"], h3_cell=f["h3_cell"],
                measure=float(r.randint(220, min(7000, f["connections"]))),
                entity=fid,
                extra={"est_restore_min": dur + r.randint(-10, 30),
                       "cause": r.choice(["UNKNOWN", "CABLE FAULT", "OVERLOAD", "TREE CONTACT"]),
                       "scheduled": r.random() < 0.12},
            ))

    # Transit delays.
    r = rng("baseline:transit", seed)
    from sim.registries import ROUTES
    for h in range(hours):
        t0 = config.HISTORY_START + timedelta(hours=h)
        ist_h = (t0 + timedelta(hours=5, minutes=30)).hour
        if not (config.SERVICE_START_HOUR_IST <= ist_h < config.SERVICE_END_HOUR_IST):
            continue
        for _ in range(poisson(r, rate_at("transit.delay", config.BASELINE_RATES["transit.delay"], t0))):
            route_id = r.choice([rt[0] for rt in ROUTES])
            route_stops = [sid for sid, s in world["stops"].items() if route_id in s["routes"]]
            if not route_stops:
                continue
            sid = r.choice(route_stops)
            s = world["stops"][sid]
            at = t0 + timedelta(seconds=r.randrange(3600))
            specs.append(EventSpec(
                category="transit.delay", source="transit_gtfs", start=at,
                end=at + timedelta(minutes=r.randint(*config.TRANSIT_DELAY_MIN)),
                lat=s["lat"], lon=s["lon"], h3_cell=s["h3_cell"],
                measure=float(r.randint(320, 1500)), entity=sid,
                extra={"route_id": route_id, "trip_id": _trip_id(route_id, at)},
            ))

    # Rain episodes.
    r = rng("baseline:rain", seed)
    for h in range(hours):
        t0 = config.HISTORY_START + timedelta(hours=h)
        for _ in range(poisson(r, rate_at("weather.rain", config.BASELINE_RATES["weather.rain"], t0))):
            st = r.choice(world["stations"])
            at = t0 + timedelta(seconds=r.randrange(3600))
            specs.append(EventSpec(
                category="weather.rain", source="weather_imd", start=at,
                end=at + timedelta(minutes=r.randint(*config.RAIN_EPISODE_MIN)),
                lat=st["lat"], lon=st["lon"], h3_cell=st["h3_cell"],
                measure=round(r.uniform(6.0, 24.0), 1), entity=st["station_id"],
            ))

    # PM2.5 episodes on top of the diurnal air baseline.
    r = rng("baseline:air", seed)
    for h in range(hours):
        t0 = config.HISTORY_START + timedelta(hours=h)
        for _ in range(poisson(r, rate_at("air.pm25", 0.09, t0))):
            sn = r.choice(world["sensors"])
            at = t0 + timedelta(seconds=r.randrange(3600))
            specs.append(EventSpec(
                category="air.pm25", source="air_sensors", start=at,
                end=at + timedelta(minutes=r.randint(*config.PM25_EPISODE_MIN)),
                lat=sn["lat"], lon=sn["lon"], h3_cell=sn["h3_cell"],
                measure=round(r.uniform(70, 150), 1), entity=sn["sensor"],
            ))

    return specs


# ------------------------------------------------------------ planted truths --

def _signal_feeder_in(world, cells, r):
    """A feeder inside these cells that carries signals, so one TRIP yields two events."""
    cands = [fid for fid, f in world["feeders"].items()
             if f["h3_cell"] in cells and f["carries_signals"]]
    if not cands:
        cands = [fid for fid, f in world["feeders"].items() if f["h3_cell"] in cells]
    return r.choice(sorted(cands)) if cands else None


def planted_specs(world, seed: int = config.SEED):
    """The three cascades. Times are relative to config.SIM_START."""
    r = rng("planted", seed)
    S = config.SIM_START
    specs, truths = [], []

    from ingest.zones import load_landmarks
    by_name = {lm["name"]: lm for lm in load_landmarks()}

    # ---- GT-001: cloudburst at Sindhi Camp -----------------------------------
    sc = by_name["Sindhi Camp"]
    jj = by_name["Jaipur Junction"]
    sc_cell, jj_cell = sc["h3_cell"], jj["h3_cell"]
    zone = [sc_cell, jj_cell]
    g1 = []

    st = _nearest_station(world, jj["lat"], jj["lon"])
    g1.append(EventSpec(
        category="weather.rain", source="weather_imd", start=S + timedelta(minutes=55),
        end=S + timedelta(minutes=110),
        lat=st["lat"], lon=st["lon"], h3_cell=st["h3_cell"],
        measure=28.0, entity=st["station_id"], truth_id="GT-001"))

    wl_cells = [sc_cell, sc_cell, jj_cell, sc_cell, jj_cell]
    for i, cell in enumerate(wl_cells):
        lat, lon = _point_in_cell(r, cell)
        g1.append(EventSpec(
            category="complaint.waterlogging", source="civic_complaints",
            start=S + timedelta(minutes=72 + i * 7),
            lat=lat, lon=lon, h3_cell=cell, measure=float(len(wl_cells)),
            extra={"landmark": nearest_landmark(lat, lon)["name"]}, truth_id="GT-001"))

    fid = _signal_feeder_in(world, {sc_cell}, r)
    f = world["feeders"][fid]
    trip_at = S + timedelta(minutes=90)
    g1.append(EventSpec(
        category="power.outage", source="power_discom", start=trip_at,
        end=trip_at + timedelta(minutes=95),
        lat=f["lat"], lon=f["lon"], h3_cell=f["h3_cell"],
        measure=4120.0, entity=fid,
        extra={"est_restore_min": 75, "cause": "UNKNOWN", "scheduled": False,
               "signal_junctions": 3, "force_signals": True}, truth_id="GT-001"))
    # Same raw record, second category, second source-independent id.
    g1.append(EventSpec(
        category="traffic.signal_down", source="power_discom", start=trip_at,
        end=trip_at + timedelta(minutes=95),
        lat=f["lat"], lon=f["lon"], h3_cell=f["h3_cell"],
        measure=3.0, entity=fid, extra={"from_power_record": True}, truth_id="GT-001"))
    # The corroborating resident report of the same dark junction.
    lat, lon = _point_in_cell(r, sc_cell)
    g1.append(EventSpec(
        category="traffic.signal_down", source="civic_complaints",
        start=trip_at + timedelta(minutes=8),
        lat=lat, lon=lon, h3_cell=sc_cell, measure=1.0,
        extra={"landmark": "Sindhi Camp"}, truth_id="GT-001"))

    for i, (delay, off) in enumerate([(840, 105), (1020, 112), (660, 125)]):
        cands = [sid for sid, s in world["stops"].items()
                 if "22A" in s["routes"] and s["h3_cell"] in zone]
        sid = sorted(cands)[i % len(cands)] if cands else sorted(
            s for s in world["stops"] if "22A" in world["stops"][s]["routes"])[i]
        s = world["stops"][sid]
        at = S + timedelta(minutes=off)
        g1.append(EventSpec(
            category="transit.delay", source="transit_gtfs", start=at,
            end=at + timedelta(minutes=28),
            lat=s["lat"], lon=s["lon"], h3_cell=s["h3_cell"],
            measure=float(delay), entity=sid,
            extra={"route_id": "22A", "trip_id": _trip_id("22A", at)}, truth_id="GT-001"))

    specs += g1
    truths.append({
        "truth_id": "GT-001",
        "label": "Cloudburst at Sindhi Camp floods the bus stand and delays route 22A",
        "root_cause_category": "weather.rain",
        "expected_chain": ["weather.rain", "complaint.waterlogging", "power.outage",
                           "traffic.signal_down", "transit.delay"],
        "expected_zone_cells": sorted({s.h3_cell for s in g1}),
        "onset": S + timedelta(minutes=55),
        "detect_by": S + timedelta(minutes=105),
        "specs": g1,
    })

    # ---- GT-002: transformer failure, Malviya Nagar. No weather. -------------
    mn = by_name["Malviya Nagar"]
    mn_cell = mn["h3_cell"]
    g2 = []
    fid2 = _signal_feeder_in(world, {mn_cell}, r)
    f2 = world["feeders"][fid2]
    trip2 = S + timedelta(minutes=80)
    g2.append(EventSpec(
        category="power.outage", source="power_discom", start=trip2,
        end=trip2 + timedelta(minutes=140),
        lat=f2["lat"], lon=f2["lon"], h3_cell=f2["h3_cell"],
        measure=5600.0, entity=fid2,
        extra={"est_restore_min": 120, "cause": "TRANSFORMER FAILURE", "scheduled": False,
               "signal_junctions": 4, "force_signals": True}, truth_id="GT-002"))
    g2.append(EventSpec(
        category="traffic.signal_down", source="power_discom", start=trip2,
        end=trip2 + timedelta(minutes=140),
        lat=f2["lat"], lon=f2["lon"], h3_cell=f2["h3_cell"],
        measure=4.0, entity=fid2, extra={"from_power_record": True}, truth_id="GT-002"))
    lat, lon = _point_in_cell(r, mn_cell)
    g2.append(EventSpec(
        category="traffic.signal_down", source="civic_complaints",
        start=trip2 + timedelta(minutes=14),
        lat=lat, lon=lon, h3_cell=mn_cell, measure=1.0,
        extra={"landmark": "Malviya Nagar"}, truth_id="GT-002"))

    mn_zone = neighbors(mn_cell, 1)
    for i, (delay, off) in enumerate([(900, 110), (1140, 118), (720, 130)]):
        cands = sorted(sid for sid, s in world["stops"].items() if s["h3_cell"] in mn_zone)
        if not cands:
            cands = sorted(sid for sid, s in world["stops"].items()
                           if "30B" in s["routes"] or "12A" in s["routes"])
        sid = cands[i % len(cands)]
        s = world["stops"][sid]
        at = S + timedelta(minutes=off)
        route_id = s["routes"][0]
        g2.append(EventSpec(
            category="transit.delay", source="transit_gtfs", start=at,
            end=at + timedelta(minutes=25),
            lat=s["lat"], lon=s["lon"], h3_cell=s["h3_cell"],
            measure=float(delay), entity=sid,
            extra={"route_id": route_id, "trip_id": _trip_id(route_id, at)},
            truth_id="GT-002"))

    specs += g2
    truths.append({
        "truth_id": "GT-002",
        "label": "Transformer failure in Malviya Nagar darkens signals and delays buses",
        "root_cause_category": "power.outage",
        "expected_chain": ["power.outage", "traffic.signal_down", "transit.delay"],
        "expected_zone_cells": sorted({s.h3_cell for s in g2}),
        "onset": trip2,
        "detect_by": trip2 + timedelta(minutes=40),
        "specs": g2,
    })

    # ---- GT-003: garbage fire, Vaishali Nagar. Tight, two feeds, small. ------
    vn = by_name["Vaishali Nagar"]
    vn_cell = vn["h3_cell"]
    g3 = []
    for i in range(3):
        lat, lon = _point_in_cell(r, vn_cell)
        g3.append(EventSpec(
            category="complaint.smoke", source="civic_complaints",
            start=S + timedelta(minutes=65 + i * 6),
            lat=lat, lon=lon, h3_cell=vn_cell, measure=3.0,
            extra={"landmark": "Vaishali Nagar"}, truth_id="GT-003"))
    sn = _nearest_sensor(world, vn["lat"], vn["lon"])
    g3.append(EventSpec(
        category="air.pm25", source="air_sensors", start=S + timedelta(minutes=70),
        end=S + timedelta(minutes=160),
        lat=sn["lat"], lon=sn["lon"], h3_cell=sn["h3_cell"],
        measure=120.0, entity=sn["sensor"], truth_id="GT-003"))

    specs += g3
    truths.append({
        "truth_id": "GT-003",
        "label": "Garbage fire in Vaishali Nagar puts smoke and PM2.5 over one area",
        "root_cause_category": "complaint.smoke",
        "expected_chain": ["complaint.smoke", "air.pm25"],
        "expected_zone_cells": sorted({s.h3_cell for s in g3}),
        "onset": S + timedelta(minutes=65),
        "detect_by": S + timedelta(minutes=115),
        "specs": g3,
    })

    return specs, truths


# --------------------------------------------------------------------- decoys --

def decoy_specs(world, seed: int = config.SEED):
    r = rng("decoys", seed)
    S = config.SIM_START
    specs, decoys = [], []

    from ingest.zones import load_landmarks
    by_name = {lm["name"]: lm for lm in load_landmarks()}

    # DC-001: routine garbage complaints in Mansarovar, during the storm, 9 km away.
    ms_cell = by_name["Mansarovar"]["h3_cell"]
    d1 = []
    for i in range(3):
        lat, lon = _point_in_cell(r, ms_cell)
        d1.append(EventSpec(
            category="complaint.garbage", source="civic_complaints",
            start=S + timedelta(minutes=70 + i * 18),
            lat=lat, lon=lon, h3_cell=ms_cell, measure=3.0,
            extra={"landmark": "Mansarovar"}, decoy_id="DC-001"))
    specs += d1
    decoys.append({
        "decoy_id": "DC-001",
        "label": "Routine garbage complaints in Mansarovar happen to land during the storm",
        "why_unrelated": "Same hour, 9 km away, and garbage complaints run at this rate every evening",
        "must_not_alert_above": "yellow",
        "specs": d1,
    })

    # DC-002: festival crowd delays near Hawa Mahal -- grid distance 4 from the storm.
    hm_cell = by_name["Hawa Mahal"]["h3_cell"]
    hm_zone = neighbors(hm_cell, 1)
    d2 = []
    cands = sorted(sid for sid, s in world["stops"].items() if s["h3_cell"] in hm_zone)
    for i, (delay, off) in enumerate([(760, 75), (880, 84), (640, 96), (920, 108)]):
        if not cands:
            break
        sid = cands[i % len(cands)]
        s = world["stops"][sid]
        at = S + timedelta(minutes=off)
        route_id = s["routes"][0]
        d2.append(EventSpec(
            category="transit.delay", source="transit_gtfs", start=at,
            end=at + timedelta(minutes=30),
            lat=s["lat"], lon=s["lon"], h3_cell=s["h3_cell"],
            measure=float(delay), entity=sid,
            extra={"route_id": route_id, "trip_id": _trip_id(route_id, at)},
            decoy_id="DC-002"))
    specs += d2
    decoys.append({
        "decoy_id": "DC-002",
        "label": "Festival crowd slows buses near Hawa Mahal at the same time as the storm",
        "why_unrelated": "Four areas away from the flooding, no rain reported here, and the crowd is scheduled",
        "must_not_alert_above": "yellow",
        "specs": d2,
    })

    # DC-003: three unrelated outages, far apart, inside the same window.
    d3 = []
    far = [by_name["Amer Fort"]["h3_cell"], by_name["Albert Hall Museum"]["h3_cell"],
           by_name["Jal Mahal"]["h3_cell"]]
    for i, cell in enumerate(far):
        cands = sorted(fid for fid, f in world["feeders"].items() if f["h3_cell"] == cell)
        if not cands:
            cands = sorted(world["feeders"])
        fid = cands[0]
        f = world["feeders"][fid]
        at = S + timedelta(minutes=60 + i * 40)
        d3.append(EventSpec(
            category="power.outage", source="power_discom", start=at,
            end=at + timedelta(minutes=r.randint(25, 70)),
            lat=f["lat"], lon=f["lon"], h3_cell=f["h3_cell"],
            measure=float(r.randint(240, 900)), entity=fid,
            extra={"est_restore_min": 45, "cause": "CABLE FAULT", "scheduled": False,
                   "force_no_signals": True},
            decoy_id="DC-003"))
    specs += d3
    decoys.append({
        "decoy_id": "DC-003",
        "label": "Three small outages in different corners of the city inside one hour",
        "why_unrelated": "Temporally close but spatially incoherent -- no two share an area or a feeder",
        "must_not_alert_above": "yellow",
        "specs": d3,
    })

    return specs, decoys


# ------------------------------------------------- expected level (CONTRACT §F) --

def expected_pulse(specs, confidences) -> int:
    """CONTRACT.md §F, computed over the planted specs so the answer key is
    internally consistent rather than asserted by hand."""
    if not specs:
        return 0
    sev = max(scale_severity(s.category, s.measure) for s in specs)
    conf = sum(confidences) / len(confidences)
    feeds = min(len({s.source for s in specs}), 3) / 3
    return round((sev * 0.6 + conf * 0.2 + feeds * 0.2) * 100)
