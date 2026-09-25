# NagarNaadi — demo script and pitch notes

Freeze build. Every line below was observed in a clean-room rehearsal:
- a fresh clone of the branch;
- only the README commands;
- internet map hosts blocked;
- the same result on two runs.

Replay: `monsoon_evening`, seed 42.

## The one-line pitch

> NagarNaadi doesn't just tell you what changed. It shows what changed **together**, **why** it
> thinks those events may be related, what it **deliberately refused to connect**, and what
> **real-world context** surrounds the situation.

Four layers, and every screen maps to one of them:

| Layer | What it is | Where it shows |
|---|---|---|
| **Observed** | What the feeds actually report: rain, complaints, power, storm-drain levels, air | Feed list, raw data in the Data room |
| **Inferred** | What NagarNaadi believes may be connected | Hero: situation, evidence chain, confidence and its reason |
| **Contextual** | What real terrain says about it | 3D terrain, modelled storm water, "Terrain context" lines |
| **Rejected** | What it deliberately refuses to connect | "Probably unrelated" |

On data honesty, say exactly this. Never say "nothing on screen is made up".

> Where we use synthetic data, we label it as synthetic. That's the "Simulated data" tag in the
> top bar. The terrain comes from real elevation data. We don't use the synthetic flood-grid
> values from our earlier Jal Drishti project.

## Before you go on stage (checklist)

1. **Data:** you ran the README pipeline (`sim.generate` → `ingest.run` → `engine.run` →
   `engine.linker`). The backend's startup log says
   **`4121 events, 296 anomalies, 8 situations, 21 rejected`**. If it says `0`, restart the backend.
2. **Servers:**
   - backend: `cd backend && uvicorn main:app --port 8000`
   - frontend: `cd frontend && python -m http.server 5500`
   - `curl http://127.0.0.1:8000/health` → `{"status":"ok"}`
   - The backend opens paused at 7:30 PM, with five situations live; step 4 moves it to 7:05 PM.
3. **Tab 1:** `http://127.0.0.1:5500/index.html`.
   **Tab 2:** `http://127.0.0.1:5500/resident.html`. In Tab 2, set **Alert me about** to **Malviya Nagar**.
4. **Tab 1 setup:**
   - open **Simulation console**;
   - **Pause**;
   - click the **7:05 PM · Storm crossing the city** marker;
   - click **64×**;
   - collapse the console.
5. **Reload Tab 1.** The 3D reveal happens only once per page load, and only when the situation
   arrives live.
6. **Screen check:**
   - The hero's two buttons ("View full analysis", "Resident view") sit just under the
     confidence line and must be above the console bar. They are at 1920×950, 1440×900,
     1440×800 and 1536×760. The explanation below them scrolls inside the right column.
   - If the text looks cramped on the venue screen, press **Ctrl −** (90%).
7. **Wifi:** not needed. The map, 3D terrain, drainage and the storm-water model all work
   offline. Only road names and the basemap's rivers need internet.
8. **Clock:** the top bar shows Jaipur's real time. The replay time is in the Simulation
   console.

To reset between rehearsals: Simulation console → **Load storm scenario** (back to 5:30 PM),
then step 4 again. No reload is needed: when the replay jumps back, the page clears every
situation it hasn't reached yet, and the 3D reveal plays again.

**Layout notes:**
- The **Simulation console** opens as a drawer over the left column (City health, Feeds,
  proof). The map and the hero stay fully visible. Close it to see the left column again.
- **Live situations** on the map has a **Hide** button; it collapses to a small
  "Live situations · N ▸" pill, and the page remembers the choice.
- Hexagons are coloured only for situations the replay has reached. They appear one by
  one, in time order, as each situation forms.

## The script (about 3 minutes)

Times in brackets are measured from pressing Play in the rehearsal.

| # | Do | You should see | Say |
|---|---|---|---|
| 1 | **Start.** Tab 1 at 7:05 PM, 2D. Open the console, press **▶ Play**, collapse the console. | One situation: **"A power cut near Vidyadhar Nagar has left a streetlight dark"**. *Live situations · 1 area* on the map. Light blue water starting to show in the west. | "Jaipur has a weather feed, a complaints register, a power grid feed, storm-drain level sensors and air sensors. Five formats, five speeds, no shared IDs. Nobody connects them." |
| 2 | Wait (≈ 15 s). | The storm reaches the city: new situations appear in **Live situations**. When **"Heavy rain near Mansarovar + 1 nearby area has damaged the road"** arrives, the map tilts into 3D over it by itself. | "The moment independent feeds agree, NagarNaadi forms a situation. This is a storm crossing the city, so it's happening in several places at once." |
| 3 | **Let the tilt finish, then Pause** in the console. | Real terrain in 3D. **Blue columns** of modelled water across the city; red and orange columns over the situations. Bottom-left: **Storm water model** with water standing (m³), area under 2 cm or more, deepest point, and people where the water is over 10 cm (sample estimate). | "That blue is a shallow-water model: this replay's recorded rain routed over real Jaipur elevation. It doesn't change the confidence or claim causation." |
| 4 | **Click a blue water cell.** | The cell inspector: grid cell, ground elevation, water depth now, water surface level, water collected (m³), deepest so far, and people living there (sample estimate). | "Every cell is inspectable: how deep, how much water, at what elevation, and roughly how many people live there. The population is a sample estimate, and it says so." |
| 5 | Point at **Terrain context**. | *"Terrain supports this. Mansarovar is on a drainage path…"* and **"Rain model at …: water over 10 cm on …% of Mansarovar …"**. | "We check each situation against real elevation and the rain model. Where terrain only partly explains it, it says so." |
| 6 | Point at the chain. | Heavy rain → Drain overflowing → Waterlogging → Road damage, with the gaps. ✓ Same geographic area · ✓ Correct temporal sequence · ✓ Historical relationship · ✓ Independent feed corroboration. | "Rain, then the drain gauges go over capacity, then residents report water, then the road breaks up. Three independent feeds, in the right order, in the same place." |
| 7 | Point at the confidence line. | The confidence word with its reason, e.g. *"Civic complaints feed stale 5 min, confidence lowered"*, and *"Still growing · 7 of ~10 reports so far"*. | "It tells you how sure it is, and why not more." |
| 8 | Click **"… other patterns … rejected as coincidence · see why"**. | The Situations view scrolls to **Probably unrelated**. | "This is what NagarNaadi **refused** to connect: things that overlapped in time and space but can't cause each other." |
| 9 | Click **Live map**. In **Live situations**, click **"Heavy rain near Sindhi Camp …"**. | The map flies there; the hero shows rain → drain overflowing → waterlogging → power cut → signal not working. | "Pick any live situation; the whole story comes with it." |
| 10 | Console: **▶ Play**, then **Stop** on *Storm-drain sensors*. | **LOW CONFIDENCE** · *"Storm-drain sensors feed stopped — confidence lowered"*. | "Now the drain sensors die. Confidence drops, and it tells you why." |
| 11 | Click **Start** on *Storm-drain sensors*. | Back to **MEDIUM CONFIDENCE**. | "Feed back, confidence back." |
| 12 | Let it play to about 8:20 PM (≈ 45 s), then **Pause**. | **Live situations · 8 areas**: Sindhi Camp, Mansarovar, Tonk Road, Sanganer, Jagatpura, Malviya Nagar, Vidyadhar Nagar, Vaishali Nagar. | "By 8:20 the storm has touched most of the city. Eight situations, each explained." |
| 13 | Click **Data room**. | Original messy records next to the cleaned events; names and phone numbers masked. | "Nothing is hidden: the raw data next to what we made of it, with personal details masked." |
| 14 | Back to **Live map**; point at **How we know it works**. | **8/8** planted situations found · **0** false links · **3/3** decoys correctly ignored. | "Scored against an answer key the detector never reads: 8 of 8 found, zero false links, all 3 decoys ignored." |
| 15 | **Tab 2**, the resident view. | **Take action**, and the alert **"New near Malviya Nagar"**. | "For residents: one word, one line, an alert for their area. No map to read." |
| 16 | Click **हिंदी**. | The same page in Hindi. | "And in Hindi." Close on the one-line pitch. |

### Things to avoid on stage

- **Don't reload the page after jumping past 7:15 PM.** The 3D reveal only fires when the
  situation arrives while the page is open. If the map is flat, press **3D terrain** on the map.
- **Don't click during the 2-second tilt.** On a weak laptop GPU the animation is heavy; wait
  for it to settle.
- **Don't call the blue water a flood forecast.** It's where this replay's rain would collect
  on real terrain. The depths are indicative (30 m elevation), the pattern is the point. The
  3D column height is the depth ×30 so it's visible at city scale; the legend says so.
- **Say "sample estimate" for population.** The people counts come from a simple density
  model, not a census.

## Slide outline (6 slides)

1. **The problem, in one line.** "Residents learn about a flooded underpass when they're stuck
   in it. The data existed; nobody connected it."
2. **The funnel (real pipeline numbers).**
   - 158,303 raw records across 5 feeds and 5 formats.
   - → 4,121 cleaned events.
   - → 296 statistically unusual windows.
   - → 35 episodes.
   - → **8 situations**, 21 candidate links rejected.
3. **Observed → inferred → contextual → rejected.** The four-layer table above.
4. **How we decide two things are linked.**
   - Same or adjacent area (H3 hexagons).
   - Right time order and gaps.
   - A plausibility table: rain can cause waterlogging; heat can't.
   - Co-occurrence against a learned 14-day baseline.
   - Confidence is a word with a stated reason, never a fake percentage.
5. **Why you can trust it.**
   - 8/8 found, 0 false links, 3/3 decoys ignored.
   - Graceful degradation.
   - PII masked on read.
   - "Possibly linked", never "caused".
   - Terrain from real elevation (engine adapted from Jal Drishti), plus a rain-on-terrain
     shallow-water model (`docs/FLOOD_MODEL.md`).
6. **Who it's for.**
   - Residents: Hindi and English, area alerts.
   - City operations staff: the chain and evidence.
   - Journalists: the Data room.

## Likely judge questions

- **"So does your terrain model predict the flood?"**
  "No. We deliberately don't claim that. The terrain layer shows real elevation and drainage
  context. NagarNaadi's situation is formed from the cross-feed evidence; terrain is additional
  context that helps us judge whether that situation is geographically plausible. Here it only
  partly is, and we say so."
- **"Why storm-drain sensors and not buses?"** Drain levels are the missing causal link in a
  flood: rain fills the drains, a drain over capacity spills into the street. Jaipur's JDA
  and JMC monitor nala levels; buses run late for a hundred reasons a civic feed can't see.
- **"Is the flood simulation real?"** It's a physical model on real terrain, driven by the
  replay's (synthetic) rain from 13 gauges across the city:
  - It uses the local-inertial shallow-water scheme from LISFLOOD-FP on SRTM elevation.
  - Drains remove 15 mm/h (the CPHEEO design range) and infiltration 7 mm/h.
  - Mass balance is exact.
  - Sanity check: under an even 60 mm storm, 5 of 6 places reported as Jaipur waterlogging
    hotspots collect more water than the city average (p = 0.003 against random places).
  - Limits: 30 m elevation, drains as a uniform sink. Details in `docs/FLOOD_MODEL.md`.
- **"Is the data real?"** The civic feeds are synthetic, as the problem statement allows, and
  labelled "Simulated data" on screen. They come in five deliberately messy formats:
  - IST text dates;
  - feeder and drain-gauge IDs with no coordinates;
  - SCADA-style tagged channels with a -999 fault value;
  - sensor fault sentinels.

  We normalize these into one schema. The terrain is real SRTM elevation.
- **"How does anomaly detection work?"**
  - A Poisson tail test per area, category and hour-of-day.
  - The baseline is learned from 14 days of history, with shrinkage toward the city-wide rate.
  - A second "rare event" rule catches single serious events.
  - Its threshold is calibrated on a held-out day.
- **"Why no LLM summary?"** Every sentence is filled from the ingested data using fixed
  wording, not generated. The problem statement asks for summaries grounded strictly in the
  ingested data, and for epistemic honesty.
- **"What happens when a feed goes down?"** Show it: script step 10.
- **"Why did you reject heat + waterlogging?"** It isn't in the plausibility table: neither is
  a known cause of the other, so co-occurring isn't enough.
