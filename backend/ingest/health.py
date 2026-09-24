"""Per-feed freshness tracking. CONTRACT.md §D intervals, §E feed_health shape.

Produces the rows Phase 6 serves in /state. No UI, no transport -- just the structure.

Field names follow §E exactly (`state`, `last_record_utc`, `age_sec`, `interval_sec`)
rather than inventing parallel ones, because Phase 6 serves these verbatim.

Staleness multiplier is 3x the feed's interval, per CONTRACT.md §E (and now
cross-referenced from §D). Ingest and the API read the same constant so they cannot
disagree about what "stale" means.
"""

from contract_constants import FEEDS
from ingest.normalize import from_iso_z, iso

STALE_AFTER_INTERVALS = 3        # CONTRACT.md §E

STATE_LIVE = "live"
STATE_STALE = "stale"
STATE_KILLED = "killed"
STATE_ERROR = "error"


class FeedHealth:
    """Tracks last-arrival per feed and reports §E rows."""

    def __init__(self):
        self.last_seen = {f: None for f in FEEDS}
        self.totals = {f: 0 for f in FEEDS}
        self.dropped = {f: 0 for f in FEEDS}
        self.killed = set()
        self.errors = {}

    def observe(self, feed: str, received_at_iso: str, dropped: int = 0):
        prior = self.last_seen.get(feed)
        if prior is None or received_at_iso > prior:
            self.last_seen[feed] = received_at_iso
        self.totals[feed] += 1
        self.dropped[feed] += dropped

    def record_counts(self, feed: str, total: int, dropped: int):
        self.totals[feed] = total
        self.dropped[feed] = dropped

    def mark_error(self, feed: str, message: str):
        self.errors[feed] = message

    def rows(self, now_iso: str) -> list:
        now = from_iso_z(now_iso)
        out = []
        for feed, spec in FEEDS.items():
            interval = spec["interval_sec"]
            last = self.last_seen.get(feed)
            age = int((now - from_iso_z(last)).total_seconds()) if last else None

            if feed in self.errors:
                state, message = STATE_ERROR, self.errors[feed]
            elif feed in self.killed:
                state, message = STATE_KILLED, "Stopped by operator"
            elif last is None:
                state, message = STATE_STALE, "No records seen"
            elif age > STALE_AFTER_INTERVALS * interval:
                state = STATE_STALE
                message = f"No update for {max(1, age // 60)} min"
            else:
                state, message = STATE_LIVE, None

            out.append({
                "feed": feed,
                "state": state,
                "last_record_utc": last,
                "age_sec": age,
                "interval_sec": interval,
                "records_total": self.totals.get(feed, 0),
                "records_dropped": self.dropped.get(feed, 0),
                "message": message,
            })
        return out


def from_raw_arrivals(last_raw_at: dict, now_iso: str, drops: dict = None,
                      totals: dict = None) -> list:
    """Build the §E rows from each feed's LAST RAW RECORD arrival.

    This is the correct input for feed health. Canonical events are the wrong signal:
    a feed only produces an event when a reading crosses a severity floor, so a weather
    station faithfully reporting "no rain" every 5 minutes emits nothing for hours and
    would look dead. Health is about whether the SOURCE is transmitting, not whether it
    currently has news. Adapters therefore report `last_raw_received_at` over every raw
    record they read, including ones they drop.
    """
    h = FeedHealth()
    for feed, iso_ts in (last_raw_at or {}).items():
        if iso_ts:
            h.observe(feed, iso_ts)
    for feed, n in (totals or {}).items():
        h.totals[feed] = n
    for feed, n in (drops or {}).items():
        h.dropped[feed] = n
    return h.rows(now_iso)


def from_events(events, now_iso: str, drops: dict = None, totals: dict = None) -> list:
    """DEPRECATED -- kept only so nothing breaks mid-refactor. Use from_raw_arrivals.

    Derives last-seen from canonical events, which systematically reports healthy but
    quiet feeds as `stale` (see from_raw_arrivals). Do not use for anything Phase 6
    serves.
    """
    h = FeedHealth()
    for ev in events:
        h.observe(ev["source"], ev["received_at"])
    for feed, n in (totals or {}).items():
        h.totals[feed] = n
    for feed, n in (drops or {}).items():
        h.dropped[feed] = n
    return h.rows(now_iso)
