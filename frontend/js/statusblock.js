// js/statusblock.js — DESIGN.md component #1, the single status block.
//
// Extracted out of js/city.js so the resident view (js/resident.js) can
// import it without pulling in city.js's module body, which bootstraps a
// MapLibre map against #map on load — resident.html has no #map and does
// not load the MapLibre/h3-js scripts, so importing city.js directly would
// throw (`maplibregl is not defined`) during module evaluation. This file
// has no DOM/library dependency beyond the standard DOM APIs, so both city
// view and resident view can share the exact same rendering.

const ALERT_LEVELS = ["green", "yellow", "orange", "red"];

// Status words per DESIGN.md §1. Hindi twins are not specified verbatim
// anywhere in DESIGN.md/CONTRACT.md for these four words — authored here in
// the same plain, sentence-case register as the rest of the vocabulary
// table (DESIGN.md "Plain-language vocabulary"). Flagged in the handoff
// report; swap freely if the team has an approved translation.
const STATUS_WORDS = {
  green: { en: "All normal", hi: "सब सामान्य है" },
  yellow: { en: "Be aware", hi: "सतर्क रहें" },
  orange: { en: "Be prepared", hi: "तैयार रहें" },
  red: { en: "Take action", hi: "कार्रवाई करें" },
};

const statusBlockState = new WeakMap(); // element -> { lastUpdate, timer }

/**
 * renderStatusBlock(target, alertLevel, oneLineSummary, opts)
 * DESIGN.md component #1 — the single status block, shared by the city view
 * (js/city.js) and the resident view (js/resident.js).
 *
 *   target          element or CSS selector string.
 *   alertLevel      "green" | "yellow" | "orange" | "red" — rendered as
 *                   given, never recomputed from a score here.
 *   oneLineSummary  { en, hi } — e.g. "3 things happening across 22 areas" /
 *                   "Nothing unusual right now" at green. A plain string is
 *                   also accepted (used as the English line, no Hindi twin).
 *   opts            reserved for future options; none currently used.
 *                   Note: pulse_score is deliberately NOT rendered by this
 *                   component — DESIGN.md places it "at the right edge of
 *                   the Naadi strip" instead (see naadi.js), never inside
 *                   the status block, and never at all on the resident view.
 *
 * Re-invoke on every relevant update (tick / situation change); the "Updated
 * N seconds ago" line then keeps ticking on its own via an internal 1s
 * interval scoped to the target element, without the caller managing a
 * timer. The block itself never animates (DESIGN.md: "it changes value, it
 * does not perform").
 */
export function renderStatusBlock(target, alertLevel, oneLineSummary, opts = {}) {
  const el = typeof target === "string" ? document.querySelector(target) : target;
  if (!el) return;
  const level = ALERT_LEVELS.includes(alertLevel) ? alertLevel : "green";
  const summary = typeof oneLineSummary === "string" ? { en: oneLineSummary, hi: "" } : (oneLineSummary || { en: "", hi: "" });
  const word = STATUS_WORDS[level];

  if (!el.dataset.nnStatusBuilt) {
    el.innerHTML = "";
    el.classList.add("nn-status");
    const bar = document.createElement("div");
    bar.className = "nn-status__bar";
    const body = document.createElement("div");
    body.className = "nn-status__body";
    const wordEl = document.createElement("div");
    wordEl.className = "nn-status__word display";
    const summaryEn = document.createElement("div");
    summaryEn.className = "nn-status__summary body";
    const summaryHi = document.createElement("div");
    summaryHi.className = "nn-status__summary nn-status__summary--hi body";
    summaryHi.lang = "hi";
    const meta = document.createElement("div");
    meta.className = "nn-status__meta";
    const updated = document.createElement("span");
    updated.className = "nn-status__updated label";
    meta.appendChild(updated);
    body.appendChild(wordEl);
    body.appendChild(summaryEn);
    body.appendChild(summaryHi);
    body.appendChild(meta);
    el.appendChild(bar);
    el.appendChild(body);
    el.dataset.nnStatusBuilt = "1";
  }

  for (const lvl of ALERT_LEVELS) el.classList.remove(`nn-status--${lvl}`);
  el.classList.add(`nn-status--${level}`);
  if (level === "red") el.querySelector(".nn-status__bar").classList.add("hatch-red");
  else el.querySelector(".nn-status__bar").classList.remove("hatch-red");

  el.querySelector(".nn-status__word").textContent = word.en;
  el.querySelector(".nn-status__summary:not(.nn-status__summary--hi)").textContent = summary.en;
  const hiEl = el.querySelector(".nn-status__summary--hi");
  hiEl.textContent = summary.hi || "";
  hiEl.style.display = summary.hi ? "" : "none";

  let state = statusBlockState.get(el);
  if (!state) {
    state = { lastUpdate: Date.now(), timer: null };
    statusBlockState.set(el, state);
  }
  state.lastUpdate = Date.now();
  const updatedEl = el.querySelector(".nn-status__updated");
  function tickUpdatedLabel() {
    const secs = Math.max(0, Math.round((Date.now() - state.lastUpdate) / 1000));
    const en = secs < 1 ? "Updated just now" : `Updated ${secs} second${secs === 1 ? "" : "s"} ago`;
    updatedEl.textContent = en;
  }
  tickUpdatedLabel();
  if (!state.timer) {
    state.timer = setInterval(tickUpdatedLabel, 1000);
  }
}
