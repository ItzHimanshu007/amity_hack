/* js/dataroom.js — feed-health rows and the data room.
 * DESIGN.md §6, anti-tell "never clean up a raw record for display".
 * CONTRACT.md §D (masking), §E (scorecard).
 *
 * Runs on both pages and renders whichever containers exist:
 *   index.html    → #feed-health only
 *   dataroom.html → #data-room (feed tabs, the aligned raw→canonical table,
 *                   the duplicate log and the scorecard)
 */

import {
  fetchState, fetchRaw, fetchScorecard, sendControl, connectStream,
  FEED_IDS, FEED_LABELS, CATEGORY_LABELS, toISTClock, formatDuration,
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

    const tableWrap = document.createElement("div");
    tableWrap.className = "data-room__table-wrap";
    tableWrap.id = "data-room-panes";
    dataRoomEl.appendChild(tableWrap);

    const dupSection = document.createElement("div");
    dupSection.className = "data-room__duplicates panel";
    dupSection.id = "data-room-duplicates";
    dataRoomEl.appendChild(dupSection);

    const scoreSection = document.createElement("div");
    scoreSection.className = "data-room__scorecard panel";
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

// The point of this view is the transformation, not two blobs of text: one row
// per raw record, the canonical event it became on the same row, and the exact
// changes between them in the middle.
function renderPanes() {
  const wrap = document.getElementById("data-room-panes");
  if (!wrap || !lastRawResponse) return;
  wrap.innerHTML = "";

  const records = lastRawResponse.records || [];
  if (!records.length) {
    wrap.innerHTML = `<p class="data-room__loading">No records for this feed yet.</p>`;
    return;
  }

  if (lastRawResponse.header) {
    const header = document.createElement("p");
    header.className = "data-room__csv-header";
    header.textContent = `${lastRawResponse.format.toUpperCase()} header: ${lastRawResponse.header}`;
    wrap.appendChild(header);
  }

  const table = document.createElement("table");
  table.className = "xform";

  const thead = document.createElement("thead");
  const hrow = document.createElement("tr");
  for (const [label, cls] of [["As the feed sent it", "xform__raw"],
                              ["What changed", "xform__delta"],
                              ["Canonical event", "xform__clean"]]) {
    const th = document.createElement("th");
    th.className = cls;
    th.textContent = label;
    hrow.appendChild(th);
  }
  thead.appendChild(hrow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  for (const record of records) tbody.appendChild(buildXformRow(record));
  table.appendChild(tbody);
  wrap.appendChild(table);
}

function buildXformRow(record) {
  const tr = document.createElement("tr");
  const matches = findMatchingEvents(record.raw_ref);
  const ev = matches[0] || null;

  // --- as the feed sent it ---
  const rawTd = document.createElement("td");
  rawTd.className = "xform__raw";
  const code = document.createElement("code");
  code.className = "xform__rawtext";
  code.textContent = lastRawResponse.format === "json" ? JSON.stringify(record.raw) : record.raw;
  rawTd.appendChild(code);
  const ref = document.createElement("span");
  ref.className = "xform__ref";
  ref.textContent = record.raw_ref;
  rawTd.appendChild(ref);
  tr.appendChild(rawTd);

  // --- what changed ---
  const deltaTd = document.createElement("td");
  deltaTd.className = "xform__delta";
  // Masking happens on ingest, so it is true of the raw record whether or not
  // the clock has reached the event yet — state it either way.
  if (typeof record.items_masked === "number" && record.items_masked > 0) {
    deltaTd.appendChild(changeLine("Personal details",
      `${record.items_masked} masked before storage`, "is-mask"));
  }
  if (record.parsed_ok === false) {
    deltaTd.appendChild(changeLine("Not parsed", record.reason || "malformed record", "is-drop"));
  } else if (!ev) {
    deltaTd.appendChild(changeLine("Not yet in window", "the clock has not reached this record", "is-quiet"));
  } else {
    if (ev.start_utc) {
      deltaTd.appendChild(changeLine("Time", `${toISTClock(ev.start_utc)} IST → ${ev.start_utc}`));
    }
    if (ev.lat != null && ev.lon != null) {
      deltaTd.appendChild(changeLine(resolutionWord(ev.resolution),
        `${ev.lat.toFixed(4)}, ${ev.lon.toFixed(4)} → ${ev.h3_cell}`));
    }
    if (ev.is_duplicate) {
      deltaTd.appendChild(changeLine("Duplicate", "flagged, not counted again", "is-dup"));
    }
  }
  tr.appendChild(deltaTd);

  // --- canonical event ---
  const cleanTd = document.createElement("td");
  cleanTd.className = "xform__clean";
  if (!ev) {
    cleanTd.textContent = record.parsed_ok === false ? "Dropped — never became an event." : "—";
    cleanTd.classList.add("is-quiet");
  } else {
    const cat = document.createElement("span");
    cat.className = "xform__category";
    cat.textContent = CATEGORY_LABELS[ev.category]?.en || ev.category;
    cleanTd.appendChild(cat);

    const dl = document.createElement("dl");
    dl.className = "xform__fields";
    addRow(dl, "category", ev.category);
    addRow(dl, "severity", ev.severity.toFixed(2));
    addRow(dl, "confidence", ev.confidence.toFixed(2));
    if (ev.measure != null) addRow(dl, "measure", String(ev.measure));
    cleanTd.appendChild(dl);
  }
  tr.appendChild(cleanTd);

  return tr;
}

function changeLine(label, value, extraClass) {
  const p = document.createElement("p");
  p.className = "xform__change" + (extraClass ? ` ${extraClass}` : "");
  const k = document.createElement("span");
  k.className = "xform__change-key";
  k.textContent = label;
  const v = document.createElement("span");
  v.className = "xform__change-val";
  v.textContent = value;
  p.appendChild(k);
  p.appendChild(v);
  return p;
}

// CONTRACT.md's `resolution` says how a place in the raw record became a point.
function resolutionWord(resolution) {
  if (resolution === "landmark") return "Landmark resolved";
  if (resolution === "registry") return "Registry id resolved";
  if (resolution === "direct") return "Coordinates given";
  return "Place resolved";
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
  addRow(primary, "Lead time", leadTimeText(card.median_detection_lag_sec));
  el.appendChild(primary);

  if (card.detection_lag_caveat) {
    const caveat = document.createElement("p");
    caveat.className = "scorecard__caveat";
    caveat.textContent = card.detection_lag_caveat;
    el.appendChild(caveat);
  }

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

function leadTimeText(sec) {
  if (sec == null) return "Not available";
  if (sec >= 0) return `Typically noticed ${formatDuration(sec)} after it started`;
  return `Typically noticed ${formatDuration(-sec)} before it fully unfolded`;
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
