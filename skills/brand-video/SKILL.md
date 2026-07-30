---
name: brand-video
description: Build enterprise-grade, brand-compliant videos - narrated deck videos and motion-graphic explainers. Use for any request to create a video, explainer, showreel, product walkthrough, sizzle, animated overview or to add voiceover, captions, intro/outro or music to existing material. Produces an mp4 plus captions, validated against the brand profile.
---

# brand-video

A Video IR JSON becomes an `.mp4` + `.srt` + `.mp4.timeline.json` — build → validate → watch until
clean and watched. Narration is the spine.

```sh
ROOT="${CLAUDE_PLUGIN_ROOT:?set CLAUDE_PLUGIN_ROOT to the brand-studio directory, or run this from Claude Code}"
PY="$HOME/.cache/brand-studio/venv/bin/python"   # the venv python
```

## Pick the pipeline

| Pipeline | Choose when | Cost |
|---|---|---|
| `deck-video` | The content is a deck, or the audience wants a narrated walkthrough. **Default.** | Cheapest, in sync with the deck, slides pre-validated. Needs `--deck`. |
| `explainer` | The idea needs motion to land — flow, build, count-up, reveal. | Chrome frame sequences; each scene authored twice. |

Say which you chose and why. `kind` picks the renderer; mixing scene kinds is legal.

## Workflow

1. **BRAND FIRST.** **brand-kit** → confirmed brand id; never guess or default to Channelplay.
   Confirm the **video kit** (intro/outro, music, captions, storyline); if `referenceVideos` is
   empty, ask for reference videos first.
2. **PICK THE PIPELINE** (above); state the choice.
3. **WRITE THE SCRIPT AS PROSE FIRST, GET IT APPROVED.** Speaking order, beat labels, word count and
   runtime (`words ÷ rateWpm × 60` + intro/outro − transitions). Nothing renders until the user has
   read the words; over target by >10%, cut words.
4. **AUTHOR THE VIDEO IR** → `<name>.video.json`; every scene declares a `role` walking the brand
   arc. Schema + examples: pipelines.md.
5. **BUILD**, `--dry-run` first, every time:
   ```sh
   "$PY" "$ROOT/scripts/build_video.py" --ir <ir.json> [--deck deck.pptx] --out <video.mp4> --dry-run
   ```
   Read the printed timeline (`hold source`, `TOTAL` vs target, warnings, captions), then rerun for
   real. Never ship a non-zero exit.
6. **VALIDATE**, looping build → validate to zero errors:
   ```sh
   "$PY" "$ROOT/scripts/validate.py" <video.mp4> --brand <id> --format human
   ```
   Sidecars attach beside the mp4 — keep the three together. Exit `2` blocks; fix the IR, never the
   mp4 or SRT. Kept warnings need a reason.
7. **WATCH IT.** Extract a frame at every scene boundary from the timeline, view them all; confirm
   the narration is not clipped. Commands: troubleshooting.md.
8. **REPORT.** Paths, duration vs target, scene inventory, caption stats, validator counts, accepted
   warnings with reasons, anything unverified.

## Production rules

- One idea per scene; a scene needing two sentences of setup is two scenes.
- `holdSec` is a floor: `max(holdSec, slideHoldSec.min, voDuration + 0.4)`. 90s ≈ 8–12 scenes.
- Narration is spoken, not read: one clause a sentence, active, present, no exclamation marks (error).
- Numbers as spoken in `vo` — "sixty two percent", not "62%"; captions may use numerals.
- Captions are not optional when the brand requires them; most of this audience watches muted.
- Music never competes with narration; ducking and loudness come from `brand.video.music`.

## Read this when

| File | Read at |
|---|---|
| `references/pipelines.md` | Steps 1–2, 4–5, 8 — both pipelines, IR schema + examples, the `?t=` template contract, new templates, video-kit intake, build flags, music, report format. |
| `references/motion-templates.md` | Step 4 when the pipeline is **explainer** — which motion carries which story beat, each template's `visual.data`, pacing an animated scene, adding a template. |
| `references/audio.md` | Steps 3 and 5 — generating a copyright-free music bed, voiceover in another language, which TTS engine covers which language, speech normalisation, loudness targets. |
| `references/script-and-vo.md` | Step 3 — narration, the arc, pacing, VO settings, captions. |
| `references/troubleshooting.md` | Steps 6–7 — every violation id + fix, build failures, watch commands, learn protocol, non-negotiables. |

Read **on demand**, at the step that needs it.

## What the validator cannot see

- Whether the story lands: `STRUCTURE.STORYLINE` checks a scene is *labelled* `proof`, nothing more.
- Whether the visuals match the words: an off-by-one slide index passes every check.
- Whether the pacing drags: six compliant 12s scenes is 72 seconds of one image at a time.

## Learn protocol

Persist every correction before continuing; never need the same correction twice.

| Tier | Destination | For |
|---|---|---|
| 1 | `brands/<id>/LEARNED.md` | **Always**, with any other tier. Dated: what, why, scope, fix. |
| 2 | `brands/<id>/rules.local.json` | Mechanically checkable rules; `id` starts with `LOCAL.`. |
| 3 | `brands/<id>/brand.json` | Durable brand facts — fps, holds, captions, music, arc, voice. |

State the tier used, every time. Never edit `templates/video/*.html`. Schemas: troubleshooting.md.

## Token discipline

A wrong 90-second render is the costliest mistake here.

- **The brand's intro and outro are reused automatically** — rendered once into
  `brands/<id>/video/generated/` and reused byte-identically thereafter, so every film for a brand
  opens the same way. The cache invalidates itself when the logo, gradient, duration, resolution or
  fps changes. Pass `--regenerate-intro` / `--regenerate-outro` **only on an explicit request**.
  `meta.introTitle` personalises one film and bypasses the cache — use it rarely.
- **Reuse cached slide renders** when the deck has not changed: same `--work-dir`; never re-run
  **brand-deck** for slides you have.
- **Always `--dry-run`** before a full render; fix drift in words there.
- **Resolve the brand once per session**; never dump raw `brand.json` into context.
- **Read a reference file only at the step that needs it.**
- **Fixing one scene, re-render that scene only** — a one-scene IR, or `--anim-fps 15 --crf 24`.
