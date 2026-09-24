"""H3 helpers shared by every phase: lat/lon -> res-8 cell, neighbors, centroid, zone label.

CONTRACT.md §C. Constants come from contract_constants.py -- nothing here redefines them.
Pure geometry only: no parsing, no normalization, no feed knowledge.
"""

import json
import math
import re
from functools import lru_cache
from pathlib import Path

import h3

from contract_constants import CITY_BBOX, EXPECTED_BBOX_CELLS, H3_RES

# Landmarks live with the generator that writes them into the complaints feed, but
# Phase 3 needs the same list to resolve those text addresses back to coordinates.
LANDMARKS_PATH = Path(__file__).resolve().parents[1] / "sim" / "landmarks.json"

# Prefixes the complaints feed sticks in front of a landmark name (CONTRACT.md §D.2).
ADDRESS_PREFIXES = ("near", "opp", "opposite", "behind", "nr.", "nr", "in front of", "next to")

# A candidate shorter than this (letters only) can never be a real landmark address --
# it rejects junk placeholders like "NA" / "-" / "Unknown" before they reach matching.
_MIN_LANDMARK_CANDIDATE_LETTERS = 4


@lru_cache(maxsize=1)
def load_landmarks() -> tuple:
    """The reference landmarks, each with its res-8 cell attached."""
    raw = json.loads(LANDMARKS_PATH.read_text(encoding="utf-8"))
    out = []
    for lm in raw["landmarks"]:
        out.append({
            "name": lm["name"],
            "name_hi": lm["name_hi"],
            "lat": lm["lat"],
            "lon": lm["lon"],
            "h3_cell": cell_of(lm["lat"], lm["lon"]),
        })
    return tuple(out)


def cell_of(lat: float, lon: float) -> str:
    """CONTRACT.md §C. Argument order is (lat, lon) -- MapLibre's is the other way."""
    return h3.latlng_to_cell(lat, lon, H3_RES)


def in_bbox(lat: float, lon: float) -> bool:
    sw_lat, sw_lon, ne_lat, ne_lon = CITY_BBOX
    return sw_lat <= lat <= ne_lat and sw_lon <= lon <= ne_lon


@lru_cache(maxsize=1)
def bbox_cells() -> tuple:
    """Every res-8 cell inside the city bbox. CONTRACT.md §C says there are 591."""
    sw_lat, sw_lon, ne_lat, ne_lon = CITY_BBOX
    poly = h3.LatLngPoly([
        (sw_lat, sw_lon), (sw_lat, ne_lon), (ne_lat, ne_lon), (ne_lat, sw_lon),
    ])
    return tuple(sorted(h3.h3shape_to_cells(poly, H3_RES)))


def assert_bbox_cell_count() -> int:
    """Hard check that the contract's cell count still holds."""
    n = len(bbox_cells())
    if n != EXPECTED_BBOX_CELLS:
        raise AssertionError(
            f"bbox holds {n} res-{H3_RES} cells, CONTRACT.md §C says {EXPECTED_BBOX_CELLS}"
        )
    return n


def neighbors(cell: str, k: int = 1) -> set:
    """The cell plus everything within k rings. k=1 -> 7 cells, k=2 -> 19."""
    return set(h3.grid_disk(cell, k))


def grid_distance(a: str, b: str) -> int:
    return h3.grid_distance(a, b)


def centroid(cell: str) -> tuple:
    """(lat, lon) of the cell centre."""
    return h3.cell_to_latlng(cell)


def boundary_geojson(cell: str) -> list:
    """Vertices as [lng, lat] pairs, the order MapLibre wants."""
    return [[lng, lat] for lat, lng in h3.cell_to_boundary(cell)]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_landmark(lat: float, lon: float) -> dict:
    return min(
        load_landmarks(),
        key=lambda lm: haversine_km(lat, lon, lm["lat"], lm["lon"]),
    )


def resolve_landmark_text(text: str):
    """'Near Sindhi Camp Bus Stand' -> the Sindhi Camp landmark, or None.

    Case-insensitive, strips the address prefixes the complaints feed uses, and matches
    on containment so trailing detail ('Bus Stand', 'Gate 2') does not break it.

    Matching is WORD-BOUNDARY containment, not raw substring: plain `"na" in s` would
    match the junk placeholder "NA" against "vaishali NAgar" or "malviya NAgar" and
    silently resolve an intentionally-unresolvable row to the wrong landmark. A short
    candidate (below _MIN_LANDMARK_CANDIDATE_LETTERS letters) is rejected outright,
    since no real landmark address is that short.
    """
    if not text:
        return None
    s = " ".join(text.strip().lower().split())
    for prefix in ADDRESS_PREFIXES:
        if s.startswith(prefix + " "):
            s = s[len(prefix) + 1:].strip()
            break
    if len(re.sub(r"[^a-z]", "", s)) < _MIN_LANDMARK_CANDIDATE_LETTERS:
        return None

    best = None
    for lm in load_landmarks():
        name = lm["name"].lower()
        matches = (
            s == name
            or re.search(rf"\b{re.escape(name)}\b", s)
            or re.search(rf"\b{re.escape(s)}\b", name)
        )
        if matches:
            # Prefer the longest matching name: 'Jal Mahal' should not win 'Hawa Mahal'.
            if best is None or len(lm["name"]) > len(best["name"]):
                best = lm
    return best


def zone_label(cell: str) -> tuple:
    """(label_en, label_hi) for one cell. CONTRACT.md §C label rule."""
    lat, lon = centroid(cell)
    lm = nearest_landmark(lat, lon)
    if lm["h3_cell"] == cell:
        return lm["name"], lm["name_hi"]
    return f"Near {lm['name']}", f"{lm['name_hi']} के पास"
