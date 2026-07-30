---
name: brand-kit
description: Resolve, confirm or create the brand profile that governs a deck or video. Use at the START of any presentation, deck, PPT, slide, video, explainer or brand-asset task, before authoring anything. Also use when the user wants to add a new brand, update brand colours/fonts/logo, or register reference videos.
---

# brand-kit

Decides **which brand profile is active** and guarantees the user agreed to it. Every deck and video
here is built from one profile (`brands/<id>/brand.json`) and validated against it. This is the
**mandatory first step of any deck or video task**: nothing runs until the brand is settled, and
`brand-deck` / `brand-video` assume a confirmed brand id. Never assume Channelplay — it is the house
brand, not the default.

## Protocol

1. **IDENTIFY** — get the brand name from the user. Do not infer it from the file you were handed or
   the client named in a slide. If unnamed, ask once, offering `brand_resolve.py --list` plus "a new
   brand". A deck *for* a client *by* the agency is two names — ask which one governs.
2. **RESOLVE**
   ```sh
   ROOT="${CLAUDE_PLUGIN_ROOT:?set CLAUDE_PLUGIN_ROOT to the brand-studio directory, or run this from Claude Code}"
   PY="$HOME/.cache/brand-studio/venv/bin/python"
   "$PY" "$ROOT/scripts/brand_resolve.py" "<name as the user typed>"; echo "exit=$?"
   ```
   The exit code is the contract — never infer from the text. `0` exact → step 3. `3` fuzzy (≥0.6,
   often wrong) → step 3, but name the candidate and its score and make the user choose between it,
   another candidate and "new brand". `4` none → step 4. Else: stop, report stderr, never guess.
3. **CONFIRM** — show the summary card `brand_resolve.py` already printed; do not paraphrase it or
   dump `brand.json`. Ask for approval explicitly; silence, "sure whatever" or moving on to content
   is **not** approval. Never proceed unconfirmed. Amendments route through the learn protocol, then
   re-show the card. Run intake group (g) even here.
4. **INTAKE** (exit 4, or a new brand was asked for) — `AskUserQuestion` in small batches, **max four
   questions per call**, one group at a time, echoing back each group in one line. Take a guidelines
   deck/PDF if offered: extraction beats interrogation. All groups mandatory: **a)** identity
   **b)** colour **c)** type **d)** logo **e)** voice **f) video kit** — intro/outro, music,
   captions, storyline; ask even for a deck, the profile is shared — **g) reference material**: *"Do
   you have sample or reference videos for this brand? Sample or reference decks?"*, of existing
   brands too. Analyse anything supplied; report every disagreement with the guidelines.
5. **WRITE** — collect the answers into a scratch JSON shaped like a partial `brand.json`, then
   ```sh
   "$PY" "$ROOT/scripts/new_brand.py" --answers <scratch>/intake-<id>.json --id <id>
   ```
   Copy assets in, make every path relative to the brand dir, re-show the card, run the STEP 5
   sanity checks, confirm.
6. **HANDOFF** — state the active profile in one line ("Active brand profile: **Channelplay**
   (`channelplay`), confirmed."), then pass the **brand id only** to `brand-deck` / `brand-video`;
   both re-load from disk.

## Read this when — on demand, never upfront

| File | Read it for |
|---|---|
| `references/intake.md` | STEP 4: full question bank; extracting values from a guidelines PPTX/PDF; logo probe; STEP 3 card detail; analysing reference videos/decks. |
| `references/registry.md` | STEP 5: `brand.json` schema; learned-rule semantics; registry format; environment, non-negotiables, failure modes; sharing a profile with a teammate. |

## Learn protocol

When the user corrects the output — colour, wording, pacing, an icon, a scene length, anything —
persist it **before** you continue. The same correction must never be needed twice.

| Tier | For |
|---|---|
| Dated entry in `brands/<id>/LEARNED.md` | Always, even alongside the others. A one-off wording change stops here. |
| Rule in `brands/<id>/rules.local.json` | A preference a script can check. `id` starts `LOCAL.`; re-load the brand after — a malformed file breaks downstream skills. |
| Edit to `brands/<id>/brand.json` | Durable facts (palette, logo placement, min size, fps). Bump `version`, set `updated`, add a `provenance` line. |

Then **state in one line which tier you used**. Never edit `grammar/deck-grammar.json` for a brand.
Templates, `kind` semantics, decision table: `references/registry.md` § *Learn protocol*.

## Token discipline

- Resolve the brand **once per session**. Once confirmed, do not re-read `brand.json`.
- Print the **compact summary** (`brand_resolve.py` output), never raw `brand.json`.
- Load a reference file only when the step you are on needs it.
- **Reuse cached brand assets** — intro, outro, music bed, rendered icons — unless the user
  explicitly asks for a new one. Regenerating a brand's intro on every video is the single most
  expensive mistake in this plugin.
