# DESIGN.md — Nagar Naadi

The authority for every UI phase. If you are building a screen and this file and your
instinct disagree, this file wins. Data shapes come from CONTRACT.md; this file never
redefines them.

Sections: [Intent](#design-intent) · [Color](#color-tokens) · [Type](#typography) ·
[Words](#plain-language-vocabulary) · [Components](#component-specs) ·
[Motion](#motion) · [Anti-tells](#anti-tells)

---

## Design intent

Nagar Naadi looks like civic infrastructure, not like a product launch. It borrows the
one visual language Indian residents already trust for risk: the IMD four-level alert —
**green = all normal, yellow = be aware, orange = be prepared, red = take action** — and
spends the rest of the screen being quiet. Light theme throughout, because this gets
projected in a bright room. Neutral paper background, dark ink, thin rules, generous
whitespace; almost nothing competes for attention. Exactly one element is allowed to be
bold: the **Naadi strip**, an ECG-style pulse trace across the top that shows the city's
event rate in real time and spikes when something happens. That contrast — a calm sheet
of paper with one living line across it — is the whole identity. Status is **never
communicated by color alone**: every colored thing carries its word next to it, and red
areas additionally get a diagonal hatch, so the screen still reads correctly in
grayscale, on a washed-out projector, and to a colorblind viewer.

---

## Color tokens

Implemented in `frontend/css/tokens.css` as custom properties on `:root`. Use the token,
never the hex. Names are Hindi so nobody confuses `--neel` (interactive blue) with a
status color.

| Token | Hex | Role |
|---|---|---|
| `--chuna` | `#F2F4F1` | Page background. Warm off-white, "limewash". |
| `--syahi` | `#1F2A30` | Ink: body text, headings, ECG trace, hex outlines. |
| `--dhool` | `#6B7780` | Secondary text: labels, timestamps, units, helper copy. |
| `--rekha` | `#D5DBD8` | Borders, dividers, the ECG grid, empty-state fills. |
| `--neel` | `#2B3A8C` | Interactive and selected only — links, focus rings, the selected hex, active tab. **Never a status.** |
| `--green` | `#2F8F5B` | Alert level: all normal. |
| `--yellow` | `#E3B505` | Alert level: be aware. |
| `--orange` | `#E0701F` | Alert level: be prepared. |
| `--red` | `#C62D2D` | Alert level: take action. |

Rules:

- The four status colors appear **only** on status. Never as a chart accent, never as a
  category color, never decoratively. If you need to tell eleven categories apart, use
  shape, position and label — not hue.
- `--neel` never means "good" or "bad". It means "you can click this" or "this is
  selected right now".
- `--yellow` at `#E3B505` fails contrast on `--chuna` for text. Yellow is a **fill and a
  rule**, never a text color. The word next to it is always `--syahi`.
- Alpha variants are derived in `tokens.css` (`--red-fill` etc. at 12% over the
  background) — do not invent new ones inline.

### the red hatch

Any map cell, card edge or chip at `red` also gets a 45° hatch, so red is distinguishable
without seeing red:

```css
background-image: repeating-linear-gradient(
  45deg,
  transparent 0 5px,
  color-mix(in srgb, var(--red) 28%, transparent) 5px 7px
);
```

On the MapLibre layer the same effect uses a `fill-pattern` with a 8×8 hatch sprite.
Orange, yellow and green have no pattern — the hatch is a red-only signal.

---

## Typography

**Anek Devanagari**, variable, served locally from `/frontend/assets/fonts/`. It was
designed for Devanagari and Latin to sit together at the same optical size, which is why
our bilingual labels do not look bolted together. No webfont CDN — it must work offline
on a hotel wifi.

Axes used: `wght` 100–800, `wdth` 75–125. We use the width axis as a real signal:

- **Status words are wide and heavy** (`wdth 110–115`, `wght 700`) so the one thing that
  matters is the one thing that is physically widest on screen.
- **Data labels are narrow** (`wdth 85`) so dense readouts stay compact and visibly
  secondary.

Numbers that change on every tick — counts, delays, timers, PM2.5 — use
`font-variant-numeric: tabular-nums` so they do not jitter.

### type scale

| Role | Size / line-height | `wght` | `wdth` | Use |
|---|---|---|---|---|
| `display` | 44px / 1.10 | 700 | 112 | The single status word in the status block. One per screen. |
| `heading` | 24px / 1.30 | 600 | 100 | Situation headline, section titles. |
| `body` | 16px / 1.55 | 400 | 100 | Sentences, timeline steps, explanations. |
| `label` | 13px / 1.30 | 500 | 85 | Field labels, feed names, chip text, table headers. |
| `data` | 15px / 1.20 | 500 | 90 | Changing numbers. Always tabular. |

Devanagari needs vertical room for matras: **any line rendering Hindi gets +0.15 added to
its line-height** (`body` Hindi is 1.70, not 1.55). Set this with a `[lang="hi"]` rule,
not by hand per component.

Measure caps at **68 characters** for body text. Sentence case everywhere, including
headings and buttons.

---

## Plain-language vocabulary

The system's job is to be understood in ten seconds. Internal words never reach the
screen. This table is binding on every string, `_en` and `_hi`.

| Never write | Always write | Hindi |
|---|---|---|
| anomaly | Unusual | असामान्य |
| situation | What's happening | क्या हो रहा है |
| correlation / correlated | Possibly linked | संभवतः जुड़ा हुआ |
| stale feed / feed timeout | No update for N min | N मिनट से कोई अपडेट नहीं |
| hex / H3 cell / tile | Area | क्षेत्र |
| normalized / normalization | Cleaned data | साफ़ किया गया डेटा |
| raw feed | Original data | मूल डेटा |
| severity score | How bad | कितना गंभीर |
| confidence | How sure we are | हम कितने निश्चित हैं |
| decoy / false positive | Probably unrelated | शायद असंबंधित |
| lift | Happens together more than usual | सामान्य से ज़्यादा साथ होता है |
| ground truth | What actually happened | असल में क्या हुआ |
| ingest / pipeline | Reading the feeds | फ़ीड पढ़ना |

Further rules:

- **Sentence case everywhere.** "Be prepared", not "Be Prepared" or "BE PREPARED".
- **Banned words:** AI-powered, insights, smart, intelligent, leverage, seamless,
  real-time™-flavoured marketing, "harnessing", "revolutionise". We say what the thing
  does. "Three feeds reported this within 20 minutes" beats any adjective.
- **Buttons name their exact action.** "Stop transit feed", not "Submit". "Show the
  original records", not "View". "Run at 8× speed", not "Speed". A button label should
  still make sense read aloud with no surrounding context.
- Numbers get units and a reference point. "14 minutes late" not "840". "PM2.5 182 —
  about 3× a normal evening here" not "182 µg/m³".
- Never claim certainty we do not have. "Possibly linked" is the strongest phrase
  available for a correlation. Causal language is reserved for the chain, and even there
  it is temporal: "17 minutes later", not "which caused".
- Every resident-visible string ships with its Hindi twin. A missing `_hi` is a bug.

---

## Component specs

Enough detail to build without guessing. Spacing is on a 4px grid; the values below are
exact, not suggestions.

### 1. Status block

The top-left anchor of the city view and the whole of the resident view's first screen.
Answers "is my city okay right now" before anything else loads.

- Container: no card, no border, no shadow. It sits directly on `--chuna`.
- A **6px vertical bar** in the status color runs the full height of the block on its
  left, with 16px of gap to the text. At `red` the bar carries the hatch.
- The status word uses the `display` scale in `--syahi` — **not** in the status color.
  Color lives in the bar; the word stays ink so it is always readable.
  Text is the level's phrase: "All normal" / "Be aware" / "Be prepared" / "Take action".
- Below it, one `body` line of context: "3 things happening across 22 areas" and, at
  green, "Nothing unusual right now".
- Below that, one `label` line: "Updated 14 seconds ago" — this is the live proof the
  system is breathing, so it must actually tick.
- Hindi twin sits under the English at `body`, in `--dhool`.
- **Data source:** the `city` block from `GET /state`, kept live by `city_alert_level`
  on each `tick`. Render `alert_level` as given; never re-derive it from `pulse_score`
  (CONTRACT.md §F explains the one case where they legitimately disagree).
- The **resident view shows the word only** — no number. A 0–100 score is a thing to
  argue with, not a thing to act on, and "fake precision" is on the anti-tells list.
  The **city view** may show `pulse_score` once, at `data` scale in `--dhool`, at the
  right edge of the Naadi strip. Nowhere else, and never larger than the word.
- Never animate this block. It changes value, it does not perform.

### 2. Alert chip

The inline status marker used on cards, map popups and list rows.

- Height 24px, horizontal padding 8px, `border-radius: 3px` — nearly square, not a pill.
- Composition, always all three parts: an 8px square swatch in the status color, a 6px
  gap, then the level word at `label` scale in `--syahi`.
- Background is the status color at 12% over `--chuna`; the border is 1px of the status
  color at 45%.
- At `red`, the chip background carries the hatch.
- There is no icon-only variant and no color-only variant. A chip without its word is
  not a chip.

### 3. Situation card

One per situation. The main unit of the city view's right rail and the resident view's
list.

- Card: 1px `--rekha` border, `border-radius: 4px`, background `--chuna`, **no shadow**.
  Padding 16px. Cards are stacked with a 12px gap and are deliberately different heights —
  they are not a grid.
- The **left edge is a 4px status bar** in the level color, full card height, flush with
  the border. Hatched at red.
- Layout, top to bottom:
  1. Row: alert chip (left) · zone label at `label` in `--dhool` (right, truncates).
  2. `headline_en` at `heading`, max two lines.
  3. `headline_hi` at `body` in `--dhool`, max two lines.
  4. Row of `label`-scale facts separated by a 1px `--rekha` vertical rule:
     "3 reports" · "2 feeds" · "started 24 minutes ago". Numbers tabular.
  5. A text button, "Why do we think this?", in `--neel` with a 1px underline. It
     expands the timeline (item 4) in place — no modal, no new page.
- Selected state: the border becomes 1px `--neel` and a 2px `--neel` inset ring appears.
  The status bar does not change. Selection is blue; status is status.
- A decoy card never appears in this list — see item 7.

### 4. "Why do we think this?" numbered timeline

The evidence view. This is the component that earns trust, so it is the most literal
thing on the screen.

- Renders `chain[]` from CONTRACT.md §F, in order, plus the `evidence` block below it.
- Each step is a row: a **numbered dot** on a vertical rail at the left, then the text.
  - Dot: 22px circle, 1.5px `--syahi` border, `--chuna` fill, the step number centred at
    `data` scale in `--syahi`. Not colored by status — the numbers are the argument, not
    the alarm.
  - Rail: 1.5px `--rekha` vertical line connecting the dots, behind them.
- Row text: `text_en` at `body`, `text_hi` at `body` in `--dhool` below it. Each step's
  text already carries its time gap in words ("17 minutes later") — the component does
  not compute or re-render times.
- To the right of each row, at `label` in `--dhool`: the category's English label and the
  area name. This is where a reader checks that three different feeds really are involved.
- Below the steps, an evidence footer of three plain lines, each one sentence:
  - **Where** — "All three reports came from adjacent areas" (from `evidence.spatial`)
  - **When** — "17 minutes, then 6 minutes apart" (from `evidence.temporal_gaps`)
  - **How unusual** — "These normally appear together about once every 8 hours here"
    (from `evidence.lift`)
- Then one line of confidence: the `confidence_level` word ("How sure we are: high") and
  `confidence_reason_en` beneath it. Never show the numeric score.

### 5. Naadi strip

The one bold element. An ECG-style trace of city event rate, full width, above everything.

- Height 72px, full bleed, background `--chuna`, a 1px `--rekha` rule beneath it.
- Grid: `--rekha` at 1px, 12px squares, like ECG paper. Grid is drawn, not implied.
- Trace: 2px `--syahi` polyline, no fill underneath, no gradient, no glow. It is a
  drawn line, not a chart.
- Horizontal axis is real time, about 5 minutes of `tick` messages, scrolling right to
  left. Vertical axis is events per tick.
- When a situation is created, a **vertical tick mark** in that situation's status color
  is planted at that x position and stays as the trace scrolls past, with the time in
  `label` beneath it. This is the only place a status color appears on the strip.
- Right edge carries the live value at `data` scale, tabular: "6 events/min".
- It must keep moving while paused, flat — a flat live line reads as "system is up,
  nothing happening". A frozen strip reads as "broken".
- Canvas or inline SVG, drawn by hand. Not a chart library.

### 6. Feed-health row

One row per feed in CONTRACT.md §D, in a plain list — not a table with zebra stripes.

- Height 44px, separated by 1px `--rekha` rules. No card, no border radius.
- Left: feed display name at `body` ("Civic complaints", "City buses" — the friendly
  name, not `civic_complaints`).
- Middle: state as a word plus a mark, never color alone:
  - `live` — a filled 8px `--green` square and the word "Live"
  - `stale` — a hollow 8px `--dhool` square and "No update for 14 min"
  - `killed` — an 8px `--syahi` square with a diagonal strike and "Stopped"
  - `error` — an 8px `--red` hatched square and "Not readable"
- Right: `label`-scale counts, tabular — "418 records · 11 dropped".
- Far right: the control button, naming its exact action — "Stop transit feed" or
  "Start transit feed". Text button, `--neel`, 1px underline, no icon.
- A feed going stale must be noticeable without animation: the row's text shifts to
  `--dhool` and the state word changes. No pulsing, no shake.

### 7. "Probably unrelated" section

Where situations with `is_decoy: true` go. Showing our own false positives, labelled, is
the most credible thing the demo does — so this section is always visible, never
collapsed by default when it has contents.

- Sits below the situation list, separated by a 1px `--rekha` rule and 32px of space.
- Heading at `label` in `--dhool`: "Probably unrelated" / "शायद असंबंधित".
- One line of `body` in `--dhool` explaining the section once: "These showed up together
  but we don't think they're connected."
- Its cards are the situation card with three changes: no status bar on the left edge,
  a 1px dashed `--rekha` border, and the headline in `--dhool` rather than `--syahi`.
  They still carry an alert chip (capped at yellow per CONTRACT.md §F).
- Each decoy card shows one extra `body` line: why we think it is coincidence — the
  human-readable form of `evidence.lift` when lift is near 1.
- Empty state: hide the section entirely. Do not show "No unrelated items".

---

## Motion

**One orchestrated motion in the entire product.** When a situation is created
(`{"type":"situation","action":"created"}`):

1. On the map, the line between its numbered event dots draws in, step 1 → 2 → 3, over
   **0.8s total** with `cubic-bezier(0.22, 1, 0.36, 1)`, stroke `--syahi` at 2px.
2. Simultaneously, its card slides in from the right rail edge over **0.8s**, translating
   16px with opacity 0 → 1.
3. The Naadi strip plants its tick mark at the same moment. No easing on the tick — it
   just appears.

Nothing else moves. No hover transitions on cards, rows or chips. No fades on route
changes. No skeleton shimmer — an empty state is a sentence, not a shimmer. Numbers
change by changing, not by counting up.

```css
@media (prefers-reduced-motion: reduce) {
  /* The line appears drawn; the card appears in place. No transition, no transform. */
}
```

Under `prefers-reduced-motion: reduce`, the line renders complete and the card renders in
final position immediately. The information is identical — only the 0.8s is removed.

---

## Anti-tells

Things that would make this look like every other hackathon dashboard. None of these
appear anywhere in the product.

- **Glassmorphism** — no `backdrop-filter`, no frosted translucent panels.
- **Gradient washes** — no gradient backgrounds, no gradient text, no gradient buttons.
  The only gradient in the codebase is the red hatch, which is a pattern.
- **A grid of identical rounded soft-shadow cards.** Cards are rectangular-ish (4px),
  bordered not shadowed, and deliberately different heights in a single column.
- **ALL-CAPS eyebrow labels** above headings. Sentence case, always.
- **"→" on buttons.** Buttons contain words. No arrows, no chevrons as decoration.
- **Emoji as icons.** No 🚨, no ⚡, no 🌧️. Anywhere. Ever.
- **A chatbot bubble** in the corner. There is no assistant in this product.
- **Default Chart.js bars** — or default anything. The Naadi strip is hand-drawn; any
  other quantity is a number with a unit, not a chart.
- **Hover animation on everything.** Hover may change color or underline. It may not
  scale, lift, shadow, rotate or transition transforms.
- **Dark mode as the demo default.** Light theme, projector-safe. The `data-theme` hook
  exists in `tokens.css` but light is what ships.
- **Fake precision** — no "97.3% confidence". We say "high", and we say why.
- **Hiding the seams** — the data room showing raw, ugly, unparsed records is a feature.
  Never clean up a raw record for display.
