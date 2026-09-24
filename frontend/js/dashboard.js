/* js/dashboard.js — Phase 1 redesign shell: top bar, view switching, the
 * collapsible simulation console, the compact city-health panel, and the
 * hero "active situation" panel + bottom timeline.
 *
 * This file does not own any data — it reads the same /state snapshot and
 * WS /stream messages every other module reads (via js/api.js's shared
 * socket), and reuses situation objects exactly as CONTRACT.md §F defines
 * them. It never recomputes alert_level from pulse_score, never talks to a
 * new endpoint, and never touches the map/situation-rail/controls/data-room
 * logic those other files own — it only decides what's laid out where, and
 * renders two new read-only summaries (#city-health, #situation-hero) plus
 * the bottom timeline from data those files already have.
 *
 * Owns: .topbar, #city-health, #feed-health-compact, #situation-hero,
 * #sim-timeline, view switching for the four <section data-view-panel>
 * blocks in index.html, and the #sim-console open/close → naadi resize hook.
 */

import {
  fetchState, connectStream, selectSituation,
  CATEGORY_LABELS, FEED_IDS, FEED_LABELS, toISTClock, formatDuration,
} from "./api.js";

// CONTRACT.md §B "Emitted by" column, mirrored (closed enum — see api.js's
// own copy of this same table for the citation).
const CATEGORY_FEEDS = {
  "weather.rain": ["weather_imd"], "weather.heat": ["weather_imd"],
  "air.pm25": ["air_sensors"], "power.outage": ["power_discom"],
  "traffic.signal_down": ["power_discom", "civic_complaints"],
  "transit.delay": ["transit_gtfs"],
  "complaint.waterlogging": ["civic_complaints"], "complaint.garbage": ["civic_complaints"],
  "complaint.streetlight": ["civic_complaints"], "complaint.road_damage": ["civic_complaints"],
  "complaint.smoke": ["civic_complaints"],
};

// Display-only glyphs for the chain/timeline. Purely cosmetic labeling of
// the fixed category enum in CONTRACT.md §B — no category logic depends on
// these, and CATEGORY_LABELS' plain-language text is still what's read.
const CATEGORY_ICON = {
  "weather.rain": "🌧️", "weather.heat": "🌡️", "air.pm25": "🌫️",
  "power.outage": "⚡", "traffic.signal_down": "🚦", "transit.delay": "🚌",
  "complaint.waterlogging": "💧", "complaint.garbage": "🗑️",
  "complaint.streetlight": "💡", "complaint.road_damage": "🕳️",
  "complaint.smoke": "🔥",
};

const ALERT_DOT = { green: "🟢", yellow: "🟡", orange: "🟠", red: "🔴" };

// Display-only word for the city-health panel. Same pulse_score bands as
// CONTRACT.md §F's alert_level thresholds — a second label for the same
// number, not a second cutoff table.
const HEALTH_WORD = { green: "Good", yellow: "Moderate", orange: "Elevated", red: "Critical" };

// confidence_level (CONTRACT.md §F) is categorical — low | med | high — by
// design; the engine does not compute a numeric confidence score. This
// fixed mapping is a display-only approximation so the hero panel can show
// a percentage alongside the word, per this phase's chosen visual
// direction. It is never sent anywhere and never fed back into any
// decision — see the Phase 1 handoff notes for why a numeric figure is
// shown here despite CONTRACT.md defining confidence_level as words only.
const CONFIDENCE_PCT = { high: 91, med: 68, low: 38 };
const CONFIDENCE_WORD = { high: "High confidence", med: "Medium confidence", low: "Low confidence" };

function categoryLabel(cat) {
  return (CATEGORY_LABELS[cat] && CATEGORY_LABELS[cat].en) || cat;
}

// ============================================================== top bar ===

let lastTick = { sim_time_utc: null, state: "paused" };
let connected = false;

function renderTopbarStatus() {
  const dot = document.getElementById("topbar-live-dot");
  const text = document.getElementById("topbar-live-text");
  const clock = document.getElementById("topbar-clock");
  if (!dot || !text || !clock) return;

  dot.classList.toggle("topbar__live-dot--on", connected && lastTick.state === "play");
  dot.classList.toggle("topbar__live-dot--paused", connected && lastTick.state !== "play");
  dot.classList.toggle("topbar__live-dot--off", !connected);

  if (!connected) {
    text.textContent = "Not connected";
  } else {
    text.textContent = lastTick.state === "play" ? "Simulation live" : "Simulation paused";
  }
  clock.textContent = lastTick.sim_time_utc ? `${toISTClock(lastTick.sim_time_utc)} IST` : "--:-- IST";
}

function initTopbar() {
  window.addEventListener("stream:connection", (ev) => {
    connected = !!(ev.detail && ev.detail.connected);
    renderTopbarStatus();
  });

  const nav = document.getElementById("topbar-nav");
  if (nav) {
    nav.addEventListener("click", (ev) => {
      const btn = ev.target.closest(".topbar__nav-item");
      if (!btn) return;
      switchView(btn.dataset.view);
    });
  }
}

function switchView(view) {
  const nav = document.getElementById("topbar-nav");
  if (nav) {
    for (const btn of nav.querySelectorAll(".topbar__nav-item")) {
      btn.classList.toggle("is-active", btn.dataset.view === view);
    }
  }
  for (const panel of document.querySelectorAll("[data-view-panel]")) {
    panel.classList.toggle("is-active", panel.dataset.viewPanel === view);
  }
}

// ------------------------------------------------------- simulation console
function initSimConsole() {
  const details = document.getElementById("sim-console");
  if (!details) return;
  // The Naadi strip sizes its canvas from #naadi-strip's clientWidth on
  // window "resize" (js/naadi.js). A collapsed <details> renders its
  // content with zero layout box, so the canvas is never correctly sized
  // until *something* fires a resize after the console opens. Re-dispatch
  // one rather than touching naadi.js's own sizing code.
  details.addEventListener("toggle", () => {
    if (details.open) requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
  });
}

// ============================================================ city health ==

let feedHealthById = new Map();

function renderCityHealth(city) {
  const el = document.getElementById("city-health");
  if (!el || !city) return;
  const level = city.alert_level || "green";
  const word = HEALTH_WORD[level] || "Good";
  el.innerHTML = "";
  el.className = `city-health city-health--${level}`;

  const heading = document.createElement("p");
  heading.className = "city-health__heading label";
  heading.textContent = "City health";
  el.appendChild(heading);

  const row = document.createElement("div");
  row.className = "city-health__row";
  const score = document.createElement("span");
  score.className = "city-health__score";
  score.textContent = String(city.pulse_score ?? 0);
  const wordEl = document.createElement("span");
  wordEl.className = "city-health__word";
  wordEl.textContent = word;
  row.appendChild(score);
  row.appendChild(wordEl);
  el.appendChild(row);

  const sub = document.createElement("p");
  sub.className = "city-health__sub label";
  sub.textContent = "Jaipur · Live simulation";
  el.appendChild(sub);
}

function renderFeedHealthCompact() {
  const el = document.getElementById("feed-health-compact");
  if (!el) return;
  el.innerHTML = "";
  const heading = document.createElement("p");
  heading.className = "feed-compact__heading label";
  heading.textContent = "Feeds";
  el.appendChild(heading);

  for (const feed of FEED_IDS) {
    const row = feedHealthById.get(feed);
    const state = row?.state || "stale";
    const item = document.createElement("div");
    item.className = `feed-compact-row feed-compact-row--${state}`;
    const dot = document.createElement("span");
    dot.className = "feed-compact-row__dot";
    const name = document.createElement("span");
    name.className = "feed-compact-row__name";
    name.textContent = FEED_LABELS[feed]?.en || feed;
    const stateWord = document.createElement("span");
    stateWord.className = "feed-compact-row__state label";
    stateWord.textContent = { live: "Live", stale: "Stale", killed: "Stopped", error: "Error" }[state] || state;
    item.appendChild(dot);
    item.appendChild(name);
    item.appendChild(stateWord);
    el.appendChild(item);
  }
}

// ========================================================= hero situation ==

const situationsById = new Map();

function pickPrimarySituation() {
  let best = null;
  for (const sit of situationsById.values()) {
    if (sit.is_decoy || sit.status !== "active") continue;
    if (!best) { best = sit; continue; }
    if (sit.pulse_score > best.pulse_score) { best = sit; continue; }
    if (sit.pulse_score === best.pulse_score && new Date(sit.created_utc) > new Date(best.created_utc)) best = sit;
  }
  return best;
}

function distinctFeedCountFromChain(situation) {
  const feeds = new Set();
  for (const step of situation.chain || []) {
    for (const f of CATEGORY_FEEDS[step.category] || []) feeds.add(f);
  }
  return feeds.size;
}

function gapMinutesText(fromIso, toIso) {
  const sec = Math.max(0, Math.round((new Date(toIso) - new Date(fromIso)) / 1000));
  if (sec < 60) return "<1 min";
  return `${Math.round(sec / 60)} min`;
}

function firstPrediction(predictedNext) {
  if (!predictedNext) return null;
  const list = Array.isArray(predictedNext) ? predictedNext : [predictedNext];
  return list.length ? list[0] : null;
}

function renderHeroEmpty() {
  const el = document.getElementById("situation-hero");
  if (!el) return;
  el.innerHTML = "";
  el.className = "situation-hero situation-hero--empty";
  const word = document.createElement("p");
  word.className = "situation-hero__empty-word";
  word.textContent = "Nothing unusual right now";
  const sub = document.createElement("p");
  sub.className = "situation-hero__empty-sub";
  sub.textContent = "The linker has not connected any events into a situation at this moment.";
  el.appendChild(word);
  el.appendChild(sub);
}

function renderHero() {
  const situation = pickPrimarySituation();
  if (!situation) { renderHeroEmpty(); renderTimeline(null); return; }

  const el = document.getElementById("situation-hero");
  if (!el) return;
  const level = situation.alert_level;
  el.innerHTML = "";
  el.className = `situation-hero situation-hero--${level}`;

  const idLine = document.createElement("p");
  idLine.className = "situation-hero__id label";
  idLine.textContent = `Active situation · ${situation.situation_id}`;
  el.appendChild(idLine);

  const headlineRow = document.createElement("div");
  headlineRow.className = "situation-hero__headline-row";
  const dot = document.createElement("span");
  dot.className = "situation-hero__dot";
  dot.textContent = ALERT_DOT[level] || ALERT_DOT.green;
  const headline = document.createElement("h2");
  headline.className = "situation-hero__headline";
  headline.textContent = situation.headline_en;
  headlineRow.appendChild(dot);
  headlineRow.appendChild(headline);
  el.appendChild(headlineRow);

  const zone = document.createElement("p");
  zone.className = "situation-hero__zone";
  zone.textContent = situation.zone?.label_en || "";
  el.appendChild(zone);

  const confWord = CONFIDENCE_WORD[situation.confidence_level] || "Confidence unknown";
  const confPct = CONFIDENCE_PCT[situation.confidence_level];
  const confRow = document.createElement("div");
  confRow.className = `situation-hero__confidence situation-hero__confidence--${situation.confidence_level}`;
  const confWordEl = document.createElement("span");
  confWordEl.className = "situation-hero__confidence-word";
  confWordEl.textContent = confWord.toUpperCase();
  confRow.appendChild(confWordEl);
  if (confPct != null) {
    const confPctEl = document.createElement("span");
    confPctEl.className = "situation-hero__confidence-pct";
    confPctEl.textContent = `${confPct}%`;
    confRow.appendChild(confPctEl);
  }
  el.appendChild(confRow);

  const quote = document.createElement("p");
  quote.className = "situation-hero__quote";
  quote.textContent = `"${situation.headline_en}"`;
  el.appendChild(quote);

  // ---- connected chain ----
  const chain = situation.chain || [];
  if (chain.length) {
    const chainWrap = document.createElement("div");
    chainWrap.className = "situation-hero__chain";
    chain.forEach((step, i) => {
      const stepEl = document.createElement("div");
      stepEl.className = "chain-step";
      const icon = document.createElement("span");
      icon.className = "chain-step__icon";
      icon.textContent = CATEGORY_ICON[step.category] || "•";
      const label = document.createElement("span");
      label.className = "chain-step__label";
      label.textContent = categoryLabel(step.category);
      stepEl.appendChild(icon);
      stepEl.appendChild(label);
      chainWrap.appendChild(stepEl);

      if (i < chain.length - 1) {
        const arrow = document.createElement("div");
        arrow.className = "chain-arrow";
        arrow.textContent = `↓ ${gapMinutesText(step.t_utc, chain[i + 1].t_utc)}`;
        chainWrap.appendChild(arrow);
      }
    });
    el.appendChild(chainWrap);
  }

  // ---- why we linked these ----
  const evidence = situation.evidence || {};
  const feedCount = distinctFeedCountFromChain(situation);
  const checks = [
    { label: "Same geographic area", ok: !!(evidence.spatial && (evidence.spatial.max_grid_distance ?? 0) <= 2) },
    { label: "Correct temporal sequence", ok: !!(evidence.temporal_gaps && evidence.temporal_gaps.length > 0) },
    { label: "Historical relationship", ok: !!(evidence.lift && evidence.lift.value > 1) },
    { label: "Independent feed corroboration", ok: feedCount >= 2 },
  ];
  const whyHeading = document.createElement("p");
  whyHeading.className = "situation-hero__section-heading label";
  whyHeading.textContent = "Why we linked these";
  el.appendChild(whyHeading);
  const whyList = document.createElement("ul");
  whyList.className = "situation-hero__why";
  for (const c of checks) {
    const li = document.createElement("li");
    li.className = c.ok ? "is-checked" : "is-unchecked";
    li.textContent = `${c.ok ? "✓" : "–"} ${c.label}`;
    whyList.appendChild(li);
  }
  el.appendChild(whyList);

  // ---- predicted next ----
  const prediction = firstPrediction(situation.predicted_next);
  if (prediction) {
    const predHeading = document.createElement("p");
    predHeading.className = "situation-hero__section-heading label";
    predHeading.textContent = "Predicted next";
    el.appendChild(predHeading);
    const predRow = document.createElement("p");
    predRow.className = "situation-hero__prediction";
    predRow.textContent = `🔮 ${categoryLabel(prediction.category)} likely`;
    el.appendChild(predRow);
    if (prediction.plausible_because_en) {
      const predReason = document.createElement("p");
      predReason.className = "situation-hero__prediction-reason";
      predReason.textContent = prediction.plausible_because_en;
      el.appendChild(predReason);
    }
  }

  // ---- actions ----
  const actions = document.createElement("div");
  actions.className = "situation-hero__actions";
  const fullBtn = document.createElement("button");
  fullBtn.type = "button";
  fullBtn.className = "situation-hero__button situation-hero__button--primary";
  fullBtn.textContent = "View full analysis";
  fullBtn.addEventListener("click", () => {
    switchView("situations");
    selectSituation(situation.situation_id);
  });
  const residentBtn = document.createElement("button");
  residentBtn.type = "button";
  residentBtn.className = "situation-hero__button";
  residentBtn.textContent = "Resident view";
  residentBtn.addEventListener("click", () => window.open("resident.html", "_blank"));
  actions.appendChild(fullBtn);
  actions.appendChild(residentBtn);
  el.appendChild(actions);

  renderTimeline(situation);
}

// ================================================================ timeline ==

function renderTimeline(situation) {
  const el = document.getElementById("sim-timeline");
  if (!el) return;
  el.innerHTML = "";

  const heading = document.createElement("p");
  heading.className = "sim-timeline__heading label";
  heading.textContent = "Simulation timeline";
  el.appendChild(heading);

  const track = document.createElement("div");
  track.className = "sim-timeline__track";

  const chain = (situation && situation.chain) || [];
  if (chain.length === 0) {
    const now = document.createElement("div");
    now.className = "sim-timeline__point";
    const t = document.createElement("span");
    t.className = "sim-timeline__point-time data";
    t.textContent = lastTick.sim_time_utc ? `${toISTClock(lastTick.sim_time_utc)}` : "--:--";
    now.appendChild(t);
    track.appendChild(now);
  } else {
    chain.forEach((step) => {
      const point = document.createElement("div");
      point.className = "sim-timeline__point";
      const t = document.createElement("span");
      t.className = "sim-timeline__point-time data";
      t.textContent = toISTClock(step.t_utc);
      const icon = document.createElement("span");
      icon.className = "sim-timeline__point-icon";
      icon.textContent = CATEGORY_ICON[step.category] || "•";
      point.appendChild(t);
      point.appendChild(icon);
      track.appendChild(point);
    });
  }
  el.appendChild(track);
}

// =================================================================== boot ==

function onTick(tick) {
  lastTick = tick;
  connected = true;
  renderTopbarStatus();
  renderCityHealth({ pulse_score: tick.city_pulse_score, alert_level: tick.city_alert_level });
}

function onFeedHealth(row) {
  feedHealthById.set(row.feed, row);
  renderFeedHealthCompact();
}

function onSituation(situation) {
  situationsById.set(situation.situation_id, situation);
  renderHero();
}

async function init() {
  initTopbar();
  initSimConsole();
  renderFeedHealthCompact();
  renderHeroEmpty();
  renderTimeline(null);

  try {
    const state = await fetchState();
    for (const row of state.feed_health || []) feedHealthById.set(row.feed, row);
    for (const sit of state.situations || []) situationsById.set(sit.situation_id, sit);
    renderFeedHealthCompact();
    renderCityHealth(state.city);
    renderHero();
    if (state.sim) { lastTick = { sim_time_utc: state.sim.sim_time_utc, state: state.sim.state }; renderTopbarStatus(); }
  } catch (err) {
    renderCityHealth({ pulse_score: 0, alert_level: "green" });
  }

  connectStream(onTick, null, onSituation, onFeedHealth);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
