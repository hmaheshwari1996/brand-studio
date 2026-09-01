# Brand intake — question bank and extraction procedures

Loaded by `brand-kit` STEP 4. Two halves:

1. [Question bank](#question-bank) — what to ask, and what a usable answer looks like.
2. [Extraction](#extraction) — how to pull the answers out of files instead of out of the user.

**Ask in groups of at most four questions.** Echo back what you captured after each group.
Prefer extraction over interrogation: if a guidelines deck exists, run the extractor first and turn
the interview into a confirmation.

Every answer must end up as a field in `brand.json`. The right-hand column of each table is the
field it lands in — see `references/registry.md` for the full schema.

---

## Question bank

### a) Identity

| Ask | Good answer | Bad answer | Lands in |
|---|---|---|---|
| Full display name as it should appear in a deck footer | `Example Brand` | `example technologies pvt ltd (but we write it Example Brand)` — pick one, put the other in aliases | `name` |
| Short id — lowercase, no spaces | `example` | `Example Brand Deck v2` | `id` (and the directory name) |
| What else do people call it? | `["channel play", "cp", "example technologies"]` | `[]` when the brand is routinely abbreviated — you will fail to resolve `cp` later | `aliases` |
| What does the brand do, in one sentence, and who is the audience? | `Retail training and field-execution agency, Delhi India. Audience is client-side brand and sales leadership plus a large field workforce.` | `We deliver excellence` — you cannot write copy from this | `description` |
| Tone in three adjectives | `plain, operational, confident` | `professional` alone | `voice.guidance` |
| House brand or client brand? | `house` / `client` | — | `kind` |

Set `version` to `1.0.0`, `updated` to today, and write `provenance.tokens` describing where the
values came from ("supplied by client 2026-07-30 as Brand_Guidelines_v4.pdf"). Provenance is what
lets the next person tell a brand fact from a guess.

### b) Colour

Ask for **hexes**. "Our blue" is not an answer; neither is a Pantone number on its own — ask for the
hex the brand's own digital team uses.

| Ask | Good answer | Bad answer | Lands in |
|---|---|---|---|
| Primary | `#0000FF` | `royal blue` | `color.brand.<name>` |
| Secondary / deep tone | `#0F0A6C` | — | `color.brand.<name>` |
| Accent | `#41E7AB` | `whatever pops` | `color.brand.<name>` |
| Neutral / default text colour | `#0F0A6C` — and pure black is banned | `#000000` unless the brand genuinely uses it | `colorRules.defaultText` |
| Page/surface background | `#FFFFFF`, wash `#EBF6F9` | — | `color.neutral.0`, `color.brand.tint` |
| Gradients — stops and angle | `#0F0A6C → #0000FF at 135°` | `a blue gradient` | `color.gradient.<name>` |
| Which colours may carry **text**, and which are decorative only? | `mint and teal are decorative; mint-family text uses #1B7A74` | silence — this is the most common accessibility failure | `colorRules.forbiddenText` |
| Any hexes that appear in old files but are **not** approved? | `#0000D5 is the old template blue; canonical is #0000FF` | — | `color.superseded.map` |
| Chart series order | `["#0000FF","#41E7AB","#0194DD","#0F0A6C"]` | — | `colorRules.chartSeries` |

Then derive and confirm:

- **Ramps.** One hex per role is not enough for hover, borders, washes and dark surfaces. Interpolate
  a 50–900 ramp anchored on each brand hex and tell the user they are derived, not brand-mandated.
- **On-surface text.** For every fill a slide may use, compute the text colour that clears contrast
  and store it in `colorRules.onSurface`. Verify each pair:

  ```sh
  "$PY" -c "import sys;sys.path.insert(0,'$ROOT/scripts/lib');import brandlib as B;print(B.passes_contrast('#FFFFFF','#0000FF',12,False))"
  # -> (True, 4.5, 8.592)   i.e. (passes, required_ratio, actual_ratio)
  ```

  Note the order: **`(passes, required, actual)`**, not `(passes, actual, required)`.

  `passes_contrast()` applies the WCAG AA defaults (3.0 at ≥18pt, or ≥14pt bold; 4.5 otherwise) and
  does **not** read the brand's own thresholds. When a brand sets `minContrastBody`,
  `minContrastLarge` or `largeTextPt` to anything other than 4.5 / 3.0 / 18, compare against those
  yourself with `B.contrast_ratio(fg, bg)`.
- **Forbidden text colours.** Any brand hex under ~3:1 on white belongs in `colorRules.forbiddenText`
  with the shade to use instead. State the measured ratio in the reason string.

If the user hands you a swatch image or a screenshot instead of hexes, sample it — same snippet as
the [logo probe](#logo-probe), pointed at the swatch — and read the values back for confirmation.
Never publish a sampled hex without the user agreeing to it; JPEG artefacts shift colours.

### c) Type

| Ask | Good answer | Bad answer | Lands in |
|---|---|---|---|
| Typeface(s) | `Poppins only` | `Poppins for headings and whatever for body` | `type.family` |
| Approved weights | `400, 500, 600` | `regular and bold` — bold is ambiguous between 600 and 700 | `type.approvedWeights` |
| PPTX family name per weight | `400 → "Poppins", 500 → "Poppins Medium", 600 → "Poppins SemiBold"` | `use bold=true` — synthetic bold is a violation | `type.weightToPptxFamily` |
| Italics allowed? | `no` | — | `type.italicsAllowed` |
| Minimum body size | `10.5pt` | `small` | `type.minBodyPt` |
| Fallback stack for machines without the font | `["ui-sans-serif","Segoe UI","Roboto","Helvetica Neue","Arial","sans-serif"]` | `Arial` alone | `type.fallback` |
| Can we ship the font files? | `yes, OFL` / `no, licensed per seat` | — | `assets/fonts/` + `provenance` |

**The weight→family mapping is the whole game in PPTX.** PowerPoint has no weight axis: a semibold
run is the family `"Poppins SemiBold"` with `bold = False`. Setting `bold = True` on `"Poppins"`
makes PowerPoint synthesise a fake bold, which the validator reports as `TYPE.SYNTHETIC_BOLD`. Ask
for the exact installed family names and check them against the font files:

```sh
"$PY" - <<'EOF'
import glob, os
from fontTools import ttLib      # not installed — use the filename + a visual check instead
EOF
```

`fontTools` is **not** available. Confirm family names by reading the filenames in `assets/fonts/`
(`Poppins-SemiBold.ttf` → `"Poppins SemiBold"`) and, if a reference deck exists, by extracting the
run fonts from it — see [Extracting from a guidelines file](#extracting-from-a-guidelines-file).
That extraction gives you the family names PowerPoint actually wrote, which is authoritative.

Then build `type.deckScalePt`. Every role the grammar uses needs an entry: `cover`, `section`,
`title`, `subtitle`, `cardTitle`, `label`, `body`, `bodySmall`, `caption`, `eyebrow`, `footer`,
`statNumber`. Each is `{size, leading, weight, tracking}`, plus `case` where it differs (eyebrow is
usually `upper`). If the brand has no opinion, scale from its body size and say the scale is derived.

### d) Logo

| Ask | Good answer | Bad answer | Lands in |
|---|---|---|---|
| File for light backgrounds | `assets/logos/x-primary.png` | a logo pasted into a slide | `logo.variants.primary.file` |
| File for dark / brand-colour backgrounds | `…-reversed.png` | "just invert it" — never recolour a logo yourself | `logo.variants.reversed.file` |
| File for photography | `…-mono-white.png` | — | `logo.variants.monoWhite.file` |
| Placement | `top-left` | `wherever it fits` | `logo.placement` |
| Minimum width | `1.25in` | — | `logo.minWidthIn` |
| Clear space rule | `equal to the logo height on all sides` | — | `logo.clearSpaceRatio`, `clearSpaceBasis` |
| Forbidden treatments | `redraw, recolour, stretch, rotate, effects, retype the wordmark` | — | `logo.forbidden` |
| Vector available? | `SVG/EPS on request` / `raster only, 1982px` | — | `logo.vectorAvailable`, `vectorNote` |

Also record the on-slide sizes the deck should use — `logo.deckSizeIn` for the header chrome and
`logo.coverSizeIn` for the cover — and `logo.variantForBackground` mapping `light`/`dark`/`photo`/
`gradient` to a variant id.

#### Logo probe

Run this on every supplied logo file **before** writing the profile. It gives you the true aspect
ratio (so the builder never stretches the mark) and shows whether the artwork itself uses
off-palette or superseded colours.

```sh
"$PY" - <<'EOF' example brands/example/assets/logos
import sys, os, collections
sys.path.insert(0, os.path.join(os.environ["ROOT"], "scripts", "lib"))
from PIL import Image
import brandlib as B

brand = B.load_brand(sys.argv[1])
pal, sup = B.palette_index(brand), B.superseded_map(brand)
for f in sorted(os.listdir(sys.argv[2])):
    if not f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        continue
    im = Image.open(os.path.join(sys.argv[2], f)).convert("RGBA")
    w, h = im.size
    bbox = im.split()[3].getbbox() or (0, 0, w, h)         # trim transparent padding
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    px = im.crop(bbox).convert("RGB").quantize(colors=6).convert("RGB")
    tally = collections.Counter()
    for n, rgb in px.getcolors(tw * th) or []:
        tally[rgb] += n
    tops = []
    for rgb, n in tally.most_common(3):
        hx = B.rgb_to_hex(rgb)
        tok, _, dE = B.nearest_token(hx, pal)
        mark = " SUPERSEDED->" + sup[hx.upper()] if hx.upper() in sup else ""
        tops.append("%s(%s dE%.1f)%s" % (hx, tok, dE, mark))
    print("%-42s %dx%d  trimmed %dx%d  aspect %.3f  %s"
          % (f, w, h, tw, th, float(tw) / th, "  ".join(tops)))
EOF
```

Write the printed `aspect` into `logo.variants.<id>.aspect`. Flag anything the probe marks
`SUPERSEDED` — a logo carrying a retired hex means the file is stale, and the fix is a new export
from the brand owner, not a recolour by you.

Rules to state back to the user, because they are load-bearing for the builder:

- Reversed and mono-white variants exist so nobody ever recolours the primary. If a variant is
  missing, say the deck will be constrained (no dark hero, no photo cover) rather than improvising.
- Raster logos below ~1000px wide will look soft on a cover at 1.9in. Ask for vector before promising
  print or large-format output.

### e) Voice

| Ask | Good answer | Bad answer | Lands in |
|---|---|---|---|
| Sentence case or title case? | `sentence, except eyebrows which are upper` | `Title Case Everywhere` (say so explicitly if true) | `voice.case`, `voice.caseExceptions` |
| Exclamation marks allowed? | `no` | shrug — ask directly, it is a one-character check | `voice.forbiddenChars` |
| Banned words and phrases | `["Submit","Click here","Lorem ipsum","Person Name"]` | `avoid jargon` | `voice.forbiddenPhrases` |
| Tense and voice | `present, active` | — | `voice.tense`, `voice.voice` |
| One line of guidance | `Plain, operational, confident, never salesy. Buttons name the action.` | `on-brand` | `voice.guidance` |

Seed `forbiddenPhrases` with every placeholder string that appears in the brand's own template —
`Lorem ipsum`, `Chapter Name Goes Here`, `Place Table / Image / Chart`, `Replace Icons from Icon
slide`. Those are the strings that actually ship by accident. Harvest them by running the
[PPTX extractor](#extracting-from-a-guidelines-file) over the template and reading the string list.

### f) Video kit

Mandatory group. Ask it even when the current request is a deck — the profile is shared, and the
next request will be a video.

**Intro / outro**

| Ask | Good answer | Lands in |
|---|---|---|
| Do you have intro/outro files? | `brands/<id>/video/intro.mp4` | `video.intro.file`, `video.outro.file` |
| If not, generate from the brand? | `generate: blue gradient, logo reveal, 3s` | `video.intro = {type:"generated", durationSec, style, file:null}` |
| Should every video carry them? | `intro yes, outro only on external videos` | recorded in `LEARNED.md`; the video IR carries `intro`/`outro` booleans per video |

**Music**

| Ask | Good answer | Bad answer | Lands in |
|---|---|---|---|
| Supplied tracks? | `video/music/confident-01.wav` (licensed) | `something from YouTube` — refuse unlicensed audio | `video.music.file` |
| Mood | `confident, understated, corporate-modern` | `nice` | `video.music.mood` |
| What to avoid | `dramatic orchestral, lo-fi hiphop, aggressive EDM` | — | `video.music.avoid` |
| Target loudness and ducking | `-23 LUFS, duck 18dB under voice` | `not too loud` | `video.music.targetLufs`, `duckUnderVoiceDb` |

Also capture fade in/out seconds. If the user supplies a track, measure it now and tell them what it
actually is:

```sh
ffmpeg -hide_banner -nostats -i TRACK.wav -af ebur128=peak=true -f null - 2>&1 | tail -14
```

**Captions**

| Ask | Good answer | Lands in |
|---|---|---|
| Burned in or sidecar? | `sidecar SRT` | `video.captions.burnIn`, `.sidecar` |
| Required on every video? | `yes` | `video.captions.required` |
| Position and margin | `bottom-centre, 8% up from the bottom` | `.position`, `.bottomMarginPct` |
| Font, size | `Poppins Medium, 28pt` | `.font`, `.sizePt` |
| Text and background colour, opacity | `#FFFFFF on #0F0A6C at 82%` | `.color`, `.background`, `.backgroundOpacity` |
| Max chars per line, max lines, min duration | `42, 2 lines, 1.2s minimum` | `.maxCharsPerLine`, `.maxLines`, `.minDurationSec` |

Captions are an accessibility requirement, not a style choice. If the user says "no captions", record
it but say plainly that the validator will report it.

**Storyline**

| Ask | Good answer | Bad answer | Lands in |
|---|---|---|---|
| What arc do your videos follow? | `hook → problem → approach → proof → outcome → call-to-action` | `we just show the work` | `video.storyline.arc` |
| Rules that go with it | `Open on the client's problem, never on our credentials. One idea per scene. Proof is numeric. Close with one concrete next step.` | — | `video.storyline.rules` |
| Is the arc mandatory? | `yes` | — | `video.storyline.required` |

The arc values become the `role` field of every scene in the video IR, so keep them to the six known
roles unless the brand genuinely differs — if it does, say that the validator's structure checks will
need the new roles added.

Also capture the mechanical defaults: `resolution`, `fps`, `container`, `vcodec`, `acodec`,
`safeMarginPct`, `transition` (type + duration) and `slideHoldSec` (min/default/max).

### g) Reference material

Ask both questions of **every** brand, new or existing:

> Do you have sample or reference videos for this brand?
> Do you have sample or reference decks?

| Ask | Lands in |
|---|---|
| Paths to reference videos | `video.referenceVideos` (array of paths, relative to the brand dir) |
| Paths to reference decks | `referenceDecks` (top-level array) |
| Which one is the best example? | note it in `provenance` — it is the tiebreaker when they disagree with each other |
| Are these approved output, or drafts? | `LEARNED.md` — a draft is not a standard |

Copy supplied files into `brands/<id>/video/` and store relative paths. Then analyse them per
[Analysing reference material](#analysing-reference-material) below and bring back a disagreement
table.

Common disagreements worth surfacing, because they change what gets built:

- Reference is 24fps or 25fps while the profile says 30.
- Reference is 1080x1350 or 1080x1920 — the brand does vertical and nobody wrote it down.
- Reference audio sits at -14 LUFS (social loudness) while the profile targets -23 (broadcast).
- Reference captions are burned in with a different font from the deck font.
- Reference palette is dominated by a hex that is in `superseded`, meaning it was cut from old
  templates.

---

## Extraction

### Extracting from a guidelines file

**PPTX / POTX.** Run this. It reports every RGB fill and text colour, every font family and size, and
every string — which is where the palette, the type scale and the placeholder phrases all come from.
Set `BRAND=<id>` to have each hex mapped onto an existing palette (useful when *updating* a brand);
omit it for a brand-new brand.

```sh
"$PY" - <<'EOF' /path/to/Brand_Guidelines.pptx
import sys, os, re, collections
sys.path.insert(0, os.path.join(os.environ["ROOT"], "scripts", "lib"))
from pptx import Presentation
from pptx.util import Pt
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.enum.dml import MSO_FILL, MSO_COLOR_TYPE
import brandlib as B

pres  = Presentation(sys.argv[1])
hexes = collections.Counter(); fonts = collections.Counter()
sizes = collections.Counter(); texts = []

def note(color):
    try:
        if color is not None and color.type == MSO_COLOR_TYPE.RGB:
            hexes["#" + str(color.rgb).upper()] += 1
    except Exception:
        pass

def walk(shapes):
    for sh in shapes:
        if sh.shape_type == MSO_SHAPE_TYPE.GROUP:
            walk(sh.shapes); continue
        try:
            if sh.fill.type == MSO_FILL.SOLID: note(sh.fill.fore_color)
        except Exception: pass
        try:
            if sh.line.fill.type == MSO_FILL.SOLID: note(sh.line.color)
        except Exception: pass
        if not sh.has_text_frame: continue
        for para in sh.text_frame.paragraphs:
            for run in para.runs:
                if run.text.strip(): texts.append(run.text.strip())
                if run.font.name: fonts[(run.font.name, bool(run.font.bold))] += 1
                if run.font.size is not None: sizes[round(run.font.size / Pt(1), 1)] += 1
                note(run.font.color)

for sl in pres.slides: walk(sl.shapes)
for m in re.findall(r"#[0-9A-Fa-f]{6}\b", " ".join(texts)):     # hexes typed in as copy
    hexes[m.upper()] += 1

bid = os.environ.get("BRAND")
pal = B.palette_index(B.load_brand(bid)) if bid else {}
print("HEXES")
for hx, n in hexes.most_common(30):
    if pal:
        tok, _, dE = B.nearest_token(hx, pal)
        print("   %s x%-4d nearest=%-16s dE=%.1f" % (hx, n, tok, dE))
    else:
        print("   %s x%d" % (hx, n))
print("FONTS", fonts.most_common(15))
print("SIZES", sorted(sizes, reverse=True))
print("STRINGS (candidate placeholders)")
for t in sorted({t for t in texts if len(t) < 60})[:40]:
    print("   " + t)
EOF
```

How to read it:

- **Hexes by frequency.** The top handful are the palette. Anything appearing once or twice is
  usually a screenshot artefact or a stray — ask before promoting it to a token.
- **`(family, bold)` pairs.** `("Poppins SemiBold", True)` means the source deck applied synthetic
  bold *on top of* the semibold family. Record the family, drop the bold — that pairing is the exact
  thing `TYPE.SYNTHETIC_BOLD` exists to catch.
- **Sizes, descending.** These are the type scale. Map the largest to `cover`, then `section`,
  `title`, and so on down to `caption`.
- **Strings.** Short repeated strings are placeholders. Feed them straight into
  `voice.forbiddenPhrases`.

**PDF.** There is no PDF library in the venv. Two usable routes:

1. Read it directly — the `Read` tool renders PDF pages, and colour swatches with hexes printed
   beside them are legible. Transcribe the values and confirm them with the user.
2. Convert and extract:

   ```sh
   soffice --headless --convert-to pptx --outdir /tmp/bk /path/to/Guidelines.pdf
   ```

   Conversion fidelity is poor for text but fine for solid fills, so use it for colour and the
   `Read` tool for everything else. If conversion produces nothing usable, say so and fall back to
   asking — do not invent values.

**Images / screenshots.** Use the [logo probe](#logo-probe) snippet pointed at the image directory.
Always read sampled hexes back to the user before writing them; compression shifts colour.

### Extracting from a reference deck

Same PPTX extractor, plus geometry. To check whether a reference deck agrees with
`grammar/deck-grammar.json`, print shape rectangles in inches and compare against the archetype
regions:

```sh
"$PY" - <<'EOF' /path/to/reference.pptx
import sys
from pptx import Presentation
pres = Presentation(sys.argv[1])
print("canvas %.3f x %.3f in" % (pres.slide_width / 914400.0, pres.slide_height / 914400.0))
for i, sl in enumerate(pres.slides, 1):
    print("-- slide %d (%s)" % (i, sl.slide_layout.name))
    for sh in sl.shapes:
        if sh.left is None: continue
        print("   %-14s l=%.3f t=%.3f w=%.3f h=%.3f"
              % (sh.shape_type, sh.left / 914400.0, sh.top / 914400.0,
                 sh.width / 914400.0, sh.height / 914400.0))
EOF
```

Look for the logo's `l`/`t` (does it match `chrome.logo`, or is it top-right?), the header rule's
`t`, and the content top. Report differences; **do not** edit the shared grammar to match one brand.
If a brand's geometry genuinely differs, that is a conversation with the plugin owner, and the note
belongs in `LEARNED.md` meanwhile.

### After extraction

1. Show the user a table of what you extracted and where it will land.
2. Ask which extracted values are **approved** and which are **legacy**. Legacy hexes go into
   `color.superseded.map` pointing at their canonical replacement — that is how the validator
   catches assets built from old templates instead of silently accepting them.
3. Only then run `new_brand.py`.

---

## Confirmation card (brand-kit STEP 3)

`brand_resolve.py` prints the card for exactly this moment. Show it verbatim; do not paraphrase it
and do not dump `brand.json` instead. It covers: brand and aliases, primary/secondary/accent hexes
with measured text contrast, chart series, superseded count, type family with its PPTX weight names
and role count, logo placement plus a `[on disk]` / `[MISSING]` check per variant, the whole video
kit, the storyline arc, and the learned rule count.

Drill down only if the user questions a specific value:

```sh
"$PY" "$ROOT/scripts/lib/brandlib.py" --brand <id>      # full resolved palette + superseded map
```

Two things the card does **not** cover, which you are still responsible for:

- **Reference material.** It does not list `video.referenceVideos` or `referenceDecks`. Run intake
  group (g) — for existing brands too.
- **`[MISSING]` logo variants.** If a variant is not on disk, say so before approval and offer to
  either get the file or record the constraint (no dark hero, no photo cover) in `LEARNED.md`.

Acceptable approvals are "yes / use it" or an amendment. Silence, "sure whatever", or moving straight
to deck content is **not** approval — ask again. If the user amends anything (a colour is stale, the
logo moved, the tone changed), do not patch it in memory and carry on: route it through the learn
protocol so it survives the session, then re-show the card.

---

## Analysing reference material

Run this whenever the user supplies a reference video. It takes under a minute and routinely catches
a brand whose real videos are 24fps, 4:5, and mint-heavy while the written guidelines say 30fps 16:9.

**1. Container, resolution, fps**

```sh
ffprobe -v error -select_streams v:0 \
  -show_entries stream=codec_name,width,height,r_frame_rate,avg_frame_rate,duration \
  -show_entries format=duration,bit_rate,format_name -of json REF.mp4
ffprobe -v error -select_streams a:0 \
  -show_entries stream=codec_name,channels,sample_rate -of json REF.mp4
```

Compare against `brand.video.resolution`, `.fps`, `.container`, `.vcodec`, `.acodec`.

**2. Loudness** (`I:` is integrated LUFS, compare to `video.music.targetLufs` / `voiceover.targetLufs`)

```sh
ffmpeg -hide_banner -nostats -i REF.mp4 -af ebur128=peak=true -f null - 2>&1 | tail -14
```

**3. Real palette** — sample frames, then map them onto the brand palette.

```sh
mkdir -p /tmp/bk-frames
ffmpeg -v error -y -i REF.mp4 -vf "fps=1/5,scale=320:-2" -frames:v 24 /tmp/bk-frames/f_%02d.png
```

```sh
"$PY" - <<'EOF' example /tmp/bk-frames
import sys, os, glob, collections
sys.path.insert(0, os.path.join(os.environ["ROOT"], "scripts", "lib"))
from PIL import Image
import brandlib as B

brand = B.load_brand(sys.argv[1])
pal, sup = B.palette_index(brand), B.superseded_map(brand)
tally = collections.Counter()
for f in sorted(glob.glob(os.path.join(sys.argv[2], "*.png"))):
    q = Image.open(f).convert("RGB").resize((160, 90)).quantize(colors=8).convert("RGB")
    for count, rgb in q.getcolors(160 * 90) or []:
        tally[rgb] += count
total = sum(tally.values()) or 1
for rgb, n in tally.most_common(12):
    hx = B.rgb_to_hex(rgb)
    tok, _, dE = B.nearest_token(hx, pal)
    if hx.upper() in sup:   flag = "SUPERSEDED -> " + sup[hx.upper()]
    elif dE <= 5.0:         flag = "on brand"
    else:                   flag = "OFF BRAND"
    print("%6.1f%%  %s  nearest=%-16s dE=%5.1f  %s" % (100.0 * n / total, hx, tok, dE, flag))
EOF
```

`dE` ≤ 5 is a match, 5–15 is drift worth mentioning, > 15 is a different colour. Anything landing in
`superseded` means the reference was cut from old templates — say so; do not silently adopt it.

**4. Captions and safe area** — pull a frame at a moment with on-screen text and look at it. Check
caption position, size and whether they are burned in. Compare to `brand.video.captions`.

**5. Report the disagreements and ask which wins.** One table, one question:

| Field | Guidelines say | Reference does | Which wins? |
|---|---|---|---|

Then persist the ruling. A "reference wins" answer edits `brand.json`; a "guidelines win" answer goes
in `LEARNED.md` so the same reference does not reopen the argument next month.

**Reference decks**: open them with the pptx tooling above
([Extracting from a reference deck](#extracting-from-a-reference-deck)) and compare fonts, sizes,
hexes and slide geometry the same way.
