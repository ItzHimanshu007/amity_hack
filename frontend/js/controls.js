/* js/controls.js — playback, replay markers, feed and scenario controls
 * (#sim-controls), rendered as the grouped Simulation console.
 * CONTRACT.md §E (POST /control). Every control calls the same
 * sendControl() action/value it always has; this file only decides how
 * they're grouped and how the live simulation state is reflected on them.
 *
 * Owns: #sim-controls on index.html.
 */

import { fetchState, sendControl, connectStream, SPEEDS, BOOKMARKS, FEED_IDS, FEED_LABELS, toISTClock } from "./api.js";

// backend/api/control.py's set_scenario accepts only "monsoon_evening" today;
// the other ids are kept for when the backend grows them, but hidden until then.
const AVAILABLE_SCENARIOS = new Set(["monsoon_evening"]);
const SCENARIOS = [
  { id: "monsoon_evening", en: "Storm (monsoon evening)", short: "Storm", sub: "Monsoon evening" },
  { id: "transformer_failure", en: "Transformer failure", short: "Transformer failure", sub: null },
  { id: "garbage_fire", en: "Garbage fire", short: "Garbage fire", sub: null },
  { id: "calm", en: "Calm (no incidents)", short: "Calm", sub: "No incidents" },
];

const DELAY_SECONDS = 300; // fixed 5-minute delay for the "Delay 5m" button

const FEED_STATE_WORD = { live: "Healthy", stale: "Stale", killed: "Stopped", error: "Error" };

let root;
let els = {};                       // named element refs for live updates
let feedHealthById = new Map();     // feed -> feed_health row, for Stop/Start labels
let delayedFeeds = new Map();       // feed -> seconds, from confirmed delay_feed responses
let recentEventIds = [];            // small ring buffer for "Send a duplicate record"
const MAX_RECENT_EVENTS = 200;
let lastSim = null;

// "Jump to 6:25pm — rain begins" -> { time: "6:25 PM", text: "Rain begins", minutes }
const MARKERS = BOOKMARKS.map((bm) => {
  const m = /^Jump to (\d{1,2}):(\d{2})(am|pm)\s+—\s+(.+)$/i.exec(bm.label_en);
  if (!m) return { ...bm, time: "", text: bm.label_en, minutes: null };
  let h = Number(m[1]) % 12;
  if (m[3].toLowerCase() === "pm") h += 12;
  const text = m[4].charAt(0).toUpperCase() + m[4].slice(1);
  return { ...bm, time: `${m[1]}:${m[2]} ${m[3].toUpperCase()}`, text, minutes: h * 60 + Number(m[2]) };
});

function istMinutes(utcIso) {
  const d = new Date(utcIso);
  return (d.getUTCHours() * 60 + d.getUTCMinutes() + 330) % 1440;
}

function init() {
  root = document.getElementById("sim-controls");
  if (!root) return;

  root.innerHTML = "";
  root.classList.add("cc");

  const colA = div("cc__col");
  colA.appendChild(buildPlaybackSection());
  colA.appendChild(buildMarkerSection());
  const colB = div("cc__col");
  colB.appendChild(buildFeedSection());
  const colC = div("cc__col");
  colC.appendChild(buildScenarioSection());
  colC.appendChild(buildAdvancedSection());
  root.appendChild(colA);
  root.appendChild(colB);
  root.appendChild(colC);

  fetchState().then((state) => {
    for (const row of state.feed_health || []) feedHealthById.set(row.feed, row);
    for (const ev of state.events || []) pushRecentEvent(ev.event_id);
    setSimStatus(state.sim);
    refreshFeedButtons();
  }).catch((err) => {
    els.playbackMsg.textContent = `Not connected to the backend yet (${err.message}).`;
  });

  connectStream(
    (tickData) => setSimStatus(tickData),
    (eventData) => pushRecentEvent(eventData.event_id),
    null, // situations are situations.js's concern
    (feedHealthData) => { feedHealthById.set(feedHealthData.feed, feedHealthData); refreshFeedButtons(); },
  );
}

function pushRecentEvent(id) {
  recentEventIds.push(id);
  if (recentEventIds.length > MAX_RECENT_EVENTS) recentEventIds.shift();
}

function trimSpeed(v) {
  return Number.isInteger(v) ? String(v) : String(v).replace(/\.0+$/, "");
}

function setSimStatus(sim) {
  if (!sim) return;
  lastSim = { ...lastSim, ...sim };
  window.dispatchEvent(new CustomEvent("sim:status", { detail: lastSim }));
  const playing = lastSim.state === "play";

  els.stateDot.className = `cc-status__dot ${playing ? "is-playing" : "is-paused"}`;
  els.stateWord.textContent = playing ? "Playing" : "Paused";
  els.speedWord.textContent = lastSim.speed != null ? `${trimSpeed(lastSim.speed)}×` : "";
  if (lastSim.sim_time_utc) els.clock.textContent = `${toISTClock(lastSim.sim_time_utc)} IST`;

  els.playBtn.classList.toggle("is-active", playing);
  els.playBtn.setAttribute("aria-pressed", String(playing));
  els.pauseBtn.classList.toggle("is-active", !playing);
  els.pauseBtn.setAttribute("aria-pressed", String(!playing));

  for (const btn of els.speedBtns) {
    const on = Number(btn.dataset.speed) === Number(lastSim.speed);
    btn.classList.toggle("is-active", on);
    btn.setAttribute("aria-pressed", String(on));
  }

  if (lastSim.sim_time_utc) refreshMarkers(istMinutes(lastSim.sim_time_utc));
}

function refreshMarkers(now) {
  const passed = MARKERS.filter((m) => m.minutes != null && m.minutes <= now);
  const current = passed.length ? Math.max(...passed.map((m) => m.minutes)) : null;
  for (const btn of els.markerBtns) {
    const mins = Number(btn.dataset.minutes);
    btn.classList.toggle("is-active", current != null && mins === current);
    btn.classList.toggle("is-passed", current != null && mins < current);
  }
  const start = MARKERS.find((m) => m.id === "window_start")?.minutes;
  const end = MARKERS.find((m) => m.id === "window_end")?.minutes;
  if (start != null && end != null && end > start) {
    const pct = Math.min(100, Math.max(0, ((now - start) / (end - start)) * 100));
    els.progressFill.style.width = `${pct}%`;
  }
}

async function runControl(action, value, msgEl, describe) {
  try {
    const res = await sendControl(action, value);
    if (res && res.sim) setSimStatus(res.sim);
    if (msgEl) {
      msgEl.classList.remove("is-error");
      msgEl.textContent = describe ? describe(res) : "";
    }
    return res;
  } catch (err) {
    if (msgEl) {
      // The backend nests its {error, detail} JSON inside FastAPI's detail string.
      let detail = err.message;
      try { detail = JSON.parse(err.message).detail || detail; } catch { /* plain text */ }
      msgEl.classList.add("is-error");
      msgEl.textContent = `Could not do that: ${detail}`;
    }
    return null;
  }
}

// ------------------------------------------------------------- playback ---

function buildPlaybackSection() {
  const sec = section("Playback", "Synthetic civic stream · deterministic replay", "cc-section--primary");

  const status = div("cc-status");
  els.stateDot = span("cc-status__dot");
  els.clock = span("cc-status__clock data", "--:-- IST");
  els.speedWord = span("cc-status__speed data", "");
  els.stateWord = span("cc-status__state", "");
  status.append(els.stateDot, els.clock, els.speedWord, els.stateWord);
  sec.querySelector(".cc-section__head").appendChild(status);

  const row = div("cc-playback");
  els.playBtn = button("▶ Play", "cc-btn cc-btn--transport", () => runControl("play", undefined, els.playbackMsg));
  els.playBtn.setAttribute("aria-label", "Play the simulation");
  els.pauseBtn = button("❚❚ Pause", "cc-btn cc-btn--transport", () => runControl("pause", undefined, els.playbackMsg));
  els.pauseBtn.setAttribute("aria-label", "Pause the simulation");
  row.append(els.playBtn, els.pauseBtn);

  const speedWrap = div("cc-speed");
  speedWrap.appendChild(span("cc-speed__label", "Speed"));
  const seg = div("cc-seg");
  seg.setAttribute("role", "group");
  seg.setAttribute("aria-label", "Simulation speed");
  els.speedBtns = [];
  for (const speed of SPEEDS) {
    const btn = button(`${trimSpeed(speed)}×`, "cc-seg__btn", () => runControl("speed", speed, els.playbackMsg));
    btn.dataset.speed = String(speed);
    btn.setAttribute("aria-label", `Run at ${trimSpeed(speed)}× speed`);
    seg.appendChild(btn);
    els.speedBtns.push(btn);
  }
  speedWrap.appendChild(seg);
  row.appendChild(speedWrap);
  sec.appendChild(row);

  els.playbackMsg = msgLine();
  sec.appendChild(els.playbackMsg);
  return sec;
}

// ------------------------------------------------------- replay markers ---

function buildMarkerSection() {
  const sec = section("Replay markers", "Jump the clock to a key moment");

  const track = div("cc-progress");
  els.progressFill = div("cc-progress__fill");
  track.appendChild(els.progressFill);
  sec.appendChild(track);

  const list = div("cc-markers");
  els.markerBtns = [];
  for (const m of MARKERS) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "cc-marker";
    btn.dataset.minutes = String(m.minutes);
    btn.setAttribute("aria-label", m.label_en);
    btn.title = m.label_en;
    btn.append(span("cc-marker__time data", m.time), span("cc-marker__text", m.text));
    btn.addEventListener("click", () => runControl("jump_to", m.id, els.markerMsg));
    list.appendChild(btn);
    els.markerBtns.push(btn);
  }
  sec.appendChild(list);
  els.markerMsg = msgLine();
  sec.appendChild(els.markerMsg);
  return sec;
}

// ---------------------------------------------------------------- feeds ---

function buildFeedSection() {
  const sec = section("Feed health", "Stop or delay a feed to test degradation");
  const list = div("cc-feeds");
  for (const feed of FEED_IDS) {
    const name = FEED_LABELS[feed]?.en || feed;
    const row = div("cc-feed");
    row.dataset.feed = feed;

    const dot = span("cc-feed__dot");
    const nameEl = span("cc-feed__name", name);
    const stateEl = span("cc-feed__state");
    const delayTag = span("cc-feed__delay");

    const toggleBtn = button("", "cc-btn cc-btn--small sim-controls__feed-toggle", async () => {
      const isKilled = feedHealthById.get(feed)?.state === "killed";
      const res = await runControl(isKilled ? "resume_feed" : "kill_feed", feed, els.feedMsg);
      if (res && isKilled) { delayedFeeds.delete(feed); refreshFeedButtons(); }
    });

    const delayBtn = button("Delay 5m", "cc-btn cc-btn--small cc-btn--quiet", async () => {
      const res = await runControl("delay_feed", { source: feed, seconds: DELAY_SECONDS }, els.feedMsg,
        () => `${name} is now held back by 5 minutes.`);
      if (res && res.ok) { delayedFeeds.set(feed, res.delay_seconds ?? DELAY_SECONDS); refreshFeedButtons(); }
    });
    delayBtn.setAttribute("aria-label", `Delay ${name} by 5 minutes`);
    delayBtn.title = `Delay ${name} by 5 minutes`;

    const actions = div("cc-feed__actions");
    actions.append(toggleBtn, delayBtn);
    row.append(dot, nameEl, stateEl, delayTag, actions);
    list.appendChild(row);
  }
  sec.appendChild(list);
  els.feedMsg = msgLine();
  sec.appendChild(els.feedMsg);
  return sec;
}

function refreshFeedButtons() {
  if (!root) return;
  for (const row of root.querySelectorAll(".cc-feed")) {
    const feed = row.dataset.feed;
    const health = feedHealthById.get(feed);
    const state = health?.state || "stale";
    const isKilled = state === "killed";
    const name = FEED_LABELS[feed]?.en || feed;

    row.className = `cc-feed cc-feed--${state}`;
    const stateEl = row.querySelector(".cc-feed__state");
    stateEl.textContent = FEED_STATE_WORD[state] || state;
    stateEl.title = health?.message || "";

    const delay = delayedFeeds.get(feed);
    const delayTag = row.querySelector(".cc-feed__delay");
    delayTag.textContent = delay ? `+${Math.round(delay / 60)} min delay` : "";

    const btn = row.querySelector(".sim-controls__feed-toggle");
    btn.textContent = isKilled ? "Start" : "Stop";
    btn.classList.toggle("cc-btn--danger", !isKilled);
    btn.classList.toggle("cc-btn--resume", isKilled);
    const full = isKilled ? `Start ${name.toLowerCase()} feed` : `Stop ${name.toLowerCase()} feed`;
    btn.setAttribute("aria-label", full);
    btn.title = full;
  }
}

// ------------------------------------------------------------- scenario ---

function buildScenarioSection() {
  const sec = section("Scenarios", "Load a predefined synthetic event pattern.");
  const grid = div("cc-scenarios");
  for (const sc of SCENARIOS.filter((x) => AVAILABLE_SCENARIOS.has(x.id))) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "cc-scenario";
    btn.setAttribute("aria-label", `Load ${sc.en.toLowerCase()} scenario`);
    btn.title = `Load ${sc.en.toLowerCase()} scenario`;
    btn.appendChild(span("cc-scenario__name", `Load ${sc.short.toLowerCase()} scenario`));
    btn.appendChild(span("cc-scenario__sub", `${sc.sub || sc.en} · resets the replay to 5:30 PM`));
    btn.addEventListener("click", async () => {
      const res = await runControl("set_scenario", sc.id, els.scenarioMsg,
        () => `Scenario set to ${sc.en.toLowerCase()}.`);
      if (res) { delayedFeeds.clear(); refreshFeedButtons(); }
    });
    grid.appendChild(btn);
  }
  sec.appendChild(grid);
  els.scenarioMsg = msgLine();
  sec.appendChild(els.scenarioMsg);
  return sec;
}

// ------------------------------------------------------------- advanced ---

function buildAdvancedSection() {
  const sec = section("Advanced · feed testing", null, "cc-section--advanced");
  const dupBtn = button("Send duplicate record", "cc-btn cc-btn--small cc-btn--quiet", () => {
    if (recentEventIds.length === 0) {
      els.advancedMsg.textContent = "No reports seen yet to duplicate.";
      return;
    }
    const id = recentEventIds[Math.floor(Math.random() * recentEventIds.length)];
    runControl("inject_duplicate", id, els.advancedMsg, () => "Sent a duplicate of one report again.");
  });
  dupBtn.setAttribute("aria-label", "Send a duplicate record");
  sec.appendChild(dupBtn);
  els.advancedMsg = msgLine();
  sec.appendChild(els.advancedMsg);
  return sec;
}

// ----------------------------------------------------------------- util ---

function div(cls) {
  const el = document.createElement("div");
  el.className = cls;
  return el;
}

function span(cls, text) {
  const el = document.createElement("span");
  el.className = cls;
  if (text != null) el.textContent = text;
  return el;
}

function button(text, cls, onClick) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = cls;
  btn.textContent = text;
  btn.addEventListener("click", onClick);
  return btn;
}

function section(title, sub, extraCls) {
  const sec = div(`cc-section${extraCls ? ` ${extraCls}` : ""}`);
  const head = div("cc-section__head");
  const titles = div("cc-section__titles");
  titles.appendChild(span("cc-section__title", title));
  if (sub) titles.appendChild(span("cc-section__sub", sub));
  head.appendChild(titles);
  sec.appendChild(head);
  return sec;
}

function msgLine() {
  const p = document.createElement("p");
  p.className = "cc-msg";
  p.setAttribute("aria-live", "polite");
  return p;
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
