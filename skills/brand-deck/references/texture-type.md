# Texture type — turning a reference photo into a display treatment

`scripts/texture_type.py` reads a photograph — a lipstick swatch, a fabric, wet paint, brushed metal,
kraft paper, foliage — and renders its palette, grain, gloss and edge character onto letterforms.
Output is an RGBA PNG plus a JSON sidecar.

Run it with the plugin venv:

```sh
PY="$HOME/.cache/brand-studio/venv/bin/python"
"$PY" scripts/texture_type.py --ref swatch.jpg --text "CHANNELPLAY" --analyze-only
```

The slash command `/brand-texture` drives the whole conversation. This file is the reference behind it.

---

## 1. What this is, and what it is not

**A photograph cannot become a .ttf.** A font file carries outlines — contours, sidebearings,
kerning — and nothing else. It has no colour, no grain, no gloss. When someone says "make a font from
this lipstick swatch", there is no tool anywhere that does that, and saying so is not pedantry: it
decides what you deliver.

What a photograph *can* become is a type **treatment**: one specific string, set in the brand face,
with the reference's surface rendered into it. That is artwork. It is a picture of words.

Consequences, all of them load-bearing:

- It sets **one string**. Change the words, re-render.
- It does not reflow, does not resize gracefully below display size, and carries no selectable text.
- It is not a typeface and must never be described to a client as one.

### The skeleton stays the brand face — non-negotiable

Channelplay's guidelines say **Poppins only, never substitute another face**. So the letterforms
always come from `brand.type.weightToPptxFamily` (400 → Poppins, 500 → Poppins Medium, 600 → Poppins
SemiBold), loaded from `brands/<id>/assets/fonts/`. Only the **fill** and the **edges** come from the
photo.

This is a hard constraint in the code, not a default you can talk your way past:

- `--weight 700` is **refused** with the list of approved weights. There is no synthetic bold.
- If the brand has no font file for the weight, the script **fails** rather than falling back to a
  system face. A silent fallback would ship a different typeface wearing the brand's colours.

A treatment that changed the skeleton would be exactly the substitution the guidelines forbid, and the
deck validator would be right to reject it.

---

## 2. When a textured treatment earns its place — and when it cheapens the deck

**Use it for:**

- a cover word, where the treatment *is* the visual and there is no photograph competing with it
- a section break that opens a campaign chapter
- a reel title or an end card, where the film needs one held frame with weight
- a campaign lockup that is deliberately not house-standard — a client brand's colours, a product
  launch, a festival moment

**Do not use it for:**

- body copy, labels, captions, table headers, chart axes, UI, or anything under ~40pt on a slide.
  Textured edges read as a printing fault at small sizes.
- more than **one slide in a deck**. A second one stops being a moment and starts being a theme, and
  the theme is Poppins on brand colour. Two textured slides in the same deck is the single fastest way
  to make an enterprise deck look like a template.
- anything that has to be searched, translated, or read aloud by a screen reader.
- a slide that already has a photograph. Texture on texture is noise.
- a busy reference. One surface, filling the frame, evenly lit. A reference with five things in it
  produces a fill that fights the letterforms, and the type stops being readable.

The honest test: if you removed the texture and set the same word in flat brand blue, would the slide
be worse? If not, the texture is decoration and it should go.

---

## 3. Workflow

1. **Analyse first.** `--analyze-only` costs nothing and tells you whether the reference is even
   on-brand. Show the user the palette, the brand distance and the suggested style.
2. **Agree the style.** `auto` proposes; the human decides.
3. **Render with `--preview`.** Look at the contact sheet before you place anything.
4. **Place the PNG** in the deck or the Video IR (§7, §8).
5. **Keep the sidecar** next to the PNG. It is how the treatment gets rebuilt six months later.

---

## 4. Full CLI

```
texture_type.py --ref <photo> [--text "WORDS"]
                [--brand ID]            brand id or name          (default channelplay)
                [--weight 400|500|600]  skeleton weight           (default 600)
                [--out FILE.png]        output PNG                (default <ref>-<text>-texture.png)
                [--width 1920]          canvas width in px
                [--height auto|N]       canvas height in px
                [--size auto|N]         glyph size in px
                [--style auto|smooth|ragged|grainy|glossy|matte]
                [--preview]             also write <out>-preview.png (light / dark / gradient)
                [--json]                print the full record as JSON
                [--letter-spacing EM]   tracking in em            (default: brand cover tracking)
                [--align left|center|right]
                [--bg light|dark|transparent|#RRGGBB]             (default transparent)
                [--analyze-only]        report and stop
                [--accept-off-palette]  record the operator's acceptance in the sidecar
```

Notes on the ones with teeth:

- **`--bg`** — `transparent` (default) keeps a real alpha channel and is what you want for a deck.
  Anything else **bakes that background into the PNG** and becomes the single background the contrast
  check reports against. Transparent output is checked against the brand's light *and* dark surfaces,
  because a transparent PNG can land on either.
- **`--size auto`** fits the ink to the canvas width. Multi-word strings **wrap to a second or third
  line** when a single line would fall below display size (5.5% of the canvas width), because wrapping
  is what buys the glyph size back. `--size N` forces a size and wraps only if it must.
- **`--letter-spacing`** defaults to the brand's `type.deckScalePt.cover.tracking` (−0.02 em for
  Channelplay), which is legal because the brand only forbids negative tracking below 22pt.
- **`--width`/`--height`** — auto height means the canvas is exactly the artwork. Fixed height centres
  the artwork and scales it down if it would not fit. Nothing is ever cropped: the layout measures
  each glyph's real ink box, so overshoots and descenders survive.
- **`--preview`** always shows the *bare* treatment on light, dark and the hero gradient, even when
  `--bg` baked a background into the PNG. Its job is to answer "where is this usable", and each panel
  carries the measured contrast — the gradient panel reports its worst stop, which is where a
  treatment usually dies (a red swatch reads 5.10:1 on white and 1.69:1 over `#0000FF`).

---

## 5. Reading the analysis

```
REFERENCE  swatch.png  (1400x600)
  subject  saturation, 49% of frame -- saturation > 1.15x frame mean (104.7) and value > 25

PALETTE (subject pixels only)
  hex        share  nearest brand token      deltaE   nearest identity     deltaE
  #B82338    37.6%  semantic.danger            10.1   brand.navy             87.6  (semantic only)
  ...
TEXTURE
  grain           14.85  (sd of subject luma; normalised 0.23)
  directionality  +0.90  horizontal streaks  (edge energy x 0.20 / y 3.89)
  gloss            0.13  (p95 98 - p50 64 of subject luma)
  specular        0.037  (that spread minus the 24 the grain alone explains)
  raggedness       0.26  (outline jitter 0.37 (1.8/0.3 px x/y), edge 0.09)

STYLE (auto)  smooth
```

| Figure | What it measures | Why it matters |
|---|---|---|
| **subject** | pixels whose saturation exceeds 1.15× the frame mean, above a darkness floor | Separates pigment from paper. A near-neutral or full-frame texture (concrete, foliage) falls back to the whole frame and says so. |
| **palette** | median-cut into 8, counted over subject pixels, top 5 with share | The colours that will actually appear in the fill. |
| **grain** | sd of subject luminance | How much the surface scatters light. Drives the `grainy` decision and the fill noise. |
| **directionality** | (edge energy y − x) / total, −1…+1 | Positive means horizontal streaks — drag marks, brushing, weave. This is what tells you a lipstick swatch is *dragged*, not stippled. |
| **gloss** | p95 − p50 of subject luminance, /255 | The raw spread the proof of concept measured. |
| **specular** | that spread minus the 1.645·sd a normal distribution gives you for free | The *real* highlight. Concrete reads gloss 0.10 and specular 0.000: it is gritty, not shiny. The gloss pass follows this number, not the raw one. |
| **raggedness** | outline jitter (row-to-row wander of the leftmost/rightmost pixel, roughest axis) blended with edge energy | Total perimeter is the obvious measure and the wrong one — scattered leaves have an enormous perimeter and no torn edge. Jitter is only measured when the subject fills ≥55% of its own bounding box; otherwise the note says why not. |

### Style selection

`auto` applies ordered rules and prints the one that fired with its numbers:

| Style | Fires when | Treatment |
|---|---|---|
| `ragged` | raggedness ≥ 0.42 | mask pre-blurred, displaced by two octaves of noise, re-thresholded hard — broken edges. Interiors always stay solid; a treatment that punched holes through the stems would be illegible. |
| `grainy` | grain ≥ 0.22 **and** edge energy ≥ 0.18 | noise mixed through the fill (deep *and* high-frequency scatter is grain) |
| `glossy` | specular ≥ 0.045 | strong specular band across the upper third of the ink |
| `matte` | specular ≤ 0.012 | gloss zeroed, fill luminance pulled toward its median |
| `smooth` | everything else | clean antialiased edges, texture in the fill only |

Measured examples, for calibration: a red lipstick swatch → `smooth` (raggedness 0.26, mild); a
blue-grey concrete wall → `grainy` (grain 0.25, edge 0.26); a green canopy → `grainy` (grain 0.41,
edge 0.38). Any style can be forced with `--style`.

---

## 6. The brand check — the reason to do this here and not in Photoshop

Two numbers, both reported, **neither ever blocking**.

### Contrast

The treatment's alpha-weighted mean colour is measured off the actual rendered pixels and put through
WCAG against the intended background(s), at the brand's `colorRules.minContrastLarge` (3.0:1 for
display sizes).

```
  contrast  ok    #CA354A vs #FFFFFF (light)  5.10:1  (needs 3.0:1 at display size)
  contrast  FAIL  #386939 vs #0F0A6C (dark)   2.54:1  (needs 3.0:1 at display size)
  !! CONTRAST FAILS AA FOR LARGE TEXT: 2.54:1 against 3.0:1.
```

A dark-green foliage treatment on navy fails, and it fails quietly in Photoshop. Here it fails loudly,
with the number, before the deck ships.

### Brand distance

Every palette colour gets its nearest approved token *and* its nearest **identity** colour
(`color.brand.*` plus gradient stops), by CIE76 delta E. The verdict uses the identity distance,
share-weighted: ≤10 on-palette, ≤25 adjacent, above that off-palette.

The split matters. Channelplay's approved palette contains `semantic.danger #C4262E`, so a lipstick red
lands 10 delta E from an approved token and looks compliant. It is not: semantic tokens signal
success/warning/danger in UI, and borrowing one for display type reads as an error state. The tool
flags that case separately (`semantic only`). Against the brand's actual identity colours the same red
sits **~85 delta E** — a different hue family entirely.

That is legitimate for a client brand or a campaign moment. It is not legitimate for house material
sitting beside the rest of the deck. The tool states it; the human decides. `--accept-off-palette`
records the decision in the sidecar (`brandCheck.offPaletteAccepted: true`) so it is auditable.

---

## 7. Placing the PNG in a deck

The treatment is a normal image asset. Transparent PNGs are embedded as-is — `build_deck.py` never
flattens them.

**Best fit: `full-bleed`**, because the region is the whole 13.333 × 7.5 in canvas and the aspect
ratio is easy to match:

```json
{
  "archetype": "full-bleed",
  "image": "/abs/path/assets/channelplay-lipstick.png",
  "title": "The moment the brand shows up",
  "caption": "Treatment from a lipstick swatch, 2026 campaign."
}
```

**Or a visual region** on `text-visual` / `visual-text` / `icon-rows`, using the `visual` object:

```json
{
  "archetype": "text-visual",
  "eyebrow": "Campaign",
  "title": "One word, in the campaign's own material",
  "body": "…",
  "visual": { "kind": "image", "src": "/abs/path/assets/channelplay-lipstick.png" }
}
```

`section-break` accepts the same `visual` object (or a bare `"art": "path"`), and `agenda` takes one in
its side panel.

**Watch the aspect ratio.** Images in a region are centre-cropped to *fill* it, so render the PNG at
the region's proportions or the ends of your word get cut off. Region boxes, in inches:

| archetype | region | box (w × h in) | render at |
|---|---|---|---|
| `full-bleed` | `image` | 13.333 × 7.50 | `--width 1920 --height 1080` |
| `text-visual` | `visual` | 7.984 × 4.85 | `--width 1600 --height 972` |
| `visual-text` | `visual` | 6.430 × 4.85 | `--width 1400 --height 1056` |
| `icon-rows` | `visual` | 4.944 × 4.85 | `--width 1200 --height 1177` |
| `section-break` | `art` | 5.910 × 6.42 | `--width 1200 --height 1304` |
| `agenda` | `visual` | 3.940 × 7.50 | `--width 1000 --height 1904` |

Auto height gives you a canvas the height of the artwork, which is almost never the region's ratio —
so pass an explicit `--height` when the PNG goes into a cropped region, and let the extra space be
transparent.

**Accessibility, stated plainly.** The builder does not write alt text (`validate_deck.py` reports
`A11Y.ALT_TEXT` as info, and the fix is manual in PowerPoint). The string in the artwork is invisible
to search, to translation and to a screen reader. So **always put the real words in the slide's
speaker notes**, which live at the top level of the IR keyed by 1-based slide number:

```json
"notes": {
  "4": "Slide art reads: CHANNELPLAY. Treatment rendered from the campaign lipstick swatch."
}
```

---

## 8. Placing the PNG in a video

In the Video IR, a still is a scene whose visual is `kind: "image"`:

```json
{
  "id": "s7", "role": "close",
  "visual": {"kind": "image", "slide": null, "template": null,
             "src": "assets/channelplay-lipstick.png", "data": {}},
  "vo": "Channelplay.",
  "caption": "Channelplay.",
  "holdSec": 3
}
```

`src` resolves against the IR's directory, the brand directory, the plugin root, then cwd. A missing
file is a hard build error, not a warning.

**Alpha is not preserved on this path.** The still goes into ffmpeg and out through `format=yuv420p`;
`pad` only fills letterbox bars. A transparent PNG will composite against whatever RGB sits under the
alpha — usually black — not against the brand pad colour. So for `kind: "image"`, **bake the
background**:

```sh
"$PY" scripts/texture_type.py --ref swatch.jpg --text "CHANNELPLAY" \
  --width 1920 --height 1080 --bg dark --out assets/end-card.png
```

**If you need real transparency in a film** — the treatment composited over a gradient, or animated —
use a motion template instead and pass the PNG as `visual.data.image`. The builder resolves it to a
`file://` URL and Chrome composites it, so alpha is honoured against the template's own background:

```json
{
  "id": "s7", "role": "close",
  "visual": {"kind": "motion", "template": "scene", "src": null,
             "data": {"image": "assets/channelplay-lipstick.png"}},
  "holdSec": 3
}
```

Render at the delivery resolution (1920 × 1080 for Channelplay's default format). Stills on scene
elements get the Ken Burns push-in, so leave a little slack around the ink if you do not want the
edges to travel out of frame.

---

## 9. Reproducing a treatment from its sidecar

Every render writes `<out>.json` beside the PNG. It carries the reference path and its SHA-256, the
extracted palette, every statistic, the chosen style and why, the contrast and brand-distance results,
the exact settings (including the derived random seed), and a `reproduce` line:

```json
"reproduce": "texture_type.py --ref /abs/swatch.png --text 'FIELD FORCE' --brand channelplay --weight 600 --style grainy --width 1920 --align center --bg dark --letter-spacing -0.0200 --size 322 --out /abs/out.png"
```

Prefix it with the venv python and the script path and it rebuilds the artwork **byte for byte** — the
noise seed is derived from the reference hash, the text, the style, the size and the width, so nothing
drifts between runs or between machines.
That is what makes a campaign lockup re-renderable at a different width a year later.

If the reference file has changed, the SHA-256 in the sidecar will no longer match: the reproduction
is only guaranteed against the same source photo. Keep the reference with the sidecar.

---

## 10. Limits, stated honestly

- **No .ttf, ever.** There is no font file at the end of this. If a client needs a real display face,
  that is a type-design commission, not a script.
- **One string per PNG.** No reflow, no editing in PowerPoint, no live text.
- **Not searchable, not translatable, not accessible.** Speaker notes are the mitigation, and they are
  mandatory, not optional.
- **A busy reference produces unreadable type.** The tool will happily render it; the analysis will
  usually warn you first (high grain plus high edge energy), but judgement is yours.
- **The subject split is a heuristic.** It works on a swatch photographed on paper and falls back to
  the full frame on a texture that fills the frame. It will not carve a lipstick out of a photograph
  of a dressing table — crop first.
- **Contrast is measured on the mean colour.** A treatment with a very wide luminance range can pass
  on average while its darkest strokes fail locally. Look at the preview sheet; that is what it is for.
- **CIE76 delta E** is the brandlib metric. It is coarse in blues by modern standards. Treat the bands
  (10 / 25) as guidance, not as a gate — which is why nothing here blocks.
