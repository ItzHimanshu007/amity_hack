"""One adapter per raw feed: raw records -> candidate canonical events.

Each adapter yields a candidate per CONTRIBUTING raw record. Records belonging to one
ongoing episode all carry the same raw_ref -- the one from the record that opened it
(CONTRACT.md §A) -- so ingest.dedupe folds them into a single event afterwards. That
keeps the folding rule in one place and makes the reconciliation delta honest.

Every adapter returns (candidates, stats).
"""

from ingest.adapters import (air_sensors, civic_complaints, drain_scada,  # noqa: F401
                             power_discom, weather_imd)

ADAPTERS = {
    "weather_imd": weather_imd,
    "air_sensors": air_sensors,
    "power_discom": power_discom,
    "drain_scada": drain_scada,
    "civic_complaints": civic_complaints,
}
