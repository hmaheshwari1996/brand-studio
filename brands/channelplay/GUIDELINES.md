# Channelplay Design System

Version 1.0 · Derived from `Channelplay_Brand_Guidelines.pptx`

## How to read this document

The source brand guidelines define three things: **colour**, **typography**, and **logo usage**. Everything else in a working design system — neutrals, spacing, radii, elevation, motion, component behaviour — was not specified, so it has been derived here to be consistent with the brand.

Every section is tagged:

- **[BRAND]** — taken directly from the guidelines deck. Do not change without brand approval.
- **[DERIVED]** — extrapolated to make the system usable. Safe to revise; flag changes to whoever owns the brand.

---

## 1. Brand foundations

### 1.1 Colour — core palette **[BRAND]**

| Token | Hex | Role |
|---|---|---|
| `brand.blue` | `#0000FF` | Primary brand colour. The anchor of the identity. |
| `brand.navy` | `#0F0A6C` | Deep end of the blue gradient. Doubles as the darkest text colour. |
| `brand.mint` | `#41E7AB` | Primary accent. High-energy, used against dark or white. |
| `brand.teal` | `#29AFA7` | Deep end of the mint gradient. |
| `brand.sky` | `#0194DD` | Secondary blue. Bridges blue and mint. |
| `brand.azure` | `#2F80ED` | Softer blue listed in the deck. Used here as the interactive/link blue. |
| `brand.tint` | `#EBF6F9` | Pale cyan wash used as the deck's own page background. |

> The deck writes the mint as both `41E7AB` and `42E6AB`. `#41E7AB` is used as canonical; `#42E6AB` appears only as the start stop of the mint gradient and is preserved there verbatim.

### 1.2 Colour — gradients **[BRAND]**

Two gradients are specified. Both run at 135° (top-left → bottom-right) in this system **[DERIVED]**.

| Token | Stops | Use |
|---|---|---|
| `gradient.blue` | `#0F0A6C` → `#0000FF` | Hero panels, primary CTA on marketing surfaces, dark section backgrounds. |
| `gradient.mint` | `#42E6AB` → `#29AFA7` | Accent panels, success/positive states, data highlights, chart series. |

Rules:

- Never place the two gradients adjacent to each other. Pick one per surface.
- Gradients are for **surfaces**, not text. No gradient text.
- Product UI uses flat colour by default; gradients are reserved for hero/marketing moments and empty-state illustration.

### 1.3 Colour — extended ramps **[DERIVED]**

Brand hexes alone can't carry hover, pressed, disabled, and border states. These ramps were interpolated from the brand hexes and are the only tints/shades that should be used.

**Blue ramp** (anchored at 600 = `#0000FF`)

| Step | Hex | Use |
|---|---|---|
| 50 | `#EDEDFF` | Selected-row wash, subtle callout background |
| 100 | `#D6D6FF` | Hover wash on light backgrounds |
| 200 | `#ADADFF` | Disabled fill |
| 300 | `#7A7AFF` | Borders on tinted surfaces |
| 400 | `#4747FF` | Hover on `brand.blue` fills |
| 500 | `#1A1AFF` | — |
| **600** | **`#0000FF`** | **Base — `brand.blue`** |
| 700 | `#0000CC` | Pressed state |
| 800 | `#0A0794` | — |
| 900 | `#0F0A6C` | `brand.navy` |
| 950 | `#080540` | Deepest surface |

**Mint ramp** (anchored at 400 = `#41E7AB`)

| Step | Hex | Use |
|---|---|---|
| 50 | `#EAFCF5` | Success background |
| 100 | `#CBF8E7` | — |
| 200 | `#96F1CE` | — |
| 300 | `#63ECBB` | Hover on mint fills |
| **400** | **`#41E7AB`** | **Base — `brand.mint`** |
| 500 | `#29AFA7` | `brand.teal` |
| 600 | `#1F8C86` | Pressed |
| 700 | `#1B7A74` | **Minimum shade for mint-family text on white** (5.1:1) |
| 800 | `#145B57` | — |
| 900 | `#0D3A38` | — |

**Sky ramp** (anchored at 500 = `#0194DD`)

| Step | Hex | Use |
|---|---|---|
| 50 | `#E6F5FC` | Info background |
| 100 | `#EBF6F9` | `brand.tint` — page wash |
| 300 | `#5EC2EE` | — |
| **500** | **`#0194DD`** | **Base — `brand.sky`** |
| 600 | `#0180BE` | Hover |
| 700 | `#016FA8` | **Minimum shade for sky-family text on white** (5.4:1) |
| 900 | `#014567` | — |

**Neutrals** — cool, tinted very slightly toward `brand.navy` so greys never fight the blue.

| Step | Hex | Use |
|---|---|---|
| 0 | `#FFFFFF` | Base surface |
| 50 | `#F7F8FC` | App background, subtle zebra |
| 100 | `#EDEFF5` | Card wash, disabled surface |
| 200 | `#DCE0EA` | Hairlines, dividers, input borders |
| 300 | `#B9C0D0` | Borders on filled surfaces, disabled text on dark |
| 400 | `#8A93A8` | Placeholder text, disabled label |
| 500 | `#5E6678` | Secondary body text (5.8:1 on white) |
| 600 | `#3C4356` | Strong secondary text |
| 700 | `#262B38` | Body text alternative to navy |
| 800 | `#171B25` | — |
| 900 | `#12141C` | Deepest neutral surface |

**Semantic (functional)** — the brand deck has no error/warning colour, so these were chosen to sit beside the palette without reading as brand colours.

| Token | Hex | Notes |
|---|---|---|
| `success` | `#1B7A74` | Mint-700. Keeps success on-brand. |
| `success.bg` | `#EAFCF5` | |
| `warning` | `#B26A00` | |
| `warning.bg` | `#FFF4E0` | |
| `danger` | `#C4262E` | Deliberately warm-red so it never reads as brand blue. |
| `danger.bg` | `#FDECEC` | |
| `info` | `#016FA8` | Sky-700. |
| `info.bg` | `#E6F5FC` | |

### 1.4 Colour — accessibility rules **[DERIVED]**

Contrast ratios below are computed against `#FFFFFF` (WCAG 2.1 relative luminance).

| Colour | Ratio on white | Verdict |
|---|---|---|
| `#0F0A6C` navy | 16.4:1 | AAA — safe for any text |
| `#0000FF` blue | 8.6:1 | AA/AAA body — safe for text and for white text **on** it |
| `#5E6678` neutral-500 | 5.8:1 | AA body — secondary text |
| `#016FA8` sky-700 | 5.4:1 | AA body |
| `#1B7A74` mint-700 | 5.1:1 | AA body |
| `#2F80ED` azure | 3.9:1 | **Large text (18.66px bold / 24px) and UI borders only** |
| `#0194DD` sky | 3.3:1 | **Non-text UI only** — icons, borders, chart fills |
| `#29AFA7` teal | 2.7:1 | **Decorative only** — never text on white |
| `#41E7AB` mint | 1.6:1 | **Decorative only** — but 13.2:1 with *dark* text on top |

Hard rules:

1. `brand.mint` and `brand.teal` are **never** text colours on a light background. Use `mint.700` (`#1B7A74`).
2. Text on mint surfaces is always `brand.navy`, never white.
3. Text on `brand.blue` and on the blue gradient is always `#FFFFFF`.
4. Focus ring is `brand.blue` at 2px with a 2px offset — on blue surfaces it flips to `#FFFFFF`.
5. Never rely on colour alone to convey state; pair with an icon or label.

---

## 2. Typography

### 2.1 Typeface **[BRAND]**

**Poppins** is the sole brand typeface. Three weights are approved:

| Level | Weight | Use |
|---|---|---|
| Level 1 | Poppins **SemiBold** (600) | Display text, headlines |
| Level 2 | Poppins **Medium** (500) | Sub-headers, call-outs |
| Level 3 | Poppins **Regular** (400) | Body copy |

Do not introduce Bold (700), Light (300), or any other weight. Italics are not part of the system.

**Fallback stack** **[DERIVED]** — Poppins is geometric with a tall x-height; these are the closest widely-available substitutes:

```
"Poppins", "Poppins Fallback", ui-sans-serif, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif
```

Monospace (code, IDs, SKUs, tabular data) **[DERIVED]** — the deck specifies none:

```
"JetBrains Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace
```

### 2.2 Type scale **[DERIVED]**

A 1.25 (major third) scale on a 16px base, snapped to 4px increments.

| Token | Size / Line height | Weight | Tracking | Use |
|---|---|---|---|---|
| `display-lg` | 56 / 60 | 600 | −0.02em | Hero headline |
| `display` | 44 / 52 | 600 | −0.02em | Page hero |
| `h1` | 36 / 44 | 600 | −0.01em | Page title |
| `h2` | 28 / 36 | 600 | −0.01em | Section title |
| `h3` | 22 / 30 | 600 | 0 | Sub-section |
| `h4` | 18 / 26 | 500 | 0 | Card title, sub-header |
| `body-lg` | 18 / 28 | 400 | 0 | Intro paragraph |
| `body` | 16 / 24 | 400 | 0 | Default body |
| `body-sm` | 14 / 20 | 400 | 0 | Dense UI, table cells |
| `caption` | 12 / 16 | 400 | 0.01em | Helper text, timestamps |
| `overline` | 12 / 16 | 500 | 0.08em | Eyebrows, ALL CAPS labels |
| `label` | 14 / 20 | 500 | 0 | Form labels, buttons, nav |

Rules:

- Headings use 600. UI labels and buttons use 500. Everything else 400.
- Negative tracking only above 22px — Poppins gets loose at display sizes.
- Body measure caps at 72ch; ideal 60–68ch.
- Never set body copy below 14px.
- Sentence case everywhere except `overline`.

---

## 3. Logo **[BRAND]**

Three approved files ship in `assets/`:

| File | Use |
|---|---|
| `channelplay-logo-primary.png` | Light backgrounds. Colour mark + dark wordmark. |
| `channelplay-logo-reversed.png` | Dark or brand-blue backgrounds. Colour mark + white wordmark. |
| `channelplay-logo-mono-white.png` | Single-colour white. Photography, video bugs, busy backgrounds. |

Rules from the guidelines:

1. Use only the approved versions. Never redraw, recolour, stretch, rotate, add effects, or rebuild the wordmark in Poppins.
2. The logo sits on **solid, clean backgrounds** only — no photographs unless the mono-white version is used over a solid overlay.
3. Keep **clear space** around it.

Derived specifics **[DERIVED]**:

- Clear space = the height of the triangular play mark, on all four sides. Nothing enters that box.
- Minimum size: 120px wide on screen, 25mm wide in print.
- Placement: top-left of a page header; centred only on splash/loading screens.
- On the blue gradient, use `channelplay-logo-reversed.png`.
- Source PNGs are 1982px wide with transparency. **Request vector (SVG/EPS) from the brand owner before any print or large-format work** — the current files will not scale cleanly.

---

## 4. Layout **[DERIVED]**

### 4.1 Spacing

4px base unit. Named steps only — no arbitrary values.

| Token | px |
|---|---|
| `space-0` | 0 |
| `space-1` | 4 |
| `space-2` | 8 |
| `space-3` | 12 |
| `space-4` | 16 |
| `space-5` | 20 |
| `space-6` | 24 |
| `space-8` | 32 |
| `space-10` | 40 |
| `space-12` | 48 |
| `space-16` | 64 |
| `space-20` | 80 |
| `space-24` | 96 |

Defaults: 8px inside compact controls, 16px between related elements, 24px inside cards, 48px between page sections (80px on marketing pages).

### 4.2 Grid and breakpoints

12-column fluid grid. Max content width 1200px, wide variant 1440px.

| Breakpoint | Min width | Columns | Gutter | Margin |
|---|---|---|---|---|
| `sm` | 0 | 4 | 16 | 16 |
| `md` | 768 | 8 | 24 | 24 |
| `lg` | 1024 | 12 | 24 | 32 |
| `xl` | 1280 | 12 | 32 | 48 |

### 4.3 Radius

Poppins is geometric and the mark has soft corners — the system leans rounded but not pill-shaped.

| Token | px | Use |
|---|---|---|
| `radius-none` | 0 | Tables, full-bleed panels |
| `radius-sm` | 4 | Badges, tags, checkboxes |
| `radius-md` | 8 | Buttons, inputs, menu items |
| `radius-lg` | 12 | Cards, popovers |
| `radius-xl` | 20 | Modals, hero panels |
| `radius-full` | 9999 | Avatars, pills, toggles |

### 4.4 Elevation

Shadows are tinted with navy rather than pure black so they sit in the palette.

| Token | Value |
|---|---|
| `shadow-xs` | `0 1px 2px rgba(15, 10, 108, 0.06)` |
| `shadow-sm` | `0 2px 6px rgba(15, 10, 108, 0.08)` |
| `shadow-md` | `0 6px 16px rgba(15, 10, 108, 0.10)` |
| `shadow-lg` | `0 16px 32px rgba(15, 10, 108, 0.12)` |
| `shadow-focus` | `0 0 0 2px #FFFFFF, 0 0 0 4px #0000FF` |

Use elevation sparingly: cards sit flat with a `neutral-200` border by default; shadow appears on hover or for floating layers only.

---

## 5. Motion **[DERIVED]**

| Token | Value | Use |
|---|---|---|
| `duration-fast` | 120ms | Hover, focus, colour change |
| `duration-base` | 200ms | Dropdowns, tooltips, toggles |
| `duration-slow` | 320ms | Modals, drawers, page transitions |
| `ease-standard` | `cubic-bezier(0.2, 0, 0, 1)` | Default |
| `ease-enter` | `cubic-bezier(0, 0, 0, 1)` | Elements entering |
| `ease-exit` | `cubic-bezier(0.4, 0, 1, 1)` | Elements leaving |

All motion respects `prefers-reduced-motion: reduce` — animation collapses to an instant state change, never removed entirely.

---

## 6. Components **[DERIVED]**

Specs are behavioural contracts. Reference implementations are in `code/components.tsx`.

### Button

Height 40px default, 32px small, 48px large. Radius `md`. Label `label` token (14/500). Horizontal padding 16px (20px on large). Icon 16px with 8px gap.

| Variant | Rest | Hover | Pressed | Disabled |
|---|---|---|---|---|
| Primary | `#0000FF` bg, white text | `blue-400` | `blue-700` | `blue-200` bg, white text |
| Secondary | transparent bg, `#0000FF` text, 1px `#0000FF` border | `blue-50` bg | `blue-100` bg | `neutral-300` text + border |
| Tertiary | transparent, `#0000FF` text | `blue-50` bg | `blue-100` bg | `neutral-400` text |
| Accent | `#41E7AB` bg, `#0F0A6C` text | `mint-300` | `mint-500` + navy text | `mint-100` bg, `neutral-400` text |
| Danger | `#C4262E` bg, white text | darken 8% | darken 16% | 40% opacity |

Focus: `shadow-focus`. Loading: spinner replaces the icon slot, label stays, control is `aria-busy` and non-interactive. Never more than one primary button per view region.

### Input / Select / Textarea

Height 40px. Radius `md`. 1px `neutral-200` border, `#FFFFFF` fill. Placeholder `neutral-400`. Focus: border `#0000FF` + `shadow-focus`. Error: border `danger`, message below in `caption`/`danger`, `aria-describedby` wired to it. Disabled: `neutral-100` fill, `neutral-400` text. Label sits above at `label` token, 6px gap. Helper text 4px below.

### Card

`#FFFFFF` fill, 1px `neutral-200` border, `radius-lg`, 24px padding. Interactive cards get `shadow-sm` on hover and a `#0000FF` border on focus. Never nest a card inside a card — use a divider.

### Badge / Tag

Height 22px, `radius-sm`, 8px horizontal padding, `caption` at weight 500. Pairs: neutral `neutral-100`/`neutral-700`, info `info.bg`/`info`, success `success.bg`/`success`, warning `warning.bg`/`warning`, danger `danger.bg`/`danger`, brand `blue-50`/`#0000FF`.

### Table

Header row `neutral-50` background, `label` token, `neutral-600` text, sticky on scroll. Cells `body-sm`, 12px vertical / 16px horizontal padding. 1px `neutral-200` row divider. Row hover `neutral-50`. Selected row `blue-50`. Numeric columns right-aligned and tabular-figures. Radius `none`; the wrapping container carries `radius-lg` and clips.

### Navigation

Top bar 64px, white, 1px `neutral-200` bottom border, logo top-left. Active item: `#0000FF` text with a 2px `#0000FF` underline. Side nav 260px, `neutral-50` background, active item `blue-50` fill with `#0000FF` text and `radius-md`.

### Modal

`radius-xl`, `shadow-lg`, 32px padding, max width 560px (720px wide variant). Overlay `rgba(15, 10, 108, 0.48)`. Focus trapped, Escape closes, focus returns to the trigger. Title `h3`, actions bottom-right with primary last.

### Toast

`radius-md`, `shadow-md`, 16px padding, max width 420px, bottom-right. 4px left indicator in the semantic colour. Auto-dismiss 5s, pauses on hover, always dismissible. Announced via `role="status"` (`role="alert"` for danger).

### Charts

Categorical series order: `#0000FF`, `#41E7AB`, `#0194DD`, `#0F0A6C`, `#29AFA7`, `#2F80ED`. Grid lines `neutral-200`, axis labels `neutral-500` at `caption`. Single-series charts use `#0000FF`. Sequential scales run the blue ramp 100 → 900. Never use a gradient fill inside a chart.

---

## 7. Voice **[DERIVED]**

Channelplay is a retail training and field-execution agency — the audience is client-side brand and sales leadership plus a large field workforce. The interface voice follows from that: plain, operational, confident, never salesy.

- Sentence case for everything except `overline`.
- Active voice, present tense. Buttons name the action: "Publish schedule", not "Submit".
- The same word for the same thing everywhere — "store", not store/outlet/site interchangeably.
- Errors state what happened and the next step. They do not apologise and are never vague.
- Empty states are an invitation to act, with the primary action in reach.
- No exclamation marks in product UI.

---

## 8. Open items to confirm with the brand owner

1. Vector logo files (SVG/EPS) — current assets are raster only.
2. Whether `#2F80ED` is an approved brand colour or an artefact of the deck. It is used here as the interactive/link blue.
3. Mint canonical hex: `#41E7AB` vs `#42E6AB`.
4. Error/warning colours — invented here, no brand source.
5. Whether Poppins is licensed for embedding in client-facing product (see `assets/FONTS.md`).
6. Iconography — no icon style is defined anywhere in the guidelines. Recommendation: a 24px, 1.5px-stroke, rounded-cap outline set, tinted `neutral-600` by default and `#0000FF` when active.
