/* js/resident.js — the resident (mobile) view. DESIGN.md §1 (status block),
 * §4 (condensed timeline), §7 (probably unrelated). CONTRACT.md §F.
 *
 * Owns: resident.html's #lang-toggle, #status-block, #area-picker,
 * #situation-summary, #situation-list, #unrelated-section.
 *
 * The status block itself is js/statusblock.js's shared renderStatusBlock(),
 * also used by js/city.js — per DESIGN.md §1 it always shows the status
 * word in ink (not the alert color) with the Hindi twin stacked under the
 * English body line, regardless of this page's own EN/हिंदी toggle (that
 * toggle governs the rest of the page's strings, not this component).
 */

import {
  fetchState, fetchSituation, connectStream, onSituationSelected,
  CATEGORY_LABELS, ALERT_WORDS, toISTClock, formatDuration, timeAgo,
  ACTION_BY_CATEGORY,
} from "./api.js";
import { renderStatusBlock } from "./statusblock.js";

const detailCache = new Map(); // situation_id -> full GET /situations/{id} response (has framing_text)

// ------------------------------------------------------------------ i18n ---
// A strings table, not a framework, per the task brief.

const STRINGS = {
  en: {
    simulated: "Simulated data",
    allAreas: "All areas",
    areaPickerLabel: "Your area",
    nothingUnusual: "Nothing unusual right now.",
    thingsHappening: (n, cells) => `${n} thing${n === 1 ? "" : "s"} happening across ${cells} area${cells === 1 ? "" : "s"}`,
    updatedAgo: (t) => `Updated ${t}`,
    noSituationSelected: "Nothing to report for this area right now.",
    suggestedActionHeading: "Suggested action",
    noActionNeeded: "No action needed.",
    why: "Why do we think this?",
    hide: "Hide the explanation",
    watchForNext: "Watch for next",
    howSure: "How sure we are",
    situationsHeading: "What's happening",
    unrelatedHeading: "Probably unrelated",
    unrelatedIntro: "These showed up together but we don't think they're connected.",
    situationUpdated: (t) => `Updated ${t}`,
    alertLabel: "Alert me about",
    alertOff: "No alerts",
    alertWatching: (a) => `Watching ${a}. You'll get an alert here when something starts or gets worse.`,
    alertAlready: (n, a) => `${n} thing${n === 1 ? "" : "s"} already happening near ${a}.`,
    alertEnableBrowser: "Also show browser notifications",
    alertNew: (a) => `New near ${a}`,
    alertWorse: (a) => `Getting worse near ${a}`,
    alertDismiss: "Dismiss",
  },
  hi: {
    simulated: "नमूना डेटा",
    allAreas: "सभी क्षेत्र",
    areaPickerLabel: "आपका क्षेत्र",
    nothingUnusual: "अभी कुछ भी असामान्य नहीं है।",
    thingsHappening: (n, cells) => `${cells} क्षेत्रों में ${n} घटनाएँ हो रही हैं`,
    updatedAgo: (t) => `${t} अपडेट किया गया`,
    noSituationSelected: "अभी इस क्षेत्र के लिए कुछ भी रिपोर्ट करने को नहीं है।",
    suggestedActionHeading: "सुझाई गई कार्रवाई",
    noActionNeeded: "कोई कार्रवाई ज़रूरी नहीं।",
    why: "हमें ऐसा क्यों लगता है?",
    hide: "स्पष्टीकरण छिपाएँ",
    watchForNext: "आगे इसकी आशंका",
    howSure: "हम कितने निश्चित हैं",
    situationsHeading: "क्या हो रहा है",
    unrelatedHeading: "शायद असंबंधित",
    unrelatedIntro: "ये एक साथ दिखे लेकिन हमें नहीं लगता ये जुड़े हैं।",
    situationUpdated: (t) => `${t} अपडेट किया गया`,
    alertLabel: "मुझे इसकी सूचना दें",
    alertOff: "कोई सूचना नहीं",
    alertWatching: (a) => `${a} पर नज़र है। कुछ शुरू होने या बिगड़ने पर यहाँ सूचना मिलेगी।`,
    alertAlready: (n, a) => `${a} के पास अभी ${n} घटनाएँ चल रही हैं।`,
    alertEnableBrowser: "ब्राउज़र सूचनाएँ भी दिखाएँ",
    alertNew: (a) => `${a} के पास नई घटना`,
    alertWorse: (a) => `${a} के पास स्थिति बिगड़ रही है`,
    alertDismiss: "हटाएँ",
  },
};

// ----------------------------------------------------------- area alerts ---
// The nine fixed landmark areas (CONTRACT.md §C). A situation belongs to an
// area when its zone label names that landmark — labels are derived from the
// nearest landmark, so this needs no h3 library on this page.
const ALERT_AREAS = [
  { en: "Hawa Mahal", hi: "हवा महल" }, { en: "Amer Fort", hi: "आमेर किला" },
  { en: "Jal Mahal", hi: "जल महल" }, { en: "Albert Hall Museum", hi: "अल्बर्ट हॉल" },
  { en: "Jaipur Junction", hi: "जयपुर जंक्शन" }, { en: "Sindhi Camp", hi: "सिंधी कैंप" },
  { en: "Vaishali Nagar", hi: "वैशाली नगर" }, { en: "Malviya Nagar", hi: "मालवीय नगर" },
  { en: "Mansarovar", hi: "मानसरोवर" }, { en: "Vidyadhar Nagar", hi: "विद्याधर नगर" },
  { en: "Tonk Road", hi: "टोंक रोड" }, { en: "Jagatpura", hi: "जगतपुरा" },
  { en: "Sanganer", hi: "सांगानेर" },
];
const LEVEL_ORDER = ["green", "yellow", "orange", "red"];
const ALERT_STORAGE_KEY = "nagarnaadi.alertArea";
let alertArea = null; // ALERT_AREAS entry or null
const recentAlerts = []; // { situation, kind }
try {
  const saved = localStorage.getItem(ALERT_STORAGE_KEY);
  alertArea = ALERT_AREAS.find((a) => a.en === saved) || null;
} catch { /* storage unavailable */ }

// ------------------------------------------------------------------ state --

let lang = "en";
let situationsById = new Map();
let currentSimTimeUtc = null;
let selectedZoneLabel = null; // null = "All areas"

let els = {};

function t(key, ...args) {
  const v = STRINGS[lang][key];
  return typeof v === "function" ? v(...args) : v;
}

// CONTRACT.md §F's confidence_level enum is low|med|high; expand the "med"
// abbreviation to a real word, in whichever language is on screen.
const CONFIDENCE_WORDS = {
  en: { low: "low", med: "medium", high: "high" },
  hi: { low: "कम", med: "मध्यम", high: "उच्च" },
};
function confidenceWord(level) {
  return CONFIDENCE_WORDS[lang][level] || level;
}

function init() {
  const root = document.getElementById("resident-view");
  if (!root) return;

  els.simBanner = document.getElementById("simulated-banner");
  els.langToggle = document.getElementById("lang-toggle");
  els.statusBlock = document.getElementById("status-block");
  els.areaPicker = document.getElementById("area-picker");
  els.summary = document.getElementById("situation-summary");
  els.list = document.getElementById("situation-list");
  els.unrelated = document.getElementById("unrelated-section");
  els.alerts = document.getElementById("area-alerts");

  buildLangToggle();
  // No manual retick needed here — statusblock.js's renderStatusBlock()
  // runs its own internal 1s timer per target element for the freshness
  // line, started the first time it's invoked below.

  fetchState().then((state) => {
    for (const sit of state.situations || []) situationsById.set(sit.situation_id, sit);
    currentSimTimeUtc = state.sim && state.sim.sim_time_utc;
    renderAll(state.city, state.counts);
  }).catch((err) => {
    renderConnectingState(err);
  });

  connectStream(
    (tickData) => {
      currentSimTimeUtc = tickData.sim_time_utc;
      updateStatusBlock({ alert_level: tickData.city_alert_level },
        { situations_active: tickData.situations_active, cells_touched: undefined });
    },
    null, // resident view does not need the raw event stream directly
    (situationData, action) => {
      const prev = situationsById.get(situationData.situation_id);
      situationsById.set(situationData.situation_id, situationData);
      maybeAlert(situationData, action, prev && prev.alert_level);
      renderAreaPicker();
      renderSituationList();
      renderSummary();
      renderUnrelated();
    },
    null,
  );

  onSituationSelected((situationId) => {
    const sit = situationsById.get(situationId);
    if (sit && !sit.is_decoy) { selectedZoneLabel = sit.zone.label_en; render(); }
  });
}

function renderConnectingState(err) {
  if (!els.statusBlock) return;
  renderStatusBlock(els.statusBlock, "green", { en: `Not connected to the backend yet (${err.message}).`, hi: "" });
}

function renderAll(city, counts) {
  renderSimBanner();
  updateStatusBlock(city, counts);
  renderAlerts();
  renderAreaPicker();
  renderSituationList();
  renderSummary();
  renderUnrelated();
}

function render() {
  renderAlerts();
  renderAreaPicker();
  renderSituationList();
  renderSummary();
  renderUnrelated();
}

// -------------------------------------------------------------- language --

function buildLangToggle() {
  els.langToggle.innerHTML = "";
  const enBtn = document.createElement("button");
  enBtn.type = "button";
  enBtn.className = "text-button lang-toggle__button";
  enBtn.textContent = "English";
  const hiBtn = document.createElement("button");
  hiBtn.type = "button";
  hiBtn.className = "text-button lang-toggle__button";
  hiBtn.textContent = "हिंदी";
  const mark = () => {
    enBtn.classList.toggle("is-active", lang === "en");
    hiBtn.classList.toggle("is-active", lang === "hi");
  };
  enBtn.addEventListener("click", () => { lang = "en"; mark(); render(); });
  hiBtn.addEventListener("click", () => { lang = "hi"; mark(); render(); });
  mark();
  els.langToggle.appendChild(enBtn);
  els.langToggle.appendChild(hiBtn);
}

function renderSimBanner() {
  if (!els.simBanner) return;
  // is_simulated is true for every event in this build (CONTRACT.md §H) —
  // shown unconditionally rather than inspecting each event for the flag.
  els.simBanner.textContent = t("simulated");
  els.simBanner.hidden = false;
}

// --------------------------------------------------------- status block ---

const CELLS_FALLBACK = { en: "several", hi: "कई" };

// DESIGN.md §1: always the city-wide roll-up, never a per-area figure —
// /state has no per-cell roll-up to read today, and the resident view is
// pinned to `city` regardless. alert_level is rendered exactly as given.
function statusSummary(counts) {
  const situationsActive = (counts && counts.situations_active) ?? [...situationsById.values()].filter((s) => !s.is_decoy).length;
  const cellsTouched = counts && counts.cells_touched;
  if (situationsActive === 0) {
    return { en: STRINGS.en.nothingUnusual, hi: STRINGS.hi.nothingUnusual };
  }
  return {
    en: STRINGS.en.thingsHappening(situationsActive, cellsTouched ?? CELLS_FALLBACK.en),
    hi: STRINGS.hi.thingsHappening(situationsActive, cellsTouched ?? CELLS_FALLBACK.hi),
  };
}

function updateStatusBlock(city, counts) {
  if (!els.statusBlock) return;
  const level = (city && city.alert_level) || "green";
  renderStatusBlock(els.statusBlock, level, statusSummary(counts));
}

// ----------------------------------------------------------- area alerts ---

function areaName(area) { return lang === "hi" ? area.hi : area.en; }

function situationInArea(sit, area) {
  return !sit.is_decoy && sit.status !== "closed" && (sit.zone?.label_en || "").includes(area.en);
}

function maybeAlert(sit, action, prevLevel) {
  if (!alertArea || !situationInArea(sit, alertArea)) return;
  let kind = null;
  if (action === "created") kind = "new";
  else if (prevLevel && LEVEL_ORDER.indexOf(sit.alert_level) > LEVEL_ORDER.indexOf(prevLevel)) kind = "worse";
  if (!kind) return;
  recentAlerts.unshift({ situation: sit, kind });
  recentAlerts.length = Math.min(recentAlerts.length, 3);
  renderAlerts();
  try {
    if ("Notification" in window && Notification.permission === "granted") {
      new Notification(kind === "new" ? STRINGS.en.alertNew(alertArea.en) : STRINGS.en.alertWorse(alertArea.en), {
        body: sit.headline_en, tag: sit.situation_id,
      });
    }
  } catch { /* notifications unavailable */ }
}

function renderAlerts() {
  if (!els.alerts) return;
  els.alerts.innerHTML = "";

  const label = document.createElement("label");
  label.className = "area-picker__label";
  label.htmlFor = "area-alert-select";
  label.textContent = t("alertLabel");
  const select = document.createElement("select");
  select.id = "area-alert-select";
  select.className = "area-picker__select";
  const off = document.createElement("option");
  off.value = "";
  off.textContent = t("alertOff");
  select.appendChild(off);
  for (const a of ALERT_AREAS) {
    const opt = document.createElement("option");
    opt.value = a.en;
    opt.textContent = areaName(a);
    select.appendChild(opt);
  }
  select.value = alertArea ? alertArea.en : "";
  select.addEventListener("change", () => {
    alertArea = ALERT_AREAS.find((a) => a.en === select.value) || null;
    recentAlerts.length = 0;
    try {
      if (alertArea) localStorage.setItem(ALERT_STORAGE_KEY, alertArea.en);
      else localStorage.removeItem(ALERT_STORAGE_KEY);
    } catch { /* storage unavailable */ }
    renderAlerts();
  });
  els.alerts.append(label, select);

  if (!alertArea) return;
  const status = document.createElement("p");
  status.className = "area-alerts__status";
  const already = [...situationsById.values()].filter((s) => situationInArea(s, alertArea)).length;
  status.textContent = already ? t("alertAlready", already, areaName(alertArea)) : t("alertWatching", areaName(alertArea));
  els.alerts.appendChild(status);

  if ("Notification" in window && Notification.permission === "default") {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "text-button";
    btn.textContent = t("alertEnableBrowser");
    btn.addEventListener("click", () => Notification.requestPermission().then(renderAlerts));
    els.alerts.appendChild(btn);
  }

  for (const [i, { situation, kind }] of recentAlerts.entries()) {
    const card = document.createElement("div");
    card.className = `area-alert area-alert--${situation.alert_level}`;
    card.setAttribute("role", "alert");
    const title = document.createElement("p");
    title.className = "area-alert__title";
    title.textContent = kind === "new" ? t("alertNew", areaName(alertArea)) : t("alertWorse", areaName(alertArea));
    const body = document.createElement("p");
    body.className = "area-alert__body";
    body.textContent = lang === "hi" ? situation.headline_hi : situation.headline_en;
    body.lang = lang === "hi" ? "hi" : null;
    const dismiss = document.createElement("button");
    dismiss.type = "button";
    dismiss.className = "text-button area-alert__dismiss";
    dismiss.textContent = t("alertDismiss");
    dismiss.addEventListener("click", () => { recentAlerts.splice(i, 1); renderAlerts(); });
    card.append(title, body, dismiss);
    els.alerts.appendChild(card);
  }
}

// ---------------------------------------------------------- area picker ---

function renderAreaPicker() {
  if (!els.areaPicker) return;
  const zones = [...new Set([...situationsById.values()].filter((s) => !s.is_decoy).map((s) => s.zone.label_en))];
  els.areaPicker.innerHTML = "";
  if (zones.length === 0) { els.areaPicker.hidden = true; return; }
  els.areaPicker.hidden = false;

  const label = document.createElement("label");
  label.className = "area-picker__label";
  label.textContent = t("areaPickerLabel");
  label.htmlFor = "area-picker-select";

  const select = document.createElement("select");
  select.id = "area-picker-select";
  select.className = "area-picker__select";
  const allOpt = document.createElement("option");
  allOpt.value = "";
  allOpt.textContent = t("allAreas");
  select.appendChild(allOpt);
  for (const z of zones) {
    const opt = document.createElement("option");
    opt.value = z;
    opt.textContent = z;
    select.appendChild(opt);
  }
  select.value = selectedZoneLabel || "";
  select.addEventListener("change", () => {
    selectedZoneLabel = select.value || null;
    renderSituationList();
    renderSummary();
  });

  els.areaPicker.appendChild(label);
  els.areaPicker.appendChild(select);
}

function visibleSituations() {
  const active = [...situationsById.values()]
    .filter((s) => !s.is_decoy)
    .sort((a, b) => (b.pulse_score || 0) - (a.pulse_score || 0));
  if (!selectedZoneLabel) return active;
  return active.filter((s) => s.zone.label_en === selectedZoneLabel);
}

// -------------------------------------------------------------- summary ---

function renderSummary() {
  if (!els.summary) return;
  els.summary.innerHTML = "";
  const top = visibleSituations()[0];
  if (!top) {
    const p = document.createElement("p");
    p.className = "situation-summary__empty";
    p.textContent = t("noSituationSelected");
    p.lang = lang === "hi" ? "hi" : null;
    els.summary.appendChild(p);
    return;
  }

  const headline = document.createElement("p");
  headline.className = "situation-summary__headline";
  headline.textContent = lang === "hi" ? top.headline_hi : top.headline_en;
  headline.lang = lang === "hi" ? "hi" : null;
  els.summary.appendChild(headline);

  const rootCategory = top.chain && top.chain[0] && top.chain[0].category;
  const action = ACTION_BY_CATEGORY[rootCategory];
  const actionHeading = document.createElement("p");
  actionHeading.className = "situation-summary__action-heading";
  actionHeading.textContent = t("suggestedActionHeading");
  const actionText = document.createElement("p");
  actionText.className = "situation-summary__action";
  actionText.textContent = action ? (lang === "hi" ? action.hi : action.en) : t("noActionNeeded");
  actionText.lang = lang === "hi" ? "hi" : null;
  els.summary.appendChild(actionHeading);
  els.summary.appendChild(actionText);

  const lastStep = top.chain && top.chain[top.chain.length - 1];
  if (lastStep && currentSimTimeUtc) {
    const updated = document.createElement("p");
    updated.className = "situation-summary__updated";
    updated.textContent = t("situationUpdated", timeAgo(lastStep.t_utc, currentSimTimeUtc));
    updated.lang = lang === "hi" ? "hi" : null;
    els.summary.appendChild(updated);
  }

  els.summary.appendChild(buildWhyToggle(top));
}

function buildWhyToggle(situation) {
  const wrap = document.createElement("div");
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "text-button situation-summary__why";
  btn.textContent = t("why");
  const timeline = document.createElement("div");
  timeline.className = "situation-timeline situation-timeline--condensed";
  timeline.hidden = true;
  btn.addEventListener("click", async () => {
    timeline.hidden = !timeline.hidden;
    btn.textContent = timeline.hidden ? t("why") : t("hide");
    if (timeline.hidden) return;
    // framing_text only ships on GET /situations/{id}'s response, not on the
    // /state or WS summary this card was built from — fetch once, cache.
    let full = detailCache.get(situation.situation_id);
    if (!full) {
      timeline.innerHTML = `<p class="situation-timeline__loading">…</p>`;
      try {
        full = await fetchSituation(situation.situation_id);
        detailCache.set(situation.situation_id, full);
      } catch {
        full = situation; // fall back to the summary shape we already have
      }
    }
    if (!timeline.hidden) renderCondensedTimeline(timeline, full);
  });
  wrap.appendChild(btn);
  wrap.appendChild(timeline);
  return wrap;
}

function renderCondensedTimeline(container, situation) {
  container.innerHTML = "";
  const ol = document.createElement("ol");
  ol.className = "timeline-steps timeline-steps--condensed";
  for (const step of situation.chain || []) {
    const li = document.createElement("li");
    li.className = "timeline-step";
    const dot = document.createElement("span");
    dot.className = "timeline-step__dot";
    dot.textContent = String(step.step);
    const body = document.createElement("p");
    body.className = "timeline-step__en";
    body.textContent = lang === "hi" ? step.text_hi : step.text_en;
    body.lang = lang === "hi" ? "hi" : null;
    li.appendChild(dot);
    li.appendChild(body);
    ol.appendChild(li);
  }
  container.appendChild(ol);

  const confLine = document.createElement("p");
  confLine.className = "confidence-line";
  confLine.textContent = `${t("howSure")}: ${confidenceWord(situation.confidence_level)}`;
  container.appendChild(confLine);

  // Live field is `framing_text`/`framing_text_hi` (no `_en` suffix) —
  // confirmed against GET /situations/{id} 2026-09-25. confidence_reason
  // is the fallback for whichever shape is on hand.
  const framing = lang === "hi"
    ? (situation.framing_text_hi || situation.confidence_reason_hi)
    : (situation.framing_text || situation.framing_text_en || situation.confidence_reason_en);
  if (framing) {
    const p = document.createElement("p");
    p.className = "confidence-reason";
    p.textContent = framing;
    p.lang = lang === "hi" ? "hi" : null;
    container.appendChild(p);
  }

  if (situation.partial && situation.partial_of != null) {
    const known = situation.members_known ?? (situation.member_event_ids || []).length;
    const p = document.createElement("p");
    p.className = "confidence-reason";
    p.textContent = lang === "hi"
      ? `अब तक अनुमानित ${situation.partial_of} में से ${known} संबंधित रिपोर्ट के आधार पर।`
      : `Based on ${known} of an estimated ${situation.partial_of} related reports so far.`;
    p.lang = lang === "hi" ? "hi" : null;
    container.appendChild(p);
  }

  const predicted = predictedNextCondensed(situation.predicted_next);
  if (predicted) {
    const p = document.createElement("p");
    p.className = "predicted-next";
    p.textContent = predicted;
    container.appendChild(p);
  }
}

// predicted_next's live shape is a list of {category, plausible_because_en,
// plausible_because_hi}; an older {category, typical_lag_range_sec,
// based_on} single-object shape is also handled — see situations.js's
// predictedNextText for the full explanation of why both exist.
function predictedNextCondensed(pn) {
  if (!pn) return null;
  const list = Array.isArray(pn) ? pn : [pn];
  if (list.length === 0) return null;
  const parts = list.map((p) => {
    const cat = (CATEGORY_LABELS[p.category]?.[lang] || p.category);
    if (lang === "hi") {
      return p.plausible_because_hi ? `${cat} — ${p.plausible_because_hi}` : cat;
    }
    if (p.plausible_because_en) return `${cat.toLowerCase()} — ${p.plausible_because_en}`;
    const [lo, hiSec] = p.typical_lag_range_sec || [];
    const label = cat.toLowerCase();
    if (lo != null && hiSec != null) {
      const lagText = lo === 0 ? `within about ${formatDuration(hiSec)}` : `${formatDuration(lo)} to ${formatDuration(hiSec)}`;
      return `${label} often follows, usually ${lagText}`;
    }
    return `${label} often follows`;
  });
  return `${t("watchForNext")}: ${parts.join("; ")}`;
}

// ---------------------------------------------------------- situation list

function renderSituationList() {
  if (!els.list) return;
  els.list.innerHTML = "";
  const heading = document.createElement("h2");
  heading.className = "situation-list__heading";
  heading.textContent = t("situationsHeading");
  heading.lang = lang === "hi" ? "hi" : null;
  els.list.appendChild(heading);

  const items = visibleSituations();
  if (items.length === 0) {
    const p = document.createElement("p");
    p.className = "situation-rail__empty";
    p.textContent = lang === "hi" ? STRINGS.hi.nothingUnusual : STRINGS.en.nothingUnusual;
    p.lang = lang === "hi" ? "hi" : null;
    els.list.appendChild(p);
    return;
  }

  for (const sit of items) els.list.appendChild(buildResidentCard(sit));
}

function buildResidentCard(situation) {
  const card = document.createElement("article");
  card.className = "situation-card";
  card.dataset.level = situation.alert_level;

  const top = document.createElement("div");
  top.className = "situation-card__row";
  const chip = document.createElement("span");
  chip.className = `alert-chip alert-chip--${situation.alert_level}`;
  if (situation.alert_level === "red") chip.classList.add("hatch-red");
  const swatch = document.createElement("span");
  swatch.className = "alert-chip__swatch";
  const chipLabel = document.createElement("span");
  chipLabel.className = "alert-chip__label";
  chipLabel.textContent = (ALERT_WORDS[situation.alert_level] || ALERT_WORDS.green)[lang];
  chip.appendChild(swatch);
  chip.appendChild(chipLabel);
  top.appendChild(chip);
  const zone = document.createElement("span");
  zone.className = "situation-card__zone";
  zone.textContent = lang === "hi" ? situation.zone.label_hi : situation.zone.label_en;
  top.appendChild(zone);
  card.appendChild(top);

  const headline = document.createElement("h3");
  headline.className = "situation-card__headline-en";
  headline.textContent = lang === "hi" ? situation.headline_hi : situation.headline_en;
  headline.lang = lang === "hi" ? "hi" : null;
  card.appendChild(headline);

  card.addEventListener("click", () => { selectedZoneLabel = situation.zone.label_en; render(); window.scrollTo({ top: 0, behavior: "auto" }); });

  return card;
}

// ------------------------------------------------------- probably unrelated

function renderUnrelated() {
  if (!els.unrelated) return;
  const decoys = [...situationsById.values()].filter((s) => s.is_decoy);
  els.unrelated.innerHTML = "";
  if (decoys.length === 0) { els.unrelated.hidden = true; return; }
  els.unrelated.hidden = false;

  const heading = document.createElement("h2");
  heading.className = "unrelated-section__heading";
  heading.textContent = t("unrelatedHeading");
  const intro = document.createElement("p");
  intro.className = "unrelated-section__intro";
  intro.textContent = t("unrelatedIntro");
  intro.lang = lang === "hi" ? "hi" : null;
  els.unrelated.appendChild(heading);
  els.unrelated.appendChild(intro);

  const list = document.createElement("div");
  list.className = "unrelated-list";
  for (const sit of decoys) {
    const card = document.createElement("article");
    card.className = "situation-card situation-card--decoy";
    card.dataset.level = sit.alert_level;
    const h = document.createElement("h3");
    h.className = "situation-card__headline-en";
    h.textContent = lang === "hi" ? sit.headline_hi : sit.headline_en;
    h.lang = lang === "hi" ? "hi" : null;
    card.appendChild(h);
    list.appendChild(card);
  }
  els.unrelated.appendChild(list);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
