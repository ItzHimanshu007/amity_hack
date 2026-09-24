/* js/controls.js — playback, chaos and scenario controls (#sim-controls).
 * CONTRACT.md §E (POST /control). DESIGN.md's button-naming rule: every
 * button names its exact action, no "Submit"/"Speed"/icon-only controls.
 *
 * Owns: #sim-controls on index.html.
 */

import { fetchState, sendControl, connectStream, SPEEDS, BOOKMARKS, FEED_IDS, FEED_LABELS } from "./api.js";

// CONTRACT.md §G only defines "monsoon_evening" today (README's
// `sim.generate --scenario calm` confirms "calm" is at least a valid data-
// generation name). The other three ids are this track's best-effort guess
// at what a storm/transformer/garbage/calm selector maps to — NOT verified
// against a live set_scenario call, because that action resets the whole
// simulation and Track A may be relying on the current run. Reconcile the
// ids against whatever backend/sim/scenario.py actually registers before
// wiring this up for real in the demo.
const SCENARIOS = [
  { id: "monsoon_evening", en: "Storm (monsoon evening)", hi: "तूफ़ान (मानसून शाम)" },
  { id: "transformer_failure", en: "Transformer failure", hi: "ट्रांसफार्मर खराबी" },
  { id: "garbage_fire", en: "Garbage fire", hi: "कचरे में आग" },
  { id: "calm", en: "Calm (no incidents)", hi: "शांत (कोई घटना नहीं)" },
];

const DELAY_SECONDS = 300; // fixed 5-minute delay for the "Delay <feed>" button

let root, statusEl;
let feedHealthById = new Map();     // feed -> feed_health row, for Stop/Start labels
let recentEventIds = [];            // small ring buffer for "Send a duplicate record"
const MAX_RECENT_EVENTS = 200;

function init() {
  root = document.getElementById("sim-controls");
  if (!root) return;

  root.innerHTML = "";
  statusEl = document.createElement("p");
  statusEl.className = "sim-controls__status";
  root.appendChild(statusEl);

  root.appendChild(buildPlaybackGroup());
  root.appendChild(buildSpeedGroup());
  root.appendChild(buildBookmarkGroup());
  root.appendChild(buildFeedGroup());
  root.appendChild(buildScenarioGroup());

  fetchState().then((state) => {
    for (const row of state.feed_health || []) feedHealthById.set(row.feed, row);
    for (const ev of state.events || []) pushRecentEvent(ev.event_id);
    setSimStatus(state.sim);
    refreshFeedButtons();
  }).catch((err) => {
    statusEl.textContent = `Not connected to the backend yet (${err.message}).`;
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

function setSimStatus(sim) {
  if (!sim) return;
  const state = sim.state === "play" ? "playing" : "paused";
  const speed = sim.speed != null ? `${trimSpeed(sim.speed)}×` : "";
  statusEl.textContent = `Currently ${state}${speed ? ` at ${speed} speed` : ""}.`;
}

function trimSpeed(v) {
  return Number.isInteger(v) ? String(v) : String(v).replace(/\.0+$/, "");
}

async function runControl(action, value, describe) {
  try {
    const res = await sendControl(action, value);
    if (res && res.sim) setSimStatus(res.sim);
    if (describe) statusEl.textContent = describe(res);
  } catch (err) {
    statusEl.textContent = `Could not do that: ${err.message}`;
  }
}

// ------------------------------------------------------------- playback ---

function buildPlaybackGroup() {
  const group = document.createElement("div");
  group.className = "sim-controls__group";
  const playBtn = actionButton("Play the simulation", () => runControl("play"));
  const pauseBtn = actionButton("Pause the simulation", () => runControl("pause"));
  group.appendChild(playBtn);
  group.appendChild(pauseBtn);
  return group;
}

// ---------------------------------------------------------------- speed ---

function buildSpeedGroup() {
  const group = document.createElement("div");
  group.className = "sim-controls__group sim-controls__speed";
  const label = document.createElement("span");
  label.className = "sim-controls__group-label";
  label.textContent = "Speed";
  group.appendChild(label);
  for (const speed of SPEEDS) {
    const btn = actionButton(`Run at ${trimSpeed(speed)}× speed`, () => runControl("speed", speed));
    group.appendChild(btn);
  }
  return group;
}

// ----------------------------------------------------------- bookmarks ---

function buildBookmarkGroup() {
  const group = document.createElement("div");
  group.className = "sim-controls__group sim-controls__bookmarks";
  const label = document.createElement("span");
  label.className = "sim-controls__group-label";
  label.textContent = "Jump to";
  group.appendChild(label);
  for (const bm of BOOKMARKS) {
    const btn = actionButton(bm.label_en, () => runControl("jump_to", bm.id));
    group.appendChild(btn);
  }
  return group;
}

// ---------------------------------------------------------------- feeds ---

function buildFeedGroup() {
  const group = document.createElement("div");
  group.className = "sim-controls__group sim-controls__feeds";
  const label = document.createElement("span");
  label.className = "sim-controls__group-label";
  label.textContent = "Feeds";
  group.appendChild(label);

  for (const feed of FEED_IDS) {
    const row = document.createElement("div");
    row.className = "sim-controls__feed-row";
    row.dataset.feed = feed;
    const name = document.createElement("span");
    name.textContent = FEED_LABELS[feed]?.en || feed;
    row.appendChild(name);

    const toggleBtn = actionButton("", () => {
      const isKilled = feedHealthById.get(feed)?.state === "killed";
      runControl(isKilled ? "resume_feed" : "kill_feed", feed);
    });
    toggleBtn.className = "text-button sim-controls__feed-toggle";
    row.appendChild(toggleBtn);

    const delayBtn = actionButton(`Delay ${FEED_LABELS[feed]?.en || feed} by 5 minutes`,
      () => runControl("delay_feed", { source: feed, seconds: DELAY_SECONDS }));
    delayBtn.className = "text-button";
    row.appendChild(delayBtn);

    group.appendChild(row);
  }

  const dupBtn = actionButton("Send a duplicate record", () => {
    if (recentEventIds.length === 0) {
      statusEl.textContent = "No reports seen yet to duplicate.";
      return;
    }
    const id = recentEventIds[Math.floor(Math.random() * recentEventIds.length)];
    runControl("inject_duplicate", id, () => `Sent a duplicate of one report again.`);
  });
  group.appendChild(dupBtn);

  return group;
}

function refreshFeedButtons() {
  if (!root) return;
  for (const row of root.querySelectorAll(".sim-controls__feed-row")) {
    const feed = row.dataset.feed;
    const health = feedHealthById.get(feed);
    const isKilled = health?.state === "killed";
    const btn = row.querySelector(".sim-controls__feed-toggle");
    const name = FEED_LABELS[feed]?.en || feed;
    btn.textContent = isKilled ? `Start ${name.toLowerCase()} feed` : `Stop ${name.toLowerCase()} feed`;
  }
}

// ------------------------------------------------------------- scenario ---

function buildScenarioGroup() {
  const group = document.createElement("div");
  group.className = "sim-controls__group sim-controls__scenario";
  const label = document.createElement("span");
  label.className = "sim-controls__group-label";
  label.textContent = "Scenario";
  group.appendChild(label);
  for (const sc of SCENARIOS) {
    const btn = actionButton(`Load ${sc.en.toLowerCase()} scenario`,
      () => runControl("set_scenario", sc.id, (res) => `Scenario set to ${sc.en.toLowerCase()}.`));
    group.appendChild(btn);
  }
  return group;
}

// ----------------------------------------------------------------- util ---

function actionButton(text, onClick) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "text-button sim-controls__button";
  btn.textContent = text;
  btn.addEventListener("click", onClick);
  return btn;
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
