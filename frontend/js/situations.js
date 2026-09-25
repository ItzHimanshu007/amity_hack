/* js/situations.js — "What's happening" card list (#situation-rail), the
 * "Why do we think this?" timeline, and the "Probably unrelated" section
 * (#unrelated-section). DESIGN.md §3, §4, §7. CONTRACT.md §F.
 *
 * Owns: #situation-rail, #unrelated-section on index.html.
 */

import {
  fetchState, fetchSituation, connectStream, onSituationSelected,
  CATEGORY_LABELS, FEED_LABELS, toISTClock, formatDuration, timeAgo,
} from "./api.js";

// ------------------------------------------------------------------ state --

const situationsById = new Map();   // situation_id -> situation object (§F shape)
const detailCache = new Map();      // situation_id -> full GET /situations/{id} response
const eventCache = new Map();       // event_id -> canonical event (for facts row + source list)
let currentSimTimeUtc = null;       // latest tick's sim_time_utc, for "started N ago"
let latestRejectedCandidates = [];  // GET /state's rejected_candidates, if/when the backend ships it

// category -> feed ids, mirrored from CONTRACT.md §B "Emitted by" column.
// Fallback only, used when a situation's member events aren't in eventCache
// yet (the /state events list is ACTIVE events only, last 30 min).
const CATEGORY_FEEDS_FALLBACK = {
  "weather.rain": ["weather_imd"], "weather.heat": ["weather_imd"],
  "air.pm25": ["air_sensors"], "power.outage": ["power_discom"],
  "traffic.signal_down": ["power_discom", "civic_complaints"],
  "drain.overflow": ["drain_scada"],
  "complaint.waterlogging": ["civic_complaints"], "complaint.garbage": ["civic_complaints"],
  "complaint.streetlight": ["civic_complaints"], "complaint.road_damage": ["civic_complaints"],
  "complaint.smoke": ["civic_complaints"],
};

// ------------------------------------------------------------------ dom ----

let railEl, unrelatedEl;

function init() {
  railEl = document.getElementById("situation-rail");
  unrelatedEl = document.getElementById("unrelated-section");
  if (!railEl || !unrelatedEl) return; // not on this page

  renderRailEmptyState();
  unrelatedEl.hidden = true;

  fetchState().then((state) => {
    for (const ev of state.events || []) eventCache.set(ev.event_id, ev);
    currentSimTimeUtc = state.sim && state.sim.sim_time_utc;
    latestRejectedCandidates = state.rejected_candidates || [];
    for (const sit of state.situations || []) situationsById.set(sit.situation_id, sit);
    renderRailFromScratch();
    renderUnrelatedSection();
  }).catch((err) => {
    console.error("[situations] fetchState failed", err);
    renderRailEmptyState("Not connected to the backend yet.");
  });

  connectStream(
    (tickData) => { currentSimTimeUtc = tickData.sim_time_utc; refreshLiveTimes(); },
    (eventData) => { eventCache.set(eventData.event_id, eventData); },
    (situationData, action) => handleSituationMessage(situationData, action),
    null, // feed health is dataroom.js's concern
  );

  onSituationSelected((situationId) => openSituationDetail(situationId, { scrollIntoView: true }));

  // js/dashboard.js refreshes GET /state's rejected_candidates as the replay moves.
  window.addEventListener("rejected:update", (ev) => {
    latestRejectedCandidates = ev.detail || [];
    renderUnrelatedSection();
  });
}

// ------------------------------------------------------- WS situation flow -

function handleSituationMessage(data, action) {
  situationsById.set(data.situation_id, data);
  detailCache.delete(data.situation_id); // full detail (member_events) is now stale

  if (data.is_decoy) {
    removeCardFromRail(data.situation_id);
    renderUnrelatedSection();
    return;
  }

  const existing = railEl.querySelector(cardSelector(data.situation_id));
  if (action === "created" && !existing) {
    insertCard(data, { animate: true });
  } else if (existing) {
    updateCardContent(existing, data);
    if (!existing.querySelector(".situation-timeline").hidden) {
      // Was expanded — refresh its detail content in place, no re-slide.
      renderTimelineInto(existing.querySelector(".situation-timeline"), data);
    }
  } else {
    // "updated"/"closed" arrived before we ever saw "created" (e.g. page
    // loaded mid-stream) — show it now, without the entrance animation.
    insertCard(data, { animate: false });
  }
}

function cardSelector(id) {
  const esc = window.CSS && CSS.escape ? CSS.escape(id) : id;
  return `.situation-card[data-situation-id="${esc}"]`;
}

function removeCardFromRail(id) {
  const el = railEl.querySelector(cardSelector(id));
  if (el) el.remove();
  if (!railEl.querySelector(".situation-card")) renderRailEmptyState();
}

// ------------------------------------------------------------- rendering ---

function renderRailEmptyState(message) {
  railEl.innerHTML = "";
  const p = document.createElement("p");
  p.className = "situation-rail__empty";
  // Not "Nothing unusual right now" — the status block above already says that,
  // in both languages. This line says what this list is, instead of repeating it.
  p.textContent = message || "No linked situations to show.";
  railEl.appendChild(p);
}

function renderRailFromScratch() {
  railEl.innerHTML = "";
  const active = [...situationsById.values()]
    .filter((s) => !s.is_decoy)
    .sort((a, b) => new Date(b.created_utc) - new Date(a.created_utc)); // newest first
  if (active.length === 0) { renderRailEmptyState(); return; }
  for (const sit of active) insertCard(sit, { animate: false });
}

function insertCard(situation, { animate }) {
  const empty = railEl.querySelector(".situation-rail__empty");
  if (empty) railEl.innerHTML = "";

  const card = buildCardShell(situation);
  if (animate) {
    card.classList.add("situation-card--enter");
    railEl.insertBefore(card, railEl.firstChild); // newest first
    // Force layout, then flip to the "active" state so the transition runs.
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        card.classList.add("situation-card--enter-active");
      });
    });
    card.addEventListener("transitionend", () => {
      card.classList.remove("situation-card--enter", "situation-card--enter-active");
    }, { once: true });
  } else {
    railEl.insertBefore(card, railEl.firstChild);
  }
  return card;
}

function buildCardShell(situation) {
  const card = document.createElement("article");
  card.className = "situation-card";
  card.dataset.situationId = situation.situation_id;
  card.dataset.level = situation.alert_level;
  card.tabIndex = 0;

  const top = document.createElement("div");
  top.className = "situation-card__row";
  top.appendChild(buildAlertChip(situation.alert_level));
  const zone = document.createElement("span");
  zone.className = "situation-card__zone";
  top.appendChild(zone);
  card.appendChild(top);

  const hEn = document.createElement("h3");
  hEn.className = "situation-card__headline-en";
  card.appendChild(hEn);

  const hHi = document.createElement("p");
  hHi.className = "situation-card__headline-hi";
  hHi.lang = "hi";
  card.appendChild(hHi);

  const facts = document.createElement("div");
  facts.className = "situation-card__facts";
  card.appendChild(facts);

  const why = document.createElement("button");
  why.type = "button";
  why.className = "text-button situation-card__why";
  why.textContent = "Why do we think this?";
  card.appendChild(why);

  const timeline = document.createElement("div");
  timeline.className = "situation-timeline";
  timeline.hidden = true;
  card.appendChild(timeline);

  card.addEventListener("click", () => openSituationDetail(situation.situation_id));
  why.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleSituationDetail(situation.situation_id);
  });

  updateCardContent(card, situation);
  return card;
}

function buildAlertChip(level) {
  const chip = document.createElement("span");
  chip.className = `alert-chip alert-chip--${level}`;
  if (level === "red") chip.classList.add("hatch-red");
  const swatch = document.createElement("span");
  swatch.className = "alert-chip__swatch";
  const label = document.createElement("span");
  label.className = "alert-chip__label";
  label.textContent = alertLevelWord(level);
  chip.appendChild(swatch);
  chip.appendChild(label);
  return chip;
}

function alertLevelWord(level) {
  // Render alert_level exactly as the API gives it — this only maps the
  // already-server-decided word to English text, it never re-derives level.
  return { green: "All normal", yellow: "Be aware", orange: "Be prepared", red: "Take action" }[level] || level;
}

function updateCardContent(card, situation) {
  card.dataset.level = situation.alert_level;
  const chip = buildAlertChip(situation.alert_level);
  const oldChip = card.querySelector(".alert-chip");
  oldChip.replaceWith(chip);

  card.querySelector(".situation-card__zone").textContent = situation.zone.label_en;
  card.querySelector(".situation-card__headline-en").textContent = situation.headline_en;
  card.querySelector(".situation-card__headline-hi").textContent = situation.headline_hi;

  const facts = card.querySelector(".situation-card__facts");
  facts.innerHTML = "";
  const reportCount = (situation.member_event_ids || []).length;
  const feedCount = distinctFeedCount(situation);
  const parts = [
    `${reportCount} report${reportCount === 1 ? "" : "s"}`,
    `${feedCount} feed${feedCount === 1 ? "" : "s"}`,
  ];
  const started = startedAgoText(situation);
  if (started) parts.push(started);
  if (situation.status === "closed") parts.push("closed");
  for (const [i, text] of parts.entries()) {
    const span = document.createElement("span");
    span.textContent = text;
    facts.appendChild(span);
    if (i < parts.length - 1) {
      const rule = document.createElement("span");
      rule.className = "situation-card__fact-rule";
      rule.setAttribute("aria-hidden", "true");
      facts.appendChild(rule);
    }
  }
}

function distinctFeedCount(situation) {
  const feeds = new Set();
  for (const eid of situation.member_event_ids || []) {
    const ev = eventCache.get(eid);
    if (ev && ev.source) feeds.add(ev.source);
  }
  if (feeds.size === 0) {
    for (const step of situation.chain || []) {
      const opts = CATEGORY_FEEDS_FALLBACK[step.category];
      if (opts) feeds.add(opts[0]);
    }
  }
  return feeds.size;
}

function startedAgoText(situation) {
  const first = situation.chain && situation.chain[0];
  if (!first || !currentSimTimeUtc) return "";
  return `started ${timeAgo(first.t_utc, currentSimTimeUtc)}`;
}

function refreshLiveTimes() {
  for (const card of railEl.querySelectorAll(".situation-card")) {
    const sit = situationsById.get(card.dataset.situationId);
    if (!sit) continue;
    const started = startedAgoText(sit);
    const facts = card.querySelectorAll(".situation-card__facts span:not(.situation-card__fact-rule)");
    if (facts.length >= 3 && started) facts[2].textContent = started;
  }
}

// ---------------------------------------------------------- detail/timeline

async function toggleSituationDetail(id) {
  const card = railEl.querySelector(cardSelector(id));
  if (!card) return;
  const timeline = card.querySelector(".situation-timeline");
  if (!timeline.hidden) { timeline.hidden = true; return; }
  await openSituationDetail(id);
}

async function openSituationDetail(id, { scrollIntoView } = {}) {
  let card = railEl.querySelector(cardSelector(id));
  if (!card) {
    // Map selected a situation not currently in the rail (e.g. it's a
    // decoy, or the rail hasn't loaded it yet) — nothing to expand here.
    return;
  }
  for (const other of railEl.querySelectorAll(".situation-card.is-selected")) {
    if (other !== card) other.classList.remove("is-selected");
  }
  card.classList.add("is-selected");
  const timeline = card.querySelector(".situation-timeline");
  timeline.hidden = false;

  let full = detailCache.get(id);
  if (!full) {
    timeline.innerHTML = `<p class="situation-timeline__loading">Loading…</p>`;
    try {
      full = await fetchSituation(id);
      detailCache.set(id, full);
      for (const ev of full.member_events || []) eventCache.set(ev.event_id, ev);
    } catch (err) {
      timeline.innerHTML = `<p class="situation-timeline__loading">Could not load this situation (${err.message}).</p>`;
      return;
    }
  }
  renderTimelineInto(timeline, full);
  if (scrollIntoView) card.scrollIntoView({ block: "nearest" });
}

function renderTimelineInto(container, situation) {
  container.innerHTML = "";

  const ol = document.createElement("ol");
  ol.className = "timeline-steps";
  for (const step of situation.chain || []) {
    const li = document.createElement("li");
    li.className = "timeline-step";
    const dot = document.createElement("span");
    dot.className = "timeline-step__dot";
    dot.textContent = String(step.step);
    const body = document.createElement("div");
    body.className = "timeline-step__body";
    const en = document.createElement("p");
    en.className = "timeline-step__en";
    en.textContent = step.text_en;
    const hi = document.createElement("p");
    hi.className = "timeline-step__hi";
    hi.lang = "hi";
    hi.textContent = step.text_hi;
    body.appendChild(en);
    body.appendChild(hi);
    const meta = document.createElement("div");
    meta.className = "timeline-step__meta";
    meta.title = step.h3_cell; // DESIGN.md §C: a raw H3 index may appear in a tooltip, never inline
    const catLabel = CATEGORY_LABELS[step.category]?.en || step.category;
    meta.textContent = catLabel;
    li.appendChild(dot);
    li.appendChild(body);
    li.appendChild(meta);
    ol.appendChild(li);
  }
  container.appendChild(ol);

  const evidence = situation.evidence || {};
  const footer = document.createElement("ul");
  footer.className = "evidence-footer";
  const whereText = evidence.spatial && evidence.spatial.note_en;
  const whenText = whenTextFrom(evidence.temporal_gaps);
  const howText = evidence.lift && evidence.lift.note_en;
  for (const [label, text] of [["Where", whereText], ["When", whenText], ["How unusual", howText]]) {
    if (!text) continue;
    const li = document.createElement("li");
    const strong = document.createElement("strong");
    strong.textContent = label;
    li.appendChild(strong);
    li.appendChild(document.createTextNode(` — ${text}`));
    footer.appendChild(li);
  }
  container.appendChild(footer);

  const confLine = document.createElement("p");
  confLine.className = "confidence-line";
  confLine.innerHTML = `How sure we are: <strong>${escapeHtml(confidenceWord(situation.confidence_level))}</strong>`;
  container.appendChild(confLine);

  // Confirmed live 2026-09-25 against GET /situations/{id}: the field is
  // `framing_text` / `framing_text_hi` (no `_en` suffix on the English one),
  // and it is ONLY present on the detail endpoint's response, not on /state's
  // or the WS "situation" message's embedded situation objects — hence the
  // fallback to confidence_reason_en/hi for any caller working from the
  // summary alone. Render verbatim, never our own "possible link" wording.
  const framing = situation.framing_text || situation.framing_text_en || situation.confidence_reason_en;
  const framingHi = situation.framing_text_hi || situation.confidence_reason_hi;
  if (framing) {
    const p = document.createElement("p");
    p.className = "confidence-reason";
    p.textContent = framing;
    container.appendChild(p);
  }
  if (framingHi) {
    const p = document.createElement("p");
    p.className = "confidence-reason confidence-reason--hi";
    p.lang = "hi";
    p.textContent = framingHi;
    container.appendChild(p);
  }

  // `partial`/`members_known`/`partial_of`: live fields not in CONTRACT.md
  // today (confirmed 2026-09-25 against a running situation) — the engine's
  // own signal that it has only matched some of an estimated larger cluster.
  // Surfacing it is exactly the epistemic-honesty spirit DESIGN.md asks for.
  const partial = partialNote(situation);
  if (partial) {
    const p = document.createElement("p");
    p.className = "confidence-reason situation-timeline__partial";
    p.textContent = partial.en;
    const pHi = document.createElement("p");
    pHi.className = "confidence-reason confidence-reason--hi situation-timeline__partial";
    pHi.lang = "hi";
    pHi.textContent = partial.hi;
    container.appendChild(p);
    container.appendChild(pHi);
  }

  const predicted = predictedNextText(situation.predicted_next);
  if (predicted) {
    const p = document.createElement("p");
    p.className = "predicted-next";
    p.textContent = predicted.en;
    const pHi = document.createElement("p");
    pHi.className = "predicted-next predicted-next--hi";
    pHi.lang = "hi";
    pHi.textContent = predicted.hi;
    container.appendChild(p);
    container.appendChild(pHi);
  }

  if (situation.member_events && situation.member_events.length) {
    const wrap = document.createElement("div");
    wrap.className = "source-events";
    const heading = document.createElement("p");
    heading.className = "source-events__heading";
    heading.textContent = "Source reports";
    wrap.appendChild(heading);
    const ul = document.createElement("ul");
    for (const ev of situation.member_events) {
      const li = document.createElement("li");
      li.title = ev.h3_cell;
      const cat = CATEGORY_LABELS[ev.category]?.en || ev.category;
      const feed = FEED_LABELS[ev.source]?.en || ev.source;
      li.textContent = `${cat} · ${feed} · ${toISTClock(ev.start_utc)}`;
      ul.appendChild(li);
    }
    wrap.appendChild(ul);
    container.appendChild(wrap);
  }
}

function whenTextFrom(gaps) {
  if (!gaps || gaps.length === 0) return null;
  return gaps.map((g) => formatDuration(g.gap_sec)).join(", then ") + " apart";
}

function partialNote(situation) {
  if (!situation.partial) return null;
  const known = situation.members_known ?? (situation.member_event_ids || []).length;
  const total = situation.partial_of;
  if (total == null) return null;
  return {
    en: `Based on ${known} of an estimated ${total} related reports so far.`,
    hi: `अब तक अनुमानित ${total} में से ${known} संबंधित रिपोर्ट के आधार पर।`,
  };
}

// predicted_next's real live shape (confirmed 2026-09-25 against a running
// situation) is a LIST of {category, trigger_category, plausible_because_en,
// plausible_because_hi} — each entry already worded as a mechanism, not a
// certainty, so it's used close to verbatim. A single-object
// {category, typical_lag_range_sec, based_on} shape was also seen in an
// on-disk situations.jsonl snapshot from what looks like an older engine
// run; handled here too so this doesn't break if a scenario reset serves
// that shape instead. null/empty -> no line at all, per DESIGN.md's rule
// against claiming a pattern we don't have.
function predictedNextText(pn) {
  if (!pn) return null;
  const list = Array.isArray(pn) ? pn : [pn];
  if (list.length === 0) return null;
  const items = list.map((p) => {
    const catEn = (CATEGORY_LABELS[p.category]?.en || p.category).toLowerCase();
    const catHi = CATEGORY_LABELS[p.category]?.hi || p.category;
    if (p.plausible_because_en) {
      return { en: `${catEn} — ${p.plausible_because_en}`, hi: `${catHi} — ${p.plausible_because_hi || ""}` };
    }
    const [lagLo, lagHiSec] = p.typical_lag_range_sec || [];
    let lagEn = null, lagHi = null;
    if (lagLo != null && lagHiSec != null) {
      if (lagLo === 0) { lagEn = `within about ${formatDuration(lagHiSec)}`; lagHi = `लगभग ${formatDuration(lagHiSec)} के भीतर`; }
      else { lagEn = `${formatDuration(lagLo)} to ${formatDuration(lagHiSec)}`; lagHi = `${formatDuration(lagLo)} से ${formatDuration(lagHiSec)} के बीच`; }
    }
    const basis = p.based_on ? ` (based on ${p.based_on} before)` : "";
    return {
      en: lagEn ? `${catEn}, usually ${lagEn}${basis}` : `${catEn}${basis}`,
      hi: lagHi ? `${catHi}, आमतौर पर ${lagHi} में` : catHi,
    };
  });
  return {
    en: `Watch for next: ${items.map((i) => i.en).join("; ")}`,
    hi: `आगे इसकी आशंका: ${items.map((i) => i.hi).join("; ")}`,
  };
}

// CONTRACT.md §F's enum is low|med|high; "med" is an internal abbreviation,
// not a word a resident reads naturally — expand it, still just the given
// level, nothing re-derived.
function confidenceWord(level) {
  return { low: "low", med: "medium", high: "high" }[level] || level;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ----------------------------------------------------- "probably unrelated"

function renderUnrelatedSection() {
  const decoys = [...situationsById.values()].filter((s) => s.is_decoy);
  if (decoys.length === 0 && latestRejectedCandidates.length === 0) {
    unrelatedEl.hidden = true;
    unrelatedEl.innerHTML = "";
    return;
  }
  unrelatedEl.hidden = false;
  unrelatedEl.innerHTML = "";

  const heading = document.createElement("h2");
  heading.className = "unrelated-section__heading";
  heading.textContent = "Probably unrelated";
  const headingHi = document.createElement("p");
  headingHi.className = "unrelated-section__heading-hi";
  headingHi.lang = "hi";
  headingHi.textContent = "शायद असंबंधित";
  const intro = document.createElement("p");
  intro.className = "unrelated-section__intro";
  intro.textContent = "These showed up together but we don't think they're connected.";
  const introHi = document.createElement("p");
  introHi.className = "unrelated-section__intro unrelated-section__intro--hi";
  introHi.lang = "hi";
  introHi.textContent = "ये एक साथ दिखे लेकिन हमें नहीं लगता ये जुड़े हैं।";
  unrelatedEl.appendChild(heading);
  unrelatedEl.appendChild(headingHi);
  unrelatedEl.appendChild(intro);
  unrelatedEl.appendChild(introHi);

  const list = document.createElement("div");
  list.className = "unrelated-list";
  for (const sit of decoys) list.appendChild(buildDecoyCard(sit));
  for (const rej of latestRejectedCandidates) list.appendChild(buildRejectedRow(rej));
  unrelatedEl.appendChild(list);
}

function buildDecoyCard(situation) {
  const card = document.createElement("article");
  card.className = "situation-card situation-card--decoy";
  card.dataset.situationId = situation.situation_id;
  card.dataset.level = situation.alert_level; // capped at yellow by the server (CONTRACT.md §F)

  const top = document.createElement("div");
  top.className = "situation-card__row";
  top.appendChild(buildAlertChip(situation.alert_level));
  const zone = document.createElement("span");
  zone.className = "situation-card__zone";
  zone.textContent = situation.zone.label_en;
  top.appendChild(zone);
  card.appendChild(top);

  const hEn = document.createElement("h3");
  hEn.className = "situation-card__headline-en";
  hEn.textContent = situation.headline_en;
  const hHi = document.createElement("p");
  hHi.className = "situation-card__headline-hi";
  hHi.lang = "hi";
  hHi.textContent = situation.headline_hi;
  card.appendChild(hEn);
  card.appendChild(hHi);

  const why = document.createElement("p");
  why.className = "situation-card__decoy-reason";
  const liftNote = situation.evidence && situation.evidence.lift && situation.evidence.lift.note_en;
  why.textContent = liftNote || situation.confidence_reason_en || "";
  card.appendChild(why);

  return card;
}

function buildRejectedRow(rej) {
  const card = document.createElement("article");
  card.className = "situation-card situation-card--decoy situation-card--rejected";

  const cats = (rej.categories || []).map((c) => CATEGORY_LABELS[c]?.en || c);
  const catsHi = (rej.categories || []).map((c) => CATEGORY_LABELS[c]?.hi || c);
  const heading = document.createElement("h3");
  heading.className = "situation-card__headline-en";
  heading.textContent = cats.length ? cats.join(" and ") : "Unrelated pattern";
  const headingHi = document.createElement("p");
  headingHi.className = "situation-card__headline-hi";
  headingHi.lang = "hi";
  headingHi.textContent = catsHi.join(" और ");

  const reason = document.createElement("p");
  reason.className = "situation-card__decoy-reason";
  reason.textContent = humanize(rej.reason_en, rej.categories, "en");
  const reasonHi = document.createElement("p");
  reasonHi.className = "situation-card__decoy-reason situation-card__decoy-reason--hi";
  reasonHi.lang = "hi";
  reasonHi.textContent = humanize(rej.reason_hi, rej.categories, "hi");

  card.appendChild(heading);
  card.appendChild(headingHi);
  card.appendChild(reason);
  card.appendChild(reasonHi);
  return card;
}

function humanize(text, categories, lang) {
  if (!text) return "";
  let out = text
    .replace("but this category pair is not in the plausibility table", "but neither is a known cause of the other")
    .replace("लेकिन यह जोड़ी प्रशंसनीयता तालिका में नहीं है", "लेकिन इनमें से कोई दूसरे का ज्ञात कारण नहीं है");
  for (const cat of categories || []) {
    const label = CATEGORY_LABELS[cat]?.[lang];
    if (label) out = out.split(cat).join(label);
  }
  return out;
}

// -------------------------------------------------------------------- boot

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
