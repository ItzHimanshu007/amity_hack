"""One reader per raw feed, plus the complaint-type synonym table.

CONTRACT.md §D. Format quirks live here and nowhere else: the adapters above this layer
see tidy dicts, never a CSV quirk or an epoch integer.

CONTRACT.md §D.2 places the complaint synonym table in this module specifically, so that
there is exactly one mapping from the register's free text to our category ids.
"""

import csv
import json
from pathlib import Path

from contract_constants import FEEDS

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def raw_path(feed: str, data_dir: Path = None) -> Path:
    return (Path(data_dir) if data_dir else DATA_DIR) / FEEDS[feed]["file"]


def read_jsonl(feed: str, data_dir: Path = None):
    """Yields (line_no, record) for a JSON Lines feed."""
    path = raw_path(feed, data_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing -- run `python -m sim.generate` first (README §c)")
    with path.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if line:
                yield n, json.loads(line)


def read_complaints_csv(data_dir: Path = None):
    """Yields (line_no, row_dict, raw_line) for the complaints register.

    The raw line comes along because `GET /raw/civic_complaints` serves the literal text
    (CONTRACT.md §E) and the data room is meant to show it exactly as it arrived.
    """
    path = raw_path("civic_complaints", data_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing -- run `python -m sim.generate` first (README §c)")
    with path.open(encoding="utf-8", newline="") as fh:
        lines = fh.read().splitlines()
    reader = csv.DictReader(lines)
    for n, row in enumerate(reader, 2):        # line 1 is the header
        yield n, row, lines[n - 1]


# --------------------------------------------------- complaint type mapping ---
# CONTRACT.md §D.2: the register's `complaint_type` is free text with inconsistent case
# and trailing spaces. Keys here are the NORMALISED form (see normalize_type). Anything
# absent is an unmapped type, which is a dropped row -- never a guess.

COMPLAINT_TYPE_MAP = {
    "water logging":               "complaint.waterlogging",
    "waterlogging":                "complaint.waterlogging",
    "water-logging":               "complaint.waterlogging",
    "jal bharav":                  "complaint.waterlogging",

    "garbage":                     "complaint.garbage",
    "garbage not lifted":          "complaint.garbage",
    "solid waste":                 "complaint.garbage",
    "kachra":                      "complaint.garbage",

    "street light not working":    "complaint.streetlight",
    "street light":                "complaint.streetlight",
    "streetlight":                 "complaint.streetlight",
    "street lamp":                 "complaint.streetlight",

    "road damage":                 "complaint.road_damage",
    "pothole":                     "complaint.road_damage",
    "potholes":                    "complaint.road_damage",
    "broken road":                 "complaint.road_damage",

    # 'garbage burning' is smoke, not garbage -- exact-match keys keep that unambiguous.
    "smoke":                       "complaint.smoke",
    "burning":                     "complaint.smoke",
    "smoke/burning":               "complaint.smoke",
    "garbage burning":             "complaint.smoke",

    # A resident reporting a dark junction. Same category id as the DISCOM feeder trip,
    # different source -- CONTRACT.md §D dedupe carve-out.
    "signal not working":          "traffic.signal_down",
    "traffic signal not working":  "traffic.signal_down",
    "signal down":                 "traffic.signal_down",
    "traffic signal":              "traffic.signal_down",
}


def normalize_type(raw_type: str) -> str:
    """Fold case and collapse whitespace, so 'Water Logging', 'water logging  ' and
    'WATERLOGGING' land on one key."""
    return " ".join((raw_type or "").strip().lower().split())


def category_for_complaint(raw_type: str):
    """The category id, or None when the type is unmapped (row is dropped)."""
    return COMPLAINT_TYPE_MAP.get(normalize_type(raw_type))
