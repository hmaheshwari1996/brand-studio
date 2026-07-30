# Pipelines

Everything `build_video.py` does, in the order it does it, for both pipelines. Read the pipeline you
picked, then the shared sections — audio, captions and output are identical for both.

- [Choosing](#choosing)
- [Pipeline A — deck-video](#pipeline-a--deck-video)
- [Pipeline B — explainer](#pipeline-b--explainer)
- [Shared: audio](#shared-audio)
- [Shared: captions](#shared-captions)
- [Shared: assembly and output](#shared-assembly-and-output)
- [The Video IR, field by field](#the-video-ir-field-by-field)
- [Worked example — deck-video](#worked-example--deck-video)
- [Worked example — explainer](#worked-example--explainer)
- [The motion-template contract](#the-motion-template-contract)
- [Adding a new motion template](#adding-a-new-motion-template)
- [Brand first — confirming the video kit](#brand-first--confirming-the-video-kit)
- [Build flags and reading the dry run](#build-flags-and-reading-the-dry-run)
- [Music: brand values and practical notes](#music-brand-values-and-practical-notes)
- [Reporting the film](#reporting-the-film)

---

## Choosing

| | `deck-video` | `explainer` |
|---|---|---|
| Source of pixels | An existing `.pptx` | HTML motion templates |
| External requirement | `soffice` + `pdftoppm` | Google Chrome / Chromium |
| Required CLI flag | `--deck <file.pptx>` | none |
| Typical render time, 90s film | 30–60s | 2–6 minutes |
| Brand compliance of the visuals | Already proven by `validate_deck.py` | Comes from `{{BRAND_VARS}}`; still verified by frame sampling |
| Best at | Argument, evidence, structure | Process, flow, motion, reveal |
| Editable afterwards | Yes — edit the deck, re-render | Yes — edit `visual.data`, re-render |

| The film has to… | Pipeline |
|---|---|
| Walk a client through a deck they will also receive | `deck-video` |
| Turn an approved pitch into something sendable | `deck-video` |
| Give a QBR an asynchronous version for people who missed the room | `deck-video` |
| Explain a process, a flow, or a mechanism that moves | `explainer` |
| Open a campaign or an event with a short branded piece | `explainer` |
| Show a number building, or a comparison resolving | `explainer` |

`deck-video` is the default: cheapest, most reliable, and it stays in sync with the deck, whose slides
have already passed the deck validator. Choose `explainer` when a still frame genuinely cannot carry
the idea — not because motion is nicer.

`kind` selects the default renderer and the preflight checks. It does **not** lock the scene types: an
`explainer` may contain `slide` scenes if you pass `--deck`, and a `deck-video` may contain `motion`
and `image` scenes. What it must not do is straddle both without a reason you can state.

---

## Pipeline A — deck-video

### What happens

1. **Preflight.** `soffice`, `ffmpeg`, `ffprobe` and (when narration is wanted) `/usr/bin/say` are
   located. Anything missing is reported before any work starts.
2. **Deck → PDF.** LibreOffice runs headless with a throwaway user profile inside the work directory,
   so a concurrent `soffice` on the machine cannot collide with it.
3. **PDF → PNG.** `pdftoppm` rasterises at 144 dpi. A 13.333 × 7.5 in canvas at 144 dpi is exactly
   1920 × 1080, so slides arrive at native resolution with no resampling.
4. **Sanity check.** `python-pptx` counts the slides in the source deck; if the PNG count does not
   match, the build **fails**. A silent short render is worse than no render.
5. **Scene mapping.** Each scene's `visual.slide` (1-based) selects a PNG. Several scenes may point at
   the same slide.
6. **Motion.** Each still gets a Ken Burns push-in — a 3% zoom across the hold (`KENBURNS_ZOOM = 1.03`),
   disabled with `--no-kenburns`. Anything that is not exactly 16:9 is scaled to fit and padded with
   the brand's pad colour, never stretched.
7. Then the shared audio, caption and assembly stages below.

### Authoring for it

- **Build the deck with brand-deck first, and validate it.** A slide that fails `validate_deck.py`
  fails in the film too, only now it is 1920 × 1080 of it for twelve seconds.
- **`visual.slide` is 1-based and refers to the deck as built.** Insert one slide and every later index
  shifts. Re-check the mapping after any deck edit; an off-by-one slide index is the single most common
  defect in this pipeline and no validator can see it.
- **Not every slide needs a scene, and not every scene needs a new slide.** A 20-slide deck usually
  narrates as 8–12 scenes. Skip the agenda. Skip the section breaks unless the reset is doing work.
- **Two scenes may share a slide.** That is the correct way to spend 20 seconds on a complex slide:
  two ideas, two scenes, one image. It also keeps you inside `slideHoldSec.max`.
- **The deck's own words are on screen already.** Do not read the slide. Narration adds the argument
  the slide implies; the slide carries the evidence.

### Command

```sh
"$PY" "$ROOT/scripts/build_video.py" \
      --ir "$WORK/film.video.json" \
      --deck "$WORK/deck.pptx" \
      --out "$WORK/film.mp4" --dry-run     # then again without --dry-run
```

---

## Pipeline B — explainer

### What happens

1. **Preflight**, plus Chrome. `build_video.py` looks in the usual macOS application directories and on
   `PATH`; `BRAND_STUDIO_CHROME` overrides. Missing Chrome is a hard failure for any scene whose visual
   is `motion` and which has no pre-rendered `mediaFile`.
2. **Brand vars.** The brand profile is compiled into one `<style id="brand-vars">` block: every
   approved colour as `--c-*`, a set of role variables, `@font-face` rules pointing at the brand's own
   `.ttf` files over `file://`, and the canvas and safe-area dimensions. Superseded colours are
   excluded, so a template physically cannot reach one.
3. **Scene HTML.** For each motion scene, the template is read, `{{BRAND_VARS}}` is replaced with that
   style block and `{{SCENE_DATA}}` with the scene's `visual.data` as JSON, and the result is written
   into the work directory.
4. **Frame sequence.** Headless Chrome screenshots the page once per frame at
   `file://…/scene.html?t=<0..1>&frame=<i>&of=<n>`. The template positions its own animation from `t`,
   so there is no CSS animation, no timing race, and every frame is reproducible. Frame 0 renders alone
   (it settles which Chrome background flag this build accepts); the rest fan out across `--jobs`
   workers.
5. **Hold.** Only the first `--motion-anim-sec` seconds (default 1.6) are unique frames. The last frame
   is hard-linked for the remainder of the scene, so a 12-second scene costs 48 renders, not 360.
6. Then the shared audio, caption and assembly stages below.

### Authoring for it

- **The narration still comes first.** `visual.data` illustrates a line that already exists.
- **Pick the `variant` that matches the idea**, not the one that looks busiest. `scene.html` ships
  five: `statement`, `stats`, `bullets`, `quote`, `split`.
- **Pick the `theme` for rhythm.** Six themes: `light`, `tint`, `brand`, `navy`, `gradient`, `accent`.
  Alternate them so consecutive scenes do not cross-fade into an identical field. Every theme already
  pairs its surface with a legal ink colour from `colorRules.onSurface`, so you cannot put mint text on
  white by choosing a theme.
- **Keep text short.** The template auto-shrinks a block that overflows, down to a floor (titles stop
  at 40px, body at 24px). A title that has been shrunk to the floor is a title that needed cutting.
- **Iterate cheap, ship at full rate.** `--anim-fps 15 --jobs 8 --crf 24 --preset veryfast` while you
  are working; drop the overrides for the final render.

### Command

```sh
"$PY" "$ROOT/scripts/build_video.py" \
      --ir "$WORK/explainer.video.json" \
      --out "$WORK/explainer.mp4" --dry-run
```

---

## Shared: audio

**Voiceover.** For each scene with a non-empty `vo`, and unless `--no-audio` is passed:

```
/usr/bin/say -v <brand.video.voiceover.voice> -r <rateWpm> -o scene.aiff -- "<vo>"
ffmpeg -i scene.aiff -ac 2 -ar 48000 -c:a pcm_s16le scene.wav
```

The `.wav` is then measured with `ffprobe`, and **the measured duration is what the timeline uses.**
`--dry-run` cannot measure anything, so it estimates `words ÷ rateWpm × 60` instead; the two normally
agree within a few percent, and the real render is the authority.

`engine` must be `say`. It is the only engine implemented; any other value is a build error. If the
brand's voice moves to a real recording or a third-party TTS, that is a `brand.json` change plus
pre-rendered audio, not a flag.

**Scene duration.** After narration is measured:

```
duration = max(holdSec, brand.video.slideHoldSec.min, voDuration + 0.4)
```

The 0.4s tail (`VO_TAIL_SEC`) is silence after the last word, so the cut never clips it. `holdSec`
above `slideHoldSec.max` warns but is honoured — the ceiling is an editorial rule, and the builder
will not silently truncate your narration to enforce it.

**Music.** When `brand.video.music.enabled` and the IR's `music.enabled` are both true *and* a file
resolves, the bed is looped or trimmed to length, faded in and out with the brand's
`fadeInSec`/`fadeOutSec`, normalised to `music.targetLufs`, and pushed under the narration by a
sidechain compressor derived from `duckUnderVoiceDb`. Enabled with no file resolves to a
narration-only mix plus a warning — the Channelplay default, since the profile ships no track.

**Master.** The mix is normalised to `brand.video.voiceover.targetLufs` (−16.0 LUFS for Channelplay)
and encoded as AAC 192k. The validator re-measures with `ebur128` and allows ±2 LU.

---

## Shared: captions

Cues are generated from each scene's `caption`, falling back to `vo` when `caption` is absent:

1. Split the text at sentence boundaries (`.?!;:`).
2. Wrap each sentence to `captions.maxCharsPerLine` (42).
3. Pack sentences into cues of at most `captions.maxLines` (2) lines. A sentence longer than the line
   budget becomes several cues on its own.
4. Distribute cue durations across the scene's **narration window** — `voStart` to `voEnd`, not the
   whole hold — weighted by character count, with every cue at or above `captions.minDurationSec` (1.2s).

The result is written to `<out-stem>.srt`. When `captions.burnIn` is true it is also rendered into the
picture with `libass`, styled from the brand's caption font, size, colours, opacity and
`bottomMarginPct`; a build that wants burn-in on an ffmpeg without libass fails unless you pass
`--allow-no-burn-in`, which downgrades it to a warning and ships the sidecar only.

**A caption is never invented and never trimmed.** If a cue does not fit, the fix is in the IR.

---

## Shared: assembly and output

Every element becomes one input to a single `ffmpeg` invocation. Consecutive elements are joined with
`xfade` at `brand.video.transition.type` / `.durationSec` — for Channelplay a 0.4s crossfade. Each
transition **overlaps** its two neighbours, so:

```
runtime = Σ elementDuration − transitionSec × (elementCount − 1)
```

where `elementCount` includes the intro and outro. The dry-run's `TOTAL` line is exactly this number.

Three files are written next to `--out`:

| File | What it is |
|---|---|
| `film.mp4` | The film. H.264 / AAC in MP4 at the brand's resolution and fps. |
| `film.srt` | The caption sidecar. |
| `film.mp4.timeline.json` | Everything the build resolved: elements with real start/end/duration, which hold rule won, VO files and durations, every cue, the audio plan, the storyline roles, the exact ffmpeg argv, and the warnings. |

**Keep all three together.** `validate.py` attaches the `.srt` and `.timeline.json` automatically when
they sit beside the mp4. Without the timeline the duration, caption-gap and storyline checks do not run.

---

## The Video IR, field by field

FROZEN CONTRACT B. Unknown keys are ignored; the ones below are the whole surface.

```json
{
  "brand": "channelplay",
  "kind": "deck-video",
  "meta": {"title": "…", "durationTargetSec": 95},
  "intro": true,
  "outro": true,
  "music": {"enabled": true, "file": null},
  "scenes": [ … ]
}
```

### Top level

| Field | Type | Required | Notes |
|---|---|---|---|
| `brand` | string | yes | Brand id. `--brand` overrides it; fix the IR instead. |
| `kind` | `"deck-video"` \| `"explainer"` | yes | Anything else is a build error. |
| `meta.title` | string | yes | Used by the generated intro and outro. |
| `meta.durationTargetSec` | int | recommended | Reported as drift in the dry run and in `VIDEO.DURATION`. |
| `meta.eyebrow` | string | no | Kicker on the generated intro. |
| `meta.outroEyebrow` | string | no | Kicker on the generated outro. |
| `meta.cta` | string | no | Outro headline. Defaults to the last `call-to-action` scene's caption, then to `meta.title`. |
| `meta.contact` | string | no | Outro contact line. |
| `intro` | bool | yes | `true` prepends `brand.video.intro` (3.0s for Channelplay). |
| `outro` | bool | yes | `true` appends `brand.video.outro` (3.5s). |
| `music.enabled` | bool | yes | ANDed with `brand.video.music.enabled`. Either being false means no bed. |
| `music.file` | string \| null | yes | Path to a track, or `null` to fall back to `brand.video.music.file`. |
| `scenes` | array | yes | Non-empty. |

### Scene

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Unique, short. Names the VO file, the frame directory and the timeline row. |
| `role` | enum | yes | One of `brand.video.storyline.arc`: `hook`, `problem`, `approach`, `proof`, `outcome`, `call-to-action`. Repeats allowed; first appearances must be in arc order. |
| `visual.kind` | `"slide"` \| `"motion"` \| `"image"` | yes | See below. |
| `visual.slide` | int \| null | when `kind:"slide"` | 1-based index into the `--deck` pptx. |
| `visual.template` | string \| null | when `kind:"motion"` | Stem of a file in `templates/video/`. `null` → `scene`. |
| `visual.src` | string \| null | when `kind:"image"` | Path, resolved against the IR's directory, the brand directory, the plugin root, then cwd. |
| `visual.data` | object | yes (may be `{}`) | Substituted into `{{SCENE_DATA}}`. Ignored for `slide` and `image`. |
| `vo` | string | yes (may be `""`) | The narration, spoken verbatim. Empty means a silent scene. |
| `caption` | string | no | Defaults to `vo`. |
| `holdSec` | number | yes | The **floor**, not the duration. See the resolution rule above. |

### Visual kinds

- **`slide`** — one PNG from the rasterised deck, push-in applied. Requires `--deck`.
- **`motion`** — an HTML template rendered to a deterministic frame sequence. Requires Chrome.
- **`image`** — a still from disk, scaled to fit and padded with the brand pad colour, push-in applied.
  Use for real photography: a store, a team, a fixture. Never for anything containing type — a JPEG of
  a slide is a slide you cannot edit and cannot validate.

---

## Worked example — deck-video

Narrating a 16-slide capability deck in eight scenes. Complete and runnable.

```json
{
  "brand": "channelplay",
  "kind": "deck-video",
  "meta": {
    "title": "Retail execution, measured",
    "durationTargetSec": 95,
    "cta": "Pick one territory and one metric.",
    "contact": "channelplay.in"
  },
  "intro": true,
  "outro": true,
  "music": {"enabled": true, "file": null},
  "scenes": [
    {
      "id": "s1", "role": "hook",
      "visual": {"kind": "slide", "slide": 1, "template": null, "src": null, "data": {}},
      "vo": "A retail plan is agreed in a boardroom. It is executed by one person, standing in one store, on a Tuesday afternoon.",
      "caption": "A retail plan is agreed in a boardroom. It is executed by one person, standing in one store, on a Tuesday afternoon.",
      "holdSec": 8
    },
    {
      "id": "s2", "role": "problem",
      "visual": {"kind": "slide", "slide": 4, "template": null, "src": null, "data": {}},
      "vo": "Most programmes do not fail on strategy. They fail on three things. Field staff turn over before they are trained. Compliance is self-reported, so nobody can check it. And the report arrives a month after the sale was lost.",
      "caption": "Most programmes do not fail on strategy. They fail on three things.",
      "holdSec": 14
    },
    {
      "id": "s3", "role": "problem",
      "visual": {"kind": "slide", "slide": 5, "template": null, "src": null, "data": {}},
      "vo": "Across the programmes we have inherited, field attrition ran at sixty two percent. Self-reported compliance overstated the audited figure by three and a half times. And it took thirty one days for a field event to reach the brand.",
      "caption": "Across the programmes we have inherited, attrition ran at sixty two percent.",
      "holdSec": 15
    },
    {
      "id": "s4", "role": "approach",
      "visual": {"kind": "slide", "slide": 7, "template": null, "src": null, "data": {}},
      "vo": "So we treat the person in the store as the product. Four steps, from in-app learning through field shadowing and classroom sessions, to a certification that is re-earned every quarter.",
      "caption": "We treat the person in the store as the product.",
      "holdSec": 13
    },
    {
      "id": "s5", "role": "approach",
      "visual": {"kind": "slide", "slide": 10, "template": null, "src": null, "data": {}},
      "vo": "And every claim the programme makes is traceable. Captured in the field with a geo-stamp, verified automatically, scored against the planogram, and raised the same day it happens.",
      "caption": "Every claim the programme makes is traceable.",
      "holdSec": 13
    },
    {
      "id": "s6", "role": "proof",
      "visual": {"kind": "slide", "slide": 13, "template": null, "src": null, "data": {}},
      "vo": "The result is not that people got better. Self-reported compliance barely moved. Audited compliance rose from sixty one to ninety two percent, because the gap between the two stopped being invisible.",
      "caption": "Audited compliance rose from sixty one to ninety two percent.",
      "holdSec": 15
    },
    {
      "id": "s7", "role": "outcome",
      "visual": {"kind": "slide", "slide": 15, "template": null, "src": null, "data": {}},
      "vo": "That model runs today across four thousand two hundred general trade stores, six hundred and eighty modern trade doors, and two thousand nine hundred telecom outlets.",
      "caption": "Live today across more than seven thousand stores.",
      "holdSec": 12
    },
    {
      "id": "s8", "role": "call-to-action",
      "visual": {"kind": "slide", "slide": 16, "template": null, "src": null, "data": {}},
      "vo": "Pick one territory and one metric. We will run it for a quarter, against your current baseline.",
      "caption": "Pick one territory and one metric.",
      "holdSec": 9
    }
  ]
}
```

```sh
"$PY" "$ROOT/scripts/build_video.py" --ir capability.video.json \
      --deck capability.pptx --out capability.mp4 --dry-run
```

**What the dry run tells you about this IR, and what it does not.** It resolves to 102.88s against a
95s target (+8.3%) and warns that five scenes exceed the 12s ceiling. Both are real editorial notes:
`s2`, `s3` and `s6` are each carrying two ideas and want splitting, and the extra 8 seconds comes out
of the same edit. The film builds and validates as it stands — the ceiling is a warning, not an error —
but shipping it without acknowledging that is exactly the habit this skill exists to prevent.

Note `s2`'s caption: the narration is three sentences, the caption is one. A muted viewer receives
*"Most programmes do not fail on strategy. They fail on three things."* — the claim, not the list. That
is the right choice only because the list is on the slide behind it.

---

## Worked example — explainer

A 45-second motion piece with no deck at all. Complete and runnable.

```json
{
  "brand": "channelplay",
  "kind": "explainer",
  "meta": {
    "title": "How a field claim becomes evidence",
    "durationTargetSec": 55,
    "cta": "Start with one territory."
  },
  "intro": true,
  "outro": true,
  "music": {"enabled": false, "file": null},
  "scenes": [
    {
      "id": "e1", "role": "hook",
      "visual": {
        "kind": "motion", "slide": null, "template": "scene", "src": null,
        "data": {
          "variant": "statement", "theme": "gradient",
          "eyebrow": "Field evidence",
          "title": "A photograph is not proof",
          "body": "It is proof of a photograph. Everything after that is process."
        }
      },
      "vo": "A photograph is not proof. It is proof of a photograph. Everything after that is process.",
      "caption": "A photograph is not proof. It is proof of a photograph.",
      "holdSec": 6
    },
    {
      "id": "e2", "role": "problem",
      "visual": {
        "kind": "motion", "slide": null, "template": "scene", "src": null,
        "data": {
          "variant": "bullets", "theme": "light",
          "eyebrow": "Where it breaks",
          "title": "Three places a claim goes missing",
          "bullets": [
            {"lead": "Capture.", "body": "No location, no time, no store."},
            {"lead": "Review.", "body": "A person, a spreadsheet, a week."},
            {"lead": "Escalation.", "body": "By the time it lands, the promotion is over."}
          ]
        }
      },
      "vo": "A claim goes missing in three places. At capture, with no time and no location. At review, which takes a week. And at escalation, too late to matter.",
      "caption": "A claim goes missing at capture, at review, and at escalation.",
      "holdSec": 9
    },
    {
      "id": "e3", "role": "approach",
      "visual": {
        "kind": "motion", "slide": null, "template": "scene", "src": null,
        "data": {
          "variant": "statement", "theme": "navy",
          "eyebrow": "The fix",
          "title": "Bind the claim to the place",
          "body": "Geo-stamped at capture, scored against the planogram automatically, raised the same day."
        }
      },
      "vo": "So we bind the claim to the place it was made. Geo-stamped at capture. Scored against the planogram. Raised the same day.",
      "caption": "Bind the claim to the place it was made.",
      "holdSec": 8
    },
    {
      "id": "e4", "role": "proof",
      "visual": {
        "kind": "motion", "slide": null, "template": "scene", "src": null,
        "data": {
          "variant": "stats", "theme": "tint",
          "eyebrow": "Measured",
          "title": "What changed",
          "stats": [
            {"value": "92%", "label": "Audited compliance, up from 61%"},
            {"value": "1 day", "label": "Field event to brand, from 31"},
            {"value": "7,780", "label": "Stores running the model"}
          ]
        }
      },
      "vo": "Audited compliance rose to ninety two percent, from sixty one. A field event now reaches the brand in a day, not thirty one. Across seven thousand seven hundred and eighty stores.",
      "caption": "Audited compliance 92%, up from 61%. One day to the brand, from 31.",
      "holdSec": 10
    },
    {
      "id": "e5", "role": "outcome",
      "visual": {
        "kind": "motion", "slide": null, "template": "scene", "src": null,
        "data": {
          "variant": "quote", "theme": "brand",
          "quote": "The gap between what we were told and what was true stopped being invisible.",
          "attrib": "National sales lead",
          "attribSub": "Consumer electronics, India"
        }
      },
      "vo": "As one national sales lead put it, the gap between what we were told and what was true stopped being invisible.",
      "caption": "The gap between what we were told and what was true stopped being invisible.",
      "holdSec": 8
    },
    {
      "id": "e6", "role": "call-to-action",
      "visual": {
        "kind": "motion", "slide": null, "template": "scene", "src": null,
        "data": {
          "variant": "statement", "theme": "accent",
          "title": "Start with one territory",
          "body": "One metric, one quarter, against your current baseline.",
          "rule": false
        }
      },
      "vo": "Start with one territory and one metric. One quarter, against your current baseline.",
      "caption": "Start with one territory and one metric.",
      "holdSec": 6
    }
  ]
}
```

```sh
"$PY" "$ROOT/scripts/build_video.py" --ir claim.video.json --out claim.mp4 --dry-run
"$PY" "$ROOT/scripts/build_video.py" --ir claim.video.json --out claim.mp4 --anim-fps 15 --jobs 8
```

This one resolves to **54.61s against a 55s target**, every scene inside the 12s ceiling, no warnings.
That is not luck: the target was set *from the approved script's own arithmetic* — 132 words at 165 wpm
is 48 seconds of narration, plus six 0.4s tails, plus a 3.0s intro and a 3.5s outro, less seven 0.4s
transitions. Do that sum in step 3 and `meta.durationTargetSec` stops being a wish.

Note also the theme rhythm: `gradient → light → navy → tint → brand → accent`. No two adjacent scenes
cross-fade into the same field, and the two dark scenes (`navy`, `brand`) sit either side of a light
one so the film breathes.

**And note what does *not* warn.** `e2` declares `holdSec: 9` and resolves to 10.58s because the
narration is longer. The hold-ceiling warning tests the **declared** `holdSec`, not the resolved
duration — so narration that pushes a scene past `slideHoldSec.max` is silent. Read the `dur` column,
not the warnings, when you are checking pacing.

---

## The motion-template contract

A motion template is a **single self-contained HTML file** in `templates/video/`. It is not a web page:
it is a function from `(brand, data, t)` to one 1920 × 1080 frame.

### The two markers

Every template must contain both, exactly once:

| Marker | Replaced with |
|---|---|
| `{{BRAND_VARS}}` | A `<style id="brand-vars">` block: `@font-face` rules plus a `:root` of every brand variable. **Missing it is a build error.** |
| `{{SCENE_DATA}}` | The scene's `visual.data`, JSON-encoded. Assign it: `window.__SCENE__ = {{SCENE_DATA}};` Every `</` in the payload is escaped, so it cannot close the script tag early. |

### `?t=` — the deterministic frame parameter

**This is the whole reason the pipeline is reproducible.** The renderer screenshots the same file once
per frame, varying only the query string:

```
file:///…/e2.html?t=0.000000&frame=0&of=48
file:///…/e2.html?t=0.021277&frame=1&of=48
…
file:///…/e2.html?t=1.000000&frame=47&of=48
```

`t` runs 0 → 1 across `--motion-anim-sec` (default 1.6s). Every remaining frame of the scene is a hard
link to the last one, so a 10-second scene costs 48 renders rather than 300.

Three rules follow, and breaking any of them breaks the render:

1. **No CSS animations, no transitions, no `requestAnimationFrame`, no timers.** Chrome screenshots
   whenever it is ready; anything time-dependent samples at an arbitrary moment and the sequence
   flickers. Read `t`, compute the position, apply it once.
2. **A frame must depend on nothing but `t` and the data.** No randomness, no `Date.now()`, no network.
   There is no network — Chrome runs with `--allow-file-access-from-files` and the CSP of a `file://`
   page; a remote font or image simply will not load.
3. **`t = 1` must be the resting state.** It is the frame that holds for most of the scene, and the one
   a `--motion-anim-sec 0` build would use. If your composition only looks right mid-animation, it is
   the wrong composition.

The reference implementation in `scene.html` is worth copying wholesale:

```js
function queryT() {
  var m = /[?&]t=([^&]*)/.exec(String(window.location.search || ""));
  var v = m ? parseFloat(decodeURIComponent(m[1])) : NaN;
  return isFinite(v) ? Math.max(0, Math.min(1, v)) : 1;   // default to the resting frame
}
var T = queryT();

function clamp(x) { return x < 0 ? 0 : (x > 1 ? 1 : x); }
function easeOut(x) { return 1 - Math.pow(1 - x, 3); }

// per element: a delay and a span, both in t-units
var p = easeOut(clamp((T - delay) / span));
node.style.opacity = p.toFixed(4);
node.style.transform = "translateY(" + ((1 - p) * 34).toFixed(2) + "px)";
```

### Variables `{{BRAND_VARS}}` provides

**Colours** — every approved colour in the profile, flattened, as `--c-<path>`:

```
--c-brand-blue #0000FF   --c-brand-navy #0F0A6C   --c-brand-mint #41E7AB
--c-brand-teal #29AFA7   --c-brand-sky  #0194DD   --c-brand-tint #EBF6F9
--c-blue-50 … --c-blue-950   --c-mint-50 … --c-mint-900   --c-neutral-0 … --c-neutral-900
--c-semantic-success  --c-semantic-danger  --c-semantic-warning  --c-semantic-info   (+ .bg variants)
```

`color.superseded` is **excluded by construction**, so a template cannot reach an unapproved hex.

**Roles** — prefer these over raw `--c-*`; they are what keeps a template brand-agnostic:

| Variable | Is |
|---|---|
| `--surface-light` `--surface-tint` `--surface-brand` `--surface-dark` `--surface-accent` | Background fields |
| `--ink-on-light` `--ink-on-tint` `--ink-on-brand` `--ink-on-dark` `--ink-on-accent` | The legal text colour for each, straight from `colorRules.onSurface` |
| `--ink` `--ink-soft` | Default and secondary text |
| `--accent` `--accent-2` | Decorative accent (mint / teal) |
| `--accent-text` | The accent-family colour that is **legal as text** (mint.700 `#1B7A74`) |
| `--rule` | Hairline / divider |
| `--grad-primary` `--grad-accent` | Ready-made `linear-gradient(...)` values |
| `--font-regular` `--font-medium` `--font-semibold` `--font-fallback` | Registered family names, e.g. `'Poppins SemiBold'` |
| `--canvas-w` `--canvas-h` `--safe-x` `--safe-y` `--safe-pct` | `1920px`, `1080px`, `96px`, `54px`, `5` |
| `--logo-primary` `--logo-reversed` `--logo-mono-white` | `url('file://…')`, plus a matching `-aspect` number |

**Never hard-code a hex, a font name, a pixel canvas size or a logo path in a template.** Anything you
hard-code is a per-brand bug waiting for the second brand.

### `scene.html` data reference

The general-purpose template. `variant` selects the layout, `theme` selects the field.

| Key | Applies to | Notes |
|---|---|---|
| `variant` | all | `statement` (default) · `stats` · `bullets` · `quote` · `split` |
| `theme` | all | `light` · `tint` · `brand` · `navy` · `gradient` · `accent`. Unknown → `light`. |
| `eyebrow` | all but `quote` | Uppercased by CSS. Write it in sentence case. |
| `title` | all but `quote` | Auto-shrinks to a 40px floor. |
| `body` | all but `quote` | Auto-shrinks to a 24px floor. |
| `rule` | all but `quote` | `false` removes the accent rule under the title. |
| `stats` | `stats` | Up to 4 × `{value, label}`. |
| `bullets` | `bullets` | Up to 6 × `{lead, body}`, or plain strings. |
| `quote` `attrib` `attribSub` | `quote` | `quote` falls back to `title`. |
| `image` | `split` | Any CSS-reachable URL; in practice a `file://` path. |
| `footer` | all | Small muted line at the bottom of the frame. |
| `logo` | all | `false` hides the logo. The variant is chosen by the theme. |

`intro.html` and `outro.html` are filled by the builder from `meta` — `title`, `eyebrow`, `contact`,
`brandName`, and `cta` (which defaults to the last `call-to-action` scene's caption). You do not
normally author their data; you author `meta`.

---

## Adding a new motion template

Only when an idea genuinely cannot be expressed by a `scene.html` variant. A new template is shared
infrastructure across every brand in the plugin — think of it as adding a deck archetype, not as
styling one film.

1. **Copy `scene.html`.** Do not start from a blank file. It already has the markers, the theme table,
   the `?t=` clock, the ease function, the auto-fit loop and the safe-area padding, all of which you
   would otherwise get subtly wrong.
2. **Name it for the communication job**, not the effect: `timeline.html`, `comparison.html`,
   `counter.html` — not `slide-in.html`. The stem is what `visual.template` refers to.
3. **Keep both markers.** `{{BRAND_VARS}}` in `<head>`, `{{SCENE_DATA}}` assigned to
   `window.__SCENE__` in the body script.
4. **Use only role variables.** No hex literals, no font names, no logo paths, no `1920`.
5. **Respect the safe area.** Pad the stage with `var(--safe-y) var(--safe-x)`. Anything that crosses
   it trips `VIDEO.SAFE_MARGIN` in every film that uses the template.
6. **Drive everything from `T`.** Set `data-anim` / `data-delay` / `data-span` attributes and run one
   `applyFrame()` pass, exactly as `scene.html` does. No CSS animation.
7. **Make `t = 1` the composition.** Look at it first.
8. **Auto-fit every text block** with a floor, so a long string shrinks instead of overflowing.
9. **Test the frames before you test the film:**

    ```sh
    "$PY" "$ROOT/scripts/build_video.py" --ir probe.video.json --out /tmp/probe.mp4 \
          --work-dir /tmp/probe-work --keep-temp --no-audio --motion-anim-sec 1.6
    open /tmp/probe-work/frames/<scene-id>/f_000000.png   # t = 0
    open /tmp/probe-work/frames/<scene-id>/f_000047.png   # t = 1, the resting frame
    ```

    Check the first frame is not blank and does not already show the finished composition (both mean
    `t` is not being read), and that the last frame is the one you want held.
10. **Test it on a second brand** if one exists. A template that only looks right for Channelplay is a
    Channelplay asset in a shared directory.
11. **Document the data keys** — variant, theme, and every key it reads — in this file, next to the
    `scene.html` table. An undocumented template is a template nobody else can use.
12. **Tell the plugin owner.** `templates/video/` is shared; a new template is a plugin change, and a
    change to an existing template affects every film ever rendered from it. Do not edit `scene.html`,
    `intro.html` or `outro.html` to make one film work.

---

## Brand first — confirming the video kit

Invoke **brand-kit** and get a confirmed brand id back. Never skip it, never guess, and never default
to Channelplay because it owns the repo.

Video needs more from the profile than a deck does, so confirm the **video kit** explicitly — it is
group (f) of the brand-kit intake and it is mandatory even when the user only asked for a film:

- **Intro / outro** — a supplied file, or a generated one with a style and a duration.
- **Music** — a track, a mood, what to avoid, target loudness, how far to duck under voice.
- **Captions** — burned in or sidecar, position, colours, max characters per line, max lines.
- **Storyline** — the arc the brand's films follow and the rules that go with it.

**If `brand.video.referenceVideos` is empty, ask for sample or reference videos before you write a
word of script.** Say it plainly:

> Do you have any sample or reference videos for this brand — anything you would be happy for this
> film to sit next to? I have none recorded, so I will be building from the written profile alone.

An empty reference list is the single largest source of "that isn't how our videos look". If the user
supplies one, analyse it with the procedure in brand-kit's *Analysing reference material* (fps,
resolution, loudness, real palette, caption treatment), report every disagreement with the written
profile, and get a ruling before you build. Record the ruling either way.

### Where brand truth lives

| Path | What it is |
|---|---|
| `brands/<id>/brand.json` | The `video` block: resolution, fps, intro/outro, music, voiceover, captions, transitions, hold times, storyline arc. The only source of brand truth. |
| `brands/<id>/video/` | Brand-supplied intro/outro clips, music beds, reference films. |
| `brands/<id>/video/generated/` | Rendered-once intro/outro clips for reuse. Point `brand.video.intro.file` / `outro.file` here rather than re-rendering bookends every build. |
| `templates/video/*.html` | Motion templates: `intro.html`, `outro.html`, `scene.html`. Brand-agnostic, shared, never edited for one film. |
| `scripts/build_video.py` | Video IR → `.mp4` + `.srt` + `.mp4.timeline.json`. |
| `scripts/validate.py` | Dispatcher. `.mp4`/`.mov`/`.m4v` → `validate_video.py`, attaching the sidecars for you. |
| `scripts/build_deck.py` | Only relevant for `deck-video`: the deck comes from **brand-deck** first. |

---

## Build flags and reading the dry run

`--dry-run` executes nothing and writes nothing. It prints the resolved brand kit, the full computed
timeline (one row per element with start, end, duration, which hold rule won, VO seconds and cue
count), every caption cue with its measured line lengths, every warning, and the exact ffmpeg command.

**Read it before you commit to a render.** Specifically:

- **The `hold source` column.** `declared` means your `holdSec` won. `voiceover` means the narration is
  longer than the hold and stretched the scene. `brand.slideHoldSec.min` means the scene was too short
  and got padded.
- **`TOTAL` against the target.** Fix drift here, in words, not after a four-minute render.
- **Every `WARNINGS` line.** A hold above the brand ceiling, a missing music file, an unresolved
  image — all of them are cheaper to fix now.
- **The caption block.** Line lengths are printed in brackets. Cues that read badly on the page read
  worse on screen.

Check `--help` once per session. A non-zero exit is a build failure: read stderr, fix the IR, rebuild.
Never hand the user an mp4 from a build that exited non-zero.

| Flag | When |
|---|---|
| `--dry-run` | Always, before the first real render. |
| `--no-audio` | Checking visuals only, or `say` is unavailable. The result is not shippable. |
| `--anim-fps 15 --jobs 8` | Explainer iteration. Halves the Chrome render time; restore before the final. |
| `--work-dir DIR --keep-temp` | Debugging, and keeping the slide PNGs, frame sequences and VO wavs for inspection between runs. |
| `--crf 20 --preset fast` | Draft renders. The final ships at the defaults (CRF 18, medium). |
| `--brand ID` | Override the IR's `brand`. Rare — fix the IR instead. |
| `--no-kenburns` | Disable the 3% push-in on deck stills. |
| `--allow-no-burn-in` | Downgrade a missing-libass burn-in failure to a warning, shipping the sidecar only. |

### Intro and outro reuse

**Reuse is automatic and is the default.** A brand's bookends are rendered once into
`brands/<id>/video/generated/`, recorded in a `manifest.json`, and reused byte-identically by every
later build — so every film for that brand opens the same way. You do not need to point
`intro.file` / `outro.file` at anything; that field is for a bookend supplied by the brand owner as a
finished file.

| Flag | Effect |
|---|---|
| `--reuse-intro` / `--reuse-outro` | The default. Listed only so a build can be explicit. |
| `--regenerate-intro` / `--regenerate-outro` | Re-render that bookend and replace the cached clip. |
| `--regenerate-brand-assets` | Both of the above. |

The cache invalidates **itself** when anything that actually defines the bookend changes — the logo
file's bytes, the gradient stops, the declared duration, the resolution or the fps. So regenerating by
hand is only for a deliberate redesign of the bookend itself.

Two things bypass the cache, by design:

- `meta.introTitle` / `meta.outroTitle` in the IR personalise a bookend for one film. That film gets a
  one-off render and nothing is cached, because it is no longer the brand's standard opening.
- A missing or corrupt cache falls back to rendering rather than failing the build.

Inspect or clear the cache with:

```
scripts/asset_cache.py --brand <id> --list
scripts/asset_cache.py --brand <id> --invalidate intro
```

`--dry-run` states `will REUSE from cache` or `will RENDER` per bookend before you commit to a render.

---

## Music: brand values and practical notes

**Music never competes with narration.** It is a floor under the film, not a layer over it. Everything
comes from `brand.video.music` and the builder implements it — you do not hand-tune the mix. For
Channelplay:

| Setting | Value | What it does |
|---|---|---|
| `targetLufs` | −23.0 | The bed's own integrated loudness. Well under the voice. |
| `duckUnderVoiceDb` | −18.0 | How far the bed drops while narration plays, via a sidechain compressor keyed off the VO. |
| `fadeInSec` / `fadeOutSec` | 1.0 / 2.0 | In under the intro, out across the outro. |
| `mood` | confident, understated, corporate-modern | What to look for in a track. |
| `avoid` | dramatic orchestral, lo-fi hiphop, aggressive EDM | What the brand will reject. |

The finished master is measured against `brand.video.voiceover.targetLufs` (−16.0 LUFS for
Channelplay), ±2 LU, and against a −1.0 dBTP true-peak ceiling. `AUDIO.LOUDNESS` and `AUDIO.CLIPPING`
are warnings, because a platform will re-normalise anyway — but a film that arrives 5 LU hot is a film
that sounds wrong next to everything else the client plays that day.

- **`music.enabled: true` with `music.file: null` builds a narration-only mix and warns.** That is the
  Channelplay default state — there is no track in the profile. Either supply one via `music.file` in
  the IR (or `brand.video.music.file`), or set `music.enabled: false` in the IR and say in the report
  that the film ships without a bed.
- **Ask before sourcing a track.** Licensing is the user's decision, not yours. Never reach for a file
  the user did not name.
- **A bed under a 20-second film is usually worse than silence.** It has no time to establish and it
  fades out as it arrives.

---

## Reporting the film

Give the user, in this order:

1. **The paths** (absolute): the `.mp4`, the `.srt`, the `.mp4.timeline.json`, and the IR beside them.
2. **The duration**: measured runtime against `meta.durationTargetSec`, with the drift.
3. **The scene inventory** — a numbered list: id, role, visual (slide N / motion template / image),
   duration, and the one-line purpose of the scene.
4. **The caption file** — cue count, longest line, and whether captions are burned in or sidecar.
5. **The validation summary** — `errors / warnings / info` counts and the exit code.
6. **Every warning you consciously accepted, with the reason.** One line each. If there are none, say so.
7. Anything you could not verify — a missing music bed, an unavailable logo variant, a claim in the
   narration the user still has to confirm.
