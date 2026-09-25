"""Diurnal rate profiles, seeded RNG streams, and a pure-Python Poisson draw.

No numpy: requirements.txt stays as scaffolded.
"""

import hashlib
import math
import random
from datetime import timedelta

IST_OFFSET = timedelta(hours=5, minutes=30)


def ist_hour(dt) -> int:
    """Hour of day in IST. Human activity follows the local clock, not UTC."""
    return (dt + IST_OFFSET).hour


def rng(stream: str, seed: int) -> random.Random:
    """A reproducible RNG per named stream.

    Python's builtin hash() is salted per process, so it cannot be used here -- the
    whole lane has to regenerate byte-identically on any machine.
    """
    digest = hashlib.sha256(f"{seed}:{stream}".encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def poisson(r: random.Random, lam: float) -> int:
    """Knuth's algorithm. lam stays small here, so the loop is cheap."""
    if lam <= 0:
        return 0
    if lam > 30:  # normal approximation, never hit at our rates but safe
        return max(0, int(r.gauss(lam, math.sqrt(lam)) + 0.5))
    limit = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        p *= r.random()
        if p <= limit:
            return k
        k += 1


# --- 24-slot multipliers, indexed by IST hour ---------------------------------
# Quiet 02-05, commute bumps, evening peak. Mean of each profile is near 1.0 so the
# BASELINE_RATES in config.py read as true hourly averages.

def _norm(vals):
    m = sum(vals) / len(vals)
    return tuple(v / m for v in vals)


# Residents complain when they are awake and out.
COMPLAINTS = _norm([
    0.3, 0.2, 0.15, 0.12, 0.15, 0.4,   # 00-05
    0.8, 1.2, 1.5, 1.6, 1.5, 1.3,      # 06-11
    1.2, 1.1, 1.0, 1.1, 1.3, 1.6,      # 12-17
    1.9, 2.0, 1.7, 1.2, 0.8, 0.5,      # 18-23
])

# Drains block with silt and debris at any hour; there's no daily rhythm to it.
DRAIN = _norm([1.0] * 24)

# Grid load peaks in the afternoon heat and again at dinner.
POWER = _norm([
    0.5, 0.4, 0.4, 0.4, 0.5, 0.6,
    0.8, 0.9, 1.0, 1.1, 1.3, 1.5,
    1.7, 1.8, 1.8, 1.6, 1.4, 1.5,
    1.7, 1.6, 1.3, 1.0, 0.8, 0.6,
])

# Cooking smoke plus a morning and evening inversion.
AIR = _norm([
    1.2, 1.1, 1.0, 1.0, 1.1, 1.3,
    1.6, 1.8, 1.7, 1.4, 1.1, 0.9,
    0.7, 0.6, 0.6, 0.7, 0.9, 1.3,
    1.7, 1.9, 1.8, 1.6, 1.4, 1.3,
])

# September convective rain: afternoon and evening biased.
RAIN = _norm([
    0.4, 0.3, 0.3, 0.3, 0.3, 0.4,
    0.5, 0.6, 0.7, 0.8, 0.9, 1.1,
    1.4, 1.7, 1.9, 1.9, 1.8, 1.6,
    1.5, 1.3, 1.0, 0.8, 0.6, 0.5,
])

PROFILE_FOR = {
    "complaint.waterlogging": COMPLAINTS,
    "complaint.garbage": COMPLAINTS,
    "complaint.streetlight": COMPLAINTS,
    "complaint.road_damage": COMPLAINTS,
    "complaint.smoke": COMPLAINTS,
    "traffic.signal_down": COMPLAINTS,
    "power.outage": POWER,
    "drain.overflow": DRAIN,
    "air.pm25": AIR,
    "weather.rain": RAIN,
}


def rate_at(category: str, base_rate: float, dt) -> float:
    """Hourly rate for a category at a moment, after the diurnal multiplier."""
    profile = PROFILE_FOR.get(category, COMPLAINTS)
    return base_rate * profile[ist_hour(dt)]
