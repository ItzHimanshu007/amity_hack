// js/city.js — the map. MapLibre GL JS + h3-js over #map, plus the
// "Simulated data" banner. Authority: DESIGN.md, CONTRACT.md §C/§F.
//
// Contract this file publishes to the rest of the app:
//   - `situation:selected` CustomEvent on `window`, detail: situationId (a
//     plain string, or null when an unsituated area/empty space is clicked —
//     read as "clear selection"). Track B's situation rail listens for this
//     via js/api.js's onSituationSelected().
//
// The status block (DESIGN.md component #1) lives in js/statusblock.js, not
// here — it has no MapLibre/DOM-bootstrap dependency, so the resident view
// can import it without pulling in this file's map init (which would throw:
// resident.html has no #map and doesn't load the MapLibre/h3-js scripts).
//
// alert_level is always rendered AS GIVEN by the backend. This file never
// re-applies the pulse_score -> alert_level thresholds itself (CONTRACT §F):
// per situation it uses situation.alert_level verbatim; per cell/city it
// picks the alert_level that belongs to whichever active, non-decoy
// situation has the highest pulse_score touching that cell/city — it never
// recomputes a level from a number.

import { fetchState, connectStream, fetchSituation, sendControl, fetchTerrain, isFloodSituation } from "./api.js";
import { renderStatusBlock } from "./statusblock.js";

const H3_RES = 8;
const BBOX = { swLat: 26.79, swLon: 75.69, neLat: 26.99, neLon: 75.89 };
const MAP_CENTER = [75.7873, 26.9124]; // [lng, lat]
const MAP_ZOOM = 11.5;

const LANDMARKS = [
  { en: "Hawa Mahal", hi: "हवा महल", lat: 26.9239, lon: 75.8267, cell: "883da21891fffff" },
  { en: "Amer Fort", hi: "आमेर किला", lat: 26.9855, lon: 75.8513, cell: "883da20319fffff" },
  { en: "Jal Mahal", hi: "जल महल", lat: 26.9535, lon: 75.8460, cell: "883da2033dfffff" },
  { en: "Albert Hall Museum", hi: "अल्बर्ट हॉल", lat: 26.9117, lon: 75.8197, cell: "883da218b9fffff" },
  { en: "Jaipur Junction", hi: "जयपुर जंक्शन", lat: 26.9196, lon: 75.7878, cell: "883da218c7fffff" },
  { en: "Sindhi Camp", hi: "सिंधी कैंप", lat: 26.9268, lon: 75.7930, cell: "883da218c3fffff" },
  { en: "Vaishali Nagar", hi: "वैशाली नगर", lat: 26.9124, lon: 75.7370, cell: "883da21801fffff" },
  { en: "Malviya Nagar", hi: "मालवीय नगर", lat: 26.8549, lon: 75.8106, cell: "883da20a6dfffff" },
  { en: "Mansarovar", hi: "मानसरोवर", lat: 26.8505, lon: 75.7628, cell: "883da219e3fffff" },
];

const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ---------------------------------------------------------------- palette --
// Resolve CSS custom properties (incl. color-mix() derived ones) to actual
// rgb()/rgba() strings once, so the MapLibre style (a static JSON tree) can
// use the same tokens tokens.css defines, without a second copy of any hex.
//
// getComputedStyle() on a color-mix()-derived value serializes in the
// modern `color(srgb ...)` syntax in current Chromium/WebKit rather than
// rgb()/rgba() — MapLibre's style-spec color parser doesn't accept that
// syntax and the whole style silently fails to apply (confirmed: canvas
// fillStyle round-trips the *same* `color(srgb ...)` string back, so
// re-serializing through fillStyle does not help). Painting a 1x1 pixel and
// reading its resolved bytes back with getImageData does work — compositing
// with source-over onto a fully transparent canvas leaves the source color
// untouched (result = src, since dst alpha is 0), so this is exact even for
// translucent colors, and always yields a plain rgba() string.
let __colorNormCtx = null;
function normalizeColor(cssColor) {
  if (!__colorNormCtx) __colorNormCtx = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
  __colorNormCtx.clearRect(0, 0, 1, 1);
  __colorNormCtx.fillStyle = cssColor;
  __colorNormCtx.fillRect(0, 0, 1, 1);
  const [r, g, b, a] = __colorNormCtx.getImageData(0, 0, 1, 1).data;
  return `rgba(${r}, ${g}, ${b}, ${(a / 255).toFixed(3)})`;
}
function readPalette() {
  const probe = document.createElement("div");
  probe.style.position = "absolute";
  probe.style.visibility = "hidden";
  probe.style.pointerEvents = "none";
  document.body.appendChild(probe);
  function v(expr) {
    probe.style.color = expr;
    return normalizeColor(getComputedStyle(probe).color);
  }
  const palette = {
    chuna: v("var(--chuna)"),
    syahi: v("var(--syahi)"),
    dhool: v("var(--dhool)"),
    rekha: v("var(--rekha)"),
    neel: v("var(--neel)"),
    green: v("var(--green)"), yellow: v("var(--yellow)"), orange: v("var(--orange)"), red: v("var(--red)"),
    greenFill: v("var(--green-fill)"), yellowFill: v("var(--yellow-fill)"), orangeFill: v("var(--orange-fill)"), redFill: v("var(--red-fill)"),
    greenEdge: v("var(--green-edge)"), yellowEdge: v("var(--yellow-edge)"), orangeEdge: v("var(--orange-edge)"), redEdge: v("var(--red-edge)"),
    redHatch: v("color-mix(in srgb, var(--red) 28%, transparent)"),
    jal: v("var(--jal)"),
    jalFill: v("color-mix(in srgb, var(--jal) 30%, transparent)"),
    shade: v("color-mix(in srgb, var(--syahi) 55%, transparent)"),
    light: v("color-mix(in srgb, var(--chuna) 60%, transparent)"),
  };
  document.body.removeChild(probe);
  return palette;
}

function fillFor(palette, level) {
  return { green: palette.greenFill, yellow: palette.yellowFill, orange: palette.orangeFill, red: palette.redFill }[level] || "rgba(0,0,0,0)";
}
function edgeFor(palette, level) {
  return { green: palette.greenEdge, yellow: palette.yellowEdge, orange: palette.orangeEdge, red: palette.redEdge }[level] || "rgba(0,0,0,0)";
}
function tokFor(palette, level) {
  return { green: palette.green, yellow: palette.yellow, orange: palette.orange, red: palette.red }[level] || palette.dhool;
}

// ----------------------------------------------------------- hatch sprite --
// DESIGN.md: "a fill-pattern with an 8x8 hatch sprite", red-only.
function buildHatchImage(strokeColor) {
  const size = 8;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, size, size);
  ctx.strokeStyle = strokeColor;
  ctx.lineWidth = 2;
  ctx.beginPath();
  // three parallel 45deg segments so the 8x8 tile repeats seamlessly
  ctx.moveTo(-2, 10); ctx.lineTo(10, -2);
  ctx.moveTo(-2, 2); ctx.lineTo(2, -2);
  ctx.moveTo(6, 10); ctx.lineTo(10, 6);
  ctx.stroke();
  return ctx.getImageData(0, 0, size, size);
}

// ------------------------------------------------------------ map style ---
// Basemap (water, roads, road names) from OpenFreeMap. The only part of the
// map that needs the internet, so it is added after "load" and underneath the
// H3/situation layers: if the tile host is unreachable, only this goes missing.
function buildBasemapLayers(palette) {
  const roadColor = palette.dhool;
  const majorRoadClasses = ["motorway", "trunk", "primary", "secondary", "tertiary"];
  return [
    {
      id: "water",
      type: "fill",
      source: "ofm",
      "source-layer": "water",
      paint: { "fill-color": palette.rekha, "fill-opacity": 0.9 },
    },
    {
      id: "waterway",
      type: "line",
      source: "ofm",
      "source-layer": "waterway",
      paint: { "line-color": palette.rekha, "line-width": 1 },
    },
    {
      id: "roads",
      type: "line",
      source: "ofm",
      "source-layer": "transportation",
      filter: ["all",
        ["in", ["get", "class"], ["literal", [...majorRoadClasses, "minor", "service"]]],
        ["!=", ["get", "brunnel"], "tunnel"],
      ],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": roadColor,
        "line-opacity": 0.7,
        "line-width": ["interpolate", ["linear"], ["zoom"],
          10, ["match", ["get", "class"], ["motorway", "trunk"], 1.4, ["primary", "secondary"], 0.9, 0.5],
          16, ["match", ["get", "class"], ["motorway", "trunk"], 4, ["primary", "secondary"], 2.6, 1.2],
        ],
      },
    },
    {
      id: "road-labels",
      type: "symbol",
      source: "ofm",
      "source-layer": "transportation_name",
      minzoom: 12,
      filter: ["in", ["get", "class"], ["literal", majorRoadClasses]],
      layout: {
        "text-field": ["coalesce", ["get", "name:en"], ["get", "name"]],
        "text-font": ["Noto Sans Regular"],
        "text-size": 11,
        "symbol-placement": "line",
        "text-letter-spacing": 0.01,
      },
      paint: {
        "text-color": palette.syahi,
        "text-halo-color": palette.chuna,
        "text-halo-width": 1.4,
      },
    },
  ];
}

function addBasemap() {
  try {
    map.addSource("ofm", { type: "vector", url: "https://tiles.openfreemap.org/planet" });
    for (const layer of buildBasemapLayers(palette)) map.addLayer(layer, "h3-grid-line");
  } catch (err) {
    console.warn("[city] basemap unavailable, continuing without it", err);
  }
}

// ---------------------------------------------------------------- terrain --
// Real Jaipur elevation (AWS Terrarium / SRTM), vendored under
// assets/terrain/ by tools/terrain/build_terrain.py, so 3D works offline.
// Relief and drainage are context: nothing here changes linking or confidence.
const TERRAIN_TILES = "assets/terrain/{z}/{x}/{y}.png";
const TERRAIN_BOUNDS = [75.63, 26.73, 75.95, 27.05];
const TERRAIN_EXAGGERATION = 2;
const DRAINAGE_FLOW_PCT = 90; // areas carrying more water than 90% of the city

let terrainOn = false;
let terrainBtn = null;
let lastUserMoveAt = 0;
const autoTerrainDone = new Set();

function addTerrainSources() {
  const dem = { type: "raster-dem", tiles: [TERRAIN_TILES], encoding: "terrarium", tileSize: 256, minzoom: 10, maxzoom: 13, bounds: TERRAIN_BOUNDS };
  map.addSource("dem-terrain", dem);
  map.addSource("dem-hillshade", { ...dem });
  map.addLayer({
    id: "hillshade",
    type: "hillshade",
    source: "dem-hillshade",
    paint: {
      "hillshade-exaggeration": 0.35,
      "hillshade-shadow-color": palette.shade,
      "hillshade-highlight-color": palette.light,
      "hillshade-accent-color": palette.shade,
    },
  }, "h3-grid-line");
  map.addSource("drainage", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({
    id: "drainage-fill",
    type: "fill",
    source: "drainage",
    layout: { visibility: "none" },
    paint: { "fill-color": palette.jalFill },
  }, "h3-fill");
  map.addLayer({
    id: "drainage-edge",
    type: "line",
    source: "drainage",
    layout: { visibility: "none" },
    paint: { "line-color": palette.jal, "line-width": 1, "line-opacity": 0.6 },
  }, "h3-fill");
  fetchTerrain().then((terrain) => {
    if (!terrain) return;
    const features = Object.entries(terrain.cells)
      .filter(([, t]) => t.flow_pct >= DRAINAGE_FLOW_PCT)
      .map(([cell]) => ({ type: "Feature", geometry: { type: "Polygon", coordinates: [ringToLngLat(cell)] }, properties: {} }));
    map.getSource("drainage").setData({ type: "FeatureCollection", features });
  });
}

function setTerrainMode(on, focus) {
  terrainOn = on;
  map.setTerrain(on ? { source: "dem-terrain", exaggeration: TERRAIN_EXAGGERATION } : null);
  for (const id of ["drainage-fill", "drainage-edge"]) map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
  const target = on
    // Face north-east so the Nahargarh/Amer ridges sit behind the situation.
    ? { pitch: 62, bearing: 32, zoom: 12.3, ...(focus ? { center: focus } : {}) }
    : { pitch: 0, bearing: 0, zoom: MAP_ZOOM, center: MAP_CENTER };
  if (reduceMotion) map.jumpTo(target);
  else map.easeTo({ ...target, duration: 2200, easing: formEase });
  if (terrainBtn) {
    terrainBtn.textContent = on ? "2D map" : "3D terrain";
    terrainBtn.setAttribute("aria-pressed", String(on));
  }
  const legend = document.getElementById("terrain-legend");
  if (legend) legend.hidden = !on;
}

class TerrainControl {
  onAdd() {
    const wrap = document.createElement("div");
    wrap.className = "maplibregl-ctrl maplibregl-ctrl-group nn-terrain-ctrl";
    terrainBtn = document.createElement("button");
    terrainBtn.type = "button";
    terrainBtn.className = "nn-terrain-ctrl__btn";
    terrainBtn.textContent = "3D terrain";
    terrainBtn.setAttribute("aria-pressed", "false");
    terrainBtn.title = "Show real Jaipur terrain in 3D";
    terrainBtn.addEventListener("click", () => setTerrainMode(!terrainOn, heroFocus));
    wrap.appendChild(terrainBtn);
    return wrap;
  }
  onRemove() {}
}

// The hero (js/dashboard.js) announces which situation it is showing. The first
// time a flood-type situation takes the hero, ease into 3D over its area once.
let heroFocus = null;
function onHeroChanged(ev) {
  const { situation: sit, live } = ev.detail || {};
  const c = sit?.zone?.centroid;
  heroFocus = c ? [c.lon, c.lat] : null;
  // Only a situation arriving during the replay tilts the map; one already on
  // screen at page load leaves the 2D ten-second read alone.
  if (!live || !sit || !heroFocus || !isFloodSituation(sit) || autoTerrainDone.has(sit.situation_id)) return;
  if (reduceMotion || Date.now() - lastUserMoveAt < 10000) return;
  autoTerrainDone.add(sit.situation_id);
  if (!terrainOn) setTerrainMode(true, heroFocus);
  else map.easeTo({ center: heroFocus, duration: 1600, easing: formEase });
}

function buildStyle(palette) {
  return {
    version: 8,
    glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
    sources: {
      // Empty GeoJSON sources declared up front (not added later via
      // addSource) so the layers below — which reference them by id — are
      // valid the moment the style loads. They're populated with .setData()
      // once real data is available.
      "h3-cells": { type: "geojson", data: { type: "FeatureCollection", features: [] } },
      events: { type: "geojson", data: { type: "FeatureCollection", features: [] } },
      "situation-bounds": { type: "geojson", data: { type: "FeatureCollection", features: [] } },
      "situation-chain": { type: "geojson", data: { type: "FeatureCollection", features: [] } },
    },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": palette.chuna } },
      // --- H3 layer: grid outline (all ~591 cells), quiet & always on ---
      {
        id: "h3-grid-line",
        type: "line",
        source: "h3-cells",
        paint: { "line-color": palette.syahi, "line-width": 0.6, "line-opacity": 0.10 },
      },
      // --- H3 layer: status fill (only cells with an active situation) ---
      {
        id: "h3-fill",
        type: "fill",
        source: "h3-cells",
        paint: {
          "fill-color": ["match", ["get", "level"],
            "green", palette.greenFill, "yellow", palette.yellowFill,
            "orange", palette.orangeFill, "red", palette.redFill,
            "rgba(0,0,0,0)"],
        },
      },
      {
        id: "h3-hatch",
        type: "fill",
        source: "h3-cells",
        filter: ["==", ["get", "level"], "red"],
        paint: { "fill-pattern": "hatch-red" },
      },
      {
        id: "h3-edge",
        type: "line",
        source: "h3-cells",
        paint: {
          "line-color": ["match", ["get", "level"],
            "green", palette.greenEdge, "yellow", palette.yellowEdge,
            "orange", palette.orangeEdge, "red", palette.redEdge,
            "rgba(0,0,0,0)"],
          "line-width": 1.3,
        },
      },
      // --- raw event dots: cells with events but no situation get a dot,
      // never a fill (CONTRACT §F "city-wide and per-cell derivation") ---
      {
        id: "event-dots",
        type: "circle",
        source: "events",
        paint: {
          "circle-radius": 2,
          "circle-color": palette.dhool,
          "circle-opacity": 0.32,
        },
      },
      // --- situation overlay: dissolved boundary, dotted, in-progress fill
      // for easy click hit-testing ---
      {
        id: "situation-hit",
        type: "fill",
        source: "situation-bounds",
        paint: { "fill-color": "rgba(0,0,0,0)" },
      },
      {
        id: "situation-outline",
        type: "line",
        source: "situation-bounds",
        layout: { "line-cap": "round" },
        paint: {
          "line-color": ["match", ["get", "level"],
            "green", palette.green, "yellow", palette.yellow,
            "orange", palette.orange, "red", palette.red, palette.syahi],
          "line-width": 2,
          "line-dasharray": [1, 1.6],
        },
      },
      // --- the one orchestrated line: draws in on situation:created ---
      {
        id: "situation-chain-line",
        type: "line",
        source: "situation-chain",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": palette.syahi, "line-width": 2 },
      },
    ],
  };
}

// ------------------------------------------------------------- geometry ---
function ringToLngLat(cell) {
  const boundary = window.h3.cellToBoundary(cell, true); // [lng,lat] pairs
  const ring = boundary.slice();
  ring.push(ring[0]);
  return ring;
}

function allBboxCells() {
  // h3-js v4, non-GeoJSON order: [lat, lng] loop.
  const loop = [
    [BBOX.swLat, BBOX.swLon],
    [BBOX.swLat, BBOX.neLon],
    [BBOX.neLat, BBOX.neLon],
    [BBOX.neLat, BBOX.swLon],
  ];
  return window.h3.polygonToCells(loop, H3_RES, false);
}

// -------------------------------------------------------- bezier easing ---
// cubic-bezier(0.22, 1, 0.36, 1) — DESIGN.md's motion curve.
function makeBezierEasing(x1, y1, x2, y2) {
  function bez(t, a, b) {
    const c = 3 * a, d = 3 * (b - a) - c, e = 1 - c - d;
    return ((e * t + d) * t + c) * t;
  }
  function bezDerivative(t, a, b) {
    const c = 3 * a, d = 3 * (b - a) - c, e = 1 - c - d;
    return (3 * e * t + 2 * d) * t + c;
  }
  return function ease(x) {
    let t = x;
    for (let i = 0; i < 8; i++) {
      const xEst = bez(t, x1, x2) - x;
      const d = bezDerivative(t, x1, x2);
      if (Math.abs(d) < 1e-6) break;
      t -= xEst / d;
    }
    t = Math.min(1, Math.max(0, t));
    return bez(t, y1, y2);
  };
}
const formEase = makeBezierEasing(0.22, 1, 0.36, 1);

// ==================================================================== app
let map = null;
let palette = null;
let cellFeaturesById = new Map(); // cellId -> geojson feature (mutated in place)
let eventsById = new Map(); // eventId -> canonical event
let situationsById = new Map(); // situationId -> situation object
let situationVisuals = new Map(); // situationId -> { markers: [], raf }
let chainFeaturesById = new Map(); // situationId -> LineString feature (module-side cache; MapLibre's GeoJSONSource has no public getter)

function computeCellRollup() {
  const rollup = new Map(); // cellId -> { situationId, level, pulseScore }
  for (const sit of situationsById.values()) {
    if (sit.status !== "active" || sit.is_decoy) continue;
    const cells = (sit.zone && sit.zone.h3_cells) || [];
    for (const cell of cells) {
      const existing = rollup.get(cell);
      if (!existing || sit.pulse_score > existing.pulseScore) {
        rollup.set(cell, { situationId: sit.situation_id, level: sit.alert_level, pulseScore: sit.pulse_score });
      }
    }
  }
  return rollup;
}

let lastRollup = new Map();

function renderCellFills() {
  lastRollup = computeCellRollup();
  for (const [cellId, feature] of cellFeaturesById) {
    const info = lastRollup.get(cellId);
    feature.properties.level = info ? info.level : "none";
    feature.properties.situationId = info ? info.situationId : null;
  }
  const src = map.getSource("h3-cells");
  if (src) src.setData({ type: "FeatureCollection", features: Array.from(cellFeaturesById.values()) });
}

function renderEventDots() {
  const src = map.getSource("events");
  if (!src) return;
  const features = [];
  for (const ev of eventsById.values()) {
    if (ev.lat == null || ev.lon == null) continue;
    features.push({ type: "Feature", geometry: { type: "Point", coordinates: [ev.lon, ev.lat] }, properties: { event_id: ev.event_id } });
  }
  src.setData({ type: "FeatureCollection", features });
}

function updateSimulatedBanner() {
  const el = document.getElementById("simulated-banner");
  if (!el) return;
  let anySimulated = false;
  for (const ev of eventsById.values()) {
    if (ev.is_simulated) { anySimulated = true; break; }
  }
  if (anySimulated) {
    el.textContent = "Simulated data · सिम्युलेटेड डेटा";
    el.classList.add("nn-simulated-banner--on");
  } else {
    el.textContent = "";
    el.classList.remove("nn-simulated-banner--on");
  }
}

// ---------------------------------------------------------- click / select
function dispatchSituationSelected(situationId) {
  // detail is the raw situationId (or null), matching js/api.js's
  // onSituationSelected()/selectSituation() contract that Track B's rail
  // and resident view listen through — NOT { situationId } wrapped.
  window.dispatchEvent(new CustomEvent("situation:selected", { detail: situationId }));
}

function wireClicks() {
  map.on("click", "h3-fill", (e) => {
    const f = e.features[0];
    dispatchSituationSelected(f.properties.situationId || null);
  });
  map.on("click", "situation-hit", (e) => {
    const f = e.features[0];
    dispatchSituationSelected(f.properties.situationId || null);
  });
  // clicking empty map (no feature) clears selection
  map.on("click", (e) => {
    const hits = map.queryRenderedFeatures(e.point, { layers: ["h3-fill", "situation-hit"] });
    if (hits.length === 0) dispatchSituationSelected(null);
  });
  for (const layerId of ["h3-fill", "situation-hit", "event-dots"]) {
    map.on("mouseenter", layerId, () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", layerId, () => { map.getCanvas().style.cursor = ""; });
  }
}

// ------------------------------------------------------------- landmarks --
function addLandmarkMarkers() {
  for (const lm of LANDMARKS) {
    const el = document.createElement("div");
    el.className = "nn-landmark";
    const en = document.createElement("span");
    en.className = "nn-landmark__en label";
    en.textContent = lm.en;
    const hi = document.createElement("span");
    hi.className = "nn-landmark__hi label";
    hi.lang = "hi";
    hi.textContent = lm.hi;
    el.appendChild(en);
    el.appendChild(hi);
    new maplibregl.Marker({ element: el, anchor: "left", offset: [6, 0] })
      .setLngLat([lm.lon, lm.lat])
      .addTo(map);
  }
}

// --------------------------------------------------------- situation viz --
function cellsUnionOutline(cells, level, situationId) {
  // h3.cellsToMultiPolygon returns [ [ [ [lng,lat], ... ] outerRing, holes... ], ... ]
  const polys = window.h3.cellsToMultiPolygon(cells, true);
  return polys.map((poly) => ({
    type: "Feature",
    geometry: { type: "Polygon", coordinates: poly },
    properties: { level, situationId },
  }));
}

function renderSituationBounds() {
  const src = map.getSource("situation-bounds");
  if (!src) return;
  const features = [];
  for (const sit of situationsById.values()) {
    if (sit.status !== "active" || sit.is_decoy) continue;
    const cells = (sit.zone && sit.zone.h3_cells) || [];
    if (!cells.length) continue;
    features.push(...cellsUnionOutline(cells, sit.alert_level, sit.situation_id));
  }
  src.setData({ type: "FeatureCollection", features });
}

function clearMarkers(situationId) {
  const vis = situationVisuals.get(situationId);
  if (!vis) return;
  for (const m of vis.markers) m.remove();
  vis.markers = [];
  if (vis.raf) cancelAnimationFrame(vis.raf);
}

function makeNumberedMarker(step, lngLat, situationId) {
  const el = document.createElement("div");
  el.className = "nn-dot data";
  el.textContent = String(step);
  el.style.opacity = "0";
  el.addEventListener("click", (ev) => {
    ev.stopPropagation();
    dispatchSituationSelected(situationId);
  });
  const marker = new maplibregl.Marker({ element: el }).setLngLat(lngLat).addTo(map);
  requestAnimationFrame(() => { el.style.opacity = "1"; });
  return marker;
}

async function resolveChainCoords(situation) {
  const chain = situation.chain || [];
  const coords = [];
  let missing = false;
  for (const step of chain) {
    const ev = eventsById.get(step.event_id);
    if (ev && ev.lat != null && ev.lon != null) {
      coords.push([ev.lon, ev.lat]);
    } else {
      missing = true;
      coords.push(null);
    }
  }
  if (missing) {
    try {
      const full = await fetchSituation(situation.situation_id);
      for (const mev of full.member_events || []) {
        eventsById.set(mev.event_id, mev);
      }
      for (let i = 0; i < chain.length; i++) {
        if (coords[i] == null) {
          const ev = eventsById.get(chain[i].event_id);
          coords[i] = ev ? [ev.lon, ev.lat] : null;
        }
      }
    } catch (_) { /* best effort — fall through with cell centroids below */ }
  }
  // last-resort fallback: cell centroid, so a dot never silently vanishes
  for (let i = 0; i < coords.length; i++) {
    if (coords[i] == null) {
      const [lat, lon] = window.h3.cellToLatLng(chain[i].h3_cell);
      coords[i] = [lon, lat];
    }
  }
  return coords;
}

function segmentLengths(coords) {
  const lens = [0];
  for (let i = 1; i < coords.length; i++) {
    const dx = coords[i][0] - coords[i - 1][0];
    const dy = coords[i][1] - coords[i - 1][1];
    lens.push(Math.sqrt(dx * dx + dy * dy));
  }
  return lens;
}

function sliceLine(coords, cum, total, ratio) {
  const target = total * ratio;
  if (target <= 0) return [coords[0]];
  const out = [coords[0]];
  for (let i = 1; i < coords.length; i++) {
    if (cum[i] <= target) {
      out.push(coords[i]);
    } else {
      const segStart = cum[i - 1], segLen = cum[i] - cum[i - 1];
      const t = segLen > 0 ? (target - segStart) / segLen : 0;
      const p = [
        coords[i - 1][0] + (coords[i][0] - coords[i - 1][0]) * t,
        coords[i - 1][1] + (coords[i][1] - coords[i - 1][1]) * t,
      ];
      out.push(p);
      break;
    }
  }
  return out;
}

function setChainLine(situationId, coords) {
  const src = map.getSource("situation-chain");
  if (!src) return;
  chainFeaturesById.set(situationId, {
    type: "Feature",
    geometry: { type: "LineString", coordinates: coords },
    properties: { situationId },
  });
  src.setData({ type: "FeatureCollection", features: Array.from(chainFeaturesById.values()) });
}

async function animateSituationCreated(situation) {
  const coords = await resolveChainCoords(situation);
  if (coords.length < 1) return;

  clearMarkers(situation.situation_id);
  const vis = { markers: [], raf: null };
  situationVisuals.set(situation.situation_id, vis);

  if (coords.length === 1 || reduceMotion) {
    setChainLine(situation.situation_id, coords);
    for (let i = 0; i < coords.length; i++) {
      vis.markers.push(makeNumberedMarker(i + 1, coords[i], situation.situation_id));
    }
    renderSituationBounds();
    return;
  }

  const cum = segmentLengths(coords).reduce((acc, l, i) => { acc.push((acc[i - 1] || 0) + l); return acc; }, []);
  const total = cum[cum.length - 1] || 1;
  const stepRatios = cum.map((c) => c / total);
  const shown = new Array(coords.length).fill(false);

  const duration = 800;
  const start = performance.now();

  function frame(now) {
    const t = Math.min(1, (now - start) / duration);
    const eased = formEase(t);
    const partial = sliceLine(coords, cum, total, eased);
    setChainLine(situation.situation_id, partial);
    for (let i = 0; i < coords.length; i++) {
      if (!shown[i] && eased >= stepRatios[i] - 1e-6) {
        shown[i] = true;
        vis.markers.push(makeNumberedMarker(i + 1, coords[i], situation.situation_id));
      }
    }
    if (t < 1) {
      vis.raf = requestAnimationFrame(frame);
    } else {
      setChainLine(situation.situation_id, coords);
      renderSituationBounds(); // outline settles in once the line finishes
    }
  }
  vis.raf = requestAnimationFrame(frame);
}

async function renderSituationFinal(situation) {
  // Non-animated full render — used on initial load and for situations
  // that already existed before this page connected.
  const coords = await resolveChainCoords(situation);
  clearMarkers(situation.situation_id);
  const vis = { markers: [], raf: null };
  situationVisuals.set(situation.situation_id, vis);
  setChainLine(situation.situation_id, coords);
  for (let i = 0; i < coords.length; i++) {
    vis.markers.push(makeNumberedMarker(i + 1, coords[i], situation.situation_id));
  }
}

async function extendSituation(situation) {
  // action:"updated" — add new dot(s) + extend the line, no replay.
  const coords = await resolveChainCoords(situation);
  const vis = situationVisuals.get(situation.situation_id);
  const already = vis ? vis.markers.length : 0;
  if (!vis) {
    situationVisuals.set(situation.situation_id, { markers: [], raf: null });
  }
  setChainLine(situation.situation_id, coords);
  const v = situationVisuals.get(situation.situation_id);
  for (let i = already; i < coords.length; i++) {
    v.markers.push(makeNumberedMarker(i + 1, coords[i], situation.situation_id));
  }
  renderSituationBounds();
}

function removeSituationVisuals(situationId) {
  clearMarkers(situationId);
  situationVisuals.delete(situationId);
  chainFeaturesById.delete(situationId);
  const chainSrc = map.getSource("situation-chain");
  if (chainSrc) {
    chainSrc.setData({ type: "FeatureCollection", features: Array.from(chainFeaturesById.values()) });
  }
}

// ------------------------------------------------------------ WS handlers
function onTick(tick) {
  // City-wide alert level, rendered as given — feeds the status block.
  // pulse_score is not shown here; naadi.js renders it at the strip's
  // right edge from this same tick message (tick.city_pulse_score).
  renderStatusBlock("#status-block", tick.city_alert_level, situationsSummaryText(true));
}

function onEvent(event) {
  eventsById.set(event.event_id, event);
  renderEventDots();
  updateSimulatedBanner();
}

function onSituation(situation, action) {
  const isNew = !situationsById.has(situation.situation_id);
  situationsById.set(situation.situation_id, situation);
  renderCellFills();

  if (situation.is_decoy || situation.status !== "active") {
    removeSituationVisuals(situation.situation_id);
    renderSituationBounds();
    return;
  }

  if (action === "created" && isNew) {
    animateSituationCreated(situation);
  } else if (action === "closed") {
    removeSituationVisuals(situation.situation_id);
  } else {
    extendSituation(situation);
  }
  renderSituationBounds();
}

function onFeedHealth(_health) {
  // Feed-health list rendering belongs to Track B's controls.js — this file
  // only needs feed health indirectly (it doesn't gate the map). No-op here.
}

// ---------------------------------------------------------- status block --
function situationsSummaryText(cityWide) {
  const active = Array.from(situationsById.values()).filter((s) => s.status === "active" && !s.is_decoy);
  if (active.length === 0) {
    return { en: "Nothing unusual right now", hi: "अभी कुछ भी असामान्य नहीं" };
  }
  const cellSet = new Set();
  for (const s of active) for (const c of (s.zone && s.zone.h3_cells) || []) cellSet.add(c);
  const n = active.length;
  const areaCount = cellSet.size;
  const enThing = n === 1 ? "1 thing happening" : `${n} things happening`;
  const enArea = areaCount === 1 ? "1 area" : `${areaCount} areas`;
  return {
    en: `${enThing} across ${enArea}`,
    hi: `${areaCount} क्षेत्रों में ${n} चीजें हो रही हैं`,
  };
}

// -------------------------------------------------------------- bootstrap
async function init() {
  palette = readPalette();

  map = new maplibregl.Map({
    container: "map",
    style: buildStyle(palette),
    center: MAP_CENTER,
    zoom: MAP_ZOOM,
    attributionControl: { compact: true },
    dragRotate: false,
    pitchWithRotate: false,
    maxPitch: 70,
  });
  map.touchZoomRotate.disableRotation();
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
  map.addControl(new TerrainControl(), "top-right");
  for (const evName of ["dragstart", "zoomstart", "rotatestart", "pitchstart"]) {
    map.on(evName, (e) => { if (e.originalEvent) lastUserMoveAt = Date.now(); });
  }

  // A missing basemap tile/glyph must not surface as an uncaught error.
  map.on("error", (e) => console.warn("[city] map resource error", e && e.error ? e.error.message : e));

  map.on("load", async () => {
    addBasemap();
    addTerrainSources();
    window.addEventListener("hero:changed", onHeroChanged);
    window.dispatchEvent(new CustomEvent("hero:request"));
    const cells = allBboxCells();
    cellFeaturesById = new Map(cells.map((cell) => [cell, {
      type: "Feature",
      id: cell,
      geometry: { type: "Polygon", coordinates: [ringToLngLat(cell)] },
      properties: { cell, level: "none", situationId: null },
    }]));

    map.addImage("hatch-red", buildHatchImage(palette.redHatch));
    map.getSource("h3-cells").setData({ type: "FeatureCollection", features: Array.from(cellFeaturesById.values()) });

    addLandmarkMarkers();
    wireClicks();

    // Initial snapshot.
    try {
      const state = await fetchState();
      for (const ev of state.events || []) eventsById.set(ev.event_id, ev);
      for (const sit of state.situations || []) situationsById.set(sit.situation_id, sit);
      renderCellFills();
      renderEventDots();
      updateSimulatedBanner();
      renderStatusBlock("#status-block", state.city.alert_level, situationsSummaryText(true));
      for (const sit of situationsById.values()) {
        if (sit.status === "active" && !sit.is_decoy) await renderSituationFinal(sit);
      }
      renderSituationBounds();
    } catch (err) {
      // GET /state failing (e.g. not_ready) is not fatal — the WS stream
      // will populate things as soon as the backend has state. Show the
      // resting/empty state rather than blanking the screen.
      renderStatusBlock("#status-block", "green", { en: "Nothing unusual right now", hi: "" });
    }

    connectStream(onTick, onEvent, onSituation, onFeedHealth);
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}

// Exposed for manual/demo triggering and for other modules that may want to
// drive the sim (e.g. a "trigger situation" debug affordance).
export { sendControl };
