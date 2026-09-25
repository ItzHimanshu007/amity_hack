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
 * #situation-others, view switching for the four <section data-view-panel>
 * blocks in index.html, and the #sim-console open/close → naadi resize hook.
 */

import {
  fetchState, fetchScorecard, connectStream, selectSituation,
  CATEGORY_LABELS, FEED_IDS, FEED_LABELS, ACTION_BY_CATEGORY, ALERT_WORDS, toISTClock, formatDuration,
  fetchTerrain, isFloodSituation, LANDMARK_BY_CELL, fetchFlood, floodFrameIndex,
} from "./api.js";

// CONTRACT.md §B "Emitted by" column, mirrored (closed enum — see api.js's
// own copy of this same table for the citation).
const CATEGORY_FEEDS = {
  "weather.rain": ["weather_imd"], "weather.heat": ["weather_imd"],
  "air.pm25": ["air_sensors"], "power.outage": ["power_discom"],
  "traffic.signal_down": ["power_discom", "civic_complaints"],
  "drain.overflow": ["drain_scada"],
  "complaint.waterlogging": ["civic_complaints"], "complaint.garbage": ["civic_complaints"],
  "complaint.streetlight": ["civic_complaints"], "complaint.road_damage": ["civic_complaints"],
  "complaint.smoke": ["civic_complaints"],
};

// Display-only glyphs for the chain/timeline. Purely cosmetic labeling of
// the fixed category enum in CONTRACT.md §B — no category logic depends on
// these, and CATEGORY_LABELS' plain-language text is still what's read.
const CATEGORY_ICON = {
  "weather.rain": "🌧️", "weather.heat": "🌡️", "air.pm25": "🌫️",
  "power.outage": "⚡", "traffic.signal_down": "🚦", "drain.overflow": "🌊",
  "complaint.waterlogging": "💧", "complaint.garbage": "🗑️",
  "complaint.streetlight": "💡", "complaint.road_damage": "🕳️",
  "complaint.smoke": "🔥",
};

const ALERT_DOT = { green: "🟢", yellow: "🟡", orange: "🟠", red: "🔴" };

// Display-only word for the city-health panel. Same pulse_score bands as
// CONTRACT.md §F's alert_level thresholds — a second label for the same
// number, not a second cutoff table.
const HEALTH_WORD = { green: "Good", yellow: "Moderate", orange: "Elevated", red: "Critical" };

// confidence_level (CONTRACT.md §F) is categorical by design; show only the word.
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
  renderLiveClock();
}

// The top-bar clock is Jaipur's real time, not replay time (the replay clock lives
// in the Simulation console).
const IST_FORMAT = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata", hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true,
});
const IST_DATE = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short" });
function renderLiveClock() {
  const clock = document.getElementById("topbar-clock");
  if (!clock) return;
  const now = new Date();
  clock.textContent = `Jaipur ${IST_FORMAT.format(now)} IST · ${IST_DATE.format(now)}`;
}

function initTopbar() {
  renderLiveClock();
  setInterval(renderLiveClock, 1000);
  // js/controls.js shares confirmed sim status straight from /control responses,
  // so the top bar doesn't wait for the next tick after a click.
  window.addEventListener("sim:status", (ev) => {
    const s = ev.detail || {};
    lastTick = { ...lastTick, ...(s.state ? { state: s.state } : {}), ...(s.sim_time_utc ? { sim_time_utc: s.sim_time_utc } : {}) };
    renderTopbarStatus();
  });
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
  const resizeOnOpen = (el) => el.addEventListener("toggle", () => {
    if (el.open) requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
  });
  resizeOnOpen(details);
  const trace = document.getElementById("sim-console-trace");
  if (trace) resizeOnOpen(trace);
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

// ============================================================= proof strip ==
// GET /scorecard grades the linker's output for the whole replay against the
// hidden ground-truth file. The lag figure is left out on purpose: the
// backend marks it as a proxy (detection_lag_caveat), not true detection time.

function renderProofStrip(card) {
  const el = document.getElementById("proof-strip");
  if (!el || !card) return;
  el.innerHTML = "";
  el.className = "proof-strip";
  const heading = document.createElement("p");
  heading.className = "proof-strip__heading label";
  heading.textContent = "How we know it works";
  el.appendChild(heading);

  const rows = [
    [`${card.matched}/${card.truth_situations}`, "planted situations found"],
    [String(card.false_positives), card.false_positives === 1 ? "false link" : "false links"],
    [`${card.decoys_correctly_ignored}/${card.decoys_planted}`, "decoys correctly ignored"],
  ];
  for (const [num, text] of rows) {
    const row = document.createElement("div");
    row.className = "proof-strip__row";
    const n = document.createElement("span");
    n.className = "proof-strip__num data";
    n.textContent = num;
    const t = document.createElement("span");
    t.className = "proof-strip__text";
    t.textContent = text;
    row.append(n, t);
    el.appendChild(row);
  }
  const note = document.createElement("p");
  note.className = "proof-strip__note";
  note.textContent = "Scored over the full 3-hour replay against a hidden answer key the detector never reads.";
  el.appendChild(note);
  const link = document.createElement("button");
  link.type = "button";
  link.className = "text-button";
  link.textContent = "See the full scorecard";
  link.addEventListener("click", () => switchView("dataroom"));
  el.appendChild(link);
}

// ======================================================== terrain context ==
// Describes what real terrain (tools/terrain/build_terrain.py) says about a
// flood-type situation's own areas. Context only: never a ✓, never confidence.

let terrain = null;
const DRAIN_PCT = 60;   // carries more runoff than 60% of the city's areas
const LOW_M = -2;       // sits at least 2 m below its surroundings
const RAISED_M = 2;     // sits at least 2 m above its surroundings

let flood = null;
let floodFrame = -1;

// The rain-on-terrain model's state for this situation's areas at the replay's
// current time (never a later peak). Context only, like the terrain line.
function floodModelText(situation) {
  if (!flood || !isFloodSituation(situation) || floodFrame < 0) return null;
  const frame = flood.frames[floodFrame];
  const when = toISTClock(frame.t_utc);
  const parts = [];
  let deepest = 0;
  let unnamed = 0;
  for (const c of situation.zone?.h3_cells || []) {
    const m = flood.cells[c];
    const pct = m ? m.over10_pct_by_frame[floodFrame] : 0;
    deepest = Math.max(deepest, m ? m.max_depth_cm_by_frame[floodFrame] : 0);
    if (pct >= 0.5) parts.push(`${Math.round(pct)}% of ${LANDMARK_BY_CELL[c] || (unnamed++ ? "another area" : "one area")}`);
  }
  if (!parts.length) return `Rain model at ${when}: no water over 10 cm in these areas.`;
  return `Rain model at ${when}: water over 10 cm on ${parts.join(" and ")} (deepest ${Math.round(deepest)} cm).`;
}

function terrainContext(situation) {
  if (!terrain || !isFloodSituation(situation)) return null;
  const cells = (situation.zone?.h3_cells || []).filter((c) => terrain.cells[c]);
  if (!cells.length) return null;
  let unnamed = 0;
  const parts = cells.map((c) => {
    const t = terrain.cells[c];
    const name = LANDMARK_BY_CELL[c] || (unnamed++ ? "another area" : "one area");
    const drains = t.flow_pct >= DRAIN_PCT;
    const low = t.rel_to_surroundings_m <= LOW_M;
    const raised = t.rel_to_surroundings_m >= RAISED_M;
    let text;
    if (low && drains) text = `${name} sits ${Math.abs(t.rel_to_surroundings_m).toFixed(0)} m below its surroundings on a drainage path`;
    else if (low) text = `${name} sits ${Math.abs(t.rel_to_surroundings_m).toFixed(0)} m below its surroundings`;
    else if (drains) text = `${name} is on a drainage path (more runoff than ${t.flow_pct}% of the city)`;
    else if (raised) text = `${name} sits ${t.rel_to_surroundings_m.toFixed(0)} m above its surroundings`;
    else text = `${name} is level with its surroundings`;
    return { text, supports: low || drains };
  });
  const supporting = parts.filter((p) => p.supports).length;
  const verdict = supporting === parts.length
    ? "Terrain supports this"
    : supporting === 0
      ? "Terrain doesn't explain this: blocked drains or local low spots are more likely"
      : "Terrain only partly explains this: local drainage likely matters";
  return `${verdict}. ${parts.map((p) => p.text).join("; ")}.`;
}

// ========================================================= hero situation ==

const situationsById = new Map();
let rejectedCandidates = [];
let lastRejectedFetch = { at: 0, sim: null };

// GET /state's rejected_candidates grows as the replay reveals evidence. It has
// no WS message, so refresh it when the sim clock has moved (at most every 4 s)
// and share it with js/situations.js via a window event.
function setRejected(list) {
  rejectedCandidates = list || [];
  window.dispatchEvent(new CustomEvent("rejected:update", { detail: rejectedCandidates }));
  renderHero();
}
function maybeRefreshRejected(simTimeUtc) {
  if (simTimeUtc === lastRejectedFetch.sim || Date.now() - lastRejectedFetch.at < 4000) return;
  lastRejectedFetch = { at: Date.now(), sim: simTimeUtc };
  fetchState().then((st) => setRejected(st.rejected_candidates)).catch(() => { /* keep last list */ });
}

const LEVEL_RANK = { green: 0, yellow: 1, orange: 2, red: 3 };

// The hero shows the most serious situation. Once one is on screen it stays
// there unless another reaches a higher alert level (or it ends), so a few
// points of pulse_score -- e.g. a stopped-feed penalty -- don't swap the story
// out from under the viewer.
function pickPrimarySituation() {
  const active = [...situationsById.values()].filter((s) => !s.is_decoy && s.status === "active");
  if (!active.length) return null;
  let best = null;
  for (const sit of active) {
    if (!best) { best = sit; continue; }
    const byLevel = (LEVEL_RANK[sit.alert_level] ?? 0) - (LEVEL_RANK[best.alert_level] ?? 0);
    if (byLevel > 0 || (byLevel === 0 && sit.pulse_score > best.pulse_score)
        || (byLevel === 0 && sit.pulse_score === best.pulse_score && new Date(sit.created_utc) > new Date(best.created_utc))) best = sit;
  }
  const pinned = active.find((s) => s.situation_id === pinnedHeroId);
  if (pinned) return pinned;
  const current = active.find((s) => s.situation_id === lastHeroId);
  if (current && (LEVEL_RANK[best.alert_level] ?? 0) <= (LEVEL_RANK[current.alert_level] ?? 0)) return current;
  return best;
}

// Feeds named by the chain's categories. Categories emitted by more than one
// feed (traffic.signal_down) are skipped, so this can undercount but never
// overstate corroboration.
function distinctFeedsFromChain(situation) {
  const feeds = new Set();
  for (const step of situation.chain || []) {
    const opts = CATEGORY_FEEDS[step.category] || [];
    if (opts.length === 1) feeds.add(opts[0]);
  }
  return [...feeds];
}

function gapMinutesText(fromIso, toIso) {
  const sec = Math.max(0, Math.round((new Date(toIso) - new Date(fromIso)) / 1000));
  if (sec < 60) return "<1 min";
  return `${Math.round(sec / 60)} min`;
}

function predictionList(predictedNext) {
  if (!predictedNext) return [];
  return Array.isArray(predictedNext) ? predictedNext : [predictedNext];
}

// ================================================= other active situations ==
// Every live situation besides the hero, most serious first. Picking one makes
// it the hero (until another is picked) and flies the map to it.
let pinnedHeroId = null;

function renderOthers(hero) {
  const el = document.getElementById("situation-others");
  if (!el) return;
  const others = [...situationsById.values()]
    .filter((s) => !s.is_decoy && s.status === "active")
    .sort((a, b) => (LEVEL_RANK[b.alert_level] ?? 0) - (LEVEL_RANK[a.alert_level] ?? 0)
      || b.pulse_score - a.pulse_score);
  el.innerHTML = "";
  el.hidden = others.length === 0;
  if (!others.length) return;
  const head = document.createElement("p");
  head.className = "situation-others__head label";
  head.textContent = `Live situations · ${others.length} ${others.length === 1 ? "area" : "areas"}`;
  el.appendChild(head);
  const list = document.createElement("ul");
  list.className = "situation-others__list";
  for (const sit of others) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `situation-others__item situation-others__item--${sit.alert_level}`
      + (hero && sit.situation_id === hero.situation_id ? " is-current" : "");
    const dot = document.createElement("span");
    dot.className = "situation-others__dot";
    const text = document.createElement("span");
    text.className = "situation-others__text";
    const h = document.createElement("span");
    h.className = "situation-others__headline";
    h.textContent = sit.headline_en;
    const meta = document.createElement("span");
    meta.className = "situation-others__meta";
    const steps = (sit.chain || []).length;
    meta.textContent = `${(ALERT_WORDS[sit.alert_level] || {}).en || sit.alert_level} · ${steps} linked ${steps === 1 ? "signal" : "signals"}`;
    text.appendChild(h);
    text.appendChild(meta);
    btn.appendChild(dot);
    btn.appendChild(text);
    btn.addEventListener("click", () => {
      pinnedHeroId = sit.situation_id;
      renderHero();
      window.dispatchEvent(new CustomEvent("situation:focus", { detail: { situation: sit } }));
    });
    li.appendChild(btn);
    list.appendChild(li);
  }
  el.appendChild(list);
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

// Tells js/city.js which situation the hero shows; `live` is true only when a
// WS situation message (not the initial snapshot) changed it.
let lastHeroId = null;
let heroChangeIsLive = false;
function announceHero(situation) {
  const id = situation ? situation.situation_id : null;
  if (id === lastHeroId) return;
  lastHeroId = id;
  window.dispatchEvent(new CustomEvent("hero:changed", { detail: { situation, live: heroChangeIsLive } }));
}
window.addEventListener("hero:request", () => {
  window.dispatchEvent(new CustomEvent("hero:changed", { detail: { situation: pickPrimarySituation(), live: false } }));
});

function renderHero() {
  const situation = pickPrimarySituation();
  announceHero(situation);
  renderOthers(situation);
  if (!situation) { renderHeroEmpty(); return; }

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
  const confRow = document.createElement("div");
  confRow.className = `situation-hero__confidence situation-hero__confidence--${situation.confidence_level}`;
  const confWordEl = document.createElement("span");
  confWordEl.className = "situation-hero__confidence-word";
  confWordEl.textContent = confWord.toUpperCase();
  confRow.appendChild(confWordEl);
  el.appendChild(confRow);

  if (situation.confidence_reason_en) {
    let reason = situation.confidence_reason_en;
    for (const [id, lbl] of Object.entries(FEED_LABELS)) reason = reason.split(id).join(lbl.en.toLowerCase());
    const reasonEl = document.createElement("p");
    reasonEl.className = "situation-hero__confidence-reason";
    reasonEl.textContent = reason.charAt(0).toUpperCase() + reason.slice(1);
    el.appendChild(reasonEl);
  }

  // The linker's own "partial" signal: it has matched only some of the reports
  // it expects to belong here. Watching this count rise is the replay's growth.
  if (situation.partial && situation.partial_of != null) {
    const known = situation.members_known ?? (situation.member_event_ids || []).length;
    const growing = document.createElement("span");
    growing.className = "situation-hero__growing";
    growing.textContent = `Still growing · ${known} of ~${situation.partial_of} reports so far`;
    el.appendChild(growing);
  }


  // ---- why it matters: the resident view's suggested action per chain category ----
  // One line per theme, so rain and waterlogging don't both say "flooded".
  const THEME = { "weather.rain": "flood", "complaint.waterlogging": "flood", "power.outage": "signals", "traffic.signal_down": "signals" };
  const matters = [];
  const seen = new Set();
  for (const step of situation.chain || []) {
    const a = ACTION_BY_CATEGORY[step.category];
    const theme = THEME[step.category] || step.category;
    if (!a || a.en.startsWith("Being tracked") || seen.has(theme)) continue;
    seen.add(theme);
    matters.push(a.en);
  }
  if (matters.length) {
    const mattersHeading = document.createElement("p");
    mattersHeading.className = "situation-hero__section-heading label";
    mattersHeading.textContent = "Why it matters";
    el.appendChild(mattersHeading);
    const mattersList = document.createElement("ul");
    mattersList.className = "situation-hero__matters";
    for (const text of matters.slice(0, 3)) {
      const li = document.createElement("li");
      li.textContent = text;
      mattersList.appendChild(li);
    }
    el.appendChild(mattersList);
  }

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
      if (i > 0) {
        const gap = document.createElement("span");
        gap.className = "chain-arrow";
        gap.textContent = `↓ ${gapMinutesText(chain[i - 1].t_utc, step.t_utc)} later`;
        stepEl.appendChild(gap);
      }
      chainWrap.appendChild(stepEl);
    });
    el.appendChild(chainWrap);
  }

  // ---- why we linked these ----
  const evidence = situation.evidence || {};
  const feeds = distinctFeedsFromChain(situation);
  const gaps = evidence.temporal_gaps || [];
  const checks = [
    {
      label: "Same geographic area",
      ok: !!(evidence.spatial && (evidence.spatial.max_grid_distance ?? 99) <= 2),
      detail: evidence.spatial?.note_en,
    },
    {
      label: "Correct temporal sequence",
      ok: gaps.length > 0 && gaps.every((g) => g.gap_sec >= 0),
      detail: gaps.length
        ? `${gaps.length + 1} steps in order, gaps of ${gaps.map((g) => (g.gap_sec < 60 ? "<1" : Math.round(g.gap_sec / 60))).join(", ")} min`
        : null,
    },
    {
      label: "Historical relationship",
      ok: !!(evidence.lift && evidence.lift.value > 1),
      detail: evidence.lift?.note_en,
    },
    {
      label: "Independent feed corroboration",
      ok: feeds.length >= 2,
      detail: feeds.length ? `${feeds.length} feeds: ${feeds.map((f) => FEED_LABELS[f]?.en || f).join(", ")}` : null,
    },
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
    const title = document.createElement("span");
    title.className = "situation-hero__why-label";
    title.textContent = `${c.ok ? "✓" : "–"} ${c.label}`;
    li.appendChild(title);
    const detail = document.createElement("span");
    detail.className = "situation-hero__why-detail";
    detail.textContent = c.detail || "Not available for this situation";
    li.appendChild(detail);
    whyList.appendChild(li);
  }
  el.appendChild(whyList);

  // ---- terrain context (real elevation; not used to link) ----
  const terrainText = terrainContext(situation);
  if (terrainText) {
    const tHead = document.createElement("p");
    tHead.className = "situation-hero__section-heading label";
    tHead.textContent = "Terrain context";
    const tNote = document.createElement("span");
    tNote.className = "situation-hero__terrain-note";
    tNote.textContent = " · real elevation, not used to link";
    tHead.appendChild(tNote);
    el.appendChild(tHead);
    const tLine = document.createElement("p");
    tLine.className = "situation-hero__terrain";
    tLine.textContent = terrainText;
    const modelText = floodModelText(situation);
    if (modelText) {
      const m = document.createElement("span");
      m.className = "situation-hero__flood-model";
      m.textContent = modelText;
      tLine.appendChild(m);
    }
    el.appendChild(tLine);
  }

  // ---- what the linker refused to link here ----
  const cells = new Set(situation.zone?.h3_cells || []);
  const nearby = rejectedCandidates.filter((r) => (r.h3_cells || []).some((c) => cells.has(c)));
  if (rejectedCandidates.length) {
    const rej = document.createElement("button");
    rej.type = "button";
    rej.className = "situation-hero__rejected";
    rej.textContent = nearby.length
      ? `${nearby.length} other pattern${nearby.length === 1 ? "" : "s"} in these areas rejected as coincidence · see why`
      : `${rejectedCandidates.length} pattern${rejectedCandidates.length === 1 ? "" : "s"} across the city rejected as coincidence · see why`;
    rej.addEventListener("click", () => {
      switchView("situations");
      document.getElementById("unrelated-section")?.scrollIntoView({ block: "start" });
    });
    el.appendChild(rej);
  }

  // ---- predicted next ----
  const predictions = predictionList(situation.predicted_next);
  if (predictions.length) {
    const predHeading = document.createElement("p");
    predHeading.className = "situation-hero__section-heading label";
    predHeading.textContent = "Predicted next";
    el.appendChild(predHeading);
    for (const prediction of predictions) {
      const predRow = document.createElement("p");
      predRow.className = "situation-hero__prediction";
      predRow.textContent = `🔮 ${categoryLabel(prediction.category)}`;
      el.appendChild(predRow);
      const reason = prediction.plausible_because_en || (prediction.based_on ? `Based on ${prediction.based_on}` : null);
      if (reason) {
        const predReason = document.createElement("p");
        predReason.className = "situation-hero__prediction-reason";
        predReason.textContent = reason;
        el.appendChild(predReason);
      }
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
  // Actions sit under the confidence line, not at the bottom: the explanation
  // below can scroll, the two buttons are always on screen.
  const firstSection = el.querySelector(".situation-hero__section-heading");
  el.insertBefore(actions, firstSection ? firstSection : null);

}


// =================================================================== boot ==

function onTick(tick) {
  lastTick = tick;
  const f = floodFrameIndex(flood, tick.sim_time_utc);
  if (f !== floodFrame) { floodFrame = f; renderHero(); }
  maybeRefreshRejected(tick.sim_time_utc);
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
  heroChangeIsLive = true;
  renderHero();
  heroChangeIsLive = false;
}

async function init() {
  initTopbar();
  initSimConsole();
  renderFeedHealthCompact();
  renderHeroEmpty();

  try {
    const state = await fetchState();
    for (const row of state.feed_health || []) feedHealthById.set(row.feed, row);
    for (const sit of state.situations || []) situationsById.set(sit.situation_id, sit);
    rejectedCandidates = state.rejected_candidates || [];
    lastRejectedFetch = { at: Date.now(), sim: state.sim && state.sim.sim_time_utc };
    renderFeedHealthCompact();
    renderCityHealth(state.city);
    renderHero();
    if (state.sim) { lastTick = { sim_time_utc: state.sim.sim_time_utc, state: state.sim.state }; renderTopbarStatus(); }
  } catch (err) {
    renderCityHealth({ pulse_score: 0, alert_level: "green" });
  }

  fetchTerrain().then((t) => { terrain = t; renderHero(); });
  fetchFlood().then((d) => { flood = d; floodFrame = floodFrameIndex(d, lastTick.sim_time_utc); renderHero(); });
  fetchScorecard().then(renderProofStrip).catch(() => { /* strip stays hidden */ });
  connectStream(onTick, null, onSituation, onFeedHealth);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
