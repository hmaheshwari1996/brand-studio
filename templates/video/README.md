# Video motion templates

Self-contained HTML rendered to PNG frame sequences by
[`scripts/build_video.py`](../../scripts/build_video.py) using headless Chrome.
Four templates ship here:

| file | used for | driven by |
| --- | --- | --- |
| `intro.html` | the opening gradient logo reveal | `brand.video.intro` + `ir.meta` |
| `outro.html` | the closing lockup and call to action | `brand.video.outro` + `ir.meta` |
| `scene.html` | the default for any scene whose `visual.kind` is `"motion"` | `visual.data` |
| `coverage-map.html` | the scale beat — dots populating a territory | `visual.data` |

A template is never opened directly by a human workflow. The builder reads it,
substitutes two markers, writes the result into its work directory, and screenshots
it once per frame.

---

## The two substitution markers

### `{{BRAND_VARS}}`

Replaced with a single `<style>` block that carries the whole brand: `@font-face`
rules pointing at `brands/<id>/assets/fonts/*.ttf` over `file://` URLs, every
approved colour as a `--c-*` custom property, and a stable set of **role**
variables that the templates actually reference.

Templates must only ever use the role layer (plus `--c-*` when a specific ramp
step is genuinely needed). That is what keeps them brand-agnostic — swap the
brand and the same markup restyles itself.

| variable | meaning |
| --- | --- |
| `--surface-light` / `--surface-tint` / `--surface-brand` / `--surface-dark` / `--surface-accent` | the five approved backgrounds |
| `--ink` / `--ink-soft` | default and secondary text colour |
| `--ink-on-light` / `--ink-on-tint` / `--ink-on-brand` / `--ink-on-dark` / `--ink-on-accent` | the text colour the brand mandates on each surface |
| `--accent` / `--accent-2` | decorative accents (mint / teal) — rules, dots, bars. **Never text.** |
| `--accent-text` | the accent-family colour that *is* legal as text on light (mint 700) |
| `--rule` | hairline / divider colour |
| `--grad-primary` / `--grad-accent` | ready-made `linear-gradient(...)` values built from the brand's gradient stops and angle |
| `--font-regular` / `--font-medium` / `--font-semibold` | quoted family names for weights 400 / 500 / 600 |
| `--font-fallback` | the brand's fallback stack |
| `--canvas-w` / `--canvas-h` | render size in px, e.g. `1920px` / `1080px` |
| `--safe-x` / `--safe-y` / `--safe-pct` | the safe margin in px on each axis, from `brand.video.safeMarginPct` |
| `--logo-primary` / `--logo-reversed` / `--logo-mono-white` | `url('file://…')` for each logo variant |
| `--logo-primary-aspect` (etc.) | width ÷ height, so a logo can be sized from its height alone |
| `--c-brand-blue`, `--c-blue-600`, `--c-semantic-success`, … | every approved colour in the profile, one property per hex |

`color.superseded` is deliberately **not** exported. Unapproved template colours
must not be reachable from a template.

### `{{SCENE_DATA}}`

Replaced with a JSON object literal, assigned to `window.__SCENE__` at the top of
the template's script. It is the scene's `visual.data` for `scene.html`, and a
builder-composed object for the intro and outro. Any `data.image` path is
resolved to an absolute `file://` URL before substitution.

Read it with `textContent`, never `innerHTML` — the payload is authored content
and must not be able to inject markup.

---

## The animation clock

There are **no CSS animations or transitions anywhere**, by design. Chrome
screenshots a single moment, so anything time-based in CSS would be captured at
an arbitrary point and the render would not be reproducible.

Instead the builder renders frame *n* by loading:

```
file:///…/scene.html?t=0.482759&frame=14&of=30
```

`t` runs 0 → 1 across the scene's animated portion. The template's inline script
reads it synchronously at parse time and sets every transform and opacity from
it. The same `t` always produces the same pixels.

`build_video.py` renders unique frames only for the first `--motion-anim-sec`
(default 1.6s) of a scene and then links the final frame for the remaining hold,
so a 13-second scene costs ~48 screenshots rather than 390. Intro and outro are
animated across their full declared duration.

Each template applies its frame twice: once during parse, once on `load`. The
second pass matters because web fonts finish loading between the two, and any
text that had to be shrunk to fit must be re-measured with the real metrics.

### Adding a new template

1. Start from `scene.html` — copy the `<head>`, the `{{BRAND_VARS}}` marker, the
   theme block and the `queryT` / `easeOut` helpers.
2. Give every animated element `data-anim` (`rise`, `fade`, `grow-x`, `grow-y`),
   `data-delay` and `data-span`, or set styles directly from `t` as the intro does.
3. Keep the file free of external references. No `<link>`, no CDN, no remote
   image, no font URL outside the brand's own asset folder. A strict reading:
   if it would make a network request, it does not belong here.
4. Reference it from the IR as `"visual": {"kind": "motion", "template": "<name>"}`.
   The builder resolves `<name>` against `templates/video/<name>.html`.

---

## Layout rules these templates follow

- **Safe margin.** `#stage` is padded by `--safe-x` / `--safe-y` (5% of the canvas
  for Channelplay: 96px × 54px). Nothing readable sits outside it.
- **Logo top-left**, per the brand's logo placement rule, sized from its height
  using the variant's aspect ratio so it is never stretched.
- **Reversed logo on dark, primary on light**, selected by the theme block rather
  than by the caller.
- **Text fits or shrinks.** Titles, bodies, quotes and stat numbers are measured
  against the container and stepped down 2px at a time to a per-role floor. Only
  the *container* is tested for overflow: Poppins' ascenders and descenders
  ink-overflow a tight `line-height`, so an element's own `scrollHeight` is always
  larger than its `clientHeight` and is useless as a signal.
- **Mint and teal are decorative only** — rules, dots and bars. On light surfaces
  accent *text* uses `--accent-text` (mint 700), which clears 4.5:1.

---

## `scene.html` data reference

```jsonc
{
  "variant": "statement",   // statement | stats | bullets | quote | split
  "theme":   "light",       // light | tint | brand | navy | gradient | accent
  "eyebrow": "What we inherit",        // rendered uppercase, in the accent colour
  "title":   "Three numbers explain most programme failure",
  "body":    "Supporting sentence.",
  "rule":    true,                     // false hides the accent rule
  "logo":    true,                     // false hides the logo
  "footer":  "Optional small footer line",

  // variant: stats  (up to 4)
  "stats":   [{ "value": "62%", "label": "Field attrition before certification" }],

  // variant: bullets  (up to 6; a plain string works too)
  "bullets": [{ "lead": "Learn.", "body": "In-app modules before the first shift." }],

  // variant: quote
  "quote":     "Audited compliance rose from sixty one to ninety two percent",
  "attrib":    "Telecom retail programme",
  "attribSub": "Twenty nine hundred outlets, four quarters",

  // variant: split
  "image": "path/to/photo.jpg"   // resolved to a file:// URL by the builder
}
```

Unknown variants fall back to `statement`; unknown themes fall back to `light`.
Every field is optional — anything absent is simply not rendered.

## `coverage-map.html` data reference

The scale beat: points populate across a territory to show reach. Select it with
`"visual": {"kind": "motion", "template": "coverage-map", "data": { … }}`.

```jsonc
{
  "outline":  "india",              // india | grid | none    (default grid)
  "surface":  "dark",               // dark | brand | light   (default dark)
  "seed":     7,                    // PRNG seed; same seed, same scatter
  "points":   320,                  // dots to render (default 320, hard cap 600)
  "eyebrow":  "Outcome",
  "headline": "4,200 stores",
  "subhead":  "across 19 states",

  // optional named clusters. `points` is split between them in proportion to
  // their counts; each count animates up to its final value.
  "regions": [{ "name": "North", "count": 1200, "x": 0.42, "y": 0.22 }],

  "legend":  [{ "label": "Covered daily", "tone": "accent" }]  // accent | accent-2 | muted
}
```

`region.x` / `region.y` are normalised 0..1 inside the plot rect — the centred
square the silhouette occupies in `india` mode, the whole inset field otherwise.

Beyond 600 dots the count is clamped and the clamp is reported with
`console.warn`: the file is re-rendered once per video frame and thousands of
nodes per frame is the difference between a fast build and a slow one.

**The `india` outline is a stylised motif, not a map.** It is a 51-vertex
low-poly polygon embedded inline — no geodata is fetched, ever. It is
deliberately generalised, takes no position on any border or disputed boundary,
and must not be used where geographic accuracy matters. See the header comment
in the template.

## `intro.html` / `outro.html` data

The builder composes these; they are not authored per scene.

| field | intro | outro |
| --- | --- | --- |
| `title` | `ir.meta.title` | `ir.meta.cta`, else the last `call-to-action` scene's caption, else `ir.meta.title` |
| `eyebrow` | `ir.meta.eyebrow` | `ir.meta.outroEyebrow` |
| `contact` | — | `ir.meta.contact` |
| `brandName` | brand display name | brand display name |

Set `brand.video.intro.file` or `brand.video.outro.file` to a real media file to
bypass the template entirely; the builder then uses that image or video for the
declared duration.

---

## Rendering one by hand

Useful when iterating on a template. The builder normally does this for you.

```sh
python3 - <<'EOF'
import sys; sys.path.insert(0, "scripts")
import build_video as bv
from lib import brandlib

brand = brandlib.load_brand("channelplay")
video = brandlib.deep_merge(bv.DEFAULT_VIDEO, brand["video"])
path  = bv.write_scene_html(
    bv.template_path("scene"),
    bv.build_brand_vars(brand, video, []),
    {"variant": "stats", "theme": "navy", "eyebrow": "Proof",
     "title": "Three numbers", "stats": [{"value": "62%", "label": "Attrition"}]},
    "/tmp/preview.html")
print(path)
EOF

open "/tmp/preview.html?t=1"
```

Append `?t=0.3` to inspect a mid-animation frame. With no `t` the template renders
its settled state (`t = 1`), which is also what a browser shows if you just open
the file.
