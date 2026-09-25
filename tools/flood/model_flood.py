"""Rain-on-terrain surface-water model for NagarNaadi's Jaipur replay.

Takes the replay's recorded rainfall (data/raw_weather_imd.jsonl, the same
weather_imd feed NagarNaadi ingests) and routes it over real Jaipur elevation
(the AWS Terrarium / SRTM tiles committed under frontend/assets/terrain/) with
a 2D shallow-water model, then writes time-stepped water-depth frames and a
per-H3-area summary for the frontend.

Model: the "local inertial" approximation of the shallow-water equations used
by LISFLOOD-FP (Bates, Horritt & Fewtrell 2010, J. Hydrology 387; with the
de Almeida et al. 2012 stability treatment), a standard scheme for urban
surface-water flooding. Per time step, on a regular grid:

    q  <- (q - g*h_f*dt*d(eta)/dx) / (1 + g*dt*n^2*|q| / h_f^(7/3))
    h  <- h + dt*(rain - losses) + dt*(sum of inflows - outflows)/dx

Losses while water is present: storm drains (CPHEEO design range 12-20 mm/h,
15 used) plus infiltration on the pervious share of the city (7 mm/h).

Context only: nothing here feeds the linker, confidence or the scorecard.
Known limits (also shown in docs/FLOOD_MODEL.md):
  * SRTM ~30 m elevation is a surface model with metres of vertical noise; it
    sees neither individual drains, kerbs nor underpasses. Depths are
    indicative; the useful output is WHERE water collects and WHEN.
  * The storm-drain network is represented as a uniform sink, not as pipes.
  * Rain is interpolated between six gauges; in this replay only IMD-JAI-06
    records rain, so the storm is a small cell over Jaipur Junction.

Run from the repo root after the data pipeline (uses the backend venv):
    backend/.venv/bin/python tools/flood/model_flood.py            # replay storm
    backend/.venv/bin/python tools/flood/model_flood.py --check    # + hotspot sanity run
"""

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import heapq

import h3
import numpy as np
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "terrain"))
from build_terrain import (ANALYSIS_ZOOM, OUT as TERRAIN_DIR, decode_png_rgb,  # noqa: E402
                           tile_range)

DATA = ROOT / "data"
OUT = ROOT / "frontend" / "assets" / "flood"
UTC = timezone.utc

# ---- physical parameters (see docs/FLOOD_MODEL.md for sources) ----
G = 9.81
MANNING_N = 0.035          # mixed urban surface (roads ~0.015-0.02, built/pervious 0.03-0.05)
DRAIN_MM_H = 15.0          # CPHEEO storm-drain design intensity range is 12-20 mm/h
INFILTRATION_MM_H = 7.0    # ~35% pervious share x ~20 mm/h sandy-loam capacity
CFL_ALPHA = 0.7
MAX_DT = 20.0              # s
DOWNSAMPLE = 2             # z13 (~19 m) -> ~38 m cells
IDW_POWER = 2.0
SMOOTH_SIGMA_CELLS = 1.5   # ~50 m Gaussian: removes SRTM resampling texture
NOISE_PIT_M = 1.0          # depressions shallower than this are noise and get filled

# ---- replay window (CONTRACT.md / backend REPLAY_BOOKMARKS) ----
SIM_START = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)   # 5:30 PM IST
SIM_END = datetime(2026, 9, 24, 15, 0, tzinfo=UTC)     # 8:30 PM IST
FRAME_SEC = 300
SHOW_DEPTH_M = 0.02        # frames draw water from 2 cm up
H3_RES = 8

LANDMARK_CELLS = {
    "Hawa Mahal": "883da21891fffff", "Amer Fort": "883da20319fffff",
    "Jal Mahal": "883da2033dfffff", "Albert Hall Museum": "883da218b9fffff",
    "Jaipur Junction": "883da218c7fffff", "Sindhi Camp": "883da218c3fffff",
    "Vaishali Nagar": "883da21801fffff", "Malviya Nagar": "883da20a6dfffff",
    "Mansarovar": "883da219e3fffff", "Vidyadhar Nagar": "883da21ab9fffff",
    "Tonk Road": "883da218a7fffff", "Jagatpura": "883da20b13fffff",
    "Sanganer": "883da20b45fffff",
}
# SAMPLE population model for the water inspector -- not census data. Jaipur's walled
# city is among India's densest cores (~40-50k/km2), the planned colonies around it
# run ~10-20k/km2, and the fringe a few thousand. Two Gaussians around the walled city
# plus a floor, with deterministic per-cell texture. Every place it is shown says
# "sample estimate".
WALLED_CITY = (26.9239, 75.8230)
POP_CORE_KM2, POP_CORE_R_KM = 42000.0, 1.8
POP_SUBURB_KM2, POP_SUBURB_R_KM = 13000.0, 7.5
POP_FLOOR_KM2 = 2500.0
# Reported waterlogging-prone places (news/civic reporting; approximate centres).
REPORTED_HOTSPOTS = {
    "Tonk Road / SMS Hospital": (26.9040, 75.8150),
    "Jawahar Nagar": (26.8870, 75.8290),
    "Vidyadhar Nagar": (26.9610, 75.7780),
    "Mansarovar": (26.8505, 75.7628),
    "Sikar Road": (26.9560, 75.7720),
    "Jal Mahal": (26.9535, 75.8460),
}


# ------------------------------------------------------------------ terrain
def load_dem():
    """Mosaic the committed z13 Terrarium tiles and downsample; returns the
    DEM plus a function mapping grid (row, col) centres to (lat, lon)."""
    z = ANALYSIS_ZOOM
    x0, y0, x1, y1 = tile_range(z)
    rows = []
    for y in range(y0, y1 + 1):
        row = []
        for x in range(x0, x1 + 1):
            rgb = decode_png_rgb((TERRAIN_DIR / str(z) / str(x) / f"{y}.png").read_bytes())
            row.append(rgb[:, :, 0] * 256 + rgb[:, :, 1] + rgb[:, :, 2] / 256 - 32768)
        rows.append(np.hstack(row))
    dem = np.vstack(rows)
    k = DOWNSAMPLE
    hh, ww = dem.shape[0] // k * k, dem.shape[1] // k * k
    dem = dem[:hh, :ww].reshape(hh // k, k, ww // k, k).mean(axis=(1, 3))
    world = 256 * 2 ** z

    def px_to_lonlat(py, px):
        X = (x0 * 256 + (px + 0.5) * k) / world
        Y = (y0 * 256 + (py + 0.5) * k) / world
        lon = X * 360 - 180
        lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * Y))))
        return lat, lon

    def lonlat_to_px(lat, lon):
        X = (lon + 180) / 360 * world
        Y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * world
        return (Y - y0 * 256) / k - 0.5, (X - x0 * 256) / k - 0.5

    dem = condition_dem(dem)
    lat_c = px_to_lonlat(dem.shape[0] / 2, dem.shape[1] / 2)[0]
    dx = 40075016.686 * math.cos(math.radians(lat_c)) / world * k   # metres per cell
    edges = {
        "north_lat": px_to_lonlat(-0.5, 0)[0], "south_lat": px_to_lonlat(dem.shape[0] - 0.5, 0)[0],
        "west_lon": px_to_lonlat(0, -0.5)[1], "east_lon": px_to_lonlat(0, dem.shape[1] - 0.5)[1],
    }
    return dem, dx, px_to_lonlat, lonlat_to_px, edges


def priority_flood(z):
    """Fill every depression to its spill level (Barnes et al. 2014 priority-flood)."""
    ny, nx = z.shape
    filled = z.copy()
    done = np.zeros(z.shape, dtype=bool)
    heap = []
    for r in range(ny):
        for c in (0, nx - 1):
            heapq.heappush(heap, (filled[r, c], r, c)); done[r, c] = True
    for c in range(1, nx - 1):
        for r in (0, ny - 1):
            heapq.heappush(heap, (filled[r, c], r, c)); done[r, c] = True
    while heap:
        e, r, c = heapq.heappop(heap)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                rr, cc = r + dr, c + dc
                if (dr or dc) and 0 <= rr < ny and 0 <= cc < nx and not done[rr, cc]:
                    done[rr, cc] = True
                    if filled[rr, cc] < e:
                        filled[rr, cc] = e
                    heapq.heappush(heap, (filled[rr, cc], rr, cc))
    return filled


def condition_dem(dem):
    """Standard hydrological conditioning: smooth away SRTM texture, then fill
    depressions shallower than NOISE_PIT_M. Deeper basins (e.g. the Jal Mahal lake
    bed) are kept, so real ponding survives."""
    z = ndimage.gaussian_filter(dem, SMOOTH_SIGMA_CELLS, mode="nearest")
    filled = priority_flood(z)
    depth = filled - z
    labels, n = ndimage.label(depth > 1e-6)
    if n:
        max_depth = ndimage.maximum(depth, labels, index=np.arange(1, n + 1))
        shallow = np.zeros(n + 1, dtype=bool)
        shallow[1:] = max_depth < NOISE_PIT_M
        z = np.where(shallow[labels], filled, z)
    return z


# --------------------------------------------------------------------- rain
def load_gauges():
    """Recorded replay rainfall per station: [(lat, lon, [(epoch, mm_per_h)...])].
    rain_mm_15min is a trailing 15-min accumulation reported every 5 min, so
    x4 gives the mean intensity over that window (CONTRACT.md §D.1)."""
    t0, t1 = SIM_START.timestamp() - 1800, SIM_END.timestamp()
    stations = {}
    with (DATA / "raw_weather_imd.jsonl").open() as fh:
        for line in fh:
            r = json.loads(line)
            if t0 <= r["ts"] <= t1:
                s = stations.setdefault(r["station_id"], {"lat": r["lat"], "lon": r["lon"], "obs": []})
                s["obs"].append((r["ts"], float(r["rain_mm_15min"]) * 4.0))
    out = []
    for sid, s in sorted(stations.items()):
        s["obs"].sort()
        out.append((sid, s["lat"], s["lon"], s["obs"]))
    return out


def idw_weights(shape, lonlat_to_px, dx, gauges):
    """Inverse-distance weights (normalised) of each gauge at every cell."""
    ny, nx = shape
    yy, xx = np.mgrid[0:ny, 0:nx].astype(np.float64)
    w = []
    for _sid, lat, lon, _obs in gauges:
        gy, gx = lonlat_to_px(lat, lon)
        d = np.hypot(yy - gy, xx - gx) * dx
        w.append(1.0 / np.maximum(d, dx) ** IDW_POWER)
    w = np.array(w)
    return w / w.sum(axis=0)


def gauge_rates_at(gauges, t_epoch):
    """Latest reported intensity (mm/h) per gauge at time t (0 before first obs)."""
    rates = []
    for _sid, _lat, _lon, obs in gauges:
        r = 0.0
        for ts, rate in obs:
            if ts <= t_epoch:
                r = rate
            else:
                break
        rates.append(r)
    return np.array(rates)


# -------------------------------------------------------------------- model
class LocalInertialModel:
    def __init__(self, z, dx):
        self.z = z
        self.dx = dx
        self.h = np.zeros_like(z)
        self.qx = np.zeros((z.shape[0], z.shape[1] - 1))
        self.qy = np.zeros((z.shape[0] - 1, z.shape[1]))
        self.vol = {"rain": 0.0, "losses": 0.0, "boundary": 0.0}

    def step(self, dt, rain_m_s, loss_m_s):
        z, h, dx = self.z, self.h, self.dx
        eta = z + h
        n2 = MANNING_N ** 2

        def face_flux(q, eta_a, eta_b, z_a, z_b):
            hf = np.maximum(eta_a, eta_b) - np.maximum(z_a, z_b)
            wet = hf > 1e-3
            slope = (eta_b - eta_a) / dx
            q_new = np.zeros_like(q)
            hfw = hf[wet]
            q_new[wet] = (q[wet] - G * hfw * dt * slope[wet]) / (1 + G * dt * n2 * np.abs(q[wet]) / hfw ** (7 / 3))
            return q_new

        self.qx = face_flux(self.qx, eta[:, :-1], eta[:, 1:], z[:, :-1], z[:, 1:])
        self.qy = face_flux(self.qy, eta[:-1, :], eta[1:, :], z[:-1, :], z[1:, :])

        # positivity limiter: a cell can't send out more water than it holds
        out = np.zeros_like(h)
        out[:, :-1] += np.maximum(self.qx, 0); out[:, 1:] += np.maximum(-self.qx, 0)
        out[:-1, :] += np.maximum(self.qy, 0); out[1:, :] += np.maximum(-self.qy, 0)
        avail = h * dx / dt
        scale = np.where(out > avail, avail / np.maximum(out, 1e-12), 1.0)
        self.qx = np.where(self.qx > 0, self.qx * scale[:, :-1], self.qx * scale[:, 1:])
        self.qy = np.where(self.qy > 0, self.qy * scale[:-1, :], self.qy * scale[1:, :])

        dh = np.zeros_like(h)
        dh[:, :-1] -= self.qx; dh[:, 1:] += self.qx
        dh[:-1, :] -= self.qy; dh[1:, :] += self.qy
        h = h + dt * dh / dx

        h = h + rain_m_s * dt
        loss = np.minimum(h, loss_m_s * dt)
        h = h - loss
        h = np.maximum(h, 0.0)

        cell_area = dx * dx
        self.vol["rain"] += float(np.sum(rain_m_s) * dt * cell_area)
        self.vol["losses"] += float(np.sum(loss) * cell_area)
        # open (absorbing) boundary: water reaching the domain edge leaves
        edge = np.zeros_like(h, dtype=bool)
        edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True
        self.vol["boundary"] += float(np.sum(h[edge]) * cell_area)
        h[edge] = 0.0
        self.h = h

    def stable_dt(self):
        hmax = max(float(self.h.max()), 0.01)
        return min(MAX_DT, CFL_ALPHA * self.dx / math.sqrt(G * hmax))


def run(z, dx, rain_at, t_start, t_end, frame_sec, on_frame):
    """Integrate from t_start to t_end (epoch s); rain_at(t) -> rain rate field (m/s)."""
    model = LocalInertialModel(z, dx)
    loss_m_s = (DRAIN_MM_H + INFILTRATION_MM_H) / 1000 / 3600
    t = t_start
    next_frame = t_start
    steps = 0
    while t < t_end:
        if t >= next_frame - 1e-6:
            on_frame(next_frame, model)
            next_frame += frame_sec
        dt = min(model.stable_dt(), next_frame - t, t_end - t)
        model.step(dt, rain_at(t), loss_m_s)
        t += dt
        steps += 1
    on_frame(t_end, model)
    stored = float(model.h.sum() * dx * dx)
    v = model.vol
    err = (v["rain"] - v["losses"] - v["boundary"] - stored) / max(v["rain"], 1e-9) * 100
    return model, {"steps": steps, "rain_m3": round(v["rain"]), "losses_m3": round(v["losses"]),
                   "boundary_m3": round(v["boundary"]), "stored_end_m3": round(stored),
                   "mass_balance_error_pct": round(err, 4)}


# ------------------------------------------------------------------- output
def sample_population(shape, px_to_lonlat, dx):
    """People per model cell from the SAMPLE density model above (not census data)."""
    ny, nx = shape
    lat0, lon0 = WALLED_CITY
    pop = np.zeros(shape)
    km_per_deg_lat = 111.0
    km_per_deg_lon = 111.0 * np.cos(np.radians(lat0))
    area_km2 = dx * dx / 1e6
    for r in range(ny):
        for c in range(nx):
            lat, lon = px_to_lonlat(r, c)
            d = np.hypot((lat - lat0) * km_per_deg_lat, (lon - lon0) * km_per_deg_lon)
            dens = (POP_FLOOR_KM2 + POP_SUBURB_KM2 * np.exp(-(d / POP_SUBURB_R_KM) ** 2)
                    + POP_CORE_KM2 * np.exp(-(d / POP_CORE_R_KM) ** 2))
            texture = 0.6 + 0.8 * ((r * 7919 + c * 104729) % 1000) / 1000.0
            pop[r, c] = dens * area_km2 * texture
    return pop


DISPLAY_BLOCK = 2            # the map draws 2x2-cell blocks (~68 m): same water, 1/4 the features
DISPLAY_FROM_M = 0.03        # a block is drawn once its mean depth reaches 3 cm


def water_features(frames, peak_depth, peak_time, px_to_lonlat, dem, pop):
    """The modelled water as ~68 m map blocks (2x2 model cells), each carrying its
    MEAN depth (cm) per frame -- the mean keeps block volume exact. Vector fills drape
    on MapLibre's 3D terrain cheaply; an image source there re-renders every frame.

    Per block, for the inspector: `z` mean ground elevation (m, SRTM), `p` people
    living in it (SAMPLE estimate), `pk` frame of peak depth, `id` a grid ref.
    `d` starts at frame `o` (leading dry frames are dropped to keep the file small)."""
    b = DISPLAY_BLOCK
    ny, nx = dem.shape
    ny2, nx2 = ny // b, nx // b

    def blocks(a):
        return a[:ny2 * b, :nx2 * b].reshape(ny2, b, nx2, b).mean(axis=(1, 3))

    depth = np.stack([blocks(h) for _t, h in frames])          # frames x ny2 x nx2
    ground = blocks(dem)
    people = blocks(pop) * b * b
    rows, cols = np.where(depth.max(axis=0) >= DISPLAY_FROM_M)
    feats = []
    for r, c in zip(rows, cols):
        series = [int(round(v)) for v in depth[:, r, c] * 100]
        first = next((i for i, v in enumerate(series) if v > 0), len(series))
        r0, c0 = r * b, c * b
        n, w = px_to_lonlat(r0 - 0.5, c0 - 0.5)
        s_, e = px_to_lonlat(r0 + b - 0.5, c0 + b - 0.5)
        ring = [[round(w, 5), round(n, 5)], [round(e, 5), round(n, 5)], [round(e, 5), round(s_, 5)],
                [round(w, 5), round(s_, 5)], [round(w, 5), round(n, 5)]]
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                      "properties": {"o": first, "d": series[first:],
                                     "z": round(float(ground[r, c]), 1),
                                     "p": int(round(people[r, c])),
                                     "pk": int(np.argmax(series)),
                                     "id": f"JPR-{r:03d}-{c:03d}"}})
    return {"type": "FeatureCollection", "features": feats}


def iso(t_epoch):
    return datetime.fromtimestamp(t_epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def ist(t_epoch):
    return (datetime.fromtimestamp(t_epoch, UTC) + timedelta(hours=5, minutes=30)).strftime("%-I:%M %p")


def cell_pixels(shape, px_to_lonlat):
    """H3 res-8 area of every grid cell centre (for per-area summaries)."""
    ny, nx = shape
    lab = np.empty((ny, nx), dtype=object)
    for r in range(ny):
        for c in range(nx):
            lab[r, c] = h3.latlng_to_cell(*px_to_lonlat(r, c), H3_RES)
    return lab


# --------------------------------------------------------------------- main
def replay_run(dem, dx, px_to_lonlat, lonlat_to_px, edges):
    gauges = load_gauges()
    weights = idw_weights(dem.shape, lonlat_to_px, dx, gauges)
    print(f"gauges: {len(gauges)}; wet in replay: "
          f"{[sid for sid, _a, _b, obs in gauges if any(r > 0 for _t, r in obs)]}")

    def rain_at(t):
        rates = gauge_rates_at(gauges, t)                      # mm/h per gauge
        return np.tensordot(rates, weights, axes=1) / 1000 / 3600

    frames = []
    peak_depth = np.zeros_like(dem)
    peak_time = np.zeros_like(dem)

    def on_frame(t, model):
        frames.append((t, model.h.copy()))
        better = model.h > peak_depth
        peak_depth[better] = model.h[better]
        peak_time[better] = t

    model, stats = run(dem, dx, rain_at, SIM_START.timestamp(), SIM_END.timestamp(), FRAME_SEC, on_frame)
    print("mass balance:", stats)
    return frames, peak_depth, peak_time, stats, gauges


def write_outputs(frames, peak_depth, peak_time, stats, dem, dx, px_to_lonlat, edges):
    OUT.mkdir(parents=True, exist_ok=True)
    frames_dir = OUT / "frames"
    if frames_dir.exists():                      # superseded PNG frames
        for old in frames_dir.glob("*.png"):
            old.unlink()
        frames_dir.rmdir()
    pop = sample_population(dem.shape, px_to_lonlat, dx)
    water = water_features(frames, peak_depth, peak_time, px_to_lonlat, dem, pop)
    (OUT / "water.geojson").write_text(json.dumps(water, separators=(",", ":")))

    # crop to where water ever reaches the display threshold (+ margin)
    ever = peak_depth >= SHOW_DEPTH_M
    rows, cols = np.where(ever)
    pad = 6
    r0, r1 = max(0, rows.min() - pad), min(dem.shape[0], rows.max() + pad + 1)
    c0, c1 = max(0, cols.min() - pad), min(dem.shape[1], cols.max() + pad + 1)
    nw = px_to_lonlat(r0 - 0.5, c0 - 0.5)
    se = px_to_lonlat(r1 - 0.5, c1 - 0.5)
    coords = [[nw[1], nw[0]], [se[1], nw[0]], [se[1], se[0]], [nw[1], se[0]]]  # tl, tr, br, bl

    labels = cell_pixels(dem.shape, px_to_lonlat)
    frame_meta = []
    for t, h in frames:
        wet = h >= SHOW_DEPTH_M
        frame_meta.append({
            "t_utc": iso(t), "wet": bool(wet.any()),
            "wet_area_km2": round(float(wet.sum()) * dx * dx / 1e6, 3),
            "max_depth_cm": round(float(h.max()) * 100, 1),
            "volume_m3": round(float(h.sum()) * dx * dx),
            "people_over_10cm_sample": int(round(float(pop[h >= 0.10].sum()))),
        })

    # per-H3-area state at every frame, so the UI can describe the model as of
    # the replay's current time (never a peak the replay hasn't reached yet)
    cells = {}
    cell_area = dx * dx
    for cell in set(labels[ever]):
        mask = labels == cell
        pd = peak_depth[mask]
        if not (pd >= SHOW_DEPTH_M).any():
            continue
        best = np.argmax(pd)
        cells[cell] = {
            "peak_depth_cm": round(float(pd.max()) * 100, 1),
            "peak_t_utc": iso(float(peak_time[mask][best])),
            "area_over_10cm_pct": round(float((pd >= 0.10).mean()) * 100, 1),
            "over10_pct_by_frame": [round(float((h[mask] >= 0.10).mean()) * 100, 1) for _t, h in frames],
            "max_depth_cm_by_frame": [round(float(h[mask].max()) * 100, 1) for _t, h in frames],
        }

    doc = {
        "model": "local inertial shallow-water (LISFLOOD-FP style; Bates et al. 2010)",
        "rain_source": "this replay's weather_imd gauges (data/raw_weather_imd.jsonl), inverse-distance interpolated",
        "terrain_source": "AWS Terrarium / SRTM elevation, frontend/assets/terrain (z13, downsampled)",
        "params": {"cell_m": round(dx, 1), "display_block_m": round(dx * DISPLAY_BLOCK, 1),
                   "display_from_cm": DISPLAY_FROM_M * 100, "smooth_sigma_cells": SMOOTH_SIGMA_CELLS, "noise_pit_fill_m": NOISE_PIT_M, "manning_n": MANNING_N, "drain_mm_h": DRAIN_MM_H,
                   "infiltration_mm_h": INFILTRATION_MM_H, "show_from_cm": SHOW_DEPTH_M * 100,
                   "frame_sec": FRAME_SEC},
        "caveat_en": "Indicative model of where this replay's rain would collect on real terrain. Not a forecast.",
        "population_note_en": "Population per cell is a SAMPLE estimate from a simple density model (dense walled city, colonies, fringe), not census data.",
        "mass_balance": stats,
        "bounds": coords,
        "water_cells": len(water["features"]),
        "frames": frame_meta,
        "cells": cells,
    }
    (OUT / "flood.json").write_text(json.dumps(doc, separators=(",", ":")))
    print(f"wrote {len(water['features'])} water cells x {len(frame_meta)} frames, {len(cells)} wet areas -> {OUT}")
    peak = max(frame_meta, key=lambda f: f["volume_m3"])
    print(f"peak volume {peak['volume_m3']} m3 at {ist(datetime.fromisoformat(peak['t_utc'].replace('Z', '+00:00')).timestamp())} IST, "
          f"wet area {peak['wet_area_km2']} km2, max depth {peak['max_depth_cm']} cm")
    for name, c in LANDMARK_CELLS.items():
        if c in cells:
            v = cells[c]
            print(f"  {name:18s} peak {v['peak_depth_cm']} cm at "
                  f"{ist(datetime.fromisoformat(v['peak_t_utc'].replace('Z', '+00:00')).timestamp())} IST, "
                  f"{v['area_over_10cm_pct']}% of area over 10 cm")
    return doc


def hotspot_check(dem, dx, px_to_lonlat, lonlat_to_px):
    """Sanity check independent of the replay: 60 mm in one hour, uniformly over
    the city. Do reported Jaipur waterlogging spots collect more water than the
    city average?"""
    rate = np.full(dem.shape, 60.0 / 1000 / 3600)
    dry = np.zeros(dem.shape)

    def rain_at(t):
        return rate if t < 3600 else dry

    peak = np.zeros_like(dem)

    def on_frame(_t, model):
        np.maximum(peak, model.h, out=peak)

    _model, stats = run(dem, dx, rain_at, 0, 2 * 3600, 600, on_frame)
    city = float((peak >= 0.10).mean() * 100)
    wet = (peak >= 0.10).astype(np.float64)
    ny, nx = dem.shape
    radius = int(round(1000 / dx))
    yy, xx = np.mgrid[-radius:radius + 1, -radius:radius + 1]
    disk = (np.hypot(yy, xx) * dx <= 1000).astype(np.float64)
    # share of area over 10 cm within 1 km of every cell (FFT-free: summed-area via convolution)
    local_share = ndimage.convolve(wet, disk, mode="constant") / ndimage.convolve(np.ones_like(wet), disk, mode="constant") * 100

    def share_at(lat, lon):
        gy, gx = lonlat_to_px(lat, lon)
        return float(local_share[int(round(gy)), int(round(gx))])

    rows = []
    for name, (lat, lon) in REPORTED_HOTSPOTS.items():
        local = share_at(lat, lon)
        rows.append({"place": name, "area_over_10cm_pct_within_1km": round(local, 1),
                     "vs_city": round(local / max(city, 1e-9), 2)})
    # baseline: the same measure at 500 random places at least 1 km inside the domain
    rng = np.random.default_rng(42)
    rr = rng.integers(radius, ny - radius, 500)
    cc = rng.integers(radius, nx - radius, 500)
    random_shares = local_share[rr, cc]
    hot_mean = float(np.mean([r["area_over_10cm_pct_within_1km"] for r in rows]))
    above = sum(r["vs_city"] > 1 for r in rows)
    # chance that 6 random places average at least the hotspots' mean
    sims = rng.choice(random_shares, size=(20000, len(rows))).mean(axis=1)
    p_value = float((sims >= hot_mean).mean())
    return {"storm": "60 mm in 1 h, uniform over the city", "city_area_over_10cm_pct": round(city, 1),
            "mass_balance_error_pct": stats["mass_balance_error_pct"], "reported_hotspots": rows,
            "hotspots_above_city_average": f"{above} of {len(rows)}",
            "hotspot_mean_pct": round(hot_mean, 1),
            "random_places_mean_pct": round(float(random_shares.mean()), 1),
            "p_random_6_places_as_wet": round(p_value, 4)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="also run the reported-hotspot sanity check")
    args = ap.parse_args()
    dem, dx, px_to_lonlat, lonlat_to_px, edges = load_dem()
    print(f"grid {dem.shape} at {dx:.1f} m, elevation {dem.min():.0f}..{dem.max():.0f} m")
    frames, peak_depth, peak_time, stats, _g = replay_run(dem, dx, px_to_lonlat, lonlat_to_px, edges)
    previous = json.loads((OUT / "flood.json").read_text()) if (OUT / "flood.json").exists() else {}
    doc = write_outputs(frames, peak_depth, peak_time, stats, dem, dx, px_to_lonlat, edges)
    if not args.check and "hotspot_check" in previous:
        doc["hotspot_check"] = previous["hotspot_check"]     # same parameters; rerun with --check to refresh
        (OUT / "flood.json").write_text(json.dumps(doc, separators=(",", ":")))
    if args.check:
        chk = hotspot_check(dem, dx, px_to_lonlat, lonlat_to_px)
        print("hotspot check:", json.dumps(chk, indent=1))
        doc["hotspot_check"] = chk
        (OUT / "flood.json").write_text(json.dumps(doc, separators=(",", ":")))


if __name__ == "__main__":
    main()
