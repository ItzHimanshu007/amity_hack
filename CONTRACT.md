# CONTRACT.md — Nagar Naadi

Single source of truth for how the four workstreams connect. City: **Jaipur, Rajasthan**.

> **Every field name, enum value, file path and endpoint in this document is FINAL.**
> Later phases are told: *use CONTRACT.md exactly, do not rename.* If something here is
> genuinely wrong, change this file first and tell the other three people — never work
> around it locally.

Sections: [A. Event schema](#a-canonical-event-schema) · [B. Categories](#b-category-enum) ·
[C. Zones](#c-zone-model) · [D. Feeds](#d-feed-list) · [E. API](#e-api-contract) ·
[E.1 Anomaly record](#e1-anomaly-record-shape) · [F. Situation](#f-situation-object-shape) ·
[F.1 Linking](#f1-linking-phase-5) ·
[G. Ground truth](#g-scenario--ground-truth-format) · [H. Conventions](#h-filenaming-conventions)

---

## A. Canonical event schema

Every record from every feed is normalized into exactly this object. There are no
feed-specific top-level fields — anything feed-specific stays in the raw record and is
reachable through `raw_ref`.

| Field | Type | Null? | Rule |
|---|---|---|---|
| `event_id` | str | no | **UUID5**, derived — never random. `uuid5(NAGARNAADI_NS, f"{raw_ref}\|{category}")`. See [event_id derivation](#event_id-derivation-fixed) below. Stable for the life of the event. |
| `source` | str | no | Feed id from [D](#d-feed-list): `weather_imd` \| `civic_complaints` \| `power_discom` \| `drain_scada` \| `air_sensors` |
| `category` | str | no | One of the 11 ids in [B](#b-category-enum). |
| `h3_cell` | str | no | H3 **resolution 8** index, 15 hex chars, e.g. `"883da218c3fffff"`. Always `latlng_to_cell(lat, lon, 8)`. |
| `lat` | float | no | WGS84, 5 decimal places. |
| `lon` | float | no | WGS84, 5 decimal places. |
| `start_utc` | str | no | ISO8601 UTC with `Z`, seconds precision: `"2026-09-24T13:12:00Z"`. When the real-world condition began. |
| `end_utc` | str | **yes** | Same format, or `null` while the condition is still ongoing. |
| `severity` | float | no | `0.0`–`1.0`. How bad it is. Per-category scaling below. |
| `confidence` | float | no | `0.0`–`1.0`. How much we trust the reading itself (sensor quality, resolution guesswork). |
| `received_at` | str | no | ISO8601 UTC `Z`. When our ingest saw the record. |
| `freshness_sec` | int | no | `received_at − start_utc` in whole seconds. Always `>= 0`; clamp to 0 if a clock drifts backwards. |
| `is_simulated` | bool | no | `true` for everything in this build. See [H](#h-filenaming-conventions). |
| `raw_ref` | str | no | `"<feed_id>:<record identifier>"` — points back to the exact raw record, used by `GET /raw/{feed}`. The identifier grammar is **fixed per feed**, see [raw_ref grammar](#raw_ref-grammar-fixed). |

### event_id derivation (fixed)

`event_id` is **derived, not random.** Phase 1 writes the raw feeds and the ground-truth
answer key; Phase 3 reads the raw feeds and normalizes them. Both must arrive at the
*same* id for the same real-world event, without talking to each other — otherwise every
`member_event_ids` entry in [G](#g-scenario--ground-truth-format) dangles and
`/scorecard` silently reports zero.

```python
import uuid

NAGARNAADI_NS = uuid.UUID("1f0a7b2c-3d4e-4f50-9a61-7b8c9d0e1f20")

def make_event_id(raw_ref: str, category: str) -> str:
    return str(uuid.uuid5(NAGARNAADI_NS, f"{raw_ref}|{category}"))
```

- The separator is a single pipe `|` with **no surrounding whitespace**.
- `category` is the exact id from [B](#b-category-enum), lowercase, unmodified.
- The `|{category}` suffix is what lets **one raw record produce several events** with
  distinct ids — a power `TRIP` on a signal-bearing feeder produces both a
  `power.outage` and a `traffic.signal_down` id from the same `raw_ref`.
- **Phase 3 reimplements this function; it does not import Phase 1's copy.** The
  normalizer must stand alone. Three lines of duplication is the correct trade here.
- Nothing is embedded in the raw records. No real municipal CSV carries our UUIDs, and
  the data room would look fake if ours did.

### raw_ref grammar (fixed)

Because the id is derived from `raw_ref`, both phases must build **byte-identical**
strings. Granularity differs per feed, because fan-out differs per feed.

| Feed | `raw_ref` | Fan-out |
|---|---|---|
| `weather_imd` | `weather_imd:<station_id>@<ts_epoch>` | many records → 1 event; id comes from the record that **opens** it |
| `air_sensors` | `air_sensors:<sensor>@<captured>` | many records → 1 event; id comes from the record that **opens** it |
| `power_discom` | `power_discom:<feeder_id>@<reported_epoch>` | 1 record → **2** events when `carries_signals` is true |
| `drain_scada` | `drain_scada:<rtu>@<epoch>` | the poll that first crossed the overflow floor; the episode keeps it |
| `civic_complaints` | `civic_complaints:<complaint_id>` | 1 record → 1 event |

Rules that follow:

- `<ts_epoch>` and `<reported_epoch>` are **integer** epoch seconds, UTC, no decimal
  point. `<captured>` is the `captured` string exactly as it appears in the record,
  `Z` included.
- **"Opens" means the first record to cross the [severity floor](#severity-scaling-fixed).**
  A rain episode spans many 5-minute observations but is one event, so the event keeps
  the `raw_ref` of the first above-floor observation for its whole life — consistent with
  [D.1](#d1-weather_imd)'s "use the latest record per station; do not sum".
- `drain_scada`'s `<epoch>` is the opening poll's IST text converted to UTC epoch seconds,
  so every later poll of the same overflow folds into one event ([D.4](#d4-drain_scada)).
- A `RESTORE` record never mints an id. It closes the `power.outage` opened by the
  matching `TRIP`, which keeps the `TRIP`'s `raw_ref`.

### severity scaling (fixed — do not invent your own)

Normalize to 0–1 with a linear ramp between a floor (below which no event is emitted) and
a ceiling (at which severity = 1.0).

| Category | Measure | floor → 0.0 | ceiling → 1.0 |
|---|---|---|---|
| `weather.rain` | mm in 15 min | 5 | 40 |
| `weather.heat` | heat index °C | 38 | 50 |
| `air.pm25` | µg/m³ | 60 | 300 |
| `power.outage` | affected_connections | 200 | 8000 |
| `traffic.signal_down` | junctions dark | 1 | 6 |
| `drain.overflow` | level, % of channel capacity | 85 | 130 |
| `complaint.*` | count of open complaints in the cell, 30 min window | 1 | 12 |

**Note on `traffic.signal_down` from `civic_complaints`.** The floor for this category
is 1 junction dark (see [severity scaling](#severity-scaling-fixed--do-not-invent-your-own)
above), and a resident report can only ever describe their own junction -- the measure
is always exactly 1. Since the ramp is
`measure <= floor -> 0.0`, every `civic_complaints`-sourced `traffic.signal_down` event
has `severity` exactly `0.0`, always, by construction. **This is not missing or degraded
data.** A consumer must not exclude these events, down-weight them, or treat a flat 0.0
as a data-quality problem -- the `power_discom`-sourced sibling of the same real-world
junction carries the real severity signal; the complaint-sourced one carries the
independent-corroboration signal instead (see the dedupe carve-out above). Blend on
`source` diversity, not on severity, when this category is involved.

### confidence rules (fixed)

| Situation | confidence |
|---|---|
| Direct sensor reading, calibrated | `0.90` |
| Direct sensor reading, `calibrated: false` | `0.60` |
| Resident-reported complaint | `0.70` |
| Location resolved from a landmark string | multiply by `0.85` |
| Location resolved from a `feeder_id` or drain `rtu` registry | multiply by `0.80` |

Round to 2 decimals. Floor at `0.30`.

### filled-in example

```json
{
  "event_id": "9f1c4b2e-5a7d-4e18-9c30-16b8ad4e2f77",
  "source": "civic_complaints",
  "category": "complaint.waterlogging",
  "h3_cell": "883da218c3fffff",
  "lat": 26.92680,
  "lon": 75.79300,
  "start_utc": "2026-09-24T13:12:00Z",
  "end_utc": null,
  "severity": 0.58,
  "confidence": 0.60,
  "received_at": "2026-09-24T13:18:43Z",
  "freshness_sec": 403,
  "is_simulated": true,
  "raw_ref": "civic_complaints:JPR-2026-114872"
}
```

That example is the CSV row shown in [D.2](#d2-civic_complaints) after normalization:
IST `24/09/2026 6:42 PM` became `2026-09-24T13:12:00Z`, the landmark string
`Near Sindhi Camp Bus Stand` resolved to a lat/lon (hence `0.70 × 0.85 = 0.60`
confidence), and that lat/lon became the res-8 cell.

---

## B. Category enum

Eleven ids. This list is closed — no phase may add a twelfth without editing this file.

| id | English label | Hindi label | Emitted by |
|---|---|---|---|
| `weather.rain` | Heavy rain | तेज़ बारिश | `weather_imd` |
| `weather.heat` | Extreme heat | अत्यधिक गर्मी | `weather_imd` |
| `air.pm25` | Poor air | खराब हवा | `air_sensors` |
| `power.outage` | Power cut | बिजली कटौती | `power_discom` |
| `traffic.signal_down` | Signal not working | सिग्नल बंद | `power_discom`, `civic_complaints` |
| `drain.overflow` | Drain overflowing | नाला उफान पर | `drain_scada` |
| `complaint.waterlogging` | Waterlogging | जलभराव | `civic_complaints` |
| `complaint.garbage` | Garbage not cleared | कचरा नहीं उठा | `civic_complaints` |
| `complaint.streetlight` | Streetlight out | स्ट्रीटलाइट बंद | `civic_complaints` |
| `complaint.road_damage` | Road damage | सड़क खराब | `civic_complaints` |
| `complaint.smoke` | Smoke or burning | धुआँ या जलना | `civic_complaints` |

Notes:

- **Category ids are source-agnostic.** `traffic.signal_down` arrives from two feeds
  (a DISCOM feeder trip on a signal-bearing circuit, and residents reporting a dark
  junction). Two independent sources agreeing is exactly the corroboration the linker
  in Phase 5 looks for — do not merge them at ingest.
- The `.` in an id has no code meaning. It is not a namespace to parse. Treat the whole
  string as one opaque key.
- English labels are **sentence case**, not Title Case. See DESIGN.md.

---

## C. Zone model

### city constants

| Constant | Value |
|---|---|
| City | Jaipur, Rajasthan, India |
| Timezone | `Asia/Kolkata` (IST, UTC+05:30) — display only, see [H](#h-filenaming-conventions) |
| Bounding box SW | `26.7900, 75.6900` |
| Bounding box NE | `26.9900, 75.8900` |
| Bbox span | ≈ 22.2 km N–S × ≈ 19.9 km E–W, ≈ 441 km² |
| `H3_RES` | `8` |
| Res-8 cells inside the bbox | **591** (exact, from `h3shape_to_cells`) |
| Res-8 average cell area | 0.737 km² |
| Res-8 average edge length | 531 m |
| Map default centre | `26.9124, 75.7873` |
| Map default zoom | `11.5` |

591 cells is the whole board. A busy scenario touches 20–40 of them.

### lat/lon → cell

```python
# backend — h3 v4 API
import h3
H3_RES = 8
cell = h3.latlng_to_cell(lat, lon, H3_RES)      # -> "883da218c3fffff"
```

```js
// frontend — h3-js v4
import { latLngToCell, cellToBoundary, gridDisk, gridDistance } from "h3-js";
const H3_RES = 8;
const cell = latLngToCell(lat, lon, H3_RES);
```

Argument order is **(lat, lon)** in both libraries. MapLibre wants **[lon, lat]**.
This is the single most common bug in this repo — `cellToBoundary(cell, true)` returns
GeoJSON-order `[lng, lat]` pairs, which is what you feed MapLibre.

### neighbors

```python
h3.grid_disk(cell, 1)      # 7 cells: the cell itself + its 6 neighbours
h3.grid_disk(cell, 2)      # 19 cells
h3.grid_distance(a, b)     # integer ring distance, e.g. 4
h3.cell_to_latlng(cell)    # (lat, lon) centroid
h3.cell_to_boundary(cell)  # 6 (lat, lon) vertices
```

The linker in Phase 5 treats events as spatially related when
`grid_distance(a, b) <= 1` (adjacent or same cell) and spatially *nearby* when
`<= 2`. Anything further apart is not one situation.

### landmarks and zone labels

A cell index means nothing to a resident, so every zone gets a human label derived from
the nearest landmark. These thirteen are the reference set. The complaints feed in
[D.2](#d2-civic_complaints) writes these strings as free text, and `ingest/geocode.py`
resolves them back to coordinates — so this table is both the label source and the
geocoder dictionary.

| Landmark | Hindi | lat | lon | res-8 cell |
|---|---|---|---|---|
| Hawa Mahal | हवा महल | 26.9239 | 75.8267 | `883da21891fffff` |
| Amer Fort | आमेर किला | 26.9855 | 75.8513 | `883da20319fffff` |
| Jal Mahal | जल महल | 26.9535 | 75.8460 | `883da2033dfffff` |
| Albert Hall Museum | अल्बर्ट हॉल | 26.9117 | 75.8197 | `883da218b9fffff` |
| Jaipur Junction | जयपुर जंक्शन | 26.9196 | 75.7878 | `883da218c7fffff` |
| Sindhi Camp | सिंधी कैंप | 26.9268 | 75.7930 | `883da218c3fffff` |
| Vaishali Nagar | वैशाली नगर | 26.9124 | 75.7370 | `883da21801fffff` |
| Malviya Nagar | मालवीय नगर | 26.8549 | 75.8106 | `883da20a6dfffff` |
| Mansarovar | मानसरोवर | 26.8505 | 75.7628 | `883da219e3fffff` |
| Vidyadhar Nagar | विद्याधर नगर | 26.9580 | 75.7810 | `883da21ab9fffff` |
| Tonk Road | टोंक रोड | 26.8830 | 75.8040 | `883da218a7fffff` |
| Jagatpura | जगतपुरा | 26.8260 | 75.8420 | `883da20b13fffff` |
| Sanganer | सांगानेर | 26.8200 | 75.7900 | `883da20b45fffff` |

**Label rule.** For a cell, take its centroid, find the nearest landmark by haversine
distance, and format:

- `label_en` = `"Near Hawa Mahal"`; `label_hi` = `"हवा महल के पास"`
- If the cell *is* the landmark's own cell, drop the "Near": `"Hawa Mahal"` / `"हवा महल"`
- A situation spanning several cells labels itself from the cell with the highest
  severity, and appends the count: `"Near Sindhi Camp + 2 nearby areas"` /
  `"सिंधी कैंप के पास + 2 और क्षेत्र"`

Never show a raw H3 index to a resident. The city view may show it in a tooltip; the
resident view never does. DESIGN.md calls a cell an "Area".

---

## D. Feed list

Five feeds. **The raw formats are deliberately different from each other** — that
mismatch is the problem the project exists to solve, so Phase 1 must generate them
faithfully ugly and Phase 3 must absorb all of it.

| # | Feed id | Raw format | Raw file | Interval | Emits |
|---|---|---|---|---|---|
| 1 | `weather_imd` | JSON lines, **UTC epoch seconds** | `/data/raw_weather_imd.jsonl` | 300 s | `weather.rain`, `weather.heat` |
| 2 | `civic_complaints` | **CSV**, IST text dates, landmark text address | `/data/raw_civic_complaints.csv` | 60 s | all 5 `complaint.*`, `traffic.signal_down` |
| 3 | `power_discom` | JSON lines, **`feeder_id`, no coordinates** | `/data/raw_power_discom.jsonl` | 120 s | `power.outage`, `traffic.signal_down` |
| 4 | `drain_scada` | **SCADA-style** tagged channels, IST text time | `/data/raw_drain_scada.jsonl` | 600 s | `drain.overflow` |
| 5 | `air_sensors` | JSON lines, **one object per sensor** | `/data/raw_air_sensors.jsonl` | 180 s | `air.pm25` |

"Interval" is in simulated time. The `speed` control in [E](#e-api-contract) multiplies
how fast simulated time runs; it does not change these numbers.

A feed's `interval_sec` above is also the unit [E](#e-api-contract)'s `feed_health`
staleness rule is measured in: a feed goes `stale` at `age_sec > 3 x interval_sec`. That
threshold lives in one place, §E, so this section only points to it rather than repeating
the number.

### deduplication rule (read this before writing any parser)

Phase 3 **does** collapse repeats of the same real-world thing from the *same* feed:
a drain gauge polled again during the same overflow ([D.4](#d4-drain_scada)) updates
one open event, and a re-sent weather observation for a station replaces the previous one.

Phase 3 **must never** collapse records that came from **different `source` feeds**, even
when they describe the same thing at the same place and minute.

> **Explicit exception — `traffic.signal_down`.** A dark junction is reported twice on
> purpose: once by `power_discom` (a tripped feeder with `carries_signals: true`) and once
> by `civic_complaints` (a resident). These are two separate canonical events with two
> different `event_id`s, two different `source` values and two different `raw_ref`s, and
> both must reach the engine. Two independent feeds agreeing is the single strongest
> signal the Phase 5 linker has — it is what drives `confidence_level` to `high` (see
> [F](#f-situation-object-shape)). Deduplicating them destroys the evidence the whole
> project exists to produce. If a dedupe pass is keyed on
> `(category, h3_cell, time_bucket)`, it **must** also key on `source`.

The same rule protects any future overlap between feeds. Dedupe within a `source`, never
across them.

### D.1 `weather_imd`

Station observations. There is no notion of an "event" — Phase 3 thresholds the numbers.

```json
{"station_id":"IMD-JAI-03","ts":1758719520,"lat":26.9124,"lon":75.7873,"rain_mm_15min":18.4,"temp_c":31.2,"rh_pct":88,"wind_kph":22}
```

Phase 3 must handle:

- `ts` is **epoch seconds, UTC**. Multiply by nothing; do not assume milliseconds.
- `rain_mm_15min` is a 15-minute accumulation, but records arrive every 5 minutes, so
  consecutive records overlap. Use the latest record per station; do not sum.
- `weather.heat` comes from `temp_c` **and** `rh_pct` combined into a heat index, not
  from `temp_c` alone.
- Below the severity floor, emit nothing at all. A calm station produces no events.
- `start_utc` = `ts`; `received_at` = ingest wall clock.

### D.2 `civic_complaints`

Municipal complaint register export. The messiest feed by design.

```csv
complaint_id,lodged_at,ward,locality,landmark,complaint_type,text,status
JPR-2026-114872,24/09/2026 6:42 PM,Ward 41,Sindhi Camp,Near Sindhi Camp Bus Stand,Water Logging,Knee deep water at bus stand gate 2,OPEN
JPR-2026-114873,24/09/2026 6:44 PM,Ward 41,Sindhi Camp,,water logging  ,,OPEN
JPR-2026-114881,24/09/2026 7:01 PM,Ward 12,Hawa Mahal,Opp Hawa Mahal,Street Light Not Working,3 poles dark since evening,OPEN
```

Phase 3 must handle:

- `lodged_at` is **`DD/MM/YYYY h:mm AM/PM` in IST**, no timezone marker.
  `24/09/2026 6:42 PM` → `2026-09-24T13:12:00Z`. Day comes first, not the month.
- **No coordinates.** Resolve `landmark` against the table in [C](#c-zone-model)
  (case-insensitive, ignoring `Near`/`Opp`/`Behind`/`Nr.` prefixes). If `landmark` is
  empty, fall back to `locality`. If neither resolves, **drop the row** and increment
  `records_dropped` for this feed in `/state`.
- `complaint_type` is free text with inconsistent case and trailing spaces
  (`"Water Logging"`, `"water logging  "`, `"WATERLOGGING"`). Map through one fixed
  synonym table in `ingest/parsers.py`. An unmapped type is a dropped row, not a guess.
- `text` may be empty. Never required.
- `status` is `OPEN` | `CLOSED`. A `CLOSED` row sets `end_utc`.
- `complaint_id` is the `raw_ref` suffix.
- **`text` contains PII.** Real complaint registers are full of contact details, so the
  synthetic ones are too: `text` carries a resident name and a 10-digit Indian mobile
  number mixed into Hinglish free text, e.g.
  `"Ramesh Meena 9829012345 - paani bhar gaya hai gate ke samne"`. All of it is
  generated; none of it refers to a real person.

  Three rules, and they are not optional:

  1. **Canonical events never carry `text`.** `ingest/normalize.py` drops the field
     entirely — there is no PII to leak downstream because the field does not survive
     normalization. The category comes from `complaint_type`, not from the prose.
  2. **`GET /raw/{feed}` masks before returning.** The data room shows
     `"Ramesh M▓▓▓▓ 98▓▓▓▓▓▓▓▓ - paani bhar gaya hai gate ke samne"` — enough to prove
     the scrubber found something, without putting a name and a mobile number on a
     projector. Masking lives in `api/routes.py`, applied on read.
  3. **The file on disk stays unmasked.** `/data/raw_civic_complaints.csv` is the raw
     record; masking it there would defeat the point of having a scrubber to demo.

### D.3 `power_discom`

Distribution company feeder events. Reports circuits, not places.

```json
{"feeder_id":"JVVNL-F-118","substation":"Sindhi Camp 33/11kV","event":"TRIP","reported_time":"2026-09-24T18:44:00","est_restore_min":75,"affected_connections":4120,"carries_signals":true,"signal_junctions":3,"cause":"UNKNOWN"}
```

Phase 3 must handle:

- `reported_time` is **naive local time with no offset** — assume IST and convert.
  It is *not* UTC despite looking ISO-shaped. This is the trap in this feed.
- **No coordinates.** `feeder_id` → lat/lon via a static feeder registry that Phase 1
  generates alongside the feed at `/data/feeder_registry.json`
  (`{"JVVNL-F-118": {"lat": 26.9268, "lon": 75.7930, "name": "Sindhi Camp 33/11kV"}}`).
- `event` is `TRIP` | `RESTORE` | `SCHEDULED_CUT`. A `RESTORE` does not create a new
  event — it sets `end_utc` on the open `power.outage` for the same `feeder_id`.
  `SCHEDULED_CUT` is a real outage but caps severity at `0.4` (it was announced).
- When `carries_signals` is `true`, the same record **also** emits a
  `traffic.signal_down` event at the same location and time, and carries
  `signal_junctions` (int ≥ 1) — the number of junctions left dark, which is the measure
  [A](#a-canonical-event-schema) scales `traffic.signal_down` severity by. The field is
  absent when `carries_signals` is `false`. A resident-reported dark junction from
  [D.2](#d2-civic_complaints) counts as `1`.
- `est_restore_min` is a claim, not a fact. It informs the UI's wording, never `end_utc`.

### D.4 `drain_scada`

Storm-drain (nala) water-level telemetry from JDA/JMC-style gauges, SCADA-shaped.

```json
{"rtu":"JDA-NALA-11","polled":"24/09/2026 18:40","ch":[{"tag":"LVL_CM","v":142.5},{"tag":"BATT_V","v":12.61}]}
```

Phase 3 must handle:

- **No coordinates and no scale.** `rtu` → lat/lon **and** `capacity_cm` (the channel's
  design depth) via `/data/drain_sensor_registry.json`. A level in cm means nothing
  without the depth, so an unknown `rtu` is dropped, never guessed.
- **`polled` is IST wall-clock text, day first**, with no zone marker. Convert to UTC.
- **Readings are tagged channels, not fields.** `LVL_CM` is the level above the channel
  bed; `BATT_V` the RTU battery (below 11.2 V, apply the low-battery multiplier).
- **Faults.** `LVL_CM` of `-999`, or a missing `LVL_CM` channel, is a fault: the record is
  dropped and any open overflow episode closes. A dead gauge is not a drained drain.
- The measure is `level / capacity × 100`, rounded to 0.1. At or over **85%** the drain
  is surcharging and an event opens; every later poll above the floor folds into it.

### D.5 `air_sensors`

A community sensor network. Cheap hardware, so the data is patchy.

```json
{"sensor":"AQ-JPR-07","captured":"2026-09-24T13:14:08Z","pm25":182.4,"pm10":244.0,"loc":{"latitude":26.9239,"longitude":75.8267},"calibrated":false,"battery_pct":41}
```

Phase 3 must handle:

- Coordinates are nested under `loc` with full-word keys `latitude`/`longitude` — not
  `lat`/`lon` like every other feed.
- `pm25` may be `null` or the sentinel `-1` when the sensor is faulty. Drop the record
  and mark that sensor unhealthy; it counts toward `records_dropped`.
- `calibrated: false` sets confidence to `0.60` instead of `0.90`.
- `captured` is already UTC with `Z`, but sensor clocks drift up to ±90 s. Do not
  "correct" it — let `freshness_sec` absorb the drift.
- `battery_pct < 15` halves confidence on top of the calibration rule.

---

## E. API contract

Base URL `http://127.0.0.1:8000`. Everything is JSON, UTF-8, no auth. All timestamps in
responses are UTC (see [H](#h-filenaming-conventions)); the frontend converts to IST at
render time.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | `{"status":"ok"}`. Liveness only. |
| `GET` | `/state` | Current snapshot: active events + situations + feed health. |
| `WS` | `/stream` | Push channel: `event` \| `situation` \| `feedhealth` \| `tick`. |
| `GET` | `/situations/{id}` | One situation with its member events expanded. |
| `GET` | `/raw/{feed}` | Last N **un-normalized** records, for the data room view. |
| `GET` | `/scorecard` | Detected vs ground truth. |
| `POST` | `/control` | Drive the simulation. |

Errors are always `{"error": "<code>", "detail": "<human sentence>"}` with codes
`not_found`, `bad_request`, `not_ready`. `not_ready` means the simulation has not been
started yet — the frontend shows an empty state, not an error toast.

### `GET /state`

The one call a fresh page load makes. After this, everything arrives over `/stream`.

```json
{
  "server_time_utc": "2026-09-24T13:20:00Z",
  "sim": {
    "scenario": "monsoon_evening",
    "state": "play",
    "speed": 4.0,
    "sim_time_utc": "2026-09-24T13:20:00Z",
    "tick": 842
  },
  "city": {
    "pulse_score": 62,
    "alert_level": "orange"
  },
  "counts": {
    "events_active": 37,
    "situations_active": 3,
    "cells_touched": 22
  },
  "events": [
    {
      "event_id": "9f1c4b2e-5a7d-4e18-9c30-16b8ad4e2f77",
      "source": "civic_complaints",
      "category": "complaint.waterlogging",
      "h3_cell": "883da218c3fffff",
      "lat": 26.92680,
      "lon": 75.79300,
      "start_utc": "2026-09-24T13:12:00Z",
      "end_utc": null,
      "severity": 0.58,
      "confidence": 0.60,
      "received_at": "2026-09-24T13:18:43Z",
      "freshness_sec": 403,
      "is_simulated": true,
      "raw_ref": "civic_complaints:JPR-2026-114872"
    }
  ],
  "situations": [
    { "situation_id": "SIT-4a91c2", "alert_level": "orange", "...": "full object, see section F" }
  ],
  "feed_health": [
    {
      "feed": "civic_complaints",
      "state": "live",
      "last_record_utc": "2026-09-24T13:19:40Z",
      "age_sec": 20,
      "interval_sec": 60,
      "records_total": 418,
      "records_dropped": 11,
      "message": null
    },
    {
      "feed": "drain_scada",
      "state": "killed",
      "last_record_utc": "2026-09-24T13:04:10Z",
      "age_sec": 950,
      "interval_sec": 30,
      "records_total": 1602,
      "records_dropped": 0,
      "message": "Stopped by operator"
    }
  ]
}
```

`city` is the roll-up defined in [F](#f-situation-object-shape) — the status block renders
it directly and computes nothing. With no active situations it is
`{"pulse_score": 0, "alert_level": "green"}`.

`events` contains **active events only** — those with `end_utc == null`, or whose
`end_utc` is within the last 30 minutes of simulated time. History lives in
`/data/events.jsonl` and SQLite, not here.

`feed_health[].state` is `live` | `stale` | `killed` | `error`:

- `live` — `age_sec <= 3 × interval_sec`
- `stale` — `age_sec > 3 × interval_sec`, nobody killed it, it just stopped arriving
- `killed` — an operator called `/control` with `kill_feed`
- `error` — the parser is throwing; `message` says what

### `WS /stream`

Connect and receive. The server pushes; the client never sends on this socket (use
`POST /control` instead). Every message has `type` and `sent_utc`.

```json
{"type":"event","sent_utc":"2026-09-24T13:18:43Z","data":{"event_id":"9f1c4b2e-5a7d-4e18-9c30-16b8ad4e2f77","source":"civic_complaints","category":"complaint.waterlogging","h3_cell":"883da218c3fffff","lat":26.92680,"lon":75.79300,"start_utc":"2026-09-24T13:12:00Z","end_utc":null,"severity":0.58,"confidence":0.60,"received_at":"2026-09-24T13:18:43Z","freshness_sec":403,"is_simulated":true,"raw_ref":"civic_complaints:JPR-2026-114872"}}
```

```json
{"type":"situation","sent_utc":"2026-09-24T13:19:02Z","action":"created","data":{"situation_id":"SIT-4a91c2","alert_level":"orange","...":"full object, see section F"}}
```

`action` is `created` | `updated` | `closed`. `created` is the only one that triggers
the line-drawing animation in DESIGN.md.

```json
{"type":"feedhealth","sent_utc":"2026-09-24T13:19:10Z","data":{"feed":"drain_scada","state":"killed","last_record_utc":"2026-09-24T13:04:10Z","age_sec":950,"interval_sec":600,"records_total":1602,"records_dropped":0,"message":"Stopped by operator"}}
```

```json
{"type":"tick","sent_utc":"2026-09-24T13:19:11Z","data":{"sim_time_utc":"2026-09-24T13:19:11Z","tick":843,"speed":4.0,"state":"play","events_active":37,"situations_active":3,"city_pulse_score":62,"city_alert_level":"orange"}}
```

`tick` is sent once per **real** second regardless of `speed`, and is what drives the
Naadi pulse strip, the clock and the status block. `city_pulse_score` and
`city_alert_level` repeat the `/state` `city` block so the status word stays live without
a refetch — same roll-up, same source, never recomputed on the client. If three ticks are missed the frontend shows
"Not connected" and attempts to reconnect.

### `GET /situations/{id}`

The situation object from [F](#f-situation-object-shape), plus `member_events`: the full
canonical event objects for every id in `member_event_ids`, sorted by `start_utc`
ascending. This is what the "Why do we think this?" timeline reads. Unknown id → 404
with `{"error":"not_found","detail":"No situation SIT-abc123"}`.

### `GET /raw/{feed}?n=50`

`{feed}` is one of the five feed ids. `n` defaults to 50, max 500. Records are returned
**exactly as they arrived**, newest first, with nothing cleaned — that is the whole point
of the data room.

```json
{
  "feed": "civic_complaints",
  "format": "csv",
  "count": 2,
  "records": [
    {
      "raw_ref": "civic_complaints:JPR-2026-114873",
      "received_at": "2026-09-24T13:20:01Z",
      "parsed_ok": false,
      "reason": "landmark and locality both unresolvable",
      "raw": "JPR-2026-114873,24/09/2026 6:44 PM,Ward 41,Sindhi Camp,,water logging  ,,OPEN"
    },
    {
      "raw_ref": "civic_complaints:JPR-2026-114872",
      "received_at": "2026-09-24T13:18:43Z",
      "parsed_ok": true,
      "reason": null,
      "raw": "JPR-2026-114872,24/09/2026 6:42 PM,Ward 41,Sindhi Camp,Near Sindhi Camp Bus Stand,Water Logging,Knee deep water at bus stand gate 2,OPEN"
    }
  ]
}
```

`format` is `csv` or `json`. When it is `csv`, `raw` is the literal line as a string and
the response also carries `"header"` with the column line. When it is `json`, `raw` is
the original object, unmodified.

### `GET /scorecard`

```json
{
  "scenario": "monsoon_evening",
  "generated_utc": "2026-09-24T13:20:00Z",
  "truth_situations": 5,
  "detected_situations": 6,
  "matched": 4,
  "missed": 1,
  "false_positives": 2,
  "decoys_planted": 3,
  "decoys_correctly_ignored": 2,
  "precision": 0.67,
  "recall": 0.80,
  "f1": 0.73,
  "alert_level_accuracy": 0.75,
  "median_detection_lag_sec": 142,
  "per_situation": [
    {
      "truth_id": "GT-001",
      "matched_situation_id": "SIT-4a91c2",
      "status": "matched",
      "expected_alert_level": "orange",
      "detected_alert_level": "orange",
      "detection_lag_sec": 142,
      "member_overlap": 0.83
    },
    {
      "truth_id": "GT-004",
      "matched_situation_id": null,
      "status": "missed",
      "expected_alert_level": "yellow",
      "detected_alert_level": null,
      "detection_lag_sec": null,
      "member_overlap": 0.0
    }
  ]
}
```

`status` is `matched` | `missed` | `false_positive` | `decoy_ignored` | `decoy_alerted`.
The matching rule is defined in [G](#g-scenario--ground-truth-format) and must be
implemented once, in `engine/scorecard.py`.

### `POST /control`

Request:

```json
{"action": "speed", "value": 8.0}
```

| `action` | `value` | Effect |
|---|---|---|
| `play` | omitted / `null` | Resume the simulation clock. |
| `pause` | omitted / `null` | Freeze it. Ticks keep flowing with `state: "paused"`. |
| `speed` | float, one of `1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 100.0, 200.0` | Simulated seconds per real second. Extended to 200x for the pitch demo (3h window in ~54s). |
| `kill_feed` | feed id string | Stop that feed. Its health goes `killed`. Apply a serve-time confidence penalty (one level down) to any active situation with a member event from that feed, with a human-readable reason. |
| `resume_feed` | feed id string | Start it again. Reverse any kill_feed penalty, restore original confidence. |
| `set_scenario` | scenario name string | Reset everything and load that scenario. |
| `jump_to` | bookmark name string or UTC timestamp | Jump the clock to a named bookmark (`window_start`, `storm_onset`, `first_situation`, `feed_kill_demo_point`, `gt002_onset`, `gt003_onset`, `peak_activity`, `window_end`) or an arbitrary UTC timestamp. Recomputes served state as-of that time — same result as linear play reaching it. |
| `delay_feed` | `{"source": "<feed_id>", "seconds": <int>}` | **Phase 6 addition.** Simulates a feed running behind: events from that source are held and revealed `seconds` later than their real timestamp until resumed. |
| `inject_duplicate` | event_id string | **Phase 6 addition.** Re-serves an already-revealed event over WS with a new `received_at` but the same `event_id`, tagged `is_duplicate: true`. The frontend/data-room recognizes and doesn't double-count it. |

Response is always the `sim` block plus what changed, so the UI needs no second call:

```json
{"ok": true, "sim": {"scenario":"monsoon_evening","state":"play","speed":8.0,"sim_time_utc":"2026-09-24T13:20:00Z","tick":842}}
```

Bad action or value → 400 `{"error":"bad_request","detail":"speed must be one of 1, 2, 4, 8, 16, 32, 64, 100, 200"}`.

---

## E.1 Anomaly record shape

Phase 4's output: one record per (h3_cell, category, 60-minute window) that the Poisson
tail test flags as statistically unusual. This is the evidence Phase 5 links into
[situations](#f-situation-object-shape) -- a situation's `member_event_ids` are drawn
from anomalies' `contributing_event_ids`, not picked directly from raw canonical events.

Written to `/data/anomalies.jsonl` ([H](#h-filenaming-conventions)). Detection code never
reads the answer key; only its own verification script may.

```json
{
  "anomaly_id": "ANOM-7f3a91c2",
  "h3_cell": "883da218c3fffff",
  "category": "complaint.waterlogging",
  "window_start_utc": "2026-09-24T13:00:00Z",
  "window_end_utc": "2026-09-24T14:00:00Z",
  "observed_count": 5,
  "expected_count": 0.42,
  "p_value": 0.00071,
  "severity_weighted": 1.83,
  "contributing_event_ids": [
    "9f1c4b2e-5a7d-4e18-9c30-16b8ad4e2f77",
    "1b0e33a4-0c2f-4a51-9ad6-6f2b7c1d9e10"
  ],
  "source_feeds": ["civic_complaints"],
  "degraded_by_stale_feeds": []
}
```

### field rules

| Field | Rule |
|---|---|
| `anomaly_id` | `"ANOM-"` + 8 lowercase hex chars, **derived**, not random: `sha1(f"{h3_cell}\|{category}\|{window_start_utc}")[:8]`. Same window, same id, on every run -- required for byte-identical regeneration. |
| `h3_cell` | The res-8 cell this window covers. One anomaly never spans multiple cells. |
| `category` | One of the 11 ids in [B](#b-category-enum). One anomaly never spans multiple categories -- this is what keeps `severity_weighted` and the Poisson test meaningful. |
| `window_start_utc` / `window_end_utc` | UTC ISO8601, `Z`. A rolling 60-minute window; `window_end_utc - window_start_utc` is always 3600s. |
| `observed_count` | Count of canonical events of this `category` starting inside the window, in this cell. |
| `expected_count` | The learned rate `λ` for this `(h3_cell, category, hour-of-day)` from the 14-day history, scaled to the window length. Never zero -- see the fallback rule below. |
| `p_value` | `scipy.stats.poisson.sf(observed_count - 1, expected_count)`: P(count ≥ observed \| λ = expected). Smaller is more unusual. |
| `severity_weighted` | Sum of `severity` over every contributing event, not just the count -- five low-severity reports and one severe one are not interchangeable, and this field is what lets Phase 5 and the scorecard tell them apart. |
| `contributing_event_ids` | Every canonical `event_id` inside the window that this anomaly is counting. Non-empty whenever `observed_count > 0`. |
| `source_feeds` | Distinct `source` values among the contributing events, sorted. For `traffic.signal_down`, seeing **both** `power_discom` and `civic_complaints` here is the corroboration signal Phase 5 raises `confidence_level` on -- see the [dedupe carve-out](#deduplication-rule-read-this-before-writing-any-parser). |
| `degraded_by_stale_feeds` | Feed ids (from [D](#d-feed-list)) that emit this `category` and were reported `stale` by [feed health](#e-api-contract) for any part of the window. Empty list when nothing was degraded. A non-empty list means `observed_count` may be an undercount -- Phase 5 and any UI must show this rather than treat the anomaly as fully trustworthy. |

### baseline learning and the Poisson test (fixed)

- λ is learned per `(h3_cell, category, hour-of-day IST)` from the **14-day history
  portion only** (never the detection window, or a real anomaly shrinks its own baseline).
- λ uses **Gamma-Poisson shrinkage**, not a hard fallback ladder:
  `λ = (count_in_bucket + α × λ_prior) / (n_days + α)`, with `λ_prior` the city-wide
  rate for that `(category, hour)` and `α = PRIOR_STRENGTH_DAYS`. A cell with real
  history is judged mostly on its own rate; a cell with none falls back smoothly to the
  city-wide prior rather than to near-zero. Never divide by zero, never skip a bucket.

### the two triggers (fixed)

A cascade is **one** rain onset, **one** power outage and **two** dark junctions. None of
those can ever reach a count of three, so a volume-only rule makes the entire headline
scenario undetectable by construction -- measured: 27% recall, with the causal backbone
of every planted cascade missing. Hence two rules:

| Trigger | Rule | Catches |
|---|---|---|
| `volume` | `observed_count >= ANOMALY_MIN_COUNT` **and** `p_value < ANOMALY_P_THRESHOLD` | a cluster of one category in one cell |
| `rare` | `observed_count >= 1` **and** `p_value < ANOMALY_RARE_P_THRESHOLD` **and** `severity_weighted >= ANOMALY_RARE_MIN_SEVERITY` **and** `category in ANOMALY_RARE_ELIGIBLE_CATEGORIES` | a single consequential event where that category essentially never happens |

All constants live in `backend/contract_constants.py`. Never re-type them at a call site.

Three things the `rare` rule deliberately requires beyond a small p-value, each of which
was measured rather than assumed:

- **A severity floor.** Statistical oddity alone is not enough; the event must also
  matter. Planted cascade events carry median `severity_weighted` 0.50 while rare-trigger
  false positives sit at 0.06, so severity separates the two populations far better than
  the p-value does. Adding this gate cut the false-alarm rate from z = +8.1 to z = +1.3
  while costing only 9 points of raw recall.
- **Category eligibility.** `weather.heat` and `air.pm25` are sustained sensor
  conditions -- a hot afternoon is hot for hours -- so their counts are strongly
  correlated and badly over-dispersed relative to Poisson. A single crossing there is
  not surprising, and flagging one produced most of the false positives. They remain
  eligible for the `volume` trigger, which is what a genuine heat or pollution cluster
  looks like.
- **An empirically calibrated threshold.** Civic data violates Poisson independence, so
  the *nominal* p-value is not the achieved false-alarm rate -- measured directly, a
  nominal `p < 0.01` rule ran roughly 3x hot. `ANOMALY_RARE_P_THRESHOLD` is therefore a
  starting point, and `engine/calibrate.py` re-fits it on a held-out ordinary day so the
  measured rate meets the target. The fitted value, not the nominal one, is what runs.
  Fitting and verification use different days, so the reported rate is not flattered.

### known limitation

A count-based Poisson test detects *unusual frequency*, not *unusual magnitude*. One
exceptionally severe reading of an otherwise common category is invisible to it --
concretely, GT-003's `air.pm25` event is a moderate elevation of a category that fires
often, so it is not flagged and Phase 5 cannot draw that step. Closing this needs a
magnitude-aware test (is this reading's *value* unusual for this cell), which is a
different detector from the one specified here.

---

## F. Situation object shape

A situation is the whole product: a few plain-language things a resident can understand
in ten seconds. Everything else in this repo exists to produce these.

```json
{
  "situation_id": "SIT-4a91c2",
  "created_utc": "2026-09-24T13:19:02Z",
  "updated_utc": "2026-09-24T13:24:11Z",
  "status": "active",
  "pulse_score": 62,
  "alert_level": "orange",
  "headline_en": "Heavy rain near Sindhi Camp has left a signal dark",
  "headline_hi": "सिंधी कैंप के पास जलभराव से बसें रुकी हैं",
  "zone": {
    "label_en": "Near Sindhi Camp + 2 nearby areas",
    "label_hi": "सिंधी कैंप के पास + 2 और क्षेत्र",
    "h3_cells": ["883da218c3fffff", "883da218c7fffff", "883da218c1fffff"],
    "centroid": { "lat": 26.92737, "lon": 75.79265 }
  },
  "member_event_ids": [
    "1b0e33a4-0c2f-4a51-9ad6-6f2b7c1d9e10",
    "9f1c4b2e-5a7d-4e18-9c30-16b8ad4e2f77",
    "c7d2f810-3e4b-4c9a-8f61-2a5d0b3e7c44"
  ],
  "chain": [
    {
      "step": 1,
      "event_id": "1b0e33a4-0c2f-4a51-9ad6-6f2b7c1d9e10",
      "t_utc": "2026-09-24T12:55:00Z",
      "category": "weather.rain",
      "h3_cell": "883da218c7fffff",
      "text_en": "Heavy rain started near Jaipur Junction",
      "text_hi": "जयपुर जंक्शन के पास तेज़ बारिश शुरू हुई"
    },
    {
      "step": 2,
      "event_id": "9f1c4b2e-5a7d-4e18-9c30-16b8ad4e2f77",
      "t_utc": "2026-09-24T13:12:00Z",
      "category": "complaint.waterlogging",
      "h3_cell": "883da218c3fffff",
      "text_en": "17 minutes later, residents reported waterlogging one area away",
      "text_hi": "17 मिनट बाद, पास के क्षेत्र से जलभराव की शिकायत आई"
    },
    {
      "step": 3,
      "event_id": "c7d2f810-3e4b-4c9a-8f61-2a5d0b3e7c44",
      "t_utc": "2026-09-24T13:18:00Z",
      "category": "drain.overflow",
      "h3_cell": "883da218c3fffff",
      "text_en": "6 minutes later, the Sindhi Camp trunk drain was running over capacity",
      "text_hi": "6 मिनट बाद, उसी स्टॉप पर 22A बसें 14 मिनट देरी से चलीं"
    }
  ],
  "evidence": {
    "spatial": {
      "h3_cells": ["883da218c3fffff", "883da218c7fffff", "883da218c1fffff"],
      "cells_involved": 3,
      "max_grid_distance": 1,
      "note_en": "All three reports came from adjacent areas"
    },
    "temporal_gaps": [
      { "from_event_id": "1b0e33a4-0c2f-4a51-9ad6-6f2b7c1d9e10", "to_event_id": "9f1c4b2e-5a7d-4e18-9c30-16b8ad4e2f77", "gap_sec": 1020 },
      { "from_event_id": "9f1c4b2e-5a7d-4e18-9c30-16b8ad4e2f77", "to_event_id": "c7d2f810-3e4b-4c9a-8f61-2a5d0b3e7c44", "gap_sec": 360 }
    ],
    "lift": {
      "value": 6.4,
      "pair": ["weather.rain", "drain.overflow"],
      "window_sec": 3600,
      "baseline_rate_per_hour": 0.12,
      "observed_rate_per_hour": 0.77,
      "note_en": "These normally appear together about once every 8 hours here"
    }
  },
  "confidence_level": "high",
  "confidence_reason_en": "Three separate feeds, all within one area, in the expected order",
  "confidence_reason_hi": "तीन अलग-अलग फीड, एक ही क्षेत्र में, अपेक्षित क्रम में",
  "is_decoy": false,
  "predicted_next": {
    "category": "traffic.signal_down",
    "typical_lag_range_sec": [0, 1800],
    "based_on": "6 historical co-occurrences"
  }
}
```

### field rules

| Field | Rule |
|---|---|
| `situation_id` | `"SIT-"` + 6 lowercase hex chars. Stable across `updated` messages. |
| `status` | `active` \| `closed`. Closed when every member event has an `end_utc`. |
| `pulse_score` | Integer `0`–`100`. **The source of truth for how bad this is.** Formula below. |
| `alert_level` | `green` \| `yellow` \| `orange` \| `red`. Derived from `pulse_score` by the engine. Clients render it; clients never compute it. |
| `headline_en` / `headline_hi` | One sentence, sentence case, no jargon. What a resident would say. |
| `zone.h3_cells` | Every cell any member event sits in. Order is irrelevant. |
| `member_event_ids` | Unordered set. Use `chain` when order matters. |
| `chain` | The numbered timeline. `step` starts at 1 and is contiguous. Sorted by `t_utc` ascending. `text_en` states the gap from the previous step in plain words. |
| `evidence.spatial` | Which cells, how many, and the largest `grid_distance` between any two. |
| `evidence.temporal_gaps` | Consecutive pairs along `chain`, in seconds. Length is always `len(chain) − 1`. |
| `evidence.lift` | Observed co-occurrence rate ÷ baseline rate for the strongest category pair. `value` above `1.0` means "more together than usual". |
| `confidence_level` | `low` \| `med` \| `high`. Rule below. |
| `confidence_reason_*` | One sentence naming the actual reason. Never "high confidence score". |
| `is_decoy` | `true` when the engine believes the cluster is coincidence. Decoys still get returned — the UI puts them in the "probably unrelated" section rather than hiding them. |
| `predicted_next` | **New, Phase 5.** `null`, or `{category, typical_lag_range_sec: [min,max], based_on}` naming the ONE category [§F.1](#f1-linking-phase-5)'s plausibility table says commonly follows this situation's most recent member, when it has not shown up yet. One hop only — never a prediction of a prediction. Worded in the UI as a pattern ("often follows within N minutes"), never a certainty. |

### pulse_score → alert_level (fixed)

There is **one** number in this system and **one** set of cutoffs. `pulse_score` is
computed once, by the engine, and `alert_level` is derived from it. The UI never shows
both and never recomputes either.

```
raw   = max(severity of member events) × 0.6
      + mean(confidence of member events) × 0.2
      + min(count of distinct source feeds, 3) / 3 × 0.2      # raw is 0.0–1.0

pulse_score = round(raw × 100)                                 # integer 0–100
```

| `pulse_score` | `alert_level` | Status word (DESIGN.md) |
|---|---|---|
| `0 – 24` | `green` | All normal |
| `25 – 49` | `yellow` | Be aware |
| `50 – 74` | `orange` | Be prepared |
| `75 – 100` | `red` | Take action |

**Source of truth:** `pulse_score` is authoritative; `alert_level` is a label for it.

**Clients must render `alert_level` as given and must never re-derive it from
`pulse_score`.** There is one deliberate case where the two disagree: a situation with
`is_decoy: true` is **capped at `yellow`** after thresholding, so a decoy can carry
`pulse_score: 71` with `alert_level: "yellow"`. That is correct and intentional. A client
that re-applies the cutoffs will color it orange and contradict its own label on stage.

`pulse_score` is an integer so it can be shown directly, with tabular figures, without
formatting decisions. It is not a percentage and carries no `%` sign.

### city-wide and per-cell derivation (fixed)

The same number, rolled up. Both are computed **server-side** and shipped in `/state`, so
the map (Phase 8) and the resident view (Phase 9) cannot disagree.

| Level | `pulse_score` | `alert_level` |
|---|---|---|
| **Situation** | the formula above | thresholds above, decoy-capped |
| **Cell** (one H3 area) | `max` of `pulse_score` across active non-decoy situations whose `zone.h3_cells` include this cell | thresholds applied to that max |
| **City** (the status block) | `max` of `pulse_score` across all active non-decoy situations | thresholds applied to that max |

Rules that follow from this:

- **Decoys never color anything.** They are excluded from both roll-ups — a cell whose
  only situation `is_decoy` renders as having no situation. Decoy cards still appear in
  the "probably unrelated" section.
- **A cell with events but no situation has no fill.** It shows its event dots on the
  `--chuna` background and nothing more. Absence of a situation is not `green`; `green`
  is a positive claim that we looked and things are normal.
- **City `green` with zero active situations** is the correct resting state, and the
  status block says "Nothing unusual right now" ([DESIGN.md §1](DESIGN.md#component-specs)).

### confidence_level rule (fixed)

| Condition | `confidence_level` |
|---|---|
| 3+ distinct `source` feeds, `max_grid_distance <= 1`, all gaps under 30 min | `high` |
| 2 distinct feeds, or one gap over 30 min | `med` |
| Single feed, or `max_grid_distance == 2`, or any member confidence below 0.5 | `low` |

---

## F.1 Linking (Phase 5)

How [§E.1](#e1-anomaly-record-shape) anomalies become [§F](#f-situation-object-shape)
Situations. `backend/engine/plausibility.py`, `engine/lift.py`, `engine/linker.py`,
`engine/situations.py`.

### the plausibility table

Hand-written domain knowledge, not learned — fourteen days of baseline noise is nowhere
near enough data to *learn* that a tripped feeder darkens traffic signals, but every
electrical engineer in the room already knows it. `min_gap` is `0`, not `1`: a DISCOM
feeder trip emits its `power.outage` and its `traffic.signal_down` from the **same raw
record at the same instant** ([§D.3](#d3-power_discom)), so a strictly-positive minimum
would reject the single most certain link in the system.

| Cause | Effect | Gap (min) | Why |
|---|---|---|---|
| `complaint.smoke` | `air.pm25` | 0–30 | burning close by pushes particulate readings up downwind |
| `complaint.waterlogging` | `complaint.road_damage` | 0–240 | water under the surface breaks the road up |
| `complaint.waterlogging` | `power.outage` | 0–60 | water in a street-level substation or feeder pillar trips it |
| `power.outage` | `complaint.streetlight` | 0–60 | the same dead feeder takes the street lights with it |
| `power.outage` | `traffic.signal_down` | 0–30 | a tripped feeder carrying signal circuits leaves junctions dark |
| `weather.heat` | `power.outage` | 0–180 | peak cooling load on a hot afternoon overloads distribution feeders |
| `weather.rain` | `complaint.road_damage` | 0–240 | standing water opens up potholes that residents then report |
| `weather.rain` | `complaint.waterlogging` | 0–90 | heavy rain pools in low-lying streets within the hour |
| `weather.rain` | `power.outage` | 0–120 | water reaching a feeder or transformer trips the circuit |
| `weather.rain` | `drain.overflow` | 0–60 | a burst of rain fills the storm drains faster than they can carry it away |
| `drain.overflow` | `complaint.waterlogging` | 0–60 | a surcharged drain backs up and spills onto the street |
| `drain.overflow` | `complaint.road_damage` | 0–240 | water forced out of a full drain undermines the road beside it |

`complaint.garbage` appears on **neither side of any edge**, deliberately. Uncollected
garbage is an accumulation condition measured in days, not an event with minute-scale
causes or effects among the other ten categories — putting an edge from it to
`complaint.smoke` (say) would let a routine, unrelated garbage backlog absorb an actual
fire report just because both happened to be nearby. `air.pm25`, `traffic.signal_down` and
`complaint.streetlight` are **effect-only**: they never start a chain (see "standalone
situations" below) — a light or a signal fails for reasons a civic feed cannot see,
so a lone cluster of them is never, on its own, claimed as a situation.

### lift

For each plausible pair, `engine/lift.py` measures how much more often it co-occurs in
the 14-day history than a local chance model predicts:

```
lift = (observed co-occurrences + 0.5) / (chance-expected co-occurrences + 0.5)
```

Chance is **localized**, not city-uniform: for each cause event, the expected count
comes from how many effect-category events the cause's own 7-cell neighbourhood held
over the whole history, spread flat over that span — a sensor that is locally common is
correctly unsurprising locally. Values above `1.0` mean "more together than chance
here"; below `1.0` means "no more than chance."

**Lift never gates a link — only the plausibility table and the time window do.**
Phase 1 generates the 14-day history as deliberately stationary noise with **no**
planted causal structure (`sim.verify` check (e) hard-fails the build otherwise), so
most genuinely plausible pairs measure *below* 1.0 there: `0.44` for
`complaint.waterlogging → power.outage`, and below 1.0 for `weather.rain →
power.outage` too — both real legs of GT-001's own planted cascade. A lift threshold near
or above 1.0 would veto legs of the headline scenario. (The one pair that IS
structurally deterministic — `power.outage → traffic.signal_down`, one raw record
emitting both at the same instant — measures a lift near 44 even in stationary noise;
it was never at risk from a lift gate. It is the *low*-lift real pairs that make gating
on lift the wrong call with 14 days of synthetic history.) `LINK_MIN_LIFT` ships at
`0.0` for this reason and stays wired for a deployment with enough real history to
turn it on. Lift is still scored into link strength and reported as
[`evidence.lift`](#f-situation-object-shape) on every situation.

### candidate links and scoring

Two episodes (Phase 4's overlapping anomaly windows for one `(h3_cell, category)`,
merged into one evidence node per burst) are a link candidate when, for some ordering:

1. the pair is in the plausibility table above;
2. `grid_distance(cell_a, cell_b) <= 1` ([§C](#c-zone-model): "spatially related");
3. the **closest-fitting pair of actual contributing events** (not a single fixed
   per-episode timestamp — an episode can span more than one burst, and using its
   raw-earliest event risks anchoring on an unrelated one) falls inside the pair's gap
   window.

Accepted links are scored `0.4·space + 0.4·time-fit + 0.2·lift` (all `0`–`1`) and
clustered into situations by connected components. The closest-gap edge into a
non-root episode also fixes its **anchor event** — the specific event that earned it a
place in the cascade — which is what the [`chain`](#f-situation-object-shape) step's
timestamp and text point at, and what the episode's evidence is pruned around (events
more than [`ANOMALY_WINDOW_SEC`](#e1-anomaly-record-shape) — 60 minutes — from the
anchor are excluded from that situation's membership; they were not evidence for this
particular link, even though Phase 4 flagged them in the same rolling window).

### standalone situations

A Situation **may consist of a single anomaly with no link partner** — this is the
resolution for a real cascade whose downstream step is undetectable by Phase 4's
count-based test (the [§E.1 known limitation](#e1-anomaly-record-shape): GT-003's
magnitude-only `air.pm25` elevation never crosses the count threshold, so nothing can
link to it). Three gates, all required (`contract_constants.py`):

1. **root-capable** — the category has at least one outgoing edge above. A lone
   occurrence of an effect-only category carries no more claim to being "a situation"
   than any other single report.
2. **discrete-incident category** — the same `ANOMALY_RARE_ELIGIBLE_CATEGORIES`
   exclusion Phase 4's rare trigger uses (`weather.heat`, `air.pm25` excluded): a
   single crossing of a sustained, city-wide condition is not a localized event.
3. `severity_weighted >= STANDALONE_MIN_SEVERITY_WEIGHTED` (`0.25`).

A standalone situation's `confidence_level` is **capped at `med`** — it lacks the
multi-source corroboration [§F](#f-situation-object-shape)'s confidence table rewards,
structurally, not as an afterthought.

### confidence addendum

[§F](#f-situation-object-shape)'s confidence table is unchanged. Two rules resolve
cases it left implicit:

- **Boundary.** "All gaps under 30 min" for `high` and "one gap over 30 min" for `med`
  leaves exactly 1800s undefined. Resolved **inclusive**: a gap of exactly 30 minutes
  is still `high`. (GT-002 lands on this boundary exactly.)
- **Degradation.** A situation depending on a feed Phase 4 reported `degraded_by_stale_feeds`
  for **drops one confidence level** (`high→med→low`, floor at `low`), with the reason
  recorded, e.g. *"civic complaints feed stale 6 min, confidence lowered."* This is
  checked once, after the base level and the standalone cap — never twice.
- **Source counting** is over the situation's member events' `source` values, not its
  episodes — a `traffic.signal_down` episode alone carrying both `power_discom` and
  `civic_complaints` (the [§D dedupe carve-out](#deduplication-rule-read-this-before-writing-any-parser))
  already counts as two independent sources agreeing, exactly the corroboration this
  table is built to reward.

### rejected candidates (new — `/data/rejected_candidates.jsonl`)

Every pair of episodes that came close enough in space (`grid_distance <= 2`) and time
(within 90 minutes) to be worth explaining, but did **not** end up in the same
situation. This is the system's own judgment on a near-miss — **not** the same thing as
a ground-truth decoy, which this code never reads; Phase 10's scorer compares the two
separately ("decoys correctly ignored"). Feeds Phase 8's "probably unrelated" section
directly.

```json
{
  "rejected_id": "REJ-04e06ea4",
  "anomaly_ids": ["ANOM-1cf88f38", "ANOM-9c14cc8b"],
  "categories": ["complaint.garbage", "complaint.smoke"],
  "h3_cells": ["883da21801fffff"],
  "gap_sec": 300,
  "grid_distance": 0,
  "reason_en": "complaint.garbage and complaint.smoke overlapped in time and space but this category pair is not in the plausibility table",
  "reason_hi": "complaint.garbage और complaint.smoke समय और स्थान में साथ थे लेकिन यह जोड़ी प्रशंसनीयता तालिका में नहीं है"
}
```

| Field | Rule |
|---|---|
| `rejected_id` | `"REJ-"` + 8 lowercase hex, derived: `sha1("\|".join(sorted(anomaly_ids)))[:8]`. |
| `anomaly_ids` | The Phase 4 anomalies behind both episodes, sorted. |
| `categories` / `h3_cells` | Sorted, deduplicated — one or two of each. |
| `gap_sec` / `grid_distance` | The closest-fitting event pair's gap, and the cell distance. |
| `reason_en` / `reason_hi` | One sentence: category pair not in the table, gap outside the plausible window, or areas not adjacent. |

### predicted_next

See [§F](#f-situation-object-shape)'s field rule. One hop from the situation's most
recent chain step, using the same plausibility table above; only emitted when the
history holds at least one instance of the situation's own category actually
occurring (`n_cause > 0` in the lift table) — never a guess with nothing behind it.

---

## G. Scenario + ground-truth format

Phase 2 **writes** `/data/ground_truth.json` when it plants a scenario. Phase 10
**reads** it and nothing else. Neither may change this shape alone.

Ground truth is never served to the frontend except through `/scorecard`. The map and
the resident view must never see it — that would be cheating at our own demo.

```json
{
  "scenario": "monsoon_evening",
  "description": "A monsoon storm cell crossing the city in the evening rush",
  "generated_utc": "2026-09-24T12:00:00Z",
  "sim_start_utc": "2026-09-24T12:00:00Z",
  "sim_end_utc": "2026-09-24T15:00:00Z",
  "planted_situations": [
    {
      "truth_id": "GT-001",
      "label": "Cloudburst over Sindhi Camp overflows drains, floods the bus stand and trips a feeder that darkens signals",
      "root_cause_category": "weather.rain",
      "expected_chain": ["weather.rain", "drain.overflow", "complaint.waterlogging", "power.outage", "traffic.signal_down"],
      "expected_alert_level": "orange",
      "expected_zone_cells": ["883da218c3fffff", "883da218c7fffff"],
      "member_event_ids": [
        "1b0e33a4-0c2f-4a51-9ad6-6f2b7c1d9e10",
        "9f1c4b2e-5a7d-4e18-9c30-16b8ad4e2f77",
        "c7d2f810-3e4b-4c9a-8f61-2a5d0b3e7c44"
      ],
      "onset_utc": "2026-09-24T12:55:00Z",
      "detect_by_utc": "2026-09-24T13:25:00Z"
    }
  ],
  "decoys": [
    {
      "decoy_id": "DC-001",
      "label": "Routine garbage complaints in Mansarovar happen to land during the storm",
      "member_event_ids": [
        "5e8a1c30-9b47-4d2e-a016-73c9f5b2e881",
        "a2470d1f-6c85-4b93-8e20-11d4a7c6f309"
      ],
      "why_unrelated": "Same hour, 9 km away, and garbage complaints run at this rate every evening",
      "must_not_alert_above": "yellow"
    }
  ]
}
```

### field rules

| Field | Rule |
|---|---|
| `truth_id` | `"GT-"` + 3 digits, starting `GT-001`. |
| `decoy_id` | `"DC-"` + 3 digits. |
| `member_event_ids` | The **exact** `event_id`s Phase 2 planted. Phase 2 therefore generates the uuids before writing the raw feeds, so the same id survives into normalization via `raw_ref`. |
| `expected_chain` | Category ids in the intended causal order. The scorecard compares this to the detected `chain` for `alert_level_accuracy`, not for matching. |
| `onset_utc` | When the first member event starts. |
| `detect_by_utc` | The deadline. Detecting after this counts as `missed`, however good the match. |
| `must_not_alert_above` | A decoy that produces a situation above this level counts as `decoy_alerted`, which hurts precision. |

### matching rule (implement once, in `engine/scorecard.py`)

A detected situation **matches** a planted one when **both** hold:

1. `|detected.member_event_ids ∩ truth.member_event_ids| / |truth.member_event_ids| >= 0.5`
2. `detected.created_utc <= truth.detect_by_utc`

Each truth situation matches at most one detected situation — the one with the highest
overlap. Detected situations left over are `false_positive`, unless they correspond to a
decoy, in which case they are `decoy_alerted`. `detection_lag_sec` is
`detected.created_utc − truth.onset_utc`.

---

## H. File/naming conventions

### where data lands

Everything generated lives in `/data`, which is **gitignored**. Nothing in `/data` is
ever hand-edited; regenerate instead.

| Path | Format | Written by |
|---|---|---|
| `/data/raw_weather_imd.jsonl` | JSON Lines | Phase 1 |
| `/data/raw_civic_complaints.csv` | CSV with a header row | Phase 1 |
| `/data/raw_power_discom.jsonl` | JSON Lines | Phase 1 |
| `/data/raw_drain_scada.jsonl` | JSON Lines | Phase 1 |
| `/data/raw_air_sensors.jsonl` | JSON Lines | Phase 1 |
| `/data/feeder_registry.json` | JSON object | Phase 1 |
| `/data/drain_sensor_registry.json` | JSON object | Phase 1 |
| `/data/ground_truth.json` | JSON object | Phase 2 |
| `/data/event_index.jsonl` | JSON Lines | Phase 1 |
| `/data/events.jsonl` | JSON Lines, canonical events | Phase 3 |
| `/data/anomalies.jsonl` | JSON Lines, [anomaly records](#e1-anomaly-record-shape) | Phase 4 |
| `/data/situations.jsonl` | JSON Lines, situation objects | Phase 5 |
| `/data/rejected_candidates.jsonl` | JSON Lines, [rejected candidates](#f1-linking-phase-5) | Phase 5 |
| `/data/nagarnaadi.db` | SQLite | Phase 6 |

`/data/event_index.jsonl` is a **Phase 1 debug artifact**: one line per raw record that
Phase 1 expects to cross a severity floor, carrying `event_id`, `raw_ref`, `category`,
`source`, `start_utc` and the planted `truth_id`/`decoy_id` if any. It exists so a human
can answer "why did the scorecard miss GT-002" in one grep, and so Phase 1 can verify its
own ground truth references records that really exist.

> **Phase 3 must never read it.** A normalizer that looks up ids in Phase 1's index is
> not a normalizer, it is a lookup against the answer key, and `/scorecard` stops meaning
> anything. Phase 3 derives every id itself with
> [`make_event_id`](#event_id-derivation-fixed). Phase 10 and humans may read it freely.

The raw complaints feed keeps its `.csv` extension because its format *is* CSV — that
mismatch is the point. Every other file is JSON Lines: one object per line, UTF-8, no
trailing commas, no wrapping array, append-only.

### timestamps

- **Store everything in UTC. Display everything in IST.** No exceptions.
- Any field ending in `_utc` is ISO8601 with a literal `Z` and second precision:
  `2026-09-24T13:12:00Z`. Never store an offset like `+05:30`.
- `freshness_sec`, `gap_sec`, `age_sec`, `delay` and friends are integer **seconds**.
- Conversion to IST happens in exactly one place per side: a `toIST()` helper in
  `frontend/js/` for display, and `zoneinfo("Asia/Kolkata")` in the backend only for
  parsing feeds that arrive in local time ([D.2](#d2-civic_complaints), [D.3](#d3-power_discom)).
- Never round-trip a timestamp through a local-time string.

### constants

Every number this document fixes lives in **one** module, `backend/contract_constants.py`,
transcribed literally from the section that owns it. Four lanes importing one copy is the
code-level version of the rule this whole file exists to enforce.

| Constant | Value | From |
|---|---|---|
| `H3_RES` | `8` | [C](#c-zone-model) |
| `CITY_BBOX` | `(26.79, 75.69, 26.99, 75.89)` as `(sw_lat, sw_lon, ne_lat, ne_lon)` | [C](#c-zone-model) |
| `CITY_TZ` | `"Asia/Kolkata"` | [C](#c-zone-model) |
| `CATEGORIES` | the 11 ids | [B](#b-category-enum) |
| `FEEDS` | the 5 feed ids + their intervals | [D](#d-feed-list) |
| `SEVERITY_RAMPS` | per-category floor → ceiling | [A](#a-canonical-event-schema) |
| `CONFIDENCE` | base values + multipliers | [A](#a-canonical-event-schema) |
| `PULSE_THRESHOLDS` | `0/25/50/75` | [F](#f-situation-object-shape) |
| `NAGARNAADI_NS` | the UUID5 namespace | [A](#a-canonical-event-schema) |
| `ACTIVE_WINDOW_SEC` | `1800` | [E](#e-api-contract) |
| `LINK_MAX_GRID_DISTANCE` / `NEARBY_MAX_GRID_DISTANCE` | `1` / `2` | [F.1](#f1-linking-phase-5) |
| `LINK_MIN_LIFT` | `0.0` (never vetoes — see [F.1](#f1-linking-phase-5)) | [F.1](#f1-linking-phase-5) |
| `STANDALONE_MIN_SEVERITY_WEIGHTED` / `STANDALONE_MAX_CONFIDENCE` | `0.25` / `"med"` | [F.1](#f1-linking-phase-5) |
| `CONFIDENCE_HIGH_MAX_GAP_SEC` | `1800`, inclusive | [F.1](#f1-linking-phase-5) |

`backend/ingest/zones.py` holds the H3 *helpers* (`cell_of`, `bbox_cells`,
`nearest_landmark`, `zone_label`, …) and imports its constants from
`contract_constants.py`. The frontend mirror is `frontend/js/zones.js`.

Never write the literal `8`, `0.25` or `60` at a call site. Import the constant. If a
number here disagrees with the section it came from, **the section wins** and this module
is the bug.

### `is_simulated`

Set by `ingest/normalize.py`, never by a feed parser. In this build it is `true` for
every event without exception — there is no live city data behind any of this. The UI
must show a persistent "Simulated data" marker whenever any visible event has
`is_simulated: true`, on both the city and resident views. The field exists so that the
day someone wires in a real feed, the honesty is already structural rather than
something to remember.

### ids

| Id | Shape | Example |
|---|---|---|
| `event_id` | UUID4 string | `9f1c4b2e-5a7d-4e18-9c30-16b8ad4e2f77` |
| `situation_id` | `SIT-` + 6 hex | `SIT-4a91c2` |
| `truth_id` | `GT-` + 3 digits | `GT-001` |
| `decoy_id` | `DC-` + 3 digits | `DC-001` |
| `raw_ref` | `<feed_id>:<record id or line no>` | `civic_complaints:JPR-2026-114872` |

### language

All Devanagari is stored as UTF-8, never transliterated, never escaped. Every
resident-visible string has both an `_en` and an `_hi` form, produced together — a
missing Hindi string is a bug, not a fallback. Wording rules live in DESIGN.md.
