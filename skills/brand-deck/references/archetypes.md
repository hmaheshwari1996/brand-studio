# Deck IR — archetype reference

This is the file to read while authoring. Everything here is derived from
`grammar/deck-grammar.json` (geometry, archetype fields, capacities, structural rules) and
`brands/<id>/brand.json` (type scale, palette, voice). Numbers shown are for the **Channelplay**
type scale; another brand shifts the measured fits, the declared capacities stay the same.

---

## 1. The envelope

```json
{
  "brand": "channelplay",
  "meta": {
    "title": "Retail execution, measured",
    "client": "Acme Consumer Products",
    "date": "2026-07-30",
    "author": "Channelplay",
    "confidentiality": "Confidential"
  },
  "slides": [ /* one object per slide, in order */ ],
  "notes": {
    "1": "Open on the problem, not on us.",
    "5": "These are our own inherited-programme numbers."
  }
}
```

| Field | Type | Notes |
|---|---|---|
| `brand` | str | The brand id confirmed by **brand-kit**. Must match a directory under `brands/`. |
| `meta.title` | str | Deck title. Usually the same string as the cover title. |
| `meta.client` | str | Who the deck is for. Goes into document properties. |
| `meta.date` | str | `YYYY-MM-DD`. |
| `meta.author` | str | Usually the brand's display name. |
| `meta.confidentiality` | str | e.g. `Confidential`, `Internal`, `Public`. |
| `slides` | list | Slide objects, in presentation order. |
| `notes` | obj | Keys are **1-based slide indices as strings**. Values are the speaker notes. |

`notes` is optional but almost always wrong to omit. Detail that a presenter says out loud belongs
there, not on the slide — it is the pressure-release valve for every capacity in this document.

---

## 2. Fields every content slide takes

Fourteen of the eighteen archetypes are **chrome** archetypes: they carry the standing furniture —
logo top-left, header rule, eyebrow top-right, slide title, optional deck line, footer band, page
number.

| Field | Applies to | Role / size | Declared | Fits | Author to |
|---|---|---|---|---|---|
| `eyebrow` | every chrome archetype | `eyebrow` 10.5pt Medium, upper, right-aligned | — | ~46 | **≤ 28** |
| `title` | every chrome archetype | `title` 32pt SemiBold, one line | 70 (`maxTitleChars`) | ~42 | **≤ 42** |
| `deck` | optional, chrome archetypes | `subtitle` 18pt Regular, up to 2 lines | — | ~169 | **≤ 160** |

**`eyebrow`** is the chapter label, repeated on every slide of that chapter. Write it in normal case
in the IR — the builder applies the brand's `type.deckScalePt.eyebrow.case: "upper"`. It is the one
field exempt from the sentence-case check. `grammar.deckRules.requireEyebrowOnContent` is true, so
give every chrome slide an eyebrow. Two or three words: `Staffing`, `The problem`, `Measurement`.

**`title`** is a claim, not a label. "Compliance improves once evidence is required" beats
"Compliance". It is the sentence the audience remembers if they remember one thing. Note the gap
between the declared 70 and the ~42 that actually fits a single 32pt line — a 60-character title
clears `CONTENT.LONG_TITLE` and then trips `LAYOUT.OVERFLOW`, which is an **error**. Write to 42.

**`deck`** is the qualifier under the title: the scope, the source, the period. It is optional and
usually the right place for the clause you had to cut from the title.

> **`deck` and `intro` are mutually exclusive.** The chrome deck line occupies y 1.78–2.56in.
> Archetypes that declare an `intro` field (`icon-rows`, `columns`, `table`, `stats`) put their intro
> in the same band. Setting both collides and raises `LAYOUT.OVERLAP`. Where an archetype has
> `intro`, use `intro`. Where it does not, use `deck`.

> Setting `deck` also pushes content down: `chrome.contentTopWithDeck` is 2.70in against
> `contentTop` 2.10in. You lose 0.60in of content height on that slide, so drop roughly 12% off the
> body capacity and re-validate. On already-dense archetypes (`icon-grid`, `steps`, `table`,
> `photo-trio`) prefer no deck line at all.

The four non-chrome archetypes — `cover`, `section-break`, `full-bleed`, `closing` — ignore
`eyebrow` and `deck` and use their own title fields, documented per archetype below.

---

## 3. Shared sub-objects

### `visual` — used by `text-visual`, `visual-text`, and optionally `icon-rows`

Three mutually exclusive forms — pick one:

```jsonc
"visual": { "kind": "image",  "src": "/abs/path/store-audit.jpg" }   // or
"visual": { "kind": "chart",  "chart": { /* the chart spec below */ } }   // or
"visual": { "kind": "placeholder" }
```

| `kind` | Extra fields | Use |
|---|---|---|
| `image` | `src` — absolute path, or a path relative to the IR file's directory | A photograph, a screenshot, a diagram you already have as a raster. |
| `chart` | `chart` — the chart spec below | Native, editable PowerPoint chart. |
| `placeholder` | — | **Drafting only.** Reserves the region so you can review structure before the asset exists. |

A `placeholder` that ships is a defect. Before delivery, every placeholder is either filled or its
slide is cut. Do not label a placeholder with text from `brand.voice.forbiddenPhrases` — that is a
`CONTENT.PLACEHOLDER` **error**, which is the point.

Images: check what you are putting on the slide. Right geography, right retail format, right people.
Give every image alt text through the builder where it supports it, or accept the `A11Y.ALT_TEXT`
info line knowingly.

### `chart` spec

```json
{
  "type": "column",
  "categories": ["Q1", "Q2", "Q3", "Q4"],
  "series": [
    { "name": "Self-reported", "values": [94, 95, 95, 96] },
    { "name": "Audited",       "values": [61, 68, 84, 92] }
  ]
}
```

| `type` | Shape | Use for |
|---|---|---|
| `column` | vertical bars | Change over time, few categories. The default. |
| `bar` | horizontal bars | Ranked comparison, or long category names. |
| `line` | lines | Continuous series, many time points. |
| `pie` | pie | Composition of one whole. One series only, ≤ 5 slices. |
| `doughnut` | ring | Same as pie. Use one or the other across a deck, not both. |

Rules the validator enforces:

- Series colours come from `brand.colorRules.chartSeries`, in order. Channelplay ships six:
  `#0000FF`, `#41E7AB`, `#0194DD`, `#0F0A6C`, `#29AFA7`, `#2F80ED`. **A seventh series has no
  colour** — restructure the chart.
- `brand.colorRules.chartGradientFillForbidden` is true. Flat fills only (`COLOR.CHART_GRADIENT`,
  error).
- Chart label text obeys the type rules like any other text: brand family, ≥ 10.5pt
  (`TYPE.OFF_FAMILY`, `TYPE.BELOW_MIN`, both errors).

Rules only you can enforce: that the axis is not truncated to exaggerate a move, that the chart type
suits the data, and that the title's claim is what the chart actually shows.

### `icon`

`icon-grid` cells, `icon-rows` rows and `steps` steps each take an optional `icon`: a `name` from
`grammar/icons.json` (86 available, tinted from the brand palette at build time).

Frequently useful names:

`badge-tick1`, `blackboard`, `blueprint`, `box-trolley`, `building`, `city`, `classroom`, `compass`,
`connected`, `connections`, `daily-calendar`, `decision-chart`, `flag`, `gears`, `globe`,
`good-inventory`, `grocery-bag`, `home`, `laptop`, `lecturer`, `link`, `management`, `map-with-pin`,
`marker`, `marketing`, `monthly-calendar`, `network`, `network-diagram`, `online-meeting`,
`open-quotation-mark`, `pin`, `play`, `playbook`, `register`, `repeat`, `selfie`, `settings`,
`shuffle1`, `smart-phone`, `store`, `team`, `transfer`, `ui-ux`, `user`, `users`, `wireless`.

List them all with:

```sh
"$PY" -c "import json;print('\n'.join(i['name'] for i in json.load(open('$ROOT/grammar/icons.json'))['icons']))"
```

Pick for meaning, not for the word that happened to appear in the copy. If no icon means anything,
omit the field — a set of arbitrary icons is worse than none.

---

## 4. Structural rules the deck must satisfy

From `grammar.deckRules`. These are checked slide-to-slide, not within a slide, and they are only
authoritative when you pass `--ir` to the validator.

| Rule | Value | Violation |
|---|---|---|
| `mustOpenWith` | `cover` | `STRUCTURE.NO_COVER` |
| `mustCloseWith` | `closing` or `full-bleed` | `STRUCTURE.NO_CLOSING` |
| `minSlides` | 3 | `STRUCTURE.TOO_FEW_SLIDES` (error) |
| `maxConsecutiveSameArchetype` | 2 | `STRUCTURE.REPEATED_ARCHETYPE` (warn) |
| `sectionBreakEveryNSlidesMax` | 8 | `STRUCTURE.MISSING_SECTION_BREAK` (warn) |
| `maxBulletsPerSlide` | 6 | `CONTENT.BULLET_COUNT` (warn) |
| `maxWordsPerBullet` | 18 | `CONTENT.LONG_BULLET` (warn) |
| `maxTitleChars` | 70 | `CONTENT.LONG_TITLE` (warn) |
| `requireEyebrowOnContent` | true | — |
| `requireLogoOnCoverAndClosing` | true | `LOGO.MISSING` (error) |
| `overflowTolerancePct` | 2.0 | `LAYOUT.OVERFLOW` (error) |
| `overlapToleranceIn` | 0.02 | `LAYOUT.OVERLAP` (warn) |

**Paragraphs.** In any `body` / `intro` field, `\n\n` starts a new paragraph. Paragraph count is
what `maxBulletsPerSlide` counts. Word count per paragraph is what `maxWordsPerBullet` counts — but
only when the block looks like a list: either a real bullet glyph is present, or there are ≥ 2
paragraphs and the shortest is ≤ 18 words. Continuous prose of two long paragraphs is not flagged;
prose with a short lead-in followed by long paragraphs **is**. Write prose as prose, lists as lists,
and do not mix the two in one block.

---

## 5. How to read the capacity tables

Each archetype below has a capacity table with four columns:

- **Declared** — `capacity` in `grammar/deck-grammar.json`. The editorial contract.
- **Fits** — what actually fits the box, measured with `brandlib.estimate_text_height()` at the
  Channelplay type scale. This is the same estimator the validator uses for `LAYOUT.OVERFLOW`.
- **Author to** — the smaller of the two, rounded down. Write to this and nothing fires.
- **Role** — the type role, so you know the size and weight you are writing for.

Where **Fits** is well below **Declared**, that region is a trap. They are called out in the notes.

The authority is always the actual validator run. If a table here and the validator disagree, the
validator is right and this file needs updating — say so in your report.

---

# The archetypes

---

## `cover`

**Slide 1 only. Exactly one per deck.** Full-bleed brand panel, logo reversed on dark, a rule, the
deck title, an optional subtitle and an optional meta line.

**Use it for:** the opening slide. That is the whole job.

**Do not use it for:** section openers (that is `section-break`), or a second "part two" cover.
A deck has one cover.

**Chrome:** none. `eyebrow` and `deck` are ignored.

### Fields

| Field | Required | Notes |
|---|---|---|
| `archetype` | yes | `"cover"` |
| `title` | yes | The deck's argument in one phrase. |
| `subtitle` | no | One sentence of scope. |
| `meta` | no | Document type, date, audience. |

### Capacity

| Field | Role | Declared | Fits | Author to |
|---|---|---|---|---|
| `title` | `cover` 54pt SemiBold | 70 | ~26 | **≤ 26** |
| `subtitle` | `subtitle` 18pt | 110 | ~61 | **≤ 61** |
| `meta` | `caption` 10.5pt | 60 | ~156 | **≤ 60** |

> **The trap.** At 54pt the cover title box takes roughly one line — about 26 characters. The
> declared 70 will overflow. "Retail execution, measured" is 26. "How Channelplay delivers
> world-class retail execution programmes" is 57 and will fail.

### Example

```json
{
  "archetype": "cover",
  "title": "Coverage you can audit",
  "subtitle": "A field execution proposal for general trade in the south and west",
  "meta": "Proposal · 30 July 2026 · Confidential"
}
```

---

## `agenda`

Chapter map after the cover. Two columns of up to four numbered items, with an optional full-height
image panel down the left.

**Use it for:** a deck of 5–8 chapters where the audience benefits from seeing the shape up front.

**Do not use it for:** decks under 8 slides (the map is longer than the territory), or as a
disguised bullet list of everything the deck says. Agenda items are chapter names, not claims.

**Chrome:** yes. Give it an `eyebrow` and a `title`.

### Fields

| Field | Required | Notes |
|---|---|---|
| `archetype` | yes | `"agenda"` |
| `eyebrow` | yes | Usually `Contents`. |
| `title` | yes | What the deck covers. |
| `items` | yes | List of strings. Fills column A (4) then column B (4). |

### Capacity

| Field | Role | Declared | Fits | Author to |
|---|---|---|---|---|
| each item | `label` 12pt Medium | 44 | ~36 | **≤ 36** |
| `items` count | — | 8 | 8 | **4–8** |

Fewer than 4 items looks thin against two columns — use `index` instead.

### Example

```json
{
  "archetype": "agenda",
  "eyebrow": "Contents",
  "title": "What this deck covers",
  "items": [
    "Where the programme is losing coverage",
    "How we staff and certify",
    "The evidence layer",
    "Measurement and audit",
    "Commercials",
    "What happens in week one"
  ]
}
```

---

## `index`

Single-column numbered contents with a vertical rule and a large left-hand section word. Quieter and
narrower than `agenda`.

**Use it for:** a short contents list (≤ 6), or a chapter-level index inside a long deck.

**Do not use it for:** more than 6 items — it silently drops the rest. Use `agenda`.

**Chrome:** yes.

### Fields

| Field | Required | Notes |
|---|---|---|
| `archetype` | yes | `"index"` |
| `eyebrow` | yes | |
| `title` | yes | |
| `label` | no | The big left-hand word, right-aligned against the rule. One or two short words. |
| `items` | yes | Up to 6 strings. |

### Capacity

| Field | Role | Declared | Fits | Author to |
|---|---|---|---|---|
| `label` | `section` 44pt SemiBold | — | ~14 | **≤ 14** |
| each item | `label` 12pt Medium | 44 | ~36 | **≤ 36** |
| `items` count | — | 6 | 6 | **≤ 6** |

> `label` is set at 44pt in a 2.4in-wide box. "Contents" fits. "Table of contents" does not.

### Example

```json
{
  "archetype": "index",
  "eyebrow": "Contents",
  "title": "The four commitments",
  "label": "Index",
  "items": [
    "Coverage",
    "Compliance",
    "Certification",
    "Latency"
  ]
}
```

---

## `section-break`

Divides chapters. Full-bleed panel, large section title, an optional kicker sentence, an optional
piece of art on the right.

**Use it for:** every chapter change. `sectionBreakEveryNSlidesMax` is 8 — go longer than that
without one and you get `STRUCTURE.MISSING_SECTION_BREAK`.

**Do not use it for:** a slide that has actual content. A section break carries a chapter name and
a claim, nothing else. Do not use two in a row, and do not open a deck with one — the cover does
that job.

**Chrome:** none. `eyebrow` and `deck` are ignored.

### Fields

| Field | Required | Notes |
|---|---|---|
| `archetype` | yes | `"section-break"` |
| `title` | yes | The chapter name. |
| `kicker` | no | One or two sentences stating what the chapter will prove. |

### Capacity

| Field | Role | Declared | Fits | Author to |
|---|---|---|---|---|
| `title` | `section` 44pt SemiBold | 60 | ~44 | **≤ 44** |
| `kicker` | `body` 12pt | 180 | ~226 | **≤ 180** |

The kicker is where a section break earns its slide. "Where retail programmes break" is a label;
adding "Most field programmes fail on execution, not on strategy" makes it an argument.

### Example

```json
{
  "archetype": "section-break",
  "title": "The evidence layer",
  "kicker": "Every claim a programme makes should be traceable to something captured at the moment it happened, by the person who was there."
}
```

---

## `title-body`

Plain narrative slide. One idea, one block of prose across the full content width.

**Use it for:** an argument that genuinely needs continuous prose — a position, a rationale, a
definition the rest of the deck depends on.

**Do not use it for:** anything that is really a list (use `icon-rows`, `columns` or `icon-grid`),
anything with a number in it worth showing (`stats`), or as the default archetype. Two `title-body`
slides in a row is the ceiling; three trips `STRUCTURE.REPEATED_ARCHETYPE`. A deck built mostly of
`title-body` is a memo.

**Chrome:** yes.

### Fields

| Field | Required | Notes |
|---|---|---|
| `archetype` | yes | `"title-body"` |
| `eyebrow` | yes | |
| `title` | yes | |
| `deck` | no | |
| `body` | yes | Prose. `\n\n` separates paragraphs. |

### Capacity

| Field | Role | Declared | Fits | Author to |
|---|---|---|---|---|
| `body` | `body` 12pt | 900 | ~1399 | **≤ 900** |

900 characters is about 150 words — three tight paragraphs. The box would physically take more; do
not give it more. A full-width wall of 12pt text at the back of a room is unreadable regardless of
what fits.

### Example

```json
{
  "archetype": "title-body",
  "eyebrow": "The problem",
  "title": "The gap between the plan and the shelf",
  "deck": "Three failure modes account for most of the loss we see when we take over a programme.",
  "body": "Attrition is the first. Field roles turn over faster than they can be trained, so the person in the store on any given day is often the least experienced person who has ever worked it.\n\nThe second is unverified reporting. When a promoter self-reports compliance with no evidence attached, the number that reaches the brand measures optimism rather than what is on the shelf.\n\nThe third is latency. A monthly report describes a month that has already gone."
}
```

---

## `text-visual`

Narrow text column on the left, large visual on the right. The evidence slide.

**Use it for:** a claim in words that a chart, table, image or diagram proves. The text column is
deliberately narrow — it forces the visual to carry the weight.

**Do not use it for:** a visual that needs no explanation (use `full-bleed` or `visual-text`), or a
text block that needs more than about 100 words.

**Chrome:** yes.

### Fields

| Field | Required | Notes |
|---|---|---|
| `archetype` | yes | `"text-visual"` |
| `eyebrow` | yes | |
| `title` | yes | |
| `deck` | no | Good place for the data source. |
| `body` | yes | Prose in a 3.23in column. |
| `visual` | yes | See [§3](#3-shared-sub-objects). |

### Capacity

| Field | Role | Declared | Fits | Author to |
|---|---|---|---|---|
| `body` | `body` 12pt | 620 | ~737 | **≤ 620** |

### Example

```json
{
  "archetype": "text-visual",
  "eyebrow": "Measurement",
  "title": "Compliance improves once evidence is required",
  "deck": "Audited compliance across a national programme, before and after evidence capture became mandatory.",
  "body": "The step change is not the result of better people. It is the result of a claim becoming checkable.\n\nSelf-reported compliance barely moved. Audited compliance rose because the gap between the two stopped being invisible.",
  "visual": {
    "kind": "chart",
    "chart": {
      "type": "column",
      "categories": ["Q1", "Q2", "Q3", "Q4"],
      "series": [
        { "name": "Self-reported", "values": [94, 95, 95, 96] },
        { "name": "Audited",       "values": [61, 68, 84, 92] }
      ]
    }
  }
}
```

---

## `visual-text`

Mirror of `text-visual`: visual on the left (6.43in), a wider text column on the right (4.94in).

**Use it for:** a visual that leads — a photograph, a screenshot, a map — with the explanation
following. Also use it simply to **alternate**: two `text-visual` slides in a row is the limit, and
a deck that always puts the picture on the right reads as mechanical.

**Do not use it for:** a chart whose detail needs the full 7.98in of `text-visual`. Dense charts go
on the right, wide-and-simple visuals go on the left.

**Chrome:** yes.

### Fields

Identical to `text-visual`: `eyebrow`, `title`, `deck?`, `body`, `visual`.

### Capacity

| Field | Role | Declared | Fits | Author to |
|---|---|---|---|---|
| `body` | `body` 12pt | 780 | ~1177 | **≤ 780** |

### Example

```json
{
  "archetype": "visual-text",
  "eyebrow": "Technology",
  "title": "The field app is the system of record",
  "body": "Everything the programme claims starts as a geo-stamped capture in the app, taken by the person standing in the store.\n\nThe app works offline, queues submissions and reconciles when the device reconnects, because coverage in general trade is not a given.\n\nNothing reaches a brand dashboard that did not enter through this screen.",
  "visual": { "kind": "image", "src": "/abs/path/assets/field-app-capture.png" }
}
```

---

## `icon-grid`

Optional narrow intro column on the left, then a 2×2 (or 3×2) grid of cells. Each cell is an icon,
a short subtitle and one line of body.

**Use it for:** 4–6 **parallel** capabilities, benefits or components. Parallel means the order does
not matter and no cell depends on another.

**Do not use it for:** anything sequential (`steps` or `process-band`), anything that needs a full
sentence per item (`icon-rows`), or a comparison (`columns` or `table`).

**Chrome:** yes.

### Fields

| Field | Required | Notes |
|---|---|---|
| `archetype` | yes | `"icon-grid"` |
| `eyebrow` | yes | |
| `title` | yes | |
| `intro` | no | Left column. Sets up the grid. |
| `cells` | yes | List of `{ "subtitle": str, "body": str, "icon": str? }`. |

### Capacity

| Field | Role | Declared | Fits | Author to |
|---|---|---|---|---|
| `intro` | `body` 12pt | 520 | ~665 | **≤ 520** |
| cell `subtitle` | `cardTitle` 14pt SemiBold | 60 | ~25 | **≤ 25** |
| cell `body` | `bodySmall` 11pt | 130 | ~107 | **≤ 107** |
| `cells` count | — | 6 | 6 | **4 or 6** |

> **The trap.** Cell subtitles are 2.70in wide at 14pt SemiBold — about 25 characters, well under
> the declared 60. "Store tiering" fits. "Certification-gated store deployment" does not. Subtitles
> here are labels of two or three words.

Use `deck` on this archetype only if you drop the intro; see [§2](#2-fields-every-content-slide-takes).

### Example

```json
{
  "archetype": "icon-grid",
  "eyebrow": "Staffing",
  "title": "What certification changes",
  "intro": "Certification is not a badge. It gates which stores a person can work, and it is re-earned every quarter.",
  "cells": [
    { "subtitle": "Store tiering",     "icon": "store",        "body": "Only level three and above are deployed to flagship doors." },
    { "subtitle": "Pay linkage",       "icon": "register",     "body": "Certification level feeds the incentive band, so training has a cash consequence." },
    { "subtitle": "Attrition signal",  "icon": "decision-chart","body": "A level that stops progressing is our earliest reliable predictor of a resignation." },
    { "subtitle": "Client visibility", "icon": "users",        "body": "The brand sees the certification mix working its stores, refreshed weekly." }
  ]
}
```

---

## `icon-rows`

Stacked list of 3–4 rows, each an icon, a subtitle and a sentence, with a divider between rows.
Optional visual on the right.

**Use it for:** 3–4 points that each need a full sentence — programme summaries, capability
statements, proof points with detail.

**Do not use it for:** more than 4 rows (the fifth is dropped), or single-word items (`icon-grid`).

**Chrome:** yes.

### Fields

| Field | Required | Notes |
|---|---|---|
| `archetype` | yes | `"icon-rows"` |
| `eyebrow` | yes | |
| `title` | yes | |
| `intro` | no | One line across the top. **Do not also set `deck`.** |
| `rows` | yes | List of `{ "subtitle": str, "body": str, "icon": str? }`. |
| `visual` | no | Right-hand visual, 4.94 × 4.85in. |

### Capacity

| Field | Role | Declared | Fits | Author to |
|---|---|---|---|---|
| `intro` | `subtitle` 18pt | 190 | ~102 | **≤ 102** |
| row `subtitle` | `cardTitle` 14pt SemiBold | 70 | ~42 | **≤ 42** |
| row `body` | `bodySmall` 11pt | 140 | ~192 | **≤ 140** |
| `rows` count | — | 4 | 4 | **3–4** |

> The `intro` sits at 18pt across 6.97in in a single 0.68in band — about 102 characters, not the
> declared 190. Keep it to one short sentence.

### Example

```json
{
  "archetype": "icon-rows",
  "eyebrow": "Proof",
  "title": "Live at scale",
  "intro": "Programmes currently running under this model.",
  "rows": [
    { "subtitle": "National FMCG general trade", "icon": "grocery-bag",
      "body": "4,200 stores across 19 states, 1,100 field staff, daily geo-verified coverage." },
    { "subtitle": "Consumer electronics modern trade", "icon": "store",
      "body": "680 doors, 540 promoters, planogram compliance audited weekly against brand standards." },
    { "subtitle": "Telecom retail", "icon": "smart-phone",
      "body": "2,900 outlets, same-day stock-out alerting, certification-gated deployment to flagship stores." }
  ]
}
```

---

## `columns`

3 or 4 equal columns, each a short subtitle and a paragraph. An optional intro across the top.

**Use it for:** comparing options, audiences, phases or offerings **qualitatively** — where each
column is a paragraph of prose, not a set of attribute values.

**Do not use it for:** comparison on three or more structured attributes (that is `table`), or a
sequence (`steps`). Do not use 2 columns — the layout is built for 3–4 and 2 looks broken.

**Chrome:** yes.

### Fields

| Field | Required | Notes |
|---|---|---|
| `archetype` | yes | `"columns"` |
| `eyebrow` | yes | |
| `title` | yes | |
| `intro` | no | One line across the top. **Do not also set `deck`.** |
| `columns` | yes | List of `{ "subtitle": str, "body": str }`. |

### Capacity

| Field | Role | Declared | Fits | Author to |
|---|---|---|---|---|
| `intro` | `subtitle` 18pt | 190 | ~173 | **≤ 173** |
| column `subtitle` | `cardTitle` 14pt SemiBold | 44 | ~24 | **≤ 24** |
| column `body` | `bodySmall` 11pt | 320 | ~368 | **≤ 320** |
| `columns` count | — | 4 | 4 | **3–4** |

> Column subtitles are 2.59in wide at 14pt SemiBold: about 24 characters. "Brand manager" fits.
> "Regional brand marketing lead" does not. Keep the columns' subtitles the same *kind* of thing —
> all roles, or all phases, or all options — never a mix.

### Example

```json
{
  "archetype": "columns",
  "eyebrow": "Technology",
  "title": "What each audience gets",
  "intro": "The same verified record, presented differently depending on who acts on it.",
  "columns": [
    { "subtitle": "Field team",   "body": "A route for the day, the checklist for each store, and immediate feedback on whether a submission was accepted." },
    { "subtitle": "Supervisor",   "body": "Exceptions only. Who did not check in, which stores failed compliance, and which stock-outs are still open." },
    { "subtitle": "Brand manager","body": "Coverage, compliance and availability by territory, with the underlying evidence one click away." },
    { "subtitle": "Finance",      "body": "Headcount, attendance and incentive exposure reconciled against the record the field sees." }
  ]
}
```

---

## `steps`

3–5 numbered cards, laid out 2×2 (or 2×3), each with an icon, a numbered label and a paragraph.

**Use it for:** a sequence where **order is load-bearing** and each stage needs explaining — an
onboarding path, a certification ladder, an implementation plan.

**Do not use it for:** anything you could shuffle without loss (that is `icon-grid`), or a flow you
want read left-to-right in one band (`process-band`).

**Chrome:** yes.

### Fields

| Field | Required | Notes |
|---|---|---|
| `archetype` | yes | `"steps"` |
| `eyebrow` | yes | |
| `title` | yes | |
| `steps` | yes | List of `{ "number": str, "body": str, "icon": str? }`. |

`number` is the step's **short label including its numeral** — `"1. In-app learning"`, not just
`"1"`. It is the card's heading.

### Capacity

| Field | Role | Declared | Fits | Author to |
|---|---|---|---|---|
| `number` | `cardTitle` 14pt SemiBold | 46 | ~52 | **≤ 46** |
| `body` | `bodySmall` 11pt | 190 | ~350 | **≤ 190** |
| `steps` count | — | 5 | 5 | **3–5** |

### Example

```json
{
  "archetype": "steps",
  "eyebrow": "Staffing",
  "title": "From hire to certified, in four steps",
  "steps": [
    { "number": "1. In-app learning", "icon": "smart-phone",
      "body": "Video modules and instructor-led sessions delivered to the field app, completed before the first store visit." },
    { "number": "2. Field shadowing", "icon": "team",
      "body": "Two supervised days in a live store with a certified peer, scored against a fixed rubric." },
    { "number": "3. Classroom session", "icon": "classroom",
      "body": "One day a month back in the classroom for refreshers, new launches and objection handling." },
    { "number": "4. Certification", "icon": "badge-tick1",
      "body": "Training scores, trainer feedback and store outcomes combine into a single certification level." }
  ]
}
```

---

## `process-band`

A single horizontal band holding 4–6 stages left to right, each an icon, a short label and a
caption. The value-chain slide.

**Use it for:** a linear flow the audience should read as one movement — capture → verify → score →
alert → report.

**Do not use it for:** a process with detail per stage (`steps`), a branching process (it cannot
show branches), or anything with more than 6 stages.

**Chrome:** yes.

### Fields

| Field | Required | Notes |
|---|---|---|
| `archetype` | yes | `"process-band"` |
| `eyebrow` | yes | |
| `title` | yes | |
| `stages` | yes | List of `{ "label": str, "body": str }`. |

### Capacity

| Field | Role | Declared | Fits | Author to |
|---|---|---|---|---|
| `label` | `cardTitle` 14pt SemiBold | 24 | ~19 | **≤ 19** |
| `body` | `caption` 10.5pt | 90 | ~112 | **≤ 90** |
| `stages` count | — | 6 | 6 | **4–6** |

> Stage labels are one word wherever possible. At 5 stages each label box is 2.08in — about 19
> characters at 14pt SemiBold. "Verify" is right. "Verification and scoring" is two stages.

### Example

```json
{
  "archetype": "process-band",
  "eyebrow": "Technology",
  "title": "Evidence, from store to dashboard",
  "stages": [
    { "label": "Capture", "body": "Geo-stamped photo and form capture in the field app, offline tolerant" },
    { "label": "Verify",  "body": "Automated checks flag staged, duplicated or out-of-window submissions" },
    { "label": "Score",   "body": "Compliance scored against the planogram and the visit checklist" },
    { "label": "Alert",   "body": "Stock-outs raised the same day to the responsible supervisor" },
    { "label": "Report",  "body": "Brand dashboards refresh continuously against the verified record" }
  ]
}
```

---

## `table`

Structured comparison. Header row plus body rows, max 7 columns and max 10 body rows.

**Use it for:** comparison across three or more attributes, commitments against targets, a rate
card, a coverage matrix — anything where the audience will scan a specific cell.

**Do not use it for:** four short phrases (that is `columns`), or a table you built because the
content was disorganised. A table with one-word cells and no numbers is a list wearing a grid.

**Chrome:** yes.

### Fields

| Field | Required | Notes |
|---|---|---|
| `archetype` | yes | `"table"` |
| `eyebrow` | yes | |
| `title` | yes | |
| `intro` | no | One line above the table. **Do not also set `deck`.** |
| `table` | yes | `{ "header": [str], "rows": [[str]] }`. Every row must have the same length as `header`. |

### Capacity

| Field | Role | Declared | Fits | Author to |
|---|---|---|---|---|
| `intro` | `subtitle` 18pt | 190 | ~169 | **≤ 169** |
| any cell | `bodySmall` / `caption` | 60 | — | **≤ 60**, and far fewer at 7 columns |
| columns | — | 7 (`maxCols`) | 7 | **3–5** |
| body rows | — | 10 (`maxRows`) | 10 | **≤ 10** |

Row height is 0.34in — one line per cell. A cell that wraps to two lines pushes the table past its
box and raises `LAYOUT.OVERFLOW`. At 5 columns each cell is roughly 2.2in wide: keep cells to a
handful of words or a number.

### Example

```json
{
  "archetype": "table",
  "eyebrow": "Measurement",
  "title": "What we commit to, and how it is checked",
  "intro": "Every commitment below is measured from the verified record, not from self-reporting.",
  "table": {
    "header": ["Commitment", "Target", "Measured from", "Frequency"],
    "rows": [
      ["Store coverage",        "98%",        "Geo-verified check-ins",    "Daily"],
      ["Planogram compliance",  "92%",        "Audited shelf photography", "Weekly"],
      ["Stock-out closure",     "24 hours",   "Alert raised to closed",    "Continuous"],
      ["Certified staffing mix","80% level 3+","Certification register",   "Weekly"],
      ["Attendance accuracy",   "99%",        "Biometric and geo check-in","Daily"],
      ["Report latency",        "Same day",   "Capture to dashboard",      "Continuous"]
    ]
  }
}
```

---

## `stats`

3–5 headline metrics across one row: a large number, a label, a caption. **The proof slide.**

**Use it for:** magnitude, scale, improvement. The slide that answers "how big" and "how much
better".

**Do not use it for:** numbers you cannot source. Every value here must survive the question "where
does that come from" asked in the room. Do not use it for four numbers that all measure the same
thing in different units.

**Chrome:** yes.

### Fields

| Field | Required | Notes |
|---|---|---|
| `archetype` | yes | `"stats"` |
| `eyebrow` | yes | |
| `title` | yes | |
| `intro` | no | One line. Put the measurement basis here. **Do not also set `deck`.** |
| `stats` | yes | List of `{ "value": str, "label": str, "caption": str }`. |

### Capacity

| Field | Role | Declared | Fits | Author to |
|---|---|---|---|---|
| `intro` | `subtitle` 18pt | — | ~173 | **≤ 173** |
| `value` | `statNumber` 32pt SemiBold | 10 | ~10 | **≤ 10** |
| `label` | `cardTitle` 14pt SemiBold | 32 | ~24 | **≤ 24** |
| `caption` | `caption` 10.5pt | 90 | ~105 | **≤ 90** |
| `stats` count | — | 5 | 5 | **3–5** |

`value` is a string, not a number — it carries its own unit and formatting: `"62%"`, `"4,200"`,
`"31 days"`, `"3.4x"`. Ten characters is the ceiling, including the unit.

The `caption` is where the number becomes defensible: what was measured, over what period, across
what population. A stat without a caption is a claim without a source.

### Example

```json
{
  "archetype": "stats",
  "eyebrow": "The problem",
  "title": "What the gap costs",
  "intro": "Measured across programmes we inherited in the last three years, before our processes were applied.",
  "stats": [
    { "value": "62%",     "label": "Annual attrition",   "caption": "Median across inherited programmes at handover" },
    { "value": "3.4x",    "label": "Reporting optimism", "caption": "Self-reported compliance versus audited compliance" },
    { "value": "31 days", "label": "Time to insight",    "caption": "Median lag from field event to brand visibility" },
    { "value": "18%",     "label": "Availability loss",  "caption": "Attributable to undetected stock-outs" }
  ]
}
```

---

## `quote`

A pull quote at 44pt with an attribution and a sub-attribution.

**Use it for:** a client testimonial, or a single decisive statement the deck turns on. **One per
deck**, two at the absolute most.

**Do not use it for:** a sentence you wrote yourself and dressed up as a quotation, or as a filler
slide. A quote used three times is decoration and the audience stops reading it.

**Chrome:** yes.

### Fields

| Field | Required | Notes |
|---|---|---|
| `archetype` | yes | `"quote"` |
| `eyebrow` | yes | |
| `title` | yes | The context — where the quote came from. |
| `quote` | yes | The quotation. Do not include the quotation marks; the layout supplies them. |
| `attrib` | yes | Who said it. |
| `attribSub` | no | Their organisation, and the scale that makes it credible. |

### Capacity

| Field | Role | Declared | Fits | Author to |
|---|---|---|---|---|
| `quote` | `section` 44pt SemiBold | 220 | ~86 | **≤ 86** |
| `attrib` | `label` 12pt Medium | 50 | ~156 | **≤ 50** |
| `attribSub` | `caption` 10.5pt | 60 | ~183 | **≤ 60** |

> **The trap.** The declared 220 characters cannot be set at 44pt in a 2.6in box — about 86 fit.
> A three-sentence testimonial will overflow. Cut it to the one sentence that lands. That is also
> better editing.

Anonymised attribution is normal for client work: `"National sales head"` +
`"FMCG client, 4,200 stores"`. Never invent an attribution, and never use a placeholder name — the
string `Person Name` is in `brand.voice.forbiddenPhrases` and is a hard error.

### Example

```json
{
  "archetype": "quote",
  "eyebrow": "Proof",
  "title": "From a national account review",
  "quote": "The difference was not that the numbers got better. It was that we finally believed them.",
  "attrib": "National sales head",
  "attribSub": "FMCG client, 4,200 stores"
}
```

---

## `photo-trio`

Optional narrow text column, then exactly three photographs with captions.

**Use it for:** putting faces or places to the work — the team who will run the account, three store
formats, three markets.

**Do not use it for:** two photos or four. The layout is built for three. Do not use stock
photography of people who are not on the account.

**Chrome:** yes.

### Fields

| Field | Required | Notes |
|---|---|---|
| `archetype` | yes | `"photo-trio"` |
| `eyebrow` | yes | |
| `title` | yes | |
| `body` | no | Left column. |
| `photos` | yes | Exactly three `{ "image": str, "caption": str }`. `image` is a path. |

### Capacity

| Field | Role | Declared | Fits | Author to |
|---|---|---|---|---|
| `body` | `body` 12pt | 620 | ~702 | **≤ 620** |
| `caption` | `caption` 10.5pt | 60 | ~86 | **≤ 60** |
| `photos` count | — | 3 | 3 | **exactly 3** |

Photo regions are portrait (roughly 2.30 × 4.01in). Supply portrait-cropped images or they will be
cropped for you, usually badly. Captions are name-and-role, or place-and-format — never a sentence.

### Example

```json
{
  "archetype": "photo-trio",
  "eyebrow": "The team",
  "title": "Who runs this account",
  "body": "The account is run by three named people with direct field experience in your categories. You will meet them in week one and they do not change without notice.",
  "photos": [
    { "image": "/abs/path/assets/team/programme-director.jpg", "caption": "Programme director · 11 years in general trade" },
    { "image": "/abs/path/assets/team/regional-lead-south.jpg", "caption": "Regional lead, south · 640 stores" },
    { "image": "/abs/path/assets/team/training-head.jpg",       "caption": "Training head · certification and content" }
  ]
}
```

---

## `full-bleed`

Edge-to-edge image with an optional dark scrim, the mono-white logo, an overlay title and a caption.

**Use it for:** visual punctuation. A tone reset between chapters, a strong photographic close, or a
chapter opener where the image *is* the argument.

**Do not use it for:** more than about two slides per deck, or to disguise a slide that had nothing
to say. It makes emptiness bigger, not smaller. Also allowed as the final slide
(`mustCloseWith: ["closing", "full-bleed"]`), but `closing` is almost always the better close
because it carries the ask.

**Chrome:** none. `eyebrow` and `deck` are ignored. The logo variant is `monoWhite` over photography.

### Fields

| Field | Required | Notes |
|---|---|---|
| `archetype` | yes | `"full-bleed"` |
| `image` | yes | Path to a 16:9 image at least 1920 × 1080. |
| `title` | no | Overlay title, sits in the scrim. |
| `caption` | no | One line under the title. |

### Capacity

| Field | Role | Declared | Fits | Author to |
|---|---|---|---|---|
| `title` | `title` 32pt SemiBold | 70 | ~37 | **≤ 37** |
| `caption` | `body` 12pt | 140 | ~201 | **≤ 140** |

Text on a photograph is only legible over the scrim. Keep the title and caption inside the lower
band and pick an image whose bottom third is quiet. `A11Y.CONTRAST` is measured against the surface
the validator can see — a busy image under white text is a judgement call it cannot make for you.

### Example

```json
{
  "archetype": "full-bleed",
  "image": "/abs/path/assets/photography/general-trade-counter.jpg",
  "title": "This is where the programme is won",
  "caption": "A general trade counter in Coimbatore, 11:40 on a Tuesday."
}
```

---

## `closing`

Final slide. Brand panel, reversed logo, a rule, a short title, one concrete next step, contact.

**Use it for:** the ask. This is the slide the meeting ends on and the one that decides whether
anything happens next.

**Do not use it for:** "Thank you" and nothing else. A closing slide without a next step wastes the
last thirty seconds of attention you will get.

**Chrome:** none. `eyebrow` and `deck` are ignored.

### Fields

| Field | Required | Notes |
|---|---|---|
| `archetype` | yes | `"closing"` |
| `title` | yes | Short. The ask in a phrase. |
| `cta` | yes | **One concrete next step.** Specific enough to diarise. |
| `contact` | no | Email and site. Keep it to one line. |

### Capacity

| Field | Role | Declared | Fits | Author to |
|---|---|---|---|---|
| `title` | `cover` 54pt SemiBold | 40 | ~26 | **≤ 26** |
| `cta` | `subtitle` 18pt | 90 | ~61 | **≤ 61** |
| `contact` | `body` 12pt | 140 | ~365 | **≤ 140** |

> Same 54pt trap as the cover: about 26 characters. "Let us run a pilot" is 18.

The `cta` must be a decision the audience can take in the room. "Pick one territory and one metric"
is a next step. "We look forward to partnering with you" is not. And no exclamation marks —
`VOICE.EXCLAMATION` is checked on every string in the deck.

### Example

```json
{
  "archetype": "closing",
  "title": "Let us run a pilot",
  "cta": "Pick one territory and one metric. We will run it for a quarter against your baseline.",
  "contact": "hello@channelplay.in · channelplay.in"
}
```

---

# Quick capacity reference

Author-to values at the Channelplay type scale. Smaller of declared and measured.

| Archetype | Field limits (characters) | Item count |
|---|---|---|
| *(chrome, all)* | `eyebrow` 28 · `title` 42 · `deck` 160 | — |
| `cover` | `title` 26 · `subtitle` 61 · `meta` 60 | — |
| `agenda` | item 36 | 4–8 |
| `index` | `label` 14 · item 36 | ≤ 6 |
| `section-break` | `title` 44 · `kicker` 180 | — |
| `title-body` | `body` 900 | — |
| `text-visual` | `body` 620 | — |
| `visual-text` | `body` 780 | — |
| `icon-grid` | `intro` 520 · subtitle 25 · body 107 | 4 or 6 cells |
| `icon-rows` | `intro` 102 · subtitle 42 · body 140 | 3–4 rows |
| `columns` | `intro` 173 · subtitle 24 · body 320 | 3–4 columns |
| `steps` | `number` 46 · `body` 190 | 3–5 steps |
| `process-band` | `label` 19 · `body` 90 | 4–6 stages |
| `table` | `intro` 169 · cell 60 | ≤ 7 cols, ≤ 10 rows |
| `stats` | `intro` 173 · `value` 10 · `label` 24 · `caption` 90 | 3–5 stats |
| `quote` | `quote` 86 · `attrib` 50 · `attribSub` 60 | — |
| `photo-trio` | `body` 620 · `caption` 60 | exactly 3 |
| `full-bleed` | `title` 37 · `caption` 140 | — |
| `closing` | `title` 26 · `cta` 61 · `contact` 140 | — |

A complete, validated 16-slide deck IR exercising 13 archetypes lives at
`examples/channelplay-capability-deck.json`. Read it before authoring your first deck.

---

# Authoring rules

Apply these while writing the IR, whichever archetypes you picked.

- **Choose archetypes deliberately.** Do not use `title-body` for everything. A deck of eight
  `title-body` slides is a memo someone pasted into PowerPoint. Pick from the communication job.
- **Alternate.** `text-visual` and `visual-text` are mirrors of each other for exactly this reason.
  More than two of the same archetype in a row is `STRUCTURE.REPEATED_ARCHETYPE`.
- **Respect capacity.** See below.
- **Write in the brand's voice.** For Channelplay: sentence case everywhere except the eyebrow
  (which the builder upper-cases), no exclamation marks, present tense, active voice, and never a
  phrase from `brand.voice.forbiddenPhrases`.
- **Speaker notes carry the detail the slide must not.** A long qualifier belongs in `notes`, not in
  a fourth bullet.

## The capacity discipline

Every archetype declares character capacities in `grammar/deck-grammar.json`. They are not
suggestions and they are not a rendering detail — **they are a content contract**.

> Exceeding a capacity is a content problem, not a layout problem.

Three responses are legitimate when copy does not fit:

1. **Cut it.** Most over-long slide copy is one clause of throat-clearing plus one real sentence.
2. **Split the slide.** Two clear slides beat one crowded one. The slide budget is a budget, not a
   law of physics — renegotiate it with the user.
3. **Move it to `notes`.** Detail the presenter says out loud does not need to be on the wall.

Three responses are not:

- Dropping the type size. `TYPE.BELOW_MIN` is an error below `brand.type.minBodyPt` (10.5pt for
  Channelplay), and everything above it that is off the scale is `TYPE.OFF_SCALE`.
- Widening or moving the box. Geometry is shared across every brand; changing it to fit one slide
  breaks the grid for everything else. **`grammar/deck-grammar.json` is never edited to suit
  content.**
- Deleting the whitespace. The gutters are the design.

**Two numbers, and the smaller one wins.** The declared capacity is the editorial target. The
validator additionally *measures* the copy with `brandlib.estimate_text_height()` and raises
`LAYOUT.OVERFLOW` (error) when it does not fit the box. The "Author to" column in every table above
is already the smaller of the two — write to it and neither number ever fires.

The three that catch people every time:

| Field | Declared | Actually fits | Author to |
|---|---|---|---|
| slide `title` (every content slide) | 70 (`maxTitleChars`) | ~42 at 32pt | **≤ 42** |
| `cover.title` | 70 | ~26 at 54pt | **≤ 26** |
| `quote.quote` | 220 | ~86 at 44pt | **≤ 86** |

## Archetype anti-patterns

- `steps` used for things that are not sequential. If the order can be shuffled without loss, it is
  `icon-grid` or `columns`.
- `table` used for four short phrases. That is `columns`.
- `stats` with numbers nobody sourced. A stat you cannot defend in the room is worse than no stat.
- `quote` used three times. The third one is decoration.
- `full-bleed` used because a slide was thin. It makes the thinness bigger.
