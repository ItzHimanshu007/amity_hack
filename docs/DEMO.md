# NagarNaadi — 3-minute demo script and pitch outline

Every time and number below was measured on the `monsoon_evening` replay (seed 42).
Rehearse it at least twice. The replay is deterministic, so it goes the same way every time.

## Before you go on stage

1. Start the backend: `cd backend && source .venv/bin/activate && uvicorn main:app --port 8000`.
2. Start the frontend: `cd frontend && python -m http.server 5500`.
3. **Tab 1:** `http://127.0.0.1:5500/index.html` (city dashboard).
4. **Tab 2:** `http://127.0.0.1:5500/resident.html`. Set **Alert me about** to **Malviya Nagar** now, so the alert fires during the demo.
5. Open **Simulation console**. Click **Pause**, then the **7:05 PM · Feed-outage demo point** marker, then **64×**. Collapse the console.
6. **Reload Tab 1 once after step 5.** The automatic 3D tilt happens only once per page load, and only when the situation arrives live.
7. Wifi check: the map works offline. That covers the H3 grid, situation outline, numbered steps, landmarks, 3D terrain and drainage. Only the road/water basemap needs internet, so a bad connection only loses the roads.

To reset between rehearsals: Simulation console → **Load storm scenario** (back to 5:30 PM). Then do steps 5–6 again.

## The script

| Time | On screen | What to say |
|---|---|---|
| 0:00 | Dashboard at 7:05 PM, 2D, "Nothing unusual right now". Press **▶ Play** in the console (64×). | "Jaipur has a weather feed, a complaints register, a power grid feed, bus GPS and air sensors. Five formats, five speeds, no shared IDs. Nobody connects them. NagarNaadi does, and it tells you when it isn't sure." (7:05 → 7:30 takes about 23 s at 64×.) |
| 0:25 | At 7:30 PM the hero fills, and the map tilts into 3D over Jaipur Junction with the Aravalli ridges behind it. **Pause.** | "The moment three feeds agree, NagarNaadi calls it a situation. And it puts it on the real terrain: this is actual Jaipur elevation. Blue is where rainwater drains." |
| 0:40 | Hero panel, top to bottom | "**What**: heavy rain near Jaipur Junction is holding up buses. **The chain**: rain, then waterlogging 12 minutes later, then buses 17 minutes after that, then a power cut and a dark signal, from 4 different feeds." |
| 0:55 | "Why we linked these" + "Terrain context" | "Same area, correct order, historically linked, independent feeds. And we checked it against real terrain. It only *partly* explains this: Sindhi Camp actually sits 5 m above its surroundings, so local drainage likely matters. We'd rather tell you that than draw a scary blue blob." |
| 1:10 | Click **3 other patterns in these areas rejected as coincidence · see why**, then back to **Live map** | "Heat and waterlogging also showed up here at the same time. No known way for one to cause the other, so we did *not* link them. Showing what we refused to link is the point." |
| 1:25 | Console: **▶ Play**. At about 7:40, **Stop** on *City buses* | Point at **Still growing · 5 of ~12 reports**. "Now the bus feed dies." Hero: **LOW CONFIDENCE · City buses feed stopped — confidence lowered**. "Confidence drops, and it tells you why." Click **Start**: back to MEDIUM. |
| 1:50 | Tab 2 (resident view) | At 7:50 PM: **New near Malviya Nagar: a power cut is holding up buses**. At 8:20 PM: **Getting worse**, now red. Click **हिंदी**. "Same information for residents, in Hindi, with no map to read." Back on Tab 1, the hero has switched itself to the red Malviya Nagar situation, because it always shows the most serious one. |
| 2:20 | Tab 1: "How we know it works" | "Not a vibe check. We planted 3 real cascades and 3 decoys in the synthetic city. We found 3 of 3, made 0 false links, and ignored all 3 decoys, scored against an answer key the detector never reads." |
| 2:35 | **Data room** tab | "And we hide nothing: here is the original messy data next to the cleaned version. The phone numbers and names residents typed are masked before they ever reach the screen." |
| 2:50 | Back to Live map | "NagarNaadi: not more data. The city, in context." |

### Things to avoid on stage

- **Don't use the 6:25 PM or 6:50 PM markers.** The linker only confirms the first situation at 7:30 PM. Before that the dashboard shows nothing.
- **Don't refresh the page after stopping a feed.** The lowered confidence arrives over the live stream; `GET /state` doesn't carry it, so a refresh shows the original level.
- **Don't jump straight to 7:30 PM for the 3D moment.** A jump shows the situation without the tilt. If that happens, press **3D terrain** on the map.
- **Don't call the blue areas a flood forecast.** They are where rainwater drains, from real elevation. The legend on the map says the same thing.
- **Don't quote the detection-lag figure** on the scorecard. The backend marks it as a proxy measure.

## Slide outline (6 slides)

1. **The problem, in one line.** "Residents learn about a flooded underpass when they're stuck in it. The data existed; nobody connected it."
2. **The funnel (real numbers).**
   - 99,069 raw records across 5 feeds and 5 formats.
   - → 4,545 cleaned events.
   - → 170 statistically unusual windows.
   - → 21 episodes.
   - → **3 situations** (and 30 candidate links rejected).
3. **How we decide two things are linked.**
   - Same or adjacent area (H3 hexagons).
   - The right time order and gaps.
   - A plausibility table: rain can cause waterlogging; waterlogging can't cause heat.
   - Co-occurrence against a learned 14-day baseline.
   - Confidence is a word (high / medium / low) with a stated reason, never a fake percentage.
4. **Terrain context, from our Jal Drishti engine.**
   - Real Jaipur elevation (SRTM via AWS Terrarium): 591 areas, D8 drainage analysis.
   - Runs offline in the browser.
   - It *describes*, it never *decides*: terrain never changes a link or a confidence level.
   - Sanity check anyone can see: drainage concentrates around the real Jal Mahal lake (6 areas) and the Amer valley (10 areas).
5. **Why you can trust it.**
   - 3/3 found, 0 false links, 3/3 decoys ignored.
   - Graceful degradation when a feed dies.
   - PII masked at read time.
   - "Possibly linked", never "caused".
6. **Who it's for.**
   - Residents: Hindi and English, area alerts, no map needed.
   - City operations staff: the full chain and evidence.
   - Journalists: the data room.

## Likely judge questions

- **"Is the data real?"** It's synthetic, as the problem statement allows. Five deliberately messy formats (IST text dates, feeder IDs with no coordinates, GTFS-style nested JSON, sensor fault sentinels) that we normalize into one schema.
- **"How does anomaly detection work?"**
  - A Poisson tail test per area, category and hour-of-day.
  - The baseline is learned from 14 days of history, with shrinkage toward the city-wide rate so quiet areas aren't over-flagged.
  - A second "rare event" rule catches single serious events, like one power trip.
  - Its threshold is calibrated on a held-out day.
- **"Why no LLM summary?"** Every sentence on screen comes from fixed wording filled with real data, so nothing can be made up. The problem statement asks for summaries "grounded strictly in the ingested data", and for epistemic honesty.
- **"What happens when a feed goes down?"** Show it: stop City buses (script step 1:25).
- **"Is the 3D flood layer real?"**
  - The elevation is real SRTM data for Jaipur, drawn at 2× height; the legend says so.
  - Blue is D8 flow accumulation: areas that carry more runoff than 90% of the city.
  - It's terrain, not a forecast, and it never changes a link or a confidence level.
  - The engine comes from our earlier project, Jal Drishti. We deliberately didn't bring over its synthetic flood grid.
- **"Why does the terrain line say it only partly explains the flood?"** Because that's what the elevation shows. The planted scenario isn't in Jaipur's lowest ground, and we report that rather than hide it.
