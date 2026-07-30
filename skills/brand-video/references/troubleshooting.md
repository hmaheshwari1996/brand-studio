# Troubleshooting

Every violation `validate_video.py` can emit, every way `build_video.py` can fail, what actually causes
it, and the fix. Then the hard cases that do not map to a single id.

**Look the id up here rather than guessing from the message.** Several of these have a cause that has
nothing to do with what the text says.

- [Reading a violation](#reading-a-violation)
- [Diagnostic commands](#diagnostic-commands)
- [VIDEO.\*](#video)
- [AUDIO.\*](#audio)
- [CAPTION.\*](#caption)
- [CONTENT, VOICE, STRUCTURE](#content-voice-structure)
- [Build failures](#build-failures)
- [The hard cases](#the-hard-cases)
- [When the validator itself fails](#when-the-validator-itself-fails)
- [Running the validator](#running-the-validator)
- [Watching the film — step 7](#watching-the-film--step-7)
- [The learn protocol, in full](#the-learn-protocol-in-full)
- [Non-negotiables](#non-negotiables)

---

## Reading a violation

Every violation carries six fields. `found` and `expected` are the measurement; `rule` is why the brand
cares; `fix` is the concrete action. In `--format human` they are laid out per finding; in JSON they
are exactly the FROZEN CONTRACT C keys.

| Exit | Meaning |
|---|---|
| `0` | `counts.error == 0`. Ships. Warnings may remain, each with a stated reason. |
| `2` | At least one error. Blocking. |
| `1` | The validator could not run. Fix the invocation, not the film. |

**The severity split is deliberate.** Errors are things that are objectively broken or that must never
reach a client: wrong resolution, no audio at all, no captions when the brand requires them,
overlapping cues, placeholder copy, exclamation marks. Everything else is a warning or info, because
video has legitimate exceptions — photographic footage, a deliberately long cut, a client-supplied
music bed at its own level. Warnings still need a reason before you leave them.

**Fix the IR and rebuild. Never patch the mp4 or hand-edit the SRT** — the next build regenerates both.

---

## Diagnostic commands

```sh
ROOT="${CLAUDE_PLUGIN_ROOT:?set CLAUDE_PLUGIN_ROOT to the brand-studio directory, or run this from Claude Code}"
PY="$HOME/.cache/brand-studio/venv/bin/python"
F="$WORK/film.mp4"

# full human report, sidecars attached automatically
"$PY" "$ROOT/scripts/validate.py" "$F" --brand channelplay --format human

# the specialist, with explicit sidecars and a denser frame sample
"$PY" "$ROOT/scripts/validate_video.py" "$F" --brand channelplay \
      --srt "${F%.mp4}.srt" --timeline "$F.timeline.json" --frames 24 --format human

# container facts
ffprobe -v error -select_streams v:0 \
  -show_entries stream=codec_name,pix_fmt,width,height,r_frame_rate,avg_frame_rate \
  -show_entries format=duration,format_name,bit_rate -of json "$F"
ffprobe -v error -select_streams a:0 \
  -show_entries stream=codec_name,channels,sample_rate -of json "$F"

# loudness and true peak (I: integrated LUFS, Peak: dBTP)
ffmpeg -hide_banner -nostats -i "$F" -af ebur128=peak=true -f null - 2>&1 | tail -14

# one frame at a chosen second
ffmpeg -v error -y -ss 12.5 -i "$F" -frames:v 1 /tmp/f.png && open /tmp/f.png

# what the build actually resolved
"$PY" -m json.tool "$F.timeline.json" | head -80
```

---

## VIDEO.*

### `VIDEO.RESOLUTION` — error

**Means:** the encoded frame size is not `brand.video.resolution`.

**Real cause:** almost always a `--deck` whose slide size is not 16:9. A 4:3 deck rasterises to
1440 × 1080 and the pad step keeps the aspect, so the film comes out at the wrong size. Occasionally a
hand-run ffmpeg step downstream of the builder.

**Fix:** fix the deck's slide size in PowerPoint (Design → Slide Size → Widescreen 16:9) and rebuild
it with `build_deck.py`, then re-render the film. Do not scale the mp4 afterwards — you would be
resampling type. If a brand genuinely ships vertical or square video, that is a `brand.json` change to
`video.resolution`, made through the learn protocol.

### `VIDEO.FPS` — error

**Means:** the average frame rate is not `brand.video.fps`.

**Real cause:** an `--anim-fps` override that leaked into the final render, or a re-encode outside the
builder. `--anim-fps` only controls how many unique motion frames Chrome renders; it must never change
the output rate, so if it did, the file was not produced by `build_video.py`.

**Fix:** re-render with `build_video.py` and no rate overrides. If the source is a supplied clip at a
different rate, re-encode it to the brand rate before referencing it.

### `VIDEO.CODEC` — warn

**Means:** one of four things — the video codec, the pixel format, the container, or the audio codec
does not match the brand profile.

**Real cause:**

- *pix_fmt* is the common one. Anything other than `yuv420p` fails to decode on some Android players
  and on older QuickTime, regardless of how correct the file looks locally.
- *container* fires when the extension and the actual muxer disagree — an `.mp4` that is really a MOV.
- *audio codec* fires on a supplied music or VO file that was passed through without re-encoding.

**Fix:** re-render with the builder, which sets all four from the profile. To repair an existing file
without touching the picture:

```sh
ffmpeg -i in.mp4 -c:v libx264 -pix_fmt yuv420p -crf 18 -preset medium -c:a aac -b:a 192k out.mp4
```

### `VIDEO.NO_INTRO` / `VIDEO.NO_OUTRO` — warn

**Means:** no frame sampled in the intro (or outro) window is dominated by a brand hero colour within
ΔE 10.

**Real cause:**

0. **The intro is a gradient — see [gradient bookends](#gradient-bookends-that-look-perfect-and-still-warn).
   For Channelplay this fires on essentially every film and is a false positive.** Check this first.
1. **`intro: false` / `outro: false` in the IR** while the brand declares one. The check reads the
   timeline first, so this only fires when the timeline says the bookend exists.
2. **A supplied `brand.video.intro.file`** that is a live-action or photographic clip. It genuinely has
   no dominant brand colour and the check cannot know it is deliberate.
3. **A generated intro that came out wrong** — usually a missing logo variant leaving a bare field.

**Fix:** for (0) and (2), accept the warning with a stated reason. For (1), set the flag true and
rebuild. For (3), open the first frame and look:

```sh
ffmpeg -v error -y -ss 1.0 -i "$F" -frames:v 1 /tmp/intro.png && open /tmp/intro.png
```

Then check the build warnings for `logo variant '<name>' not found`.

Note the asymmetry: `VIDEO.NO_OUTRO` usually does *not* fire alongside it, because the generated outro
uses the flat `navy` theme (`#0F0A6C`) which matches a hero exactly, while the intro uses `gradient`.
An intro warning with no outro warning is the signature of this false positive.

### `VIDEO.DURATION` — warn

**Means:** the rendered runtime is more than 1.0s outside the window the timeline predicted.

**Real cause:** the mp4 and the timeline are **out of sync** — the timeline is from an older build, or
the mp4 was trimmed or concatenated after the fact. It is not about `meta.durationTargetSec`; target
drift is reported by the dry run, not by this check.

**Fix:** rebuild both together. If you are validating an externally edited cut, re-render it from the
IR instead of validating an edit the IR does not describe.

### `VIDEO.SAFE_MARGIN` — warn

**Means:** content crosses the safe band — for Channelplay, the outer 5% on every edge — in one or more
sampled frames. The report names which edges and the worst inset.

**Real cause:**

- **A deck slide whose element sits outside the deck grammar's margins.** The deck validator uses the
  deck's margins; the video check uses `brand.video.safeMarginPct`, which is stricter. A slide can pass
  as a slide and fail as a frame.
- **A motion template that pads with a raw pixel value** instead of `var(--safe-x)` / `var(--safe-y)`.
- **A full-bleed image** — but frames that are more than 60% content are exempt automatically, so if
  one is reported it is not actually full-bleed.

**Fix:** for a deck slide, fix it in the deck IR and rebuild the deck. For a template, use the safe-area
variables. Look at the frame the violation names before changing anything:

```sh
ffmpeg -v error -y -ss <time from the violation> -i "$F" -frames:v 1 /tmp/edge.png
```

### `VIDEO.OFF_PALETTE` — warn

**Means:** a colour holding at least 8% of a frame is further than ΔE 12 from every approved brand
colour. Greys, near-white, near-black and skin tones are already exempt; up to 12 clusters are
reported and the rest are summarised.

**Real cause, in order of likelihood:**

1. **A gradient.** A gradient field has no dominant flat colour: quantisation lands on intermediates
   between the two stops, and an intermediate matches neither. A blue-gradient slide reliably reports
   something like `#0604B0`, *nearest `blue.700 #0000CC` at ΔE 13.6*. Same root cause as
   [gradient bookends](#gradient-bookends-that-look-perfect-and-still-warn).
2. **Scaling and encoding softening a flat colour.** The Ken Burns push-in resamples every frame, and
   x264 then quantises the chroma, so the boundary of a large flat blue block becomes a band of
   near-blues. A draft render at `--crf 26` trips this noticeably more than a final at `--crf 18`;
   re-check at the shipping CRF before chasing it.
3. **Photographic content** — see [the hard cases](#photographic-frames-tripping-the-palette-check).
4. **A superseded template colour.** `#0000D5` and its family are ΔE-close to `#0000FF` but not equal;
   if a slide was built from the old master they show up here. Cross-check against
   `brand.color.superseded.map` and replace with the canonical hex in the deck, never by adding the old
   hex to the palette.
5. **A chart series colour that is not from `colorRules.chartSeries`.** Charts fill large flat areas,
   so an off-brand series is the fastest way to trip this.
6. **A client logo or product shot** in an otherwise branded frame. Legitimate, and a warning you keep
   with a stated reason.

**Fix:** fix (4) and (5) in the deck or the template. Re-check (2) at the final CRF. Accept (1), (3)
and (6) explicitly in the report.

**Read the reported ΔE.** Anything in the 12–15 band against a *neighbouring shade of the same brand
colour* (`blue.700`, `blue.400`) is almost always (1) or (2) — a rendering artifact of a colour that is
already correct. A large ΔE against an unrelated token is a genuinely off-brand colour.

---

## AUDIO.*

### `AUDIO.MISSING` — error

**Means:** the film declares narration or music and has no audio stream at all.

**Real cause:** `--no-audio` was passed to the final build. Almost always this and nothing else.

**Fix:** rebuild without `--no-audio`. If `say` is unavailable on this machine, the film is not
shippable from here — say so rather than shipping a silent master. If the film genuinely ships silent,
set `music.enabled: false` in the IR *and* `brand.video.voiceover.enabled: false`, which is a
`brand.json` change through the learn protocol.

### `AUDIO.LOUDNESS` — warn

**Means:** integrated loudness is more than 2 LU from the target (−16.0 LUFS for Channelplay), or the
stream is digital silence (`-inf`).

**Real cause:** see [loudness drift](#loudness-drift). `-inf` specifically means the mix is empty — the
VO and music inputs both failed to resolve while the audio stream was still created.

**Fix:** re-master without touching the picture:

```sh
ffmpeg -i in.mp4 -af loudnorm=I=-16:TP=-1:LRA=11 -c:v copy -c:a aac -b:a 192k out.mp4
```

Prefer rebuilding from the IR — the builder normalises correctly, and a re-master leaves the timeline
describing a file that no longer exists.

### `AUDIO.CLIPPING` — warn

**Means:** true peak is above −1.0 dBTP.

**Real cause:** a supplied music bed that is already mastered loud, stacking with normalised narration.
The `say` output alone will not do this.

**Fix:** the `loudnorm` command above sets `TP=-1` and fixes it in one pass. If it recurs on every film
with the same bed, the bed is the problem: normalise the source track once and point
`brand.video.music.file` at the normalised copy.

### `AUDIO.SILENCE` — info

**Means:** more than 1.5s of silence at the head or the tail.

**Real cause:** normal and usually correct. A generated 3.0s intro with no music bed is silent by
construction, and so is a 3.5s outro. It is informational precisely because the brand's own defaults
produce it.

**Fix:** none needed when music is disabled — note it and move on. It becomes real when music *is*
enabled and the head is still silent: that means the bed did not resolve. Check the build warnings for
`music track not found` or `no file supplied; skipped`.

---

## CAPTION.*

### `CAPTION.MISSING` — error

**Means:** `brand.video.captions.required` is true and there is neither an SRT nor an embedded subtitle
stream.

**Real cause, in order:**

1. **The SRT was not passed to the validator.** The dispatcher attaches `film.srt` only when it sits
   beside `film.mp4`. Moving the mp4 without the sidecar produces this error on a perfectly captioned
   film.
2. No scene carries `caption` or `vo` text, so the builder had nothing to write. It warns at build time
   with `captions are enabled but no scene carries caption or vo text`.

**Fix:** for (1), keep the three output files together, or pass `--srt` explicitly. For (2), write the
narration.

### `CAPTION.OVERLAP` — error

**Means:** two cues overlap in time, or a cue ends before it starts.

**Real cause:** a hand-edited SRT. The builder cannot produce this — it lays cues end to end inside
each scene's narration window.

**Fix:** stop editing the SRT. Fix the `caption` text in the IR and rebuild.

### `CAPTION.LINE_LEN` — warn

**Means:** a line exceeds `maxCharsPerLine` (42).

**Real cause:** a single word longer than the limit — a URL, a hyphenless compound, a long product code.
The wrapper breaks on spaces and hard-splits only as a last resort. Otherwise: a hand-edited SRT.

**Fix:** rewrite the caption to avoid the unbreakable token. `channelplay.in/retail-execution-programme`
in a caption is unreadable at 28pt anyway; put it in the outro `meta.contact`.

### `CAPTION.LINE_COUNT` — warn

**Means:** a cue has more lines than `maxLines` (2).

**Real cause:** the builder respects the limit, so this is a hand-edited SRT or a foreign one.

**Fix:** rebuild from the IR.

### `CAPTION.TOO_FAST` — warn

**Means:** a cue is held under `minDurationSec` (1.2s).

**Real cause:** a scene whose `caption` splits into more cues than its narration window can hold at the
minimum. A 5-second scene with four sentences of caption cannot give each one 1.2s. The builder warns
at build time — `N caption cue(s) do not fit Xs at the brand minimum` — and splits evenly anyway.

**Fix:** shorten the `caption` for that scene, or split the scene. Do **not** lengthen `holdSec` to buy
caption room: that leaves a still frame on screen to service a subtitle.

### `CAPTION.GAP` — info

**Means:** over 3s with no caption while the timeline says narration is running.

**Real cause:** a scene with `vo` but an empty `caption`, or a caption so short its cues finish long
before the narration does. It cannot fire on a silent scene — the check is bounded by the narration
windows.

**Fix:** give the scene a caption that spans the claim rather than the first clause. Info-level, and
still worth fixing: it marks exactly the seconds a muted viewer receives nothing.

---

## CONTENT, VOICE, STRUCTURE

### `CONTENT.PLACEHOLDER` — error

**Means:** a phrase from `brand.voice.forbiddenPhrases` appears in the shipped caption text.

**Real cause:** narration drafted around scaffolding — `Person Name`, `Lorem ipsum`, `Click here`,
`This is placeholder copy` — that survived into the `caption`. It is checked on captions because that is
what ships as text.

**Fix:** rewrite the cue in the IR and rebuild, so the sidecar and any burned-in copy stay identical.
Never edit only the SRT.

### `VOICE.EXCLAMATION` — error

**Means:** a character from `brand.voice.forbiddenChars` — `!` for Channelplay — appears in a cue.

**Real cause:** enthusiasm. Occasionally a quotation that genuinely contains one.

**Fix:** delete the mark. If the line needs force, make the claim specific — a number does the work.
For a genuine quotation, paraphrase; the brand has no exception for quoted exclamations, and adding one
would be a `brand.json` change.

### `STRUCTURE.STORYLINE` — warn

**Means:** one of four things about the scene roles, and the message says which:

| Message | Cause |
|---|---|
| *no scene declares a role* | `role` omitted throughout the IR. |
| *missing: …* | A stage of the arc has no scene. |
| *'X' first appears after 'Y'* | First appearances are out of arc order. |
| *unrecognised role(s)* | A role not in `brand.video.storyline.arc` — often a typo, or `cta` instead of `call-to-action`. |

**Real cause of the interesting one — out of order:** almost always a film that opens on credentials.
`approach` lands before `problem` because the first two scenes introduce the agency. That is the exact
failure the brand's first storyline rule exists to prevent: *open on the client's problem, never on
Channelplay's credentials.*

**Fix:** reorder the scenes so each stage first appears in arc order, or add the missing beat. Repeats
are fine — `hook, problem, problem, approach, approach, proof, outcome, call-to-action` passes cleanly.
Only drop a stage from `brand.video.storyline.arc` if the brand genuinely no longer uses it, through
the learn protocol.

---

## Build failures

`build_video.py` exits non-zero with a `BuildError` on stderr. None of these produce a partial file you
can ship.

| Message | Cause | Fix |
|---|---|---|
| `IR file not found` / `is not valid JSON` | Path or syntax | `"$PY" -m json.tool film.video.json` |
| `kind must be one of deck-video / explainer` | Typo in `kind` | Only those two values. |
| `'scenes' must be a non-empty array` | Empty IR | Author the scenes. |
| `visual.kind must be one of slide / motion / image` | Typo, or `visual` omitted | Every scene needs a `visual` object. |
| `visual.kind is 'slide' but visual.slide is null` | Slide scene with no index | 1-based index into the deck. |
| `visual.kind is 'image' but visual.src is empty` | Image scene with no path | Set `src`. |
| `scene 'X' asks for slide N but deck.pptx has M slide(s)` | Index past the end, or the deck lost slides | `visual.slide` is 1-based; re-check every index after any deck edit. |
| `scene 'X': image not found: <src>` | Unresolvable `visual.src` | Resolved against the IR dir, the brand dir, the plugin root, then cwd. |
| `N scene(s) reference deck slides but no .pptx was given` | Missing `--deck` | Pass it. |
| `no brand: neither --brand nor the IR's 'brand' key is set` | Missing brand | Set `brand` in the IR. |
| `required tool(s) not found` | Missing binary | `brew install ffmpeg poppler`, `brew install --cask libreoffice`. |
| `LibreOffice (soffice) is required` | `deck-video` without soffice | Install LibreOffice, or switch to `explainer`. |
| `LibreOffice produced no PDF` | The deck will not open | Open it in PowerPoint; if it opens there, rebuild it from the deck IR. |
| `slide rasterisation mismatch` | Poppler missing or a stale work dir | See [soffice render mismatch](#soffice-render-mismatch). |
| `Google Chrome (or Chromium) is required` | `motion` scenes without Chrome | Install Chrome, or set `BRAND_STUDIO_CHROME`. |
| `motion template 'X' not found. Available: …` | Bad `visual.template` | Use a listed stem, or add a template (see `pipelines.md`). |
| `template X has no {{BRAND_VARS}} marker` | A hand-written template | Add the marker in `<head>`. |
| `headless Chrome produced no screenshot` | Template JS threw | Open the HTML in the work dir directly with `?t=1`. |
| `the brand asks for voiceover engine 'say' but /usr/bin/say was not found` | Not macOS | `--no-audio`, or supply pre-rendered audio. |
| `unsupported voiceover engine 'X'` | Profile changed | `say` is the only engine implemented. |
| `music track not found: X` | Bad `music.file` path | Resolved against the IR dir, the brand dir, the plugin root, then cwd. |
| `captions.burnIn is true but this ffmpeg has no 'subtitles' filter` | ffmpeg built without libass | `brew reinstall ffmpeg`, or `--allow-no-burn-in` to ship the sidecar only. |
| `ffmpeg produced no output` | The filter graph failed | Re-run with `--keep-temp` and read the ffmpeg stderr. |

**Warnings the build prints and you must not ignore:**

- `scene 'X' holds for Ns, above the brand maximum` — editorial. Split the scene.
- `music is enabled but neither the IR nor the brand supplies a track` — the Channelplay default state.
  Supply a bed or set `music.enabled: false` and say so in the report.
- `logo variant 'X' not found` — the motion templates and the generated intro/outro will render without
  a logo.
- `no assets/fonts directory` / `no font file found for weight N` — see
  [missing Poppins](#missing-poppins-in-rendered-frames).
- `pdftoppm not found; falling back to LibreOffice PNG export` — only the first slide will export on
  most builds. `brew install poppler`.

---

## The hard cases

### Clipped narration

**Symptom:** the last word of a scene is cut off, or a scene changes while the voice is still speaking.
Nothing in the validator reports it — the audio measures as normal audio.

**Why it should be impossible:** scene duration is `max(holdSec, slideHoldSec.min, voDuration + 0.4)`,
and `voDuration` is measured from the rendered wav with `ffprobe`. The 0.4s tail exists for exactly
this. So when it happens, one of the inputs is wrong.

**Real causes:**

1. **The mp4 and the timeline disagree.** The film was trimmed, concatenated or re-encoded after the
   build. `VIDEO.DURATION` usually fires alongside.
2. **The transition eats it.** Narration starts at the scene's `visibleStartSec` — *after* the incoming
   crossfade completes — but the 0.4s tail is measured from `startSec`. So whenever `holdSec` is what
   won the duration, the real tail is `holdSec − transitionSec − voDuration`, which can fall well under
   the intended 0.4s. A measured example: `holdSec: 8`, narration 7.37s, transition 0.4s → an effective
   tail of **0.23s**. Audible, but the picture is already dissolving under the last word.
3. **A `say` voice that trails off** on a sentence ending in a soft consonant — the waveform is there,
   the intelligibility is not.

**Diagnose:**

```sh
"$PY" -c "
import json, sys
tl = json.load(open(sys.argv[1]))
for e in tl['elements']:
    if e['kind'] != 'scene': continue
    if e.get('voEndSec') is None: continue
    print('%-6s vo ends %7.2f  scene ends %7.2f  tail %5.2f%s'
          % (e['id'], e['voEndSec'], e['endSec'], e['endSec'] - e['voEndSec'],
             '   <-- TIGHT' if e['endSec'] - e['voEndSec'] < 0.45 else ''))
" "$F.timeline.json"
```

Then listen to the join:

```sh
ffmpeg -v error -y -ss <voEnd - 2> -t 4 -i "$F" /tmp/join.wav && afplay /tmp/join.wav
```

**Fix:** for (1), rebuild. For (2), add a second to that scene's `holdSec` so the narration finishes
before the crossfade begins — this is the one legitimate reason to raise `holdSec` above what the
visual needs. For (3), end the sentence on a stronger word, or change the voice.

### Loudness drift

**Symptom:** `AUDIO.LOUDNESS` on a film the builder normalised, sometimes several LU out.

**Real causes:**

1. **A music bed that is not ducking.** `duckUnderVoiceDb` drives a sidechain compressor keyed off the
   narration. A bed that is already heavily limited has no dynamic range for the sidechain to act on,
   so it sits at full level under the voice and pushes the integrated figure up.
2. **A very short film.** EBU R128 integrated loudness is unstable under about 10 seconds of programme.
   A 12-second film with a 3s silent intro is measuring roughly 9 seconds of content.
3. **A long silent head or tail.** Silence drags the integrated figure down. `AUDIO.SILENCE` normally
   fires alongside and is the real signal.
4. **The narration is quiet in the mix** because `music.targetLufs` was raised without lowering the
   duck. Check the timeline's `audio.music` block against `audio.voiceover`.

**Diagnose:**

```sh
ffmpeg -hide_banner -nostats -i "$F" -af ebur128=peak=true -f null - 2>&1 | tail -14
# I:  integrated LUFS      LRA: loudness range      Peak: dBTP
```

An `LRA` above about 14 with music enabled means the ducking is not working. An `LRA` under 3 means
something is over-limited.

**Fix:** re-master with `loudnorm=I=-16:TP=-1:LRA=11 -c:v copy`, or better, fix the cause: a
narration-only mix (`music.enabled: false`) always lands on target, and a properly pre-normalised bed
does too. For (2), accept it and say why — a 12-second film cannot be measured to R128 meaningfully.

### soffice render mismatch

**Symptom:** `slide rasterisation mismatch for deck.pptx: the deck has 16 slides but 1 PNG(s) were
produced`. Or the film renders but shows the wrong slides.

**Real causes, in order:**

1. **Poppler is not installed.** Without `pdftoppm` the builder falls back to LibreOffice's own PNG
   export, which exports **only the first slide** on most builds. This is by far the most common cause
   and the build warns about it before failing.
2. **A concurrent LibreOffice.** LibreOffice refuses a second headless instance sharing a user profile.
   The builder isolates its profile inside the work directory, so this only bites when a `--work-dir`
   is reused while another build is running.
3. **A stale `--work-dir`.** Slide PNGs from a previous build with a different slide count are still
   there and get picked up.
4. **The deck does not open cleanly** — a corrupt embed, a linked image LibreOffice cannot resolve.

**Fix:**

```sh
brew install poppler                    # (1) — the fix in almost every case
rm -rf "$WORK/video-work"               # (3)
soffice --headless --convert-to pdf --outdir /tmp "$WORK/deck.pptx"   # (4) — does it convert alone?
```

For (2), close other LibreOffice processes or drop `--work-dir` so each build gets its own temp dir.

**The subtler variant:** the count matches but the *rendering* is wrong — a chart missing, a gradient
flat, a table's borders gone. LibreOffice's PowerPoint fidelity is good, not perfect. Check the slide
PNGs directly before blaming the film:

```sh
"$PY" "$ROOT/scripts/build_video.py" --ir film.video.json --deck deck.pptx \
      --out /tmp/probe.mp4 --work-dir /tmp/probe-work --keep-temp --no-audio --dry-run
# then build for real and inspect:
open /tmp/probe-work/slides/
```

If a slide renders wrong in LibreOffice, simplify the construct in the deck IR. The film is only ever
as good as the rasterisation.

### Missing Poppins in rendered frames

**Symptom:** the film's type is not Poppins. Usually Liberation Sans or DejaVu — wider, with different
letterforms and noticeably different line breaks. The deck validates clean; the film looks wrong.

**This is two different bugs with the same appearance.**

**In `deck-video`:** the `.pptx` references the family by name. LibreOffice substitutes at PDF
conversion if the font is not installed **system-wide**. The brand's `assets/fonts/*.ttf` are *not*
enough — they are the plugin's copy, not the OS's.

```sh
ls ~/Library/Fonts | grep -i poppins        # what the OS can see
fc-list 2>/dev/null | grep -i poppins       # if fontconfig is available
cp "$ROOT/brands/channelplay/assets/fonts/"*.ttf ~/Library/Fonts/   # then re-render
```

Note the family names the profile maps to — `Poppins`, `Poppins Medium`, `Poppins SemiBold`. All three
must be installed; a machine with only `Poppins-Regular.ttf` substitutes for every 500 and 600 run.

**In `explainer`:** the templates register the faces themselves with `@font-face` from the brand's own
`.ttf` files over `file://`, so no system install is needed. It fails when the brand directory has no
`assets/fonts/`, or when no file matches an approved weight. The build warns:

```
no assets/fonts directory for brand 'X'; HTML templates fall back to an installed copy of Poppins
no font file found for weight 600 in .../assets/fonts
```

Filenames are matched by suffix against the weight: `400 → -Regular` / `-Book`, `500 → -Medium`,
`600 → -SemiBold` / `-Semibold` / `-DemiBold`. A file named `Poppins600.ttf` will not match; rename it
to `Poppins-SemiBold.ttf`.

**Confirm from a frame, not from the source.** Extract a frame with a lot of type and look at it — the
lowercase `a` and `g` distinguish Poppins from every common substitute at a glance.

### Gradient bookends that look perfect and still warn

**Symptom:** `VIDEO.NO_INTRO` on a film whose intro is exactly right — the navy-to-blue field, the
reversed logo lockup, the mint rule. Often with one or two `VIDEO.OFF_PALETTE` warnings on the
gradient slides as well.

**Why:** both checks work by quantising a frame and testing the *dominant* colours. A gradient has no
dominant colour. Quantisation returns points along the ramp, and a point on the ramp is not either
endpoint. Measured on a real Channelplay intro frame:

```
20.4%  #0B0792        16.4%  #0704BD        15.2%  #0503C9        14.5%  #0805AA
```

Every one of those sits between `navy #0F0A6C` and `blue #0000FF` and none is within ΔE 10 of either.
The check is looking for a flat brand field; the brand's intro style is literally
`blue-gradient-logo-reveal`. **This is a limitation of the measurement, not a defect in the film.**

**Confirm it in ten seconds, then accept it:**

```sh
ffmpeg -v error -y -ss 1.125 -i "$F" -frames:v 1 /tmp/intro.png && open /tmp/intro.png
```

If the frame shows the gradient with a legible logo, the warning is a false positive. Record it:

> Accepted: `VIDEO.NO_INTRO`. The generated intro renders correctly (navy→blue gradient, reversed
> lockup, mint rule); the check quantises a gradient to intermediate values that match neither stop.
> `VIDEO.NO_OUTRO` did not fire, because the outro uses the flat navy field.

**What not to do:** do not set `intro: false` to silence it — that removes the intro. Do not add an
intermediate hex to the brand palette. Do not switch the intro to a flat field to satisfy a check.

**When it is *not* a false positive:** the frame is blank, the logo is missing, or the field is a
colour the brand does not own. Then it is cause (3) above — look for `logo variant '<name>' not found`
in the build warnings.

### Photographic frames tripping the palette check

**Symptom:** a wall of `VIDEO.OFF_PALETTE` warnings on a film with real photography — a store interior,
a team shot, a product on a shelf.

**Why it happens, and why it is not simply suppressed:** the check flags any colour holding ≥8% of a
frame that sits further than ΔE 12 from every brand colour. It already exempts greys, near-white,
near-black and skin tones across the full Fitzpatrick range. What it cannot exempt is a large area of a
real colour: a wooden shelf, a competitor's packaging, a saree, a sky. Those are genuinely off-palette
and sometimes they genuinely matter — the check has no way to tell a warm shelf from a chart series in
the wrong blue.

**Triage, in this order:**

1. **Cross-check against `color.superseded.map` first.** `#0000D5`, `#0029E3`, `#0036AA`, `#0F237B`,
   `#00006B`, `#272525`, `#08F8B9`, `#0094DE`, `#E90C29`. If a reported hex is close to one of these,
   it is an old-template colour, not photography, and it must be fixed.
2. **Cross-check against `colorRules.chartSeries`.** A chart fills large flat areas; an off-list series
   colour surfaces here before anywhere else.
3. **Open the named frames.** The violation gives timestamps.

   ```sh
   for t in 00:00:34 00:00:41 00:00:48; do
     ffmpeg -v error -y -ss "$t" -i "$F" -frames:v 1 "/tmp/op-${t//:/}.png"
   done; open /tmp/op-*.png
   ```

4. **What is left is photography.** Accept it — explicitly, in the report:

   > Accepted: 4 × `VIDEO.OFF_PALETTE` on scenes 5–7 (store-interior photography, shelf timber and
   > competitor packaging). Verified against the superseded map and the chart series; neither applies.

**Do not** lower `--frames` to make it quiet. If you reduce the sample for a heavily photographic film,
say so and say why. And never add a photographic colour to the brand palette to silence the check — the
palette is what the brand *is*, not what one film contains.

---

## When the validator itself fails

Exit `1` means the validator could not run. The film is not implicated.

| Message | Cause | Fix |
|---|---|---|
| `ffprobe was not found on PATH` | No ffmpeg | `brew install ffmpeg` |
| `pillow is required` | Wrong interpreter | Use `$HOME/.cache/brand-studio/venv/bin/python` |
| `BrandNotFound` | Unknown `--brand` | `"$PY" "$ROOT/scripts/brand_resolve.py" --list` |
| `<file> was not found` | Bad path | Absolute paths |
| `ffprobe timed out` | Very large file, or a network volume | Copy locally and re-run |
| unparseable timeline | Hand-edited or truncated JSON | `"$PY" -m json.tool "$F.timeline.json"`; rebuild |

A malformed `brands/<id>/rules.local.json` makes `load_brand` raise and takes every skill in the plugin
with it. After any learn-protocol write:

```sh
"$PY" -c "import sys;sys.path.insert(0,'$ROOT/scripts/lib');import brandlib;\
b=brandlib.load_brand('channelplay');print(len(b['learnedRules'].get('rules',[])),'learned rules ok')"
```

---

## Running the validator

```sh
"$PY" "$ROOT/scripts/validate.py" "$WORK/film.mp4" --brand <id> --format human
```

The dispatcher picks up `film.srt` and `film.mp4.timeline.json` automatically when they sit beside the
mp4, which is exactly where `build_video.py` puts them. **Do not move them.** Without the timeline the
duration, caption-gap and storyline checks cannot run at all; without the SRT, `CAPTION.MISSING` fires
as an error whenever the brand requires captions. If the sidecars are genuinely elsewhere, call
`validate_video.py` directly with `--srt` and `--timeline` (see [Diagnostic commands](#diagnostic-commands)).

The PostToolUse hook runs the dispatcher on any video you write, so you will see the output whether or
not you ask for it.

**Errors block. Loop build → validate until zero errors.** Fix the IR and rebuild; never patch the mp4
or hand-edit the SRT, because the next build regenerates both from the IR.

**Warnings need a stated reason to leave in place.** "The client asked for a 4-minute cut" is a reason.
"It is only a warning" is not. Video warnings are noisier than deck warnings by design — photographic
footage trips `VIDEO.OFF_PALETTE`, `say` output rarely lands inside 2 LU of target — so triage them, do
not ignore them. Every warning you keep gets named in the final report with its justification.

`--frames N` controls how many frames are sampled for the pixel checks (default 12, `0` disables).
Raise it to 24 for a final check on a long film; a photographic film is the one case where lowering it
is legitimate, and you say so in the report.

---

## Watching the film — step 7

The validator measures. It does not judge. **Extract a frame at every scene boundary and look at all of
them**, then play the audio.

```sh
mkdir -p "$WORK/frames"
"$PY" -c "
import json, sys
tl = json.load(open(sys.argv[1]))
for e in tl['elements']:
    print('%.2f %02d-%s' % (e['visibleStartSec'] + 0.75, e['index'], e['id']))
" "$WORK/film.mp4.timeline.json" |
while read -r t name; do
  ffmpeg -v error -y -ss "$t" -i "$WORK/film.mp4" -frames:v 1 "$WORK/frames/$name.png"
done
ls "$WORK/frames"
```

Read every PNG. Then check the audio actually plays and the narration is not clipped:

```sh
# does a narration-bearing stretch have signal, and where does the level sit
ffmpeg -hide_banner -nostats -i "$WORK/film.mp4" -af ebur128=peak=true -f null - 2>&1 | tail -14

# the last scene in full: the most common place for a truncated final word
"$PY" -c "
import json, sys
tl = json.load(open(sys.argv[1]))
last = [e for e in tl['elements'] if e['kind'] == 'scene'][-1]
print('%.2f %.2f' % (last['startSec'], last['durationSec'] + 2.0))
" "$WORK/film.mp4.timeline.json"
```

What you are looking for, none of which any check can see:

- **The visual matches the words.** Scene 5 narrates the training model; is slide 7 still on screen
  when it does? A one-scene offset between narration and slide is invisible to every validator and
  obvious to every viewer.
- **The narration is not clipped.** The last word of a scene must land before the cut. `voDuration +
  0.4s` protects this arithmetically, but a `say` voice that trails off, or a music bed that has not
  faded, will still eat it. See [Clipped narration](#clipped-narration).
- **The pacing does not drag.** Two 15-second scenes back to back is thirty seconds on one idea.
- **The caption says what the voice says.** Read the captions with the sound off. That is how most of
  this audience will watch it.
- **The intro and outro are on brand and legible** — not a gradient with a logo lost in it.

### What the validator cannot see, in full

The video validator is deterministic and mechanical. It reads container metadata, samples frames,
measures loudness, parses the SRT and walks the timeline. All of this is invisible to it:

- **Whether the story lands.** Does the hook earn the next eight seconds? Does the proof actually prove
  the claim the approach made? `STRUCTURE.STORYLINE` checks that a scene is *labelled* `proof`. It has
  no opinion on whether it proves anything.
- **Whether the visuals match the words.** The single most common defect in a deck-video is an
  off-by-one slide index: every scene shows the slide belonging to the previous scene. Every check
  passes. Watch the frames.
- **Whether the pacing drags.** Six compliant 12-second scenes is 72 seconds of one image at a time.
- **Whether the voice is right.** `say -v Samantha` is a synthetic American voice narrating an Indian
  retail programme. That may be fine for an internal cut and wrong for a client deliverable. The
  validator has no opinion; the user does. Ask.
- **Whether the narration is clipped.** `AUDIO.SILENCE` finds silence, not truncation. A final word
  eaten by a cut measures as perfectly normal audio.
- **Whether the captions say what the voice says.** They are checked for length, timing, overlap,
  placeholders and exclamation marks — never against the narration.
- **Whether the numbers are real.** Every figure in the script needs a source you can say out loud.
- **Whether the film is too long.** Ninety compliant seconds that could have been sixty is a worse film.
- **Whether the music fits.** Loudness is measured; taste is not.

The validator is a floor, never a ceiling.

---

## The learn protocol, in full

Identical to the protocol in **brand-kit**. **Whenever the user corrects the output** — a colour, a
scene length, wording, pacing, a caption break, the voice, the music, the arc, anything — **persist it
before you continue**. The same correction must never be needed twice. Three tiers, and you may use
more than one.

### Tier 1 — `brands/<id>/LEARNED.md` (always)

Append a dated entry. Do this even when you also do tier 2 or tier 3.

```markdown
## 2026-07-30 — Scenes never run past ten seconds

- **Correction:** user rejected two 15s scenes in the capability film as "a slideshow with a voice".
- **Why:** a still frame past ~10s reads as dead air; the fix is to split the idea, not to shorten the hold.
- **Scope:** channelplay, all videos, every scene.
- **Persisted as:** `brand.json` `video.slideHoldSec.max` 12.0 → 10.0.
```

### Tier 2 — `brands/<id>/rules.local.json` (when it is mechanically checkable)

Object form with a `rules` array. `brandlib.load_brand()` merges the file into `learnedRules`.

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
`error` only for things that must block a build. `id` **must** start with `LOCAL.` so learned rules are
distinguishable from built-in ones in validator output. Re-load the brand afterwards with the check in
[When the validator itself fails](#when-the-validator-itself-fails): a malformed local rules file makes
`load_brand` raise and takes every downstream skill with it.

### Tier 3 — `brands/<id>/brand.json` (when it is a durable brand fact)

"Our films are 24fps", "the outro is now 5 seconds", "we caption burned-in from now on", "the voice is
a real recording, not `say`" are facts about what the brand *is*, not preferences for one film. Most
video corrections land here, because nearly the whole video kit lives in `brand.video`. Edit the
profile, bump `version`, set `updated`, and add a line to `provenance` naming who ruled and when.

### Which tier?

| The correction is… | Destination |
|---|---|
| A one-off wording change for this film only | `LEARNED.md` only |
| A preference a script can check ("never say Submit") | `LEARNED.md` + `rules.local.json` |
| A change to the video kit (fps, hold times, captions, music, arc, voice) | `LEARNED.md` + `brand.json` |
| A disagreement between the guidelines and a reference video | `LEARNED.md` + whichever of the other two the user's ruling implies |
| A motion-template or geometry complaint that applies to all brands | `LEARNED.md`, and say plainly that template changes need the plugin owner — **do not edit `templates/video/*.html` or `grammar/deck-grammar.json`** |

### Tier 4 — tell the user

Say in one line which tier you used. Every time.

> Learned: scenes never run past ten seconds — logged in `LEARNED.md` and `video.slideHoldSec.max`
> lowered to 10.0 in `brand.json` (version 1.0.1).

---

## Non-negotiables

- Never author before the brand is confirmed by **brand-kit**, including the video kit.
- Never skip asking for reference videos when the profile has none recorded.
- Never render before the script is approved as prose.
- Never render without a `--dry-run` first.
- Never ship a film whose validator run exited non-zero.
- Never hand-edit the `.mp4` or the `.srt` to clear a violation. Fix the IR and rebuild.
- Never edit `templates/video/*.html` to make one film work.
- Never leave a warning unexplained in the final report.
- Never skip step 7. A film you have not watched is a film you have not made.
