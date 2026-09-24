"""WS /stream: broadcasts event | situation | feedhealth | tick messages. See CONTRACT.md §E.

Pushes messages in four types as the clock advances, matching §E's example shapes exactly.
The client never sends on this socket — use POST /control instead.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Set

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("nagarnaadi.ws")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class Broadcaster:
    """Manages connected WebSocket clients and pushes messages to all of them."""

    def __init__(self):
        self._clients: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._clients.add(ws)
        logger.info(f"WS client connected, total: {len(self._clients)}")

    def disconnect(self, ws: WebSocket):
        self._clients.discard(ws)
        logger.info(f"WS client disconnected, total: {len(self._clients)}")

    async def broadcast(self, message: dict):
        """Send a message to all connected clients."""
        text = json.dumps(message, ensure_ascii=False)
        dead = set()
        for ws in self._clients:
            try:
                await ws.send_text(text)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self._clients.discard(ws)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    # ---------------------------------------------------------------- message builders

    @staticmethod
    def tick_msg(sim_time: datetime, tick: int, speed: float, state: str,
                 events_active: int, situations_active: int,
                 city_pulse_score: int, city_alert_level: str) -> dict:
        """CONTRACT.md §E tick message."""
        return {
            "type": "tick",
            "sent_utc": _iso(sim_time),
            "data": {
                "sim_time_utc": _iso(sim_time),
                "tick": tick,
                "speed": speed,
                "state": state,
                "events_active": events_active,
                "situations_active": situations_active,
                "city_pulse_score": city_pulse_score,
                "city_alert_level": city_alert_level,
            }
        }

    @staticmethod
    def event_msg(event: dict, sim_time: datetime) -> dict:
        """CONTRACT.md §E event message — lightweight summary."""
        return {
            "type": "event",
            "sent_utc": _iso(sim_time),
            "data": {
                "event_id": event["event_id"],
                "source": event["source"],
                "category": event["category"],
                "h3_cell": event["h3_cell"],
                "lat": event["lat"],
                "lon": event["lon"],
                "start_utc": event["start_utc"],
                "end_utc": event.get("end_utc"),
                "severity": event["severity"],
                "confidence": event["confidence"],
                "received_at": event["received_at"],
                "freshness_sec": event["freshness_sec"],
                "is_simulated": event.get("is_simulated", True),
                "raw_ref": event["raw_ref"],
            }
        }

    @staticmethod
    def situation_msg(situation: dict, action: str, sim_time: datetime) -> dict:
        """CONTRACT.md §E situation message."""
        return {
            "type": "situation",
            "sent_utc": _iso(sim_time),
            "action": action,  # created | updated | closed
            "data": situation,
        }

    @staticmethod
    def feedhealth_msg(health_row: dict, sim_time: datetime) -> dict:
        """CONTRACT.md §E feedhealth message."""
        return {
            "type": "feedhealth",
            "sent_utc": _iso(sim_time),
            "data": health_row,
        }

    @staticmethod
    def duplicate_event_msg(event: dict, sim_time: datetime) -> dict:
        """inject_duplicate chaos-control: re-serves an event tagged is_duplicate."""
        data = dict(event)
        data["received_at"] = _iso(sim_time)
        data["is_duplicate"] = True
        return {
            "type": "event",
            "sent_utc": _iso(sim_time),
            "data": data,
        }
