// js/naadi.js — the Naadi strip. DESIGN.md §5: the one bold ECG-style
// element, hand-drawn on canvas, plus per-feed lanes and the replay
// scrubber (task brief item 3). Driven entirely by WS "tick"/"feedhealth"/
// "situation" messages — never a local simulated clock.
//
// Layout inside #naadi-strip, top to bottom:
//   1. Main lane   72px  — DESIGN.md-exact: grid, bold 2px city pulse trace,
//                          per-situation tick marks, live value at data scale.
//   2. Feed lanes  100px — five thin per-feed traces (amplitude = that
//                          feed's event arrival rate this tick), dashed grey
//                          when the feed is stale/killed/error.
//   3. Scrub row    32px — play/pause + a speed control. CONTRACT.md's
//                          POST /control exposes only play, pause, speed
//                          (1/2/4/8/16), kill_feed, resume_feed, set_scenario
//                          — there is no jump_to and no bookmark list, so
//                          this does not fake arbitrary-position seeking.
//                          Dragging snaps to the nearest of the five allowed
//                          speeds, which is the only "position" the backend
//                          actually exposes, and says so.
//
// All line art is canvas; all text is real HTML so it can use the actual
// type-scale tokens (incl. tabular-nums and the wdth axis), which a canvas
// fillText cannot reproduce faithfully.

import { connectStream, sendControl } from "./api.js";

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
const MAIN_H = 72, LANE_H = 20, LANES_H = LANE_H * FEED_IDS.length, CTRL_H = 32, RULE_H = 1;
const TOTAL_H = MAIN_H + RULE_H + LANES_H + RULE_H + CTRL_H;

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
let mainValueEl = null, pulseEl = null, clockEl = null, playBtn = null, speedFill = null, speedHandle = null, markersEl = null;
const laneEls = {}; // feedId -> { label, state }

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
let draggingSpeed = false;

// ------------------------------------------------------------------- DOM --
function buildDom() {
  root = document.getElementById("naadi-strip");
  root.innerHTML = "";
  root.classList.add("nn-naadi");
  root.style.height = `${TOTAL_H}px`;

  canvas = document.createElement("canvas");
  canvas.className = "nn-naadi__canvas";
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
  root.appendChild(markersEl);

  const lanesMeta = document.createElement("div");
  lanesMeta.className = "nn-naadi__lanes-meta";
  lanesMeta.style.top = `${MAIN_H + RULE_H}px`;
  lanesMeta.style.height = `${LANES_H}px`;
  for (const feed of FEED_IDS) {
    const row = document.createElement("div");
    row.className = "nn-naadi__lane-row";
    row.style.height = `${LANE_H}px`;
    const label = document.createElement("span");
    label.className = "nn-naadi__lane-label label";
    label.textContent = FEED_LABELS[feed].en;
    const state = document.createElement("span");
    state.className = "nn-naadi__lane-state label";
    row.appendChild(label);
    row.appendChild(state);
    lanesMeta.appendChild(row);
    laneEls[feed] = { label, state, row };
  }
  root.appendChild(lanesMeta);

  const controls = document.createElement("div");
  controls.className = "nn-naadi__controls";
  controls.style.top = `${MAIN_H + RULE_H + LANES_H + RULE_H}px`;
  controls.style.height = `${CTRL_H}px`;

  playBtn = document.createElement("button");
  playBtn.type = "button";
  playBtn.className = "nn-naadi__playbtn body";
  playBtn.textContent = "Pause simulation";
  playBtn.addEventListener("click", onPlayToggle);
  controls.appendChild(playBtn);

  const track = document.createElement("div");
  track.className = "nn-naadi__speedtrack";
  track.setAttribute("role", "slider");
  track.setAttribute("aria-label", "Simulation speed");
  track.setAttribute("tabindex", "0");
  speedFill = document.createElement("div");
  speedFill.className = "nn-naadi__speedtrack-fill";
  track.appendChild(speedFill);
  for (const s of SPEEDS) {
    const tick = document.createElement("span");
    tick.className = "nn-naadi__speed-tick label";
    tick.textContent = `${s}x`;
    tick.style.left = `${(SPEEDS.indexOf(s) / (SPEEDS.length - 1)) * 100}%`;
    track.appendChild(tick);
  }
  speedHandle = document.createElement("div");
  speedHandle.className = "nn-naadi__speedtrack-handle";
  track.appendChild(speedHandle);
  wireSpeedDrag(track);
  controls.appendChild(track);

  clockEl = document.createElement("span");
  clockEl.className = "nn-naadi__clock data";
  controls.appendChild(clockEl);

  const note = document.createElement("span");
  note.className = "nn-naadi__seek-note label";
  note.textContent = "No jump-to-a-moment control from the backend — drag sets speed instead";
  controls.appendChild(note);

  root.appendChild(controls);

  resizeCanvas();
  window.addEventListener("resize", resizeCanvas);
}

function resizeCanvas() {
  dpr = window.devicePixelRatio || 1;
  const w = root.clientWidth || 800;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round((MAIN_H + LANES_H) * dpr);
  canvas.style.width = `${w}px`;
  canvas.style.height = `${MAIN_H + LANES_H}px`;
  ctx = canvas.getContext("2d");
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

function speedFractionFromClientX(track, clientX) {
  const rect = track.getBoundingClientRect();
  return Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
}
function nearestSpeedIndex(fraction) {
  return Math.round(fraction * (SPEEDS.length - 1));
}
function setSpeedHandlePosition(fraction) {
  speedHandle.style.left = `${fraction * 100}%`;
  speedFill.style.width = `${fraction * 100}%`;
}

function wireSpeedDrag(track) {
  function onMove(clientX) {
    const frac = speedFractionFromClientX(track, clientX);
    setSpeedHandlePosition(nearestSpeedIndex(frac) / (SPEEDS.length - 1));
  }
  function onUp(clientX) {
    draggingSpeed = false;
    const frac = speedFractionFromClientX(track, clientX);
    const idx = nearestSpeedIndex(frac);
    const value = SPEEDS[idx];
    if (value !== lastTick.speed) sendControl("speed", value);
    window.removeEventListener("pointermove", moveHandler);
    window.removeEventListener("pointerup", upHandler);
  }
  let moveHandler, upHandler;
  track.addEventListener("pointerdown", (e) => {
    draggingSpeed = true;
    onMove(e.clientX);
    moveHandler = (ev) => onMove(ev.clientX);
    upHandler = (ev) => onUp(ev.clientX);
    window.addEventListener("pointermove", moveHandler);
    window.addEventListener("pointerup", upHandler, { once: true });
  });
  track.addEventListener("keydown", (e) => {
    const cur = SPEEDS.indexOf(lastTick.speed) >= 0 ? SPEEDS.indexOf(lastTick.speed) : 0;
    if (e.key === "ArrowRight" && cur < SPEEDS.length - 1) sendControl("speed", SPEEDS[cur + 1]);
    if (e.key === "ArrowLeft" && cur > 0) sendControl("speed", SPEEDS[cur - 1]);
  });
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

  if (!draggingSpeed) {
    const idx = SPEEDS.indexOf(tick.speed);
    setSpeedHandlePosition((idx >= 0 ? idx : 0) / (SPEEDS.length - 1));
  }
  playBtn.textContent = tick.state === "play" ? "Pause simulation" : "Resume simulation";
  clockEl.textContent = `${toIST(tick.sim_time_utc)} IST · ${tick.speed}x · ${tick.state === "play" ? "Playing" : "Paused"}`;

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

function onFeedHealth(health) {
  feedHealth[health.feed] = health;
  const cell = laneEls[health.feed];
  if (!cell) return;
  cell.row.classList.toggle("nn-naadi__lane-row--stale", health.state === "stale");
  cell.row.classList.toggle("nn-naadi__lane-row--killed", health.state === "killed");
  cell.row.classList.toggle("nn-naadi__lane-row--error", health.state === "error");
  if (health.state === "live") {
    cell.state.textContent = "";
  } else {
    // health.message already carries the plain-language reason verbatim
    // ("No update for 14 min" / "Stopped by operator"), per CONTRACT.md §E.
    cell.state.textContent = health.message || health.state;
  }
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

function drawFeedLanes(width, now) {
  let y0 = MAIN_H;
  for (const feed of FEED_IDS) {
    drawGrid(y0, LANE_H, width);
    const health = feedHealth[feed];
    const degraded = health && health.state !== "live";
    const alert = feedAlertById.get(feed);
    const baseline = y0 + LANE_H / 2;
    const amp = LANE_H * 0.4;

    ctx.beginPath();
    ctx.lineWidth = 1.2;
    if (degraded) {
      ctx.strokeStyle = tok.dhool;
      ctx.setLineDash([3, 2]);
    } else if (alert) {
      ctx.strokeStyle = tokFor(tok, alert.level);
      ctx.setLineDash([]);
    } else {
      ctx.strokeStyle = tok.dhool;
      ctx.setLineDash([]);
    }
    let started = false;
    for (const s of samples) {
      const x = xForAtReal(s.atReal, now, width);
      if (x < -20) continue;
      let level = amplitudeFor(s.byFeed[feed] || 0);
      if (alert) {
        // "irregular-looking" — a bit of high-frequency jitter riding on
        // top of the normal deflection while this feed is implicated.
        level = Math.min(1, level + Math.abs(Math.sin(x * 0.9)) * 0.35);
      }
      const y = degraded ? baseline : baseline - level * amp;
      if (!started) { ctx.moveTo(x, y); started = true; } else { ctx.lineTo(x, y); }
    }
    if (started) ctx.lineTo(width, degraded ? baseline : baseline - amplitudeFor((samples[samples.length - 1] || { byFeed: {} }).byFeed[feed] || 0) * amp);
    ctx.stroke();
    ctx.setLineDash([]);

    y0 += LANE_H;
  }
}

function frame() {
  const now = performance.now();
  const widthCss = root.clientWidth || 800;
  ctx.save();
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, widthCss, MAIN_H + LANES_H);
  ctx.fillStyle = tok.chuna;
  ctx.fillRect(0, 0, widthCss, MAIN_H + LANES_H);
  drawMainTrace(widthCss, now);
  drawFeedLanes(widthCss, now);
  drawMarkers(widthCss, now);
  ctx.restore();
  requestAnimationFrame(frame);
}

// -------------------------------------------------------------- bootstrap
function init() {
  tok = readTokens();
  buildDom();
  requestAnimationFrame(frame);
  connectStream(onTick, onEvent, onSituation, onFeedHealth);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
