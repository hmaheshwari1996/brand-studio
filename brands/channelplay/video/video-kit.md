# Channelplay video kit

What exists today for Channelplay films, what the machine enforces, and what is still missing.
The enforceable half of this document lives in `brands/channelplay/brand.json` under `video`;
this file is the prose around it. When the two disagree, `brand.json` is right and this file is
out of date — fix it.

Status as of 2026-07-30: **the kit is generated, not supplied.** No intro plate, no outro plate, no
licensed music and no reference film has been handed over. Everything below that says "generated"
is the plugin standing in for an asset the brand owner has not provided yet. See
[Open items](#open-items).

---

## Output format

| Setting | Value | Enforced as |
|---|---|---|
| Resolution | 1920 x 1080 | `VIDEO.RESOLUTION` |
| Frame rate | 30 fps | `VIDEO.FPS` |
| Container / codecs | mp4, `libx264` video, `aac` audio | `VIDEO.CODEC` |
| Title-safe margin | 5% of each edge | `VIDEO.SAFE_MARGIN` |
| Transition | 0.4s crossfade | — |
| Scene hold | 3.0s min, 5.0s default, 12.0s max | `VIDEO.DURATION` |

Anything legible — text, logo, a number the viewer is meant to read — stays inside the safe margin.
The margin exists because a film gets cropped by a video wall, a Teams window and an Instagram
frame, and none of them ask first.

---

## Intro and outro

Both are **generated**, from the brand tokens, by `scripts/build_video.py` through the shared HTML
motion templates in `templates/video/`. There is no Channelplay-specific animation: the plates are
brand-agnostic geometry filled with Channelplay colour, type and logo, exactly like the deck
grammar.

| | Intro | Outro |
|---|---|---|
| `type` | `generated` | `generated` |
| `style` | `blue-gradient-logo-reveal` | `navy-lockup-cta` |
| Duration | 3.0s | 3.5s |
| Template | `templates/video/intro.html` | `templates/video/outro.html` |
| Source file | none — `file: null` | none — `file: null` |

The intro is the blue gradient (`#0F0A6C → #0000FF` at 135°) with the reversed logo revealing over
it. The outro is the navy lockup carrying the film's single call to action. Frames are rendered
deterministically in headless Chrome from a `?t=<0..1>` parameter, so a rebuild produces identical
pixels.

**When real plates arrive**, drop them in this directory and point `video.intro.file` /
`video.outro.file` at them, relative to the brand directory. `build_video.py` then plays the supplied
clip instead of rendering the template — `type` becomes `file`, and `durationSec` stops mattering
because the clip's own length wins. Nothing else in the pipeline changes.

A film with neither an intro nor an outro trips `VIDEO.NO_INTRO` / `VIDEO.NO_OUTRO`. Both default to
on in the Video IR (`"intro": true, "outro": true`).

---

## Music

| Setting | Value |
|---|---|
| Enabled | yes |
| Track | **none supplied** — `music.file` is `null` |
| Bed loudness | -23.0 LUFS integrated |
| Duck under voice | -18.0 dB |
| Fade in / out | 1.0s / 2.0s |

**Mood:** confident, understated, corporate-modern.
**Avoid:** dramatic orchestral, lo-fi hiphop, aggressive EDM.

The loudness pair is the important part. Voiceover is normalised to **-16.0 LUFS**, the bed to
**-23.0 LUFS**, and the bed ducks a further 18 dB while anyone is speaking. That is a deliberately
wide gap: this is a corporate explainer, not a trailer, and the music is there so the silence between
sentences is not dead air. If the music is noticeable as music, it is too loud.

`music.enabled` is `true` with `music.file` `null`, which is an honest description of the current
state — the brand wants a bed and does not have one. The builder renders the film with voice only
and the validator reports the shortfall through `AUDIO.LOUDNESS` / `AUDIO.SILENCE` rather than
pretending the mix is finished. **Do not set `music.enabled` to `false` to silence the warning.**
That records a decision nobody made.

Licensing: a track has to be cleared for client-facing commercial use before it goes in this
directory. "Free for YouTube" is not that.

---

## Voiceover

| Setting | Value |
|---|---|
| Engine | `say` (macOS system synthesiser) |
| Voice | Samantha |
| Rate | 165 wpm |
| Loudness | -16.0 LUFS integrated |

`say` is a review-cut voice. It is fine for agreeing pacing, timing and script, and it is not fine
for a film a client keeps — it fluffs Indian place names, retail jargon and any acronym it has not
seen. Budget a human read or a commercial TTS pass before delivery, and say so when you hand over a
`say` cut so nobody circulates it by mistake.

165 wpm is also what `--dry-run` uses to estimate scene durations before anything renders, so the
timeline you see without audio is close to the one you get with it.

---

## Captions

| Setting | Value | Enforced as |
|---|---|---|
| Required | yes | `CAPTION.MISSING` |
| Delivery | sidecar `.srt`, not burnt in | — |
| Font | Poppins Medium, 28pt | — |
| Colour | `#FFFFFF` on `#0F0A6C` at 82% opacity | — |
| Position | bottom-centre, 8% up from the bottom edge | — |
| Max line length | 42 characters | `CAPTION.LINE_LEN` |
| Max lines | 2 | `CAPTION.LINE_COUNT` |
| Min on-screen time | 1.2s | `CAPTION.TOO_FAST` |

Captions are an accessibility requirement, not a style choice, and the field workforce this brand
talks to watches on a phone with the sound off. A sidecar is the default because it stays editable
and translatable; burning in is a per-film decision (`captions.burnIn`), and it needs `libass` in
the local ffmpeg.

42 characters and two lines is a hard ceiling, not a target. A caption longer than that is a
sentence that should have been two scenes. `CAPTION.GAP` and `CAPTION.OVERLAP` catch timing that
drifted off the scenes it belongs to.

---

## Storyline

`video.storyline.required` is `true`. Every film follows the six-beat arc, in order, and the
validator reports a missing or out-of-order beat as `STRUCTURE.STORYLINE`. Each scene in the Video
IR carries exactly one `role` from this list.

| Beat | `role` | What it has to accomplish | Failure mode |
|---|---|---|---|
| Hook | `hook` | Earn the next eight seconds. One concrete image or one number from the viewer's world. | Opening on who we are. Nobody has agreed to care yet. |
| Problem | `problem` | Name the viewer's problem in their words, specifically enough that they recognise it as theirs. | A generic "retail is changing" that describes everyone and therefore no one. |
| Approach | `approach` | Say what we actually do about it. Mechanism, not adjectives. | "Our proven methodology." That is a claim wearing a mechanism's clothes. |
| Proof | `proof` | Evidence, numeric wherever possible: stores, SKUs, states, headcount, weeks. | An unattributed percentage. A number without a denominator proves nothing. |
| Outcome | `outcome` | What is different for the client afterwards, in their terms — sales, coverage, compliance, speed. | Restating the approach in the past tense. |
| Call to action | `call-to-action` | One concrete next step, singular. A named thing a named person can do this week. | Three options, or "get in touch". |

The brand's own rules, from `video.storyline.rules`:

1. Open on the client's problem, never on Channelplay's credentials.
2. One idea per scene. If a scene needs two sentences of setup, it is two scenes.
3. Proof is specific and numeric wherever possible: stores, SKUs, states, headcount.
4. Close with a single, concrete next step.

Two beats may span more than one scene when the material genuinely needs it — proof usually does —
but the order never changes, and no beat is skipped. A film without a `proof` scene is a brochure.

---

## Voice on screen and in narration

The deck voice rules apply to narration and captions unchanged, and both are checked:

- **No exclamation marks.** `VOICE.EXCLAMATION`.
- **Sentence case**, except the eyebrow, which is upper.
- **No placeholder copy.** Everything in `voice.forbiddenPhrases` — "Lorem ipsum", "Person Name",
  "This is the title text right here" — is `CONTENT.PLACEHOLDER`, and it is an error, not a warning,
  because it is the one defect a client will definitely notice.
- Plain, operational, confident, never salesy. Present tense, active voice.

---

## Reference material

`video.referenceVideos` is `[]`.

That is a recorded state, not an answer: **nobody has asked the brand owner whether reference films
exist.** Until somebody does, every Channelplay film is being built from a written profile alone,
which is the single most common cause of "that isn't how our videos look". Ask before writing a word
of script; if films come back, drop them in this directory and list them in `video.referenceVideos`
relative to the brand directory.

---

## Open items

Everything here needs the brand owner. None of it blocks a review cut; all of it blocks a delivery.

| # | Item | Why it matters | Consequence today | Where it lands |
|---|---|---|---|---|
| 1 | **Intro plate** — the real animated open, as a source clip | The generated reveal is the plugin's guess at a title sequence. If a real one exists, every film shipped without it is off-brand at second zero. | `intro.type` stays `generated`; films open on the blue-gradient logo reveal. | `brands/channelplay/video/`, then `video.intro.file` + `type: "file"` |
| 2 | **Outro plate** — the real close, with the standing lockup | Same, at the end, where the call to action sits. | `outro.type` stays `generated`; films close on the navy lockup. | `brands/channelplay/video/`, then `video.outro.file` + `type: "file"` |
| 3 | **Licensed music** — one or two cleared beds matching the stated mood | `music.enabled` is true and `music.file` is null: the brand wants a bed and has none. Every film currently ships voice-only. | Silence between sentences; `AUDIO.*` warnings on every build. | `brands/channelplay/video/`, then `video.music.file` |
| 4 | **Reference videos** — any film the brand would be happy for a new one to sit next to | Written rules cannot capture pacing, density, how much motion is too much, or how the brand sounds. | Films are built from the profile alone. | `brands/channelplay/video/`, then `video.referenceVideos[]` |
| 5 | **A human or commercial voice** for delivery cuts | `say` is a review voice and mispronounces Indian place names and retail jargon. | Delivery cuts need a re-record before they leave the building. | `video.voiceover.engine` / a supplied audio track |

Each of these is a question for the brand owner, and each answer — including "we don't have one" —
gets a dated entry in `brands/channelplay/LEARNED.md`. An unasked question and an answered "none"
look identical in `brand.json`, which is exactly why the ledger exists.
