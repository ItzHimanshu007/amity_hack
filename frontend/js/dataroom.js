/* js/dataroom.js — feed-health rows (#feed-health), the data room's raw vs
 * cleaned side-by-side view, and the scorecard sub-view (#data-room).
 * DESIGN.md §6, anti-tell "never clean up a raw record for display".
 * CONTRACT.md §D (masking), §E (scorecard).
 *
 * Owns: #feed-health, #data-room on index.html.
 */

import {
  fetchState, fetchRaw, fetchScorecard, sendControl, connectStream,
  FEED_IDS, FEED_LABELS, CATEGORY_LABELS, toISTClock,
} from "./api.js";

let feedHealthEl, dataRoomEl;
let feedHealthById = new Map();
const eventCache = new Map();          // event_id -> canonical event, this file's own copy
const duplicateLog = [];               // recent is_duplicate:true WS events
const MAX_DUPLICATE_LOG = 25;
let activeFeed = "civic_complaints";   // the PII-masking feed, shown by default

function init() {
  feedHealthEl = document.getElementById("feed-health");
  dataRoomEl = document.getElementById("data-room");
  if (!feedHealthEl && !dataRoomEl) return;

  if (dataRoomEl) {
    dataRoomEl.innerHTML = "";
    dataRoomEl.appendChild(buildTabs());
    const panes = document.createElement("div");
    panes.className = "data-room__panes";
    panes.id = "data-room-panes";
    dataRoomEl.appendChild(panes);

    const dupSection = document.createElement("div");
    dupSection.className = "data-room__duplicates";
    dupSection.id = "data-room-duplicates";
    dataRoomEl.appendChild(dupSection);

    const scoreSection = document.createElement("div");
    scoreSection.className = "data-room__scorecard";
    scoreSection.id = "data-room-scorecard";
    dataRoomEl.appendChild(scoreSection);
  }

  fetchState().then((state) => {
    for (const row of state.feed_health || []) feedHealthById.set(row.feed, row);
    for (const ev of state.events || []) eventCache.set(ev.event_id, ev);
    renderFeedHealth();
    loadFeedPane(activeFeed);
  }).catch((err) => {
    if (feedHealthEl) feedHealthEl.innerHTML = `<p class="feed-health__empty">Not connected to the backend yet (${err.message}).</p>`;
  });

  fetchScorecard().then(renderScorecard).catch(() => {
    const el = document.getElementById("data-room-scorecard");
    if (el) el.innerHTML = `<p class="scorecard__empty">Scorecard not available yet.</p>`;
  });

  connectStream(
    null, // ticks are the naadi strip / status block's concern
    (eventData) => {
      eventCache.set(eventData.event_id, eventData);
      if (eventData.is_duplicate) {
        duplicateLog.unshift({ event: eventData, at: eventData.received_at });
        if (duplicateLog.length > MAX_DUPLICATE_LOG) duplicateLog.pop();
        renderDuplicateLog();
      }
      // A live event for the feed currently on screen — refresh its cleaned column.
      if (eventData.source === activeFeed) refreshNormalizedColumn();
    },
    null, // situations are situations.js's concern
    (feedHealthData) => {
      feedHealthById.set(feedHealthData.feed, feedHealthData);
      renderFeedHealth();
    },
  );
}

// -------------------------------------------------------- feed health row -

function renderFeedHealth() {
  if (!feedHealthEl) return;
  feedHealthEl.innerHTML = "";
  for (const feed of FEED_IDS) {
    const row = feedHealthById.get(feed);
    feedHealthEl.appendChild(buildFeedHealthRow(feed, row));
  }
}

function buildFeedHealthRow(feed, row) {
  const el = document.createElement("div");
  el.className = "feed-health-row";
  el.dataset.feed = feed;

  const name = document.createElement("span");
  name.className = "feed-health-row__name";
  name.textContent = FEED_LABELS[feed]?.en || feed;
  el.appendChild(name);

  const state = row?.state || "stale";
  const stateEl = document.createElement("span");
  stateEl.className = `feed-health-row__state feed-health-row__state--${state}`;
  const mark = document.createElement("span");
  mark.className = "feed-health-row__mark";
  const word = document.createElement("span");
  word.textContent = stateWord(state, row);
  stateEl.appendChild(mark);
  stateEl.appendChild(word);
  el.appendChild(stateEl);

  const counts = document.createElement("span");
  counts.className = "feed-health-row__counts";
  counts.textContent = row
    ? `${row.records_total} record${row.records_total === 1 ? "" : "s"} · ${row.records_dropped} dropped`
    : "No data yet";
  el.appendChild(counts);

  const control = document.createElement("button");
  control.type = "button";
  control.className = "text-button feed-health-row__control";
  const name_lc = (FEED_LABELS[feed]?.en || feed).toLowerCase();
  control.textContent = state === "killed" ? `Start ${name_lc} feed` : `Stop ${name_lc} feed`;
  control.addEventListener("click", () => {
    sendControl(state === "killed" ? "resume_feed" : "kill_feed", feed).catch((err) => {
      console.error("[dataroom] feed control failed", err);
    });
  });
  el.appendChild(control);

  return el;
}

function stateWord(state, row) {
  // DESIGN.md §6 gives the default word per state; Phase 6's distinct reason
  // string (row.message) is preferred when present so "No update for 14 min"
  // and "Stopped by operator" read as the different situations they are.
  if (row && row.message) return row.message;
  return { live: "Live", stale: "No update yet", killed: "Stopped", error: "Not readable" }[state] || state;
}

// --------------------------------------------------------------- raw vs --
// -------------------------------------------------------- cleaned panes --

function buildTabs() {
  const tabs = document.createElement("div");
  tabs.className = "data-room__tabs";
  for (const feed of FEED_IDS) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "text-button data-room__tab";
    btn.dataset.feed = feed;
    btn.textContent = FEED_LABELS[feed]?.en || feed;
    if (feed === activeFeed) btn.classList.add("is-active");
    btn.addEventListener("click", () => {
      activeFeed = feed;
      for (const b of tabs.querySelectorAll(".data-room__tab")) b.classList.toggle("is-active", b === btn);
      loadFeedPane(feed);
    });
    tabs.appendChild(btn);
  }
  return tabs;
}

let lastRawResponse = null;

async function loadFeedPane(feed) {
  const panes = document.getElementById("data-room-panes");
  if (!panes) return;
  panes.innerHTML = `<p class="data-room__loading">Loading…</p>`;
  try {
    lastRawResponse = await fetchRaw(feed, 50);
  } catch (err) {
    panes.innerHTML = `<p class="data-room__loading">Could not load ${feed} (${err.message}).</p>`;
    return;
  }
  renderPanes();
}

function refreshNormalizedColumn() {
  if (lastRawResponse && lastRawResponse.feed === activeFeed) renderPanes();
}

function renderPanes() {
  const panes = document.getElementById("data-room-panes");
  if (!panes || !lastRawResponse) return;
  panes.innerHTML = "";

  const rawCol = document.createElement("div");
  rawCol.className = "data-room__col data-room__col--raw";
  const rawHeading = document.createElement("h3");
  rawHeading.textContent = "Original data";
  rawCol.appendChild(rawHeading);
  const rawHeadingHi = document.createElement("p");
  rawHeadingHi.className = "data-room__col-hi";
  rawHeadingHi.lang = "hi";
  rawHeadingHi.textContent = "मूल डेटा";
  rawCol.appendChild(rawHeadingHi);

  const cleanCol = document.createElement("div");
  cleanCol.className = "data-room__col data-room__col--clean";
  const cleanHeading = document.createElement("h3");
  cleanHeading.textContent = "Cleaned data";
  cleanCol.appendChild(cleanHeading);
  const cleanHeadingHi = document.createElement("p");
  cleanHeadingHi.className = "data-room__col-hi";
  cleanHeadingHi.lang = "hi";
  cleanHeadingHi.textContent = "साफ़ किया गया डेटा";
  cleanCol.appendChild(cleanHeadingHi);

  if (lastRawResponse.format === "csv" && lastRawResponse.header) {
    const header = document.createElement("p");
    header.className = "data-room__csv-header";
    header.textContent = lastRawResponse.header;
    rawCol.appendChild(header);
  }

  for (const record of lastRawResponse.records || []) {
    rawCol.appendChild(buildRawRow(record));
    cleanCol.appendChild(buildCleanRow(record));
  }

  panes.appendChild(rawCol);
  panes.appendChild(cleanCol);
}

function buildRawRow(record) {
  const row = document.createElement("div");
  row.className = "data-room__raw-row";
  const pre = document.createElement("code");
  pre.className = "data-room__raw-text";
  pre.textContent = lastRawResponse.format === "json" ? JSON.stringify(record.raw) : record.raw;
  row.appendChild(pre);

  const meta = document.createElement("p");
  meta.className = "data-room__raw-meta";
  const bits = [record.raw_ref];
  if (record.parsed_ok === false) bits.push(`not parsed — ${record.reason}`);
  if (typeof record.items_masked === "number" && record.items_masked > 0) {
    bits.push(`${record.items_masked} item${record.items_masked === 1 ? "" : "s"} masked`);
  }
  meta.textContent = bits.join(" · ");
  row.appendChild(meta);
  return row;
}

function buildCleanRow(record) {
  const row = document.createElement("div");
  row.className = "data-room__clean-row";
  const matches = findMatchingEvents(record.raw_ref);
  if (matches.length === 0) {
    const p = document.createElement("p");
    p.className = "data-room__clean-empty";
    p.textContent = record.parsed_ok === false
      ? "Dropped — never became an event."
      : "Not in the active window right now.";
    row.appendChild(p);
    return row;
  }
  for (const ev of matches) {
    const p = document.createElement("p");
    const cat = CATEGORY_LABELS[ev.category]?.en || ev.category;
    p.textContent = `${cat} · severity ${ev.severity.toFixed(2)} · confidence ${ev.confidence.toFixed(2)} · ${toISTClock(ev.start_utc)}`;
    if (ev.is_duplicate) {
      const flag = document.createElement("span");
      flag.className = "data-room__duplicate-flag";
      flag.textContent = "duplicate";
      p.appendChild(document.createTextNode(" "));
      p.appendChild(flag);
    }
    row.appendChild(p);
  }
  return row;
}

function findMatchingEvents(rawRef) {
  if (!rawRef) return [];
  const exact = [];
  const partial = [];
  for (const ev of eventCache.values()) {
    if (ev.raw_ref === rawRef) exact.push(ev);
    else if (ev.raw_ref && (ev.raw_ref.startsWith(rawRef) || rawRef.startsWith(ev.raw_ref))) partial.push(ev);
  }
  return exact.length ? exact : partial;
}

// ------------------------------------------------------------ duplicates --

function renderDuplicateLog() {
  const el = document.getElementById("data-room-duplicates");
  if (!el) return;
  el.innerHTML = "";
  if (duplicateLog.length === 0) return;
  const heading = document.createElement("p");
  heading.className = "data-room__duplicates-heading";
  heading.textContent = "Duplicate records sent";
  el.appendChild(heading);
  const ul = document.createElement("ul");
  for (const { event } of duplicateLog) {
    const li = document.createElement("li");
    const cat = CATEGORY_LABELS[event.category]?.en || event.category;
    const feed = FEED_LABELS[event.source]?.en || event.source;
    li.textContent = `${cat} from ${feed}, ${toISTClock(event.received_at)} — flagged duplicate, not counted again.`;
    ul.appendChild(li);
  }
  el.appendChild(ul);
}

// ------------------------------------------------------------- scorecard --

function renderScorecard(card) {
  const el = document.getElementById("data-room-scorecard");
  if (!el) return;
  el.innerHTML = "";

  const heading = document.createElement("h3");
  heading.textContent = "Scorecard";
  el.appendChild(heading);

  const primary = document.createElement("dl");
  primary.className = "scorecard__primary";
  addRow(primary, "Detected", `${card.matched} of ${card.truth_situations} real situations found`);
  addRow(primary, "False links", String(card.false_positives));
  addRow(primary, "Decoys ignored", `${card.decoys_correctly_ignored} of ${card.decoys_planted}`);
  // median_detection_lag_sec is left out: the backend's own detection_lag_caveat
  // marks it as a proxy (created_utc), not true detection time.
  el.appendChild(primary);

  const secondary = document.createElement("dl");
  secondary.className = "scorecard__secondary";
  addRow(secondary, "Precision", formatPct(card.precision));
  addRow(secondary, "Recall", formatPct(card.recall));
  addRow(secondary, "F1", formatPct(card.f1));
  addRow(secondary, "Alert level accuracy", formatPct(card.alert_level_accuracy));
  el.appendChild(secondary);
}

function addRow(dl, term, value) {
  const dt = document.createElement("dt");
  dt.textContent = term;
  const dd = document.createElement("dd");
  dd.textContent = value;
  dl.appendChild(dt);
  dl.appendChild(dd);
}

function formatPct(v) {
  if (v == null) return "Not available";
  return `${Math.round(v * 100)}%`;
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
