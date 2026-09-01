---
description: Turn a reference photo — a lipstick swatch, fabric, paint, metal, foliage — into a display type treatment in the brand face
argument-hint: "[path to the reference photo] [the words to set, e.g. 'EXAMPLE BRAND']"
---

# /brand-texture

Read a photograph's colour, grain, gloss and edge character onto letterforms, and render the result
as an RGBA PNG that drops into a deck or a film. Arguments: `$ARGUMENTS`

```sh
ROOT="${CLAUDE_PLUGIN_ROOT:?set CLAUDE_PLUGIN_ROOT to the brand-studio directory, or run this from Claude Code}"
PY="$HOME/.cache/brand-studio/venv/bin/python"
```

## 0. Say what this is, once

If the user asked for a **font** from a photo, correct it plainly and move on — this is the answer to
the request, not a blocker:

> A photo can't become a .ttf — font files carry outlines, not colour or texture. What it can become
> is a type treatment: your reference's palette and surface rendered onto letterforms. The letterforms
> stay Poppins, because the guidelines allow no other face; only the fill and edges come from the photo.

## 1. Brand and reference

Resolve the brand as usual (the **brand-kit** skill). If `$ARGUMENTS` names no image, ask for one, and
say what makes a good one: **one surface, filling most of the frame, evenly lit** — a busy photo with
five things in it produces unreadable type. If there is no string, ask; display treatments are 1–4 words.

## 2. Analyse first, always

```sh
"$PY" "$ROOT/scripts/texture_type.py" --ref <photo> --text "<WORDS>" --analyze-only
```

Show the output as it prints: the palette with each colour's nearest brand token and its delta E, the
grain / directionality / gloss / raggedness figures, and the style `auto` picked with its reason. This
step is worth running alone — it says whether the reference is on-brand before anyone makes artwork.
If it reports OFF-PALETTE, say so: a lipstick red sits ~85 delta E from the brand's blues. Fine for
a client brand or a campaign moment; wrong for house material that sits beside the rest of the deck.
The user decides, not you.

## 3. Get the nod on the style

Offer the auto pick plus the alternatives, one line each: `smooth` (clean edges, texture fill only),
`ragged` (broken edges — chalk, paint, lipstick), `grainy` (noise through the fill), `glossy`
(specular lift), `matte` (gloss flattened). Wait for an answer. Do not render five and ask later.

## 4. Render and show

```sh
"$PY" "$ROOT/scripts/texture_type.py" --ref <photo> --text "<WORDS>" \
  --style <chosen> --width 1920 --preview --out <name>.png
```

Show the preview sheet — light, dark, hero gradient — and quote the contrast numbers. If contrast
fails 3.0:1 on a background they intend to use, say it in the open, with the number. Pass
`--accept-off-palette` only after the user has actually accepted it; it records the decision in the
sidecar. Hand back three paths: the PNG, the `-preview.png` sheet, and the `.json` sidecar — whose
`reproduce` line rebuilds the artwork byte for byte.

## 5. Where it may be used

Covers, section breaks, hero moments, reel titles, campaign lockups. **Never** body copy, a label, UI,
or anything below display size. It is a picture of words: no reflow, no selectable text, invisible to
a screen reader — so whenever it goes in a deck, put the real string in that slide's speaker notes.

Placement in a deck archetype and in a Video IR, and the full CLI:
`skills/brand-deck/references/texture-type.md`.
