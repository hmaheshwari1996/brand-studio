# Motion templates — animating a brand story

Read this when authoring an **explainer**: a film whose scenes are animated rather than
rendered from deck slides. For the deck-video pipeline you do not need this file.

A motion template is a self-contained HTML file in `templates/video/`. You reference it by
filename stem:

```json
{ "id": "s3", "role": "proof",
  "visual": { "kind": "motion", "template": "counter", "data": { ... } },
  "vo": "...", "caption": "...", "holdSec": 8 }
```

The builder injects the brand into `{{BRAND_VARS}}` and your `visual.data` into `{{SCENE_DATA}}`,
then screenshots the page once per frame at `?t=0…1`. **Every template is brand-agnostic** — it
reads only role variables (`--ink`, `--accent`, `--surface-*`, `--font-*`), so the same markup
restyles itself for any brand in the registry. See
[`templates/video/README.md`](../../../templates/video/README.md) for the full contract.

---

## Choosing a template from the story beat

The brand's storyline arc lives in `brand.video.storyline.arc`. For Example Brand that is
hook → problem → approach → proof → outcome → call-to-action. Map each beat to the motion that
carries it:

| Beat | Template | What it does | Reach for it when |
|---|---|---|---|
| **hook** | `kinetic-type` | A short statement assembles itself and lands | The film opens on an idea, not a number. Almost always the right opener. |
| **problem** | `compare` (`mode: "wipe"`) | Before wiped away by after | The problem is best shown as a contrast — what it looks like now vs what it should be |
| **problem** | `chart-reveal` | Bars or a line grow into place | The problem *is* the data — a gap, a decline, a divergence |
| **approach** | `process-flow` | Steps draw in sequence, connectors leading the eye | You are explaining a method. This is the workhorse for how-we-do-it. |
| **proof** | `counter` | Numbers count up to their final value | The claim is numeric. Counting up makes a figure feel earned rather than asserted. |
| **proof** | `chart-reveal` (`highlightGap: true`) | Two series, with the gap measured at the end | The proof is a comparison over time |
| **outcome** | `coverage-map` | Points populate across a territory | The outcome is *scale* — stores, states, headcount |
| **call-to-action** | `outro` | The brand lockup and the next step | Always. The outro is a cached brand asset; do not hand-build one. |
| *any* | `scene` | Generic bullets / quote / split / stats | Nothing above fits. Prefer a specific template — generic motion reads as generic. |

Two rules that matter more than the table:

1. **One idea per scene.** If a scene needs two sentences of setup, it is two scenes. A template
   that has to carry two ideas will look cluttered at every value of `t`.
2. **Vary the motion.** Three `counter` scenes in a row is a spreadsheet with a soundtrack.
   Alternate the *kind* of movement — type, then flow, then number, then map.

---

## The data each template takes

Every template documents its own data contract in a comment at the top of its file. Read that
file rather than guessing — it is the authoritative source and it cannot drift from the code:

```
head -40 templates/video/counter.html
```

| Template | Shape of `visual.data` |
|---|---|
| `kinetic-type` | `lines[]`, `emphasis[]`, `eyebrow`, `surface`, `align`, `mark` |
| `counter` | `stats[{value,label,caption}]`, `eyebrow`, `title`, `surface`, `countUp` |
| `chart-reveal` | `type`, `categories[]`, `series[{name,values[]}]`, `valueSuffix`, `highlightGap`, `max` |
| `process-flow` | `steps[{label,body,icon}]`, `orientation`, `numbered`, `emphasis` |
| `coverage-map` | `regions[{name,count,x,y}]` or `points`, `seed`, `outline`, `headline`, `subhead` |
| `compare` | `mode`, `before{}`, `after{}`, `emphasise`, `eyebrow`, `title` |
| `scene` | `variant`: `bullets` \| `quote` \| `split` \| `stats` |

`counter` parses a human-authored value string — `"4,200"`, `"62%"`, `"3.4x"`, `"24 hours"`,
`"Same day"` — and renders it back **exactly as authored** at `t = 1`. Write the number the way
you want it to read; do not pre-split it.

---

## Pacing an animated scene

The animation occupies only the first `--motion-anim-sec` of a scene (default in
`build_video.py`); after that the frame holds. So:

- **Give an animated scene room.** A `coverage-map` populating 300 dots inside a 3-second hold is
  a blur. The brand's hold floor and ceiling are in `brand.video.slideHoldSec`.
- **The narration drives the length, not the animation.** The builder already extends a scene when
  its voiceover runs longer than the declared `holdSec`. Write the line first.
- **Land the motion before the point.** The visual should be settled when the narration reaches
  the sentence it illustrates — not still moving.

---

## Adding a template

1. Copy the closest existing template. `scene.html` is the simplest, `chart-reveal.html` the most
   involved.
2. Keep both markers, exactly once each: `{{BRAND_VARS}}` and `{{SCENE_DATA}}`.
3. **No CSS animations or transitions.** Chrome screenshots one instant; anything time-based in CSS
   is captured at an arbitrary point and the render stops being reproducible. Drive everything from
   the `t` query parameter in synchronous inline JS.
4. **No hardcoded hexes.** Role variables only — that is what makes it work for every brand.
5. **No randomness.** If you need scatter, seed a small PRNG from `data.seed`. `Math.random()`
   makes every frame a different picture and the video will strobe.
6. Document the data contract in a header comment.
7. Test it before wiring it into a film — render five frames and confirm they differ, and that
   `t=1.0` is fully settled:

```bash
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
for T in 0 0.25 0.5 0.75 1.0; do
  "$CHROME" --headless --disable-gpu --no-sandbox --hide-scrollbars \
    --screenshot="/tmp/f-$T.png" --window-size=1920,1080 "file:///tmp/test.html?t=$T"
done
```

Render `t=0.5` twice and diff the two PNGs. If they are not identical, something in the template
is non-deterministic and the film will flicker.

---

## What the validator will catch

`validate_video.py` samples frames and checks the dominant colours against the brand palette, so a
template that hardcodes an off-brand colour surfaces as `VIDEO.OFF_PALETTE` with the timestamp.
It also enforces the safe margin (`VIDEO.SAFE_MARGIN`), so a template that runs type to the edge
will be flagged. Neither check can tell you whether the motion is *good* — watch it.
