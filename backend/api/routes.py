"""GET /state, /situations/{id}, /raw/{feed}, /scorecard. Shapes are fixed by CONTRACT.md §E.

=== DETECTION BOUNDARY ===
This module serves precomputed data. The ONLY place ground_truth.json is read is inside
the /scorecard handler. It must never feed back into anything that /state or /stream serve
as "detected". If you are adding an import of ground_truth or event_index OUTSIDE
scorecard(), you are crossing the boundary — stop and rethink.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from contract_constants import FEEDS
from ingest.pii import mask_record

logger = logging.getLogger("nagarnaadi.routes")
router = APIRouter()

# These are set by the app lifespan; not imported at module level.
_store = None
_clock = None
_broadcaster = None


def init(store, clock, broadcaster):
    """Called once by the app startup to inject shared state."""
    global _store, _clock, _broadcaster
    _store = store
    _clock = clock
    _broadcaster = broadcaster


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- GET /state

@router.get("/state")
def get_state():
    """CONTRACT.md §E: full current snapshot."""
    if _clock is None or _store is None:
        raise HTTPException(status_code=503, detail="Simulation not started")

    sim_time = _clock.now()

    active_evts = _store.active_events(sim_time)
    active_sits = _store.revealed_situations(sim_time)  # include all revealed
    feed_health = _store.compute_feed_health(sim_time)
    city = _store.city_rollup(sim_time)
    counts = _store.counts(sim_time)

    return {
        "server_time_utc": _iso(datetime.utcnow()),
        "sim": _clock.sim_block(),
        "city": city,
        "counts": counts,
        "events": active_evts,
        "situations": active_sits,
        "feed_health": feed_health,
        # Additive: links the linker considered and rejected, with its reason.
        # Shown as "Probably unrelated" (DESIGN.md §7).
        "rejected_candidates": _store.revealed_rejected(sim_time),
    }


# ---------------------------------------------------------------- GET /situations/{id}

@router.get("/situations/{situation_id}")
def get_situation(situation_id: str):
    """CONTRACT.md §E: full situation with member events expanded."""
    if _store is None:
        raise HTTPException(status_code=503, detail="Simulation not started")

    sim_time = _clock.now()
    revealed = _store.revealed_situations(sim_time)
    sit = None
    for s in revealed:
        if s["situation_id"] == situation_id:
            sit = s
            break

    if sit is None:
        raise HTTPException(
            status_code=404,
            detail=json.dumps({"error": "not_found",
                               "detail": f"No situation {situation_id}"}))

    # Expand member events, sorted by start_utc ascending
    member_events = []
    for eid in sit.get("member_event_ids", []):
        ev = _store.events_by_id.get(eid)
        if ev:
            member_events.append(ev)
    member_events.sort(key=lambda e: e["start_utc"])

    result = dict(sit)
    result["member_events"] = member_events

    # Add "possible link, not confirmed" framing field for the frontend to render
    result["framing_text"] = (
        "This situation links events that appear connected based on timing, location, "
        "and known cause-effect relationships. It is a possible link, not a confirmed one."
    )
    result["framing_text_hi"] = (
        "यह स्थिति उन घटनाओं को जोड़ती है जो समय, स्थान और ज्ञात कारण-प्रभाव संबंधों के "
        "आधार पर जुड़ी हुई दिखती हैं। यह एक संभावित कड़ी है, पुष्ट नहीं।"
    )
    return result


# ---------------------------------------------------------------- GET /raw/{feed}

@router.get("/raw/{feed}")
def get_raw(feed: str, n: int = Query(50, ge=1, le=500)):
    """CONTRACT.md §E: Last N raw records for a feed, PII-masked per §D.2."""
    if feed not in FEEDS:
        raise HTTPException(
            status_code=404,
            detail=json.dumps({"error": "not_found",
                               "detail": f"Unknown feed: {feed}"}))

    if _store is None:
        raise HTTPException(status_code=503, detail="Simulation not started")

    raw_records = _store.raw_feeds.get(feed, [])
    fmt = FEEDS[feed]["format"]

    # Take last N, newest first
    selected = raw_records[-n:][::-1] if len(raw_records) >= n else raw_records[::-1]

    # PII masking — CONTRACT.md §D.2: mask before returning
    result_records = []
    for rec in selected:
        raw = rec["raw"]
        masked_raw, items_masked = mask_record(feed, raw)

        out = {
            "raw_ref": rec.get("raw_ref", ""),
            "received_at": rec.get("received_at", ""),
            "parsed_ok": rec.get("parsed_ok", True),
            "reason": rec.get("reason"),
            "raw": masked_raw,
        }
        if items_masked > 0:
            out["items_masked"] = items_masked
        result_records.append(out)

    response = {
        "feed": feed,
        "format": "csv" if fmt == "csv" else "json",
        "count": len(result_records),
        "records": result_records,
    }
    if fmt == "csv" and raw_records:
        response["header"] = raw_records[0].get("header", "")

    return response


# ---------------------------------------------------------------- GET /scorecard
# === BOUNDARY: This is the ONE place in the whole backend/api/ where reading
# === ground_truth.json is legitimate. It must never feed back into what /state
# === or /stream serve as "detected". =========================================

@router.get("/scorecard")
def get_scorecard():
    """CONTRACT.md §E, §G: detected vs ground truth.

    BOUNDARY: This handler — and ONLY this handler — reads ground_truth.json.
    It computes scoring metrics for grading/display. Nothing it produces feeds
    back into the detection pipeline or into what /state or /stream serve.
    """
    if _store is None:
        raise HTTPException(status_code=503, detail="Simulation not started")

    gt = _store.ground_truth
    if not gt:
        raise HTTPException(status_code=404, detail="No ground truth loaded")

    situations = _store.situations  # All precomputed situations
    all_member_ids = {eid for s in situations for eid in s.get("member_event_ids", [])}

    # Match planted situations to detected ones
    matched_list = []
    used_sits = set()

    for truth in gt.get("planted_situations", []):
        truth_ids = set(truth["member_event_ids"])
        best_sit = None
        best_overlap = 0.0

        for s in situations:
            if s["situation_id"] in used_sits:
                continue
            overlap = len(truth_ids & set(s["member_event_ids"])) / len(truth_ids) if truth_ids else 0
            if overlap > best_overlap:
                best_sit = s
                best_overlap = overlap

        if best_sit and best_overlap >= 0.5:
            # COUNT AS MATCHED: Phase 5's framing — overlap >= 0.5 means the pipeline
            # found this situation. Detection timing (detect_by_utc) is Phase 10's
            # scoring job; we report it here but don't gate "detected" on it.
            used_sits.add(best_sit["situation_id"])

            # Detection lag: created_utc - onset_utc
            # NOTE: created_utc is Phase 5's max(window_end_utc) proxy — not true
            # first-detection time. A positive lag means the situation was formed
            # after the deadline; Phase 10 will score this as "detected late".
            from api.clock import _parse_utc
            lag = None
            try:
                created = _parse_utc(best_sit["created_utc"])
                onset = _parse_utc(truth["onset_utc"])
                lag = int((created - onset).total_seconds())
            except Exception:
                pass

            deadline = truth.get("detect_by_utc", "9999-01-01T00:00:00Z")
            late = best_sit["created_utc"] > deadline

            matched_list.append({
                "truth_id": truth["truth_id"],
                "matched_situation_id": best_sit["situation_id"],
                "status": "matched_late" if late else "matched",
                "expected_alert_level": truth.get("expected_alert_level"),
                "detected_alert_level": best_sit["alert_level"],
                "detection_lag_sec": lag,
                "detect_by_utc": deadline,
                "late_detection": late,
                "member_overlap": round(best_overlap, 2),
            })

        else:
            matched_list.append({
                "truth_id": truth["truth_id"],
                "matched_situation_id": None,
                "status": "missed",
                "expected_alert_level": truth.get("expected_alert_level"),
                "detected_alert_level": None,
                "detection_lag_sec": None,
                "member_overlap": round(best_overlap, 2) if best_overlap > 0 else 0.0,
            })

    # False positives: detected situations not matched to any truth
    false_positive_sits = [s for s in situations if s["situation_id"] not in used_sits]

    # Decoys: did any decoy member event appear in any situation?
    decoy_results = []
    for d in gt.get("decoys", []):
        dmem = set(d["member_event_ids"])
        leaked = dmem & all_member_ids
        # A decoy that never even got flagged by Phase 4 also counts as correctly ignored
        if leaked:
            decoy_results.append({
                "decoy_id": d["decoy_id"],
                "status": "decoy_alerted",
            })
        else:
            decoy_results.append({
                "decoy_id": d["decoy_id"],
                "status": "decoy_ignored",
            })

    matched_count = sum(1 for m in matched_list if m["status"] in ("matched", "matched_late"))
    matched_late_count = sum(1 for m in matched_list if m["status"] == "matched_late")
    missed_count = sum(1 for m in matched_list if m["status"] == "missed")
    false_pos_count = len(false_positive_sits)
    decoys_ignored = sum(1 for d in decoy_results if d["status"] == "decoy_ignored")
    total_decoys = len(decoy_results)

    precision = matched_count / (matched_count + false_pos_count) if (matched_count + false_pos_count) > 0 else 0.0
    recall = matched_count / (matched_count + missed_count) if (matched_count + missed_count) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    # Alert level accuracy
    alert_matches = sum(1 for m in matched_list
                        if m["status"] in ("matched", "matched_late") and
                        m["expected_alert_level"] == m["detected_alert_level"])
    alert_accuracy = alert_matches / matched_count if matched_count > 0 else 0.0

    # Median detection lag
    lags = [m["detection_lag_sec"] for m in matched_list
            if m["detection_lag_sec"] is not None]
    median_lag = sorted(lags)[len(lags)//2] if lags else None

    # Add false positive per-situation entries
    fp_entries = []
    for s in false_positive_sits:
        # Check if it matches a decoy
        is_decoy = False
        for d in gt.get("decoys", []):
            dmem = set(d["member_event_ids"])
            overlap = len(dmem & set(s["member_event_ids"]))
            if overlap > 0:
                is_decoy = True
                break
        fp_entries.append({
            "truth_id": None,
            "matched_situation_id": s["situation_id"],
            "status": "decoy_alerted" if is_decoy else "false_positive",
            "expected_alert_level": None,
            "detected_alert_level": s["alert_level"],
            "detection_lag_sec": None,
            "member_overlap": 0.0,
        })

    return {
        "scenario": gt.get("scenario", ""),
        "generated_utc": _iso(datetime.utcnow()),
        "truth_situations": len(gt.get("planted_situations", [])),
        "detected_situations": len(situations),
        "matched": matched_count,
        "matched_late": matched_late_count,
        "missed": missed_count,
        "false_positives": false_pos_count,
        "decoys_planted": total_decoys,
        "decoys_correctly_ignored": decoys_ignored,
        "precision": round(precision, 2),
        "recall": round(recall, 2),
        "f1": round(f1, 2),
        "alert_level_accuracy": round(alert_accuracy, 2),
        "median_detection_lag_sec": median_lag,
        "detection_lag_caveat": (
            "detection_lag_sec uses created_utc which is Phase 5's max(window_end_utc) "
            "proxy, not true first-detection time. A positive lag beyond detect_by_utc "
            "means Phase 5 formed the situation after the deadline — Phase 10 will "
            "penalise this. See Phase 5 report."
        ),
        "per_situation": matched_list + fp_entries,
    }
