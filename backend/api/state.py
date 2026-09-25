"""In-process snapshot of active events, situations and feed health that /state and /stream read from.

Loads all Phase 3/4/5 precomputed outputs at startup, indexed by timestamp.
As the clock advances, items whose timestamp falls behind sim_time are "revealed".
Handles bookmark jumps by recomputing the full revealed set as-of any timestamp.

**Phase 6b — progressive reveal**
A situation is no longer a batch object revealed whole at max(window_end_utc). Instead:
  - It first appears (action: "created") when at least 2 member events have cleared the clock.
  - Each time another member's window_end_utc passes, the situation is pushed again
    (action: "updated") with the enlarged member list, re-evaluated confidence, and a
    freshly computed predicted_next from the plausibility table.
  - The final "updated" (all members known) is identical to what Phase 5 computed, so
    the linker output is never modified.
  - This is a serve-time reveal-timing change, same category as kill_feed's confidence
    penalty. The linker is never re-run.

CONTRACT.md §E: /state returns active events (end_utc is null or within the last 30 min
of simulated time). City and per-cell pulse_score/alert_level are server-computed.
"""

import csv
import json
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from copy import deepcopy

from contract_constants import (
    FEEDS, ACTIVE_WINDOW_SEC, PULSE_THRESHOLDS,
    alert_level_for, CONFIDENCE_ORDER,
    CHAOS_CONFIDENCE_PENALTY, CHAOS_PULSE_PENALTY,
)
from ingest.health import FeedHealth, STATE_KILLED, STATE_LIVE
from engine.plausibility import successors, reason_for
from engine.situations import compute_pulse

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def _parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class TimelineStore:
    """Precomputed data indexed by reveal-time, with as-of queries for any sim_time."""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or DATA_DIR
        self.events: list = []         # sorted by start_utc
        self.anomalies: list = []      # sorted by window_end_utc
        self.situations: list = []     # sorted by created_utc (Phase 5 final state)
        self.rejected: list = []
        self.ground_truth: dict = {}
        self.events_by_id: dict = {}

        # Phase 6b: milestone schedule for each situation.
        # sit_milestones[situation_id] = list of (window_end_iso, event_id, category)
        # sorted chronologically. The 2nd milestone crossing triggers "created";
        # subsequent crossings trigger "updated".
        self.sit_milestones: dict = {}   # situation_id -> sorted milestone list
        self.sit_reveal_utc: dict = {}   # situation_id -> iso of 2nd milestone (first appear)

        # Raw feed data for GET /raw/{feed}
        self.raw_feeds: dict = {}      # feed_id -> list of raw records

        # Feed arrival times for health (extracted from raw feeds)
        self._feed_arrivals: dict = {}  # feed_id -> list of (sim_time, record)
        self._feed_totals: dict = {}
        self._feed_dropped: dict = {}

        # Chaos-control state
        self.killed_feeds: set = set()
        self.delayed_feeds: dict = {}  # feed_id -> delay_seconds
        self.confidence_overrides: dict = {}  # situation_id -> {original, reason}

    def load(self):
        """Load all precomputed data once at startup."""
        self._load_events()
        self._load_anomalies()
        self._load_situations()
        self._load_rejected()
        self._load_ground_truth()
        self._load_raw_feeds()

    def _load_jsonl(self, filename: str) -> list:
        path = self.data_dir / filename
        if not path.exists():
            return []
        items = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    items.append(json.loads(line))
        return items

    def _load_events(self):
        self.events = self._load_jsonl("events.jsonl")
        self.events.sort(key=lambda e: e["start_utc"])
        self.events_by_id = {e["event_id"]: e for e in self.events}

    def _load_anomalies(self):
        self.anomalies = self._load_jsonl("anomalies.jsonl")
        self.anomalies.sort(key=lambda a: a["window_end_utc"])

    def _load_situations(self):
        self.situations = self._load_jsonl("situations.jsonl")
        self.situations.sort(key=lambda s: s["created_utc"])
        self._build_milestones()

    def _build_milestones(self):
        """Phase 6b: for each situation, build a chronological schedule of when
        each member event clears the clock (using their window_end_utc from anomalies,
        or start_utc from events as fallback).

        The 2nd entry in the schedule is the "created" reveal point; each subsequent
        entry is an "updated" reveal point.
        """
        # Build anomaly lookup: event_id -> window_end_utc
        # Anomalies contribute member events; their window_end_utc is when the
        # evidence for that member window is fully in.
        event_window_end: dict = {}  # event_id -> iso string
        for anomaly in self.anomalies:
            wend = anomaly.get("window_end_utc", "")
            for eid in anomaly.get("contributing_event_ids", []):
                # Take the latest window_end for any event that appears in multiple
                # anomaly windows (conservative — fully confirmed when latest clears)
                if eid not in event_window_end or wend > event_window_end[eid]:
                    event_window_end[eid] = wend

        self.sit_milestones.clear()
        self.sit_reveal_utc.clear()

        for sit in self.situations:
            sid = sit["situation_id"]
            milestones = []
            for eid in sit.get("member_event_ids", []):
                ev = self.events_by_id.get(eid)
                if not ev:
                    continue
                # Use anomaly window_end if known (most precise); fallback start_utc
                ts = event_window_end.get(eid) or ev.get("start_utc", "")
                cat = ev.get("category", "")
                milestones.append((ts, eid, cat))

            # Sort chronologically; break ties by event_id for determinism
            milestones.sort(key=lambda x: (x[0], x[1]))
            self.sit_milestones[sid] = milestones

            # Reveal point: 2nd milestone (index 1). If < 2 members, use Phase 5's
            # created_utc as the reveal point (no partial story to tell).
            if len(milestones) >= 2:
                self.sit_reveal_utc[sid] = milestones[1][0]
            else:
                self.sit_reveal_utc[sid] = sit["created_utc"]

    def _load_rejected(self):
        self.rejected = self._load_jsonl("rejected_candidates.jsonl")
        # A rejected link can only be shown once all the anomaly windows it
        # considered have closed: reveal at the latest of their window_end_utc.
        wend = {a["anomaly_id"]: a["window_end_utc"] for a in self.anomalies}
        for r in self.rejected:
            ends = [wend[aid] for aid in r.get("anomaly_ids", []) if aid in wend]
            r["_reveal_utc"] = max(ends) if ends else None

    def _load_ground_truth(self):
        path = self.data_dir / "ground_truth.json"
        if path.exists():
            self.ground_truth = json.loads(path.read_text("utf-8"))

    def _load_raw_feeds(self):
        """Load raw feed files for GET /raw/{feed}."""
        for feed_id, spec in FEEDS.items():
            filename = spec["file"]
            path = self.data_dir / filename
            if not path.exists():
                self.raw_feeds[feed_id] = []
                continue

            if spec["format"] == "csv":
                self._load_csv_feed(feed_id, path)
            else:
                self._load_jsonl_feed(feed_id, path)

    def _load_csv_feed(self, feed_id: str, path: Path):
        records = []
        with open(path, "r", encoding="utf-8") as f:
            header = f.readline().strip()
            for line in f:
                line = line.strip()
                if not line:
                    continue
                # Parse for complaint_id
                reader = csv.reader(io.StringIO(line))
                row = next(reader, None)
                if row:
                    records.append({
                        "raw_ref": f"civic_complaints:{row[0]}",
                        "raw": line,
                        "header": header,
                        "parsed_ok": True,
                        "reason": None,
                    })
        self.raw_feeds[feed_id] = records

    def _load_jsonl_feed(self, feed_id: str, path: Path):
        records = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    records.append({
                        "raw": obj,
                        "parsed_ok": True,
                        "reason": None,
                    })
                except json.JSONDecodeError:
                    records.append({
                        "raw": line,
                        "parsed_ok": False,
                        "reason": "Invalid JSON",
                    })
        self.raw_feeds[feed_id] = records

    # ------------------------------------------------------------------ as-of queries

    def revealed_events(self, sim_time: datetime) -> list:
        """Events whose start_utc <= sim_time."""
        cutoff = _iso(sim_time)
        return [e for e in self.events if e["start_utc"] <= cutoff]

    def active_events(self, sim_time: datetime) -> list:
        """Active events: end_utc is null or within the last ACTIVE_WINDOW_SEC."""
        cutoff_iso = _iso(sim_time)
        active_cutoff = _iso(sim_time - timedelta(seconds=ACTIVE_WINDOW_SEC))
        result = []
        for e in self.events:
            if e["start_utc"] > cutoff_iso:
                break
            end = e.get("end_utc")
            if end is None or end >= active_cutoff:
                result.append(e)
        return result

    def revealed_situations(self, sim_time: datetime) -> list:
        """Phase 6b: situations whose 2nd-member milestone (reveal_utc) <= sim_time.

        Each returned situation is the PARTIAL view — only the members that have cleared
        the clock. The final full view is identical to Phase 5's output once all members
        have arrived. Nothing about Phase 5's linker is changed.
        """
        cutoff = _iso(sim_time)
        result = []
        for s in self.situations:
            sid = s["situation_id"]
            reveal = self.sit_reveal_utc.get(sid, s["created_utc"])
            if reveal <= cutoff:
                result.append(self.partial_situation_as_of(s, sim_time))
        return result

    def partial_situation_as_of(self, sit: dict, sim_time: datetime) -> dict:
        """Return a serve-time partial view of a situation.

        Members are filtered to those whose milestone timestamp <= sim_time.
        confidence, pulse_score, alert_level, and predicted_next are recomputed
        from the plausibility table using only the known partial membership.
        This is a serve-time rule application, NOT new detection logic.
        """
        cutoff = _iso(sim_time)
        sid = sit["situation_id"]
        milestones = self.sit_milestones.get(sid, [])

        # Which events have cleared the clock?
        visible_eids = [eid for (ts, eid, _cat) in milestones if ts <= cutoff]
        all_eids = set(sit.get("member_event_ids", []))
        visible_set = set(visible_eids) & all_eids

        # If all members are visible, return the Phase 5 final state with normalised
        # predicted_next. Phase 5 stored it as a single dict; we convert to a list so
        # the frontend always sees an array — same shape as the partial-view path.
        if visible_set == all_eids:
            pn = sit.get("predicted_next")
            if isinstance(pn, dict):
                s = deepcopy(sit)
                s["predicted_next"] = [pn] if pn else []
                return s
            return sit

        # Partial view
        s = deepcopy(sit)
        s["member_event_ids"] = [eid for eid in sit["member_event_ids"]
                                  if eid in visible_set]
        s["partial"] = True
        s["partial_of"] = len(all_eids)
        s["members_known"] = len(visible_set)

        # Recompute pulse from partial members
        partial_members = [self.events_by_id[eid] for eid in s["member_event_ids"]
                           if eid in self.events_by_id]
        if partial_members:
            try:
                partial_pulse = compute_pulse(partial_members)
            except Exception:
                partial_pulse = sit["pulse_score"] * len(visible_set) // len(all_eids)
        else:
            partial_pulse = 0

        s["pulse_score"] = partial_pulse
        s["alert_level"] = alert_level_for(partial_pulse)

        # Downgrade confidence to reflect partial evidence
        known_cats = {self.events_by_id[eid]["category"]
                      for eid in s["member_event_ids"]
                      if eid in self.events_by_id}
        n_known = len(known_cats)
        n_total = len({self.events_by_id[eid]["category"]
                       for eid in all_eids if eid in self.events_by_id})
        fraction = n_known / n_total if n_total else 1.0

        orig_conf = sit.get("confidence_level", "med")
        if fraction < 0.5 and orig_conf == "high":
            s["confidence_level"] = "med"
        elif fraction < 0.35:
            s["confidence_level"] = "low"
        # else keep original

        # Recompute predicted_next from partial member categories and plausibility table
        s["predicted_next"] = self._predicted_next(known_cats)

        return s

    def _predicted_next(self, known_categories: set) -> list:
        """Serve-time prediction: what categories might plausibly follow the current
        partial membership? Reads PLAUSIBLE_PAIRS — the same table the linker used —
        and returns the unobserved successors with the highest combined support.

        This is purely serve-time rule application, not detection.
        """
        candidates: dict = {}  # category -> best (why_en, why_hi, from_cat)
        for cat in known_categories:
            for succ in successors(cat):
                if succ in known_categories:
                    continue  # already seen
                why = reason_for(cat, succ)
                if why and succ not in candidates:
                    candidates[succ] = {
                        "category": succ,
                        "plausible_because_en": why[0],
                        "plausible_because_hi": why[1],
                        "trigger_category": cat,
                    }
        # Return top 2 most-interesting (shortest cascade chain first = most certain)
        return list(candidates.values())[:2]

    def active_situations(self, sim_time: datetime) -> list:
        """Active situations (status == active) as partial views."""
        return [s for s in self.revealed_situations(sim_time)
                if s.get("status") == "active"]

    def revealed_rejected(self, sim_time: datetime) -> list:
        """Rejected candidate links whose evidence windows have all closed."""
        cutoff = _iso(sim_time)
        return [{k: v for k, v in r.items() if not k.startswith("_")}
                for r in self.rejected
                if r.get("_reveal_utc") and r["_reveal_utc"] <= cutoff]

    def revealed_anomalies(self, sim_time: datetime) -> list:
        """Anomalies whose window_end_utc <= sim_time."""
        cutoff = _iso(sim_time)
        return [a for a in self.anomalies if a["window_end_utc"] <= cutoff]

    # ------------------------------------------------------------------ feed health

    def compute_feed_health(self, sim_time: datetime) -> list:
        """Compute feed health rows as-of sim_time, reusing ingest.health.FeedHealth."""
        h = FeedHealth()

        # Look at revealed events to find last-seen per feed
        revealed = self.revealed_events(sim_time)
        sim_iso = _iso(sim_time)

        # Group by source and find last received_at
        last_by_feed = {}
        total_by_feed = {f: 0 for f in FEEDS}
        for ev in revealed:
            src = ev["source"]
            total_by_feed[src] = total_by_feed.get(src, 0) + 1
            rat = ev["received_at"]
            if src not in last_by_feed or rat > last_by_feed[src]:
                last_by_feed[src] = rat

        for feed, iso_ts in last_by_feed.items():
            if iso_ts:
                h.observe(feed, iso_ts)
        for feed, n in total_by_feed.items():
            h.totals[feed] = n

        # Apply killed feeds
        for feed in self.killed_feeds:
            h.killed.add(feed)

        return h.rows(sim_iso)

    # ------------------------------------------------------------------ rollups

    def city_rollup(self, sim_time: datetime) -> dict:
        """City-level pulse_score and alert_level from CONTRACT.md §F."""
        active_sits = self.active_situations(sim_time)
        # Exclude decoys from rollup
        non_decoy = [s for s in active_sits if not s.get("is_decoy", False)]

        # Apply confidence overrides
        effective_sits = self._apply_overrides(non_decoy)

        if not effective_sits:
            return {"pulse_score": 0, "alert_level": "green"}

        max_pulse = max(s["pulse_score"] for s in effective_sits)
        return {
            "pulse_score": max_pulse,
            "alert_level": alert_level_for(max_pulse),
        }

    def cell_rollups(self, sim_time: datetime) -> dict:
        """Per-cell rollup. Only non-default cells are included."""
        active_sits = self.active_situations(sim_time)
        non_decoy = [s for s in active_sits if not s.get("is_decoy", False)]
        effective_sits = self._apply_overrides(non_decoy)

        cells = {}
        for s in effective_sits:
            for cell in s.get("zone", {}).get("h3_cells", []):
                if cell not in cells or s["pulse_score"] > cells[cell]["pulse_score"]:
                    cells[cell] = {
                        "h3_cell": cell,
                        "pulse_score": s["pulse_score"],
                        "alert_level": alert_level_for(s["pulse_score"]),
                    }
        return cells

    def killed_feed_override(self, situation: dict) -> Optional[dict]:
        """CONTRACT.md §E kill_feed: any active situation with a member event from a
        stopped feed gets the one-level-down penalty -- including situations revealed
        after the feed was stopped, which an at-kill-time snapshot would miss."""
        if not self.killed_feeds:
            return None
        feeds = sorted({self.events_by_id[eid]["source"]
                        for eid in situation.get("member_event_ids", [])
                        if eid in self.events_by_id} & set(self.killed_feeds))
        if not feeds:
            return None
        conf = situation.get("confidence_level")
        names = " and ".join(feeds)
        return {
            "original_confidence": conf,
            "adjusted_confidence": CHAOS_CONFIDENCE_PENALTY.get(conf, conf),
            "reason_en": f"{names} feed stopped — confidence lowered",
            "reason_hi": f"{names} फीड बंद — भरोसा कम किया गया",
            "penalty_applied": True,
            "killed_feed": feeds[0],
        }

    def _apply_overrides(self, situations: list) -> list:
        """Apply serve-time confidence/pulse overrides from chaos controls."""
        if not self.confidence_overrides and not self.killed_feeds:
            return situations
        result = []
        for s in situations:
            sid = s["situation_id"]
            override = self.confidence_overrides.get(sid) or self.killed_feed_override(s)
            if override:
                s = deepcopy(s)
                s["confidence_level"] = override["adjusted_confidence"]
                s["confidence_reason_en"] = override["reason_en"]
                s["confidence_reason_hi"] = override.get("reason_hi",
                                                          override["reason_en"])
                # Recalculate pulse_score with lowered confidence: one level down
                # is a serve-time penalty, but we don't recompute the formula —
                # just drop pulse_score by a fixed penalty
                if override.get("penalty_applied"):
                    s["pulse_score"] = max(0, s["pulse_score"] - CHAOS_PULSE_PENALTY)
                    s["alert_level"] = alert_level_for(s["pulse_score"])
            result.append(s)
        return result

    # ------------------------------------------------------------------ counts

    def counts(self, sim_time: datetime) -> dict:
        active_evts = self.active_events(sim_time)
        active_sits = self.active_situations(sim_time)
        cells = set()
        for s in active_sits:
            cells.update(s.get("zone", {}).get("h3_cells", []))
        return {
            "events_active": len(active_evts),
            "situations_active": len(active_sits),
            "cells_touched": len(cells),
        }

    # ------------------------------------------------------------------ deltas

    def new_events_since(self, prev_time: datetime, cur_time: datetime) -> list:
        """Events revealed between prev_time (exclusive) and cur_time (inclusive)."""
        prev_iso = _iso(prev_time)
        cur_iso = _iso(cur_time)
        return [e for e in self.events
                if prev_iso < e["start_utc"] <= cur_iso]

    def new_situations_since(self, prev_time: datetime, cur_time: datetime) -> list:
        """Phase 6b: situations whose reveal_utc (2nd milestone) crossed in (prev, cur].
        Returns partial views — the tick loop emits these as 'created' messages.
        """
        prev_iso = _iso(prev_time)
        cur_iso = _iso(cur_time)
        result = []
        for s in self.situations:
            sid = s["situation_id"]
            reveal = self.sit_reveal_utc.get(sid, s["created_utc"])
            if prev_iso < reveal <= cur_iso:
                result.append(self.partial_situation_as_of(s, _parse_utc(cur_iso)))
        return result

    def updated_situations_since(self, prev_time: datetime, cur_time: datetime) -> list:
        """Phase 6b: situations that had a NEW milestone cross in (prev, cur] but whose
        reveal_utc was already before prev_time (so they already exist for the client
        as 'created'). These become 'updated' messages on the WS stream.
        """
        prev_iso = _iso(prev_time)
        cur_iso = _iso(cur_time)
        result = []
        for s in self.situations:
            sid = s["situation_id"]
            reveal = self.sit_reveal_utc.get(sid, s["created_utc"])
            if reveal > prev_iso:
                continue  # Not yet created for the client, or just being created
            milestones = self.sit_milestones.get(sid, [])
            # Did any later milestone cross in this tick window?
            new_member = any(prev_iso < ts <= cur_iso for (ts, _eid, _cat) in milestones)
            if new_member:
                result.append((s, self.partial_situation_as_of(s, _parse_utc(cur_iso))))
        return result
