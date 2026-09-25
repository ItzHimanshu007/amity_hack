"""Raw weather feed: JSON Lines, UTC epoch seconds. CONTRACT.md §D.1.

Emits continuous station observations. There is no 'event' in this feed -- Phase 3
thresholds the numbers. This writer mirrors those floors only so it can record which
observation OPENS an event, which is what fixes the event_id (CONTRACT.md §A).
"""

import json
import math
from datetime import timedelta

from contract_constants import FEEDS, crosses_floor
from sim import config
from sim.ids import make_event_id, ref_weather
from sim.index import entry
from sim.profiles import ist_hour, rng


def heat_index_c(temp_c: float, rh: float) -> float:
    """NWS Rothfusz. Below 27 C the heat index is just the temperature."""
    t = temp_c * 9 / 5 + 32
    if t < 80:
        return temp_c
    hi = (-42.379 + 2.04901523 * t + 10.14333127 * rh - 0.22475541 * t * rh
          - 0.00683783 * t * t - 0.05481717 * rh * rh + 0.00122874 * t * t * rh
          + 0.00085282 * t * rh * rh - 0.00000199 * t * t * rh * rh)
    return (hi - 32) * 5 / 9


def _rain_shape(frac: float) -> float:
    """Rises fast, peaks a third of the way in, tails off."""
    if frac <= 0.33:
        return frac / 0.33
    return max(0.0, 1.0 - (frac - 0.33) / 0.67) ** 0.7


def write(world, specs, seed: int = config.SEED):
    """Returns (record_count, index_entries). Mutates specs with raw_ref/event_id."""
    r = rng("weather_writer", seed)
    path = config.DATA_DIR / config.OUTPUT_FILES["weather_imd"]
    interval = FEEDS["weather_imd"]["interval_sec"]

    rain_specs = [s for s in specs if s.category == "weather.rain"]
    by_station = {}
    for s in rain_specs:
        by_station.setdefault(s.entity, []).append(s)

    # Per-day weather character, stable across stations on the same day.
    def day_profile(day_key):
        d = rng(f"weather_day:{day_key}", seed)
        return {
            "tmax": d.uniform(29.5, 34.5),     # late-September monsoon, Jaipur
            "tmin": d.uniform(22.0, 27.0),
            "rh_base": d.uniform(52.0, 82.0),
        }

    profiles = {}
    index, count = [], 0
    open_state = {}   # (station, category) -> True while the event is open

    with path.open("w", encoding="utf-8") as fh:
        for st in world["stations"]:
            sid = st["station_id"]
            t = config.HISTORY_START
            while t <= config.SIM_END:
                day_key = t.strftime("%Y-%m-%d")
                if day_key not in profiles:
                    profiles[day_key] = day_profile(day_key)
                dp = profiles[day_key]

                # Diurnal temperature: coolest around 05:00 IST, hottest around 15:00.
                h = ist_hour(t) + (t.minute / 60.0)
                swing = (dp["tmax"] - dp["tmin"]) / 2
                mid = (dp["tmax"] + dp["tmin"]) / 2
                temp = mid - swing * math.cos((h - 5) / 24 * 2 * math.pi)
                temp += r.gauss(0, 0.35)

                # Rain: zero unless an episode is running at this station.
                rain = 0.0
                active = None
                for s in by_station.get(sid, []):
                    if s.start <= t < (s.end or s.start + timedelta(minutes=30)):
                        frac = (t - s.start).total_seconds() / max(
                            1.0, (s.end - s.start).total_seconds())
                        rain = max(rain, s.measure * _rain_shape(frac))
                        active = s
                if rain > 0:
                    rain = round(max(0.0, rain + r.gauss(0, 0.6)), 1)

                rh = dp["rh_base"] + (18 if rain > 0 else 0) - (temp - mid) * 1.6
                rh = round(min(97.0, max(28.0, rh + r.gauss(0, 2.0))), 0)
                temp = round(temp - (2.5 if rain > 4 else 0), 1)

                rec = {
                    "station_id": sid,
                    "ts": int(t.timestamp()),
                    "lat": st["lat"],
                    "lon": st["lon"],
                    "rain_mm_15min": rain,
                    "temp_c": temp,
                    "rh_pct": rh,
                    "wind_kph": round(max(0.0, r.gauss(14 if rain > 0 else 8, 5)), 0),
                }
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                count += 1

                # --- floor crossings open events (CONTRACT.md §A raw_ref grammar) ---
                for cat, measure in (("weather.rain", rain),
                                     ("weather.heat", heat_index_c(temp, rh))):
                    key = (sid, cat)
                    above = crosses_floor(cat, measure)
                    if above and not open_state.get(key):
                        raw_ref = ref_weather(sid, t)
                        eid = make_event_id(raw_ref, cat)
                        src = active if (cat == "weather.rain" and active) else None
                        if src is not None:
                            src.raw_ref, src.event_id = raw_ref, eid
                        index.append(entry(
                            eid, raw_ref, cat, "weather_imd", t,
                            st["h3_cell"], st["lat"], st["lon"], measure,
                            truth_id=src.truth_id if src else None,
                            decoy_id=src.decoy_id if src else None))
                    open_state[key] = above

                t += timedelta(seconds=interval)

    return count, index
