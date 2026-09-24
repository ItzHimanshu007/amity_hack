"""Resolves the feeds that lack coordinates: feeder_id -> lat/lon, stop_id -> lat/lon,
landmark text -> lat/lon.

CONTRACT.md §D.2/§D.3/§D.4. Two of the five feeds report opaque ids and no coordinates,
and a third reports a free-text address. Everything those feeds know about *where* comes
through here, which is what the x0.80 / x0.85 confidence multipliers in §A are paying
for.

Reads only the registries (§H names them as Phase 1 outputs and Phase 3 inputs). It does
NOT read event_index.jsonl or ground_truth.json -- those are the answer key.
"""

import json
from pathlib import Path

from ingest.normalize import (RESOLUTION_LANDMARK, RESOLUTION_REGISTRY)
from ingest.zones import cell_of, in_bbox, resolve_landmark_text

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


class Unresolved(Exception):
    """Raised when a location cannot be pinned down. Never guess a centroid."""


class Resolver:
    """Location resolution for every feed, plus a tally of how it went."""

    def __init__(self, data_dir: Path = None):
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self.feeders = self._load("feeder_registry.json")
        self.stops = self._load("stop_registry.json")
        self.stats = {
            "feeder_hit": 0, "feeder_miss": 0,
            "stop_hit": 0, "stop_miss": 0,
            "landmark_hit": 0, "landmark_miss": 0,
            "out_of_bbox": 0,
        }

    def _load(self, name: str) -> dict:
        path = self.data_dir / name
        if not path.exists():
            raise FileNotFoundError(
                f"{path} missing -- run `python -m sim.generate` first (README §c)")
        return json.loads(path.read_text(encoding="utf-8"))

    # --- direct coordinates, still validated -------------------------------
    def direct(self, lat, lon):
        lat, lon = float(lat), float(lon)
        if not in_bbox(lat, lon):
            self.stats["out_of_bbox"] += 1
            raise Unresolved(f"coordinates outside the city bbox: {lat},{lon}")
        return lat, lon, cell_of(lat, lon)

    # --- registry lookups ---------------------------------------------------
    def feeder(self, feeder_id: str):
        f = self.feeders.get(feeder_id)
        if f is None:
            self.stats["feeder_miss"] += 1
            raise Unresolved(f"feeder_id not in registry: {feeder_id}")
        self.stats["feeder_hit"] += 1
        return float(f["lat"]), float(f["lon"]), f["h3_cell"], RESOLUTION_REGISTRY

    def stop(self, stop_id: str):
        s = self.stops.get(stop_id)
        if s is None:
            self.stats["stop_miss"] += 1
            raise Unresolved(f"stop_id not in registry: {stop_id}")
        self.stats["stop_hit"] += 1
        return float(s["lat"]), float(s["lon"]), s["h3_cell"], RESOLUTION_REGISTRY

    def feeder_meta(self, feeder_id: str) -> dict:
        return self.feeders.get(feeder_id, {})

    # --- landmark text ------------------------------------------------------

    # zones.resolve_landmark_text() matches by substring containment, which is right
    # for real addresses ("Near Sindhi Camp Bus Stand" containing "Sindhi Camp") but
    # wrong for short placeholder strings: "NA" is a literal substring of "vaishali
    # NAgar" and "malviya NAgar" (lowercased), so an unresolvable row whose locality
    # register-exports as "NA"/"-"/"Unknown" would falsely resolve to a real landmark
    # instead of being dropped as CONTRACT.md §D.2 requires. Guarded here rather than
    # in zones.py, which is shared and not this lane's file to rewrite.
    _MIN_CANDIDATE_LEN = 4

    def _plausible_address(self, text: str) -> bool:
        letters = sum(1 for c in text if c.isalpha())
        return letters >= self._MIN_CANDIDATE_LEN

    def landmark(self, landmark_text: str, locality_text: str = ""):
        """CONTRACT.md §D.2: try `landmark`, fall back to `locality`. If neither
        resolves the row is dropped -- we do not guess a centroid."""
        for candidate in (landmark_text, locality_text):
            if not candidate or not self._plausible_address(candidate):
                continue
            lm = resolve_landmark_text(candidate)
            if lm:
                self.stats["landmark_hit"] += 1
                return (float(lm["lat"]), float(lm["lon"]), lm["h3_cell"],
                        RESOLUTION_LANDMARK)
        self.stats["landmark_miss"] += 1
        raise Unresolved(
            f"neither landmark {landmark_text!r} nor locality {locality_text!r} resolves")

    def landmark_rate(self) -> float:
        hit, miss = self.stats["landmark_hit"], self.stats["landmark_miss"]
        return hit / (hit + miss) if (hit + miss) else 1.0
