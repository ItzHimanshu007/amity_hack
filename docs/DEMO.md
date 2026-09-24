# NagarNaadi — 3-minute demo script and pitch outline

Every time and number below was measured on the `monsoon_evening` replay (seed 42).
Rehearse it at least twice. The replay is deterministic, so it goes the same way every time.

## Before you go on stage

1. Start the backend: `cd backend && source .venv/bin/activate && uvicorn main:app --port 8000`.
2. Start the frontend: `cd frontend && python -m http.server 5500`.
3. **Tab 1:** `http://127.0.0.1:5500/index.html` (city dashboard).
4. **Tab 2:** `http://127.0.0.1:5500/resident.html`. Set **Alert me about** to **Malviya Nagar** now, so the alert fires during the demo.
5. Open **Simulation console**, click **Pause**, then the **7:30 PM · Peak activity** marker.
6. Wifi check: the map (H3 grid, situation outline, numbered steps, landmarks) works offline. Only the road/water basemap needs internet, so a bad connection only loses the roads.

To reset between rehearsals: Simulation console → **Load storm scenario** (back to 5:30 PM). Then do step 5 again.

## The script

| Time | On screen | What to say |
|---|---|---|
| 0:00 | Dashboard at 7:30 PM, paused | "Jaipur has a weather feed, a complaints register, a power grid feed, bus GPS and air sensors. Five formats, five speeds, no shared IDs. Nobody connects them. NagarNaadi does, and it tells you when it isn't sure." |
| 0:20 | Hero panel, read top to bottom | "One glance: **what**: heavy rain near Jaipur Junction is holding up buses. **Where**: the orange outline on the map. **The chain**: rain, then waterlogging 12 minutes later, then buses 17 minutes after that, then a power cut and a dark signal, from 4 different feeds." |
| 0:45 | "Why we linked these" | "We don't just claim a link. Same area: the 2 areas are adjacent. Time order is correct. Historically these appear together about once every 10 hours here. And the evidence comes from independent feeds." |
| 1:00 | "Predicted next" + "Why it matters" | "It says what usually follows, road damage from standing water, and what a resident should do: avoid low-lying roads, expect bus delays, expect dark signals." |
| 1:10 | Console: **64×**, then **▶ Play** | "Let's run the clock." Point at **Still growing · 5 of ~12 reports**, then 7 of ~12: "the linker is still collecting evidence, and says so." |
| 1:30 | Console: **Stop** on *City buses* | "Now the bus feed dies." Hero: **LOW CONFIDENCE · City buses feed stopped — confidence lowered**. "It doesn't pretend. Confidence drops, and it tells you why." Click **Start**: back to MEDIUM. |
| 1:50 | Tab 2 (resident view) | At 7:50 PM: **New near Malviya Nagar: a power cut is holding up buses**. At 8:20 PM: **Getting worse**, now red. Click **हिंदी**. "Same information for residents, in Hindi, with no map to read." Back on Tab 1, the hero has switched itself to the red Malviya Nagar situation, because it always shows the most serious one. |
| 2:20 | Tab 1: "How we know it works" | "Not a vibe check. We planted 3 real cascades and 3 decoys in the synthetic city. We found 3 of 3, made 0 false links, and ignored all 3 decoys, scored against an answer key the detector never reads." |
| 2:35 | **Data room** tab | "And we hide nothing: here is the original messy data next to the cleaned version. The phone numbers and names residents typed are masked before they ever reach the screen." |
| 2:50 | Back to Live map | "NagarNaadi: not more data. The city, in context." |

### Things to avoid on stage

- **Don't use the 6:25 PM or 6:50 PM markers.** The linker only confirms the first situation at 7:30 PM. Before that the dashboard shows nothing.
- **Don't refresh the page after stopping a feed.** The lowered confidence arrives over the live stream; `GET /state` doesn't carry it, so a refresh shows the original level.
- **Don't open "Situations" to show "Probably unrelated".** The backend computes 30 rejected links but doesn't send them to the page yet. Quote the number instead (see slide 3).
- **Don't quote the detection-lag figure** on the scorecard. The backend marks it as a proxy measure.

## Slide outline (5 slides)

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
4. **Why you can trust it.**
   - 3/3 found, 0 false links, 3/3 decoys ignored.
   - Graceful degradation when a feed dies.
   - PII masked at read time.
   - "Possibly linked", never "caused".
5. **Who it's for.**
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
- **"What happens when a feed goes down?"** Show it: stop City buses (script step 1:30).
