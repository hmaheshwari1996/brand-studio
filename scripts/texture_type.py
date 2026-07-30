#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Turn a reference photograph into a display TYPE TREATMENT.

The honest framing, because it decides what this script can and cannot do:

    A photograph cannot become a .ttf. A font file carries outlines -- contours,
    sidebearings, kerning -- and nothing else. It has no colour, no grain, no
    gloss. So "make a font from this lipstick swatch" is not a thing any tool
    can do, here or in Photoshop.

    What a photograph CAN become is a type TREATMENT: the reference's palette,
    grain, gloss and edge character rendered onto letterforms. That is what this
    script produces -- an RGBA PNG of a specific string, plus a JSON sidecar that
    makes the result reproducible.

    The letterforms stay the brand face. Channelplay's guidelines say "Poppins
    only, never substitute another face", so the skeleton always comes from
    brand.type.weightToPptxFamily and only the FILL and the EDGES come from the
    photo. That is a hard constraint, not a preference: a treatment that changed
    the skeleton would be a different typeface wearing the brand's colours, and
    the validator would be right to reject it.

Pipeline
    1. analyse   separate subject from background, quantise a palette, measure
                 grain / directionality / gloss / raggedness
    2. classify  map those statistics onto a style preset (or take --style)
    3. brand     nearest brand token per palette colour (delta E), WCAG contrast
                 of the treatment's mean colour against the intended background
    4. render    brand TTF -> L-mode mask -> edge perturbation -> texture tile
                 composited through the mask -> gloss pass -> RGBA
    5. record    sidecar JSON with everything needed to reproduce the artwork

Usage
    texture_type.py --ref swatch.jpg --text "CHANNELPLAY" --analyze-only --json
    texture_type.py --ref swatch.jpg --text "CHANNELPLAY" --preview --out hero.png
    texture_type.py --ref concrete.png --text "FIELD FORCE" --style ragged \\
                    --width 2400 --bg dark --accept-off-palette

Requires the plugin venv (pillow + numpy):
    ~/.cache/brand-studio/venv/bin/python scripts/texture_type.py ...
"""

from __future__ import absolute_import

import argparse
import datetime
import hashlib
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from lib import brandlib as bl  # noqa: E402

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError as _exc:  # pragma: no cover - environment problem, not logic
    sys.stderr.write(
        "texture_type: needs pillow and numpy. Run it with the plugin venv:\n"
        "  ~/.cache/brand-studio/venv/bin/python scripts/texture_type.py ...\n"
        "  (%s)\n" % _exc)
    raise SystemExit(1)

VERSION = "1.0.0"

#: weight -> filename fragments looked for in brands/<id>/assets/fonts/.
#: Deliberately duplicated from build_video.py rather than imported: this script
#: must not drag the whole video builder (and ffmpeg probing) into a still render.
FONT_WEIGHT_FILES = {
    100: ("Thin",),
    200: ("ExtraLight", "Extralight"),
    300: ("Light",),
    400: ("Regular", "Book"),
    500: ("Medium",),
    600: ("SemiBold", "Semibold", "DemiBold"),
    700: ("Bold",),
    800: ("ExtraBold", "Extrabold"),
    900: ("Black", "Heavy"),
}

#: Longest side the analysis runs at. Statistics are scale-sensitive, so every
#: reference is normalised to the same working resolution before measuring --
#: otherwise a 6000px phone photo and a 900px crop of the same texture would
#: report different grain.
ANALYSIS_MAX_SIDE = 900

STYLES = ("auto", "smooth", "ragged", "grainy", "glossy", "matte")

#: Per-style rendering parameters.
#:   edge_blur_em   pre-blur of the glyph mask, in em -- gives the noise a ramp
#:                  to wander across. 0 keeps PIL's own antialiasing untouched.
#:   edge_amp       how far the noise displaces the edge (0 = no displacement)
#:   shoulder       half-width of the soft threshold. 0.5 == identity (no
#:                  re-sharpening), small values snap the edge back to crisp.
#:   grain_mix      how much noise is mixed into the FILL luminance
#:   gloss_mul      multiplier on the measured gloss for the specular pass
#:   flatten        pulls fill luminance toward its median (matte)
STYLE_PARAMS = {
    "smooth": {"edge_blur_em": 0.000, "edge_amp": 0.00, "shoulder": 0.50,
               "grain_mix": 0.00, "gloss_mul": 0.6, "flatten": 0.0},
    "ragged": {"edge_blur_em": 0.022, "edge_amp": 0.42, "shoulder": 0.10,
               "grain_mix": 0.10, "gloss_mul": 0.9, "flatten": 0.0},
    "grainy": {"edge_blur_em": 0.008, "edge_amp": 0.14, "shoulder": 0.26,
               "grain_mix": 0.55, "gloss_mul": 0.7, "flatten": 0.0},
    "glossy": {"edge_blur_em": 0.004, "edge_amp": 0.06, "shoulder": 0.34,
               "grain_mix": 0.05, "gloss_mul": 1.8, "flatten": 0.0},
    "matte":  {"edge_blur_em": 0.006, "edge_amp": 0.10, "shoulder": 0.32,
               "grain_mix": 0.12, "gloss_mul": 0.0, "flatten": 0.55},
}

#: delta E (CIE76) bands for "how far from the brand palette is this?".
#: 2.3 is the just-noticeable difference; 10 is "same colour family, different
#: shade"; past 25 you are looking at a different hue entirely.
DE_ON_PALETTE = 10.0
DE_ADJACENT = 25.0


class Fail(Exception):
    """Anything that should stop the run with a human-readable message."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1 << 16)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def now_iso():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def rgb_to_hex_t(t):
    return "#%02X%02X%02X" % (int(round(t[0])), int(round(t[1])), int(round(t[2])))


def shlex_quote(s):
    s = str(s)
    if s and all(c.isalnum() or c in "-_./=:#," for c in s):
        return s
    return "'" + s.replace("'", "'\\''") + "'"


# ---------------------------------------------------------------------------
# BRAND PLUMBING
# ---------------------------------------------------------------------------

def resolve_brand_profile(name):
    """Load a brand profile, tolerating a human-typed name."""
    try:
        return bl.load_brand(name)
    except bl.BrandNotFound:
        res = bl.resolve_brand(name)
        if res.get("brand"):
            return bl.load_brand(res["brand"])
        known = ", ".join(b["id"] for b in bl.list_brands()) or "(none)"
        raise Fail("brand '%s' not found. Known brands: %s" % (name, known))


def font_file_for_weight(brand, weight):
    """Absolute path of the brand TTF for ``weight``.

    Raises when the brand does not approve the weight (bl.weight_to_family does
    that for us) or when no file backs it -- silently falling back to a system
    face would break the one constraint this tool must not break.
    """
    family = bl.weight_to_family(brand, weight)
    fonts_dir = os.path.join(brand.get("_dir", ""), "assets", "fonts")
    if not os.path.isdir(fonts_dir):
        raise Fail(
            "brand '%s' has no assets/fonts directory (%s). The treatment must be "
            "rendered in the brand face, so there is nothing safe to fall back to."
            % (brand.get("id"), fonts_dir))
    available = [f for f in sorted(os.listdir(fonts_dir))
                 if f.lower().endswith((".ttf", ".otf"))]
    for frag in FONT_WEIGHT_FILES.get(int(weight), ()):
        for fname in available:
            stem = os.path.splitext(fname)[0].lower()
            if stem.endswith("-" + frag.lower()) or stem.endswith(frag.lower()):
                return os.path.join(fonts_dir, fname), family
    raise Fail("no font file for weight %s (%s) in %s. Present: %s"
               % (weight, family, fonts_dir, ", ".join(available) or "(none)"))


def core_palette_index(brand):
    """{'#RRGGBB': 'token'} of the brand's IDENTITY colours only.

    brandlib.palette_index() returns every approved hex, which includes the
    semantic ramp -- so a lipstick red lands 4 delta E from semantic.danger and
    looks "on palette" when it is nothing of the sort. Semantic tokens are
    status signals for UI, never display colours, so the identity distance is
    measured separately against brand.* plus the gradient stops.
    """
    out = {}
    color = (brand or {}).get("color") or {}
    for name, hexv in (color.get("brand") or {}).items():
        if bl.is_hex(hexv):
            out[bl.normalize_hex(hexv)] = "brand.%s" % name
    for gname, grad in (color.get("gradient") or {}).items():
        if not isinstance(grad, dict):
            continue
        for i, stop in enumerate(grad.get("stops") or []):
            if bl.is_hex(stop):
                out.setdefault(bl.normalize_hex(stop), "gradient.%s.%d" % (gname, i))
    return out


def background_hex(brand, spec):
    """Resolve --bg to a concrete hex (or None for transparent)."""
    color = (brand or {}).get("color") or {}
    if spec == "transparent":
        return None
    if spec == "light":
        return bl.normalize_hex((color.get("neutral") or {}).get("0") or "#FFFFFF")
    if spec == "dark":
        navy = (color.get("brand") or {}).get("navy")
        if bl.is_hex(navy):
            return bl.normalize_hex(navy)
        return bl.normalize_hex((color.get("neutral") or {}).get("900") or "#12141C")
    if bl.is_hex(spec):
        return bl.normalize_hex(spec)
    raise Fail("--bg must be light, dark, transparent or #RRGGBB, got %r" % spec)


# ---------------------------------------------------------------------------
# ANALYSIS
# ---------------------------------------------------------------------------

def load_reference(path):
    if not os.path.isfile(path):
        raise Fail("reference image not found: %s" % path)
    try:
        im = Image.open(path)
        im.load()
    except Exception as exc:
        raise Fail("could not read %s as an image: %s" % (path, exc))
    return im.convert("RGB")


def analysis_copy(im):
    w, h = im.size
    scale = float(ANALYSIS_MAX_SIDE) / max(w, h)
    if scale >= 1.0:
        return im.copy(), 1.0
    small = im.resize((max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
                      Image.LANCZOS)
    return small, scale


def luma(arr):
    """Rec.709 luminance of an HxWx3 uint8 array, as float 0..255."""
    a = arr.astype(np.float64)
    return 0.2126 * a[:, :, 0] + 0.7152 * a[:, :, 1] + 0.0722 * a[:, :, 2]


def _hf_energy(lum, mask):
    """Mean local luminance gradient inside ``mask`` -- how textured a side is."""
    px = mask[:, 1:] & mask[:, :-1]
    py = mask[1:, :] & mask[:-1, :]
    vals = []
    if px.any():
        vals.append(float(np.abs(lum[:, 1:] - lum[:, :-1])[px].mean()))
    if py.any():
        vals.append(float(np.abs(lum[1:, :] - lum[:-1, :])[py].mean()))
    return sum(vals) / len(vals) if vals else 0.0


def subject_mask(rgb_small):
    """Boolean HxW mask of the textured SUBJECT, plus how it was derived.

    Saturation is the signal that works on a swatch photographed on paper: the
    pigment is saturated, the paper is not. It is meaningless on a full-frame
    texture (concrete, foliage filling the frame), so two guards fall back to
    using the whole frame rather than carving a random 30% out of it.
    """
    hsv = np.array(Image.fromarray(rgb_small).convert("HSV"))
    sat = hsv[:, :, 1].astype(np.float64)
    val = hsv[:, :, 2].astype(np.float64)
    lit = val > 25.0

    sat_mean = float(sat.mean())
    sat_std = float(sat.std())
    mask = (sat > 1.15 * sat_mean) & lit
    frac = float(mask.mean())

    reason = "saturation > 1.15x frame mean (%.1f) and value > 25" % sat_mean
    method = "saturation"

    # Saturation proposes the split; texture decides which side is the subject.
    # White chalk on a dark board, silver on black, paper on a coloured ground --
    # in all of them the GROUND is the more saturated side, and saturation alone
    # would sample the backdrop. The test is high-frequency energy, not variance:
    # variance is high for any two-population mix (dark ground plus lit leaves),
    # while local gradient energy is high only where a surface is actually
    # textured, which is what a fill needs.
    lum_all = luma(rgb_small)
    comp = (~mask) & lit
    if frac >= 0.08 and float(comp.mean()) >= 0.08:
        hf_in = _hf_energy(lum_all, mask)
        hf_out = _hf_energy(lum_all, comp)
        if hf_out > hf_in * 1.25:
            mask = comp
            frac = float(mask.mean())
            method = "saturation-inverted"
            reason = ("the LESS saturated side carries the texture (local gradient "
                      "energy %.2f vs %.2f), so the subject is the surface "
                      "saturation would have discarded" % (hf_out, hf_in))
    if sat_mean < 25.0 or sat_std < 8.0:
        method = "full-frame"
        reason = ("frame is near-neutral (mean saturation %.1f, sd %.1f) -- no "
                  "colour-defined subject, treating the whole frame as texture"
                  % (sat_mean, sat_std))
        mask = lit
    elif frac < 0.08 or frac > 0.92:
        method = "full-frame"
        reason = ("saturation split isolated %.0f%% of the frame, which is not a "
                  "subject -- treating the whole frame as texture" % (frac * 100.0))
        mask = lit
    if not mask.any():
        mask = np.ones(sat.shape, dtype=bool)
        method = "full-frame"
        reason = "reference is uniformly dark; using every pixel"
    return mask, {"method": method, "reason": reason,
                  "coverage": round(float(mask.mean()), 4)}


def extract_palette(rgb_small, mask, top=5):
    """Top ``top`` colours of the SUBJECT, by median-cut quantisation."""
    q = Image.fromarray(rgb_small).quantize(colors=8, method=Image.MEDIANCUT)
    idx = np.array(q)
    pal = q.getpalette() or []
    counts = np.bincount(idx[mask].ravel(), minlength=8).astype(np.float64)
    total = float(counts.sum()) or 1.0
    order = np.argsort(-counts)
    out = []
    for i in order[:top]:
        if counts[i] <= 0:
            continue
        base = int(i) * 3
        if base + 2 >= len(pal):
            continue
        out.append({"hex": rgb_to_hex_t(pal[base:base + 3]),
                    "share": round(float(counts[i] / total), 4)})
    return out


def silhouette_jitter(mask):
    """How much the subject's OUTLINE wanders, or None when it has no outline.

    Total perimeter is the obvious raggedness measure and it is the wrong one:
    a frame of scattered leaves has an enormous perimeter and no torn edge at
    all, while a lipstick drag has a modest perimeter and a comb-like left and
    right edge. What separates them is coherence -- one blob versus a scatter --
    so the outline is only measured when the subject fills most of its own
    bounding box, and then it is measured as row-to-row jitter of the leftmost
    and rightmost (and top and bottom) pixel, which is exactly what the eye
    reads as "torn".
    """
    rows = np.nonzero(mask.any(axis=1))[0]
    cols = np.nonzero(mask.any(axis=0))[0]
    if rows.size < 8 or cols.size < 8:
        return None
    sub = mask[rows.min():rows.max() + 1, cols.min():cols.max() + 1]
    bh, bw = sub.shape
    fill = float(sub.mean())
    if fill < 0.55:
        return None
    rsel = sub.any(axis=1)
    csel = sub.any(axis=0)
    left = np.argmax(sub, axis=1)[rsel].astype(np.float64)
    right = (bw - 1 - np.argmax(sub[:, ::-1], axis=1))[rsel].astype(np.float64)
    top = np.argmax(sub, axis=0)[csel].astype(np.float64)
    bottom = (bh - 1 - np.argmax(sub[::-1, :], axis=0))[csel].astype(np.float64)
    if left.size < 4 or top.size < 4:
        return None
    jx = 0.5 * (float(np.abs(np.diff(left)).mean()) + float(np.abs(np.diff(right)).mean()))
    jy = 0.5 * (float(np.abs(np.diff(top)).mean()) + float(np.abs(np.diff(bottom)).mean()))
    # The roughest axis sets the reading. A lipstick drag is torn down its left
    # and right ends and dead straight top and bottom; averaging the two axes
    # would halve a real signal because the other edge happens to be clean.
    # 5px of average row-to-row wander (at the 900px analysis width) is fully
    # torn -- that is what a chalk or paint edge looks like.
    return {"jitter": clamp(max(jx, jy) / 5.0, 0.0, 1.0),
            "jitterX": round(jx, 3), "jitterY": round(jy, 3),
            "fill": round(fill, 4)}


def texture_stats(rgb_small, mask):
    """Grain, directionality, gloss and raggedness of the subject."""
    lum = luma(rgb_small)
    vals = lum[mask]
    if vals.size < 16:
        vals = lum.ravel()

    grain = float(vals.std())
    p50 = float(np.percentile(vals, 50))
    p95 = float(np.percentile(vals, 95))
    gloss = (p95 - p50) / 255.0
    # p95 - p50 is the spread the proof of concept measured, and on a noisy
    # reference most of that spread is just noise: for a normal distribution
    # p95 - p50 is already 1.645 sd before any highlight exists. The specular
    # figure is the EXCESS over that, which is what a gloss pass should follow --
    # otherwise concrete reads as shiny purely because it is gritty.
    specular = clamp(((p95 - p50) - 1.645 * grain) / 255.0, 0.0, 1.0)

    # Edge energy per axis. dx is the gradient BETWEEN horizontal neighbours, so
    # a texture of horizontal drag marks changes little along x and a lot along
    # y -- dy > dx means horizontal streaking.
    pair_x = mask[:, 1:] & mask[:, :-1]
    pair_y = mask[1:, :] & mask[:-1, :]
    dx = np.abs(lum[:, 1:] - lum[:, :-1])
    dy = np.abs(lum[1:, :] - lum[:-1, :])
    ex = float(dx[pair_x].mean()) if pair_x.any() else 0.0
    ey = float(dy[pair_y].mean()) if pair_y.any() else 0.0
    denom = ex + ey
    directionality = ((ey - ex) / denom) if denom > 1e-6 else 0.0
    if directionality > 0.15:
        direction = "horizontal streaks"
    elif directionality < -0.15:
        direction = "vertical streaks"
    else:
        direction = "isotropic"

    full_frame = float(mask.mean()) > 0.985
    sil = None if full_frame else silhouette_jitter(mask)

    edge_norm = clamp(((ex + ey) / 2.0) / 24.0, 0.0, 1.0)
    grain_norm = clamp(grain / 64.0, 0.0, 1.0)
    if sil is None:
        raggedness = edge_norm
    else:
        raggedness = clamp(0.62 * sil["jitter"] + 0.38 * edge_norm, 0.0, 1.0)

    return {
        "grain": round(grain, 2),
        "grainNorm": round(grain_norm, 4),
        "edgeEnergyX": round(ex, 3),
        "edgeEnergyY": round(ey, 3),
        "edgeNorm": round(edge_norm, 4),
        "directionality": round(directionality, 4),
        "direction": direction,
        "gloss": round(gloss, 4),
        "specular": round(specular, 4),
        "glossP50": round(p50, 1),
        "glossP95": round(p95, 1),
        "outlineJitter": (None if sil is None else round(sil["jitter"], 4)),
        "outlineJitterPx": (None if sil is None else [sil["jitterX"], sil["jitterY"]]),
        "outlineFill": (None if sil is None else sil["fill"]),
        "outlineNote": ("subject is the whole frame -- no outline to measure"
                        if full_frame else
                        ("subject is a scatter of texture elements, not one "
                         "silhouette -- no outline to measure" if sil is None
                         else "silhouette measured over %.0f%% of its own bounding box"
                              % (sil["fill"] * 100.0))),
        "raggedness": round(raggedness, 4),
        "meanLuma": round(float(vals.mean()), 1),
    }


def classify_style(stats):
    """Pick a style preset from the measured statistics, and say why.

    Ordered rules, first match wins. The order matters: a torn edge is the most
    visible property of a reference, so it outranks a shiny one.
    """
    rag = stats["raggedness"]
    grain = stats["grainNorm"]
    edge = stats["edgeNorm"]
    spec = stats["specular"]
    if rag >= 0.42:
        return "ragged", ("raggedness %.2f (>= 0.42)%s -- the reference has a broken, "
                          "torn edge, so the letterforms get one too"
                          % (rag, "" if stats["outlineJitter"] is None
                             else ", outline jitter %.2f" % stats["outlineJitter"]))
    if grain >= 0.22 and edge >= 0.18:
        return "grainy", ("grain %.2f (>= 0.22) and edge energy %.2f (>= 0.18) -- the "
                          "luminance scatter is both deep (%.0f levels) and "
                          "high-frequency, which is grain, so it goes into the fill"
                          % (grain, edge, stats["grain"]))
    if spec >= 0.045:
        return "glossy", ("specular %.2f (>= 0.045): the top 5%% of the subject sits "
                          "%.0f luma above its median, %.0f more than its own grain "
                          "explains -- that is a highlight, not noise"
                          % (spec, stats["glossP95"] - stats["glossP50"],
                             spec * 255.0))
    if spec <= 0.012:
        return "matte", ("specular %.3f (<= 0.012) -- once grain is discounted the "
                         "subject has no highlight at all, so the fill is flattened "
                         "rather than lit" % spec)
    return "smooth", ("raggedness %.2f, grain %.2f, specular %.2f all sit mid-range -- "
                      "clean edges, texture in the fill only" % (rag, grain, spec))


def analyse(ref_path):
    """Full analysis of a reference photo. Pure measurement, no brand opinion."""
    full = load_reference(ref_path)
    small, scale = analysis_copy(full)
    arr = np.array(small)
    mask, subject = subject_mask(arr)
    palette = extract_palette(arr, mask)
    stats = texture_stats(arr, mask)
    style, why = classify_style(stats)
    return {
        "reference": {
            "path": os.path.abspath(ref_path),
            "size": [full.size[0], full.size[1]],
            "analysedAt": [small.size[0], small.size[1]],
            "sha256": sha256_file(ref_path),
        },
        "subject": subject,
        "palette": palette,
        "stats": stats,
        "styleAuto": style,
        "styleWhy": why,
        "_full": full,
        "_mask": mask,
        "_scale": scale,
    }


# ---------------------------------------------------------------------------
# BRAND CHECK
# ---------------------------------------------------------------------------

def palette_distance(brand, palette):
    """Per-colour nearest brand token, plus the identity-palette verdict."""
    approved = bl.palette_index(brand)
    core = core_palette_index(brand)
    rows = []
    wsum = 0.0
    wcore = 0.0
    for entry in palette:
        name, thex, de = bl.nearest_token(entry["hex"], approved)
        cname, chex, cde = bl.nearest_token(entry["hex"], core)
        rows.append({
            "hex": entry["hex"],
            "share": entry["share"],
            "nearestToken": name,
            "nearestHex": thex,
            "deltaE": round(de, 2),
            "nearestCoreToken": cname,
            "nearestCoreHex": chex,
            "coreDeltaE": round(cde, 2),
            "semanticOnly": bool(name and str(name).startswith("semantic.")),
        })
        wsum += de * entry["share"]
        wcore += cde * entry["share"]

    if wcore <= DE_ON_PALETTE:
        verdict = "on-palette"
    elif wcore <= DE_ADJACENT:
        verdict = "adjacent"
    else:
        verdict = "off-palette"
    semantic_only = bool(rows) and all(r["semanticOnly"] for r in rows[:2])
    return {
        "colors": rows,
        "weightedDeltaE": round(wsum, 2),
        "weightedCoreDeltaE": round(wcore, 2),
        "verdict": verdict,
        "semanticMatchOnly": semantic_only,
        "thresholds": {"onPalette": DE_ON_PALETTE, "adjacent": DE_ADJACENT},
    }


def contrast_check(brand, mean_hex, bg_spec, brand_bg):
    """WCAG contrast of the treatment's mean colour against its background(s)."""
    min_large = float((((brand or {}).get("colorRules") or {})
                       .get("minContrastLarge") or 3.0))
    targets = []
    if brand_bg is None:
        targets.append(("light", background_hex(brand, "light")))
        targets.append(("dark", background_hex(brand, "dark")))
    else:
        targets.append((bg_spec, brand_bg))
    rows = []
    for label, hexv in targets:
        ratio = bl.contrast_ratio(mean_hex, hexv)
        rows.append({"background": label, "backgroundHex": hexv,
                     "ratio": round(ratio, 2), "passes": ratio >= min_large})
    return {
        "meanColor": mean_hex,
        "threshold": min_large,
        "results": rows,
        "passes": all(r["passes"] for r in rows),
        "note": ("transparent output is checked against BOTH the brand's light and "
                 "dark surfaces, because a transparent PNG can land on either"
                 if brand_bg is None else
                 "checked against the declared background only"),
    }


# ---------------------------------------------------------------------------
# TEXTURE TILE
# ---------------------------------------------------------------------------

def seamless_tile(full, mask, scale):
    """Mirror-tiled 2x2 crop of the subject's interquartile region.

    Cropping to the interquartile box keeps the sample away from the subject's
    own edges (which are background, not texture); mirroring makes it repeat
    without a visible seam, which matters because the tile is laid across a
    canvas several times wider than the crop.
    """
    ys, xs = np.nonzero(mask)
    if xs.size < 16:
        crop_box = (0, 0, full.size[0], full.size[1])
    else:
        x0 = int(np.percentile(xs, 25))
        x1 = int(np.percentile(xs, 75))
        y0 = int(np.percentile(ys, 25))
        y1 = int(np.percentile(ys, 75))
        inv = 1.0 / (scale or 1.0)
        crop_box = (int(x0 * inv), int(y0 * inv),
                    max(int(x1 * inv), int(x0 * inv) + 8),
                    max(int(y1 * inv), int(y0 * inv) + 8))
    crop = full.crop(crop_box)
    if crop.size[0] < 32 or crop.size[1] < 32:
        crop = crop.resize((max(32, crop.size[0]), max(32, crop.size[1])), Image.LANCZOS)
    w, h = crop.size
    tile = Image.new("RGB", (w * 2, h * 2))
    tile.paste(crop, (0, 0))
    tile.paste(crop.transpose(Image.FLIP_LEFT_RIGHT), (w, 0))
    tile.paste(crop.transpose(Image.FLIP_TOP_BOTTOM), (0, h))
    tile.paste(crop.transpose(Image.ROTATE_180), (w, h))
    return tile, list(crop_box)


def tile_fill(tile, size):
    w, h = size
    out = Image.new("RGB", (w, h))
    tw, th = tile.size
    for y in range(0, h, th):
        for x in range(0, w, tw):
            out.paste(tile, (x, y))
    return out


# ---------------------------------------------------------------------------
# TYPE LAYOUT
# ---------------------------------------------------------------------------

def line_metrics(font, text, tracking_px):
    """(ink_x0, ink_y0, ink_x1, ink_y1, advance) for one line.

    Positions are in the same frame PIL draws in with the default 'la' anchor,
    so the ink box below is exactly what will be rendered -- including the
    overshoot of an O and the descender of a y. Nothing gets clipped because
    nothing is estimated.
    """
    x = 0.0
    ink = [None, None, None, None]
    for ch in text:
        b = font.getbbox(ch)
        if b and b[2] > b[0] and b[3] > b[1]:
            gx0, gy0, gx1, gy1 = x + b[0], float(b[1]), x + b[2], float(b[3])
            ink[0] = gx0 if ink[0] is None else min(ink[0], gx0)
            ink[1] = gy0 if ink[1] is None else min(ink[1], gy0)
            ink[2] = gx1 if ink[2] is None else max(ink[2], gx1)
            ink[3] = gy1 if ink[3] is None else max(ink[3], gy1)
        x += font.getlength(ch) + tracking_px
    advance = x - tracking_px if text else 0.0
    if ink[0] is None:
        ink = [0.0, 0.0, 0.0, 0.0]
    return ink[0], ink[1], ink[2], ink[3], advance


def wrap_balanced(words, n_lines):
    """Split ``words`` into ``n_lines`` lines with the most even character load."""
    if n_lines <= 1 or len(words) < n_lines:
        return [" ".join(words)]
    best = None
    total = len(words)

    def cost(split):
        lines = []
        prev = 0
        for s in list(split) + [total]:
            lines.append(" ".join(words[prev:s]))
            prev = s
        widths = [len(x) for x in lines]
        return (max(widths), sum(abs(w - sum(widths) / float(len(widths))) for w in widths)), lines

    def recurse(start, remaining, acc):
        # At most two split points (three lines), so the candidate list is
        # O(words^2) and exhaustive search is cheaper than being clever.
        if remaining == 0:
            return [cost(acc)]
        out = []
        for i in range(start + 1, total - remaining + 1):
            out.extend(recurse(i, remaining - 1, acc + [i]))
        return out

    for c, lines in recurse(0, n_lines - 1, []):
        if best is None or c < best[0]:
            best = (c, lines)
    return best[1] if best else [" ".join(words)]


def fit_size(font_path, lines, tracking_em, target_w, lo=8.0, hi=3000.0):
    """Largest px size whose widest line's INK width fits ``target_w``."""
    def widest(size):
        f = ImageFont.truetype(font_path, int(round(size)))
        tr = tracking_em * size
        return max(line_metrics(f, ln, tr)[2] - line_metrics(f, ln, tr)[0] for ln in lines)

    size = 200.0
    for _ in range(6):
        w = widest(size)
        if w <= 0.5:
            break
        size = clamp(size * (target_w / w), lo, hi)
    size = clamp(size, lo, hi)
    while size > lo and widest(size) > target_w:
        size -= 1.0
    return int(round(size))


def layout_text(font_path, text, tracking_em, target_w, min_glyph_px, max_lines=3):
    """Choose a line break-up and a size that keeps the type at display scale.

    Fitting a long string to a fixed width makes the glyphs small; wrapping to a
    second line is what buys the size back. So the rule is: if a single line
    would render below the display floor and there is more than one word,
    wrap -- up to ``max_lines``.
    """
    words = [w for w in str(text).split() if w]
    if not words:
        raise Fail("--text is empty")
    lines = [" ".join(words)]
    size = fit_size(font_path, lines, tracking_em, target_w)
    used = 1
    while size < min_glyph_px and used < max_lines and len(words) > used:
        cand = wrap_balanced(words, used + 1)
        cand_size = fit_size(font_path, cand, tracking_em, target_w)
        if cand_size <= size:
            break
        lines, size, used = cand, cand_size, used + 1
    return lines, size


def render_mask(font_path, lines, size, tracking_em, align, margin):
    """L-mode glyph mask, cropped to ink + ``margin``. Returns (mask, ink box)."""
    font = ImageFont.truetype(font_path, int(size))
    tracking = tracking_em * size
    ascent, descent = font.getmetrics()
    line_h = int(round(size * 1.10))

    metrics = [line_metrics(font, ln, tracking) for ln in lines]
    ink_w = max(m[2] - m[0] for m in metrics)
    pad = int(math.ceil(margin))
    canvas_w = int(math.ceil(ink_w)) + 2 * pad + int(size)
    canvas_h = (len(lines) - 1) * line_h + ascent + descent + 2 * pad + int(size)

    img = Image.new("L", (canvas_w, canvas_h), 0)
    draw = ImageDraw.Draw(img)
    for i, (ln, m) in enumerate(zip(lines, metrics)):
        w = m[2] - m[0]
        if align == "left":
            ox = pad - m[0]
        elif align == "right":
            ox = pad + (ink_w - w) - m[0]
        else:
            ox = pad + (ink_w - w) / 2.0 - m[0]
        oy = pad + i * line_h
        x = float(ox)
        for ch in ln:
            draw.text((x, oy), ch, font=font, fill=255)
            x += font.getlength(ch) + tracking
    box = img.getbbox()
    if box is None:
        raise Fail("the text rendered to nothing -- check --text and the font file")
    box = (max(0, box[0] - pad), max(0, box[1] - pad),
           min(canvas_w, box[2] + pad), min(canvas_h, box[3] + pad))
    return img.crop(box), box


# ---------------------------------------------------------------------------
# COMPOSITE
# ---------------------------------------------------------------------------

def blurred_noise(shape, radius, rng):
    """Zero-mean blurred noise in roughly [-1, 1]."""
    h, w = shape
    n = (rng.random((h, w)) * 255.0).astype(np.uint8)
    n = np.array(Image.fromarray(n).filter(ImageFilter.GaussianBlur(radius)),
                 dtype=np.float64)
    n -= n.mean()
    sd = n.std()
    if sd > 1e-6:
        n /= sd
    return np.clip(n / 2.5, -1.0, 1.0)


def perturb_alpha(mask_img, params, stats, size_px, rng):
    """Break the glyph edge up the way the reference's own edge breaks up.

    The mask is pre-blurred so the noise has a ramp several pixels wide to
    displace, then re-thresholded with a soft shoulder to snap the edge back to
    something crisp but wandering. Interiors stay solid at every amplitude -- a
    treatment that punched holes through the stems would be prettier and
    illegible, and legibility is the whole point of a display face.
    """
    amp = params["edge_amp"] * (0.55 + 0.75 * stats["raggedness"])
    blur = params["edge_blur_em"] * size_px
    m = np.array(mask_img, dtype=np.float64) / 255.0
    if blur > 0.3:
        m = np.array(mask_img.filter(ImageFilter.GaussianBlur(blur)),
                     dtype=np.float64) / 255.0
    if amp > 1e-4:
        fine = blurred_noise(m.shape, max(1.0, size_px * 0.012), rng)
        coarse = blurred_noise(m.shape, max(3.0, size_px * 0.045), rng)
        m = m + amp * (0.6 * fine + 0.4 * coarse)
    sh = params["shoulder"]
    lo, hi = 0.5 - sh, 0.5 + sh
    a = np.clip((m - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
    return a


def gloss_field(shape, ink_box, amount, direction):
    """A soft luminance lift across the upper third of the ink -- a highlight.

    Real gloss on a swatch is a band, not a vignette, and it sits above centre
    because that is where the light is. When the reference streaks horizontally
    the band is widened along x so it reads as the same kind of sheen.
    """
    h, w = shape
    y0, y1 = ink_box[1], ink_box[3]
    span = max(1.0, float(y1 - y0))
    cy = y0 + 0.32 * span
    sigma = 0.24 * span
    ys = np.arange(h, dtype=np.float64)[:, None]
    band = np.exp(-((ys - cy) ** 2) / (2.0 * sigma * sigma))
    field = np.repeat(band, w, axis=1)
    if direction == "vertical streaks":
        xs = np.arange(w, dtype=np.float64)[None, :]
        field = field * (0.85 + 0.15 * np.cos(xs / max(8.0, w / 24.0)))
    return field * amount


def composite(tile, mask_img, alpha, stats, params, rng, size_px):
    """Texture through the mask, plus gloss / grain / flatten passes."""
    h, w = alpha.shape
    target_th = max(24, int(round(size_px * 1.25)))
    tw, th = tile.size
    tile_scaled = tile.resize((max(8, int(round(tw * target_th / float(th)))), target_th),
                              Image.LANCZOS)
    fill = np.array(tile_fill(tile_scaled, (w, h)), dtype=np.float64)

    if params["flatten"] > 0.0:
        lum = 0.2126 * fill[:, :, 0] + 0.7152 * fill[:, :, 1] + 0.0722 * fill[:, :, 2]
        med = float(np.median(lum[alpha > 0.5])) if (alpha > 0.5).any() else float(lum.mean())
        target = med + (lum - med) * (1.0 - params["flatten"])
        ratio = np.where(lum > 1.0, target / np.maximum(lum, 1.0), 1.0)
        fill = fill * ratio[:, :, None]

    if params["grain_mix"] > 0.0:
        g = blurred_noise((h, w), max(0.6, size_px * 0.004), rng)
        fill = fill + g[:, :, None] * (params["grain_mix"] * 90.0 *
                                       (0.4 + stats["grainNorm"]))

    # The lift follows the grain-corrected specular figure, times the style's
    # multiplier. x4 turns a typical specular reading (0.03-0.15) into a visible
    # but not blown-out band; matte zeroes it outright.
    amount = clamp(stats["specular"] * params["gloss_mul"] * 4.0, 0.0, 0.55)
    if amount > 0.005:
        box = (0, 0, w, h)
        rows = np.nonzero(alpha.max(axis=1) > 0.05)[0]
        if rows.size:
            box = (0, int(rows[0]), w, int(rows[-1]) + 1)
        field = gloss_field((h, w), box, amount, stats["direction"])
        fill = fill + field[:, :, None] * 255.0

    fill = np.clip(fill, 0.0, 255.0)
    return fill


def mean_color(fill, alpha):
    wsum = float(alpha.sum())
    if wsum < 1e-6:
        return "#000000"
    m = (fill * alpha[:, :, None]).sum(axis=(0, 1)) / wsum
    return rgb_to_hex_t(m)


# ---------------------------------------------------------------------------
# PREVIEW SHEET
# ---------------------------------------------------------------------------

def linear_gradient(size, stops, angle_deg):
    w, h = size
    ang = math.radians(float(angle_deg))
    dx, dy = math.sin(ang), -math.cos(ang)
    xs = np.linspace(0.0, 1.0, w)[None, :] * dx
    ys = np.linspace(0.0, 1.0, h)[:, None] * dy
    proj = xs + ys
    rng_span = float(proj.max() - proj.min()) or 1.0
    proj = (proj - proj.min()) / rng_span
    c0 = np.array(bl.hex_to_rgb(stops[0]), dtype=np.float64)
    c1 = np.array(bl.hex_to_rgb(stops[-1]), dtype=np.float64)
    arr = c0[None, None, :] + (c1 - c0)[None, None, :] * proj[:, :, None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def preview_sheet(brand, art, out_path, contrast):
    """Three panels: on light, on dark, on the brand's hero gradient.

    The point is not decoration. A treatment that sings on white can vanish on
    navy, and the only honest way to say so is to show all three next to the
    measured contrast number.
    """
    panel_w = 1200
    pad = int(panel_w * 0.05)
    art_w = panel_w - 2 * pad
    scale = art_w / float(art.size[0])
    art_h = max(1, int(round(art.size[1] * scale)))
    art_s = art.resize((art_w, art_h), Image.LANCZOS)
    cap_h = 54
    panel_h = art_h + 2 * pad + cap_h

    light = background_hex(brand, "light")
    dark = background_hex(brand, "dark")
    grad = ((brand.get("color") or {}).get("gradient") or {}).get("blue") or {}
    stops = grad.get("stops") or [dark, light]
    angle = grad.get("angle", 135)

    ratios = {}
    for row in contrast.get("results", []):
        ratios[row["backgroundHex"]] = row["ratio"]
    mean_hex = contrast.get("meanColor", "#000000")

    def ratio_for(hexv):
        if hexv in ratios:
            return ratios[hexv]
        return round(bl.contrast_ratio(mean_hex, hexv), 2)

    panels = [
        ("light", Image.new("RGB", (panel_w, panel_h), bl.hex_to_rgb(light)),
         "on light %s   contrast %.2f:1" % (light, ratio_for(light)), light),
        ("dark", Image.new("RGB", (panel_w, panel_h), bl.hex_to_rgb(dark)),
         "on dark %s   contrast %.2f:1" % (dark, ratio_for(dark)), dark),
        ("gradient", linear_gradient((panel_w, panel_h), stops, angle),
         "on the hero gradient %s -> %s   contrast %.2f:1 at its worst stop"
         % (stops[0], stops[-1], min(ratio_for(stops[0]), ratio_for(stops[-1]))),
         stops[0]),
    ]

    try:
        cap_path, _ = font_file_for_weight(brand, 400)
        cap_font = ImageFont.truetype(cap_path, 22)
    except Exception:
        cap_font = ImageFont.load_default()

    sheet = Image.new("RGB", (panel_w, panel_h * len(panels)), (255, 255, 255))
    for i, (_name, bg, caption, ref_hex) in enumerate(panels):
        bg = bg.convert("RGBA")
        bg.alpha_composite(art_s, (pad, pad))
        flat = bg.convert("RGB")
        d = ImageDraw.Draw(flat)
        ink = "#FFFFFF" if not bl.is_light(ref_hex) else "#0F0A6C"
        d.text((pad, art_h + pad + 12), caption, font=cap_font, fill=ink)
        sheet.paste(flat, (0, i * panel_h))
    sheet.save(out_path)
    return out_path


# ---------------------------------------------------------------------------
# HUMAN OUTPUT
# ---------------------------------------------------------------------------

def print_analysis(a, dist, out=sys.stdout):
    ref = a["reference"]
    st = a["stats"]
    out.write("REFERENCE  %s  (%dx%d)\n" % (os.path.basename(ref["path"]),
                                            ref["size"][0], ref["size"][1]))
    out.write("  subject  %s, %.0f%% of frame -- %s\n"
              % (a["subject"]["method"], a["subject"]["coverage"] * 100.0,
                 a["subject"]["reason"]))
    out.write("\nPALETTE (subject pixels only)\n")
    out.write("  %-9s %6s  %-22s %8s   %-18s %8s\n"
              % ("hex", "share", "nearest brand token", "deltaE", "nearest identity", "deltaE"))
    for row in dist["colors"]:
        out.write("  %-9s %5.1f%%  %-22s %8.1f   %-18s %8.1f%s\n"
                  % (row["hex"], row["share"] * 100.0,
                     row["nearestToken"] or "-", row["deltaE"],
                     row["nearestCoreToken"] or "-", row["coreDeltaE"],
                     "  (semantic only)" if row["semanticOnly"] else ""))
    out.write("\nTEXTURE\n")
    out.write("  grain          %6.2f  (sd of subject luma; normalised %.2f)\n"
              % (st["grain"], st["grainNorm"]))
    out.write("  directionality %+6.2f  %s  (edge energy x %.2f / y %.2f)\n"
              % (st["directionality"], st["direction"], st["edgeEnergyX"], st["edgeEnergyY"]))
    out.write("  gloss          %6.2f  (p95 %.0f - p50 %.0f of subject luma)\n"
              % (st["gloss"], st["glossP95"], st["glossP50"]))
    out.write("  specular       %6.3f  (that spread minus the %.0f the grain alone "
              "explains)\n" % (st["specular"], 1.645 * st["grain"]))
    out.write("  raggedness     %6.2f  (outline jitter %s, edge %.2f)\n"
              % (st["raggedness"],
                 ("n/a: %s" % st["outlineNote"]) if st["outlineJitter"] is None
                 else "%.2f (%.1f/%.1f px x/y)" % (st["outlineJitter"],
                                                   st["outlineJitterPx"][0],
                                                   st["outlineJitterPx"][1]),
                 st["edgeNorm"]))
    out.write("\nSTYLE (auto)  %s\n  because %s\n" % (a["styleAuto"], a["styleWhy"]))


def print_brand_check(dist, contrast, accepted, out=sys.stdout):
    out.write("\nBRAND CHECK\n")
    for row in contrast["results"]:
        flag = "ok  " if row["passes"] else "FAIL"
        out.write("  contrast  %s  %s vs %s (%s)  %.2f:1  (needs %.1f:1 at display size)\n"
                  % (flag, contrast["meanColor"], row["backgroundHex"],
                     row["background"], row["ratio"], contrast["threshold"]))
    if not contrast["passes"]:
        worst = min(r["ratio"] for r in contrast["results"])
        out.write("  !! CONTRAST FAILS AA FOR LARGE TEXT: %.2f:1 against %.1f:1.\n"
                  "     At display size this is still hard to read. Change the background,\n"
                  "     darken the reference, or put the treatment on a plate.\n"
                  % (worst, contrast["threshold"]))

    out.write("  palette   %s  weighted deltaE %.1f to the nearest approved token, "
              "%.1f to the brand's identity colours\n"
              % (dist["verdict"].upper(), dist["weightedDeltaE"],
                 dist["weightedCoreDeltaE"]))
    if dist["verdict"] == "off-palette":
        out.write("  !! OFF-PALETTE: this reference is nowhere near the brand's own colours\n"
                  "     (deltaE %.1f; anything over %.0f is a different hue family).\n"
                  "     Legitimate for a client brand or a campaign moment. Not legitimate\n"
                  "     for house material that has to sit beside the rest of the deck.\n"
                  % (dist["weightedCoreDeltaE"], DE_ADJACENT))
    if dist["semanticMatchOnly"]:
        out.write("  !! The closest approved tokens are SEMANTIC (status) colours, not\n"
                  "     identity colours. Semantic tokens signal success/warning/danger in\n"
                  "     UI -- borrowing one for display type reads as an error state.\n")
    if accepted:
        out.write("  -- off-palette accepted by the operator (--accept-off-palette); "
                  "recorded in the sidecar.\n")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(
        prog="texture_type.py",
        description=("Render a display type treatment from a reference photo. The "
                     "letterforms stay the brand face; only the fill and the edges "
                     "come from the photo."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="A photo cannot become a .ttf. This produces artwork, not a font file.")
    ap.add_argument("--ref", required=True, help="reference photo (jpg/png/heic-as-png)")
    ap.add_argument("--text", help="the string to set (required unless --analyze-only)")
    ap.add_argument("--brand", default=os.environ.get("BRAND_STUDIO_BRAND", "channelplay"),
                    help="brand id or name (default channelplay)")
    ap.add_argument("--weight", default="600",
                    help="brand weight for the skeleton (default 600). Must be an "
                         "approved weight -- unapproved weights are refused, not "
                         "silently substituted.")
    ap.add_argument("--out", help="output PNG (default <ref-stem>-<text>-texture.png)")
    ap.add_argument("--width", type=int, default=1920, help="canvas width in px (default 1920)")
    ap.add_argument("--height", default="auto", help="canvas height in px, or auto (default)")
    ap.add_argument("--size", default="auto", help="glyph size in px, or auto (default)")
    ap.add_argument("--style", default="auto", choices=STYLES,
                    help="edge/fill treatment (default auto, chosen from the measurements)")
    ap.add_argument("--preview", action="store_true",
                    help="also write a contact sheet on light / dark / hero gradient")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="print the full record as JSON instead of a human report")
    ap.add_argument("--letter-spacing", type=float, default=None, metavar="EM",
                    help="tracking in em (default: the brand's cover tracking)")
    ap.add_argument("--align", default="center", choices=("left", "center", "right"))
    ap.add_argument("--bg", default="transparent",
                    help="light | dark | transparent | #RRGGBB (default transparent). "
                         "Anything other than transparent is baked into the PNG and "
                         "becomes the background the contrast check uses.")
    ap.add_argument("--analyze-only", action="store_true",
                    help="report the analysis and stop; render nothing")
    ap.add_argument("--accept-off-palette", action="store_true",
                    help="record in the sidecar that the operator accepted an "
                         "off-palette reference. Nothing is ever blocked either way.")
    return ap


def run(a):
    brand = resolve_brand_profile(a.brand)
    analysis = analyse(a.ref)
    dist = palette_distance(brand, analysis["palette"])

    public = dict((k, v) for k, v in analysis.items() if not k.startswith("_"))
    public["brandDistance"] = dist

    if a.analyze_only:
        if a.as_json:
            sys.stdout.write(json.dumps(
                {"tool": "texture_type.py", "version": VERSION, "mode": "analyze-only",
                 "brand": brand.get("id"), "analysis": public}, indent=2) + "\n")
        else:
            print_analysis(analysis, dist)
            sys.stdout.write("\n(--analyze-only: nothing rendered. Re-run without it, "
                             "or with --style %s, to produce the treatment.)\n"
                             % analysis["styleAuto"])
        return 0

    if not a.text or not a.text.strip():
        raise Fail("--text is required unless you pass --analyze-only")

    style = analysis["styleAuto"] if a.style == "auto" else a.style
    style_why = (analysis["styleWhy"] if a.style == "auto"
                 else "chosen explicitly with --style %s" % style)
    params = STYLE_PARAMS[style]

    font_path, family = font_file_for_weight(brand, int(float(a.weight)))
    tracking = a.letter_spacing
    if tracking is None:
        try:
            tracking = float(bl.type_role(brand, "cover").get("tracking", 0.0) or 0.0)
        except KeyError:
            tracking = 0.0

    width = int(a.width)
    if width < 64:
        raise Fail("--width must be at least 64px")
    # Ragged edges wander outside the ink box, so the fit target leaves them room.
    bleed = max(8.0, width * 0.012)
    target_w = width - 2.0 * bleed
    min_glyph = width * 0.055

    if str(a.size).lower() == "auto":
        lines, size = layout_text(font_path, a.text, tracking, target_w, min_glyph)
    else:
        size = int(float(a.size))
        lines = [a.text.strip()]
        if fit_size(font_path, lines, tracking, target_w) < size:
            words = [w for w in a.text.split() if w]
            for n in (2, 3):
                if len(words) >= n and fit_size(font_path, wrap_balanced(words, n),
                                                tracking, target_w) >= size:
                    lines = wrap_balanced(words, n)
                    break

    seed_src = "%s|%s|%s|%d|%d" % (analysis["reference"]["sha256"][:16],
                                   a.text, style, size, width)
    seed = int(hashlib.md5(seed_src.encode("utf-8")).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)

    mask_img, _ = render_mask(font_path, lines, size, tracking, a.align,
                              margin=max(6.0, size * 0.06))
    alpha = perturb_alpha(mask_img, params, analysis["stats"], size, rng)
    tile, crop_box = seamless_tile(analysis["_full"], analysis["_mask"], analysis["_scale"])
    fill = composite(tile, mask_img, alpha, analysis["stats"], params, rng, size)

    art_w, art_h = mask_img.size
    auto_height = str(a.height).lower() == "auto"
    canvas_w = width

    rgba = np.zeros((art_h, art_w, 4), dtype=np.uint8)
    rgba[:, :, :3] = fill.astype(np.uint8)
    rgba[:, :, 3] = np.clip(alpha * 255.0, 0, 255).astype(np.uint8)
    art = Image.fromarray(rgba)

    # Fit the artwork into the requested canvas without ever cropping a glyph.
    # The mask already carries its own margin, so only a canvas that is actually
    # too small forces a resize -- an auto-height canvas never does, because it
    # is defined by the artwork.
    scale = min(1.0, canvas_w / float(art_w))
    if not auto_height:
        fixed_h = int(float(a.height))
        if fixed_h > 0:
            scale = min(scale, fixed_h / float(art_h))
    if scale < 0.999:
        art = art.resize((max(1, int(art_w * scale)), max(1, int(art_h * scale))),
                         Image.LANCZOS)
    canvas_h = art.size[1] if auto_height else int(float(a.height))
    bg_hex = background_hex(brand, a.bg)
    canvas = Image.new("RGBA", (canvas_w, max(canvas_h, art.size[1])),
                       (0, 0, 0, 0) if bg_hex is None
                       else tuple(list(bl.hex_to_rgb(bg_hex)) + [255]))
    slack = max(0, canvas.size[0] - art.size[0])
    if a.align == "left":
        ox = min(int(bleed), slack)
    elif a.align == "right":
        ox = slack - min(int(bleed), slack)
    else:
        ox = slack // 2
    oy = (canvas.size[1] - art.size[1]) // 2
    canvas.alpha_composite(art, (max(0, ox), max(0, oy)))

    out_path = a.out
    if not out_path:
        stem = os.path.splitext(os.path.basename(a.ref))[0]
        slug = "".join(c.lower() if c.isalnum() else "-" for c in a.text)[:32].strip("-")
        out_path = os.path.abspath("%s-%s-texture.png" % (stem, slug))
    out_path = os.path.abspath(out_path)
    out_dir = os.path.dirname(out_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    canvas.save(out_path)

    mean_hex = mean_color(fill, alpha)
    contrast = contrast_check(brand, mean_hex, a.bg, bg_hex)

    preview_path = None
    if a.preview:
        preview_path = os.path.splitext(out_path)[0] + "-preview.png"
        preview_sheet(brand, art, preview_path, contrast)

    argv = ["--ref", analysis["reference"]["path"], "--text", a.text,
            "--brand", str(brand.get("id")), "--weight", str(a.weight),
            "--style", style, "--width", str(width), "--align", a.align,
            "--bg", a.bg, "--letter-spacing", "%.4f" % tracking,
            "--size", str(size), "--out", out_path]
    if str(a.height).lower() != "auto":
        argv += ["--height", str(int(float(a.height)))]
    if a.accept_off_palette:
        argv += ["--accept-off-palette"]
    record = {
        "tool": "texture_type.py",
        "version": VERSION,
        "generated": now_iso(),
        "brand": brand.get("id"),
        "analysis": public,
        "style": {"chosen": style, "requested": a.style, "why": style_why,
                  "params": params},
        "settings": {
            "text": a.text,
            "lines": lines,
            "weight": int(float(a.weight)),
            "fontFamily": family,
            "fontFile": font_path,
            "sizePx": size,
            "letterSpacingEm": round(tracking, 4),
            "align": a.align,
            "width": canvas.size[0],
            "height": canvas.size[1],
            "heightMode": "auto" if str(a.height).lower() == "auto" else "fixed",
            "background": a.bg,
            "backgroundHex": bg_hex,
            "seed": seed,
            "textureCropBox": crop_box,
        },
        "brandCheck": {
            "contrast": contrast,
            "paletteDistance": dist,
            "offPaletteAccepted": bool(a.accept_off_palette),
            "skeleton": ("brand face preserved: %s from type.weightToPptxFamily; only "
                         "fill and edges derive from the reference" % family),
        },
        "outputs": {"png": out_path, "preview": preview_path},
        "reproduce": "texture_type.py " + " ".join(shlex_quote(x) for x in argv),
    }
    sidecar = os.path.splitext(out_path)[0] + ".json"
    with open(sidecar, "w") as fh:
        json.dump(record, fh, indent=2)
        fh.write("\n")
    record["outputs"]["sidecar"] = sidecar

    if a.as_json:
        sys.stdout.write(json.dumps(record, indent=2) + "\n")
        return 0

    print_analysis(analysis, dist)
    sys.stdout.write("\nSTYLE (used)  %s -- %s\n" % (style, style_why))
    sys.stdout.write("TYPE  %s %dpx, tracking %+.3f em, %d line%s: %s\n"
                     % (family, size, tracking, len(lines),
                        "" if len(lines) == 1 else "s", " / ".join(lines)))
    print_brand_check(dist, contrast, a.accept_off_palette)
    sys.stdout.write("\nWROTE\n  %s  (%dx%d RGBA)\n" % (out_path, canvas.size[0], canvas.size[1]))
    if preview_path:
        sys.stdout.write("  %s  (contact sheet: light / dark / hero gradient)\n" % preview_path)
    sys.stdout.write("  %s  (sidecar -- rerun `reproduce` to rebuild this exactly)\n" % sidecar)
    return 0


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        return run(a)
    except Fail as exc:
        sys.stderr.write("texture_type: %s\n" % exc)
        return 1
    except ValueError as exc:
        sys.stderr.write("texture_type: %s\n" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
