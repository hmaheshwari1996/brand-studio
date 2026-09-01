# CGI product scenes

A single self-contained template, [`product.html`](product.html), that puts a client's product in a
branded 3D studio. Rendered by [`scripts/render_cgi.py`](../../scripts/render_cgi.py) through
headless Chrome running WebGL 2 on **SwiftShader** — CPU rasterisation. There is no GPU in this
pipeline and no Blender; three.js r160.1 is vendored in [`vendor/`](vendor/) and nothing is ever
fetched from the network.

Driven from the `/brand-cgi` command.

---

## The substitution contract

Identical to [`templates/video/`](../video/README.md). The builder reads this file, replaces two
markers, and writes the result into a work directory:

- **the brand-vars marker** → a `<style>` block carrying the whole brand: `@font-face` rules over
  `file://`, every approved colour as a `--c-*` property, and the role layer (`--surface-brand`,
  `--accent`, `--ink`, `--grad-primary`, `--font-semibold`, `--safe-pct`, …). Built by
  `build_video.build_brand_vars()`, so CGI and motion cannot drift apart.
- **the scene-data marker** → a JSON literal assigned to `window.__SCENE__`.

Each marker appears **exactly once** in the template and must never be written literally a second
time, including inside a comment. Substitution is a plain `str.replace`, so a duplicate would inject
the entire style block — ~60 custom properties and three `@font-face` blocks — into that comment, and
would break the comment outright the moment a value contained `-->`. `render_cgi.write_page()` raises
on a duplicate rather than producing a corrupt page.

Every colour is read at runtime from those CSS variables via `getComputedStyle`. **No hex is
hardcoded anywhere in the template** — that is what lets the same scene restyle itself for another
brand.

---

## Frame sheets, and why they exist

The expensive part of a CGI frame here is **not drawing it — it is starting Chrome.** Process startup
plus SwiftShader initialisation costs roughly 7–13 seconds, and it is paid on every launch regardless
of what is on the page.

So the renderer does not screenshot one frame at a time. The page reads

```
product.html?w=1920&h=1080&t0=0&dt=0.0333&n=6&cols=3
```

lays out `n` cells in a `cols`-wide grid, renders the scene once per cell into **one** sheet canvas,
and Chrome takes a **single** screenshot of the whole grid. `render_cgi.py` then slices the sheet back
into `n` PNGs with pillow.

One detail matters: there is **one WebGL context for the entire sheet**, not one per cell. The
renderer draws at cell size and the result is blitted into a 2D canvas with `drawImage`. `n` contexts
would pay SwiftShader's per-context setup `n` times over — the exact cost the mechanism exists to
avoid — and would hit Chrome's per-page context limit above ~16.

Measured on an 8-core M-series Mac, bottle scene, single worker:

| | 6 frames @ 1280×720 | frames/min |
| --- | --- | --- |
| one Chrome launch per frame (`--per-sheet 1`) | 88s | 4.0 |
| one launch for a 6-frame sheet (`--per-sheet 6`) | 34s | **10.5** |

**2.6× from sheets alone**, before any parallelism. Combined with `--jobs` (default: CPU count − 2)
the two multiply.

`render_cgi.py` renders the *first* sheet on its own and calibrates against it, then prints measured
throughput and a projected time before starting the long part. The calibration sheet is real output,
not a throwaway probe, so the measurement is free — and the projection comes from this scene on this
machine rather than from a table in a README.

---

## Measured performance

1920×1080, Example Brand bottle scene with reflection and shadows, 8-core M-series Mac:

| what | time |
| --- | --- |
| a single still (`--still`) | ~15s |
| a preview contact sheet, 4 stills (`--preview`) | ~25s |
| within a warm sheet, per frame | ~6.2s |
| 24 frames, `--per-sheet 6 --jobs 6` | 96s (**15.3 frames/min**) |

Scale from that: **a hero still is cheap, a 6-second turntable is a few minutes, and a 30-second
full-CGI film is an overnight job.** Say so before someone asks for thirty seconds.

Cheapest levers, in order: `--width/--height`, `reflection: 0` (drops the mirrored second pass),
`shadow: false`, then `--per-sheet` / `--jobs`.

---

## The scene schema

```json
{
  "model": "product.glb",
  "primitive": "bottle",
  "productColor": "#0000FF",
  "labelColor": null,
  "capColor": null,
  "label": {"image": null, "text": "BRAND", "wrap": true, "repeat": 1},
  "studio": "gradient",
  "move": "turntable",
  "lighting": "studio",
  "floor": true,
  "reflection": 0.35,
  "shadow": true,
  "headline": "Built for the shelf.",
  "sub": "Retail execution, rendered.",
  "seed": 7
}
```

| field | meaning |
| --- | --- |
| `model` | path to a `.glb` / `.gltf`. Relative paths resolve against the scene file. When set, `primitive` is ignored. `null` for a parametric pack. |
| `primitive` | `bottle` · `box` · `can` · `tube` · `sachet` · `phone` · `jar`. Used when `model` is null. |
| `productColor` | the pack body. Defaults to brand blue. |
| `labelColor` | the label stock. Defaults to brand white. |
| `capColor` | cap / closure / foil. Defaults to the brand accent. |
| `label.image` | artwork wrapped round the body. A local file; resolved to `file://`. |
| `label.text` | set in the brand face instead, when there is no artwork. |
| `label.wrap` | `true` = a full 360° band; `false` = a front decal on a 130° arc, turned to face the camera. Ignored for flat packs, which always take a decal on the front face. |
| `label.repeat` | How many times the lockup is laid down around a band. **Default 1 — a real label wraps once, it does not tile.** `true` means 3; a number is clamped to 8. Meaningless on a flat pack. |
| `studio` | `gradient` (brand ramp) · `seamless` (white sweep) · `dark` · `pedestal` (plinth) · `floating` (no floor). |
| `move` | `turntable` · `dolly-in` · `orbit` · `hero-reveal` · `rise` · `static`. All eased, all settled by `t=1`. |
| `lighting` | `studio` · `dramatic` · `soft` · `retail`. Moves the key/fill/rim ratios and the exposure, never the geometry. |
| `floor` | draw the floor plane. Forced off for `floating`. |
| `reflection` | `0`–`1`. Above `0.02` this adds a mirrored second copy of the pack, so it roughly doubles geometry cost. |
| `shadow` | real shadow-mapped contact shadow from the key light. |
| `headline` / `sub` | optional 2D overlay, brand face, inside the safe margin. |
| `seed` | seeds the PRNG. Same seed, same dust. |

A Video IR fragment is accepted too: if the file has a `visual` key, `visual.data` is used.

---

## Determinism

Non-negotiable, and the reason several obvious things are banned:

- **No `requestAnimationFrame`, no animation loop.** The clock is read synchronously from the URL,
  the scene is built once, and `renderer.render()` is called exactly once per cell.
- **No CSS animations or transitions.** Chrome screenshots one moment; a CSS animation would be
  caught at an arbitrary point and the render would not reproduce.
- **No `Math.random()`, no `Date`.** All scatter comes from the seeded mulberry32 PRNG. A template
  that uses `Math.random()` looks fine frame by frame and *strobes* in the finished film.

Async work — web fonts, a label image, a `.glb` — cannot be waited for synchronously, so every cell
is drawn **again** after each async milestone lands. That stays deterministic because redrawing is
idempotent: it is the same pure function of `t`. Chrome's `--virtual-time-budget` holds the
screenshot until that work has drained.

Verified: the same `t` rendered in two separate Chrome launches is pixel-identical (max channel
delta 0), and six frames across the clock are six distinct images.

> Headless Chrome writes the screenshot reliably but frequently **does not exit afterwards**. The
> renderer therefore drives the process to the *file*, polling for a complete PNG (signature +
> `IEND`) and then tearing Chrome down. Waiting on the exit code adds a full timeout to every sheet.
> `build_video.py` does the same thing for the same reason.

---

## Supplying a `.glb`

Point `model` at the file. It loads over `file://` with no network.

The vendored `GLTFLoader.esm.js` is ES module source, and ES modules do not load from `file://`
without relaxing Chrome's security. `render_cgi.build_gltf_shim()` therefore transpiles it at render
time into `vendor/gltfloader.umd.js` in the work directory: it binds the named `three` imports out of
the UMD global, drops the `export`, and publishes `THREE.GLTFLoader`. The one helper it imports from
`BufferGeometryUtils` (`toTrianglesDrawMode`, used only for the triangle-strip and triangle-fan
primitive modes that almost no exporter emits) is reimplemented in the shim rather than vendoring a
second file. Chrome is given `--allow-file-access-from-files` so the loader's XHR can read the model.

**Models are auto-framed.** A client's export arrives at an arbitrary scale, in an arbitrary unit, on
an arbitrary origin — one is 3cm tall and the next is 3000. `normalise()` measures the bounding box,
scales it to a standard height, centres it in X and Z, and sits it on the floor, so one camera rig
frames every product without per-model tuning. A pack much wider than it is tall is clamped on width
instead, so it cannot grow out of frame. Tested with a box 3000 units tall centred 5000 units off
origin; it frames correctly.

If the model fails to load, the scene falls back to `primitive` and paints a fault banner into the
frame — a *visibly* failed frame, never a silently blank one.

---

## How a wrapped label is placed

Three things have to agree or the lockup reads as clipped or duplicated. All of them are
handled in `attachLabel()`.

1. **Canvas aspect matches the unwrapped surface.** A band's unwrapped size is its
   circumference × its height — 4.9:1 on the bottle, 7.5:1 on the jar. A fixed-aspect
   canvas stretched onto that distorts every glyph horizontally, so the canvas is built
   at `surfaceWidth / labelH`.
2. **The lockup is sized against the readable arc, not the circumference.** A camera sees
   at most 180° of a cylinder and the last ~35° of that are too foreshortened to read. Type
   sized against the *full* circumference gets 190°+ on a narrow pack like a can or a tube,
   so it wraps past both silhouette edges and looks clipped from every angle.
   `READABLE_ARC_FRAC` caps it at 110°.
3. **The seam is steered to the back.** `CylinderGeometry` puts `u=0` at `thetaStart`,
   which with `thetaStart=0` is the `+Z` face — so the texture seam lands square on the
   front of the pack, pointing at the camera. `faceOffset()` sets `map.offset.x` so the
   lockup centres on `HERO.azimuth` and the seam falls at `azimuth - π`, opposite the
   camera. `wrapS` is `RepeatWrapping` so sampling either side of the seam wraps round the
   band instead of smearing the edge pixel along it.

The offset applies **only** to a wrapped band. A flat decal and the 130° front arc are
positioned by geometry instead (the arc is rotated by `HERO.azimuth`), and offsetting their
texture would slide the artwork off its face.

A turntable naturally rotates the seam through shot. That is correct and invisible: the
canvas edges are plain label stock and the hairline rules run the full width, so they stay
continuous across the join, and the back of the pack shows clean stock exactly as a real
single-wrap label does.

## Adding a primitive

Primitives live in the `PRIMITIVES` map in `product.html`. Each builder returns:

```js
{ group, labelY, labelR, labelH, flat, faceW }
```

in its own natural units — proportion is all that matters, because `normalise()` rescales everything
to the standard height afterwards. `labelY` / `labelR` / `labelH` place the label band; `flat: true`
means the label is a front decal on a plane rather than a wrapped cylinder.

Build round packs with `lathe(profile)` and flat ones with `roundedSlab(w, h, d, r)`. Then add the
name to the `pick()` list in `product.html` **and** to `PRIMITIVES` in `render_cgi.py` so the
validator recognises it.

Two things to get right, learned the hard way:

- **Proportion is everything.** Because packs are normalised to a fixed *height*, the profile's
  width-to-height ratio is the only thing controlling how fat the pack reads. A tube at the wrong
  ratio renders as a jar with a tab on it.
- **A lathe cannot pinch a circular section flat.** For a crimped tube, stop the lathe as a
  near-full cylinder and *overlap* it with the crimp slab. Tapering the lathe to a point leaves a
  dome with a tab hovering above it.

---

## Honest limits

This is a CPU rasteriser. It cannot do, and will not be made to do:

- **No path tracing, no global illumination.** Lighting is three directional lights plus an image-
  based environment baked from the brand gradient with `PMREMGenerator`. Light does not bounce.
- **No caustics.** Clear glass, liquid and anything refractive will not read convincingly.
- **No subsurface scattering.** Skin, wax, thick plastic and food do not look right.
- **Reflections are approximations.** The floor reflection is a mirrored copy of the pack under a
  part-transparent glossy floor, with double-sided materials because flipping Y reverses the
  winding. It is not a ray-traced reflection and will not survive close inspection.
- **The backdrop is unlit** by design, so it reads as a clean sweep instead of catching the key
  light and the product's shadow.
- **Antialiasing is MSAA on SwiftShader** — fine at 1080p, expensive above it.

What it *is* good for: a clean, on-brand, deterministic product film or hero still, built from the
brand profile, reproducible from a sidecar, and cheap enough to iterate on with `--preview`.

**This produces a clean branded product film. It does not produce a Nike spot.** If the brief needs
photoreal cosmetics, route it to a real 3D artist and say so early.
