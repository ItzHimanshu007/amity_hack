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
| **Observed** | What the feeds actually report: rain, complaints, power, buses, air | Feed list, raw data in the Data room |
| **Inferred** | What NagarNaadi believes may be connected | Hero: situation, evidence chain, confidence and its reason |
| **Contextual** | What real terrain says about it | 3D terrain, drainage, "Terrain context" line |
| **Rejected** | What it deliberately refuses to connect | "Probably unrelated" |

On data honesty, say exactly this. Never say "nothing on screen is made up".

> Where we use synthetic data, we label it as synthetic. That's the "Simulated data" tag in the
> top bar. The terrain comes from real elevation data. We don't use the synthetic flood-grid
> values from our earlier Jal Drishti project.

## Before you go on stage (checklist)

1. **Data:** you ran the README pipeline (`sim.generate` → `ingest.run` → `engine.run` →
   `engine.linker`). The backend's startup log says
   **`4545 events, 170 anomalies, 3 situations, 30 rejected`**. If it says `0`, restart the backend.
2. **Servers:**
   - backend: `cd backend && uvicorn main:app --port 8000`
   - frontend: `cd frontend && python -m http.server 5500`
   - `curl http://127.0.0.1:8000/health` → `{"status":"ok"}`
3. **Tab 1:** `http://127.0.0.1:5500/index.html`.
   **Tab 2:** `http://127.0.0.1:5500/resident.html`. In Tab 2, set **Alert me about** to **Malviya Nagar**.
4. **Tab 1 setup:**
   - open **Simulation console**;
   - **Pause**;
   - click the **7:05 PM · Feed-outage demo point** marker;
   - click **64×**;
   - collapse the console.
5. **Reload Tab 1.** The 3D reveal happens only once per page load, and only when the situation
   arrives live.
6. **Screen check:**
   - The whole hero panel, down to "View full analysis", should sit above the console bar.
     It does at 1920×950, 1440×900, 1440×800 and 1536×760.
   - If it doesn't on the venue screen, press **Ctrl −** (90% or 80%).
7. **Wifi:** not needed. The map, 3D terrain and drainage all work offline. Only road names and
   water outlines need internet.

To reset between rehearsals: Simulation console → **Load storm scenario** (back to 5:30 PM),
then steps 4–5 again.

## The script (about 3 minutes)

Times in brackets are measured from pressing Play in the rehearsal.

| # | Do | You should see | Say |
|---|---|---|---|
| 1 | **Start.** Tab 1 at 7:05 PM, 2D. Open the console, press **▶ Play**, collapse the console. | "Nothing unusual right now". | "Jaipur has a weather feed, a complaints register, a power grid feed, bus GPS and air sensors. Five formats, five speeds, no shared IDs. Nobody connects them." |
| 2 | Wait (≈ 28 s). | The hero fills: **"Heavy rain near Jaipur Junction + 1 nearby area is holding up buses"**, orange. | "The moment independent feeds agree, NagarNaadi forms a situation." |
| 3 | Wait about 2 s for the map to tilt into 3D on its own. **Let the tilt finish, then Pause** in the console. | Real terrain in 3D, Aravalli ridges behind Jaipur Junction; blue drainage; the legend: *"This is terrain flow, not a flood forecast."* | "When a flood-related situation emerges, NagarNaadi brings in real terrain context to help interpret it. It does not change the confidence or claim causation." |
| 4 | Point at **Terrain context**. | *"Terrain only partly explains this: local drainage likely matters. Sindhi Camp sits 5 m above its surroundings; Jaipur Junction is on a drainage path (more runoff than 70% of the city)."* | "We checked the situation against real elevation, and we tell you it only partly fits. The terrain is context, not a magic explanation." |
| 5 | Point at the chain. | Heavy rain → Waterlogging (12 min later) → Bus running late (17 min) → Power cut → Signal not working → Bus running late (15 min). Then ✓ Same geographic area · ✓ Correct temporal sequence · ✓ Historical relationship · ✓ Independent feed corroboration. | "Four feeds, in the right order, in adjacent areas, a pairing that historically happens about once every 10 hours here." |
| 6 | Point at the confidence line. | **MEDIUM CONFIDENCE** · *"Civic complaints feed stale 6 min, confidence lowered"* · *"Still growing · 7 of ~12 reports so far"*. | "Not high, and it says why: one feed is late. It's also still collecting evidence." |
| 7 | Click **"… other patterns in these areas rejected as coincidence · see why"**. | The Situations view scrolls to **Probably unrelated**. The first card: *"Waterlogging and Extreme heat … overlapped in time and space but neither is a known cause of the other."* | "This is what NagarNaadi **refused** to connect. Heat and waterlogging showed up together here, but there's no mechanism, so no link." |
| 8 | Click **Live map**. Console: **▶ Play**. At about 7:59 PM, **Stop** on *City buses*. | **LOW CONFIDENCE** · *"City buses feed stopped — confidence lowered"*. | "Now the bus feed dies. Confidence drops, and it tells you why." |
| 9 | Click **Start** on *City buses*. | Back to **MEDIUM CONFIDENCE**. | "Feed back, confidence back." |
| 10 | Let it play to about 8:25 PM (≈ 25 s), then **Pause**. | At 8:20 PM the hero switches itself to the red **"A power cut near Malviya Nagar is holding up buses"**. | (It always shows the most serious situation.) |
| 11 | Click **Data room**. | Original messy records next to the cleaned events; names and phone numbers masked. | "Nothing is hidden: here's the raw data next to what we made of it, with personal details masked before they reach the screen." |
| 12 | Back to **Live map**; point at **How we know it works**. | **3/3** planted situations found · **0** false links · **3/3** decoys correctly ignored. | "Scored against an answer key the detector never reads: 3 of 3 found, zero false links, all 3 decoys ignored." |
| 13 | **Tab 2**, the resident view. | **Take action**. Alerts: **"New near Malviya Nagar"** and **"Getting worse near Malviya Nagar"**. | "For residents: one word, one line, an alert for their area. No map to read." |
| 14 | Click **हिंदी**. | *"मालवीय नगर के पास बिजली कटौती से बसें रुकी हैं"* and the alert *"मालवीय नगर के पास स्थिति बिगड़ रही है"*. | "And in Hindi." Close on the one-line pitch. |

### Things to avoid on stage

- **Don't jump straight to 7:30 PM.** A jump shows the situation without the 3D reveal. If
  that happens, press **3D terrain** on the map.
- **Don't click during the 2-second tilt.** On a weak laptop GPU the animation is heavy; wait
  for it to settle.
- **Don't refresh after stopping a feed.** The lowered confidence arrives over the live stream;
  a refresh shows the original level.
- **Don't scroll to the bottom of the Data room.** Its full scorecard includes a detection-lag
  figure the backend itself marks as an approximation, with internal notes. Use the
  **How we know it works** box on the Live map for the numbers.
- **Don't call the blue areas a flood forecast.** They're where rainwater drains, from real
  elevation.

## Slide outline (6 slides)

1. **The problem, in one line.** "Residents learn about a flooded underpass when they're stuck
   in it. The data existed; nobody connected it."
2. **The funnel (real pipeline numbers).**
   - 99,069 raw records across 5 feeds and 5 formats.
   - → 4,545 cleaned events.
   - → 170 statistically unusual windows.
   - → 21 episodes.
   - → **3 situations**, 30 candidate links rejected.
3. **Observed → inferred → contextual → rejected.** The four-layer table above.
4. **How we decide two things are linked.**
   - Same or adjacent area (H3 hexagons).
   - Right time order and gaps.
   - A plausibility table: rain can cause waterlogging; heat can't.
   - Co-occurrence against a learned 14-day baseline.
   - Confidence is a word with a stated reason, never a fake percentage.
5. **Why you can trust it.**
   - 3/3 found, 0 false links, 3/3 decoys ignored.
   - Graceful degradation.
   - PII masked on read.
   - "Possibly linked", never "caused".
   - Terrain from real elevation (engine adapted from Jal Drishti).
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
- **"Is the data real?"** The civic feeds are synthetic, as the problem statement allows, and
  labelled "Simulated data" on screen. They come in five deliberately messy formats:
  - IST text dates;
  - feeder IDs with no coordinates;
  - GTFS-style nested JSON;
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
- **"What happens when a feed goes down?"** Show it: script step 8.
- **"Why did you reject heat + waterlogging?"** It isn't in the plausibility table: neither is
  a known cause of the other, so co-occurring isn't enough.
