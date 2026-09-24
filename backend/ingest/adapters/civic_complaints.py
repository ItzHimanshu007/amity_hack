"""civic_complaints: CSV, IST text dates, landmark addresses. CONTRACT.md §D.2.

The messiest feed. Everything it throws at us is deliberate:
  - `lodged_at` is DD/MM/YYYY h:mm AM/PM in IST, day first, no timezone marker
  - no coordinates: only a landmark string with a Near/Opp/Behind prefix
  - `complaint_type` is free text; unmapped types are dropped rows, never guesses
  - `text` is Hinglish and carries a resident's name and mobile number

PII never reaches a canonical event: the §A schema has no prose field, so the complaint
text is read for nothing except a masked-item count and then discarded here. Masking for
`GET /raw/{feed}` is a separate path -- see ingest/pii.mask_record.

Severity is NOT final when this adapter returns. CONTRACT.md §A scales complaint.* by
"count of open complaints in the cell, 30 min window", which needs every complaint in
hand, so ingest.run applies it as a second pass.
"""

from contract_constants import CONFIDENCE
from ingest.geocode import Unresolved
from ingest.normalize import (RESOLUTION_LANDMARK, build_event, confidence_for,
                              from_ist_complaint, iso, received_at_for, ref_complaint)
from ingest.parsers import category_for_complaint, read_complaints_csv
from ingest.pii import count_pii

FEED = "civic_complaints"


def extract(resolver, data_dir=None):
    stats = {"records": 0, "candidates": 0, "dropped_unmapped_type": 0,
             "dropped_unresolved": 0, "dropped_bad_timestamp": 0,
             "pii_items_seen": 0, "records_with_pii": 0, "closed_rows": 0,
             "last_raw_received_at": None}
    last_raw = None
    out = []

    for _n, row, _raw_line in read_complaints_csv(data_dir):
        stats["records"] += 1

        category = category_for_complaint(row.get("complaint_type"))
        if category is None:
            stats["dropped_unmapped_type"] += 1
            continue

        try:
            start = from_ist_complaint(row["lodged_at"])
        except (ValueError, KeyError):
            stats["dropped_bad_timestamp"] += 1
            continue

        # A row the register sent proves liveness even if we later drop it for an
        # unmapped type or an unresolvable address.
        arrived = received_at_for(FEED, start)
        if last_raw is None or arrived > last_raw:
            last_raw = arrived

        # CONTRACT.md §D.2: landmark first, then locality. Never guess a centroid.
        try:
            lat, lon, cell, resolution = resolver.landmark(
                row.get("landmark", ""), row.get("locality", ""))
        except Unresolved:
            stats["dropped_unresolved"] += 1
            continue

        # Read the prose only to count what a scrubber would remove, then drop it.
        n_pii = count_pii(row.get("text", ""))
        if n_pii:
            stats["pii_items_seen"] += n_pii
            stats["records_with_pii"] += 1

        # A CLOSED row means the complaint was already resolved by the time the register
        # exported it. The register carries no resolution timestamp, so the export is the
        # tightest honest bound we have. Flagged to the team -- see the patch summary.
        end = None
        if (row.get("status") or "").strip().upper() == "CLOSED":
            stats["closed_rows"] += 1
            end = received_at_for(FEED, start)

        out.append(build_event(
            source=FEED, category=category, raw_ref=ref_complaint(row["complaint_id"]),
            start=start, lat=lat, lon=lon, h3_cell=cell,
            measure=1.0,                      # provisional; cluster pass rescales
            end=end,
            confidence=confidence_for(CONFIDENCE["resident_complaint"], resolution),
            resolution=resolution))
        stats["candidates"] += 1

    stats["last_raw_received_at"] = iso(last_raw) if last_raw else None
    return out, stats
