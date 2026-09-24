"""Replay clock for the simulation. Tracks simulated time keyed to the demo window.

CONTRACT.md §E: play/pause/speed(N). Speeds up to 200x so the 3-hour demo window
completes in ~54 seconds of real time. Bookmark jumps recompute state as-of that
timestamp without replaying tick-by-tick — same served state as linear playback.
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

from contract_constants import ACTIVE_WINDOW_SEC

# Allowed speed values. CONTRACT.md §E lists 1,2,4,8,16 but the prompt requires
# up to ~200x for the pitch demo (3h / 200 = 54s). We accept any of these.
ALLOWED_SPEEDS = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 100.0, 200.0)


def _parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class ReplayClock:
    """Controllable simulation clock.

    The clock is anchored to the demo window's `start_utc`. In play mode, simulated
    time advances at `speed` real seconds per simulated second. In paused mode it stays
    frozen.

    Jump-to-bookmark sets sim_time directly — the caller is responsible for recomputing
    the served state as-of the new timestamp so there are no path-dependent bugs.
    """

    def __init__(self, start_utc: str, end_utc: str, scenario: str):
        self.start_dt = _parse_utc(start_utc)
        self.end_dt = _parse_utc(end_utc)
        self.scenario = scenario

        # Start paused at the beginning of the window
        self.sim_time = self.start_dt
        self.speed: float = 1.0
        self.playing: bool = False
        self.tick_count: int = 0

        # Anchor: when we last started/resumed playing
        self._anchor_real: Optional[float] = None
        self._anchor_sim: Optional[datetime] = None

    @property
    def state(self) -> str:
        return "play" if self.playing else "paused"

    def now(self) -> datetime:
        """Current simulated time."""
        if not self.playing or self._anchor_real is None:
            return self.sim_time
        elapsed_real = time.monotonic() - self._anchor_real
        elapsed_sim = elapsed_real * self.speed
        from datetime import timedelta
        t = self._anchor_sim + timedelta(seconds=elapsed_sim)
        # Clamp to window end
        if t > self.end_dt:
            t = self.end_dt
        self.sim_time = t
        return t

    def play(self):
        if not self.playing:
            self.playing = True
            self._anchor_real = time.monotonic()
            self._anchor_sim = self.sim_time

    def pause(self):
        if self.playing:
            self.sim_time = self.now()
            self.playing = False
            self._anchor_real = None
            self._anchor_sim = None

    def set_speed(self, speed: float):
        if speed not in ALLOWED_SPEEDS:
            raise ValueError(f"speed must be one of {ALLOWED_SPEEDS}")
        # Freeze current position, then restart with new speed
        was_playing = self.playing
        if was_playing:
            self.pause()
        self.speed = speed
        if was_playing:
            self.play()

    def jump_to(self, target_utc: str):
        """Jump to a specific simulated time. The caller recomputes state."""
        was_playing = self.playing
        self.pause()
        self.sim_time = _parse_utc(target_utc)
        # Clamp
        if self.sim_time < self.start_dt:
            self.sim_time = self.start_dt
        if self.sim_time > self.end_dt:
            self.sim_time = self.end_dt
        if was_playing:
            self.play()

    def advance_tick(self) -> datetime:
        """Called once per real second by the tick loop. Returns current sim time."""
        self.tick_count += 1
        return self.now()

    def sim_block(self) -> dict:
        """The `sim` block for /state and /control responses."""
        return {
            "scenario": self.scenario,
            "state": self.state,
            "speed": self.speed,
            "sim_time_utc": _iso(self.now()),
            "tick": self.tick_count,
        }

    def is_finished(self) -> bool:
        return self.now() >= self.end_dt

    def window_duration_sec(self) -> float:
        return (self.end_dt - self.start_dt).total_seconds()
