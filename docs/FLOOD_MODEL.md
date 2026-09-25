# Rain-on-terrain flood model

What the blue water in NagarNaadi's 3D terrain view is, how it's made, and how far to trust it.

**In one sentence:** the replay's recorded rain is routed over real Jaipur elevation with a 2D
shallow-water model, to show where that rain would collect and when. It is context: it never
changes a link, a confidence level or the scorecard, and it is not a forecast.

## Inputs

| Input | Source |
|---|---|
| Elevation | SRTM via AWS Terrarium tiles, committed in `frontend/assets/terrain/` (zoom 13, downsampled to ~34 m cells) |
| Rain | This replay's `weather_imd` feed (`data/raw_weather_imd.jsonl`), the same six gauges NagarNaadi ingests; inverse-distance interpolated (power 2) |

In the `monsoon_evening` replay only gauge **IMD-JAI-06** (next to Jaipur Junction) records
rain:
- a cloudburst from 6:30 to 7:15 PM;
- a peak of 26 mm per 15 min (about 100 mm/h);
- about 56 mm in total.

The other five gauges stay dry, so the storm is a small, intense cell. That is a realistic
pattern for a Jaipur monsoon evening.

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
| 6:40 PM | First standing water, 10 min after the rain starts |
| 7:05 PM | Peak: 1.16 km² over 2 cm, about 109,000 m³ standing |
| 7:30 PM | 0.92 km²; the Jaipur Junction and Sindhi Camp areas each have about 5% of their ground over 10 cm, deepest point about 15 cm |
| 8:30 PM | 0.47 km² still waterlogged (drains remove only 15 mm/h) |

- Water runs along the terrain's drainage lines and collects in the low parts of the storm area.
- Jaipur Junction lies on one of those lines.
- This agrees with the terrain line in the hero: terrain only *partly* explains the reported
  waterlogging.

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
- **Rain comes from six gauges.** Between gauges it's interpolated, not observed.
- **The civic feeds are synthetic.** So this is "where *this replay's* rain would go on *real*
  Jaipur terrain", not a statement about any real evening.

## Regenerating

```bash
backend/.venv/bin/python tools/flood/model_flood.py           # replay storm, about 2 min
backend/.venv/bin/python tools/flood/model_flood.py --check   # + hotspot check, about 7 min
```

It writes two files, both committed:
- `frontend/assets/flood/water.geojson`: every cell that is ever wet, as a vector square carrying
  its depth (cm) for each 5-minute frame. The map draws it on the 3D terrain and picks the
  frame that matches the replay clock.
- `frontend/assets/flood/flood.json`: the per-frame summary, per-area numbers for the hero's
  "Rain model" line, the parameters and the hotspot check. The model has no random elements; the hotspot check's random baseline uses a
fixed seed. Repeated runs produced the same summary numbers.
