#!/usr/bin/env python3
"""Create a new brand profile under brands/<id>/ from intake answers.

The brand-kit skill interviews the client, writes the answers to JSON, and
hands that file here. Everything the answers cover becomes an enforced rule.
Everything they do not cover falls back to a deliberately NEUTRAL default that
is stamped ``"$unverified": true`` -- so the validator warns "this profile is
incomplete" instead of confidently enforcing a rule nobody ever agreed to.
Inventing brand rules is worse than having none.

Colour is checked, not trusted:
  * every colour in the answers must parse as a hex value, or nothing is written
  * every foreground/background pair is measured against WCAG AA and any
    failure is reported as a warning
  * if the profile's own body text fails contrast on its own background the
    profile is REFUSED outright -- that one is not a warning, it is a bug that
    would poison every deck built from the brand

Files created:
    brands/<id>/brand.json
    brands/<id>/LEARNED.md          seeded with a header and the entry format
    brands/<id>/rules.local.json    {"rules": []}
    brands/<id>/assets/logos/       drop the logo PNG/SVG files here
    brands/<id>/assets/fonts/       drop the brand TTF/OTF files here
    brands/<id>/video/              music beds, intro/outro plates, reference cuts
    brands/_registry.json           upserted, never rewritten from scratch

Answers file (every key optional except that a name must come from somewhere):
    {
      "name": "Acme Retail", "aliases": ["acme"], "kind": "client",
      "description": "...",
      "colors":      {"primary":"#123456","secondary":"#...","accent":"#...",
                      "surface":"#FFFFFF","tint":"#...", "<any token>":"#..."},
      "neutral":     {"0":"#FFFFFF", ... "900":"#..."},
      "semantic":    {"success":"#...","warning":"#...","danger":"#...","info":"#..."},
      "gradients":   {"hero": {"stops":["#...","#..."], "angle":135}},
      "superseded":  {"#oldhex":"#canonicalhex"},
      "textColors":  {"default":"#...","secondary":"#..."},
      "forbiddenText": {"#000000":"why it must never ship"},
      "onSurface":   {"#background":"#textcolour"},
      "chartSeries": ["#...","#..."],
      "type":        {"family":"Poppins","weights":[400,600],
                      "weightToPptxFamily":{"400":"Poppins","600":"Poppins SemiBold"},
                      "minBodyPt":10.5,"italicsAllowed":false,
                      "syntheticBoldAllowed":false,"deckScalePt":{...}},
      "logo":        {"placement":"top-left",
                      "variants":{"primary":{"file":"assets/logos/x.png","use":"light"}}},
      "voice":       {"case":"sentence","forbiddenChars":["!"],
                      "forbiddenPhrases":["Lorem ipsum"],"guidance":"..."},
      "video":       {"music":{"enabled":true},"captions":{"required":true}},

      "color": {...}, "colorRules": {...}   <- raw brand.json blocks, merged last
    }

Exit codes:
    0   the brand was written
    1   bad usage, or an internal failure
    2   the answers were refused (bad hex, unusable weights, failing body text)

Usage:
    new_brand.py --id acme-retail --name "Acme Retail"
    new_brand.py --id acme-retail --from-json intake.json
    new_brand.py --id acme-retail --name "Acme Retail" --from-json intake.json --copy-grammar
    new_brand.py --id acme-retail --name "Acme Retail" --dry-run --json
"""

import argparse
import datetime
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

import brandlib  # noqa: E402

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_REFUSED = 2

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,46}[a-z0-9]$")

# CSS numeric weight -> the suffix type foundries put in the family name.
WEIGHT_SUFFIX = {
    100: "Thin", 200: "ExtraLight", 300: "Light", 400: "",
    500: "Medium", 600: "SemiBold", 700: "Bold", 800: "ExtraBold", 900: "Black",
}

# Keys under color.* whose string values are prose, not colours.
NON_COLOR_KEYS = ("note", "$comment", "$note", "use", "rationale", "description")

NEUTRAL_RAMP = {
    "0": "#FFFFFF", "50": "#F9FAFB", "100": "#F3F4F6", "200": "#E5E7EB",
    "300": "#D1D5DB", "400": "#9CA3AF", "500": "#6B7280", "600": "#4B5563",
    "700": "#374151", "800": "#1F2937", "900": "#111827",
}

NEUTRAL_BRAND = {
    "primary": "#1F2937",
    "secondary": "#4B5563",
    "accent": "#6B7280",
    "surface": "#FFFFFF",
    "tint": "#F3F4F6",
}

NEUTRAL_SEMANTIC = {
    "success": "#166534", "success.bg": "#F0FDF4",
    "warning": "#92400E", "warning.bg": "#FFFBEB",
    "danger": "#991B1B", "danger.bg": "#FEF2F2",
    "info": "#1E40AF", "info.bg": "#EFF6FF",
}

NEUTRAL_CHART = ["#1F2937", "#4B5563", "#6B7280", "#374151", "#7F8896", "#111827"]

# Sizes and leading are geometry: they are tuned to the shared grammar's boxes,
# not to any brand, so reusing them is accurate rather than invented. Only the
# weights are brand-owned, and they get snapped into whatever is approved.
BASE_SCALE = {
    "cover":      {"size": 54, "leading": 60, "weight": 600, "tracking": -0.02},
    "section":    {"size": 44, "leading": 50, "weight": 600, "tracking": -0.02},
    "title":      {"size": 32, "leading": 38, "weight": 600, "tracking": -0.01},
    "subtitle":   {"size": 18, "leading": 26, "weight": 400, "tracking": 0},
    "cardTitle":  {"size": 14, "leading": 20, "weight": 600, "tracking": 0},
    "label":      {"size": 12, "leading": 17, "weight": 500, "tracking": 0},
    "body":       {"size": 12, "leading": 18, "weight": 400, "tracking": 0},
    "bodySmall":  {"size": 11, "leading": 16, "weight": 400, "tracking": 0},
    "caption":    {"size": 10.5, "leading": 15, "weight": 400, "tracking": 0.01},
    "eyebrow":    {"size": 10.5, "leading": 15, "weight": 500, "tracking": 0.08, "case": "upper"},
    "footer":     {"size": 10.5, "leading": 15, "weight": 400, "tracking": 0},
    "statNumber": {"size": 32, "leading": 38, "weight": 600, "tracking": -0.01},
}

# Placeholder strings that must never survive into a shipped artifact. These are
# not brand voice -- they are scaffolding markers, true for every brand.
UNIVERSAL_FORBIDDEN_PHRASES = [
    "Lorem ipsum", "Click here", "Insert text here", "Placeholder",
    "Your text here", "Company Name", "TBD", "TODO",
]


class Refused(Exception):
    """The answers cannot produce a usable profile. Nothing is written."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write("%s: error: %s\n" % (self.prog, message))
        raise SystemExit(EXIT_INTERNAL)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _hex(value, where):
    # type: (object, str) -> str
    if not brandlib.is_hex(value):
        raise Refused("%s must be a hex colour like #123ABC, got %r" % (where, value))
    return brandlib.normalize_hex(value)


def _dict(answers, key):
    # type: (dict, str) -> dict
    val = answers.get(key)
    return dict(val) if isinstance(val, dict) and val else {}


def _list(answers, key):
    # type: (dict, str) -> list
    val = answers.get(key)
    return list(val) if isinstance(val, (list, tuple)) and val else []


def _luminance_sort(hexes):
    # type: (list) -> list
    return sorted(hexes, key=brandlib.relative_luminance)


def _snap_weight(want, approved):
    # type: (int, list) -> int
    if not approved:
        return want
    if want in approved:
        return want
    return min(approved, key=lambda w: (abs(w - want), w))


def _family_for_weight(family, weight):
    # type: (str, int) -> str
    suffix = WEIGHT_SUFFIX.get(int(weight))
    if suffix is None:
        return "%s %d" % (family, int(weight))
    return family if not suffix else "%s %s" % (family, suffix)


# ---------------------------------------------------------------------------
# profile sections
# ---------------------------------------------------------------------------

def build_color(answers, unverified):
    # type: (dict, list) -> tuple
    """Return (color_block, colorRules_block)."""
    colors = _dict(answers, "colors")
    neutral_in = _dict(answers, "neutral")
    semantic_in = _dict(answers, "semantic")
    gradients_in = _dict(answers, "gradients")
    superseded_in = _dict(answers, "superseded")

    if colors:
        brand_colors = {}
        for token, value in colors.items():
            if str(token).startswith("$"):
                continue
            brand_colors[str(token)] = _hex(value, "colors.%s" % token)
    else:
        brand_colors = dict(NEUTRAL_BRAND)
        unverified.append("color.brand")

    if neutral_in:
        neutral = {}
        for step, value in neutral_in.items():
            neutral[str(step)] = _hex(value, "neutral.%s" % step)
    else:
        neutral = dict(NEUTRAL_RAMP)
        unverified.append("color.neutral")

    if semantic_in:
        semantic = {}
        for role, value in semantic_in.items():
            semantic[str(role)] = _hex(value, "semantic.%s" % role)
    else:
        semantic = dict(NEUTRAL_SEMANTIC)
        unverified.append("color.semantic")

    gradient = {}
    for name, spec in gradients_in.items():
        if not isinstance(spec, dict):
            raise Refused("gradients.%s must be an object with 'stops' and 'angle'" % name)
        stops = [_hex(s, "gradients.%s.stops" % name) for s in (spec.get("stops") or [])]
        if len(stops) < 2:
            raise Refused("gradients.%s needs at least two stops" % name)
        angle = spec.get("angle", 135)
        try:
            angle = int(angle)
        except (TypeError, ValueError):
            raise Refused("gradients.%s.angle must be a number, got %r" % (name, angle))
        gradient[str(name)] = {"stops": stops, "angle": angle}

    superseded = {}
    for old, new in superseded_in.items():
        superseded[_hex(old, "superseded key")] = _hex(new, "superseded[%s]" % old)

    color = {
        "brand": brand_colors,
        "neutral": neutral,
        "semantic": semantic,
        "gradient": gradient,
        "superseded": {
            "note": ("Colours observed in legacy material that are NOT approved. "
                     "The validator maps each to its canonical token and reports "
                     "COLOR.SUPERSEDED."),
            "map": superseded,
        },
    }
    if not colors:
        color["$unverified"] = True

    # ---- rules --------------------------------------------------------------
    surface = brand_colors.get("surface") or neutral.get("0") or "#FFFFFF"
    surface = brandlib.normalize_hex(surface)

    text_in = _dict(answers, "textColors")
    if text_in.get("default"):
        default_text = _hex(text_in["default"], "textColors.default")
    else:
        pool = [h for h in list(brand_colors.values()) + list(neutral.values())]
        passing = [h for h in _luminance_sort(pool)
                   if brandlib.contrast_ratio(h, surface) >= 4.5]
        default_text = passing[0] if passing else neutral.get("900", "#111827")
        unverified.append("colorRules.defaultText")

    if text_in.get("secondary"):
        secondary_text = _hex(text_in["secondary"], "textColors.secondary")
    else:
        pool = [h for h in list(neutral.values()) + list(brand_colors.values())
                if brandlib.normalize_hex(h) != default_text]
        passing = [h for h in _luminance_sort(pool)
                   if brandlib.contrast_ratio(h, surface) >= 4.5]
        secondary_text = passing[-1] if passing else default_text
        unverified.append("colorRules.secondaryText")

    forbidden_text = {}
    for value, reason in _dict(answers, "forbiddenText").items():
        forbidden_text[_hex(value, "forbiddenText key")] = str(reason)

    on_surface_in = _dict(answers, "onSurface")
    on_surface = {}
    if on_surface_in:
        for bg, fg in on_surface_in.items():
            on_surface[_hex(bg, "onSurface key")] = _hex(fg, "onSurface[%s]" % bg)
    else:
        # Derived, not invented: for each surface the brand actually owns, pick
        # whichever of the two text colours WCAG says is legible on it.
        surfaces = []
        for h in list(brand_colors.values()) + [neutral.get(k) for k in ("0", "50", "100", "800", "900")]:
            if h and brandlib.normalize_hex(h) not in surfaces:
                surfaces.append(brandlib.normalize_hex(h))
        white = "#FFFFFF"
        for bg in surfaces:
            dark_ok = brandlib.contrast_ratio(default_text, bg)
            light_ok = brandlib.contrast_ratio(white, bg)
            on_surface[bg] = default_text if dark_ok >= light_ok else white

    chart_in = _list(answers, "chartSeries")
    if chart_in:
        chart = [_hex(c, "chartSeries[%d]" % i) for i, c in enumerate(chart_in)]
    elif colors:
        chart = []
        for h in brand_colors.values():
            h = brandlib.normalize_hex(h)
            if h != surface and h not in chart and brandlib.contrast_ratio(h, surface) >= 3.0:
                chart.append(h)
        for filler in NEUTRAL_CHART:
            if len(chart) >= 4:
                break
            if filler not in chart:
                chart.append(filler)
    else:
        chart = list(NEUTRAL_CHART)
        unverified.append("colorRules.chartSeries")

    color_rules = {
        "defaultText": default_text,
        "secondaryText": secondary_text,
        "forbiddenText": forbidden_text,
        "onSurface": on_surface,
        "minContrastBody": 4.5,
        "minContrastLarge": 3.0,
        "largeTextPt": 18.0,
        "gradientTextForbidden": True,
        "maxGradientsPerSurface": 1,
        "chartSeries": chart,
        "chartGradientFillForbidden": True,
    }
    return (color, color_rules)


def build_type(answers, unverified):
    # type: (dict, list) -> dict
    src = _dict(answers, "type")
    if not src:
        unverified.append("type")

    family = str(src.get("family") or "Arial").strip() or "Arial"

    weights_in = src.get("weights") or src.get("approvedWeights")
    approved = []
    if isinstance(weights_in, (list, tuple)) and weights_in:
        for w in weights_in:
            try:
                approved.append(int(w))
            except (TypeError, ValueError):
                raise Refused("type.weights must be numbers, got %r" % (w,))
    else:
        approved = [400, 700]
        if src:
            unverified.append("type.approvedWeights")
    approved = sorted(set(approved))
    if not approved:
        raise Refused("type.weights cannot be empty")

    mapping_in = src.get("weightToPptxFamily")
    mapping = {}
    if isinstance(mapping_in, dict) and mapping_in:
        for w, fam in mapping_in.items():
            try:
                wi = int(w)
            except (TypeError, ValueError):
                raise Refused("type.weightToPptxFamily keys must be numeric weights, got %r" % (w,))
            if wi not in approved:
                raise Refused(
                    "type.weightToPptxFamily maps weight %d, which is not in approved weights %s"
                    % (wi, approved))
            mapping[str(wi)] = str(fam)
        for w in approved:
            if str(w) not in mapping:
                raise Refused(
                    "type.weightToPptxFamily is missing approved weight %d. Every approved "
                    "weight needs a real installed family name, because PowerPoint has no "
                    "weight axis -- it only has family names." % w)
    else:
        for w in approved:
            mapping[str(w)] = _family_for_weight(family, w)

    scale_in = src.get("deckScalePt")
    if isinstance(scale_in, dict) and scale_in:
        scale = {}
        for role, spec in scale_in.items():
            if not isinstance(spec, dict):
                raise Refused("type.deckScalePt.%s must be an object" % role)
            spec = dict(spec)
            if "weight" in spec:
                try:
                    w = int(spec["weight"])
                except (TypeError, ValueError):
                    raise Refused("type.deckScalePt.%s.weight must be a number" % role)
                if w not in approved:
                    raise Refused(
                        "type.deckScalePt.%s uses weight %d, which is not approved %s"
                        % (role, w, approved))
            scale[str(role)] = spec
        missing = [r for r in BASE_SCALE if r not in scale]
        for role in missing:
            base = dict(BASE_SCALE[role])
            base["weight"] = _snap_weight(base["weight"], approved)
            scale[role] = base
        if missing:
            unverified.append("type.deckScalePt")
    else:
        scale = {}
        for role, base in BASE_SCALE.items():
            spec = dict(base)
            spec["weight"] = _snap_weight(spec["weight"], approved)
            scale[role] = spec
        if src:
            unverified.append("type.deckScalePt")

    min_body = src.get("minBodyPt", 10.5)
    try:
        min_body = float(min_body)
    except (TypeError, ValueError):
        raise Refused("type.minBodyPt must be a number, got %r" % (min_body,))

    block = {
        "family": family,
        "fallback": [str(f) for f in (src.get("fallback")
                                      or ["ui-sans-serif", "Segoe UI", "Roboto",
                                          "Helvetica Neue", "Arial", "sans-serif"])],
        "weightToPptxFamily": mapping,
        "approvedWeights": approved,
        "forbiddenWeights": [int(w) for w in (src.get("forbiddenWeights") or [])],
        # Permissive unless the brand owner said otherwise. A restriction nobody
        # asked for is an invented rule, and invented rules are the failure mode
        # this whole script exists to avoid.
        "italicsAllowed": bool(src.get("italicsAllowed", True)),
        "syntheticBoldAllowed": bool(src.get("syntheticBoldAllowed", True)),
        "deckScalePt": scale,
        "minBodyPt": min_body,
        "negativeTrackingAbovePt": src.get("negativeTrackingAbovePt", 22),
    }
    if not src:
        block["$unverified"] = True
    return block


def build_logo(answers, unverified):
    # type: (dict, list) -> dict
    src = _dict(answers, "logo")
    if not src:
        unverified.append("logo")

    variants = {}
    for name, spec in (src.get("variants") or {}).items():
        if isinstance(spec, str):
            spec = {"file": spec}
        if not isinstance(spec, dict):
            raise Refused("logo.variants.%s must be an object or a path string" % name)
        entry = {"file": str(spec.get("file") or ""), "use": str(spec.get("use") or "")}
        if spec.get("aspect") is not None:
            try:
                entry["aspect"] = float(spec["aspect"])
            except (TypeError, ValueError):
                raise Refused("logo.variants.%s.aspect must be a number" % name)
        variants[str(name)] = entry

    block = {
        "placement": str(src.get("placement") or "top-left"),
        "variants": variants,
        "variantForBackground": src.get("variantForBackground")
            or {"light": "primary", "dark": "reversed", "photo": "monoWhite",
                "gradient": "reversed"},
        # Sizes come from the shared grammar chrome so the logo lands on the grid.
        "deckSizeIn": src.get("deckSizeIn") or {"w": 1.397, "h": 0.288},
        "coverSizeIn": src.get("coverSizeIn") or {"w": 1.91, "h": 0.394},
        "minWidthIn": src.get("minWidthIn", 1.25),
        "clearSpaceRatio": src.get("clearSpaceRatio", 1.0),
        "clearSpaceBasis": str(src.get("clearSpaceBasis")
                               or "one logo height of clear space on every side"),
        "forbidden": [str(f) for f in (src.get("forbidden")
                                       or ["redraw", "recolour", "stretch", "rotate",
                                           "effects", "retype wordmark"])],
        "vectorAvailable": bool(src.get("vectorAvailable", False)),
    }
    if src.get("placementRationale"):
        block["placementRationale"] = str(src["placementRationale"])
    if not src:
        block["$unverified"] = True
    return block


def build_voice(answers, unverified):
    # type: (dict, list) -> dict
    src = _dict(answers, "voice")
    if not src:
        unverified.append("voice")

    phrases = [str(p) for p in (src.get("forbiddenPhrases") or [])]
    for p in UNIVERSAL_FORBIDDEN_PHRASES:
        if p.lower() not in [x.lower() for x in phrases]:
            phrases.append(p)

    block = {
        "case": str(src.get("case") or "sentence"),
        "caseExceptions": [str(c) for c in (src.get("caseExceptions") or ["eyebrow"])],
        "tense": str(src.get("tense") or "present"),
        "voice": str(src.get("voice") or "active"),
        "forbiddenChars": [str(c) for c in (src.get("forbiddenChars") or [])],
        "forbiddenPhrases": phrases,
        "guidance": str(src.get("guidance")
                        or "Not captured during intake. Replace this with the brand's own "
                           "voice guidance before shipping client-facing work."),
    }
    if not src:
        block["$unverified"] = True
    return block


def build_video(answers, unverified):
    # type: (dict, list) -> dict
    src = _dict(answers, "video")
    if not src:
        unverified.append("video")

    base = {
        "resolution": {"w": 1920, "h": 1080},
        "fps": 30,
        "container": "mp4",
        "vcodec": "libx264",
        "acodec": "aac",
        "safeMarginPct": 5,
        "intro": {"type": "generated", "durationSec": 3.0, "style": "logo-reveal", "file": None},
        "outro": {"type": "generated", "durationSec": 3.5, "style": "lockup-cta", "file": None},
        "music": {
            "enabled": False,
            "file": None,
            "targetLufs": -23.0,
            "duckUnderVoiceDb": -18.0,
            "fadeInSec": 1.0,
            "fadeOutSec": 2.0,
            "mood": [],
            "avoid": [],
        },
        "voiceover": {
            "enabled": True,
            "engine": "say",
            "voice": "Samantha",
            "rateWpm": 165,
            "targetLufs": -16.0,
        },
        # Captions are an accessibility floor, not a brand preference.
        "captions": {
            "enabled": True,
            "required": True,
            "burnIn": False,
            "sidecar": "srt",
            "font": None,
            "sizePt": 28,
            "color": "#FFFFFF",
            "background": None,
            "backgroundOpacity": 0.82,
            "position": "bottom-center",
            "bottomMarginPct": 8,
            "maxCharsPerLine": 42,
            "maxLines": 2,
            "minDurationSec": 1.2,
        },
        "transition": {"type": "crossfade", "durationSec": 0.4},
        "slideHoldSec": {"min": 3.0, "default": 5.0, "max": 12.0},
        "storyline": {
            "required": True,
            "arc": ["hook", "problem", "approach", "proof", "outcome", "call-to-action"],
            "rules": [
                "Open on the audience's problem, never on the agency's credentials.",
                "One idea per scene.",
                "Proof is specific and numeric wherever possible.",
                "Close with a single, concrete next step.",
            ],
        },
        "referenceVideos": [],
    }
    block = brandlib.deep_merge(base, src) if src else base
    if not src:
        block["$unverified"] = True
    return block


def expand_raw_blocks(answers):
    # type: (dict) -> dict
    """Let the answers arrive either as intake keys or as raw brand.json blocks.

    Someone handing us a finished ``color`` / ``colorRules`` block has plainly
    verified those colours, so back-filling the intake keys from them keeps the
    ``$unverified`` bookkeeping honest instead of flagging confirmed values.
    """
    out = dict(answers)
    raw_color = out.get("color")
    if isinstance(raw_color, dict):
        for src_key, dst_key in (("brand", "colors"), ("neutral", "neutral"),
                                 ("semantic", "semantic"), ("gradient", "gradients")):
            block = raw_color.get(src_key)
            if isinstance(block, dict) and block and not out.get(dst_key):
                out[dst_key] = {k: v for k, v in block.items() if not str(k).startswith("$")}
        sup = raw_color.get("superseded")
        if isinstance(sup, dict) and isinstance(sup.get("map"), dict) and not out.get("superseded"):
            out["superseded"] = dict(sup["map"])

    raw_rules = out.get("colorRules")
    if isinstance(raw_rules, dict):
        text = {}
        if raw_rules.get("defaultText"):
            text["default"] = raw_rules["defaultText"]
        if raw_rules.get("secondaryText"):
            text["secondary"] = raw_rules["secondaryText"]
        if text and not out.get("textColors"):
            out["textColors"] = text
        for src_key, dst_key in (("forbiddenText", "forbiddenText"),
                                 ("onSurface", "onSurface"),
                                 ("chartSeries", "chartSeries")):
            block = raw_rules.get(src_key)
            if block and not out.get(dst_key):
                out[dst_key] = block
    return out


def build_profile(brand_id, name, answers, today):
    # type: (str, str, dict, str) -> tuple
    """Return (profile, unverified_paths)."""
    answers = expand_raw_blocks(answers)
    unverified = []

    color, color_rules = build_color(answers, unverified)
    type_block = build_type(answers, unverified)
    logo = build_logo(answers, unverified)
    voice = build_voice(answers, unverified)
    video = build_video(answers, unverified)

    # Caption typography follows the brand once it is known.
    if video.get("captions", {}).get("font") in (None, ""):
        heaviest = max(type_block["approvedWeights"])
        video["captions"]["font"] = type_block["weightToPptxFamily"][str(heaviest)]
    if video.get("captions", {}).get("background") in (None, ""):
        video["captions"]["background"] = color_rules["defaultText"]

    aliases = [str(a) for a in _list(answers, "aliases")]

    profile = {
        "$schema": "../_schema.json",
        "id": brand_id,
        "name": name,
        "aliases": aliases,
        "kind": str(answers.get("kind") or "client"),
        "description": str(answers.get("description") or ""),
        "version": str(answers.get("version") or "0.1.0"),
        "updated": str(answers.get("updated") or today),
        "provenance": {
            "tokens": str((answers.get("provenance") or {}).get("tokens")
                          if isinstance(answers.get("provenance"), dict) else "")
                      or "Brand intake answers collected by the brand-kit skill.",
            "geometry": "grammar/deck-grammar.json (shared, brand-agnostic).",
            "resolution": ("Created by new_brand.py on %s. Sections marked \"$unverified\" "
                           "were never confirmed by the brand owner and carry neutral "
                           "defaults; the validator reports them rather than enforcing "
                           "them." % today),
        },
        "color": color,
        "colorRules": color_rules,
        "type": type_block,
        "logo": logo,
        "voice": voice,
        "video": video,
        "learned": {"file": "LEARNED.md", "rules": "rules.local.json"},
    }

    # Raw brand.json-shaped blocks win over everything derived above. The
    # per-section builders already consume answers["type"|"logo"|"voice"|"video"]
    # directly, so only these three need a second, literal pass.
    for key in ("color", "colorRules", "provenance"):
        override = answers.get(key)
        if isinstance(override, dict) and override:
            profile[key] = brandlib.deep_merge(profile[key], override)

    if unverified:
        profile["$unverified"] = True
        profile["$unverifiedFields"] = sorted(set(unverified))
    return (profile, sorted(set(unverified)))


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def check_hexes(profile):
    # type: (dict) -> None
    """Every colour-bearing leaf in the profile must parse. Raises Refused."""
    bad = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                key = str(k)
                if key.startswith("$") or key in NON_COLOR_KEYS:
                    continue
                walk(v, "%s.%s" % (path, key))
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                walk(v, "%s[%d]" % (path, i))
        elif isinstance(node, str):
            if not brandlib.is_hex(node):
                bad.append("%s = %r" % (path, node))

    walk(profile.get("color") or {}, "color")

    # Dict keys are paths to the walker, so the superseded map's keys -- which
    # are themselves colours -- need checking explicitly.
    sup_map = ((profile.get("color") or {}).get("superseded") or {}).get("map") or {}
    for old in sup_map.keys():
        if not brandlib.is_hex(old):
            bad.append("color.superseded.map key = %r" % (old,))

    rules = profile.get("colorRules") or {}
    for key in ("defaultText", "secondaryText"):
        if rules.get(key) is not None and not brandlib.is_hex(rules[key]):
            bad.append("colorRules.%s = %r" % (key, rules[key]))
    for bg, fg in (rules.get("onSurface") or {}).items():
        if not brandlib.is_hex(bg):
            bad.append("colorRules.onSurface key = %r" % (bg,))
        if not brandlib.is_hex(fg):
            bad.append("colorRules.onSurface[%s] = %r" % (bg, fg))
    for value in (rules.get("forbiddenText") or {}).keys():
        if not brandlib.is_hex(value):
            bad.append("colorRules.forbiddenText key = %r" % (value,))
    for i, c in enumerate(rules.get("chartSeries") or []):
        if not brandlib.is_hex(c):
            bad.append("colorRules.chartSeries[%d] = %r" % (i, c))

    if bad:
        raise Refused("these values are not hex colours:\n    " + "\n    ".join(bad))


def audit_contrast(profile):
    # type: (dict) -> tuple
    """Return (warnings, fatal_or_None). Every pair is measured, none guessed."""
    rules = profile.get("colorRules") or {}
    color = profile.get("color") or {}
    brand_colors = color.get("brand") or {}
    neutral = color.get("neutral") or {}

    min_body = float(rules.get("minContrastBody", 4.5))
    min_large = float(rules.get("minContrastLarge", 3.0))

    surface = brand_colors.get("surface") or neutral.get("0") or "#FFFFFF"
    surface = brandlib.normalize_hex(surface)
    body = brandlib.normalize_hex(rules.get("defaultText"))

    warnings = []
    fatal = None

    ratio = brandlib.contrast_ratio(body, surface)
    if ratio < min_body:
        fatal = ("body text %s on its own background %s is %.2f:1, below the profile's "
                 "own minimum of %.1f:1. A deck built on this would be unreadable, so "
                 "the profile is refused. Darken the text or lighten the surface."
                 % (body, surface, ratio, min_body))

    secondary = rules.get("secondaryText")
    if brandlib.is_hex(secondary):
        secondary = brandlib.normalize_hex(secondary)
        r = brandlib.contrast_ratio(secondary, surface)
        if r < min_body:
            warnings.append("secondary text %s on %s is %.2f:1 (AA body needs %.1f:1)"
                            % (secondary, surface, r, min_body))

    for bg, fg in (rules.get("onSurface") or {}).items():
        bg_n = brandlib.normalize_hex(bg)
        fg_n = brandlib.normalize_hex(fg)
        r = brandlib.contrast_ratio(fg_n, bg_n)
        if r < min_body:
            warnings.append("onSurface %s on %s is %.2f:1 (AA body needs %.1f:1)"
                            % (fg_n, bg_n, r, min_body))

    for i, c in enumerate(rules.get("chartSeries") or []):
        c_n = brandlib.normalize_hex(c)
        r = brandlib.contrast_ratio(c_n, surface)
        if r < min_large:
            warnings.append("chart series %d %s on %s is %.2f:1 (graphics need %.1f:1)"
                            % (i + 1, c_n, surface, r, min_large))

    for token, value in brand_colors.items():
        if str(token).startswith("$") or not brandlib.is_hex(value):
            continue
        v = brandlib.normalize_hex(value)
        if v == surface:
            continue
        if brandlib.is_light(v):
            continue
        r = brandlib.contrast_ratio("#FFFFFF", v)
        if r < min_body:
            warnings.append("white text on %s (%s) is %.2f:1 (AA body needs %.1f:1)"
                            % (token, v, r, min_body))

    for name, spec in (color.get("gradient") or {}).items():
        stops = [s for s in (spec.get("stops") or []) if brandlib.is_hex(s)]
        for stop in stops:
            fg = brandlib.on_surface_text(profile, stop) or "#FFFFFF"
            r = brandlib.contrast_ratio(fg, stop)
            if r < min_body:
                warnings.append("gradient %s stop %s only reaches %.2f:1 with %s"
                                % (name, brandlib.normalize_hex(stop), r, fg))

    return (warnings, fatal)


def check_assets(profile, bdir):
    # type: (dict, str) -> list
    warnings = []
    for name, spec in ((profile.get("logo") or {}).get("variants") or {}).items():
        rel = str(spec.get("file") or "")
        if not rel:
            warnings.append("logo variant %r has no file path" % name)
            continue
        full = rel if os.path.isabs(rel) else os.path.join(bdir, rel)
        if not os.path.isfile(full):
            warnings.append("logo variant %r points at %s, which is not on disk yet" % (name, rel))
    if not ((profile.get("logo") or {}).get("variants")):
        warnings.append("no logo variants declared - decks will render without a logo")
    return warnings


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

def learned_header(name, today):
    # type: (str, str) -> str
    return (
        "# %s - learned rules\n"
        "\n"
        "Created %s by new_brand.py.\n"
        "\n"
        "Append one `##` entry per lesson, newest at the bottom, dated `YYYY-MM-DD`.\n"
        "`brand_resolve.py` surfaces the three most recent entries before anything is\n"
        "built, so keep each entry to one decision and one reason.\n"
        "\n"
        "Format:\n"
        "\n"
        "    ## 2026-01-31 - Short statement of the rule\n"
        "    Why it exists, and what to do instead.\n"
        "\n"
        "Rules a machine can enforce belong in `rules.local.json`; this file is the\n"
        "human record of decisions, exceptions and client feedback.\n"
        "\n"
        "<!-- entries below this line -->\n"
        % (name, today)
    )


def rules_local(name):
    # type: (str) -> dict
    return {
        "$comment": ("Machine-enforceable rules learned for %s after the profile was "
                     "created. Merged into brand.learnedRules by brandlib.load_brand()."
                     % name),
        "rules": [],
    }


def write_brand(root, brand_id, profile, name, today, copy_grammar):
    # type: (str, str, dict, str, str, bool) -> list
    bdir = os.path.join(root, "brands", brand_id)
    created = []

    for sub in ("", "assets", os.path.join("assets", "logos"),
                os.path.join("assets", "fonts"), "video"):
        path = os.path.join(bdir, sub) if sub else bdir
        if not os.path.isdir(path):
            os.makedirs(path)
            created.append(path + os.sep)

    for keep in (os.path.join("assets", "logos"), os.path.join("assets", "fonts"), "video"):
        marker = os.path.join(bdir, keep, ".gitkeep")
        if not os.path.exists(marker):
            with open(marker, "w") as fh:
                fh.write("")

    profile_path = os.path.join(bdir, "brand.json")
    with open(profile_path, "w") as fh:
        json.dump(profile, fh, indent=2, sort_keys=False)
        fh.write("\n")
    created.append(profile_path)

    learned_path = os.path.join(bdir, "LEARNED.md")
    if not os.path.exists(learned_path):
        with open(learned_path, "w") as fh:
            fh.write(learned_header(name, today))
        created.append(learned_path)

    rules_path = os.path.join(bdir, "rules.local.json")
    if not os.path.exists(rules_path):
        with open(rules_path, "w") as fh:
            json.dump(rules_local(name), fh, indent=2)
            fh.write("\n")
        created.append(rules_path)

    if copy_grammar:
        src = os.path.join(root, "grammar", "deck-grammar.json")
        dst = os.path.join(bdir, "deck-grammar.json")
        if not os.path.isfile(src):
            raise Refused("cannot copy grammar: %s does not exist" % src)
        shutil.copyfile(src, dst)
        created.append(dst)

    return created


def update_registry(root, entry, today):
    # type: (str, dict, str) -> str
    path = os.path.join(root, "brands", "_registry.json")
    shape = "object"
    rows = []

    if os.path.isfile(path):
        try:
            with open(path, "r") as fh:
                data = json.load(fh)
        except ValueError:
            data = None
        if isinstance(data, list):
            shape = "list"
            rows = [r for r in data if isinstance(r, dict)]
        elif isinstance(data, dict) and isinstance(data.get("brands"), list):
            rows = [r for r in data["brands"] if isinstance(r, dict)]
    else:
        # First registry ever written. Seed it from what is already on disk, or
        # the directory-scan fallback would stop working the moment this file
        # appears and every existing brand would vanish.
        rows = list(brandlib.list_brands())

    replaced = False
    for i, row in enumerate(rows):
        if str(row.get("id")) == entry["id"]:
            rows[i] = entry
            replaced = True
            break
    if not replaced:
        rows.append(entry)
    rows.sort(key=lambda r: str(r.get("id") or ""))

    with open(path, "w") as fh:
        if shape == "list":
            json.dump(rows, fh, indent=2)
        else:
            json.dump({
                "$comment": ("Brand registry. brandlib.list_brands() reads this first and "
                             "falls back to scanning brands/*/brand.json when it is absent."),
                "updated": today,
                "brands": rows,
            }, fh, indent=2)
        fh.write("\n")
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_answers(path):
    # type: (str) -> dict
    if not os.path.isfile(path):
        raise Refused("answers file not found: %s" % path)
    try:
        with open(path, "r") as fh:
            data = json.load(fh)
    except ValueError as exc:
        raise Refused("%s is not valid JSON: %s" % (path, exc))
    if isinstance(data, dict):
        for wrapper in ("answers", "brand", "intake"):
            inner = data.get(wrapper)
            if isinstance(inner, dict) and len(data) == 1:
                return inner
        return data
    raise Refused("%s must contain a JSON object" % path)


def main(argv=None):
    # type: (list) -> int
    ap = _Parser(
        prog="new_brand.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--id", required=True, dest="brand_id",
                    help="brand slug: lowercase letters, digits and hyphens")
    ap.add_argument("--name", default=None, help="display name, e.g. \"Acme Retail\"")
    ap.add_argument("--from-json", default=None, dest="from_json",
                    help="intake answers JSON collected by the brand-kit skill")
    ap.add_argument("--copy-grammar", action="store_true", dest="copy_grammar",
                    help="copy grammar/deck-grammar.json into the brand so it can diverge")
    ap.add_argument("--force", action="store_true",
                    help="overwrite brand.json when the brand directory already exists")
    ap.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="validate and print the profile without writing anything")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="machine-readable result on stdout")
    args = ap.parse_args(argv)

    today = datetime.date.today().isoformat()

    try:
        brand_id = str(args.brand_id).strip().lower()
        if not SLUG_RE.match(brand_id):
            raise Refused(
                "--id %r is not a usable slug. Use lowercase letters, digits and hyphens, "
                "2-48 characters, starting and ending alphanumeric." % args.brand_id)

        answers = load_answers(args.from_json) if args.from_json else {}

        name = args.name or answers.get("name")
        if not name or not str(name).strip():
            raise Refused("a display name is required: pass --name, or put \"name\" in "
                          "the answers file")
        name = str(name).strip()

        root = brandlib.plugin_root()
        bdir = os.path.join(root, "brands", brand_id)
        if os.path.isfile(os.path.join(bdir, "brand.json")) and not args.force and not args.dry_run:
            raise Refused("brand %r already exists at %s. Use --force to overwrite its "
                          "brand.json (LEARNED.md and rules.local.json are never "
                          "overwritten)." % (brand_id, bdir))

        profile, unverified = build_profile(brand_id, name, answers, today)
        check_hexes(profile)
        warnings, fatal = audit_contrast(profile)
        warnings += check_assets(profile, bdir)

        if fatal:
            raise Refused(fatal)

        written = []
        registry = ""
        if not args.dry_run:
            written = write_brand(root, brand_id, profile, name, today, args.copy_grammar)
            registry = update_registry(root, {
                "id": brand_id,
                "name": name,
                "aliases": profile.get("aliases") or [],
                "updated": profile.get("updated") or today,
            }, today)

        if args.as_json:
            json.dump({
                "ok": True,
                "dryRun": bool(args.dry_run),
                "id": brand_id,
                "name": name,
                "dir": bdir,
                "written": written,
                "registry": registry,
                "unverified": unverified,
                "warnings": warnings,
                "profile": profile,
            }, sys.stdout, indent=2)
            sys.stdout.write("\n")
            return EXIT_OK

        head = "Brand %s (%s)%s" % (name, brand_id, "  [DRY RUN - nothing written]"
                                    if args.dry_run else "")
        print(head)
        print("=" * max(24, len(head)))
        rules = profile["colorRules"]
        surface = (profile["color"]["brand"].get("surface")
                   or profile["color"]["neutral"].get("0") or "#FFFFFF")
        print("  body text    %s on %s   %.2f:1"
              % (rules["defaultText"], surface,
                 brandlib.contrast_ratio(rules["defaultText"], surface)))
        print("  type         %s   weights %s"
              % (profile["type"]["family"],
                 ", ".join(str(w) for w in profile["type"]["approvedWeights"])))
        print("  logo         %s   %d variant(s)"
              % (profile["logo"]["placement"], len(profile["logo"]["variants"])))
        for path in written:
            print("  created      %s" % path)
        if registry:
            print("  registry     %s" % registry)
        if unverified:
            print("")
            print("  UNVERIFIED - neutral defaults are in place for:")
            for path in unverified:
                print("    %s" % path)
            print("  The validator will warn on these until the brand owner confirms them.")
        if warnings:
            print("")
            print("  WARNINGS (%d):" % len(warnings))
            for w in warnings:
                print("    %s" % w)
        print("")
        if not args.dry_run:
            print("  Next: drop logo files into %s"
                  % os.path.join(bdir, "assets", "logos"))
            print("        drop brand fonts into %s"
                  % os.path.join(bdir, "assets", "fonts"))
            print("        confirm with: brand_resolve.py %s" % brand_id)
        return EXIT_OK

    except Refused as exc:
        sys.stderr.write("new_brand: refused - %s\n" % exc)
        return EXIT_REFUSED
    except brandlib.BrandNotFound as exc:
        sys.stderr.write("new_brand: %s\n" % exc)
        return EXIT_INTERNAL
    except (IOError, OSError, ValueError) as exc:
        sys.stderr.write("new_brand: %s\n" % exc)
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
