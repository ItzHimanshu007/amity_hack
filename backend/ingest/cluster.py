"""Complaint severity, which is a property of the neighbourhood rather than the row.

CONTRACT.md §A scales every complaint.* category by "count of open complaints in the
cell, 30 min window" -- so a single complaint is near the floor and a cluster of a dozen
in one area is at the ceiling. That needs every complaint in hand, so it runs as a pass
after the adapters rather than inside one.

Interpretation, flagged to the team: the count is same-cell AND same-category, over a
trailing 30-minute window ending at the event. Counting unlike categories together would
make one noisy area inflate every unrelated complaint in it.
"""

from collections import defaultdict

from contract_constants import scale_severity
from ingest.normalize import from_iso_z

WINDOW_SEC = 1800        # CONTRACT.md §A: "30 min window"


def apply_complaint_clusters(events) -> dict:
    """Rescales complaint.* severity in place. Returns a small report."""
    groups = defaultdict(list)
    for ev in events:
        if ev["category"].startswith("complaint."):
            groups[(ev["h3_cell"], ev["category"])].append(ev)

    rescaled, peak = 0, 0
    for group in groups.values():
        group.sort(key=lambda e: e["start_utc"])
        times = [from_iso_z(e["start_utc"]) for e in group]
        left = 0
        for i, ev in enumerate(group):
            while (times[i] - times[left]).total_seconds() > WINDOW_SEC:
                left += 1
            count = i - left + 1
            ev["measure"] = float(count)
            ev["severity"] = round(scale_severity(ev["category"], count), 4)
            rescaled += 1
            peak = max(peak, count)

    return {"rescaled": rescaled, "groups": len(groups), "peak_cluster": peak}
