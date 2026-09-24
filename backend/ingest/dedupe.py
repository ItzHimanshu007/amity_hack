"""Source-scoped deduplication. CONTRACT.md §D, "deduplication rule".

Adapters emit one candidate per contributing raw record, so an episode that spans many
observations arrives as many candidates sharing one raw_ref. This folds them into a
single canonical event.

THE KEY ALWAYS INCLUDES `source`. Two traffic.signal_down events for the same dark
junction -- one from power_discom's feeder trip, one from a resident's complaint -- are
independent corroboration, and collapsing them would destroy the very agreement Phase 5
uses to raise confidence_level to "high". Dedupe within a source, never across them.
"""

from collections import Counter


def key_for(event) -> tuple:
    """(source, raw_ref, category). `source` is first because it is the part that must
    never be dropped from this tuple."""
    return (event["source"], event["raw_ref"], event["category"])


def fold(candidates):
    """Collapse candidates to one event per key. Returns (events, stats).

    Folding rule: earliest start wins, severity/measure take the episode peak, end_utc
    takes the latest non-null close, received_at takes the latest sighting.
    """
    merged = {}
    order = []
    removed = Counter()

    for ev in candidates:
        k = key_for(ev)
        prior = merged.get(k)
        if prior is None:
            merged[k] = dict(ev)
            order.append(k)
            continue

        removed[ev["source"]] += 1

        if ev["start_utc"] < prior["start_utc"]:
            prior["start_utc"] = ev["start_utc"]
        if ev["measure"] > prior["measure"]:
            prior["measure"] = ev["measure"]
            prior["severity"] = ev["severity"]
        if ev["end_utc"] and (not prior["end_utc"] or ev["end_utc"] > prior["end_utc"]):
            prior["end_utc"] = ev["end_utc"]
        if ev["received_at"] > prior["received_at"]:
            prior["received_at"] = ev["received_at"]
        # confidence: keep the most pessimistic reading of the same thing
        prior["confidence"] = min(prior["confidence"], ev["confidence"])

    events = [merged[k] for k in order]
    for ev in events:
        ev["freshness_sec"] = max(0, _delta(ev["received_at"], ev["start_utc"]))
    return events, {"removed_per_source": dict(removed), "removed_total": sum(removed.values())}


def _delta(later_iso: str, earlier_iso: str) -> int:
    from ingest.normalize import from_iso_z
    return int((from_iso_z(later_iso) - from_iso_z(earlier_iso)).total_seconds())


def assert_carveout_intact(events) -> dict:
    """CONTRACT.md §D: traffic.signal_down must still exist under both source values.
    Returns the per-source split so the caller can report it."""
    split = Counter(e["source"] for e in events
                    if e["category"] == "traffic.signal_down")
    return dict(split)
