// js/naadi.js — the Naadi strip. DESIGN.md §5: the one bold ECG-style
// element, hand-drawn on canvas, plus per-feed lanes. Driven entirely by WS
// "tick"/"feedhealth"/"situation" messages — never a local simulated clock.
//
// Layout inside #naadi-strip, top to bottom:
//   1. Main lane   72px — DESIGN.md-exact: grid, bold 2px city pulse trace,
//                         per-situation tick marks, live value at data scale.
//   2. Feed lanes  60px — five thin per-feed traces (amplitude = that feed's
//                         event arrival rate this tick), dashed grey when the
//                         feed is stale/killed/error.
//
// The transport row (play/pause, discrete speed buttons, clock) is a separate
// bar rendered after the strip, not inside it: a horizontal speed scale sitting
// directly under the traces reads as a time axis, which made the whole strip
// hard to parse.
//
// All line art is canvas; all text is real HTML so it can use the actual
// type-scale tokens (incl. tabular-nums and the wdth axis), which a canvas
// fillText cannot reproduce faithfully.

import { connectStream, sendControl, fetchState, BOOKMARKS } from "./api.js";

const FEED_IDS = ["weather_imd", "civic_complaints", "power_discom", "transit_gtfs", "air_sensors"];
const FEED_LABELS = {
  weather_imd: { en: "Weather", hi: "मौसम" },
  civic_complaints: { en: "Civic complaints", hi: "नागरिक शिकायतें" },
  power_discom: { en: "Power", hi: "बिजली" },
  transit_gtfs: { en: "City buses", hi: "शहर की बसें" },
  air_sensors: { en: "Air quality", hi: "हवा की गुणवत्ता" },
};
// CONTRACT.md §B "Emitted by" — the category enum is closed/fixed, so this
// static map is safe. Used only to know which feed lane should flag as
// "part of an anomaly" when a situation names a category.
const CATEGORY_TO_FEEDS = {
  "weather.rain": ["weather_imd"],
  "weather.heat": ["weather_imd"],
  "air.pm25": ["air_sensors"],
  "power.outage": ["power_discom"],
  "traffic.signal_down": ["power_discom", "civic_complaints"],
  "transit.delay": ["transit_gtfs"],
  "complaint.waterlogging": ["civic_complaints"],
  "complaint.garbage": ["civic_complaints"],
  "complaint.streetlight": ["civic_complaints"],
  "complaint.road_damage": ["civic_complaints"],
  "complaint.smoke": ["civic_complaints"],
};

const SPEEDS = [1, 2, 4, 8, 16];
const WINDOW_SEC = 300; // ~5 minutes of real time, per DESIGN.md §5
const MAIN_H = 72; // DESIGN.md §5, exact
// Each feed is one real flex row now, not a shared absolutely-positioned
// canvas with floating labels — LANE_H is a legibility floor (13px label
// type at 1.3 line-height needs ~17px) rather than a size to chase down.
const LANE_H = 18;

// ---------------------------------------------------------------- palette --
// See js/city.js's readPalette() for the color-mix()/color(srgb...) note —
// canvas strokeStyle/fillStyle here happens to accept that syntax directly,
// but normalizing through actual pixel bytes keeps this file's tokens byte-
// identical to city.js's (same source values, same normalization path).
let __colorNormCtx = null;
function normalizeColor(cssColor) {
  if (!__colorNormCtx) __colorNormCtx = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
  __colorNormCtx.clearRect(0, 0, 1, 1);
  __colorNormCtx.fillStyle = cssColor;
  __colorNormCtx.fillRect(0, 0, 1, 1);
  const [r, g, b, a] = __colorNormCtx.getImageData(0, 0, 1, 1).data;
  return `rgba(${r}, ${g}, ${b}, ${(a / 255).toFixed(3)})`;
}
function readTokens() {
  const probe = document.createElement("div");
  probe.style.position = "absolute";
  probe.style.visibility = "hidden";
  probe.style.pointerEvents = "none";
  document.body.appendChild(probe);
  function v(expr) { probe.style.color = expr; return normalizeColor(getComputedStyle(probe).color); }
  const t = {
    chuna: v("var(--chuna)"), syahi: v("var(--syahi)"), dhool: v("var(--dhool)"),
    rekha: v("var(--rekha)"), neel: v("var(--neel)"),
    green: v("var(--green)"), yellow: v("var(--yellow)"), orange: v("var(--orange)"), red: v("var(--red)"),
  };
  document.body.removeChild(probe);
  return t;
}
function tokFor(t, level) {
  return { green: t.green, yellow: t.yellow, orange: t.orange, red: t.red }[level] || t.syahi;
}

const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ==================================================================== app
let tok = null;
let root = null, canvas = null, ctx = null, dpr = 1;
let mainValueEl = null, pulseEl = null, clockEl = null, playBtn = null, markersEl = null;
const laneEls = {};   // feedId -> { label, state }
const speedBtns = {}; // speed value -> button

// Data buffers
let samples = []; // { atReal, simTimeUtc, tick, speed, state, cityEvents, byFeed, cityPulseScore, cityAlertLevel }
let pendingByFeed = Object.fromEntries(FEED_IDS.map((f) => [f, 0]));
let pendingTotal = 0;
let feedHealth = Object.fromEntries(FEED_IDS.map((f) => [f, { state: "live", message: null }]));
let situationsById = new Map();
let feedAlertById = new Map(); // feedId -> { level, pulseScore }
let markers = []; // { atReal, level, label, el }
let runningMax = 3;
let lastTick = { speed: 1, state: "paused", sim_time_utc: null };

// ------------------------------------------------------------------- DOM --
function buildDom() {
  root = document.getElementById("naadi-strip");
  root.innerHTML = "";
  root.classList.add("nn-naadi");

  // The main canvas covers only the DESIGN.md-exact 72px main lane now — each
  // feed row below has its own small canvas, in normal flex flow, so a row's
  // label, trace and state word are one aligned line instead of a shared
  // canvas with HTML floated on top of it at absolute positions.
  canvas = document.createElement("canvas");
  canvas.className = "nn-naadi__canvas";
  canvas.style.height = `${MAIN_H}px`;
  root.appendChild(canvas);

  mainValueEl = document.createElement("div");
  mainValueEl.className = "nn-naadi__value data";
  root.appendChild(mainValueEl);

  // DESIGN.md §1: the city view "may show pulse_score once, at data scale
  // in --dhool, at the right edge of the Naadi strip. Nowhere else, and
  // never larger than the word." This is that one place.
  pulseEl = document.createElement("div");
  pulseEl.className = "nn-naadi__pulse data";
  root.appendChild(pulseEl);

  markersEl = document.createElement("div");
  markersEl.className = "nn-naadi__markers";
  markersEl.style.height = `${MAIN_H}px`;
  root.appendChild(markersEl);

  const lanes = document.createElement("div");
  lanes.className = "nn-lanes";
  for (const feed of FEED_IDS) {
    const row = document.createElement("div");
    row.className = "nn-lane";
    row.dataset.feed = feed;

    const label = document.createElement("span");
    label.className = "nn-lane__label label";
    label.textContent = FEED_LABELS[feed].en;
    row.appendChild(label);

    const spark = document.createElement("canvas");
    spark.className = "nn-lane__spark";
    row.appendChild(spark);

    const state = document.createElement("span");
    state.className = "nn-lane__state label";
    row.appendChild(state);

    lanes.appendChild(row);
    laneEls[feed] = { row, label, state, canvas: spark, ctx: null, phase: feedPhase(feed) };
  }
  root.appendChild(lanes);

  buildTransport();

  resizeCanvas();
  window.addEventListener("resize", resizeCanvas);
}

// A stable per-feed phase so idle traces (see drawLane) wobble out of sync
// with each other — five feeds breathing in lockstep would read as one fake
// animation rather than five independent ones.
function feedPhase(feed) {
  let h = 0;
  for (let i = 0; i < feed.length; i++) h = (h * 31 + feed.charCodeAt(i)) >>> 0;
  return (h % 1000) / 1000 * Math.PI * 2;
}

// The transport sits after the strip, not under the traces — discrete buttons
// so nothing on screen can be mistaken for a draggable time axis.
function buildTransport() {
  const bar = document.createElement("div");
  bar.className = "nn-transport";
  bar.id = "naadi-transport";

  playBtn = document.createElement("button");
  playBtn.type = "button";
  playBtn.className = "nn-transport__play body";
  playBtn.textContent = "Pause simulation";
  playBtn.addEventListener("click", onPlayToggle);
  bar.appendChild(playBtn);

  const speedGroup = document.createElement("div");
  speedGroup.className = "nn-transport__speeds";
  const speedLabel = document.createElement("span");
  speedLabel.className = "nn-transport__label label";
  speedLabel.textContent = "Speed";
  speedGroup.appendChild(speedLabel);
  for (const s of SPEEDS) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "nn-transport__speed label";
    btn.dataset.speed = String(s);
    btn.textContent = `${s}×`;
    btn.setAttribute("aria-label", `Run at ${s}× speed`);
    btn.addEventListener("click", () => sendControl("speed", s));
    speedGroup.appendChild(btn);
    speedBtns[s] = btn;
  }
  bar.appendChild(speedGroup);

  const bookmarks = document.createElement("div");
  bookmarks.className = "nn-transport__bookmarks";
  const bmLabel = document.createElement("span");
  bmLabel.className = "nn-transport__label label";
  bmLabel.textContent = "Jump to";
  bookmarks.appendChild(bmLabel);
  const select = document.createElement("select");
  select.className = "nn-transport__jump label";
  select.setAttribute("aria-label", "Jump to a moment in the replay");
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "a moment…";
  select.appendChild(placeholder);
  for (const bm of BOOKMARKS) {
    const opt = document.createElement("option");
    opt.value = bm.id;
    opt.textContent = bm.label_en.replace(/^Jump to /, "");
    select.appendChild(opt);
  }
  select.addEventListener("change", () => {
    if (!select.value) return;
    sendControl("jump_to", select.value);
    select.value = "";
  });
  bookmarks.appendChild(select);
  bar.appendChild(bookmarks);

  clockEl = document.createElement("span");
  clockEl.className = "nn-transport__clock data";
  bar.appendChild(clockEl);

  root.insertAdjacentElement("afterend", bar);
  trackBandHeight();
}

// The overlay panels clear the top band by its measured height, not a guess:
// the band grows and shrinks with the simulated-data banner and with wrapping
// in the transport row.
function trackBandHeight() {
  const band = document.getElementById("top-band");
  if (!band) return;
  const apply = () => {
    document.documentElement.style.setProperty("--band-h", `${Math.round(band.getBoundingClientRect().height)}px`);
  };
  apply();
  if (window.ResizeObserver) new ResizeObserver(apply).observe(band);
  else window.addEventListener("resize", apply);
}

function resizeCanvas() {
  dpr = window.devicePixelRatio || 1;
  const w = root.clientWidth || 800;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(MAIN_H * dpr);
  canvas.style.width = `${w}px`;
  ctx = canvas.getContext("2d");

  for (const feed of FEED_IDS) {
    const lane = laneEls[feed];
    const sw = lane.canvas.clientWidth || 1;
    lane.canvas.width = Math.round(sw * dpr);
    lane.canvas.height = Math.round(LANE_H * dpr);
    lane.ctx = lane.canvas.getContext("2d");
  }
}

// --------------------------------------------------------------- toIST() --
// CONTRACT.md §H: "Conversion to IST happens in exactly one place per side."
// This is that place for the frontend's display-only needs here; every
// timestamp shown by this file goes through it.
function toIST(utcIso) {
  if (!utcIso) return "--:--:--";
  const d = new Date(utcIso);
  return d.toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// -------------------------------------------------------------- controls --
function onPlayToggle() {
  if (lastTick.state === "play") sendControl("pause");
  else sendControl("play");
}

function markActiveSpeed(speed) {
  for (const [value, btn] of Object.entries(speedBtns)) {
    btn.classList.toggle("is-active", Number(value) === speed);
    btn.setAttribute("aria-pressed", Number(value) === speed ? "true" : "false");
  }
}

// ---------------------------------------------------------------- ticking
function onTick(tick) {
  const now = performance.now();
  const byFeed = { ...pendingByFeed };
  samples.push({
    atReal: now,
    simTimeUtc: tick.sim_time_utc,
    eventsThisTick: pendingTotal,
    byFeed,
    cityPulseScore: tick.city_pulse_score,
    cityAlertLevel: tick.city_alert_level,
  });
  const cutoff = now - WINDOW_SEC * 1000 - 2000;
  while (samples.length && samples[0].atReal < cutoff) samples.shift();

  pendingTotal = 0;
  for (const f of FEED_IDS) pendingByFeed[f] = 0;

  runningMax = Math.max(1, Math.max(runningMax * 0.98, byFeedMaxOf(byFeed)));
  lastTick = tick;

  markActiveSpeed(tick.speed);
  playBtn.textContent = tick.state === "play" ? "Pause simulation" : "Resume simulation";
  clockEl.textContent = `${toIST(tick.sim_time_utc)} IST · ${tick.state === "play" ? "Playing" : "Paused"}`;

  const perMinute = Math.round((pendingRateEstimate()) * 60);
  mainValueEl.textContent = `${perMinute} events/min`;
  if (typeof tick.city_pulse_score === "number") pulseEl.textContent = String(tick.city_pulse_score);
}

function byFeedMaxOf(byFeed) {
  let m = 0;
  for (const f of FEED_IDS) m = Math.max(m, byFeed[f] || 0);
  return m;
}

function pendingRateEstimate() {
  // events/sec, smoothed over the last ~10 samples (real seconds)
  const recent = samples.slice(-10);
  if (!recent.length) return 0;
  const total = recent.reduce((a, s) => a + s.eventsThisTick, 0);
  return total / recent.length;
}

function onEvent(event) {
  pendingTotal += 1;
  if (pendingByFeed[event.source] != null) pendingByFeed[event.source] += 1;
}

function applyFeedHealth(health) {
  feedHealth[health.feed] = health;
  const cell = laneEls[health.feed];
  if (!cell) return;
  const degraded = health.state !== "live";
  cell.row.classList.toggle("nn-lane--degraded", degraded);
  // Always a word, in words — a blank cell is not information. health.message
  // carries the plain-language reason verbatim ("No update for 14 min" /
  // "Stopped by operator") per CONTRACT.md §E; "Live" is this file's own,
  // since the backend has no reason string for the healthy case.
  cell.state.textContent = degraded ? (health.message || health.state) : "Live";
}

function onFeedHealth(health) {
  applyFeedHealth(health);
}

function recomputeFeedAlerts() {
  feedAlertById = new Map();
  for (const sit of situationsById.values()) {
    if (sit.status !== "active" || sit.is_decoy) continue;
    const feeds = new Set();
    for (const step of sit.chain || []) {
      for (const f of CATEGORY_TO_FEEDS[step.category] || []) feeds.add(f);
    }
    for (const f of feeds) {
      const existing = feedAlertById.get(f);
      if (!existing || sit.pulse_score > existing.pulseScore) {
        feedAlertById.set(f, { level: sit.alert_level, pulseScore: sit.pulse_score });
      }
    }
  }
}

function onSituation(situation, action) {
  situationsById.set(situation.situation_id, situation);
  if (situation.status !== "active" || action === "closed") situationsById.delete(situation.situation_id);
  recomputeFeedAlerts();

  if (action === "created" && !situation.is_decoy) {
    plantMarker(situation.alert_level, toIST(situation.created_utc || situation.updated_utc));
  }
}

function plantMarker(level, timeLabel) {
  const el = document.createElement("div");
  el.className = "nn-naadi__marker-label label";
  el.textContent = timeLabel;
  markersEl.appendChild(el);
  markers.push({ atReal: performance.now(), level, el });
}

// ------------------------------------------------------------------ draw --
function xForAtReal(atReal, now, width) {
  const elapsedSec = (now - atReal) / 1000;
  return width - (elapsedSec / WINDOW_SEC) * width;
}

function amplitudeFor(value) {
  return Math.min(1, value / runningMax);
}

// A healthy feed that simply has nothing to report this tick drew as a dead
// flat line — visually identical to a broken one. A small idle wobble (never
// reaching a real event's amplitude) makes "calm" and "broken" distinguishable
// at a glance: calm gently breathes, broken is a flat dashed line with no
// motion at all (see drawLane).
function idleLevel(now, phase) {
  return 0.16 + 0.07 * Math.sin(now / 1400 + phase);
}

function drawGrid(y0, h, width) {
  ctx.strokeStyle = tok.rekha;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let x = 0; x < width; x += 12) {
    ctx.moveTo(x + 0.5, y0);
    ctx.lineTo(x + 0.5, y0 + h);
  }
  for (let y = y0; y < y0 + h; y += 12) {
    ctx.moveTo(0, y + 0.5);
    ctx.lineTo(width, y + 0.5);
  }
  ctx.stroke();
}

function drawMainTrace(width, now) {
  drawGrid(0, MAIN_H, width);
  const baseline = MAIN_H / 2;
  const amp = MAIN_H * 0.42;
  ctx.beginPath();
  ctx.strokeStyle = tok.syahi;
  ctx.lineWidth = 2;
  ctx.setLineDash([]);
  let started = false;
  for (const s of samples) {
    const x = xForAtReal(s.atReal, now, width);
    if (x < -20) continue;
    const rate = amplitudeFor(s.eventsThisTick);
    const scoreBoost = (s.cityPulseScore || 0) / 100;
    const level = Math.min(1, rate * 0.7 + scoreBoost * 0.3);
    const y = baseline - level * amp;
    if (!started) { ctx.moveTo(x, y); started = true; } else { ctx.lineTo(x, y); }
  }
  // keep drawing to "now" even with no new samples yet — a flat live line,
  // not a frozen one (DESIGN.md §5).
  if (started) {
    const lastLevel = samples.length ? Math.min(1, amplitudeFor(samples[samples.length - 1].eventsThisTick)) : 0;
    ctx.lineTo(width, baseline - lastLevel * amp);
  }
  ctx.stroke();
}

function drawMarkers(width, now) {
  for (const m of markers) {
    const x = xForAtReal(m.atReal, now, width);
    m.el.style.display = x < -20 || x > width + 20 ? "none" : "block";
    m.el.style.left = `${x}px`;
    m.el.style.color = tokFor(tok, m.level);
    ctx.strokeStyle = tokFor(tok, m.level);
    ctx.lineWidth = 2;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, MAIN_H);
    ctx.stroke();
  }
  markers = markers.filter((m) => {
    const alive = now - m.atReal < (WINDOW_SEC + 5) * 1000;
    if (!alive) m.el.remove();
    return alive;
  });
}

function levelForFeedSample(feed, s, now, degraded, alert, phase) {
  if (degraded) return 0; // a flat dashed line, deliberately motionless — see drawLane
  let level = Math.max(amplitudeFor(s.byFeed[feed] || 0), idleLevel(now, phase));
  if (alert) {
    // "irregular-looking" — a bit of high-frequency jitter riding on top of
    // the normal deflection while this feed is implicated.
    level = Math.min(1, level + Math.abs(Math.sin(now / 220 + phase)) * 0.35);
  }
  return level;
}

// One feed's own small canvas: its trace only, no shared grid — at 18px tall
// a millimeter grid is just noise, the line itself carries the signal.
function drawLane(feed, now) {
  const lane = laneEls[feed];
  const lctx = lane.ctx;
  const width = lane.canvas.clientWidth || 1;
  if (!lctx || !width) return;

  lctx.save();
  lctx.scale(dpr, dpr);
  lctx.clearRect(0, 0, width, LANE_H);

  const health = feedHealth[feed];
  const degraded = health && health.state !== "live";
  const alert = feedAlertById.get(feed);
  const baseline = LANE_H / 2;
  const amp = LANE_H * 0.4;

  lctx.beginPath();
  lctx.lineWidth = 1.3;
  if (degraded) {
    lctx.strokeStyle = tok.rekha;
    lctx.setLineDash([3, 2]);
    lctx.moveTo(0, baseline);
    lctx.lineTo(width, baseline);
  } else {
    lctx.strokeStyle = alert ? tokFor(tok, alert.level) : tok.syahi;
    lctx.setLineDash([]);
    let started = false;
    for (const s of samples) {
      const x = xForAtReal(s.atReal, now, width);
      if (x < -20) continue;
      const level = levelForFeedSample(feed, s, s.atReal, degraded, alert, lane.phase);
      const y = baseline - level * amp;
      if (!started) { lctx.moveTo(x, y); started = true; } else { lctx.lineTo(x, y); }
    }
    const lastSample = samples[samples.length - 1] || { byFeed: {} };
    const lastLevel = levelForFeedSample(feed, lastSample, now, degraded, alert, lane.phase);
    if (started) lctx.lineTo(width, baseline - lastLevel * amp);
  }
  lctx.stroke();
  lctx.setLineDash([]);
  lctx.restore();
}

function frame() {
  const now = performance.now();
  const widthCss = root.clientWidth || 800;
  ctx.save();
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, widthCss, MAIN_H);
  ctx.fillStyle = tok.chuna;
  ctx.fillRect(0, 0, widthCss, MAIN_H);
  drawMainTrace(widthCss, now);
  drawMarkers(widthCss, now);
  ctx.restore();
  for (const feed of FEED_IDS) drawLane(feed, now);
  requestAnimationFrame(frame);
}

// A fresh page has no history, so the trace would otherwise render as a stub
// in the right-hand corner until five real minutes had elapsed. Backfilling the
// window with flat samples gives DESIGN.md §5's "flat live line" across the full
// width from the first frame; real ticks scroll in from the right and push these
// out. Value 0, so it reports nothing that did not happen.
function seedFlatWindow() {
  const now = performance.now();
  const step = 1000;
  for (let ago = WINDOW_SEC * 1000; ago > 0; ago -= step) {
    samples.push({
      atReal: now - ago,
      simTimeUtc: null,
      eventsThisTick: 0,
      byFeed: Object.fromEntries(FEED_IDS.map((f) => [f, 0])),
      cityPulseScore: 0,
      cityAlertLevel: "green",
    });
  }
}

// -------------------------------------------------------------- bootstrap
function init() {
  tok = readTokens();
  buildDom();
  seedFlatWindow();
  requestAnimationFrame(frame);

  // feedhealth WS messages only fire on a state *change* (main.py's tick loop
  // diffs against the previous row), so a feed that has been live since
  // before this page connected would otherwise never get its "Live" label.
  fetchState().then((state) => {
    for (const row of state.feed_health || []) applyFeedHealth(row);
  }).catch(() => { /* connectStream's own reconnect handles this; nothing to show yet */ });

  connectStream(onTick, onEvent, onSituation, onFeedHealth);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
