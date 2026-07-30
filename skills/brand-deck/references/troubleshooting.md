# Validator troubleshooting

Every violation id the deck validator can emit, what actually causes it, and the concrete fix.

Numbers and hexes below are Channelplay's. Another brand shifts the values; the causes and fixes are
the same.

---

## Reading the output

```sh
"$PY" "$ROOT/scripts/validate.py" deck.pptx --brand channelplay --format human --ir deck.json
```

`--format json` (the default) emits FROZEN CONTRACT C:

```json
{ "target": "/abs/deck.pptx", "brand": "channelplay", "kind": "deck", "pass": false,
  "counts": {"error": 2, "warn": 5, "info": 1},
  "violations": [ {"id":"TYPE.INHERITED_FONT","severity":"error","where":"slide 4 / body",
                   "found":"…","expected":"…","rule":"…","fix":"…"} ] }
```

| Exit | Meaning | What to do |
|---|---|---|
| `0` | `counts.error == 0` | Ship-able. Still read the warnings. |
| `2` | At least one error | **Blocking.** Fix and rebuild. |
| `1` | Internal failure | Wrong path, unknown brand, unreadable file, malformed `rules.local.json`. Fix the invocation or the brand profile, not the deck. |

Each violation names `where` (slide and shape path), `found`, `expected`, the `rule` it comes from
and a `fix`. Read `fix` before reaching for this file — it is usually specific enough to act on.

### Always pass `--ir`

Without `--ir` the validator has to infer each slide's archetype from geometry. The consequences:

- `STRUCTURE.UNKNOWN` (info) instead of real structural checks, when nothing can be inferred.
- `STRUCTURE.NO_COVER` and `STRUCTURE.NO_CLOSING` drop from **error** to **warn**.
- Role detection is weaker, so title-specific checks (`CONTENT.LONG_TITLE`, learned rules with
  `scope: title`) miss shapes they should catch.

With `--ir`, structural checks are authoritative. `STRUCTURE.IR_MISMATCH` (info) tells you the IR
and the `.pptx` describe different decks — you validated a stale build.

### The fix loop

1. Read the violation. Identify the **IR field** it comes from.
2. Edit the IR.
3. Rebuild: `build_deck.py --ir deck.json --out deck.pptx`.
4. Re-validate.

**Never hand-edit the `.pptx` to clear a violation.** The next build overwrites it, the IR is what
the deck is regenerated from, and the fix is silently lost. If the violation cannot be expressed as
an IR change, it is a builder bug or a brand-profile problem — say so rather than patching the
output.

---

## The five traps that cause most failures

### 1. The theme leak — `TYPE.INHERITED_FONT` and `+mn-lt`

**Severity:** error. **Most common error in the whole system.**

A run with no explicit `<a:latin typeface="…">` on its `rPr` inherits the PowerPoint theme font.
On current Office builds that is **Aptos**, so a deck that looks fine on the machine that built it
renders in Aptos anywhere else. The validator does not resolve the theme — it reports the *absence*
of an explicit family, because an unset family is the bug.

There is a second spelling of the same bug: a typeface of `+mn-lt` or `+mj-lt`. Those are theme
references, not families, so they surface as `TYPE.OFF_FAMILY` with `found: "+mn-lt"`.

**Causes**
- Text copied in from another deck, an email or a browser, carrying no run-level font.
- A shape created from a layout placeholder that inherits from the master.
- A table cell, a chart axis label or a chart legend — the three places run properties are most
  often left unset.
- Text pasted into the `.pptx` by hand after the build.

**Fix.** Every run must carry an explicit family:

| Weight | PPTX family name |
|---|---|
| 400 | `Poppins` |
| 500 | `Poppins Medium` |
| 600 | `Poppins SemiBold` |

If it appears on text the builder wrote, it is a builder bug — report it with the `where` string
rather than working around it. If it appears on text you added by hand, remove the hand edit and put
the content in the IR.

### 2. Synthetic bold — `TYPE.SYNTHETIC_BOLD`

**Severity:** error.

In this system **weight is a family name, never a boolean**. `bold=True` tells the renderer to
smear the outlines of `Poppins` into a fake heavy, which is not Poppins SemiBold and does not look
like it. `brand.type.syntheticBoldAllowed` is false.

**Cause.** Almost always paste-in from a source that used `<b>`, or a hand edit in PowerPoint where
someone hit ⌘B.

**Fix.** `bold=False`, and set the family to `Poppins SemiBold` (600) or `Poppins Medium` (500).
The same rule applies to `TYPE.ITALIC` — `italicsAllowed` is false, so emphasis is carried by weight,
never by slant.

### 3. Off-palette colour from copy-paste — `COLOR.OFF_PALETTE`

**Severity:** error.

Every colour in the file — text, fill, line, chart series, table borders — must be in
`brandlib.palette_index(brand)`. The message names the nearest approved token and the delta E, so
the fix is usually one substitution.

**Causes**
- A chart or table pasted from Excel, which brings Office's default series colours.
- An image with a coloured background box behind it.
- A hand-typed hex that is one digit off.
- A shape copied from a non-brand deck.

**Fix.** Snap to the hex the message names. If the colour genuinely should be approved, that is a
brand decision: route it through **brand-kit** and the learn protocol, editing `brand.json`. Do not
widen the palette to make one slide pass.

**Note.** `palette_index()` deliberately excludes `color.superseded` and
`colorRules.forbiddenText`, so an unapproved hex can never win a nearest-token lookup.

### 4. Overflow — `LAYOUT.OVERFLOW`

**Severity:** error.

The estimated height of the text exceeds its box by more than
`grammar.deckRules.overflowTolerancePct` (2%). The message gives `needs X in a Y in box (Z% over)`.

**Cause.** Copy longer than the region can hold. Almost always a title, a cover title or a quote —
see the capacity traps in `references/archetypes.md`. The declared grammar capacity is sometimes
generous relative to what a 32pt or 54pt line can physically hold:

| Field | Declared | Actually fits |
|---|---|---|
| slide `title` | 70 | ~42 at 32pt |
| `cover.title` / `closing.title` | 70 / 40 | ~26 at 54pt |
| `quote.quote` | 220 | ~86 at 44pt |
| `icon-grid` cell subtitle | 60 | ~25 at 14pt |
| `columns` column subtitle | 44 | ~24 at 14pt |
| `icon-rows.intro` | 190 | ~102 at 18pt |
| `process-band` stage label | 24 | ~19 at 14pt |

**Fix — the only three legitimate ones:**
1. Cut the copy.
2. Split the slide.
3. Move the detail into `notes`.

**Not** shrinking the type (that is `TYPE.BELOW_MIN` or `TYPE.OFF_SCALE`), **not** widening the box,
**not** editing `grammar/deck-grammar.json`.

Also check for a `deck` line on an archetype that has an `intro`. Setting both pushes content down
by 0.60in (`chrome.contentTopWithDeck`) and overflows whatever was sized for `contentTop`.

### 5. Contrast, especially on mint — `A11Y.CONTRAST` and `COLOR.FORBIDDEN_TEXT`

**Severity:** error, both.

Mint `#41E7AB` and teal `#29AFA7` are **decorative only**. They are listed in
`brand.colorRules.forbiddenText` and cannot be text on any surface. Measured against white:

| Pair | Ratio | Verdict |
|---|---|---|
| mint `#41E7AB` on white | 1.59:1 | fails everything |
| teal `#29AFA7` on white | 2.70:1 | fails everything |
| sky `#0194DD` on white | 3.34:1 | large text only (≥ 18pt) |
| azure `#2F80ED` on white | 3.87:1 | large text only |
| mint.700 `#1B7A74` on white | 5.14:1 | **use this for mint-family text** |
| neutral.500 `#5E6678` on white | 5.76:1 | secondary text |
| blue `#0000FF` on white | 8.59:1 | passes |
| navy `#0F0A6C` on white | 16.40:1 | the default text colour |
| **navy on mint** `#0F0A6C` on `#41E7AB` | 10.34:1 | the correct way to use mint |
| white on mint | 1.59:1 | never |
| white on blue / white on navy | 8.59 / 16.40:1 | passes |

The rule the brand states plainly: **mint and teal are never text on light. Text on blue is white.
Text on mint is navy.**

`brand.colorRules.minContrastBody` is 4.5, `minContrastLarge` is 3.0, and `largeTextPt` is 18. So
sky and azure are usable at 18pt and above and unusable for body.

`A11Y.CONTRAST` resolves the background by finding the shape the text box sits inside (98%
containment). If the text sits on a photograph or on nothing the validator can resolve, it cannot
measure it — that case is yours to judge in step 6 of the workflow.

`brand.colorRules.onSurface` is the authoritative map and the validator quotes it in `fix` when
the surface is known:

```
#0000FF → #FFFFFF   #41E7AB → #0F0A6C   #FFFFFF → #0F0A6C
#0F0A6C → #FFFFFF   #29AFA7 → #0F0A6C   #F7F8FC → #0F0A6C
#080540 → #FFFFFF   #42E6AB → #0F0A6C   #EBF6F9 → #0F0A6C
```

---

## COLOR

| id | Sev | Cause | Fix |
|---|---|---|---|
| `COLOR.SUPERSEDED` | error | A hex from the old master template. `brand.color.superseded.map` lists them: `#0000D5`, `#0029E3`, `#0036AA`, `#0F237B`, `#00006B`, `#272525`, `#08F8B9`, `#0094DE`, `#E90C29`. Almost always an asset or a shape reused from `Channelplay Deck Template 3.pptx`. | Recolour to the canonical hex the message names. **Do not add the old hex to the palette** — the owner ruled on 2026-07-30 that the design system wins and the template palette is superseded. |
| `COLOR.OFF_PALETTE` | error | Any colour not in the brand palette. See trap 3. | Snap to the nearest token the message names. |
| `COLOR.BLACK_TEXT` | error | Text within delta E 6 of `#000000`. Pure black is never used. Comes in with pasted text, Excel tables and chart labels. | Set the run colour to `colorRules.defaultText` = navy `#0F0A6C`. Secondary text is `#5E6678`. |
| `COLOR.FORBIDDEN_TEXT` | error | Text set in a colour listed in `colorRules.forbiddenText`: mint `#41E7AB`, teal `#29AFA7`, `#000000`, `#FF0000`. `#FF0000` is a template scaffold marker that must never ship. | Mint/teal text → `mint.700 #1B7A74`. Black → navy. Red scaffolding → delete the shape; it was never content. |
| `COLOR.GRADIENT_TEXT` | error | A text run with a gradient fill. `colorRules.gradientTextForbidden` is true. | Set the run to a solid palette colour. |
| `COLOR.CHART_GRADIENT` | error | A gradient fill inside a chart series. `colorRules.chartGradientFillForbidden` is true. Usually an Excel paste or an Office chart style. | Flat-fill each series from `colorRules.chartSeries`, in order: `#0000FF`, `#41E7AB`, `#0194DD`, `#0F0A6C`, `#29AFA7`, `#2F80ED`. |
| `COLOR.TWO_GRADIENTS` | warn | More than `colorRules.maxGradientsPerSurface` (1) gradient-filled shapes on one slide. | Keep one gradient; make the others solid. Competing gradients read as noise. |

There are two brand gradients, both at 135°: blue `#0F0A6C → #0000FF`, mint `#42E6AB → #29AFA7`.
Anything else is `COLOR.OFF_PALETTE` on its stops.

---

## TYPE

| id | Sev | Cause | Fix |
|---|---|---|---|
| `TYPE.INHERITED_FONT` | error | No explicit family on the run — the Aptos theme leak. See trap 1. | Set the run family explicitly. |
| `TYPE.OFF_FAMILY` | error | A family that is not `Poppins`, `Poppins Medium` or `Poppins SemiBold`. Includes `+mn-lt` / `+mj-lt` theme references, `Calibri`, `Aptos`, `Arial`, and `Poppins Bold` (700 is a forbidden weight). Also fires on chart text. | Map the intended weight through `type.weightToPptxFamily`. Only 400/500/600 exist. |
| `TYPE.SYNTHETIC_BOLD` | error | `bold=True`. See trap 2. | `bold=False` + the SemiBold family. |
| `TYPE.ITALIC` | error | `italic=True`. `type.italicsAllowed` is false. | Remove the italic; use a heavier family for emphasis. |
| `TYPE.BELOW_MIN` | error | A run below `type.minBodyPt` = 10.5pt. Also checked on chart labels, which default to 9pt or 10pt in Office. | Raise to at least 10.5pt. If the copy then does not fit, that is a content problem — cut it. |
| `TYPE.OFF_SCALE` | warn | A size more than 0.6pt away from every step of `type.deckScalePt`. The scale is 54, 44, 32, 18, 14, 12, 11, 10.5. | Snap to the nearest step. Usually caused by autofit shrinking text, which is itself a signal the copy is too long. |
| `TYPE.TRACKING` | warn | Negative letter-spacing applied below `type.negativeTrackingAbovePt` = 22pt. Tightening small text hurts legibility. | Remove the tracking below 22pt. Negative tracking belongs on `cover` (−0.02), `section` (−0.02), `title` (−0.01) and `statNumber` (−0.01) only. |

---

## CONTENT and VOICE

| id | Sev | Cause | Fix |
|---|---|---|---|
| `CONTENT.PLACEHOLDER` | error | A string from `brand.voice.forbiddenPhrases` shipped in the deck: `Submit`, `Click here`, `Lorem ipsum`, `Place Table / Image / Chart`, `Replace Icons from Icon slide`, `Chapter Name Goes Here`, `This is the title text right here`, `Place Subtitle here`, `This is placeholder copy`, `Person Name`. Nearly always a `visual: {"kind":"placeholder"}` that was never filled, or template furniture copied in. | Replace with the real sentence, or cut the slide. A `placeholder` visual is a drafting tool only — it must not survive to delivery. |
| `VOICE.EXCLAMATION` | error | An `!` anywhere in the deck, or any other character in `voice.forbiddenChars`. | Delete it. The sentence carries the weight. |
| `VOICE.TITLE_CASE` | warn | A paragraph that fails `brandlib.is_sentence_case()` — either ALL CAPS (every letter uppercase, and either > 4 letters or ≥ 3 words) or Title Case (≥ 3 eligible words with ≥ 60% capitalised). Acronyms, single letters, digit-bearing tokens and sentence-initial words are exempt, so `KPI`, `CRM` and proper nouns pass. The eyebrow is exempt entirely. | Lower-case everything after the first word except proper nouns and acronyms. If the string genuinely is a proper noun the checker cannot recognise — a client's legal name, a product name — that is a legitimate accepted warning: **state it in the report with the reason**, or persist it as a `LOCAL.` learned rule if it recurs. |
| `CONTENT.LONG_TITLE` | warn | A title over `maxTitleChars` = 70. | Shorten it. Move the qualifier into the `deck` line. Note that at 32pt only ~42 characters actually fit, so a title that trips this will usually also trip `LAYOUT.OVERFLOW`, which is an error — write to 42. |
| `CONTENT.BULLET_COUNT` | warn | More than `maxBulletsPerSlide` = 6 paragraphs in one text block. `\n\n` in a `body` or `intro` field starts a new paragraph. | Split the slide, or promote the extra points to their own slide. |
| `CONTENT.LONG_BULLET` | warn | A paragraph over `maxWordsPerBullet` = 18 words, **in a block that looks like a list**. The block is list-like when any paragraph carries a bullet glyph, or when there are ≥ 2 paragraphs and the shortest is ≤ 18 words. Continuous prose of two long paragraphs is not flagged; a short lead-in followed by long paragraphs **is**. | Either make it a real list (every item ≤ 18 words) or make it real prose (no short lead-in paragraph). Do not mix the two in one block. Detail goes to `notes`. |
| `CONTENT.EMPTY_SLIDE` | warn | A slide with no text and no picture. | Populate it from the IR, or delete it. Usually a build that silently dropped an unknown field. |

---

## LAYOUT

| id | Sev | Cause | Fix |
|---|---|---|---|
| `LAYOUT.OVERFLOW` | error | Estimated text height exceeds the box by > 2%. See trap 4. | Cut, split, or move to `notes`. |
| `LAYOUT.OFF_CANVAS` | error | A shape extends beyond 0–13.333 × 0–7.5in, with 0.03in of tolerance. | Move or resize it back inside. A full-bleed image should be exactly 13.333 × 7.5 at 0,0. If the builder produced it, report the `where` string. |
| `LAYOUT.OVERLAP` | warn | Two text blocks overlap by more than `overlapToleranceIn` (0.02 sq in). | The usual cause is `deck` set on an archetype that already has an `intro` — the chrome deck line (y 1.78–2.56in) collides with the intro band (y ≈ 1.78–1.85). Use one or the other. Otherwise move one block to its own grid column or row. |
| `LAYOUT.OFF_GRID` | info | A left edge that is not 0, 0.41 (bleed), 0.869 (content) or a column x. | Snap the left edge to the nearest column x: `x(n) = 0.869 + n × (0.7325 + 0.28)`. Informational — a deliberate optical adjustment is a fine reason to leave it. |

---

## LOGO

| id | Sev | Cause | Fix |
|---|---|---|---|
| `LOGO.MISSING` | error | No logo on a `cover` or `closing` slide, or a picture that matches no declared variant. `deckRules.requireLogoOnCoverAndClosing` is true. | Place a variant from `brands/channelplay/assets/logos/`. On the dark hero panels of `cover`, `section-break` and `closing` that is `reversed`; over photography (`full-bleed`) it is `monoWhite`; on light chrome slides it is `primary`. |
| `LOGO.UNDERSIZE` | error | Below `logo.minWidthIn` = 1.25in. | Scale up, keeping the aspect. Deck chrome size is 1.397 × 0.288in; cover/closing size is 1.91 × 0.394in. |
| `LOGO.DISTORTED` | error | Rendered aspect differs from the declared aspect by more than 2%. Primary and reversed are 4.846; mono-white is 4.685. `logo.forbidden` includes stretching. | Restore the aspect. The message gives the exact height for the current width. |
| `LOGO.PLACEMENT` | warn | Logo left edge in the right half of the canvas. The owner ruled top-left on 2026-07-30; the old master template used top-right, so this fires on anything reused from it. | Move to `x = 0.869, y = 0.300`. |
| `LOGO.CLEARSPACE` | warn | Something intrudes into the clear-space box. `logo.clearSpaceRatio` is 1.0 with `clearSpaceBasis` "height of the play mark == logo height", so the pad equals the logo's height on all sides. | Move the intruding shape out. On chrome slides the usual culprit is a long eyebrow or a title that starts too high. |

The brand has **raster logos only**, at 1982px wide. Do not upscale them for large format — request
SVG/EPS from the brand owner. That constraint is recorded in `logo.vectorNote`.

---

## STRUCTURE

Only meaningful with `--ir`. Without it, `NO_COVER` and `NO_CLOSING` downgrade to warnings and the
rest may not fire at all.

| id | Sev | Cause | Fix |
|---|---|---|---|
| `STRUCTURE.TOO_FEW_SLIDES` | error | Fewer than `minSlides` = 3. | A deck needs a cover, at least one content slide and a close. |
| `STRUCTURE.NO_COVER` | error with `--ir`, else warn | Slide 1 is not `cover`. | Make slide 1 a `cover`. |
| `STRUCTURE.NO_CLOSING` | error with `--ir`, else warn | The last slide is not `closing` or `full-bleed`. | End with `closing` carrying one concrete next step. |
| `STRUCTURE.REPEATED_ARCHETYPE` | warn | More than `maxConsecutiveSameArchetype` = 2 identical archetypes in a row. | Alternate: swap one for its mirror (`text-visual` ↔ `visual-text`), or break the run with `stats`, `quote` or `section-break`. Three of anything in a row is also a signal the content was not structured. |
| `STRUCTURE.MISSING_SECTION_BREAK` | warn | More than `sectionBreakEveryNSlidesMax` = 8 slides since the last `section-break`. | Insert a `section-break` before the named slide. If the deck genuinely has one chapter, it is probably too long. |
| `STRUCTURE.IR_MISMATCH` | info | The `.pptx` and the `--ir` file have different slide counts. | You validated a stale build. Rebuild from the current IR, then re-validate. |
| `STRUCTURE.UNKNOWN` | info | No archetype could be inferred for any slide and no `--ir` was supplied. | Re-run with `--ir`. |
| `STRUCTURE.WARN_BUDGET` | error | You passed `--max-warn N` and the warning count exceeded N. | Clear warnings until the count is within budget, or drop the flag. Useful for a final pre-delivery gate; not needed during iteration. |

---

## A11Y

| id | Sev | Cause | Fix |
|---|---|---|---|
| `A11Y.CONTRAST` | error | Text-to-background ratio below `minContrastBody` (4.5) or `minContrastLarge` (3.0 at ≥ 18pt). See trap 5. | Use the colour `colorRules.onSurface` mandates for that background. The message names it when the surface is known. |
| `A11Y.ALT_TEXT` | info | A picture with no `descr`. | Set alt text to a one-line description of what the image shows. Informational, but a deck that will be read by a screen reader or forwarded to an accessibility-conscious client should clear it. |

---

## `LOCAL.*` — learned rules

Learned rules from `brands/<id>/rules.local.json` are enforced with the id, severity, rule text and
fix text written into the rule itself. Their ids start with `LOCAL.` so they are distinguishable
from built-in checks in the output.

If a `LOCAL.` violation is wrong or stale, do **not** delete the rule silently. It exists because a
user corrected the output once. Confirm with the user, then update the rule and log the change in
`LEARNED.md`.

A malformed `rules.local.json` makes `load_brand()` raise, which surfaces as **exit 1**, not exit 2.
Confirm it parses:

```sh
"$PY" -c "import sys;sys.path.insert(0,'$ROOT/scripts/lib');import brandlib;\
b=brandlib.load_brand('channelplay');print(len(b['learnedRules'].get('rules',[])),'learned rules ok')"
```

---

## Namespaces you will not see on a deck

`VIDEO.*`, `AUDIO.*` and `CAPTION.*` belong to `validate_video.py`. If one appears on a `.pptx` run,
`validate.py` dispatched to the wrong child — check the file extension and report it.

---

## When the validator looks wrong

It is deterministic, so "it is wrong" usually means one of:

| What you see | What it usually is |
|---|---|
| A violation on a shape you never authored | Template furniture, or a builder bug. Report the `where` string; do not delete it from the `.pptx`. |
| The same violation on every slide | Chrome, master or layout inheritance. One fix clears all of them. |
| `COLOR.OFF_PALETTE` on a colour you believe is approved | Check `palette_index()` — superseded and forbidden-text hexes are deliberately excluded from it. `"$PY" "$ROOT/scripts/lib/brandlib.py" --brand channelplay` dumps the real palette. |
| Exit 1 with no violations | Internal failure: bad path, unknown brand, unreadable `.pptx`, malformed `rules.local.json`. Read stderr. |
| Violations disappear when you drop `--ir` | They were structural, and they were real. Put `--ir` back. |
| Numbers in `references/archetypes.md` disagree with a violation | The validator is the authority. Follow it, and say in your report that the reference needs updating. |

A genuine false positive is a plugin bug. Report it to the user with the `where` string and the
`found`/`expected` pair — **do not** work around it by editing the `.pptx`, widening the palette, or
changing `grammar/deck-grammar.json`.

---

## Environment and paths

```sh
ROOT="${CLAUDE_PLUGIN_ROOT:?set CLAUDE_PLUGIN_ROOT to the brand-studio directory, or run this from Claude Code}"
PY="$HOME/.cache/brand-studio/venv/bin/python"     # 3.9.6 + python-pptx, pillow, lxml
# ffmpeg, ffprobe and soffice are on PATH.
WORK="${WORK:-$PWD}"                               # where the IR and the .pptx land
```

| Path | What it is |
|---|---|
| `brands/<id>/brand.json` | Colour, type, logo, voice. The only source of brand truth. |
| `grammar/deck-grammar.json` | Geometry, archetypes, capacities, structural rules. Brand-agnostic, shared, **never edited to suit a brand**. |
| `grammar/icons.json` + `grammar/icons/*.svg` | 86 tintable icons, addressed by `name`. |
| `scripts/build_deck.py` | Deck IR → `.pptx`. |
| `scripts/validate.py` | Dispatcher. `.pptx` → `validate_deck.py`, `.mp4` → `validate_video.py`. |
| `scripts/render_preview.sh` | `.pptx` → PNG per slide, so you can look at it. |
| `scripts/lib/brandlib.py` | `--brand <id>` dumps the resolved palette and the superseded map. |

Check `build_deck.py --help` once per session; `--ir` and `--out` are the contract, anything else is
optional. A PostToolUse hook runs `validate.py` automatically on any `.pptx` you write, so you will
see validator output whether or not you ask for it.

If `render_preview.sh` is unavailable, render by hand:

```sh
soffice --headless --convert-to pdf --outdir "$WORK/preview" "$WORK/deck.pptx"
```

---

## Accepted warnings

Errors block. Warnings do not — but **a warning you leave in place needs a stated reason**. "The
client's legal name really is title case" is a reason. "It is only a warning" is not. Every warning
you keep gets named in the final report with its justification. If it is a recurring, defensible
exception, it stops being a per-deck judgement call and becomes a learned rule — see below.

---

## Beyond the validator — what it cannot see

The validator is deterministic and mechanical. It reads colours, families, sizes, geometry,
structure and strings. Everything below is invisible to it and is entirely yours, and step 6 of the
workflow (render the deck and read every slide) is where you do it.

- **Whether the argument holds.** Does slide 6 follow from slide 5? Does the deck reach the decision
  you named in the storyline, or does it stop at "here is some information"?
- **Whether the data supports the claim.** A title that says "compliance improved" over a chart that
  shows a 2-point move inside the noise is a lie the validator will happily pass.
- **Whether the numbers are real.** Every stat needs a source you can say out loud when asked. The
  validator checks that "62%" is 3 characters, not that it is true.
- **Whether the images are appropriate.** Right geography, right retail format, right people, not a
  generic stock photo of a boardroom. It checks alt text, not judgement.
- **Whether the deck is too long.** Twenty compliant slides that could have been eleven is a worse
  deck than eleven. Cut.
- **Whether the tone fits the room.** A confident capability overview and a defensive
  we-missed-target readout are different decks. Sentence case is not tone.
- **Whether the chart type is honest.** A truncated axis, a pie with nine slices, a line chart over
  categorical data — all pass, all mislead.
- **Whether the icon means anything.** `grammar/icons.json` has 86 icons. Picking `anchor` because
  the word "anchor" appeared in the copy is decoration, not communication.

The validator is a floor, never a ceiling.

---

## The learn protocol, in full

Identical to the protocol in **brand-kit**. **Whenever the user corrects the output** — a colour,
spacing, wording, an archetype choice, an icon, slide order, deck length, anything — **persist it
before you continue**. The same correction must never be needed twice. Three tiers, and you may use
more than one.

### Tier 1 — `brands/<id>/LEARNED.md` (always)

Append a dated entry. Do this even when you also do tier 2 or tier 3.

```markdown
## 2026-07-30 — Mint is not a heading colour

- **Correction:** user rejected mint `#41E7AB` on a slide title over white.
- **Why:** 1.6:1 contrast. Mint is decorative only; mint-family text uses `mint.700 #1B7A74`.
- **Scope:** channelplay, all decks and videos, title and body text.
- **Persisted as:** `rules.local.json` rule `LOCAL.NO_MINT_TEXT`.
```

### Tier 2 — `brands/<id>/rules.local.json` (when it is mechanically checkable)

Object form with a `rules` array. `brandlib.load_brand()` merges the file into `learnedRules`, and
the deck validator enforces every rule in it.

```json
{
  "rules": [
    {
      "id": "LOCAL.NO_MINT_TEXT",
      "kind": "forbid_color",
      "scope": "title",
      "value": "#41E7AB",
      "severity": "error",
      "rule": "Mint is decorative only; never a text colour.",
      "fix": "Use mint.700 #1B7A74, or navy #0F0A6C.",
      "added": "2026-07-30",
      "source": "user correction, session 2026-07-30"
    }
  ]
}
```

| `kind` | `value` | Fires when |
|---|---|---|
| `forbid_text` | string or list of strings | the string appears in text in scope (case-insensitive) |
| `require_text` | string | the string is absent everywhere in scope |
| `forbid_color` | hex | the colour is used in scope |
| `min_font_size` | number (pt) | text in scope is smaller |
| `max_font_size` | number (pt) | text in scope is larger |
| `forbid_font_size` | number or list | text in scope uses exactly that size |
| `regex` | pattern (+ `mode: "forbid"` \| `"require"`) | pattern matches / fails to match in scope |

`scope` is one of `any`, `title`, `body`, `eyebrow`. `severity` is `error`, `warn` or `info` — use
`error` only for things that must block a build. `id` **must** start with `LOCAL.` so learned rules
are distinguishable from built-in ones in validator output.

Re-load the brand afterwards. A malformed local rules file makes `load_brand` raise and takes every
downstream skill with it:

```sh
"$PY" -c "import sys;sys.path.insert(0,'$ROOT/scripts/lib');import brandlib;\
b=brandlib.load_brand('<id>');print(len(b['learnedRules'].get('rules',[])),'learned rules ok')"
```

### Tier 3 — `brands/<id>/brand.json` (when it is a durable brand fact)

"Our logo moved to top-left", "azure is retired", "body minimum is 11pt now" are facts about what
the brand *is*, not preferences. Edit the profile, bump `version`, set `updated`, and add a line to
`provenance` naming who ruled and when.

### Which tier?

| The correction is… | Destination |
|---|---|
| A one-off wording change for this deck only | `LEARNED.md` only |
| A preference a script can check ("never say Submit") | `LEARNED.md` + `rules.local.json` |
| A change to what the brand *is* (palette, logo placement, min size) | `LEARNED.md` + `brand.json` |
| A disagreement between the guidelines and a reference deck | `LEARNED.md` + whichever of the other two the user's ruling implies |
| A layout/geometry complaint that applies to all brands | `LEARNED.md`, and say plainly that grammar changes need the plugin owner — **do not edit `deck-grammar.json`** |

### Tier 4 — tell the user

Say in one line which tier you used. Every time.

> Learned: mint is never a text colour — logged in `LEARNED.md` and added as `LOCAL.NO_MINT_TEXT`
> (error, scope title).
