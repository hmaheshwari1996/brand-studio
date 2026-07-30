---
description: Render a branded CGI product shot or film — a 3D pack in a brand studio, from a .glb or a parametric primitive.
argument-hint: "[what the product is, e.g. 'a hero shot of the shampoo bottle' or '6s turntable of the carton']"
---

# /brand-cgi

Puts a client's product in a branded 3D studio and renders it — a still for a deck, or a short film.
```sh
ROOT="${CLAUDE_PLUGIN_ROOT:?set CLAUDE_PLUGIN_ROOT to the brand-studio directory, or run this from Claude Code}"
PY="$HOME/.cache/brand-studio/venv/bin/python"
```

**Be honest about the cost up front.** This is CPU rendering through SwiftShader — there is no GPU
and no Blender. A hero still is ~15s. A 6-second turntable is a few minutes. A 30-second full-CGI
film is an overnight job. Say so *before* the user asks for thirty seconds, not after.

## 1. Brand
Resolve and confirm the brand (**brand-kit**), unless it is already settled in this session. Every
colour, the backdrop, the rim light and the overlay face come from the profile.

## 2. What is the product?
Ask, and do not guess:
- **What is it**, and **is there a 3D model?** A `.glb`/`.gltf` is auto-framed, so an export at any
  scale or origin is fine.
- **If not**, pick the closest primitive and *say plainly which one and that it is a stand-in*:
  `bottle`, `box`, `can`, `tube`, `sachet`, `phone`, `jar`. This is the normal case — most clients
  send photos, not geometry. A stand-in pack in the right studio is a usable frame; a stand-in
  passed off as the client's actual pack is not.
- **Label** — artwork (`label.image`, a local file) or set text (`label.text`).

## 3. The look
Choose with the user, defaulting sensibly rather than interrogating them:
- `studio` — `gradient` (brand ramp) · `seamless` (white sweep) · `dark` · `pedestal` · `floating`
- `move` — `turntable` · `dolly-in` · `orbit` · `hero-reveal` · `rise` · `static`
- `lighting` — `studio` · `dramatic` · `soft` · `retail`

Write the scene to a JSON file — schema in `templates/cgi/README.md`.

## 4. Preview FIRST — always
```sh
"$PY" "$ROOT/scripts/render_cgi.py" --data scene.json --brand <id> --preview --out /tmp/look.png
```
Four stills across the clock, one Chrome launch, ~25s. **Show the contact sheet and get agreement on
the look before committing to a full render** — skipping this is how forty minutes get spent on the
wrong lighting preset. Iterate on the preview until the user is happy.

## 5. Commit to the render
```sh
# a still for a deck
"$PY" "$ROOT/scripts/render_cgi.py" --data scene.json --still 0.55 --out hero.png
# a film -- --yes skips the confirmation prompt; drop it to be asked
"$PY" "$ROOT/scripts/render_cgi.py" --data scene.json --frames 90 --per-sheet 8 \
    --out film.mp4 --yes
```
It calibrates on the first sheet and prints measured throughput and a projected time **before** the
long part. Relay that projection. If it is longer than the user expected, offer: fewer frames, a
smaller `--width/--height`, `reflection: 0`, or `shadow: false`. Report the actual time afterwards.

## 6. Where it goes
Into a deck as a PNG, or into a Video IR scene with `visual.kind` `"image"` (a still) or `"video"`
(the mp4). A sidecar `.cgi.json` lands beside every output with the scene, seed, settings and
timings, so a render can be reproduced exactly.

## Limits — state these plainly if asked
CPU rasterisation, so: no path tracing, no caustics, no subsurface scattering. Reflections are a
mirrored copy under a glossy floor, not real ray-traced ones. Clear glass and liquid do not read
convincingly. This produces a **clean branded product film — not a Nike spot.** If the brief needs
photoreal cosmetics, say so and route it to a real 3D artist.
