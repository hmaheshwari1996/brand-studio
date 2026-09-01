#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""brandlib - shared primitives for the brand-studio plugin.

Zero third-party dependencies. Python 3.9 compatible.

Import from any script under <plugin>/scripts:

    import os, sys
    sys.path.insert(0, os.path.join(PLUGIN_ROOT, 'scripts'))
    from lib import brandlib

Sections
    PATHS / LOADING   plugin_root, load_brand, load_grammar, list_brands, resolve_brand
    COLOUR            hex_to_rgb, rgb_to_hex, normalize_hex, relative_luminance,
                      contrast_ratio, delta_e, nearest_token, is_light, palette_index,
                      passes_contrast
    TYPE              weight_to_family, family_to_weight, type_role
    GEOMETRY          inches, emu_to_in, rect_overlap, estimate_text_height, fits_in_box
    TEXT / VOICE      is_sentence_case, find_forbidden_phrases, word_count, srt_timestamp
    VIOLATIONS        Violation, Report

Running this file directly executes the self-test suite.
"""

from __future__ import absolute_import

import argparse
import difflib
import json
import math
import os
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

__all__ = [
    # paths / loading
    "BrandNotFound", "plugin_root", "brand_dir", "load_brand", "load_grammar",
    "list_brands", "resolve_brand", "deep_merge",
    # colour
    "is_hex", "hex_to_rgb", "rgb_to_hex", "normalize_hex", "relative_luminance",
    "contrast_ratio", "delta_e", "nearest_token", "is_light", "palette_index",
    "passes_contrast", "superseded_map", "on_surface_text", "forbidden_text_colors",
    # type
    "weight_to_family", "family_to_weight", "type_role",
    # geometry
    "inches", "emu_to_in", "rect_overlap", "estimate_text_height", "fits_in_box",
    "EMU_PER_INCH", "GLYPH_WIDTH_EM", "DEFAULT_GLYPH_WIDTH_EM", "LEADING_FACTOR",
    # text / voice
    "is_sentence_case", "find_forbidden_phrases", "word_count", "srt_timestamp",
    # violations
    "Violation", "Report", "SEVERITIES", "SEVERITY_ORDER",
]

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

EMU_PER_INCH = 914400

#: Average advance width of a glyph, expressed in em, per PPTX font family.
#: Used by estimate_text_height() for deterministic overflow prediction.
GLYPH_WIDTH_EM = {
    "poppins": 0.520,
    "poppinsmedium": 0.530,
    "poppinssemibold": 0.545,
    "poppinsbold": 0.560,
}
DEFAULT_GLYPH_WIDTH_EM = 0.520

#: Line box height as a multiple of point size.
LEADING_FACTOR = 1.35

SEVERITIES = ("error", "warn", "info")
# Values a learned rule's optional `formats` field accepts. A CLOSED vocabulary:
# 'deck' and 'video' name the artifact kind, the rest name a delivery format
# from brand.video.formats. A reel is 'vertical'.
RULE_FORMATS = ("deck", "video", "landscape", "square", "vertical")
SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}

_HEX_RE = re.compile(r"^#?(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


# ---------------------------------------------------------------------------
# PATHS / LOADING
# ---------------------------------------------------------------------------

class BrandNotFound(Exception):
    """Raised when a brand id cannot be resolved to a brands/<id>/brand.json."""


_ROOT_CACHE = None  # type: Optional[str]


def _looks_like_root(path):
    # type: (str) -> bool
    if os.path.isdir(os.path.join(path, ".claude-plugin")):
        return True
    return (os.path.isdir(os.path.join(path, "brands"))
            and os.path.isdir(os.path.join(path, "grammar")))


def plugin_root():
    # type: () -> str
    """Absolute path of the brand-studio plugin root.

    Honours the BRAND_STUDIO_ROOT environment variable, otherwise walks up
    from this file looking for the plugin markers (.claude-plugin, or both
    brands/ and grammar/). Falls back to two levels above scripts/lib.
    """
    global _ROOT_CACHE
    if _ROOT_CACHE is not None:
        return _ROOT_CACHE

    env = os.environ.get("BRAND_STUDIO_ROOT")
    if env and os.path.isdir(env):
        _ROOT_CACHE = os.path.abspath(env)
        return _ROOT_CACHE

    here = os.path.dirname(os.path.abspath(__file__))
    cur = here
    while True:
        if _looks_like_root(cur):
            _ROOT_CACHE = cur
            return _ROOT_CACHE
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent

    # scripts/lib/brandlib.py -> scripts/lib -> scripts -> root
    _ROOT_CACHE = os.path.abspath(os.path.join(here, os.pardir, os.pardir))
    return _ROOT_CACHE


def brand_dir(brand_id):
    # type: (str) -> str
    """Absolute path of brands/<brand_id>."""
    return os.path.join(plugin_root(), "brands", str(brand_id))


def _read_json(path):
    # type: (str) -> Any
    with open(path, "r") as fh:
        return json.load(fh)


def deep_merge(base, overlay):
    # type: (Any, Any) -> Any
    """Recursively merge ``overlay`` onto ``base``, returning a new structure.

    Dicts merge key-wise. Any other type in ``overlay`` replaces ``base``.
    Neither input is mutated.
    """
    if isinstance(base, dict) and isinstance(overlay, dict):
        out = dict(base)
        for k, v in overlay.items():
            if k in out:
                out[k] = deep_merge(out[k], v)
            else:
                out[k] = v
        return out
    return overlay


def load_brand(brand_id):
    # type: (str) -> Dict[str, Any]
    """Load brands/<brand_id>/brand.json.

    brands/<brand_id>/rules.local.json, when present, is deep-merged into the
    returned profile under the key ``learnedRules``. The key always exists
    (an empty dict when there is no local rules file).

    Raises BrandNotFound when the brand directory or brand.json is missing,
    or ValueError when either file is not valid JSON.
    """
    if not brand_id or not str(brand_id).strip():
        raise BrandNotFound("no brand id given")
    bid = str(brand_id).strip()
    bdir = brand_dir(bid)
    profile_path = os.path.join(bdir, "brand.json")
    if not os.path.isfile(profile_path):
        known = ", ".join(b["id"] for b in list_brands()) or "(none)"
        raise BrandNotFound(
            "brand '%s' not found: %s does not exist. Known brands: %s"
            % (bid, profile_path, known))

    try:
        brand = _read_json(profile_path)
    except ValueError as exc:
        raise ValueError("%s is not valid JSON: %s" % (profile_path, exc))
    if not isinstance(brand, dict):
        raise ValueError("%s must contain a JSON object" % profile_path)

    learned = brand.get("learnedRules")
    if not isinstance(learned, dict):
        learned = {}

    local_path = os.path.join(bdir, "rules.local.json")
    if os.path.isfile(local_path):
        try:
            local = _read_json(local_path)
        except ValueError as exc:
            raise ValueError("%s is not valid JSON: %s" % (local_path, exc))
        if isinstance(local, dict):
            learned = deep_merge(learned, local)
        else:
            learned = deep_merge(learned, {"rules": local})

    brand["learnedRules"] = learned
    brand.setdefault("id", bid)
    brand["_dir"] = bdir
    return brand


def load_grammar():
    # type: () -> Dict[str, Any]
    """Load grammar/deck-grammar.json."""
    path = os.path.join(plugin_root(), "grammar", "deck-grammar.json")
    if not os.path.isfile(path):
        raise IOError("deck grammar not found at %s" % path)
    grammar = _read_json(path)
    if not isinstance(grammar, dict):
        raise ValueError("%s must contain a JSON object" % path)
    return grammar


def _registry_entry(raw):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    aliases = raw.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    return {
        "id": str(raw.get("id") or ""),
        "name": str(raw.get("name") or raw.get("id") or ""),
        "aliases": [str(a) for a in aliases],
        "updated": str(raw.get("updated") or ""),
    }


def list_brands():
    # type: () -> List[Dict[str, Any]]
    """Every registered brand as {id, name, aliases, updated}.

    Reads brands/_registry.json when it exists. When it does not (or is
    unreadable), the brands/ directory is scanned and each brand.json is read
    directly, so the plugin still works before a registry has been written.
    """
    root = plugin_root()
    brands_dir = os.path.join(root, "brands")
    registry_path = os.path.join(brands_dir, "_registry.json")

    entries = []  # type: List[Dict[str, Any]]
    if os.path.isfile(registry_path):
        try:
            data = _read_json(registry_path)
        except ValueError:
            data = None
        rows = None
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            for key in ("brands", "entries", "items"):
                if isinstance(data.get(key), list):
                    rows = data[key]
                    break
        if rows is not None:
            for raw in rows:
                if isinstance(raw, dict) and raw.get("id"):
                    entries.append(_registry_entry(raw))
        if entries:
            return entries

    if not os.path.isdir(brands_dir):
        return []
    for name in sorted(os.listdir(brands_dir)):
        if name.startswith("_") or name.startswith("."):
            continue
        profile = os.path.join(brands_dir, name, "brand.json")
        if not os.path.isfile(profile):
            continue
        try:
            raw = _read_json(profile)
        except ValueError:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        raw = dict(raw)
        raw.setdefault("id", name)
        entries.append(_registry_entry(raw))
    return entries


def _norm_name(s):
    # type: (str) -> str
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def resolve_brand(name):
    # type: (str) -> Dict[str, Any]
    """Resolve a human-typed brand name to a brand id.

    Order: exact id, exact name (case-insensitive), alias, then difflib.

    Returns {"match": "exact"|"fuzzy"|"none",
             "brand": <brand id or None>,
             "score": float,
             "candidates": [{"id","name","score"}, ...]}
    """
    brands = list_brands()
    query = _norm_name(name)
    result = {"match": "none", "brand": None, "score": 0.0, "candidates": []}
    if not brands:
        return result

    if query:
        for b in brands:                                    # exact id
            if _norm_name(b["id"]) == query:
                return {"match": "exact", "brand": b["id"], "score": 1.0,
                        "candidates": [{"id": b["id"], "name": b["name"], "score": 1.0}]}
        for b in brands:                                    # exact name
            if _norm_name(b["name"]) == query:
                return {"match": "exact", "brand": b["id"], "score": 1.0,
                        "candidates": [{"id": b["id"], "name": b["name"], "score": 1.0}]}
        for b in brands:                                    # alias
            for alias in b.get("aliases", []):
                if _norm_name(alias) == query:
                    return {"match": "exact", "brand": b["id"], "score": 1.0,
                            "candidates": [{"id": b["id"], "name": b["name"], "score": 1.0}]}

    scored = []  # type: List[Dict[str, Any]]
    for b in brands:
        keys = [b["id"], b["name"]] + list(b.get("aliases", []))
        best = 0.0
        if query:
            for k in keys:
                nk = _norm_name(k)
                if not nk:
                    continue
                ratio = difflib.SequenceMatcher(None, query, nk).ratio()
                if nk.startswith(query) or query.startswith(nk):
                    ratio = max(ratio, 0.9)
                elif query in nk or nk in query:
                    ratio = max(ratio, 0.8)
                if ratio > best:
                    best = ratio
        scored.append({"id": b["id"], "name": b["name"], "score": round(best, 4)})

    scored.sort(key=lambda c: (-c["score"], c["id"]))
    result["candidates"] = scored[:5]
    if scored and scored[0]["score"] >= 0.6:
        result["match"] = "fuzzy"
        result["brand"] = scored[0]["id"]
        result["score"] = scored[0]["score"]
    else:
        result["score"] = scored[0]["score"] if scored else 0.0
    return result


# ---------------------------------------------------------------------------
# COLOUR
# ---------------------------------------------------------------------------

def is_hex(value):
    # type: (Any) -> bool
    """True when ``value`` is a string parseable as a hex colour."""
    return isinstance(value, str) and bool(_HEX_RE.match(value.strip()))


def normalize_hex(h):
    # type: (str) -> str
    """Return '#RRGGBB' uppercase. Accepts #abc, abc, #aabbcc, aabbccff."""
    if not isinstance(h, str):
        raise ValueError("not a colour string: %r" % (h,))
    s = h.strip()
    if not _HEX_RE.match(s):
        raise ValueError("not a hex colour: %r" % (h,))
    s = s.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    elif len(s) == 8:
        s = s[:6]
    return "#" + s.upper()


def hex_to_rgb(h):
    # type: (str) -> Tuple[int, int, int]
    """'#0000FF' -> (0, 0, 255)."""
    s = normalize_hex(h).lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def rgb_to_hex(t):
    # type: (Sequence[float]) -> str
    """(0, 0, 255) -> '#0000FF'. Values are clamped to 0..255 and rounded."""
    if len(t) < 3:
        raise ValueError("rgb tuple needs 3 components, got %r" % (t,))
    out = []
    for c in list(t)[:3]:
        v = int(round(float(c)))
        v = 0 if v < 0 else (255 if v > 255 else v)
        out.append(v)
    return "#%02X%02X%02X" % (out[0], out[1], out[2])


def _linearize(channel_0_255):
    # type: (float) -> float
    c = float(channel_0_255) / 255.0
    if c <= 0.03928:
        return c / 12.92
    return math.pow((c + 0.055) / 1.055, 2.4)


def relative_luminance(h):
    # type: (str) -> float
    """WCAG 2.1 relative luminance, 0.0 (black) .. 1.0 (white)."""
    r, g, b = hex_to_rgb(h)
    return (0.2126 * _linearize(r)
            + 0.7152 * _linearize(g)
            + 0.0722 * _linearize(b))


def contrast_ratio(a, b):
    # type: (str, str) -> float
    """WCAG 2.1 contrast ratio between two hex colours, 1.0 .. 21.0."""
    la = relative_luminance(a)
    lb = relative_luminance(b)
    hi, lo = (la, lb) if la >= lb else (lb, la)
    return (hi + 0.05) / (lo + 0.05)


def _rgb_to_xyz(h):
    # type: (str) -> Tuple[float, float, float]
    r, g, b = hex_to_rgb(h)
    rl, gl, bl = _linearize(r), _linearize(g), _linearize(b)
    x = (0.4124564 * rl + 0.3575761 * gl + 0.1804375 * bl) * 100.0
    y = (0.2126729 * rl + 0.7151522 * gl + 0.0721750 * bl) * 100.0
    z = (0.0193339 * rl + 0.1191920 * gl + 0.9503041 * bl) * 100.0
    return (x, y, z)


_D65 = (95.047, 100.000, 108.883)
_LAB_EPS = (6.0 / 29.0) ** 3
_LAB_K = 1.0 / (3.0 * (6.0 / 29.0) ** 2)


def _lab_f(t):
    # type: (float) -> float
    if t > _LAB_EPS:
        return math.pow(t, 1.0 / 3.0)
    return _LAB_K * t + 4.0 / 29.0


def _rgb_to_lab(h):
    # type: (str) -> Tuple[float, float, float]
    x, y, z = _rgb_to_xyz(h)
    fx = _lab_f(x / _D65[0])
    fy = _lab_f(y / _D65[1])
    fz = _lab_f(z / _D65[2])
    return (116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz))


def delta_e(a, b):
    # type: (str, str) -> float
    """CIE76 colour difference in Lab. 0 == identical, ~2.3 == just noticeable."""
    la, aa, ba = _rgb_to_lab(a)
    lb, ab, bb = _rgb_to_lab(b)
    return math.sqrt((la - lb) ** 2 + (aa - ab) ** 2 + (ba - bb) ** 2)


def _palette_pairs(palette_dict):
    # type: (Dict[str, str]) -> List[Tuple[str, str]]
    """Normalise a palette mapping into [(token_name, token_hex), ...].

    Accepts either {hex: token} (as produced by palette_index) or
    {token: hex}. Detection is per-entry so mixed inputs still work.
    """
    pairs = []
    for k, v in palette_dict.items():
        if is_hex(k) and not is_hex(v):
            pairs.append((str(v), normalize_hex(k)))
        elif is_hex(v):
            pairs.append((str(k), normalize_hex(v)))
        elif is_hex(k):
            pairs.append((str(v), normalize_hex(k)))
    return pairs


def nearest_token(h, palette_dict):
    # type: (str, Dict[str, str]) -> Tuple[Optional[str], Optional[str], float]
    """Closest palette entry to ``h`` by CIE76 delta E.

    ``palette_dict`` may be {hex: token} (palette_index output) or {token: hex}.
    Returns (token_name, token_hex, delta_e); (None, None, inf) for an empty
    palette.
    """
    target = normalize_hex(h)
    best_name = None  # type: Optional[str]
    best_hex = None   # type: Optional[str]
    best_d = float("inf")
    for name, thex in _palette_pairs(palette_dict or {}):
        d = delta_e(target, thex)
        if d < best_d:
            best_d, best_name, best_hex = d, name, thex
    return (best_name, best_hex, best_d)


def is_light(h):
    # type: (str) -> bool
    """True when the colour's WCAG relative luminance exceeds 0.5."""
    return relative_luminance(h) > 0.5


def _walk_colors(node, path, out):
    # type: (Any, str, Dict[str, str]) -> None
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "superseded" or str(k).startswith("$"):
                continue
            _walk_colors(v, ("%s.%s" % (path, k)) if path else str(k), out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_colors(v, "%s.%d" % (path, i), out)
    elif is_hex(node):
        key = normalize_hex(node)
        if key not in out:
            out[key] = path


def palette_index(brand):
    # type: (Dict[str, Any]) -> Dict[str, str]
    """Flat {'#RRGGBB': 'token.path'} of every approved colour in the brand.

    Walks brand['color'] recursively: brand.*, every ramp, semantic, and every
    gradient stop. The superseded map is deliberately excluded - those hexes
    are not approved and must never win a nearest_token() lookup. When one hex
    appears under several paths the first (most canonical, e.g. 'brand.blue')
    wins.
    """
    out = {}  # type: Dict[str, str]
    color = (brand or {}).get("color")
    if isinstance(color, dict):
        _walk_colors(color, "", out)
    return out


def superseded_map(brand):
    # type: (Dict[str, Any]) -> Dict[str, str]
    """{'#OLD': '#CANONICAL'} of unapproved template colours, normalised."""
    raw = (((brand or {}).get("color") or {}).get("superseded") or {}).get("map") or {}
    out = {}
    for k, v in raw.items():
        if is_hex(k) and is_hex(v):
            out[normalize_hex(k)] = normalize_hex(v)
    return out


def on_surface_text(brand, bg_hex):
    # type: (Dict[str, Any], str) -> Optional[str]
    """The mandated text colour for a given background, or None if unlisted."""
    rules = ((brand or {}).get("colorRules") or {}).get("onSurface") or {}
    want = normalize_hex(bg_hex)
    for k, v in rules.items():
        if is_hex(k) and normalize_hex(k) == want and is_hex(v):
            return normalize_hex(v)
    return None


def forbidden_text_colors(brand):
    # type: (Dict[str, Any]) -> Dict[str, str]
    """{'#HEX': 'reason'} of colours that must never be used as text."""
    rules = ((brand or {}).get("colorRules") or {}).get("forbiddenText") or {}
    out = {}
    for k, v in rules.items():
        if is_hex(k):
            out[normalize_hex(k)] = str(v)
    return out


def passes_contrast(fg, bg, pt, bold):
    # type: (str, str, float, bool) -> Tuple[bool, float, float]
    """WCAG AA check. Returns (passes, required_ratio, actual_ratio).

    Large text (>= 18pt, or >= 14pt when bold) needs 3.0:1, everything else
    needs 4.5:1.
    """
    size = float(pt)
    large = size >= 18.0 or (bool(bold) and size >= 14.0)
    required = 3.0 if large else 4.5
    actual = contrast_ratio(fg, bg)
    return (actual + 1e-9 >= required, required, actual)


# ---------------------------------------------------------------------------
# TYPE
# ---------------------------------------------------------------------------

def _norm_family(family):
    # type: (str) -> str
    return re.sub(r"[^a-z0-9]+", "", str(family or "").lower())


def weight_to_family(brand, weight):
    # type: (Dict[str, Any], Union[int, str]) -> str
    """Numeric weight -> PPTX font family name. 600 -> 'Poppins SemiBold'.

    Raises ValueError for a weight the brand does not approve.
    """
    tmap = ((brand or {}).get("type") or {}).get("weightToPptxFamily") or {}
    try:
        key = str(int(float(weight)))
    except (TypeError, ValueError):
        raise ValueError("weight must be numeric, got %r" % (weight,))
    if key in tmap:
        return str(tmap[key])
    base = ((brand or {}).get("type") or {}).get("family")
    if key == "400" and base:
        return str(base)
    approved = ", ".join(sorted(tmap.keys(), key=lambda k: int(k))) or "(none)"
    raise ValueError(
        "weight %s is not an approved weight for brand '%s'. Approved: %s"
        % (key, (brand or {}).get("id", "?"), approved))


def family_to_weight(brand, family):
    # type: (Dict[str, Any], str) -> Optional[int]
    """PPTX font family name -> numeric weight, or None when unrecognised.

    Tolerant of case, spacing and separators: 'poppins semibold',
    'Poppins-SemiBold' and 'PoppinsSemiBold' all resolve to 600.
    """
    if not family:
        return None
    tmap = ((brand or {}).get("type") or {}).get("weightToPptxFamily") or {}
    lookup = {}  # type: Dict[str, int]
    for w, fam in tmap.items():
        try:
            wi = int(str(w))
        except ValueError:
            continue
        lookup[_norm_family(fam)] = wi
    base = ((brand or {}).get("type") or {}).get("family")
    if base:
        lookup.setdefault(_norm_family(base), 400)
    return lookup.get(_norm_family(family))


def type_role(brand, role):
    # type: (Dict[str, Any], str) -> Dict[str, Any]
    """The deck type scale entry for ``role`` (size, leading, weight, tracking).

    Raises KeyError naming the available roles when ``role`` is unknown.
    """
    scale = ((brand or {}).get("type") or {}).get("deckScalePt") or {}
    if role in scale and isinstance(scale[role], dict):
        return dict(scale[role])
    raise KeyError(
        "type role '%s' is not defined for brand '%s'. Available roles: %s"
        % (role, (brand or {}).get("id", "?"), ", ".join(sorted(scale.keys())) or "(none)"))


# ---------------------------------------------------------------------------
# GEOMETRY
# ---------------------------------------------------------------------------

def inches(v):
    # type: (float) -> int
    """Inches -> EMU (English Metric Units), rounded to the nearest unit."""
    return int(round(float(v) * EMU_PER_INCH))


def emu_to_in(v):
    # type: (float) -> float
    """EMU -> inches."""
    return float(v) / float(EMU_PER_INCH)


def rect_overlap(a, b):
    # type: (Sequence[float], Sequence[float]) -> float
    """Overlapping area of two (left, top, width, height) rects, in sq inches."""
    al, at, aw, ah = [float(x) for x in a[:4]]
    bl, bt, bw, bh = [float(x) for x in b[:4]]
    dx = min(al + aw, bl + bw) - max(al, bl)
    dy = min(at + ah, bt + bh) - max(at, bt)
    if dx <= 0.0 or dy <= 0.0:
        return 0.0
    return dx * dy


def _glyph_width_em(family):
    # type: (Optional[str]) -> float
    return GLYPH_WIDTH_EM.get(_norm_family(family), DEFAULT_GLYPH_WIDTH_EM)


def _wrapped_line_count(text, chars_per_line):
    # type: (str, int) -> int
    cpl = max(1, int(chars_per_line))
    lines = 0
    for para in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        words = para.split()
        if not words:
            lines += 1
            continue
        used = 0
        for w in words:
            lw = len(w)
            need = lw if used == 0 else used + 1 + lw
            if need <= cpl:
                used = need
                continue
            if used > 0:
                lines += 1
                used = 0
            if lw > cpl:
                lines += lw // cpl
                used = lw % cpl
            else:
                used = lw
        if used > 0:
            lines += 1
    return max(1, lines)


def estimate_text_height(text, pt, width_in, family, leading_pt=None):
    # type: (str, float, float, Optional[str], Optional[float]) -> float
    """Deterministic estimate of the height a text block needs, in inches.

    Characters per line come from an average glyph advance per family
    (Poppins ~0.52 em, Poppins Medium ~0.53, Poppins SemiBold ~0.545).

    ``leading_pt`` is the line box in points. Pass the leading that will ACTUALLY
    be applied -- the brand type scale carries an explicit leading per role, and
    several of them are looser than LEADING_FACTOR (subtitle is 18/26, a 1.44
    ratio, not 1.35). Measuring with the default while painting with the role's
    leading under-estimates the height and the block silently overflows, so a
    caller that sets line spacing explicitly must pass it here too. Omitted, the
    estimate falls back to LEADING_FACTOR x the point size.

    Explicit newlines are honoured and over-long words wrap mid-word. Empty text
    needs no height.
    """
    if text is None or not str(text).strip():
        return 0.0
    size = float(pt)
    if size <= 0:
        raise ValueError("pt must be positive, got %r" % (pt,))
    w = float(width_in)
    if w <= 0:
        raise ValueError("width_in must be positive, got %r" % (width_in,))
    line_box = float(leading_pt) if leading_pt else (LEADING_FACTOR * size)
    if line_box <= 0:
        line_box = LEADING_FACTOR * size
    char_w_in = _glyph_width_em(family) * size / 72.0
    chars_per_line = max(1, int(math.floor(w / char_w_in)))
    lines = _wrapped_line_count(text, chars_per_line)
    return lines * line_box / 72.0


def fits_in_box(text, pt, box_w_in, box_h_in, family, leading_pt=None):
    # type: (str, float, float, float, Optional[str], Optional[float]) -> Tuple[bool, float]
    """(fits, needed_height_in) for a text block in a fixed box.

    Pass ``leading_pt`` whenever the caller sets line spacing explicitly; see
    ``estimate_text_height``.
    """
    needed = estimate_text_height(text, pt, box_w_in, family, leading_pt)
    return (needed <= float(box_h_in) + 1e-6, needed)


# ---------------------------------------------------------------------------
# TEXT / VOICE
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’\-]*")
_SENTENCE_BREAK = frozenset(".?!:;-–—")


def _is_acronymish(word):
    # type: (str) -> bool
    letters = [c for c in word if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def is_sentence_case(s):
    # type: (str) -> bool
    """True when ``s`` reads as sentence case.

    Tolerant by design: acronyms (KPI, ROI), already-capitalised proper nouns,
    numbers, and the word that starts a new sentence after . ? ! : ; or a dash
    are all allowed. It flags the two failure modes that matter for decks:
    ALL CAPS strings and Title Case Strings Like This One.
    """
    if s is None:
        return True
    text = str(s).strip()
    if not text:
        return True

    tokens = text.split()
    alpha_tokens = [t for t in tokens if any(c.isalpha() for c in t)]
    if not alpha_tokens:
        return True

    # ALL CAPS
    letters = [c for c in text if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        if len(letters) > 4 or len(alpha_tokens) >= 3:
            return False
        return True

    # TITLE CASE: capitalised words that are neither sentence-initial,
    # acronyms, single letters, nor digit-bearing.
    eligible = 0
    capitalised = 0
    new_sentence = True
    for tok in tokens:
        core = tok.strip("\"'“”‘’()[]{}")
        m = _WORD_RE.match(core)
        word = m.group(0) if m else ""
        if not word:
            if core and core[-1] in _SENTENCE_BREAK:
                new_sentence = True
            continue
        if not new_sentence and len(word) > 1 and not _is_acronymish(word) \
                and not any(c.isdigit() for c in core):
            eligible += 1
            if word[0].isupper():
                capitalised += 1
        new_sentence = bool(core) and core[-1] in _SENTENCE_BREAK

    # Deck titles are short -- "Retail Execution Measured" has only two eligible
    # words -- so a 3-word floor would never fire on the strings that matter most.
    # The ratio is held at 0.75 rather than 0.6 so a sentence-case title carrying
    # two proper nouns ("From Delhi to Mumbai", 2 of 3) is not flagged, while real
    # Title Case (all eligible words capitalised) still is.
    if eligible >= 2 and (float(capitalised) / float(eligible)) >= 0.75:
        return False
    return True


def rule_formats(raw):
    # type: (Optional[Dict[str, Any]]) -> Tuple[List[str], List[str]]
    """(recognised, unrecognised) values from a learned rule's `formats` field.

    Absent, empty or entirely unrecognised all mean "applies everywhere". That
    is deliberate on both counts: every rule written before the field existed is
    unscoped, and a typo must never narrow a rule to nothing. A rule silently
    disabled by a misspelling is the failure this field exists to prevent, so
    the unrecognised values are returned for the caller to REPORT while the rule
    keeps running.
    """
    value = (raw or {}).get("formats")
    if value is None:
        return ([], [])
    values = value if isinstance(value, (list, tuple)) else [value]
    good = []  # type: List[str]
    bad = []  # type: List[str]
    for item in values:
        name = str(item).strip().lower()
        if not name:
            continue
        if name in RULE_FORMATS:
            good.append(name)
        else:
            bad.append(name)
    return (good, bad)


def rule_applies(raw, kind, format_name=None):
    # type: (Optional[Dict[str, Any]], str, Optional[str]) -> bool
    """Does a learned rule apply to the artifact being validated?

    kind         'deck' or 'video'
    format_name  a video's resolved delivery format ('vertical', 'landscape'...)

    An unscoped rule applies everywhere. A scoped rule applies when its list
    names the artifact kind, or -- for a video -- the delivery format. So
    ``"formats": ["vertical"]`` is the reel-only rule the ledger describes, and
    ``["deck"]`` keeps a slide rule off every film.
    """
    good, _unknown = rule_formats(raw)
    if not good:
        return True
    if str(kind or "").strip().lower() in good:
        return True
    if format_name and str(format_name).strip().lower() in good:
        return True
    return False


def find_forbidden_phrases(text, phrases):
    # type: (Optional[str], Optional[Sequence[str]]) -> List[str]
    """Forbidden phrases present in ``text`` (case-insensitive substring match).

    Returns the phrases as spelled in ``phrases``, in that order, deduplicated.
    """
    if not text or not phrases:
        return []
    hay = str(text).lower()
    hits = []
    seen = set()
    for p in phrases:
        needle = str(p or "").strip().lower()
        if not needle or needle in seen:
            continue
        if needle in hay:
            hits.append(str(p))
            seen.add(needle)
    return hits


def word_count(s):
    # type: (Optional[str]) -> int
    """Words in ``s``. Whitespace-separated tokens with at least one alnum."""
    if not s:
        return 0
    return len([t for t in str(s).split() if any(c.isalnum() for c in t)])


def srt_timestamp(seconds):
    # type: (float) -> str
    """Seconds -> 'HH:MM:SS,mmm' (SubRip). Negative input clamps to zero."""
    total_ms = int(round(float(seconds) * 1000.0))
    if total_ms < 0:
        total_ms = 0
    ms = total_ms % 1000
    total_s = total_ms // 1000
    ss = total_s % 60
    mm = (total_s // 60) % 60
    hh = total_s // 3600
    return "%02d:%02d:%02d,%03d" % (hh, mm, ss, ms)


# ---------------------------------------------------------------------------
# VIOLATIONS
# ---------------------------------------------------------------------------

class Violation(object):
    """One rule breach, matching FROZEN CONTRACT C's violation shape."""

    __slots__ = ("id", "severity", "where", "found", "expected", "rule", "fix")

    def __init__(self, id, severity, where="", found="", expected="", rule="", fix=""):
        # type: (str, str, Any, Any, Any, Any, Any) -> None
        sev = str(severity or "").strip().lower()
        if sev not in SEVERITIES:
            raise ValueError(
                "severity must be one of %s, got %r" % (", ".join(SEVERITIES), severity))
        vid = str(id or "").strip()
        if not vid:
            raise ValueError("violation id is required (e.g. 'COLOR.SUPERSEDED')")
        self.id = vid
        self.severity = sev
        self.where = "" if where is None else str(where)
        self.found = "" if found is None else str(found)
        self.expected = "" if expected is None else str(expected)
        self.rule = "" if rule is None else str(rule)
        self.fix = "" if fix is None else str(fix)

    @property
    def namespace(self):
        # type: () -> str
        return self.id.split(".", 1)[0]

    def to_dict(self):
        # type: () -> Dict[str, str]
        return {
            "id": self.id,
            "severity": self.severity,
            "where": self.where,
            "found": self.found,
            "expected": self.expected,
            "rule": self.rule,
            "fix": self.fix,
        }

    def __repr__(self):
        # type: () -> str
        return "<Violation %s %s @ %s>" % (self.severity.upper(), self.id, self.where or "-")


class Report(object):
    """Accumulates Violations and renders CONTRACT C json or a human report."""

    def __init__(self, target=None, brand=None, kind=None):
        # type: (Optional[str], Optional[str], Optional[str]) -> None
        self.target = target
        self.brand = brand
        self.kind = kind
        self.violations = []  # type: List[Violation]

    # -- collection ---------------------------------------------------------

    def add(self, violation=None, **kw):
        # type: (Union[Violation, str, None], Any) -> Violation
        """Add a Violation.

        Accepts an instance, ``add(id='COLOR.X', severity='error', ...)``, or
        ``add('COLOR.X', severity='error', ...)``.
        """
        if isinstance(violation, Violation):
            v = violation
        elif isinstance(violation, str):
            v = Violation(violation, **kw)
        elif violation is None:
            v = Violation(**kw)
        else:
            raise TypeError("add() expects a Violation, an id string, or keywords")
        self.violations.append(v)
        return v

    def extend(self, violations):
        # type: (Sequence[Violation]) -> None
        for v in violations or []:
            self.add(v)

    def __len__(self):
        # type: () -> int
        return len(self.violations)

    # -- results ------------------------------------------------------------

    def counts(self):
        # type: () -> Dict[str, int]
        c = {"error": 0, "warn": 0, "info": 0}
        for v in self.violations:
            c[v.severity] = c.get(v.severity, 0) + 1
        return c

    def passed(self):
        # type: () -> bool
        return self.counts()["error"] == 0

    def exit_code(self):
        # type: () -> int
        return 2 if self.counts()["error"] > 0 else 0

    def _sorted(self):
        # type: () -> List[Violation]
        indexed = list(enumerate(self.violations))
        indexed.sort(key=lambda p: (SEVERITY_ORDER.get(p[1].severity, 9), p[0]))
        return [v for _, v in indexed]

    def to_json(self, target=None, brand=None, kind=None):
        # type: (Optional[str], Optional[str], Optional[str]) -> Dict[str, Any]
        """FROZEN CONTRACT C dict. Ready for json.dumps()."""
        tgt = target if target is not None else self.target
        brd = brand if brand is not None else self.brand
        knd = kind if kind is not None else self.kind
        counts = self.counts()
        return {
            "target": "" if tgt is None else str(tgt),
            "brand": "" if brd is None else str(brd),
            "kind": "" if knd is None else str(knd),
            "pass": counts["error"] == 0,
            "counts": counts,
            "violations": [v.to_dict() for v in self._sorted()],
        }

    def to_human(self, target=None, brand=None, kind=None):
        # type: (Optional[str], Optional[str], Optional[str]) -> str
        """Plain-text report, grouped by severity then namespace. No colour."""
        tgt = target if target is not None else self.target
        brd = brand if brand is not None else self.brand
        knd = kind if kind is not None else self.kind
        counts = self.counts()
        ok = counts["error"] == 0

        lines = []
        lines.append("brand-studio validation")
        lines.append("  target : %s" % (tgt if tgt else "-"))
        lines.append("  brand  : %s" % (brd if brd else "-"))
        lines.append("  kind   : %s" % (knd if knd else "-"))
        lines.append("  result : %s  (%d error, %d warn, %d info)"
                     % ("PASS" if ok else "FAIL",
                        counts["error"], counts["warn"], counts["info"]))
        lines.append("")

        if not self.violations:
            lines.append("No violations found.")
            lines.append("")
            return "\n".join(lines)

        for sev in SEVERITIES:
            group = [v for v in self.violations if v.severity == sev]
            if not group:
                continue
            lines.append("%s (%d)" % (sev.upper(), len(group)))
            lines.append("-" * 72)
            ordered = list(enumerate(group))
            ordered.sort(key=lambda p: (p[1].namespace, p[1].id, p[0]))
            last_ns = None
            for _, v in ordered:
                if v.namespace != last_ns:
                    lines.append("  [%s]" % v.namespace)
                    last_ns = v.namespace
                lines.append("    %-28s %s" % (v.id, v.where or "-"))
                if v.found:
                    lines.append("      found    : %s" % v.found)
                if v.expected:
                    lines.append("      expected : %s" % v.expected)
                if v.rule:
                    lines.append("      rule     : %s" % v.rule)
                if v.fix:
                    lines.append("      fix      : %s" % v.fix)
            lines.append("")

        return "\n".join(lines)

    def __repr__(self):
        # type: () -> str
        c = self.counts()
        return "<Report %s e=%d w=%d i=%d>" % (self.target, c["error"], c["warn"], c["info"])


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

class _Checker(object):
    def __init__(self):
        self.passed = 0

    def ok(self, cond, label):
        if not cond:
            raise AssertionError("FAILED: %s" % label)
        self.passed += 1

    def close(self, a, b, tol, label):
        self.ok(abs(float(a) - float(b)) <= tol,
                "%s (got %r, want %r +/- %r)" % (label, a, b, tol))


def _self_test(verbose=True):
    # type: (bool) -> int
    c = _Checker()

    # --- hex plumbing ---
    c.ok(normalize_hex("0f0a6c") == "#0F0A6C", "normalize_hex lowercase no-hash")
    c.ok(normalize_hex("#abc") == "#AABBCC", "normalize_hex shorthand")
    c.ok(hex_to_rgb("#0000FF") == (0, 0, 255), "hex_to_rgb blue")
    c.ok(rgb_to_hex((65, 231, 171)) == "#41E7AB", "rgb_to_hex mint")

    # --- WCAG contrast, known pairs ---
    c.close(contrast_ratio("#000000", "#FFFFFF"), 21.0, 0.001, "contrast black/white")
    c.close(contrast_ratio("#FFFFFF", "#FFFFFF"), 1.0, 0.001, "contrast white/white")
    c.close(contrast_ratio("#777777", "#FFFFFF"), 4.478, 0.01, "contrast #777 on white")
    c.close(contrast_ratio("#0000FF", "#FFFFFF"), 8.592, 0.01, "contrast blue on white")
    c.close(contrast_ratio("#FFFFFF", "#0000FF"),
            contrast_ratio("#0000FF", "#FFFFFF"), 1e-9, "contrast is symmetric")
    c.ok(contrast_ratio("#41E7AB", "#FFFFFF") < 2.0, "mint on white is decorative only")
    c.ok(contrast_ratio("#0F0A6C", "#FFFFFF") > 4.5, "navy body text passes AA on white")

    # --- passes_contrast ---
    ok12, req12, act12 = passes_contrast("#0194DD", "#FFFFFF", 12, False)
    c.ok(ok12 is False and req12 == 4.5, "sky at 12pt fails AA body on white")
    ok18, req18, act18 = passes_contrast("#0194DD", "#FFFFFF", 18, False)
    c.ok(ok18 is True and req18 == 3.0, "sky at 18pt passes AA large on white")
    c.close(act12, act18, 1e-9, "contrast is size-independent")
    ok14b, req14b, _ = passes_contrast("#0194DD", "#FFFFFF", 14, True)
    c.ok(ok14b is True and req14b == 3.0, "14pt bold counts as large text")

    # --- luminance / is_light ---
    c.ok(is_light("#FFFFFF") is True, "white is light")
    c.ok(is_light("#0F0A6C") is False, "navy is dark")
    c.ok(is_light("#41E7AB") is True, "mint is light")

    # --- delta E ---
    c.close(delta_e("#0000FF", "#0000FF"), 0.0, 1e-9, "delta_e identity is zero")
    c.close(delta_e("#FFFFFF", "#000000"), 100.0, 0.01, "delta_e white/black is 100")
    c.ok(delta_e("#0000FF", "#0000D5") < delta_e("#0000FF", "#41E7AB"),
         "superseded blue is nearer to blue than to mint")

    # --- brand loading + palette ---
    brand = load_brand("example")
    c.ok(brand["id"] == "example", "load_brand returns the example profile")
    c.ok(isinstance(brand.get("learnedRules"), dict), "learnedRules key always present")
    idx = palette_index(brand)
    c.ok(idx.get("#0000FF") == "brand.blue", "palette_index keys canonical brand path")
    c.ok(idx.get("#0F0A6C") == "brand.navy", "palette_index includes navy")
    c.ok("#080540" in idx and "#1B7A74" in idx, "palette_index includes ramp steps")
    c.ok("#42E6AB" in idx, "palette_index includes gradient stops")
    c.ok("#0000D5" not in idx, "palette_index excludes superseded colours")
    name, thex, d = nearest_token("#0000FF", idx)
    c.ok(name == "brand.blue" and thex == "#0000FF" and d == 0.0, "nearest_token exact hit")
    name2, thex2, d2 = nearest_token("#41E7AA", idx)
    c.ok(thex2 == "#41E7AB" and d2 < 2.0, "nearest_token snaps a near-mint to mint")
    name3, thex3, _ = nearest_token("#0000FF", {"brand.blue": "#0000FF"})
    c.ok(name3 == "brand.blue" and thex3 == "#0000FF", "nearest_token accepts {token: hex}")
    c.ok(superseded_map(brand)["#0000D5"] == "#0000FF", "superseded map is normalised")

    # --- brand resolution ---
    r = resolve_brand("example")
    c.ok(r["match"] == "exact" and r["brand"] == "example", "resolve_brand exact id")
    r = resolve_brand("Channel Play")
    c.ok(r["match"] == "exact" and r["brand"] == "example", "resolve_brand alias")
    r = resolve_brand("channelpaly")
    c.ok(r["match"] == "fuzzy" and r["brand"] == "example", "resolve_brand fuzzy typo")
    r = resolve_brand("zzzzzzzzzz")
    c.ok(r["match"] == "none" and r["brand"] is None, "resolve_brand gives up cleanly")
    try:
        load_brand("no-such-brand")
        c.ok(False, "load_brand raises BrandNotFound")
    except BrandNotFound:
        c.ok(True, "load_brand raises BrandNotFound")

    # --- grammar ---
    grammar = load_grammar()
    c.ok(grammar["canvas"]["w"] == 13.333, "load_grammar returns the 16:9 canvas")
    c.ok(len(grammar["archetypes"]) >= 17, "grammar carries the full archetype set")

    # --- type mapping ---
    c.ok(weight_to_family(brand, 600) == "Poppins SemiBold", "weight 600 -> SemiBold family")
    c.ok(weight_to_family(brand, "400") == "Poppins", "weight 400 -> Poppins")
    c.ok(weight_to_family(brand, 500) == "Poppins Medium", "weight 500 -> Medium family")
    c.ok(family_to_weight(brand, "poppins semibold") == 600, "family_to_weight is case-tolerant")
    c.ok(family_to_weight(brand, "PoppinsSemiBold") == 600, "family_to_weight is space-tolerant")
    c.ok(family_to_weight(brand, "Poppins-Medium") == 500, "family_to_weight is dash-tolerant")
    c.ok(family_to_weight(brand, "Arial") is None, "family_to_weight rejects foreign families")
    try:
        weight_to_family(brand, 700)
        c.ok(False, "weight_to_family rejects unapproved weight 700")
    except ValueError:
        c.ok(True, "weight_to_family rejects unapproved weight 700")
    c.ok(type_role(brand, "body")["size"] == 12, "type_role body is 12pt")
    c.ok(type_role(brand, "caption")["size"] == brand["type"]["minBodyPt"],
         "caption sits on the minimum body size")
    try:
        type_role(brand, "nope")
        c.ok(False, "type_role raises KeyError for an unknown role")
    except KeyError:
        c.ok(True, "type_role raises KeyError for an unknown role")

    # --- geometry ---
    c.ok(inches(1) == 914400, "inches -> EMU")
    c.ok(inches(0.869) == 794614, "inches rounds to whole EMU")
    c.close(emu_to_in(914400), 1.0, 1e-12, "emu_to_in round trip")
    c.close(rect_overlap((0, 0, 2, 2), (1, 1, 2, 2)), 1.0, 1e-9, "rect_overlap corner")
    c.close(rect_overlap((0, 0, 1, 1), (2, 2, 1, 1)), 0.0, 1e-9, "rect_overlap disjoint")
    c.close(rect_overlap((0, 0, 4, 4), (1, 1, 1, 1)), 1.0, 1e-9, "rect_overlap containment")

    # --- text metrics ---
    one_line = estimate_text_height("Retail execution", 12, 3.23, "Poppins")
    c.close(one_line, 1.35 * 12 / 72.0, 1e-9, "a short string is exactly one line box")
    c.ok(estimate_text_height("", 12, 3.23, "Poppins") == 0.0, "empty text needs no height")
    long_txt = ("Example Brand runs retail execution programmes across India with "
                "trained field teams, daily reporting and measurable outcomes. ") * 3
    h12 = estimate_text_height(long_txt, 12, 3.23, "Poppins")
    h24 = estimate_text_height(long_txt, 24, 3.23, "Poppins")
    c.ok(h24 > h12 > one_line, "height grows with copy length and point size")
    h_semi = estimate_text_height(long_txt, 12, 3.23, "Poppins SemiBold")
    c.ok(h_semi >= h12, "SemiBold is wider so it never needs less height")
    c.ok(estimate_text_height("a\nb\nc", 12, 3.23, "Poppins")
         > estimate_text_height("a", 12, 3.23, "Poppins"),
         "explicit newlines are honoured")
    fits, needed = fits_in_box("Retail execution", 12, 3.23, 4.85, "Poppins")
    c.ok(fits is True and needed < 4.85, "short copy fits the text-visual body box")
    fits2, needed2 = fits_in_box(long_txt * 4, 12, 3.23, 1.0, "Poppins")
    c.ok(fits2 is False and needed2 > 1.0, "overflow is detected")

    # --- voice ---
    c.ok(is_sentence_case("How we build field teams that deliver") is True,
         "sentence case passes")
    c.ok(is_sentence_case("How We Build Field Teams That Deliver") is False,
         "title case is flagged")
    c.ok(is_sentence_case("SUBMIT") is False, "ALL CAPS is flagged")
    c.ok(is_sentence_case("KPI") is True, "a bare acronym is allowed")
    c.ok(is_sentence_case("Our KPI dashboard tracks store visits daily") is True,
         "an inline acronym does not trip title case")
    c.ok(is_sentence_case("We deploy in Delhi and Mumbai with Samsung teams") is True,
         "proper nouns do not trip title case")
    c.ok(is_sentence_case("Retail execution. Measured daily.") is True,
         "a word after a full stop may be capitalised")
    c.ok(is_sentence_case("") is True, "empty string is trivially sentence case")
    forb = brand["voice"]["forbiddenPhrases"]
    c.ok(find_forbidden_phrases("Please click here to continue", forb) == ["Click here"],
         "forbidden phrase match is case-insensitive")
    c.ok(find_forbidden_phrases("Chapter Name Goes Here and Lorem ipsum dolor", forb)
         == ["Lorem ipsum", "Chapter Name Goes Here"],
         "multiple forbidden phrases return in list order")
    c.ok(find_forbidden_phrases("Retail execution, measured", forb) == [],
         "clean copy reports nothing")
    c.ok(word_count("one two three") == 3, "word_count basic")
    c.ok(word_count("  spaced   out  copy  ") == 3, "word_count ignores extra whitespace")
    c.ok(word_count(None) == 0, "word_count handles None")
    c.ok(srt_timestamp(0) == "00:00:00,000", "srt_timestamp zero")
    c.ok(srt_timestamp(3661.5) == "01:01:01,500", "srt_timestamp hours")
    c.ok(srt_timestamp(-4) == "00:00:00,000", "srt_timestamp clamps negatives")
    c.ok(srt_timestamp(0.9999) == "00:00:01,000", "srt_timestamp rounds to the millisecond")

    # --- violations / report ---
    rep = Report("deck.pptx", "example", "deck")
    c.ok(rep.exit_code() == 0 and rep.to_json()["pass"] is True, "empty report passes")
    c.ok(rep.counts() == {"error": 0, "warn": 0, "info": 0}, "empty counts are complete")
    rep.add(Violation("TYPE.SYNTHETIC_BOLD", "warn", where="slide 4 / title",
                      found="bold=True", expected="bold=False",
                      rule="Synthetic bold is forbidden.",
                      fix="Use the Poppins SemiBold family."))
    c.ok(rep.exit_code() == 0 and rep.to_json()["pass"] is True, "warnings do not fail a deck")
    rep.add("COLOR.SUPERSEDED", severity="error", where="slide 2 / panel",
            found="#0000D5", expected="#0000FF",
            rule="Template palette is superseded.", fix="Recolour to brand blue.")
    rep.add(id="CONTENT.PLACEHOLDER", severity="info", where="slide 6",
            found="Person Name", expected="a real name")
    c.ok(rep.exit_code() == 2 and rep.to_json()["pass"] is False, "one error fails the deck")
    c.ok(rep.counts() == {"error": 1, "warn": 1, "info": 1}, "counts tally by severity")
    payload = rep.to_json()
    c.ok(sorted(payload.keys()) == ["brand", "counts", "kind", "pass", "target", "violations"],
         "to_json matches contract C keys")
    c.ok([v["severity"] for v in payload["violations"]] == ["error", "warn", "info"],
         "violations sort error -> warn -> info")
    c.ok(sorted(payload["violations"][0].keys())
         == ["expected", "fix", "found", "id", "rule", "severity", "where"],
         "violation dicts match contract C fields")
    c.ok(json.loads(json.dumps(payload))["target"] == "deck.pptx", "to_json is serialisable")
    human = rep.to_human()
    c.ok("FAIL" in human and "COLOR.SUPERSEDED" in human and "TYPE.SYNTHETIC_BOLD" in human,
         "to_human renders every violation")
    c.ok(human.index("ERROR (1)") < human.index("WARN (1)") < human.index("INFO (1)"),
         "to_human groups error -> warn -> info")
    c.ok("\x1b[" not in human, "to_human emits no ANSI colour")
    c.ok("No violations found." in Report().to_human(), "empty to_human says so")
    try:
        Violation("X.Y", "critical")
        c.ok(False, "Violation rejects an unknown severity")
    except ValueError:
        c.ok(True, "Violation rejects an unknown severity")

    if verbose:
        sys.stdout.write("brandlib self-test: %d passed\n" % c.passed)
    return c.passed


def main(argv=None):
    # type: (Optional[Sequence[str]]) -> int
    parser = argparse.ArgumentParser(
        prog="brandlib.py",
        description="Shared library for brand-studio. Running it directly executes "
                    "the built-in self-test suite.")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress the summary line; exit status still reports success")
    parser.add_argument("--brand", metavar="ID", default=None,
                        help="print the resolved palette index for a brand and exit")
    args = parser.parse_args(argv)

    if args.brand:
        res = resolve_brand(args.brand)
        if not res["brand"]:
            sys.stderr.write("no brand matched %r; candidates: %s\n"
                             % (args.brand, json.dumps(res["candidates"])))
            return 1
        b = load_brand(res["brand"])
        sys.stdout.write(json.dumps({
            "brand": b["id"],
            "match": res["match"],
            "palette": palette_index(b),
            "superseded": superseded_map(b),
        }, indent=2) + "\n")
        return 0

    try:
        _self_test(verbose=not args.quiet)
    except AssertionError as exc:
        sys.stderr.write("brandlib self-test: %s\n" % exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
