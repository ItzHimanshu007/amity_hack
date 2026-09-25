"""Build NagarNaadi's offline terrain layer for Jaipur from real elevation data.

Downloads AWS Terrarium elevation tiles (SRTM-derived, public) for the Jaipur
bbox once, writes them under frontend/assets/terrain/{z}/{x}/{y}.png so the 3D
map needs no internet, and computes per-H3-area terrain facts into
frontend/assets/terrain/terrain_h3.json.

Terrain is CONTEXT, not evidence: nothing here feeds the linker, confidence or
the scorecard. The frontend only uses it to render relief and to describe what
the terrain does or does not explain about a flood-type situation.

The slope/flow-accumulation approach is adapted from the TerrainAnalyzer in our
Jal Drishti project (github.com/Ayush090207/TeamNeuronest_MUJ_Hackx4.0,
src/terrain_analyzer.py), applied here to real Jaipur elevation rather than a
synthetic DEM.

Run once from the repo root (uses the backend venv's numpy + h3):
    backend/.venv/bin/python tools/terrain/build_terrain.py
"""

import json
import math
import struct
import sys
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path

import h3
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "frontend" / "assets" / "terrain"
TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"

# CONTRACT.md §C bbox, plus a margin so the map viewport edges still have relief.
S, W, N, E = 26.79, 75.69, 26.99, 75.89
MARGIN = 0.06
ZOOMS = range(10, 14)       # 10-13; MapLibre overzooms 13 beyond that
ANALYSIS_ZOOM = 13          # ~19 m/pixel at this latitude
H3_RES = 8
DOWNSAMPLE = 4              # flow accumulation on ~76 m cells

LANDMARK_CELLS = {
    "Hawa Mahal": "883da21891fffff", "Amer Fort": "883da20319fffff",
    "Jal Mahal": "883da2033dfffff", "Albert Hall Museum": "883da218b9fffff",
    "Jaipur Junction": "883da218c7fffff", "Sindhi Camp": "883da218c3fffff",
    "Vaishali Nagar": "883da21801fffff", "Malviya Nagar": "883da20a6dfffff",
    "Mansarovar": "883da219e3fffff",
}


def tile_xy(lat, lon, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def pixel_xy(lat, lon, z):
    n = 2 ** z * 256
    x = (lon + 180) / 360 * n
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def decode_png_rgb(data: bytes) -> np.ndarray:
    """Minimal PNG decoder for 8-bit RGB/RGBA, non-interlaced (what Terrarium serves)."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    i, idat = 8, b""
    width = height = color_type = None
    while i < len(data):
        length, kind = struct.unpack(">I4s", data[i:i + 8])
        body = data[i + 8:i + 8 + length]
        if kind == b"IHDR":
            width, height, depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", body)
            assert depth == 8 and color_type in (2, 6) and interlace == 0, "unsupported PNG"
        elif kind == b"IDAT":
            idat += body
        i += 12 + length
    bpp = 3 if color_type == 2 else 4
    stride = width * bpp
    raw = np.frombuffer(zlib.decompress(idat), np.uint8)
    out = np.zeros((height, stride), np.int32)
    prev = np.zeros(stride, np.int32)
    for r in range(height):
        f = raw[r * (stride + 1)]
        line = raw[r * (stride + 1) + 1:(r + 1) * (stride + 1)].astype(np.int32)
        cur = np.zeros(stride, np.int32)
        if f == 0:
            cur = line.copy()
        elif f == 2:
            cur = (line + prev) & 255
        else:
            for c in range(stride):
                a = cur[c - bpp] if c >= bpp else 0
                b = prev[c]
                cc = prev[c - bpp] if c >= bpp else 0
                if f == 1:
                    v = line[c] + a
                elif f == 3:
                    v = line[c] + (a + b) // 2
                else:
                    p = a + b - cc
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - cc)
                    v = line[c] + (a if pa <= pb and pa <= pc else b if pb <= pc else cc)
                cur[c] = v & 255
        out[r] = cur
        prev = cur
    return out.reshape(height, width, bpp)[:, :, :3].astype(np.float64)


def fetch_tile(z, x, y) -> bytes:
    path = OUT / str(z) / str(x) / f"{y}.png"
    if path.exists():
        return path.read_bytes()
    data = urllib.request.urlopen(TILE_URL.format(z=z, x=x, y=y), timeout=30).read()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def tile_range(z):
    x0, y0 = tile_xy(N + MARGIN, W - MARGIN, z)
    x1, y1 = tile_xy(S - MARGIN, E + MARGIN, z)
    return x0, y0, x1, y1


def d8_flow_accumulation(dem: np.ndarray) -> np.ndarray:
    """Upstream-cell count per pixel: each cell drains to its steepest downhill neighbour."""
    h, w = dem.shape
    flat = dem.ravel()
    acc = np.ones(h * w)
    neighbours = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    for idx in np.argsort(flat)[::-1]:
        r, c = divmod(int(idx), w)
        best, best_drop = None, 0.0
        for dr, dc in neighbours:
            rr, cc = r + dr, c + dc
            if 0 <= rr < h and 0 <= cc < w:
                drop = (flat[idx] - flat[rr * w + cc]) / (1.414 if dr and dc else 1.0)
                if drop > best_drop:
                    best_drop, best = drop, rr * w + cc
        if best is not None:
            acc[best] += acc[idx]
    return acc.reshape(h, w)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    counts = {}
    for z in ZOOMS:
        x0, y0, x1, y1 = tile_range(z)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                fetch_tile(z, x, y)
        counts[z] = (x1 - x0 + 1) * (y1 - y0 + 1)
    print("tiles per zoom:", counts)

    # --- mosaic the analysis zoom into one DEM ---
    z = ANALYSIS_ZOOM
    x0, y0, x1, y1 = tile_range(z)
    rows = []
    for y in range(y0, y1 + 1):
        row = []
        for x in range(x0, x1 + 1):
            rgb = decode_png_rgb(fetch_tile(z, x, y))
            row.append(rgb[:, :, 0] * 256 + rgb[:, :, 1] + rgb[:, :, 2] / 256 - 32768)
        rows.append(np.hstack(row))
    dem = np.vstack(rows)
    print(f"DEM {dem.shape}, {dem.min():.0f}..{dem.max():.0f} m")

    def dem_at(lat, lon):
        px, py = pixel_xy(lat, lon, z)
        return float(dem[int(py - y0 * 256), int(px - x0 * 256)])

    # --- flow accumulation on a downsampled DEM ---
    k = DOWNSAMPLE
    hh, ww = dem.shape[0] // k * k, dem.shape[1] // k * k
    coarse = dem[:hh, :ww].reshape(hh // k, k, ww // k, k).mean(axis=(1, 3))
    acc = d8_flow_accumulation(coarse)

    def upstream_at(lat, lon):
        px, py = pixel_xy(lat, lon, z)
        r, c = int(py - y0 * 256) // k, int(px - x0 * 256) // k
        return float(acc[max(0, r - 2):r + 3, max(0, c - 2):c + 3].max())

    # --- per-H3-area facts ---
    cells = sorted(h3.polygon_to_cells(h3.LatLngPoly([(S, W), (S, E), (N, E), (N, W)]), H3_RES))
    elev = {c: dem_at(*h3.cell_to_latlng(c)) for c in cells}
    up = {c: upstream_at(*h3.cell_to_latlng(c)) for c in cells}
    elev_arr, up_arr = np.array(list(elev.values())), np.array(list(up.values()))
    out_cells = {}
    for c in cells:
        ring = [elev[n] for n in h3.grid_disk(c, 2) if n in elev and n != c]
        out_cells[c] = {
            "elev_m": round(elev[c], 1),
            "rel_to_surroundings_m": round(elev[c] - float(np.mean(ring)), 1),
            "elev_pct": round(float((elev_arr < elev[c]).mean() * 100)),
            "upstream_cells": int(up[c]),
            "flow_pct": round(float((up_arr < up[c]).mean() * 100)),
        }

    doc = {
        "source": "AWS Terrarium elevation tiles (SRTM-derived), s3.amazonaws.com/elevation-tiles-prod",
        "built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": {
            "elevation": f"Terrarium z{z} (~19 m/px), value at each H3 res-{H3_RES} area's centre",
            "rel_to_surroundings_m": "area elevation minus the mean of its 2-ring neighbours (negative = sits lower)",
            "flow": f"D8 flow accumulation on a {k}x-downsampled DEM (~76 m cells); max upstream count within ~150 m of the area centre",
            "percentiles": "share of the city's 591 areas that are lower (elev_pct) / carry less flow (flow_pct)",
            "adapted_from": "Jal Drishti src/terrain_analyzer.py (flow accumulation / terrain analysis)",
        },
        "tiles": {"zooms": list(ZOOMS), "bounds": [W - MARGIN, S - MARGIN, E + MARGIN, N + MARGIN]},
        "city": {
            "elev_p10_m": round(float(np.percentile(elev_arr, 10))),
            "elev_median_m": round(float(np.median(elev_arr))),
            "elev_p90_m": round(float(np.percentile(elev_arr, 90))),
        },
        "cells": out_cells,
    }
    (OUT / "terrain_h3.json").write_text(json.dumps(doc, separators=(",", ":")))
    print(f"wrote {len(out_cells)} areas to {OUT / 'terrain_h3.json'}")
    for name, c in LANDMARK_CELLS.items():
        if c in out_cells:
            print(f"  {name:18s} {out_cells[c]}")


if __name__ == "__main__":
    sys.exit(main())
