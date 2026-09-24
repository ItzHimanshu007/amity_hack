"""Phase 5: groups anomalies across categories by space + time into situations.
Computes lift. See CONTRACT.md F.

Pipeline (orchestrated by run()):
  1. load Phase 4's anomalies.jsonl + canonical events (for evidence detail only --
     see the module note below on the one deliberate exception to that rule).
  2. COLLAPSE overlapping/adjacent anomaly windows for the same (h3_cell, category)
     into "episodes" -- one evidence node per real burst, not one per 5-minute step.
  3. build the lift table from the 14-day history (engine.lift).
  4. score every plausible, spatially-close, temporally-fitting pair of episodes as a
     candidate LINK (engine.plausibility decides "plausible", engine.lift feeds the
     score).
  5. cluster accepted links into connected components -> Situations (engine.situations
     builds the object). A component of size 1 becomes a situation only if it clears
     the standalone-significant bar (CONTRACT.md §F.1 / contract_constants).
  6. every spatiotemporally-close pair that did NOT end up in the same component is
     recorded as a REJECTED CANDIDATE with a reason -- Phase 8's "probably unrelated".

is_decoy vs rejected candidates -- read before changing either. CONTRACT.md §F defines
`is_decoy` as a flag on a Situation the engine still built but suspects is coincidence.
This linker never sets it true: everything it turns into a Situation, it turned into a
Situation *because* the plausibility table and the space/time/lift score said the link
was credible. Anything the score did not support becomes a rejected candidate instead
and is never wrapped in a Situation at all. So `is_decoy` ships wired (situations.py
still applies the §F yellow-cap rule if a future version of this file ever sets it) but
always false from this code -- the honest reason is in the docstring, not a TODO.

ON READING CANONICAL EVENTS. The Phase 5 hard boundary says: "Do not re-derive
anomalies or read raw/canonical events directly except to pull evidence details...
already referenced by an anomaly's contributing_event_ids." Step 2 of the brief (LIFT)
explicitly requires computing co-occurrence over the full 14-day history, which is not
literally "referenced by a contributing_event_ids list" -- it is Phase 4's own history
half of events.jsonl, the same file and the same history/detection split
engine.baseline already uses to learn the Poisson lambda. This module reads that file
once, splits it with engine.baseline.split_history_detection (not reimplemented), and:
  - the DETECTION half is only ever looked up BY id, for ids named in some anomaly's
    contributing_event_ids -- the letter of the boundary, for the half that matters
    (no anomaly-equivalent re-derivation happens against it);
  - the HISTORY half is read in full, because CONTRACT.md's own Phase 5 brief has no
    other way to compute lift, and it is canonical (Phase 3 output), never a raw feed
    or the answer key. Flagged here and in the final report rather than silently
    stretched to fit.
"""

import json
from pathlib import Path

from contract_constants import (ANOMALY_WINDOW_SEC, LINK_MAX_GRID_DISTANCE,
                                LINK_MIN_LIFT, LINK_W_LIFT, LINK_W_SPACE, LINK_W_TIME,
                                NEARBY_MAX_GRID_DISTANCE,
                                REJECTED_CANDIDATE_MAX_GAP_SEC,
                                STANDALONE_MIN_SEVERITY_WEIGHTED,
                                ANOMALY_RARE_ELIGIBLE_CATEGORIES)
from engine import health as engine_health
from engine import situations as sit
from engine.baseline import split_history_detection
from engine.config import DATA_DIR, DETECTION_LOOKBACK_HOURS
from engine.lift import build_lift_table, lift_for
from engine.plausibility import is_root_capable, window_for
from ingest import run as ingest_run
from ingest.normalize import from_iso_z, iso
from ingest.zones import grid_distance


# --------------------------------------------------------------------- episodes ---

def _load_jsonl(path: Path) -> list:
    out = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def collapse_episodes(anomalies: list, events_by_id: dict) -> list:
    """Merges Phase 4's overlapping/adjacent sliding-window anomalies for the same
    (h3_cell, category) into one evidence node per real burst.

    Phase 4 slides a 60-min window every 5 min (engine/anomaly.py STEP_SEC), so one
    sustained cluster is re-flagged in up to 12 consecutive windows sharing most of
    their contributing events. Linking on the raw anomaly records would score that one
    burst as up to 12 redundant candidate nodes against every neighbour -- merging
    first is what makes "one link per real thing" true.

    `severity_weighted` is RECOMPUTED from the union of contributing events rather
    than summed across merged anomaly records, because overlapping windows share most
    of their events and naively summing would count each shared event several times.
    """
    by_key = {}
    for a in anomalies:
        by_key.setdefault((a["h3_cell"], a["category"]), []).append(a)

    episodes = []
    for (cell, category), members in sorted(by_key.items()):
        members = sorted(members, key=lambda a: (a["window_start_utc"], a["anomaly_id"]))
        run = []
        for a in members:
            if run and a["window_start_utc"] > run[-1]["window_end_utc"]:
                episodes.append(_build_episode(cell, category, run, events_by_id))
                run = []
            run.append(a)
        if run:
            episodes.append(_build_episode(cell, category, run, events_by_id))
    return episodes


def _build_episode(cell: str, category: str, members: list, events_by_id: dict) -> dict:
    """The RAW episode: everything Phase 4 flagged in this (cell, category) burst,
    unpruned. `event_times` is what candidate_links() scans for the best-fitting
    event PAIR against another episode -- see that function's docstring for why a
    single fixed "representative" timestamp per episode is not good enough. `rep_t`
    (the episode's own earliest event) is kept only as the fallback anchor for a
    chain ROOT or a standalone situation, which have no incoming edge to anchor on
    instead -- see resolve_cluster() and its docstring.
    """
    contributing = sorted({eid for m in members for eid in m["contributing_event_ids"]})
    member_events = [events_by_id[eid] for eid in contributing if eid in events_by_id]
    severity_weighted = round(sum(e["severity"] for e in member_events), 4)
    rep = min(member_events, key=lambda e: (e["start_utc"], e["event_id"]))
    event_times = sorted(
        (from_iso_z(e["start_utc"]).timestamp(), e["event_id"]) for e in member_events)
    return {
        "h3_cell": cell,
        "category": category,
        "trigger": "rare" if any(m["trigger"] == "rare" for m in members) else "volume",
        "window_start_utc": min(m["window_start_utc"] for m in members),
        "window_end_utc": max(m["window_end_utc"] for m in members),
        "observed_count": len(contributing),
        "min_p_value": min(m["p_value"] for m in members),
        "severity_weighted": severity_weighted,
        "contributing_event_ids": contributing,
        "event_times": event_times,
        "source_feeds": sorted({s for m in members for s in m["source_feeds"]}),
        "degraded_by_stale_feeds": sorted({f for m in members for f in m["degraded_by_stale_feeds"]}),
        "anomaly_ids": sorted({m["anomaly_id"] for m in members}),
        "rep_t": from_iso_z(rep["start_utc"]).timestamp(),
        "rep_event_id": rep["event_id"],
    }


# ---------------------------------------------------------------- candidate links --

def score_link(distance: int, gap_sec: float, window: tuple, lift_value: float) -> float:
    """LINK_W_SPACE/TIME/LIFT-weighted closeness + time-fit + above-chance score, all
    in [0, 1]. See contract_constants for why LIFT never gates acceptance on its own."""
    space_score = 1.0 if distance == 0 else 0.6
    min_gap, max_gap = window
    span = max(1.0, max_gap - min_gap)
    time_score = max(0.0, 1.0 - (gap_sec - min_gap) / span)
    lift_score = min(1.0, lift_value / 1.5) if lift_value > 0 else 0.0
    return round(LINK_W_SPACE * space_score + LINK_W_TIME * time_score
                + LINK_W_LIFT * lift_score, 4)


def _best_event_pair(a_times: list, b_times: list, min_gap: float, max_gap: float):
    """Scans every (event in A) x (event in B) pair and returns the one with the
    SMALLEST valid gap inside [min_gap, max_gap], or None.

    Why per-event, not per-episode. An episode can merge several bursts that Phase 4's
    overlapping sliding windows happened to bridge (engine/verify_linker.py check
    documents a real case: a real signal-then-bus-delay pair 22 minutes apart, bridged
    in the SAME episode with an unrelated pre-storm blip 93 minutes apart). Comparing
    a single fixed "representative" timestamp per episode picks whichever one happens
    to be earliest -- which can be the irrelevant blip, silently failing a link that
    should succeed, or (worse) succeeding on the wrong pair of events. Scanning every
    combination and keeping the closest valid one finds the pair that actually
    supports the link, the same way engine.lift already checks co-occurrence
    event-by-event rather than episode-by-episode.

    Smallest gap, not first found: the closest-in-time pair is the strongest single
    piece of evidence for this specific link (CONTRACT.md's own chain text speaks in
    "N minutes later" -- closer is a tighter, more legible claim), and it is also the
    correct anchor for that episode's chain-step timestamp (see resolve_cluster).
    """
    best = None
    for ta, ea in a_times:
        for tb, eb in b_times:
            gap = tb - ta
            if min_gap <= gap <= max_gap and (best is None or gap < best[0]):
                best = (gap, ea, eb)
    return best


def candidate_links(episodes: list, lift_table: dict) -> list:
    """Every directed (cause, effect) pair across all episodes that CONTRACT.md §F.1's
    plausibility table allows, that sit within LINK_MAX_GRID_DISTANCE, and whose
    CLOSEST valid event pair fits the pair's window. One entry per accepted directed
    edge, carrying the specific (cause_event_id, effect_event_id) that supports it."""
    edges = []
    for i, a in enumerate(episodes):
        for j, b in enumerate(episodes):
            if i == j:
                continue
            window = window_for(a["category"], b["category"])
            if window is None:
                continue
            distance = grid_distance(a["h3_cell"], b["h3_cell"])
            if distance > LINK_MAX_GRID_DISTANCE:
                continue
            min_gap, max_gap = window
            match = _best_event_pair(a["event_times"], b["event_times"], min_gap, max_gap)
            if match is None:
                continue
            gap, cause_event_id, effect_event_id = match
            row = lift_for(lift_table, a["category"], b["category"])
            if row["value"] < LINK_MIN_LIFT:
                continue
            score = score_link(distance, gap, window, row["value"])
            edges.append({"i": i, "j": j, "pair": (a["category"], b["category"]),
                          "distance": distance, "gap_sec": gap, "score": score,
                          "lift_value": row["value"], "cause_event_id": cause_event_id,
                          "effect_event_id": effect_event_id})
    edges.sort(key=lambda e: (-e["score"], e["i"], e["j"]))
    return edges


# ---------------------------------------------------------------------- clustering -

class _UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def cluster_episodes(episodes: list, edges: list):
    """Connected components over accepted links. Returns (components, edges_by_pair)
    where `components` is a list of index-lists, sorted for determinism, and
    `edges_by_pair` maps a frozenset{i, j} -> the highest-scoring edge between them
    (there can be at most one plausible direction per unordered pair in this table --
    see engine.plausibility's module docstring on why it has no reciprocal entries)."""
    uf = _UnionFind(len(episodes))
    edges_by_pair = {}
    for e in edges:
        key = frozenset((e["i"], e["j"]))
        if key not in edges_by_pair or e["score"] > edges_by_pair[key]["score"]:
            edges_by_pair[key] = e
        uf.union(e["i"], e["j"])

    groups = {}
    for idx in range(len(episodes)):
        groups.setdefault(uf.find(idx), []).append(idx)
    components = sorted(
        (sorted(members) for members in groups.values()),
        key=lambda members: episodes[members[0]]["rep_t"])
    return components, edges_by_pair


def resolve_cluster(members: list, episodes: list, edges: list, events_by_id: dict) -> list:
    """Turns a raw connected component into the episode list build_situation() should
    actually use: one ANCHOR event per episode, and its evidence pruned to what is
    plausibly the same burst as that anchor.

    The anchor for an episode with an incoming edge inside this cluster is the
    `effect_event_id` of that edge's CLOSEST-gap instance (ties broken by edge score) --
    the specific event that actually justified this episode joining the cascade. An
    episode with no incoming edge in this cluster (a chain ROOT, or every member of a
    standalone situation) anchors on its own earliest event, for lack of anything
    better to point at.

    Pruning: an episode's raw contributing_event_ids can include Phase 4 anomaly-
    window noise that has nothing to do with the anchor (see _best_event_pair's
    docstring for the concrete case this was built for). Anything more than
    ANOMALY_WINDOW_SEC (CONTRACT.md §E.1's own 60-minute window -- reused rather than
    inventing a new constant) from the anchor is dropped from THIS situation's
    membership, and severity_weighted/source_feeds are recomputed from what remains.
    `degraded_by_stale_feeds` is deliberately NOT recomputed -- it names feeds, not
    events, and engine/health.py's own snapshot is already uniform across the whole
    run rather than windowed, so pruning events changes nothing it would say. The
    dropped events are not lost data -- they simply were not evidence for this
    particular link, and Phase 4's anomaly record for them still exists on disk for
    anyone who wants to trace it by hand.
    """
    member_set = frozenset(members)
    incoming = {}
    for e in edges:
        if e["i"] in member_set and e["j"] in member_set:
            cur = incoming.get(e["j"])
            if cur is None or e["gap_sec"] < cur["gap_sec"]:
                incoming[e["j"]] = e

    resolved = []
    for idx in members:
        raw = episodes[idx]
        edge = incoming.get(idx)
        anchor_id = edge["effect_event_id"] if edge is not None else raw["rep_event_id"]
        anchor_t = from_iso_z(events_by_id[anchor_id]["start_utc"]).timestamp()

        pruned_ids = sorted(eid for t, eid in raw["event_times"]
                            if abs(t - anchor_t) <= ANOMALY_WINDOW_SEC)
        if anchor_id not in pruned_ids:
            pruned_ids.append(anchor_id)   # the anchor always belongs to its own episode
        pruned_events = [events_by_id[eid] for eid in pruned_ids]

        resolved.append({
            **raw,
            "contributing_event_ids": sorted(pruned_ids),
            "severity_weighted": round(sum(e["severity"] for e in pruned_events), 4),
            "source_feeds": sorted({e["source"] for e in pruned_events}),
            "anchor_event_id": anchor_id,
        })
    return resolved


def standalone_eligible(ep: dict) -> bool:
    """CONTRACT.md §F.1 single-anomaly-situation policy: a Situation may consist of
    one anomaly with no link partner when it is a discrete, consequential, root-
    capable event. All three gates are load-bearing (see contract_constants):
      - root-capable: it must be able to START a chain, or a lone occurrence carries
        no more claim to being "a situation" than any other single report;
      - discrete-incident category: excludes weather.heat/air.pm25, the same sustained
        sensor conditions Phase 4 excludes from its own rare trigger, for the same
        reason -- a single crossing of a city-wide condition is not a localized event;
      - severity_weighted clears STANDALONE_MIN_SEVERITY_WEIGHTED.
    """
    return (is_root_capable(ep["category"])
            and ep["category"] in ANOMALY_RARE_ELIGIBLE_CATEGORIES
            and ep["severity_weighted"] >= STANDALONE_MIN_SEVERITY_WEIGHTED)


# ------------------------------------------------------------------ rejected pairs -

def _plausibility_reason(cat_a: str, cat_b: str, gap_sec: float, distance: int):
    """Why a spatiotemporally-close pair did NOT link. Checks both directions since
    the caller does not know which one (if either) the table would allow."""
    for cause, effect in ((cat_a, cat_b), (cat_b, cat_a)):
        window = window_for(cause, effect)
        if window is None:
            continue
        min_gap, max_gap = window
        if distance > LINK_MAX_GRID_DISTANCE:
            return (f"{cause} and {effect} fit the expected time gap but the areas are "
                    f"{distance} apart, not adjacent",
                    f"{cause} और {effect} का समय अंतर ठीक है लेकिन क्षेत्र सटे हुए नहीं हैं ({distance} दूर)")
        g = gap_sec if cause == cat_a else -gap_sec
        if g < min_gap:
            return (f"{effect} showed up before {cause} would plausibly cause it",
                    f"{effect}, {cause} के असर में आने से पहले ही दिख गया")
        return (f"{cause} and {effect} are more than {round(max_gap / 60)} minutes apart, "
                f"outside the usual window",
                f"{cause} और {effect} के बीच {round(max_gap / 60)} मिनट से ज़्यादा का अंतर है")
    return (f"{cat_a} and {cat_b} overlapped in time and space but this category pair "
            f"is not in the plausibility table",
            f"{cat_a} और {cat_b} समय और स्थान में साथ थे लेकिन यह जोड़ी प्रशंसनीयता तालिका में नहीं है")


def _closest_pair_gap(a_times: list, b_times: list):
    """Smallest |gap| between any event in A and any event in B, signed b-a. Unlike
    _best_event_pair, no window constraint -- this is for REPORTING a near-miss's
    actual closest approach, not for accepting a link."""
    best = None
    for ta, _ in a_times:
        for tb, _ in b_times:
            gap = tb - ta
            if best is None or abs(gap) < abs(best):
                best = gap
    return best


def rejected_candidates(episodes: list, components: list) -> list:
    """CONTRACT.md §F.1 (new): every near-miss pair that did NOT end up in the same
    final component, close enough in space and time to be worth explaining. This is
    the system's OWN judgment on unlinked pairs -- not ground-truth is_decoy, which
    this code never sees (see module docstring)."""
    component_of = {}
    for c_idx, members in enumerate(components):
        for m in members:
            component_of[m] = c_idx

    seen = set()
    out = []
    for i, a in enumerate(episodes):
        for j, b in enumerate(episodes):
            if j <= i:
                continue
            if component_of[i] == component_of[j]:
                continue
            distance = grid_distance(a["h3_cell"], b["h3_cell"])
            gap = _closest_pair_gap(a["event_times"], b["event_times"])
            if distance > NEARBY_MAX_GRID_DISTANCE or abs(gap) > REJECTED_CANDIDATE_MAX_GAP_SEC:
                continue
            key = frozenset((a["anomaly_ids"][0], b["anomaly_ids"][0]))
            if key in seen:
                continue
            seen.add(key)
            reason_en, reason_hi = _plausibility_reason(
                a["category"], b["category"], gap, distance)
            out.append(sit.build_rejected_candidate(
                a, b, reason_en, reason_hi, int(round(abs(gap))), distance))
    out.sort(key=lambda r: r["rejected_id"])
    return out


# ------------------------------------------------------------------------- run() --

def _created_utc_for(episodes: list) -> str:
    """The earliest moment ALL of this situation's evidence could exist: the latest
    window_end_utc among its member episodes (an anomaly's window cannot be flagged
    before its window closes). A documented proxy for "detected_at", since Phase 4's
    schema does not carry one -- see the module docstring and the final report."""
    return max(ep["window_end_utc"] for ep in episodes)


def build_situations_and_rejections(episodes: list, lift_table: dict, events_by_id: dict,
                                    degraded_feeds: set, feed_ages: dict):
    """The core algorithm once episodes and the lift table are ready: score candidate
    links, cluster, build Situations for every component that qualifies (linked, or a
    standalone that clears the bar), and record every close-but-unlinked pair as a
    rejected candidate. No file I/O -- shared by run() and engine/verify_linker.py."""
    edges = candidate_links(episodes, lift_table)
    components, edges_by_pair = cluster_episodes(episodes, edges)

    situations = []
    for members in components:
        standalone = len(members) == 1
        if standalone and not standalone_eligible(episodes[members[0]]):
            continue    # not enough on its own; not a link partner either -- dropped
        cluster = resolve_cluster(members, episodes, edges, events_by_id)
        used_pair = None
        if len(members) > 1:
            best = max(
                (e for k, e in edges_by_pair.items() if k <= frozenset(members)),
                key=lambda e: e["score"], default=None)
            if best is not None:
                used_pair = best["pair"]
        situations.append(sit.build_situation(
            cluster, events_by_id, lift_table, used_pair, degraded_feeds,
            feed_ages, _created_utc_for(cluster), standalone))

    rejected = rejected_candidates(episodes, components)
    situations.sort(key=lambda s: s["situation_id"])
    return situations, rejected, edges, components


def run(data_dir: Path = None, verbose: bool = True):
    data_dir = Path(data_dir) if data_dir else DATA_DIR
    anomalies = _load_jsonl(data_dir / "anomalies.jsonl")
    events = _load_jsonl(data_dir / "events.jsonl")
    events_by_id = {e["event_id"]: e for e in events}
    if verbose:
        print(f"  loaded {len(anomalies)} anomalies, {len(events)} canonical events")

    history, detection, detection_start = split_history_detection(
        events, DETECTION_LOOKBACK_HOURS)
    if verbose:
        print(f"  history for lift: {len(history)} events before {detection_start}")

    lift_table = build_lift_table(history.to_dict("records"), from_iso_z)
    if verbose:
        print(f"  lift table: {len(lift_table)} plausible pairs measured")

    # Feed-health snapshot for confidence degradation wording (step 6/10 of the
    # brief). Same re-run-ingest approach engine/run.py and engine/health.py use for
    # anomaly detection -- see engine/health.py's docstring for why canonical events
    # alone cannot answer this.
    now_iso = iso(detection["start_ts"].max().to_pydatetime()) if len(detection) else None
    ingest_result = ingest_run.run(data_dir, verbose=False, now_iso=now_iso)
    degraded_feeds = engine_health.degraded_feeds_snapshot(ingest_result["health_rows"])
    feed_ages = {r["feed"]: r["age_sec"] for r in ingest_result["health_rows"]
                if r["age_sec"] is not None}

    episodes = collapse_episodes(anomalies, events_by_id)
    situations, rejected, edges, components = build_situations_and_rejections(
        episodes, lift_table, events_by_id, degraded_feeds, feed_ages)
    if verbose:
        print(f"  {len(episodes)} episodes -> {len(situations)} situations, "
              f"{len(rejected)} rejected candidates")

    sit_path = data_dir / "situations.jsonl"
    with sit_path.open("w", encoding="utf-8") as fh:
        for s in situations:
            out = {k: v for k, v in s.items() if not k.startswith("_")}
            fh.write(json.dumps(out, ensure_ascii=False) + "\n")

    rej_path = data_dir / "rejected_candidates.jsonl"
    with rej_path.open("w", encoding="utf-8") as fh:
        for r in rejected:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    return {
        "anomalies": anomalies, "events_by_id": events_by_id, "episodes": episodes,
        "edges": edges, "components": components, "lift_table": lift_table,
        "situations": situations, "rejected": rejected,
        "degraded_feeds": degraded_feeds, "feed_ages": feed_ages,
        "sit_path": sit_path, "rej_path": rej_path,
    }


def main():
    import time
    t0 = time.time()
    print("nagar naadi linker -- grouping anomalies into situations\n")
    result = run()
    dt = time.time() - t0
    print(f"\nwrote {len(result['situations'])} situations to {result['sit_path']}")
    print(f"wrote {len(result['rejected'])} rejected candidates to {result['rej_path']}")
    print(f"in {dt:.2f}s")


if __name__ == "__main__":
    main()
