# Rain-on-terrain flood model

What the blue water in NagarNaadi's 3D terrain view is, how it's made, and how far to trust it.

**In one sentence:** the replay's recorded rain is routed over real Jaipur elevation with a 2D
shallow-water model, to show where that rain would collect and when. It is context: it never
changes a link, a confidence level or the scorecard, and it is not a forecast.

## Inputs

| Input | Source |
|---|---|
| Elevation | SRTM via AWS Terrarium tiles, committed in `frontend/assets/terrain/` (zoom 13, downsampled to ~34 m cells) |
| Rain | This replay's `weather_imd` feed (`data/raw_weather_imd.jsonl`): the same 13 gauges NagarNaadi ingests, one per landmark plus the city centre; inverse-distance interpolated (power 2) |

In the `monsoon_evening` replay a storm cell crosses the city from the west, 6:00–8:05 PM:
- **heavy cores** (19–28 mm per 15 min at peak, about 75–110 mm/h) over Mansarovar, Tonk
  Road, Sindhi Camp / Jaipur Junction, Sanganer and Jagatpura, 45–55 minutes each;
- **light rain** (2.4–3.3 mm per 15 min, about 10–13 mm/h) over the rest of the city.

12 of the 13 gauges record rain. That is a realistic pattern for a heavy Jaipur monsoon
evening: intense convective cells embedded in wider moderate rain.

## Method

The **local inertial** form of the shallow-water equations, as used by LISFLOOD-FP (Bates,
Horritt & Fewtrell 2010, *J. Hydrology* 387; stability treatment after de Almeida et al. 2012),
on a regular grid:

```
q  ← (q − g·h_f·Δt·∂η/∂x) / (1 + g·Δt·n²·|q| / h_f^(7/3))
h  ← h + Δt·(rain − losses) + Δt·(inflow − outflow)/Δx
```

- **Time step:** adaptive (Courant-limited, at most 20 s); frames are saved every 5 minutes of
  replay time.
- **Elevation conditioning** (standard hydrological preprocessing):
  - a ~50 m Gaussian smooth removes the SRTM resampling texture;
  - a priority-flood fill (Barnes et al. 2014) removes depressions shallower than 1 m, treated as
    noise;
  - deeper real basins, such as the Jal Mahal lake bed, are kept.
- **Mass balance:** every cubic metre of rain is accounted for as drained, infiltrated, left the
  domain or still standing. Error: **0.00 %**.

| Parameter | Value | Basis |
|---|---|---|
| Manning's n | 0.035 | mixed urban surface (roads 0.015–0.02, built/pervious 0.03–0.05) |
| Storm-drain removal | 15 mm/h | CPHEEO design intensity for Indian storm drains is 12–20 mm/h |
| Infiltration | 7 mm/h | about 35% pervious share × about 20 mm/h sandy-loam capacity |
| Shown from | 2 cm depth | thinner sheet flow isn't drawn |

## What it produces (this replay)

| Replay time | Modelled water |
|---|---|
| 6:30 PM | First standing water, in the west (Mansarovar core) |
| 7:15–7:20 PM | Peak volume: about 4.9 million m³ standing |
| 7:30 PM | 52 km² under 2 cm or more; about 4.35 million m³; deepest point 1.2 m (a closed basin) |
| 8:30 PM | 19.7 km² still under 2 cm or more (drains remove only 15 mm/h) |

Per area, at peak:

| Area | Deepest | Share of area over 10 cm |
|---|---|---|
| Jaipur Junction | 22 cm | 9% |
| Sindhi Camp | 19 cm | 6% |
| Mansarovar | 84 cm | 8% |
| Tonk Road | 17 cm | 2% |
| Jagatpura | 52 cm | 2% |
| Sanganer | 143 cm | 8% |

- Water runs along the terrain's drainage lines and collects in low ground across the city.
- The deep values are closed low spots (a basin, a railway cutting) at 30 m resolution: read
  them as "water pools here", not as a measured depth.
- Mass balance error: **0.00 %** (33 million m³ of rain: drained, infiltrated, left the domain,
  or still standing).

### What the map and inspector show

- **Map blocks.** The model runs on ~34 m cells. The map draws ~68 m blocks (2×2 cells,
  mean depth, so volumes are exact) from 3 cm up: 7,727 blocks across the evening.
- **3D.** Each block is a column whose height is its depth ×30, so shallow city-wide water
  is visible at city scale. The legend says so.
- **Inspector.** Clicking a block shows:
  - grid reference;
  - ground elevation (SRTM, m);
  - depth now and deepest so far;
  - water-surface elevation;
  - water collected (depth × block area, m³);
  - people living in the block.
- **Population is a SAMPLE ESTIMATE**, not census data. It comes from a simple density
  model:
  - about 42,000/km² at the walled city (Hawa Mahal), falling off over about 2 km;
  - about 13,000/km² across the planned colonies, falling off over about 7.5 km;
  - a 2,500/km² floor;
  - a fixed per-cell texture.

  The summary's "people where water is over 10 cm" uses the same model and is labelled a
  sample estimate everywhere it appears.

## Sanity check against reported Jaipur hotspots

This test is independent of the replay:
- **Storm:** 60 mm in one hour, spread evenly over the whole city.
- **Measure:** the share of ground within 1 km that gets more than 10 cm of water.
- **Compared:** places reported in news and civic coverage as waterlogging-prone.

| Place | Share over 10 cm (within 1 km) | vs. city average (11.5%) |
|---|---|---|
| Jal Mahal | 28.1% | 2.4× |
| Tonk Road / SMS Hospital | 18.1% | 1.6× |
| Sikar Road | 16.6% | 1.5× |
| Vidyadhar Nagar | 14.4% | 1.3× |
| Mansarovar | 13.6% | 1.2× |
| Jawahar Nagar | 6.1% | 0.5× |

- 5 of 6 reported hotspots collect more water than the city average.
- Their mean is 16.2%, versus 11.7% for 500 random places.
- The chance that 6 random places would be this wet is **0.3%** (p = 0.0034).

The model points to real trouble spots from terrain alone. It does not reproduce every one:
Jawahar Nagar's problems are likely drainage-related, which a 30 m elevation model can't see.

## Limits: say these out loud if asked

- **Elevation is SRTM (~30 m).** It carries a few metres of vertical noise and can't see drains,
  kerbs, underpasses or building footprints. Depths are indicative; the useful output is
  **where** water collects and **when**.
- **The drain network is a uniform sink**, not modelled pipes; blocked drains would make things
  worse than shown.
- **Uniform infiltration.** There is no land-cover map, so it's the same everywhere.
- **Rain comes from 13 gauges.** Between gauges it's interpolated, not observed; with inverse
  distance weighting a heavy core influences a wide area around it.
- **Population is a sample estimate** from a density model, not census data.
- **The civic feeds are synthetic.** So this is "where *this replay's* rain would go on *real*
  Jaipur terrain", not a statement about any real evening.

## Regenerating

```bash
backend/.venv/bin/python tools/flood/model_flood.py           # replay storm, about 3 min
backend/.venv/bin/python tools/flood/model_flood.py --check   # + hotspot check, about 8 min
```

It writes two files, both committed:
- `frontend/assets/flood/water.geojson`: every ~68 m block that ever reaches 3 cm, as a vector
  square carrying its depth (cm) for each 5-minute frame (`d`, starting at frame `o`), its
  ground elevation `z`, sample population `p` and a grid reference `id`. The map picks the
  frame that matches the replay clock.
- `frontend/assets/flood/flood.json`: the per-frame summary, per-area numbers for the hero's
  "Rain model" line, the parameters and the hotspot check. The model has no random elements; the hotspot check's random baseline uses a
fixed seed. Repeated runs produced the same summary numbers.
