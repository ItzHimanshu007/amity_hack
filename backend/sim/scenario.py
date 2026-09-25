"""What happens, and when. The feed writers decide what it looks like on the wire.

Three layers, in order:
  1. baseline  -- 14 days of stationary Poisson noise with a diurnal shape
  2. planted   -- the eight cascades the engine is supposed to find
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

def build_world(feeder_registry: dict, drain_registry: dict, seed: int = config.SEED) -> dict:
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

    # Weather stations: one per landmark (Jal Mahal's slot is the centre gauge). Station 03 sits at the city
    # centre, matching CONTRACT.md §D.1's example.
    stations = []
    centre_lat, centre_lon = 26.9124, 75.7873
    for i in range(config.N_WEATHER_STATIONS):
        sid = f"IMD-JAI-{i + 1:02d}"
        if i == 2:
            lat, lon = centre_lat, centre_lon
        else:
            lm = landmarks[i % len(landmarks)]
            lat = round(lm["lat"] + r.gauss(0, 0.0015), 5)
            lon = round(lm["lon"] + r.gauss(0, 0.0015), 5)
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
        "drains": drain_registry,
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


def _drains_near(world, cell, ring=1):
    """RTU ids within `ring` cells of this cell, nearest first."""
    from ingest.zones import grid_distance
    near = [(grid_distance(cell, d["h3_cell"]), rid) for rid, d in world["drains"].items()]
    return [rid for dist, rid in sorted(near) if dist <= ring]


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

    # Drain overflows with no rain behind them: silt, plastic and debris choke a
    # channel at any hour. Stationary like everything else in the history.
    r = rng("baseline:drain", seed)
    drain_ids = sorted(world["drains"])
    for h in range(hours):
        t0 = config.HISTORY_START + timedelta(hours=h)
        for _ in range(poisson(r, rate_at("drain.overflow", config.BASELINE_RATES["drain.overflow"], t0))):
            rid = r.choice(drain_ids)
            d = world["drains"][rid]
            at = t0 + timedelta(seconds=r.randrange(3600))
            specs.append(EventSpec(
                category="drain.overflow", source="drain_scada", start=at,
                end=at + timedelta(minutes=r.randint(*config.DRAIN_OVERFLOW_MIN)),
                lat=d["lat"], lon=d["lon"], h3_cell=d["h3_cell"],
                measure=round(r.uniform(88.0, 108.0), 1), entity=rid,
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
    """A monsoon storm cell crossing the city, and what it sets off in eight areas.

    Times are minutes after config.SIM_START (17:30 IST). The storm arrives from the
    west: heavy cores (above the 5 mm / 15 min event floor) over the areas where a
    cascade is planted, and light rain everywhere else -- real rain on the flood model's
    terrain, but below the floor, so it produces no events and cannot pose as evidence.
    Power-rooted situations start BEFORE the rain reaches them, so no rain -> power link
    is possible there by construction.
    """
    r = rng("planted", seed)
    S = config.SIM_START
    specs, truths = [], []

    from ingest.zones import load_landmarks
    by_name = {lm["name"]: lm for lm in load_landmarks()}

    def at(m):
        return S + timedelta(minutes=m)

    def rain(lm_name, start, dur, peak, tid=None, station=None):
        lm = by_name[lm_name]
        st = station or _nearest_station(world, lm["lat"], lm["lon"])
        return EventSpec(
            category="weather.rain", source="weather_imd", start=at(start),
            end=at(start + dur), lat=st["lat"], lon=st["lon"], h3_cell=st["h3_cell"],
            measure=peak, entity=st["station_id"], truth_id=tid)

    def drains(lm_name, start, pct, tid, n=2, dur=70):
        cell = by_name[lm_name]["h3_cell"]
        out = []
        for k, rid in enumerate(_drains_near(world, cell, 1)[:n]):
            d = world["drains"][rid]
            out.append(EventSpec(
                category="drain.overflow", source="drain_scada", start=at(start + k * 10),
                end=at(start + k * 10 + dur), lat=d["lat"], lon=d["lon"],
                h3_cell=d["h3_cell"], measure=pct - k * 6, entity=rid, truth_id=tid))
        return out

    def complaints(cat, lm_name, starts, tid=None, decoy=None, cells=None):
        out = []
        for k, m in enumerate(starts):
            cell = (cells or [by_name[lm_name]["h3_cell"]])[k % len(cells or [0])]
            lat, lon = _point_in_cell(r, cell)
            out.append(EventSpec(
                category=cat, source="civic_complaints", start=at(m), lat=lat, lon=lon,
                h3_cell=cell, measure=float(len(starts)),
                extra={"landmark": nearest_landmark(lat, lon)["name"]
                       if cells else lm_name},
                truth_id=tid, decoy_id=decoy))
        return out

    def power_cut(lm_name, start, dur, connections, cause, junctions, tid):
        cell = by_name[lm_name]["h3_cell"]
        fid = (_signal_feeder_in(world, {cell}, r)
               or _signal_feeder_in(world, set(neighbors(cell, 1)), r))
        f = world["feeders"][fid]
        extra = {"est_restore_min": dur - 20, "cause": cause, "scheduled": False,
                 "signal_junctions": junctions, "force_signals": True}
        return [
            EventSpec(category="power.outage", source="power_discom", start=at(start),
                      end=at(start + dur), lat=f["lat"], lon=f["lon"], h3_cell=f["h3_cell"],
                      measure=connections, entity=fid, extra=extra, truth_id=tid),
            # Same raw record, second category (CONTRACT.md §D.3).
            EventSpec(category="traffic.signal_down", source="power_discom", start=at(start),
                      end=at(start + dur), lat=f["lat"], lon=f["lon"], h3_cell=f["h3_cell"],
                      measure=float(junctions), entity=fid,
                      extra={"from_power_record": True}, truth_id=tid),
        ]

    def truth(tid, label, root, chain, group, onset, detect_after):
        specs.extend(group)
        truths.append({
            "truth_id": tid, "label": label, "root_cause_category": root,
            "expected_chain": chain,
            "expected_zone_cells": sorted({g.h3_cell for g in group}),
            "onset": at(onset), "detect_by": at(onset + detect_after), "specs": group,
        })

    # ---- light rain over the rest of the city: real water, no events -------------
    heavy = {"Jaipur Junction", "Sindhi Camp", "Mansarovar", "Tonk Road", "Sanganer",
             "Jagatpura"}
    light_onset = {"Vaishali Nagar": 30, "Vidyadhar Nagar": 60, "Albert Hall Museum": 62,
                   "Hawa Mahal": 66, "Malviya Nagar": 78, "Amer Fort": 84}
    used = set()
    for name, m in light_onset.items():
        lm = by_name[name]
        st = _nearest_station(world, lm["lat"], lm["lon"])
        used.add(st["station_id"])
        specs.append(rain(name, m, 55, round(r.uniform(2.4, 3.3), 1), station=st))
    for st in world["stations"]:
        near = min(by_name.values(),
                   key=lambda lm: (lm["lat"] - st["lat"]) ** 2 + (lm["lon"] - st["lon"]) ** 2)
        if st["station_id"] not in used and near["name"] not in heavy:
            specs.append(EventSpec(
                category="weather.rain", source="weather_imd", start=at(70), end=at(125),
                lat=st["lat"], lon=st["lon"], h3_cell=st["h3_cell"],
                measure=round(r.uniform(2.4, 3.3), 1), entity=st["station_id"]))

    # ---- GT-001: cloudburst over Sindhi Camp / Jaipur Junction ------------------
    sc_cell = by_name["Sindhi Camp"]["h3_cell"]
    jj_cell = by_name["Jaipur Junction"]["h3_cell"]
    g = [rain("Jaipur Junction", 55, 55, 28.0, "GT-001"),
         rain("Sindhi Camp", 58, 50, 24.0, "GT-001")]
    g += drains("Sindhi Camp", 64, 124.0, "GT-001")
    g += complaints("complaint.waterlogging", None, [72, 79, 86, 93, 100], "GT-001",
                    cells=[sc_cell, sc_cell, jj_cell, sc_cell, jj_cell])
    g += power_cut("Sindhi Camp", 90, 95, 4120.0, "UNKNOWN", 3, "GT-001")
    g += complaints("traffic.signal_down", "Sindhi Camp", [98], "GT-001")
    truth("GT-001", "Cloudburst over Sindhi Camp overflows drains, floods the bus stand "
          "and trips a feeder that darkens signals", "weather.rain",
          ["weather.rain", "drain.overflow", "complaint.waterlogging", "power.outage",
           "traffic.signal_down"], g, 55, 50)

    # ---- GT-002: transformer failure, Malviya Nagar. No weather. -----------------
    g = power_cut("Malviya Nagar", 50, 150, 5600.0, "TRANSFORMER FAILURE", 4, "GT-002")
    g += complaints("traffic.signal_down", "Malviya Nagar", [64], "GT-002")
    g += complaints("complaint.streetlight", "Malviya Nagar", [58, 66, 74, 81], "GT-002")
    truth("GT-002", "Transformer failure in Malviya Nagar darkens signals and streetlights",
          "power.outage", ["power.outage", "traffic.signal_down", "complaint.streetlight"],
          g, 50, 40)

    # ---- GT-003: garbage fire, Vaishali Nagar. Tight, two feeds, small. ------------
    vn = by_name["Vaishali Nagar"]
    g = complaints("complaint.smoke", "Vaishali Nagar", [65, 71, 77], "GT-003")
    sn = _nearest_sensor(world, vn["lat"], vn["lon"])
    g.append(EventSpec(
        category="air.pm25", source="air_sensors", start=at(70), end=at(160),
        lat=sn["lat"], lon=sn["lon"], h3_cell=sn["h3_cell"],
        measure=120.0, entity=sn["sensor"], truth_id="GT-003"))
    truth("GT-003", "Garbage fire in Vaishali Nagar puts smoke and PM2.5 over one area",
          "complaint.smoke", ["complaint.smoke", "air.pm25"], g, 65, 50)

    # ---- GT-004: Mansarovar -- first core of the storm ----------------------------
    g = [rain("Mansarovar", 40, 60, 22.0, "GT-004")]
    g += drains("Mansarovar", 50, 118.0, "GT-004")
    g += complaints("complaint.waterlogging", "Mansarovar", [58, 64, 71, 79], "GT-004")
    g += complaints("complaint.road_damage", "Mansarovar", [96, 104, 112], "GT-004")
    truth("GT-004", "Storm over Mansarovar overflows colony drains, waterlogs streets and "
          "breaks the road up", "weather.rain",
          ["weather.rain", "drain.overflow", "complaint.waterlogging",
           "complaint.road_damage"], g, 40, 45)

    # ---- GT-005: Tonk Road -- waterlogged underpass trips a feeder ----------------
    g = [rain("Tonk Road", 45, 55, 20.0, "GT-005")]
    g += complaints("complaint.waterlogging", "Tonk Road", [60, 66, 72, 79], "GT-005")
    g += power_cut("Tonk Road", 76, 80, 4400.0, "WATER INGRESS", 2, "GT-005")
    truth("GT-005", "Rain on Tonk Road floods a feeder pillar; power and signals go out",
          "weather.rain", ["weather.rain", "complaint.waterlogging", "power.outage",
                           "traffic.signal_down"], g, 45, 45)

    # ---- GT-006: Vidyadhar Nagar -- evening overload, before any rain ---------------
    g = power_cut("Vidyadhar Nagar", 15, 110, 4700.0, "OVERLOAD", 3, "GT-006")
    g += complaints("complaint.streetlight", "Vidyadhar Nagar", [22, 29, 37], "GT-006")
    truth("GT-006", "Feeder overload in Vidyadhar Nagar darkens signals and streetlights",
          "power.outage", ["power.outage", "traffic.signal_down", "complaint.streetlight"],
          g, 15, 40)

    # ---- GT-007: Sanganer -- the storm moves south-east ------------------------------
    g = [rain("Sanganer", 70, 50, 26.0, "GT-007")]
    g += drains("Sanganer", 78, 116.0, "GT-007")
    g += complaints("complaint.road_damage", "Sanganer", [92, 99, 107], "GT-007")
    truth("GT-007", "Storm over Sanganer overflows the nala and damages the road beside it",
          "weather.rain", ["weather.rain", "drain.overflow", "complaint.road_damage"],
          g, 70, 50)

    # ---- GT-008: Jagatpura -- last core, drains back up into streets ------------------
    g = [rain("Jagatpura", 85, 45, 21.0, "GT-008")]
    g += drains("Jagatpura", 93, 120.0, "GT-008")
    g += complaints("complaint.waterlogging", "Jagatpura", [100, 106, 113], "GT-008")
    truth("GT-008", "Storm over Jagatpura backs the drains up into the streets",
          "weather.rain", ["weather.rain", "drain.overflow", "complaint.waterlogging"],
          g, 85, 45)

    return specs, truths


# --------------------------------------------------------------------- decoys --

def decoy_specs(world, seed: int = config.SEED):
    r = rng("decoys", seed)
    S = config.SIM_START
    specs, decoys = [], []

    from ingest.zones import load_landmarks
    by_name = {lm["name"]: lm for lm in load_landmarks()}

    # DC-001: routine garbage complaints in Jagatpura, during the storm.
    jg_cell = by_name["Jagatpura"]["h3_cell"]
    d1 = []
    for i in range(3):
        lat, lon = _point_in_cell(r, jg_cell)
        d1.append(EventSpec(
            category="complaint.garbage", source="civic_complaints",
            start=S + timedelta(minutes=70 + i * 18),
            lat=lat, lon=lon, h3_cell=jg_cell, measure=3.0,
            extra={"landmark": "Jagatpura"}, decoy_id="DC-001"))
    specs += d1
    decoys.append({
        "decoy_id": "DC-001",
        "label": "Routine garbage complaints in Jagatpura happen to land during the storm",
        "why_unrelated": "Garbage piles up over days; nothing in a storm causes it within the hour",
        "must_not_alert_above": "yellow",
        "specs": d1,
    })

    # DC-002: festival crowd at Hawa Mahal -- garbage and a dark streetlight, no outage.
    hm_cell = by_name["Hawa Mahal"]["h3_cell"]
    d2 = []
    for i, (cat, off) in enumerate([("complaint.garbage", 75), ("complaint.garbage", 84),
                                    ("complaint.streetlight", 96),
                                    ("complaint.garbage", 108)]):
        lat, lon = _point_in_cell(r, hm_cell)
        d2.append(EventSpec(
            category=cat, source="civic_complaints", start=S + timedelta(minutes=off),
            lat=lat, lon=lon, h3_cell=hm_cell, measure=3.0,
            extra={"landmark": "Hawa Mahal"}, decoy_id="DC-002"))
    specs += d2
    decoys.append({
        "decoy_id": "DC-002",
        "label": "Festival crowd near Hawa Mahal leaves garbage and reports a dark streetlight",
        "why_unrelated": "No power cut on this feeder and no heavy rain here; the crowd is scheduled",
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
