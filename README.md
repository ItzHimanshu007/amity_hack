# Nagar Naadi

Civic situational awareness for **Jaipur**. Five messy city feeds go in; a few
plain-language *situations* a resident understands in ten seconds come out.

Two documents govern everything and are worth reading before you write any code:

- **[CONTRACT.md](CONTRACT.md)** — event schema, categories, zones, feeds, API,
  situation shape, ground truth, conventions. Field names and endpoints are final.
- **[DESIGN.md](DESIGN.md)** — the authority for every UI decision.

---

## Layout

```
backend/
  main.py          FastAPI entry, /health
  sim/             Phase 1-2  synthetic raw feeds + scenario planting
  ingest/          Phase 3    parse each raw format -> canonical event
  engine/          Phase 4-5  anomaly detection, linking, scorecard
  api/             Phase 6    routes, websocket, control plane
frontend/
  index.html       city view
  resident.html    resident view
  css/tokens.css   colors, type, spacing — the only place hexes live
  css/app.css      components
  js/              modules (Phase 7-9)
  assets/fonts/    Anek Devanagari, local (see below)
data/              generated feeds — gitignored, never hand-edited
```

---

## (a) Backend

Python **3.11**. From the repo root:

```bash
python3.11 -m venv .venv
# KNOWN-GOOD: Python 3.11.x, and 3.12.x with these exact pins (verified 3.12).
# DO NOT upgrade mid-hackathon. 3.13+ and 3.14 FAIL: pydantic 2.10.4 ships no wheel
# for them, so pip falls back to compiling pydantic-core from source and dies.
# Check before you debug anything else:  python --version
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r backend/requirements.txt
```

Run it **from inside `backend/`** so the package imports resolve:

```bash
cd backend
uvicorn main:app --reload --port 8000
```

Confirm it is up:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

Interactive API docs while you build: <http://127.0.0.1:8000/docs>

## (b) Frontend

No build step, no bundler, no npm. It is plain HTML/CSS/JS, so it only needs a static
server (opening the file directly with `file://` will break the module and font loads).
In a **second terminal**, from the repo root:

```bash
cd frontend
python -m http.server 5500
```

Then open:

- City view — <http://127.0.0.1:5500/index.html>
- Resident view — <http://127.0.0.1:5500/resident.html>

The backend already sends permissive CORS headers, so port 5500 talking to port 8000 is
fine.

### fonts (one-time)

Anek Devanagari is served locally — no CDN, so the demo survives bad venue wifi.
Download it once from <https://fonts.google.com/specimen/Anek+Devanagari> ("Get font" →
"Download all"), then drop the **variable** file into `frontend/assets/fonts/`:

```
frontend/assets/fonts/AnekDevanagari-VariableFont_wdth,wght.ttf
```

`tokens.css` also looks for `AnekDevanagari-Variable.woff2` at the same path and prefers
it if present — converting the TTF to woff2 is a nice-to-have, not required. The font
files are gitignored; each person downloads them once.

## (c) Generate data

From `backend/` with the venv active:

```bash
cd backend
python -m sim.generate                    # monsoon_evening scenario, seed 42
python -m sim.generate --scenario calm    # 14 days of baseline noise only, nothing planted
python -m sim.generate --seed 7           # a different reproducible draw
python -m sim.verify                      # re-run the self-checks without regenerating
```

Everything is driven by one seed (`SEED = 42` in `backend/sim/config.py`), so two runs
with the same scenario and seed produce **byte-identical** files — verified across
separate processes as part of Phase 1.

It writes every file listed in [CONTRACT.md §H](CONTRACT.md#h-filenaming-conventions)
into `/data`: the five raw feeds in their five deliberately different formats, the
feeder and drain-sensor registries, `/data/event_index.jsonl` (a Phase 1 debug artifact — see
§H; Phase 3 must not read it), and `ground_truth.json`.

`generate.py` runs six self-checks after writing (bbox containment and ground-truth
integrity are hard failures — a non-zero exit means the data is not usable):

```
(a) record counts         history dwarfs the 3-hour demo window in every feed
(b) bbox containment       every lat/lon falls inside CONTRACT.md §C's bbox   [hard fail]
(c) registry integrity     every feeder_id / drain rtu resolves through a registry
(d) ground-truth integrity every member_event_ids id exists in a real raw record [hard fail]
(e) history stationarity   no planted-cascade signature in the 14-day baseline
(f) signal_down provenance the category arrives from both feeds, never merged
```

Until you run this, `/data` is empty and the backend serves an empty snapshot — `/state`
returns `{"error":"not_ready", ...}` by design, and the UI shows an empty state rather
than an error.

### (c.2) Run the pipeline — required before the demo

`sim.generate` only writes the raw feeds. The replay also needs the cleaned events, the
anomalies and the linked situations, or the city stays empty all evening. Run these in
order, then (re)start the backend so it loads them:

```bash
cd backend
python -m sim.generate     # raw feeds        -> "all six checks passed"
python -m ingest.run       # cleaned events   -> "wrote 4121 canonical events"
python -m engine.run       # anomalies        -> "wrote 296 anomalies"
python -m engine.linker    # situations       -> "35 episodes -> 8 situations, 21 rejected candidates"
uvicorn main:app --port 8000   # startup log: "4121 events, 296 anomalies, 8 situations, 21 rejected"
python -m api.verify_api   # in a second terminal, backend running: "All checks passed." (7/7)
```

If the backend's startup log says `0 events` or `0 situations`, it was started before the
pipeline finished: stop it and start it again.

The 3D terrain assets in `frontend/assets/terrain/` and the rain-on-terrain flood model
output in `frontend/assets/flood/` are committed, so they need no step here. To regenerate
them: `tools/terrain/build_terrain.py` (real elevation tiles) and
`tools/flood/model_flood.py --check` (the shallow-water model; see
[docs/FLOOD_MODEL.md](docs/FLOOD_MODEL.md)).

To start over, delete the folder contents and regenerate; nothing in `/data` is precious.

```bash
rm -rf data/* && touch data/.gitkeep
```

---

## Working in parallel

Four people, four lanes, one contract:

| Lane | Owns | Reads |
|---|---|---|
| Data | `backend/sim/` | CONTRACT.md §A, §B, §D, §G |
| Pipeline | `backend/ingest/` | CONTRACT.md §A, §C, §D, §H |
| Engine | `backend/engine/` | CONTRACT.md §A, §C, §F, §G |
| Interface | `backend/api/`, `frontend/` | CONTRACT.md §E, §F + all of DESIGN.md |

Nobody waits on anybody: every lane builds against the shapes in CONTRACT.md, not against
another lane's code. If a shape genuinely needs to change, change CONTRACT.md first and
tell the other three — do not work around it locally.
