"""POST /control: play, pause, speed, kill_feed, resume_feed, set_scenario,
jump_to, delay_feed, inject_duplicate. See CONTRACT.md §E.

Chaos controls apply serve-time adjustments only — no re-running of Phase 5's linker.
"""

import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

from contract_constants import (
    FEEDS, REPLAY_ALLOWED_SPEEDS, REPLAY_BOOKMARKS,
    CHAOS_CONFIDENCE_PENALTY, CHAOS_PULSE_PENALTY,
    CONTROL_ACTIONS,
)

logger = logging.getLogger("nagarnaadi.control")
router = APIRouter()

_store = None
_clock = None
_broadcaster = None


def init(store, clock, broadcaster):
    global _store, _clock, _broadcaster
    _store = store
    _clock = clock
    _broadcaster = broadcaster


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@router.post("/control")
async def post_control(request: Request):
    """CONTRACT.md §E: drive the simulation."""
    if _clock is None or _store is None:
        raise HTTPException(status_code=503, detail="Simulation not started")

    body = await request.json()
    action = body.get("action")
    value = body.get("value")

    if action not in CONTROL_ACTIONS:
        raise HTTPException(status_code=400, detail=json.dumps({
            "error": "bad_request",
            "detail": f"Unknown action: {action}. Must be one of {CONTROL_ACTIONS}",
        }))

    # ----- play -----
    if action == "play":
        _clock.play()
        return {"ok": True, "sim": _clock.sim_block()}

    # ----- pause -----
    if action == "pause":
        _clock.pause()
        return {"ok": True, "sim": _clock.sim_block()}

    # ----- speed -----
    if action == "speed":
        try:
            spd = float(value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=json.dumps({
                "error": "bad_request",
                "detail": f"speed must be one of {REPLAY_ALLOWED_SPEEDS}",
            }))
        if spd not in REPLAY_ALLOWED_SPEEDS:
            raise HTTPException(status_code=400, detail=json.dumps({
                "error": "bad_request",
                "detail": f"speed must be one of {REPLAY_ALLOWED_SPEEDS}",
            }))
        _clock.set_speed(spd)
        return {"ok": True, "sim": _clock.sim_block()}

    # ----- jump_to -----
    if action == "jump_to":
        bookmark = str(value) if value else ""
        if bookmark in REPLAY_BOOKMARKS:
            target_utc = REPLAY_BOOKMARKS[bookmark]
        else:
            # Try as a raw UTC timestamp
            try:
                from api.clock import _parse_utc
                _parse_utc(bookmark)
                target_utc = bookmark
            except Exception:
                raise HTTPException(status_code=400, detail=json.dumps({
                    "error": "bad_request",
                    "detail": f"Unknown bookmark: {bookmark}. Available: {list(REPLAY_BOOKMARKS.keys())}",
                }))
        _clock.jump_to(target_utc)
        return {"ok": True, "sim": _clock.sim_block()}

    # ----- kill_feed -----
    if action == "kill_feed":
        feed = str(value) if value else ""
        if feed not in FEEDS:
            raise HTTPException(status_code=400, detail=json.dumps({
                "error": "bad_request",
                "detail": f"Unknown feed: {feed}",
            }))
        _store.killed_feeds.add(feed)

        # Apply serve-time confidence penalty to active situations with events from this feed
        sim_time = _clock.now()
        active_sits = _store.active_situations(sim_time)
        updated_sits = []
        for sit in active_sits:
            if sit["situation_id"] in _store.confidence_overrides:
                continue  # Already penalized
            # Check if any member event is from this feed
            has_feed_event = False
            for eid in sit.get("member_event_ids", []):
                ev = _store.events_by_id.get(eid)
                if ev and ev["source"] == feed:
                    has_feed_event = True
                    break
            if has_feed_event:
                original_conf = sit["confidence_level"]
                adjusted = CHAOS_CONFIDENCE_PENALTY.get(original_conf, original_conf)
                reason_en = f"{feed} feed stopped — confidence lowered"
                reason_hi = f"{feed} फीड बंद — भरोसा कम किया गया"
                _store.confidence_overrides[sit["situation_id"]] = {
                    "original_confidence": original_conf,
                    "adjusted_confidence": adjusted,
                    "reason_en": reason_en,
                    "reason_hi": reason_hi,
                    "penalty_applied": True,
                    "killed_feed": feed,
                }
                updated_sits.append(sit["situation_id"])

        # Push feedhealth update
        if _broadcaster:
            health_rows = _store.compute_feed_health(sim_time)
            for row in health_rows:
                if row["feed"] == feed:
                    msg = _broadcaster.feedhealth_msg(row, sim_time)
                    import asyncio
                    await _broadcaster.broadcast(msg)

            # Push updated situation messages
            for sid in updated_sits:
                for sit in active_sits:
                    if sit["situation_id"] == sid:
                        # Same serve-time override /state and the tick loop apply.
                        sit_copy = _store._apply_overrides([sit])[0]
                        msg = _broadcaster.situation_msg(sit_copy, "updated", sim_time)
                        await _broadcaster.broadcast(msg)

        return {"ok": True, "sim": _clock.sim_block(),
                "feed": feed, "state": "killed",
                "situations_updated": updated_sits}

    # ----- resume_feed -----
    if action == "resume_feed":
        feed = str(value) if value else ""
        if feed not in FEEDS:
            raise HTTPException(status_code=400, detail=json.dumps({
                "error": "bad_request",
                "detail": f"Unknown feed: {feed}",
            }))
        _store.killed_feeds.discard(feed)
        _store.delayed_feeds.pop(feed, None)

        # Reverse confidence penalties for this feed
        sim_time = _clock.now()
        restored_sits = []
        to_remove = []
        for sid, override in _store.confidence_overrides.items():
            if override.get("killed_feed") == feed:
                to_remove.append(sid)
                restored_sits.append(sid)
        for sid in to_remove:
            del _store.confidence_overrides[sid]

        # Push feedhealth update
        if _broadcaster:
            health_rows = _store.compute_feed_health(sim_time)
            for row in health_rows:
                if row["feed"] == feed:
                    msg = _broadcaster.feedhealth_msg(row, sim_time)
                    await _broadcaster.broadcast(msg)

            # Push restored values for every active situation that includes this
            # feed (covers ones penalised after they appeared while it was stopped).
            for sit in _store._apply_overrides(_store.active_situations(sim_time)):
                sources = {(_store.events_by_id.get(eid) or {}).get("source")
                           for eid in sit.get("member_event_ids", [])}
                if sit["situation_id"] in restored_sits or feed in sources:
                    msg = _broadcaster.situation_msg(sit, "updated", sim_time)
                    await _broadcaster.broadcast(msg)

        return {"ok": True, "sim": _clock.sim_block(),
                "feed": feed, "state": "live",
                "situations_restored": restored_sits}

    # ----- set_scenario -----
    if action == "set_scenario":
        scenario = str(value) if value else ""
        # For now, only monsoon_evening is precomputed
        if scenario != "monsoon_evening":
            raise HTTPException(status_code=400, detail=json.dumps({
                "error": "bad_request",
                "detail": f"Only 'monsoon_evening' is available. Got: {scenario}",
            }))
        # Reset everything
        _store.killed_feeds.clear()
        _store.delayed_feeds.clear()
        _store.confidence_overrides.clear()
        _store.load()
        gt = _store.ground_truth
        _clock.__init__(gt["sim_start_utc"], gt["sim_end_utc"], gt["scenario"])
        return {"ok": True, "sim": _clock.sim_block()}

    # ----- delay_feed -----
    if action == "delay_feed":
        if not isinstance(value, dict):
            raise HTTPException(status_code=400, detail=json.dumps({
                "error": "bad_request",
                "detail": "delay_feed requires value: {source: <feed_id>, seconds: <int>}",
            }))
        feed = value.get("source", "")
        seconds = value.get("seconds", 0)
        if feed not in FEEDS:
            raise HTTPException(status_code=400, detail=json.dumps({
                "error": "bad_request",
                "detail": f"Unknown feed: {feed}",
            }))
        try:
            seconds = int(seconds)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=json.dumps({
                "error": "bad_request",
                "detail": "seconds must be an integer",
            }))
        _store.delayed_feeds[feed] = seconds
        return {"ok": True, "sim": _clock.sim_block(),
                "feed": feed, "delay_seconds": seconds}

    # ----- inject_duplicate -----
    if action == "inject_duplicate":
        event_id = str(value) if value else ""
        ev = _store.events_by_id.get(event_id)
        if not ev:
            raise HTTPException(status_code=400, detail=json.dumps({
                "error": "bad_request",
                "detail": f"Unknown event_id: {event_id}",
            }))
        if _broadcaster:
            sim_time = _clock.now()
            msg = _broadcaster.duplicate_event_msg(ev, sim_time)
            await _broadcaster.broadcast(msg)
        return {"ok": True, "sim": _clock.sim_block(),
                "event_id": event_id, "injected": True}

    # Should not reach here
    raise HTTPException(status_code=400, detail=json.dumps({
        "error": "bad_request", "detail": f"Unhandled action: {action}"}))
