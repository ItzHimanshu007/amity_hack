/* js/api.js — shared data/interface layer.
 *
 * NOTE FOR INTEGRATION: this file does not exist yet in the shared repo at the
 * time Track B (situations/resident/controls/data room) started building, so
 * this is a local stub implementing the agreed signatures against the
 * documented + observed REST/WS endpoints. Track A owns js/api.js proper —
 * reconcile on merge. Every export below keeps the signature given in the
 * task brief so callers do not need to change when the real file lands:
 *
 *   connectStream(onTick, onEvent, onSituation, onFeedHealth)
 *   fetchState(), fetchSituation(id), fetchRaw(feed, n=50), fetchScorecard()
 *   sendControl(action, payload)
 *
 * Everything below `--- vocabulary & helpers ---` is extra (category/feed
 * labels, IST time formatting) that situations.js / resident.js / controls.js
 * / dataroom.js all need and CONTRACT.md fixes centrally — kept here once
 * rather than copy-pasted four times. Track A's real api.js may or may not
 * carry these; if it doesn't, the four files importing them here still work
 * standalone.
 */

const API_BASE = "http://127.0.0.1:8000";
const WS_URL = "ws://127.0.0.1:8000/stream";

// ---------------------------------------------------------------- REST ---

async function getJSON(path) {
  const res = await fetch(API_BASE + path);
  let body = null;
  try { body = await res.json(); } catch { /* no body */ }
  if (!res.ok) {
    const detail = (body && (body.detail || body.error)) || res.statusText;
    const err = new Error(detail);
    err.code = body && body.error;
    err.status = res.status;
    throw err;
  }
  return body;
}

export async function fetchState() {
  return getJSON("/state");
}

export async function fetchSituation(id) {
  return getJSON(`/situations/${encodeURIComponent(id)}`);
}

export async function fetchRaw(feed, n = 50) {
  return getJSON(`/raw/${encodeURIComponent(feed)}?n=${encodeURIComponent(n)}`);
}

export async function fetchScorecard() {
  return getJSON("/scorecard");
}

/**
 * POST /control. `payload` becomes the request's "value" — a feed id string
 * for kill_feed/resume_feed, a float for speed, a bookmark name or ISO
 * timestamp string for jump_to, {source, seconds} for delay_feed, an
 * event_id string for inject_duplicate, a scenario name for set_scenario,
 * or omitted for play/pause.
 */
export async function sendControl(action, payload) {
  const res = await fetch(API_BASE + "/control", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, value: payload === undefined ? null : payload }),
  });
  let body = null;
  try { body = await res.json(); } catch { /* no body */ }
  if (!res.ok) {
    const detail = (body && (body.detail || body.error)) || res.statusText;
    const err = new Error(detail);
    err.code = body && body.error;
    err.status = res.status;
    throw err;
  }
  return body;
}

// ------------------------------------------------------------ WS /stream ---
//
// One shared socket for the whole page: every call to connectStream() adds
// its callbacks to a shared listener list rather than opening a second
// connection, so Track A's map/naadi code and this track's cards/controls/
// data room can each call connectStream() independently without doubling up
// on sockets. Returns an unsubscribe function.

const listeners = new Set();
let socket = null;
let reconnectTimer = null;
let missedTickTimer = null;
let lastTickAt = 0;

function dispatch(kind, ...args) {
  for (const l of listeners) {
    const fn = l[kind];
    if (typeof fn === "function") {
      try { fn(...args); } catch (e) { console.error(`[api] ${kind} handler failed`, e); }
    }
  }
}

function armMissedTickWatch() {
  clearInterval(missedTickTimer);
  missedTickTimer = setInterval(() => {
    if (lastTickAt && Date.now() - lastTickAt > 3000) {
      window.dispatchEvent(new CustomEvent("stream:connection", { detail: { connected: false } }));
    }
  }, 1000);
}

function openSocket() {
  socket = new WebSocket(WS_URL);
  socket.addEventListener("open", () => {
    window.dispatchEvent(new CustomEvent("stream:connection", { detail: { connected: true } }));
  });
  socket.addEventListener("message", (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    switch (msg.type) {
      case "tick":
        lastTickAt = Date.now();
        window.dispatchEvent(new CustomEvent("stream:connection", { detail: { connected: true } }));
        dispatch("onTick", msg.data, msg);
        break;
      case "event":
        dispatch("onEvent", msg.data, msg);
        break;
      case "situation":
        dispatch("onSituation", msg.data, msg.action, msg);
        break;
      case "feedhealth":
        dispatch("onFeedHealth", msg.data, msg);
        break;
      default:
        break;
    }
  });
  socket.addEventListener("close", scheduleReconnect);
  socket.addEventListener("error", () => { try { socket.close(); } catch { /* ignore */ } });
}

function scheduleReconnect() {
  window.dispatchEvent(new CustomEvent("stream:connection", { detail: { connected: false } }));
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    openSocket();
  }, 1500);
}

export function connectStream(onTick, onEvent, onSituation, onFeedHealth) {
  const entry = { onTick, onEvent, onSituation, onFeedHealth };
  listeners.add(entry);
  if (!socket) {
    openSocket();
    armMissedTickWatch();
  }
  return () => listeners.delete(entry);
}

// ------------------------------------------------------- map -> rail link ---
// Track A's map dispatches 'situation:selected' with {detail: situationId}
// when a cell/outline is clicked. Small wrapper so callers don't repeat the
// addEventListener boilerplate.
export function onSituationSelected(handler) {
  const fn = (ev) => handler(ev.detail);
  window.addEventListener("situation:selected", fn);
  return () => window.removeEventListener("situation:selected", fn);
}

export function selectSituation(situationId) {
  window.dispatchEvent(new CustomEvent("situation:selected", { detail: situationId }));
}

// --------------------------------------------------- vocabulary & helpers ---
// CONTRACT.md §B, mirrored client-side (contract_constants.py is the
// server's copy — this is the frontend's, per CONTRACT.md §H's toIST() note).

export const CATEGORY_LABELS = {
  "weather.rain":            { en: "Heavy rain",           hi: "तेज़ बारिश" },
  "weather.heat":            { en: "Extreme heat",         hi: "अत्यधिक गर्मी" },
  "air.pm25":                { en: "Poor air",             hi: "खराब हवा" },
  "power.outage":            { en: "Power cut",            hi: "बिजली कटौती" },
  "traffic.signal_down":     { en: "Signal not working",   hi: "सिग्नल बंद" },
  "transit.delay":           { en: "Bus running late",     hi: "बस देरी से" },
  "complaint.waterlogging":  { en: "Waterlogging",         hi: "जलभराव" },
  "complaint.garbage":       { en: "Garbage not cleared",  hi: "कचरा नहीं उठा" },
  "complaint.streetlight":   { en: "Streetlight out",      hi: "स्ट्रीटलाइट बंद" },
  "complaint.road_damage":   { en: "Road damage",          hi: "सड़क खराब" },
  "complaint.smoke":         { en: "Smoke or burning",     hi: "धुआँ या जलना" },
};

export const FEED_LABELS = {
  weather_imd:      { en: "Weather station",      hi: "मौसम स्टेशन" },
  civic_complaints: { en: "Civic complaints",     hi: "नागरिक शिकायतें" },
  power_discom:     { en: "Power grid",           hi: "बिजली ग्रिड" },
  transit_gtfs:     { en: "City buses",           hi: "शहर की बसें" },
  air_sensors:      { en: "Air quality sensors",  hi: "वायु गुणवत्ता सेंसर" },
};

export const FEED_IDS = Object.keys(FEED_LABELS);

// DESIGN.md §1 status words. English is quoted verbatim in DESIGN.md; DESIGN.md
// does not supply Hindi for these four, so the _hi values here are this
// track's own translation — flag for a Hindi-speaking reviewer before ship.
export const ALERT_WORDS = {
  green:  { en: "All normal",   hi: "सब सामान्य है" },
  yellow: { en: "Be aware",     hi: "सतर्क रहें" },
  orange: { en: "Be prepared",  hi: "तैयार रहें" },
  red:    { en: "Take action",  hi: "कार्रवाई करें" },
};

// CONTRACT.md §E documents speeds (1, 2, 4, 8, 16). The LIVE backend's actual
// bad_request error for an invalid speed lists a wider set — confirmed
// 2026-09-25 against http://127.0.0.1:8000/control. Using the live set so the
// "up to 200x" requirement is met; reconcile with CONTRACT.md if it's amended.
export const SPEEDS = [1, 2, 4, 8, 16, 32, 64, 100, 200];

// REPLAY_BOOKMARKS: not documented in CONTRACT.md. Names + resulting
// sim_time_utc confirmed live via jump_to against the running
// monsoon_evening scenario. Labels are this track's plain-language wording
// per DESIGN.md's button rule ("6pm, before the storm", not the bookmark id).
export const BOOKMARKS = [
  { id: "window_start",         label_en: "Jump to 5:30pm — simulation start" },
  { id: "storm_onset",          label_en: "Jump to 6:25pm — rain begins" },
  { id: "gt003_onset",          label_en: "Jump to 6:35pm — smoke reported" },
  { id: "first_situation",      label_en: "Jump to 6:50pm — first situation forms" },
  { id: "gt002_onset",          label_en: "Jump to 6:50pm — power cut begins" },
  { id: "feed_kill_demo_point", label_en: "Jump to 7:05pm — feed-outage demo point" },
  { id: "peak_activity",        label_en: "Jump to 7:30pm — peak activity" },
  { id: "window_end",           label_en: "Jump to 8:30pm — simulation end" },
];

/** ISO UTC string -> "6:42 pm" in IST. The one place this side does the conversion. */
export function toISTClock(utcIso) {
  if (!utcIso) return "";
  return new Date(utcIso).toLocaleTimeString("en-IN", {
    timeZone: "Asia/Kolkata", hour: "numeric", minute: "2-digit", hour12: true,
  }).toLowerCase();
}

/** Whole seconds -> "17 minutes" / "1 hour 5 minutes" / "less than a minute". */
export function formatDuration(totalSec) {
  const sec = Math.max(0, Math.round(totalSec));
  if (sec < 60) return "less than a minute";
  const mins = Math.round(sec / 60);
  if (mins < 60) return `${mins} minute${mins === 1 ? "" : "s"}`;
  const hrs = Math.floor(mins / 60);
  const rem = mins % 60;
  return rem === 0
    ? `${hrs} hour${hrs === 1 ? "" : "s"}`
    : `${hrs} hour${hrs === 1 ? "" : "s"} ${rem} minute${rem === 1 ? "" : "s"}`;
}

/** "<fromUtcIso> vs <nowUtcIso>" -> "24 minutes ago" / "14 seconds ago". */
export function timeAgo(fromUtcIso, nowUtcIso) {
  if (!fromUtcIso || !nowUtcIso) return "";
  const diffSec = Math.max(0, Math.round((new Date(nowUtcIso) - new Date(fromUtcIso)) / 1000));
  if (diffSec < 5) return "just now";
  if (diffSec < 60) return `${diffSec} second${diffSec === 1 ? "" : "s"} ago`;
  return `${formatDuration(diffSec)} ago`;
}
