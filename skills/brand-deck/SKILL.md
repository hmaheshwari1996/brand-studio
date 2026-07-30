---
name: brand-deck
description: Build enterprise-grade, brand-compliant PowerPoint decks. Use for any request to create, draft, restructure or brand-check a deck, presentation, slides, PPT, pitch, QBR, proposal or client readout. Produces a native editable .pptx validated against the brand profile.
---

# brand-deck

Produces a native, editable `.pptx`: built from a Deck IR JSON on a shared slide grammar, then
validated against the brand profile (colour, type, logo, layout, structure, voice) to zero errors.

Venv python `PY="$HOME/.cache/brand-studio/venv/bin/python"`; `ROOT` = plugin root, `WORK` holds the
IR and the `.pptx`. Path map: `references/troubleshooting.md`.

## Workflow

1. **BRAND FIRST.** Invoke **brand-kit** for a confirmed brand id; never guess, never default to
   Channelplay — a client's deck may follow the client's guidelines.
   `"$PY" "$ROOT/scripts/lib/brandlib.py" --brand <id>`
2. **STORYLINE BEFORE SLIDES.** Settle audience, the single decision, the arc, the slide count.
   Present a numbered outline (purpose + archetype per slide) and get it approved; silence is not
   approval. `references/storyline.md`.
3. **AUTHOR THE IR.** Write `$WORK/<name>.deck.json` — `brand`, `meta`, `slides`, `notes`. Fields
   and capacities: `references/archetypes.md`.
4. **BUILD.** `"$PY" "$ROOT/scripts/build_deck.py" --ir "$WORK/deck.json" --out "$WORK/deck.pptx"`
   Non-zero exit = build failure: read stderr, fix the IR, rebuild. Never ship one.
5. **VALIDATE.** `"$PY" "$ROOT/scripts/validate.py" "$WORK/deck.pptx" --brand <id> --format human --ir "$WORK/deck.json"`
   Always pass `--ir`, or structural checks degrade. Exit `0` ship, `2` errors, `1` bad invocation.
   **Errors block — loop build → validate until zero**, fixing the IR, never the `.pptx`. Keep a
   warning only with a reason.
6. **LOOK AT IT.** `"$ROOT/scripts/render_preview.sh" "$WORK/deck.pptx" "$WORK/preview"` — then open
   and read every PNG. The validator checks rules, not taste. Never skip.
7. **REPORT.** Path to `.pptx` + IR · slide inventory (archetype + purpose) · error/warn/info counts
   and exit code · every accepted warning + its reason · anything unverified.

## Archetype selection

| Communication job | Archetype |
|---|---|
| Open the deck; slide 1, exactly one | `cover` |
| Map 4–8 chapters, two columns | `agenda` |
| Contents list ≤ 6 items, quieter | `index` |
| Chapter change, every 6–8 slides | `section-break` |
| One argument in prose; not the default | `title-body` |
| Claim in words, visual proof right | `text-visual` |
| Visual leads, text right; alternate | `visual-text` |
| Prove scale; 3–5 sourced numbers | `stats` |
| Qualitative comparison, 3–4 columns | `columns` |
| Compare ≥ 3 attributes; ≤ 7 cols, ≤ 10 rows | `table` |
| Sequence where order matters; 3–5 cards | `steps` |
| Linear flow; 4–6 short stage labels | `process-band` |
| 4–6 parallel capabilities, one line each | `icon-grid` |
| 3–4 points each needing a sentence | `icon-rows` |
| One decisive statement or testimonial | `quote` |
| Faces or places; exactly three photos | `photo-trio` |
| Visual punctuation; ≤ 2 per deck | `full-bleed` |
| Ask for the decision; one next step | `closing` |

## Read this when

| Reference (read on demand) | Read it when |
|---|---|
| `references/archetypes.md` | Step 3, authoring the IR: fields, capacities, rules, examples. |
| `references/storyline.md` | Step 2, the outline: pitch, QBR, readout, rollout, capability. |
| `references/troubleshooting.md` | Step 5, validation failed: violation ids and fixes; plus paths, learn protocol. |
| `references/texture-type.md` | A display moment wants type textured from a reference photo (`/brand-texture`). Display only — never body. |

## Capacity discipline

- Archetypes declare character capacities in `grammar/deck-grammar.json`: a content contract.
- Exceeding one is a **content** problem: cut the copy, split the slide, or move detail to `notes`.
- Never shrink the type, widen the box, delete whitespace, or edit `deck-grammar.json` to fit.
- Declared and measured limits both apply, smaller wins; use `archetypes.md`'s "Author to" column:
  slide `title` ≤ 42, `cover.title` ≤ 26, `quote` ≤ 86.

## What the validator cannot see

- Whether the argument holds: slide 6 following from slide 5, the deck reaching the decision.
- Whether the data supports the claim, and whether every number is real and sourced.
- Whether images suit the geography, format and people — and whether the deck is too long. Cut.

## Learn protocol

Persist every user correction (colour, wording, archetype, icon, order, length) before continuing;
never need the same correction twice.

1. `brands/<id>/LEARNED.md` — **always**. Dated entry: correction, why, scope, where persisted.
2. `brands/<id>/rules.local.json` — when mechanically checkable. Id starts `LOCAL.`; the validator
   enforces it. Re-load the brand after; a malformed file makes `load_brand()` raise.
3. `brands/<id>/brand.json` — durable brand facts only (palette, logo placement, minimums).

Say in one line which tier you used, every time. Schemas and tier table:
`references/troubleshooting.md`.

## Token discipline

- Resolve the brand once per session. Never dump raw `brand.json` into context.
- Read a reference file only at the step that needs it.
- Reuse the brand's cached assets (rendered icons, logo variants), do not regenerate.
- When validation fails, fix the IR and rebuild; do not re-read the deck or redo discovery.
- Keep validator output in `--format human` and let it group violations. Never paste full JSON.
