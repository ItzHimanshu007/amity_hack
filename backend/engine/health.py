"""degraded_by_stale_feeds support. CONTRACT.md §E.1.

IMPORTANT LIMITATION, found while building this: canonical events cannot answer "was
the raw feed still transmitting" for any feed except transit_gtfs. For every other feed,
ingest.normalize.received_at_for() sets received_at = start_utc + interval_sec
DETERMINISTICALLY (verified: every weather_imd canonical event has freshness_sec exactly
300, always) -- so a gap between two canonical events' received_at is just the gap
between their start_utc, shifted by a constant. It carries no information about the raw
feed's own liveness: a weather station reporting faithfully every 5 minutes through 3
calm, rainless hours produces ZERO canonical events in that span (nothing crosses the
severity floor), and a naive gap-based staleness scan over canonical events would call
that span "stale" -- wrongly.

Given the hard boundary for this phase (canonical events only, no raw-feed reads, and
"don't invent new health logic"), the honest answer is NOT to fabricate a windowed
staleness signal from a source that structurally cannot support one. Instead this
consumes Phase 3's own end-of-run health snapshot (`ingest.run.run()`'s `health_rows`,
CONTRACT.md §E shape) exactly as computed, and applies it uniformly to every anomaly
rather than per-window.

This is a real precision loss versus the ideal "was feed X stale specifically during
window [a,b]" -- flagged to the team below and in the final report. Fixing it properly
means Phase 3's adapters tracking every RAW record's arrival, not just the ones that
produced a candidate event, which is Phase 3-adapter-level work, not something to
quietly paper over here.
"""

STALE_STATES = {"stale", "error"}


def degraded_feeds_snapshot(health_rows: list) -> set:
    """Feed ids Phase 3's own health rows report as stale/error, at the single snapshot
    Phase 3 computes (end of the run). CONTRACT.md §E `feed_health[].state`."""
    return {row["feed"] for row in health_rows if row["state"] in STALE_STATES}
