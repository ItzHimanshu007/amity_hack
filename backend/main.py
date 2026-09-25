"""FastAPI app entry point for Nagar Naadi. Wires routers; holds no business logic."""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from api.clock import ReplayClock
from api.state import TimelineStore
from api.websocket import Broadcaster
from api import routes as routes_mod
from api import control as control_mod
from contract_constants import REPLAY_BOOKMARKS

# The clock opens here rather than at window_start: nothing has linked yet at
# 12:00Z, so a cold open there is an empty map that reads as a broken app.
# peak_activity is the exact minute GT-001 becomes visible.
OPENING_BOOKMARK = "peak_activity"

logging.basicConfig(level=logging.INFO, format="%(name)s | %(message)s")
logger = logging.getLogger("nagarnaadi")

# Shared instances — created during lifespan
store: TimelineStore = None
clock: ReplayClock = None
broadcaster: Broadcaster = None
_tick_task: asyncio.Task = None


async def _tick_loop():
    """Push tick messages once per real second while the server is running."""
    while True:
        try:
            await asyncio.sleep(1.0)
            if clock is None or store is None or broadcaster is None:
                continue

            sim_time = clock.advance_tick()
            city = store.city_rollup(sim_time)
            counts = store.counts(sim_time)

            # Push newly revealed events and situations
            # We track what was last pushed via the clock's previous sim_time
            # For simplicity, use a stored marker
            prev_time = getattr(clock, '_last_push_time', clock.start_dt)
            cur_time = sim_time

            if cur_time > prev_time:
                # New events
                new_evts = store.new_events_since(prev_time, cur_time)
                for ev in new_evts:
                    msg = broadcaster.event_msg(ev, sim_time)
                    await broadcaster.broadcast(msg)

                # Phase 6b: New situations (2nd member crossed)
                new_sits = store.new_situations_since(prev_time, cur_time)
                # Stopped-feed confidence penalties must survive routine updates.
                for sit in store._apply_overrides(new_sits):
                    msg = broadcaster.situation_msg(sit, "created", sim_time)
                    await broadcaster.broadcast(msg)

                # Phase 6b: Existing situations gaining new members ("updated")
                updated = store.updated_situations_since(prev_time, cur_time)
                for partial in store._apply_overrides([p for _orig, p in updated]):
                    msg = broadcaster.situation_msg(partial, "updated", sim_time)
                    await broadcaster.broadcast(msg)

                # Feed health changes
                health_rows = store.compute_feed_health(sim_time)
                prev_health = getattr(store, '_last_health', {})
                for row in health_rows:
                    prev_state = prev_health.get(row["feed"])
                    if prev_state != row["state"]:
                        msg = broadcaster.feedhealth_msg(row, sim_time)
                        await broadcaster.broadcast(msg)
                store._last_health = {r["feed"]: r["state"] for r in health_rows}

            clock._last_push_time = cur_time


            # Tick message — always sent
            tick_msg = broadcaster.tick_msg(
                sim_time=sim_time,
                tick=clock.tick_count,
                speed=clock.speed,
                state=clock.state,
                events_active=counts["events_active"],
                situations_active=counts["situations_active"],
                city_pulse_score=city["pulse_score"],
                city_alert_level=city["alert_level"],
            )
            await broadcaster.broadcast(tick_msg)

        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("tick loop error")
            await asyncio.sleep(1.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: load data, create clock, start tick loop."""
    global store, clock, broadcaster, _tick_task

    logger.info("Loading precomputed data...")
    store = TimelineStore()
    store.load()
    logger.info(f"  {len(store.events)} events, {len(store.anomalies)} anomalies, "
                f"{len(store.situations)} situations, {len(store.rejected)} rejected")

    gt = store.ground_truth
    scenario = gt.get("scenario", "monsoon_evening")
    start_utc = gt.get("sim_start_utc", "2026-09-24T12:00:00Z")
    end_utc = gt.get("sim_end_utc", "2026-09-24T15:00:00Z")

    clock = ReplayClock(start_utc, end_utc, scenario)
    broadcaster = Broadcaster()

    opening_utc = REPLAY_BOOKMARKS[OPENING_BOOKMARK]
    clock.jump_to(opening_utc)
    # Without this the first tick would replay every event from window_start as
    # "new" over the WS.
    clock._last_push_time = clock.now()

    # Inject shared state into route modules
    routes_mod.init(store, clock, broadcaster)
    control_mod.init(store, clock, broadcaster)

    logger.info(f"Replay clock ready: {start_utc} → {end_utc}, scenario={scenario}")
    logger.info(f"Opening at {OPENING_BOOKMARK} ({opening_utc}), paused")

    # Start the tick loop
    _tick_task = asyncio.create_task(_tick_loop())
    logger.info("Tick loop started")

    yield

    # Shutdown
    if _tick_task:
        _tick_task.cancel()
        try:
            await _tick_task
        except asyncio.CancelledError:
            pass
    logger.info("Shutdown complete")


app = FastAPI(title="Nagar Naadi", version="0.6.0", lifespan=lifespan)

# The frontend is served from a separate `python -m http.server` port, so it is
# cross-origin. Hackathon-wide open policy; tighten if this ever leaves a laptop.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    """Liveness probe. Everyone uses this to confirm the backend is up."""
    return {"status": "ok"}


# Mount route modules
app.include_router(routes_mod.router)
app.include_router(control_mod.router)


@app.websocket("/stream")
async def ws_stream(ws: WebSocket):
    """WS /stream: push channel for event | situation | feedhealth | tick.

    CONTRACT.md §E: connect and receive. The server pushes; the client never sends
    on this socket.
    """
    await broadcaster.connect(ws)
    try:
        # Keep connection alive — client never sends, so we just wait
        while True:
            try:
                await ws.receive_text()
            except WebSocketDisconnect:
                break
    finally:
        broadcaster.disconnect(ws)
