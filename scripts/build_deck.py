#!/usr/bin/env python3
"""build_deck.py -- render a Deck IR document into a brand-compliant .pptx.

The engine takes three inputs and owns none of them:

  * the Deck IR      -- what to say (authored by a skill, Contract A)
  * brands/<id>/brand.json  -- how the brand looks (colour, type, logo, voice)
  * grammar/deck-grammar.json -- where things sit (brand-agnostic geometry)

Every visual property is painted explicitly on every run and every shape.
Nothing is inherited from the Office theme that ships inside the default
python-pptx template, because that theme is not the brand.

Usage:
    build_deck.py --ir deck.json --out deck.pptx
    build_deck.py --ir deck.json --brand example --out deck.pptx --strict
    build_deck.py --list-archetypes

Exit codes:
    0  deck written
    1  build failed (bad IR, missing brand, capacity overflow under --strict)
"""

import argparse
import copy
import json
import math
import os
import re
import sys

from typing import Any, Dict, List, Optional, Sequence, Tuple  # noqa: F401

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_LIB = os.path.join(_HERE, "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import brandlib as bl  # noqa: E402

try:  # icon rasterisation is best-effort; a missing Chrome must not fail a build
    import render_icon as _render_icon
except Exception:  # pragma: no cover - defensive
    _render_icon = None

from pptx import Presentation  # noqa: E402
from pptx.chart.data import CategoryChartData  # noqa: E402
from pptx.dml.color import RGBColor  # noqa: E402
from pptx.enum.chart import (  # noqa: E402
    XL_CHART_TYPE,
    XL_LABEL_POSITION,
    XL_LEGEND_POSITION,
    XL_TICK_MARK,
)
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE  # noqa: E402
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN  # noqa: E402
from pptx.oxml import parse_xml  # noqa: E402
from pptx.oxml.ns import nsdecls, qn  # noqa: E402
from pptx.util import Emu, Pt  # noqa: E402

try:
    from PIL import Image as _PILImage
except Exception:  # pragma: no cover - pillow is a hard dep but degrade anyway
    _PILImage = None


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

BLANK_LAYOUT_INDEX = 6
ELLIPSIS = u"…"
EMU_PER_PT = 12700

#: python-pptx has no "no table style" default; this is Office's built-in
#: "No Style, No Grid" table style GUID.
TABLE_STYLE_NO_GRID = "{2D5ABB26-0587-4C30-8999-92F81FD0307C}"

#: brandlib's glyph advance is an average over the ASCII range and measures a
#: little narrow against real Poppins, which is harmless for wrapped copy but
#: fatal for a caller that asked for a hard line cap: one extra wrap in a 40pt
#: label is a broken slide. Line-capped fits are therefore measured with this
#: margin. It is deliberately applied nowhere else, so normal body copy keeps
#: the brand's own type sizes.
LINE_CAP_SAFETY = 1.12

CHART_TYPES = {
    "bar": XL_CHART_TYPE.BAR_CLUSTERED,
    "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
    "line": XL_CHART_TYPE.LINE_MARKERS,
    "pie": XL_CHART_TYPE.PIE,
    "doughnut": XL_CHART_TYPE.DOUGHNUT,
}

#: chart families whose colour is carried by a series fill rather than a line,
#: and whose points (not series) are coloured individually.
_POINT_COLOURED = ("pie", "doughnut")

ALIGN = {
    "left": PP_ALIGN.LEFT,
    "right": PP_ALIGN.RIGHT,
    "center": PP_ALIGN.CENTER,
    "centre": PP_ALIGN.CENTER,
    "justify": PP_ALIGN.JUSTIFY,
}

ANCHOR = {
    "top": MSO_ANCHOR.TOP,
    "middle": MSO_ANCHOR.MIDDLE,
    "center": MSO_ANCHOR.MIDDLE,
    "bottom": MSO_ANCHOR.BOTTOM,
}

_NUMERIC_CELL = re.compile(
    r"^\s*[₹$€£]?\s*[-+]?\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*"
    r"(?:%|x|X|k|K|m|M|bn|Bn|hrs?|hours?|days?|mins?|minutes?|secs?|pts?)?\s*$"
)

_BULLET_PREFIX = re.compile(r"^\s*(?:[-*•–—]|\d+[.)])\s+")


class BuildError(Exception):
    """Raised for any condition that must abort the build with a message."""


# ---------------------------------------------------------------------------
# small pure helpers
# ---------------------------------------------------------------------------

def _s(value, default=""):
    # type: (Any, str) -> str
    """Coerce an IR field to a stripped string."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    return text if text.strip() else default


def _rgb(hex_value):
    # type: (str) -> RGBColor
    return RGBColor.from_string(bl.normalize_hex(hex_value)[1:])


def _bare(hex_value):
    # type: (str) -> str
    return bl.normalize_hex(hex_value)[1:]


def _dig(node, path, default=None):
    # type: (Any, str, Any) -> Any
    """Dotted lookup into nested dicts. ``_dig(brand, 'color.brand.navy')``."""
    cur = node
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def _grid_layout(count, cols, rows):
    # type: (int, int, int) -> Tuple[int, int]
    """Pick a (cols, rows) that holds ``count`` cells, preferring the grammar's."""
    count = max(1, int(count))
    cols = max(1, int(cols or 1))
    rows = max(1, int(rows or 1))
    if count <= cols:
        return count, 1
    if count <= cols * rows:
        return cols, rows
    new_cols = int(math.ceil(float(count) / rows))
    return new_cols, rows


# ---------------------------------------------------------------------------
# builder
# ---------------------------------------------------------------------------

class DeckBuilder(object):
    """Renders one Deck IR document into one Presentation."""

    def __init__(self, ir, brand, grammar, ir_path=None, strict=False):
        # type: (Dict[str, Any], Dict[str, Any], Dict[str, Any], Optional[str], bool) -> None
        self.ir = ir
        self.brand = brand
        self.grammar = grammar
        self.ir_dir = os.path.dirname(os.path.abspath(ir_path)) if ir_path else os.getcwd()
        self.strict = bool(strict)
        self.warnings = []  # type: List[str]

        self.canvas = grammar.get("canvas") or {}
        self.grid = grammar.get("grid") or {}
        self.chrome_spec = grammar.get("chrome") or {}
        self.radius = grammar.get("radius") or {}
        self.spacing = grammar.get("spacingScale") or [0.042, 0.083, 0.125, 0.167,
                                                       0.25, 0.333, 0.5, 0.667, 0.833, 1.0]
        self.deck_rules = grammar.get("deckRules") or {}
        self.archetypes = {}  # type: Dict[str, Dict[str, Any]]
        for arche in grammar.get("archetypes") or []:
            if isinstance(arche, dict) and arche.get("id"):
                self.archetypes[str(arche["id"])] = arche

        self.min_pt = float(_dig(brand, "type.minBodyPt", 10.5))
        self.neg_track_above = float(_dig(brand, "type.negativeTrackingAbovePt", 0.0))
        self.overflow_tol = 1.0 + float(self.deck_rules.get("overflowTolerancePct", 0.0)) / 100.0
        self.accents = [h for h in (_dig(brand, "colorRules.chartSeries") or []) if bl.is_hex(h)]
        if not self.accents:
            self.accents = [self.color("brand.blue", "#0000FF")]

        self.prs = Presentation()
        self.prs.slide_width = bl.inches(float(self.canvas.get("w", 13.333)))
        self.prs.slide_height = bl.inches(float(self.canvas.get("h", 7.5)))
        self._blank = self.prs.slide_layouts[BLANK_LAYOUT_INDEX]
        self._icon_cache = {}  # type: Dict[Tuple[str, str], Optional[str]]
        #: True while rendering a slide whose optional side panel was drawn.
        self._side_panel = False

    # -- diagnostics --------------------------------------------------------

    def warn(self, message):
        # type: (str) -> None
        self.warnings.append(message)
        sys.stderr.write("build_deck: warning: %s\n" % message)

    # -- colour -------------------------------------------------------------

    def color(self, path, default=None):
        # type: (str, Optional[str]) -> str
        """A brand colour by dotted path under ``brand.color``, e.g. 'neutral.200'."""
        value = _dig(self.brand.get("color") or {}, path)
        if bl.is_hex(value):
            return bl.normalize_hex(value)
        if default is not None and bl.is_hex(default):
            return bl.normalize_hex(default)
        raise BuildError("brand '%s' has no colour at color.%s" % (self.brand.get("id"), path))

    @property
    def ink(self):
        # type: () -> str
        return bl.normalize_hex(_dig(self.brand, "colorRules.defaultText", "#0F0A6C"))

    @property
    def ink_secondary(self):
        # type: () -> str
        return bl.normalize_hex(_dig(self.brand, "colorRules.secondaryText", "#5E6678"))

    def text_on(self, background_hex):
        # type: (str) -> str
        """Mandated text colour for a surface, falling back to a luminance pick."""
        mandated = bl.on_surface_text(self.brand, background_hex)
        if mandated:
            return mandated
        return self.ink if bl.is_light(background_hex) else self.color("neutral.0", "#FFFFFF")

    def hero_gradient(self):
        # type: () -> Tuple[List[str], float]
        spec = _dig(self.brand, "color.gradient.blue") or {}
        stops = [bl.normalize_hex(h) for h in (spec.get("stops") or []) if bl.is_hex(h)]
        if len(stops) < 2:
            stops = [self.color("brand.navy", "#0F0A6C"), self.color("brand.blue", "#0000FF")]
        return stops, float(spec.get("angle", 135))

    def accent(self, index):
        # type: (int) -> str
        """A decorative accent: chart series fills and filled marks only."""
        return self.accents[int(index) % len(self.accents)]

    def accent_graphic(self, index, background=None):
        # type: (int, Optional[str]) -> str
        """An accent safe for a mark that must stay legible (icons, rules).

        Held to the brand's large-text contrast floor, so mint and teal step
        down their ramp rather than vanishing on a light surface.
        """
        floor = float(_dig(self.brand, "colorRules.largeTextPt", 18.0))
        return self.safe_text_color(self.accent(index),
                                    background or self.surface, floor)

    @property
    def surface(self):
        # type: () -> str
        """The slide background every chrome archetype sits on."""
        return self.color("neutral.0", "#FFFFFF")

    def _ramp_containing(self, hex_value):
        # type: (str) -> List[Tuple[int, str]]
        """The brand ramp a colour belongs to, as [(shade, hex), ...] ascending."""
        colors = self.brand.get("color") or {}
        token, _thex, _delta = bl.nearest_token(hex_value, bl.palette_index(self.brand))
        names = []
        if token:
            parts = str(token).split(".")
            names = [parts[-1], parts[0]]
        for name in names:
            ramp = colors.get(name)
            if not isinstance(ramp, dict):
                continue
            shades = []
            for key, value in ramp.items():
                if not bl.is_hex(value):
                    continue
                try:
                    shades.append((int(str(key)), bl.normalize_hex(value)))
                except ValueError:
                    continue
            if len(shades) >= 2:
                return sorted(shades)
        return []

    def safe_text_color(self, hex_value, background, size_pt):
        # type: (Optional[str], str, float) -> str
        """Nearest compliant ink for a colour: never forbidden, always legible.

        A colour that is already allowed and passes WCAG AA at this size is
        returned untouched. Otherwise the engine walks the colour's own brand
        ramp toward the background's opposite end and takes the first shade
        that clears both gates, falling back to the mandated on-surface ink.
        """
        if not hex_value or not bl.is_hex(hex_value):
            return self.text_on(background)
        target = bl.normalize_hex(hex_value)
        forbidden = bl.forbidden_text_colors(self.brand)
        allowed = target not in forbidden
        passes, _required, _actual = bl.passes_contrast(target, background, size_pt, False)
        if allowed and passes:
            return target

        ramp = self._ramp_containing(target)
        if ramp:
            anchor = min(range(len(ramp)),
                         key=lambda i: bl.delta_e(target, ramp[i][1]))
            order = (range(anchor + 1, len(ramp)) if bl.is_light(background)
                     else range(anchor - 1, -1, -1))
            for index in order:
                candidate = ramp[index][1]
                if candidate in forbidden:
                    continue
                good, _req, _act = bl.passes_contrast(candidate, background, size_pt, False)
                if good:
                    return candidate
        return self.text_on(background)

    # -- geometry -----------------------------------------------------------

    def radius_of(self, name, default=0.0):
        # type: (str, float) -> float
        try:
            return float(self.radius.get(name, default))
        except (TypeError, ValueError):
            return default

    def space(self, step, default=0.125):
        # type: (int, float) -> float
        try:
            return float(self.spacing[step])
        except (IndexError, TypeError, ValueError):
            return default

    @staticmethod
    def box(region):
        # type: (Dict[str, Any]) -> Tuple[float, float, float, float]
        return (float(region.get("x", 0.0)), float(region.get("y", 0.0)),
                float(region.get("w", 0.0)), float(region.get("h", 0.0)))

    # -- shape primitives ---------------------------------------------------

    @staticmethod
    def _strip_theme_style(shape):
        """Remove the <p:style> theme reference block python-pptx writes onto
        autoshapes and connectors.

        Every fill, line and font property is painted explicitly, so the block
        is dead weight -- but it points lnRef/fillRef/effectRef/fontRef at the
        Office theme's accent colours, which is precisely the inheritance this
        engine must not leave behind.
        """
        element = shape._element
        style = element.find(qn("p:style"))
        if style is not None:
            element.remove(style)

    def _add_shape(self, slide, shape_enum, x, y, w, h):
        shape = slide.shapes.add_shape(shape_enum, bl.inches(x), bl.inches(y),
                                       bl.inches(max(w, 0.0)), bl.inches(max(h, 0.0)))
        self._strip_theme_style(shape)
        shape.shadow.inherit = False
        shape.line.fill.background()
        tf = shape.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        return shape

    def add_rect(self, slide, x, y, w, h, fill_hex=None, radius_in=0.0,
                 line_hex=None, line_pt=1.0, gradient=None, gradient_angle=135.0):
        """A rectangle or rounded rectangle painted explicitly (never themed)."""
        if radius_in and radius_in > 0:
            shape = self._add_shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
            self._set_corner_radius(shape, radius_in, w, h)
        else:
            shape = self._add_shape(slide, MSO_SHAPE.RECTANGLE, x, y, w, h)
        if gradient:
            self.apply_gradient(shape, gradient, gradient_angle)
        elif fill_hex:
            shape.fill.solid()
            shape.fill.fore_color.rgb = _rgb(fill_hex)
        else:
            shape.fill.background()
        if line_hex:
            shape.line.color.rgb = _rgb(line_hex)
            shape.line.width = Pt(line_pt)
        return shape

    def add_circle(self, slide, x, y, size, fill_hex=None, line_hex=None, line_pt=1.0):
        shape = self._add_shape(slide, MSO_SHAPE.OVAL, x, y, size, size)
        if fill_hex:
            shape.fill.solid()
            shape.fill.fore_color.rgb = _rgb(fill_hex)
        else:
            shape.fill.background()
        if line_hex:
            shape.line.color.rgb = _rgb(line_hex)
            shape.line.width = Pt(line_pt)
        return shape

    def add_line(self, slide, x, y, w, h, color_hex, width_pt=1.0):
        """A straight rule. ``h == 0`` draws horizontal, ``w == 0`` vertical."""
        shape = slide.shapes.add_connector(
            MSO_CONNECTOR.STRAIGHT,
            bl.inches(x), bl.inches(y),
            bl.inches(x + float(w or 0.0)), bl.inches(y + float(h or 0.0)))
        self._strip_theme_style(shape)
        shape.line.color.rgb = _rgb(color_hex)
        shape.line.width = Pt(width_pt)
        shape.shadow.inherit = False
        return shape

    @staticmethod
    def _set_corner_radius(shape, radius_in, w_in, h_in):
        """Set the roundRect adj so the corner radius equals ``radius_in``.

        DrawingML expresses the roundRect adjustment as a fraction of the
        shorter side, so the adjustment is radius / min(w, h), clamped to 0.5.
        """
        short_side = min(float(w_in), float(h_in))
        if short_side <= 0:
            return
        adj = float(radius_in) / short_side
        adj = max(0.0, min(0.5, adj))
        try:
            shape.adjustments[0] = adj
        except (IndexError, ValueError):  # pragma: no cover - geometry without adj
            pass

    def apply_gradient(self, shape, stops, angle_deg=135.0):
        """Paint a linear gradient. ``stops`` is [hex, ...] or [(hex, alpha_pct), ...].

        python-pptx only knows how to emit the theme's two-stop accent gradient,
        so the gsLst is rewritten with explicit sRGB stops and ``a:lin/@ang`` is
        written directly (DrawingML angles are 60000ths of a degree).
        """
        normalised = []
        for stop in stops:
            if isinstance(stop, (tuple, list)):
                normalised.append((bl.normalize_hex(stop[0]),
                                   float(stop[1]) if len(stop) > 1 else 100.0))
            else:
                normalised.append((bl.normalize_hex(stop), 100.0))
        if len(normalised) == 1:
            normalised = normalised * 2

        fill = shape.fill
        fill.gradient()
        xpr = fill._xPr
        grad = xpr.find(qn("a:gradFill"))
        if grad is None:  # pragma: no cover - defensive
            raise BuildError("could not create a gradient fill on %r" % shape.shape_type)
        grad.set("rotWithShape", "1")

        gs_lst = grad.find(qn("a:gsLst"))
        if gs_lst is None:  # pragma: no cover - defensive
            gs_lst = parse_xml("<a:gsLst %s/>" % nsdecls("a"))
            grad.insert(0, gs_lst)
        for child in list(gs_lst):
            gs_lst.remove(child)

        last = len(normalised) - 1
        for index, (hex_value, alpha_pct) in enumerate(normalised):
            pos = int(round(100000.0 * index / last)) if last else 0
            alpha = ""
            if alpha_pct < 100.0:
                alpha = '<a:alpha val="%d"/>' % int(round(max(0.0, alpha_pct) * 1000))
            gs_lst.append(parse_xml(
                '<a:gs %s pos="%d"><a:srgbClr val="%s">%s</a:srgbClr></a:gs>'
                % (nsdecls("a"), pos, _bare(hex_value), alpha)))

        for tag in ("a:path", "a:tileRect"):
            node = grad.find(qn(tag))
            if node is not None:
                grad.remove(node)
        lin = grad.find(qn("a:lin"))
        if lin is None:
            lin = parse_xml("<a:lin %s/>" % nsdecls("a"))
            grad.append(lin)
        lin.set("ang", str(int(round(float(angle_deg) % 360.0 * 60000))))
        lin.set("scaled", "0")
        return shape

    # -- images -------------------------------------------------------------

    def resolve_asset(self, src):
        # type: (Optional[str]) -> Optional[str]
        """Find an asset by absolute path, or relative to the IR / brand / plugin."""
        if not src:
            return None
        candidate = os.path.expanduser(str(src))
        roots = [""] if os.path.isabs(candidate) else [
            self.ir_dir,
            self.brand.get("_dir") or "",
            bl.plugin_root(),
            os.getcwd(),
        ]
        for root in roots:
            path = candidate if not root else os.path.join(root, candidate)
            if os.path.isfile(path):
                return os.path.abspath(path)
        return None

    @staticmethod
    def _image_size(path):
        # type: (str) -> Optional[Tuple[int, int]]
        if _PILImage is None:
            return None
        try:
            with _PILImage.open(path) as img:
                return img.size
        except Exception:
            return None

    def add_picture_cover(self, slide, path, x, y, w, h):
        """Place a picture filling the box, centre-cropped to preserve aspect."""
        pic = slide.shapes.add_picture(path, bl.inches(x), bl.inches(y),
                                       bl.inches(w), bl.inches(h))
        size = self._image_size(path)
        if not size or not size[0] or not size[1] or w <= 0 or h <= 0:
            return pic
        img_ratio = float(size[0]) / float(size[1])
        box_ratio = float(w) / float(h)
        if abs(img_ratio - box_ratio) < 1e-6:
            return pic
        if img_ratio > box_ratio:
            crop = (1.0 - box_ratio / img_ratio) / 2.0
            pic.crop_left = crop
            pic.crop_right = crop
        else:
            crop = (1.0 - img_ratio / box_ratio) / 2.0
            pic.crop_top = crop
            pic.crop_bottom = crop
        return pic

    def add_picture_fit(self, slide, path, x, y, w, h):
        """Place a picture inside the box without cropping, centred."""
        size = self._image_size(path)
        draw_w, draw_h = float(w), float(h)
        if size and size[0] and size[1]:
            img_ratio = float(size[0]) / float(size[1])
            if img_ratio > (float(w) / float(h) if h else img_ratio):
                draw_h = float(w) / img_ratio
            else:
                draw_w = float(h) * img_ratio
        off_x = x + (float(w) - draw_w) / 2.0
        off_y = y + (float(h) - draw_h) / 2.0
        return slide.shapes.add_picture(path, bl.inches(off_x), bl.inches(off_y),
                                        bl.inches(draw_w), bl.inches(draw_h))

    def logo_variant_for(self, region):
        # type: (Dict[str, Any]) -> str
        mapping = _dig(self.brand, "logo.variantForBackground") or {}
        if region.get("onPhoto"):
            key = "photo"
        elif region.get("onDark"):
            key = "dark"
        else:
            key = "light"
        return str(mapping.get(key) or mapping.get("light") or "primary")

    def logo_path(self, variant):
        # type: (str) -> Optional[str]
        variants = _dig(self.brand, "logo.variants") or {}
        spec = variants.get(variant) or variants.get("primary") or {}
        return self.resolve_asset(spec.get("file"))

    def place_logo(self, slide, region):
        """Draw the brand logo into a grammar logo region, aspect preserved."""
        variant = self.logo_variant_for(region)
        path = self.logo_path(variant)
        x, y, w, h = self.box(region)
        min_w = float(_dig(self.brand, "logo.minWidthIn", 0.0) or 0.0)
        if w + 1e-6 < min_w:
            self.warn("logo region is %.3fin wide, below the brand minimum of %.3fin"
                      % (w, min_w))
        if not path:
            self.warn("logo variant '%s' not found on disk; drawing a wordmark fallback"
                      % variant)
            dark = region.get("onDark") or region.get("onPhoto")
            colour = self.color("neutral.0", "#FFFFFF") if dark else self.ink
            backdrop = self.color("neutral.900", "#12141C") if dark else self.surface
            return self.add_text(slide, {"x": x, "y": y, "w": w, "h": h},
                                 str(self.brand.get("name") or self.brand.get("id") or ""),
                                 "cardTitle", color=colour, anchor="middle",
                                 background=backdrop, where="logo fallback")
        return self.add_picture_fit(slide, path, x, y, w, h)

    # -- icons --------------------------------------------------------------

    def icon_png(self, name, color_hex):
        # type: (Optional[str], str) -> Optional[str]
        """Rasterise a grammar icon, or return None when it cannot be produced."""
        if not name or _render_icon is None:
            return None
        key = (str(name), bl.normalize_hex(color_hex))
        if key in self._icon_cache:
            return self._icon_cache[key]
        path = None
        try:
            path = _render_icon.render(str(name), bl.normalize_hex(color_hex), 256)
        except Exception as exc:
            self.warn("icon '%s' not rendered (%s); falling back to the badge only"
                      % (name, exc))
            path = None
        self._icon_cache[key] = path
        return path

    def place_icon(self, slide, name, x, y, size, tint_hex=None, halo=None):
        """Draw the icon: halo plus tinted glyph, or a deliberate accent mark.

        An empty halo reads as a missing asset, so when no glyph can be produced
        the halo is skipped in favour of a solid accent dot the same optical
        weight as the glyph would have been.
        """
        tint = tint_hex or self.color("brand.blue", "#0000FF")
        path = self.icon_png(name, tint)
        if path:
            if halo:
                halo_size = float(halo.get("w", size) or size)
                halo_fill = halo.get("fill") or self.color("sky.100", "#EBF6F9")
                self.add_circle(slide,
                                x - (halo_size - size) / 2.0,
                                y - (halo_size - size) / 2.0,
                                halo_size, fill_hex=halo_fill)
            return self.add_picture_fit(slide, path, x, y, size, size)
        if halo:
            dot = float(size) * 0.5
            return self.add_circle(slide, x + (size - dot) / 2.0, y + (size - dot) / 2.0,
                                   dot, fill_hex=tint)
        bar_h = self.radius_of("sm", 0.042)
        return self.add_rect(slide, x, y + size / 2.0 - bar_h / 2.0, size, bar_h,
                             fill_hex=tint, radius_in=bar_h / 2.0)

    # -- text ---------------------------------------------------------------

    @staticmethod
    def _set_family(run, family):
        """Set the latin and complex-script typeface so nothing inherits."""
        run.font.name = family
        rpr = run.font._element
        latin = rpr.find(qn("a:latin"))
        if latin is None:  # pragma: no cover - font.name always creates it
            return
        cs = rpr.find(qn("a:cs"))
        if cs is None:
            cs = parse_xml('<a:cs %s typeface="%s"/>'
                           % (nsdecls("a"), family.replace("&", "&amp;").replace('"', "&quot;")))
            latin.addnext(cs)
        else:
            cs.set("typeface", family)

    @staticmethod
    def _set_tracking(run, tracking_em, size_pt):
        """Letter spacing. python-pptx has no API, so write a:rPr/@spc directly."""
        if not tracking_em:
            return
        spc = int(round(float(tracking_em) * float(size_pt) * 100.0))
        if spc == 0:
            return
        run.font._element.set("spc", str(spc))

    def paint_run(self, run, family, size_pt, color_hex, tracking_em=0.0):
        """Every property, every time. Bold is always False (no synthetic bold)."""
        self._set_family(run, family)
        run.font.size = Pt(float(size_pt))
        run.font.bold = False
        run.font.italic = False
        run.font.underline = False
        run.font.color.rgb = _rgb(color_hex)
        self._set_tracking(run, tracking_em, size_pt)

    def _effective_tracking(self, spec, size_pt):
        # type: (Dict[str, Any], float) -> float
        tracking = float(spec.get("tracking", 0.0) or 0.0)
        if tracking < 0 and size_pt <= self.neg_track_above:
            return 0.0
        return tracking

    def _height_limit(self, h_in, size, max_lines, leading_ratio=None):
        # type: (float, float, Optional[int], Optional[float]) -> float
        """Usable height at a given size: the box plus tolerance, line-capped.

        The line cap must use the SAME leading the text is measured and painted
        with. Capping at the default 1.35 factor while measuring at the role's
        real leading makes a single line look like an overflow -- eyebrow is
        10.5/15 (1.43), so one line needs 0.208in against a 0.197in cap, and the
        block shrinks to nothing and gets truncated away.
        """
        ratio = float(leading_ratio) if leading_ratio else bl.LEADING_FACTOR
        limit = float(h_in) * self.overflow_tol
        if max_lines:
            limit = min(limit, int(max_lines) * size * ratio / 72.0 + 1e-6)
        return limit

    @staticmethod
    def _measure_width(w_in, tracking, max_lines):
        # type: (float, float, Optional[int]) -> float
        """Width to measure against: tracking widens text, a line cap needs margin."""
        width = float(w_in) / (1.0 + max(0.0, tracking))
        if max_lines:
            width /= LINE_CAP_SAFETY
        return max(width, 0.01)

    def _shrink_to_fit(self, text, base_size, family, w_in, h_in, tracking,
                       max_lines=None, leading_ratio=None):
        # type: (str, float, str, float, float, float, Optional[int], Optional[float]) -> Tuple[float, bool]
        """Step down in 0.5pt increments to the brand's minimum body size.

        ``leading_ratio`` is leading/size for the role being set. It must be
        supplied whenever the caller will set line spacing explicitly, because
        several roles are looser than brandlib's default factor (subtitle is
        18/26 = 1.44 against a 1.35 default). Measuring at 1.35 while painting at
        1.44 under-estimates by 7% and the block overflows -- which the validator
        then catches, since it reads the real line spacing back out of the XML.
        """
        size = float(base_size)
        measure_w = self._measure_width(w_in, tracking, max_lines)
        while True:
            limit = self._height_limit(h_in, size, max_lines, leading_ratio)
            leading = (size * leading_ratio) if leading_ratio else None
            fits, _needed = bl.fits_in_box(text, size, measure_w, limit, family, leading)
            if fits:
                return size, True
            nxt = round(size - 0.5, 2)
            if nxt < self.min_pt - 1e-9:
                return size, False
            size = nxt

    def _truncate_to_fit(self, text, size, family, w_in, h_in, tracking,
                         max_lines=None, leading_ratio=None):
        # type: (str, float, str, float, float, float, Optional[int], Optional[float]) -> str
        """Longest prefix that fits, with a single ellipsis character appended."""
        limit = self._height_limit(h_in, size, max_lines, leading_ratio)
        measure_w = self._measure_width(w_in, tracking, max_lines)
        leading = (float(size) * leading_ratio) if leading_ratio else None
        low, high = 0, len(text)
        best = ""
        while low <= high:
            mid = (low + high) // 2
            candidate = text[:mid].rstrip() + ELLIPSIS
            fits, _needed = bl.fits_in_box(candidate, size, measure_w, limit, family, leading)
            if fits:
                best = candidate
                low = mid + 1
            else:
                high = mid - 1
        return best or ELLIPSIS

    def add_text(self, slide, region, text, role, color=None, align=None,
                 anchor="top", uppercase=False, bullets=False, where="",
                 size_cap=None, box_override=None, background=None, max_lines=None):
        """Render a text block into a region, shrinking or truncating on overflow.

        ``background`` is the surface the text sits on; it defaults to the slide
        surface and is used to hold every run to the brand's contrast and
        forbidden-ink rules at the size the run actually ends up at.

        Returns the created shape, or None when there was nothing to render.
        """
        content = _s(text)
        if not content:
            return None
        if box_override is not None:
            x, y, w, h = box_override
        else:
            x, y, w, h = self.box(region or {})
        if w <= 0 or h <= 0:
            self.warn("skipping %s: zero-sized region" % (where or role))
            return None

        spec = bl.type_role(self.brand, role)
        family = bl.weight_to_family(self.brand, spec.get("weight", 400))
        base_size = float(spec.get("size", self.min_pt))
        if size_cap:
            base_size = min(base_size, float(size_cap))
        if uppercase or str(spec.get("case", "")).lower() == "upper":
            content = content.upper()

        paragraphs = self._split_paragraphs(content, bullets)
        measured = "\n".join(p[0] for p in paragraphs)
        tracking = self._effective_tracking(spec, base_size)

        # Measure with the leading this role will actually be painted with, not
        # brandlib's default factor -- see _shrink_to_fit.
        _declared_leading = float(spec.get("leading", 0) or 0)
        leading_ratio = (_declared_leading / base_size) if (_declared_leading and base_size) else None

        size, fitted = self._shrink_to_fit(measured, base_size, family, w, h,
                                           tracking, max_lines, leading_ratio)
        tracking = self._effective_tracking(spec, size)
        if not fitted:
            label = where or "%s in %s" % (role, region.get("role", "region") if region else role)
            message = ("%s overflows its %.2f x %.2fin box even at the %.1fpt minimum"
                       % (label, w, h, self.min_pt))
            if self.strict:
                raise BuildError(message + " (--strict)")
            self.warn(message + "; truncating")
            measured = self._truncate_to_fit(measured, size, family, w, h, tracking,
                                             max_lines, leading_ratio)
            paragraphs = self._split_paragraphs(measured, bullets)

        shape = slide.shapes.add_textbox(bl.inches(x), bl.inches(y),
                                         bl.inches(w), bl.inches(h))
        shape.shadow.inherit = False
        frame = shape.text_frame
        frame.word_wrap = True
        frame.auto_size = MSO_AUTO_SIZE.NONE
        frame.margin_left = frame.margin_right = 0
        frame.margin_top = frame.margin_bottom = 0
        frame.vertical_anchor = ANCHOR.get(str(anchor).lower(), MSO_ANCHOR.TOP)

        base_leading = float(spec.get("leading", base_size * bl.LEADING_FACTOR) or
                             base_size * bl.LEADING_FACTOR)
        leading = base_leading * (size / base_size) if base_size else base_leading
        ink = self.safe_text_color(color or self.ink, background or self.surface, size)
        alignment = ALIGN.get(str(align).lower(), None) if align else None

        for index, (line, is_bullet) in enumerate(paragraphs):
            para = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            para.line_spacing = Pt(leading)
            para.space_before = Pt(0)
            para.space_after = Pt(0)
            if alignment is not None:
                para.alignment = alignment
            if is_bullet:
                self._apply_bullet(para, size, ink)
            if not line:
                # Preserve the blank line so the rendered height matches the estimate.
                run = para.add_run()
                run.text = " "
                self.paint_run(run, family, size, ink, tracking)
                continue
            run = para.add_run()
            run.text = line
            self.paint_run(run, family, size, ink, tracking)
        return shape

    @staticmethod
    def _split_paragraphs(text, bullets):
        # type: (str, bool) -> List[Tuple[str, bool]]
        out = []
        for raw in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            if bullets:
                match = _BULLET_PREFIX.match(raw)
                if match:
                    out.append((raw[match.end():].strip(), True))
                    continue
            out.append((raw.strip(), False))
        return out or [("", False)]

    def _apply_bullet(self, paragraph, size_pt, color_hex):
        """Hanging bullet written as buChar/buClr, since python-pptx has no API."""
        ppr = paragraph._pPr if paragraph._pPr is not None else paragraph._p.get_or_add_pPr()
        indent = int(round(float(size_pt) * 1.15 * EMU_PER_PT))
        ppr.set("marL", str(indent))
        ppr.set("indent", str(-indent))
        for tag in ("a:buNone", "a:buChar", "a:buAutoNum", "a:buClr", "a:buSzPct", "a:buFont"):
            node = ppr.find(qn(tag))
            if node is not None:
                ppr.remove(node)
        ppr.append(parse_xml('<a:buClr %s><a:srgbClr val="%s"/></a:buClr>'
                             % (nsdecls("a"), _bare(color_hex))))
        ppr.append(parse_xml('<a:buFont %s typeface="Arial"/>' % nsdecls("a")))
        ppr.append(parse_xml('<a:buChar %s char="•"/>' % nsdecls("a")))

    # -- chrome -------------------------------------------------------------

    def render_chrome(self, slide, sd, page_no, left_override=None, arche=None):
        """Header rule, logo, eyebrow, title, deck line, footer band, page number."""
        spec = self.chrome_spec
        shift = 0.0
        if left_override is not None:
            shift = float(left_override) - float(_dig(spec, "logo.x", 0.0))

        rule = spec.get("headerRule")
        if rule:
            x, y, w, h = self.box(rule)
            self.add_line(slide, x, y, w, h, self.color("neutral.200", "#DCE0EA"), 1.0)

        logo_region = spec.get("logo")
        if logo_region:
            region = dict(logo_region)
            region["x"] = float(region.get("x", 0.0)) + shift
            self.place_logo(slide, region)

        eyebrow = _s(sd.get("eyebrow")) or self.default_eyebrow(sd)
        eyebrow_region = spec.get("eyebrow")
        if eyebrow and eyebrow_region:
            self.add_text(slide, eyebrow_region, eyebrow, "eyebrow",
                          color=self.ink_secondary,
                          align=eyebrow_region.get("align", "right"),
                          anchor="middle", uppercase=True, max_lines=1,
                          where="slide %d eyebrow" % page_no)

        title_region = spec.get("title")
        title = _s(sd.get("title"))
        if title and title_region:
            region = dict(title_region)
            region["x"] = float(region.get("x", 0.0)) + shift
            region["w"] = max(float(region.get("w", 0.0)) - shift, 1.0)
            max_chars = int(self.deck_rules.get("maxTitleChars", 0) or 0)
            if max_chars and len(title) > max_chars:
                self.warn("slide %d title is %d characters, over the %d character rule"
                          % (page_no, len(title), max_chars))
            self.add_text(slide, region, title, "title", color=self.ink,
                          anchor="top", where="slide %d title" % page_no)

        deck_line = _s(sd.get("deck"))
        deck_region = spec.get("deck")

        # A slide gets ONE subtitle slot. Several archetypes own an "intro"
        # region that sits in the same band as the chrome deck line (icon-rows,
        # columns, stats and table all start around y=1.85 against the deck
        # line's y=1.78), so painting both stacks two paragraphs on top of each
        # other. Where the archetype owns the slot, the chrome yields it.
        arche_regions = (arche or {}).get("regions") or {}
        if deck_line and "intro" in arche_regions:
            if _s(sd.get("intro")):
                self.warn("slide %d supplies both 'deck' and 'intro'; the %s archetype owns "
                          "that slot, so 'deck' was dropped -- put the line in 'intro'"
                          % (page_no, (arche or {}).get("id", "?")))
                deck_line = ""
            else:
                # Nothing in intro: reuse the archetype's own region for the line
                # so the copy is not silently lost.
                deck_region = arche_regions.get("intro")

        if deck_line and deck_region:
            region = dict(deck_region)
            region["x"] = float(region.get("x", 0.0)) + shift
            region["w"] = max(float(region.get("w", 0.0)) - shift, 1.0)
            # The grammar lets the deck line start inside the title box. Seat it
            # below the title instead so the two boxes never overlap.
            if title and title_region:
                title_bottom = (float(title_region.get("y", 0.0))
                                + float(title_region.get("h", 0.0)))
                if float(region["y"]) < title_bottom:
                    region["h"] = max(float(region.get("h", 0.0))
                                      - (title_bottom - float(region["y"])), 0.3)
                    region["y"] = title_bottom
            self.add_text(slide, region, deck_line, "subtitle", color=self.ink_secondary,
                          anchor="top", where="slide %d deck line" % page_no)

        band = spec.get("footerBand")
        if band:
            x, y, w, h = self.box(band)
            stops, angle = self.hero_gradient()
            self.add_rect(slide, x, y, w, h, gradient=stops, gradient_angle=angle)

        page_region = spec.get("pageNumber")
        if page_region:
            self.add_text(slide, page_region, str(page_no), "caption",
                          color=self.ink_secondary,
                          align=page_region.get("align", "right"), anchor="middle",
                          where="slide %d page number" % page_no)

    def default_eyebrow(self, sd):
        # type: (Dict[str, Any]) -> str
        """Satisfy requireEyebrowOnContent when the author left the eyebrow empty."""
        if not self.deck_rules.get("requireEyebrowOnContent"):
            return ""
        meta = self.ir.get("meta") or {}
        fallback = _s(meta.get("client")) or _s(meta.get("title"))
        if fallback:
            self.warn("slide '%s' has no eyebrow; falling back to '%s'"
                      % (_s(sd.get("title"), sd.get("archetype", "?")), fallback))
        return fallback

    # -- region preparation -------------------------------------------------

    def regions_for(self, arche, sd):
        # type: (Dict[str, Any], Dict[str, Any]) -> Dict[str, Dict[str, Any]]
        """Grammar regions for a slide, shifted down when a deck line is present."""
        regions = copy.deepcopy(arche.get("regions") or {})
        if not arche.get("chrome") or not _s(sd.get("deck")):
            return regions
        top = float(self.chrome_spec.get("contentTopNoDeck",
                                         self.chrome_spec.get("contentTop", 2.1)))
        with_deck = float(self.chrome_spec.get("contentTopWithDeck", top))
        bottom = float(self.chrome_spec.get("contentBottom", 6.95))
        delta = with_deck - top
        if delta <= 0:
            return regions
        for region in regions.values():
            if not isinstance(region, dict):
                continue
            if float(region.get("y", 0.0)) + 1e-6 < top:
                continue
            region["y"] = float(region["y"]) + delta
            if "h" in region and float(region["h"]) > 0:
                overflow = (region["y"] + float(region["h"])) - bottom
                if overflow > 0:
                    region["h"] = max(float(region["h"]) - overflow, 0.2)
        return regions

    def content_top(self, sd):
        # type: (Dict[str, Any]) -> float
        """Where a slide's content region actually starts, deck line or not."""
        top = float(self.chrome_spec.get("contentTopNoDeck",
                                         self.chrome_spec.get("contentTop", 2.1)))
        if _s(sd.get("deck")):
            return float(self.chrome_spec.get("contentTopWithDeck", top))
        return top

    def widen_to_content(self, region):
        # type: (Dict[str, Any]) -> float
        """Extra width available if a region stretches to the content right edge."""
        right = float(self.grid.get("contentRight", self.canvas.get("w", 13.333)))
        return max(0.0, right - (float(region.get("x", 0.0)) + float(region.get("w", 0.0))))

    # -- visuals ------------------------------------------------------------

    def render_visual(self, slide, region, visual, has_slide_title=True, where=""):
        """Dispatch a visual region to an image, a native chart or a quiet panel."""
        x, y, w, h = self.box(region)
        if not isinstance(visual, dict) or not visual:
            return self.render_placeholder(slide, x, y, w, h)
        kind = str(visual.get("kind") or "placeholder").lower()
        if kind == "image":
            path = self.resolve_asset(visual.get("src"))
            if not path:
                self.warn("%s: image '%s' not found; drawing a placeholder panel"
                          % (where or "visual", visual.get("src")))
                return self.render_placeholder(slide, x, y, w, h)
            return self.add_picture_cover(slide, path, x, y, w, h)
        if kind == "chart":
            chart_spec = visual.get("chart")
            if not isinstance(chart_spec, dict):
                self.warn("%s: chart visual has no chart spec" % (where or "visual"))
                return self.render_placeholder(slide, x, y, w, h)
            return self.render_chart(slide, x, y, w, h, chart_spec, has_slide_title, where)
        return self.render_placeholder(slide, x, y, w, h)

    def render_placeholder(self, slide, x, y, w, h):
        """A quiet tinted panel. Deliberately carries no text: placeholder copy
        such as 'Place Table / Image / Chart' is a brand voice violation."""
        return self.add_rect(slide, x, y, w, h,
                             fill_hex=self.color("sky.100", "#EBF6F9"),
                             radius_in=self.radius_of("lg", 0.125))

    # -- charts -------------------------------------------------------------

    def render_chart(self, slide, x, y, w, h, spec, has_slide_title=True, where=""):
        kind = str(spec.get("type") or "column").lower()
        if kind not in CHART_TYPES:
            raise BuildError("%s: unknown chart type '%s'. Known: %s"
                             % (where or "chart", kind, ", ".join(sorted(CHART_TYPES))))
        categories = [_s(c) for c in (spec.get("categories") or [])]
        series = [s for s in (spec.get("series") or []) if isinstance(s, dict)]
        if not categories:
            raise BuildError("%s: chart has no categories" % (where or "chart"))
        if not series:
            raise BuildError("%s: chart has no series" % (where or "chart"))

        data = CategoryChartData()
        data.categories = categories
        for entry in series:
            values = []
            for raw in (entry.get("values") or []):
                try:
                    values.append(float(raw))
                except (TypeError, ValueError):
                    values.append(None)
            while len(values) < len(categories):
                values.append(None)
            data.add_series(_s(entry.get("name"), "Series"), tuple(values[:len(categories)]))

        frame = slide.shapes.add_chart(CHART_TYPES[kind], bl.inches(x), bl.inches(y),
                                       bl.inches(w), bl.inches(h), data)
        chart = frame.chart

        caption = bl.type_role(self.brand, "caption")
        caption_family = bl.weight_to_family(self.brand, caption.get("weight", 400))
        caption_size = float(caption.get("size", self.min_pt))
        label_ink = self.color("neutral.500", "#5E6678")

        chart.font.name = caption_family
        chart.font.size = Pt(caption_size)
        chart.font.bold = False
        chart.font.italic = False
        chart.font.color.rgb = _rgb(label_ink)

        self._strip_chart_frame(chart)

        # The slide already carries the title; a chart title would duplicate it.
        chart.has_title = bool(not has_slide_title and False)

        self._style_axes(chart, kind, caption_family, caption_size, label_ink)

        plot = chart.plots[0]
        if kind in ("column", "bar"):
            plot.gap_width = 60
            plot.overlap = -10
        if kind in _POINT_COLOURED:
            try:
                plot.vary_by_categories = True
            except Exception:
                pass

        # Data labels first: point colouring then repaints the labels that sit
        # on top of a slice so each one keeps its mandated on-surface ink.
        if kind in ("column", "bar") or kind in _POINT_COLOURED:
            self._add_data_labels(plot, kind, caption_family, caption_size, label_ink)
        self._colour_series(chart, kind, caption_family, caption_size)

        multi = len(series) > 1 or kind in _POINT_COLOURED
        chart.has_legend = bool(multi)
        if chart.has_legend:
            chart.legend.position = XL_LEGEND_POSITION.BOTTOM
            chart.legend.include_in_layout = False
            chart.legend.font.name = caption_family
            chart.legend.font.size = Pt(caption_size)
            chart.legend.font.bold = False
            chart.legend.font.color.rgb = _rgb(label_ink)
        return frame

    def _strip_chart_frame(self, chart):
        """No chart-area fill and no border: the slide surface shows through."""
        chart_space = chart._chartSpace
        sp_pr = chart_space.find(qn("c:spPr"))
        if sp_pr is None:
            sp_pr = parse_xml(
                "<c:spPr %s><a:noFill/><a:ln><a:noFill/></a:ln></c:spPr>"
                % nsdecls("c", "a"))
            anchor = chart_space.find(qn("c:chart"))
            if anchor is not None:
                anchor.addnext(sp_pr)
            else:  # pragma: no cover - c:chart is always present
                chart_space.append(sp_pr)

    def _colour_series(self, chart, kind, label_family=None, label_size=None):
        for index, series in enumerate(chart.plots[0].series):
            colour = self.accent(index)
            if kind in _POINT_COLOURED:
                for point_index, point in enumerate(series.points):
                    slice_hex = self.accent(point_index)
                    point.format.fill.solid()
                    point.format.fill.fore_color.rgb = _rgb(slice_hex)
                    point.format.line.color.rgb = _rgb(self.color("neutral.0", "#FFFFFF"))
                    point.format.line.width = Pt(1.0)
                    if label_family:
                        # The label sits on the slice, so it takes the slice's
                        # mandated on-surface ink rather than the axis grey.
                        font = point.data_label.font
                        font.name = label_family
                        font.size = Pt(label_size)
                        font.bold = False
                        font.italic = False
                        font.color.rgb = _rgb(self.text_on(slice_hex))
                continue
            if kind == "line":
                series.format.line.color.rgb = _rgb(colour)
                series.format.line.width = Pt(2.25)
                series.format.fill.background()
                try:
                    series.smooth = False
                except Exception:
                    pass
                continue
            series.format.fill.solid()
            series.format.fill.fore_color.rgb = _rgb(colour)
            series.format.line.fill.background()

    def _style_axes(self, chart, kind, family, size_pt, ink):
        if kind in _POINT_COLOURED:
            return
        grid_ink = self.color("neutral.200", "#DCE0EA")
        try:
            value_axis = chart.value_axis
        except Exception:  # pragma: no cover
            value_axis = None
        if value_axis is not None:
            value_axis.has_major_gridlines = True
            value_axis.major_gridlines.format.line.color.rgb = _rgb(grid_ink)
            value_axis.major_gridlines.format.line.width = Pt(0.75)
            value_axis.has_minor_gridlines = False
            value_axis.format.line.fill.background()
            value_axis.major_tick_mark = XL_TICK_MARK.NONE
            value_axis.minor_tick_mark = XL_TICK_MARK.NONE
            value_axis.has_title = False
            self._paint_tick_labels(value_axis, family, size_pt, ink)
        try:
            category_axis = chart.category_axis
        except Exception:  # pragma: no cover
            category_axis = None
        if category_axis is not None:
            category_axis.has_major_gridlines = False
            category_axis.has_minor_gridlines = False
            category_axis.format.line.color.rgb = _rgb(grid_ink)
            category_axis.format.line.width = Pt(0.75)
            category_axis.major_tick_mark = XL_TICK_MARK.NONE
            category_axis.minor_tick_mark = XL_TICK_MARK.NONE
            category_axis.has_title = False
            self._paint_tick_labels(category_axis, family, size_pt, ink)

    @staticmethod
    def _paint_tick_labels(axis, family, size_pt, ink):
        font = axis.tick_labels.font
        font.name = family
        font.size = Pt(size_pt)
        font.bold = False
        font.italic = False
        font.color.rgb = _rgb(ink)

    def _add_data_labels(self, plot, kind, family, size_pt, ink):
        plot.has_data_labels = True
        labels = plot.data_labels
        labels.font.name = family
        labels.font.size = Pt(size_pt)
        labels.font.bold = False
        labels.font.italic = False
        labels.font.color.rgb = _rgb(ink)
        labels.show_value = True
        for attr in ("show_series_name", "show_category_name", "show_legend_key",
                     "show_percentage", "show_bubble_size"):
            try:
                setattr(labels, attr, False)
            except Exception:
                pass
        try:
            labels.position = (XL_LABEL_POSITION.OUTSIDE_END if kind in ("column", "bar")
                               else XL_LABEL_POSITION.CENTER)
        except Exception:
            pass

    # -- tables -------------------------------------------------------------

    def render_table(self, slide, region, table_spec, payload, where=""):
        header = [_s(c) for c in (payload.get("header") or [])]
        rows = [[_s(c) for c in (r or [])] for r in (payload.get("rows") or [])]
        if not header and not rows:
            raise BuildError("%s: table has neither a header nor rows" % (where or "table"))
        if not header and rows:
            header = ["" for _ in rows[0]]

        max_cols = int(table_spec.get("maxCols", len(header)) or len(header))
        max_rows = int(table_spec.get("maxRows", len(rows)) or len(rows))
        if len(header) > max_cols:
            self.warn("%s: %d columns exceeds the grammar maximum of %d; truncating"
                      % (where or "table", len(header), max_cols))
            header = header[:max_cols]
        if len(rows) > max_rows:
            self.warn("%s: %d body rows exceeds the grammar maximum of %d; truncating"
                      % (where or "table", len(rows), max_rows))
            rows = rows[:max_rows]
        n_cols = len(header)
        rows = [(r + [""] * n_cols)[:n_cols] for r in rows]

        x, y, w, h = self.box(region)
        header_h = float(table_spec.get("headerHeight", 0.42))
        row_h = float(table_spec.get("rowHeight", 0.34))
        needed = header_h + row_h * len(rows)
        if needed > h and len(rows):
            row_h = max((h - header_h) / len(rows), 0.22)
            needed = header_h + row_h * len(rows)

        frame = slide.shapes.add_table(len(rows) + 1, n_cols, bl.inches(x), bl.inches(y),
                                       bl.inches(w), bl.inches(min(needed, h)))
        table = frame.table
        self._neutralise_table_style(table)

        for index, width in enumerate(self._column_widths(header, rows, w)):
            table.columns[index].width = Emu(bl.inches(width))
        table.rows[0].height = Emu(bl.inches(header_h))
        for index in range(len(rows)):
            table.rows[index + 1].height = Emu(bl.inches(row_h))

        numeric_cols = self._numeric_columns(header, rows)

        head_spec = bl.type_role(self.brand, "label")
        head_family = bl.weight_to_family(self.brand, head_spec.get("weight", 500))
        body_spec = bl.type_role(self.brand, "bodySmall")
        body_family = bl.weight_to_family(self.brand, body_spec.get("weight", 400))

        pad_x = float(table_spec.get("cellPadX", 0.12))
        pad_y = float(table_spec.get("cellPadY", 0.06))
        rule_ink = self.color("neutral.200", "#DCE0EA")
        head_fill = self.color("neutral.50", "#F7F8FC")
        head_ink = self.color("neutral.600", "#3C4356")
        body_fill = self.color("neutral.0", "#FFFFFF")

        for col in range(n_cols):
            self._fill_cell(
                table.cell(0, col), header[col], head_family,
                float(head_spec.get("size", 12)), head_ink, head_fill,
                pad_x, pad_y,
                "right" if col in numeric_cols else "left",
                edges=("a:lnT", "a:lnB"), rule_ink=rule_ink)

        for r_index, row in enumerate(rows):
            for col in range(n_cols):
                self._fill_cell(
                    table.cell(r_index + 1, col), row[col], body_family,
                    float(body_spec.get("size", 11)), self.ink, body_fill,
                    pad_x, pad_y,
                    "right" if col in numeric_cols else "left",
                    edges=("a:lnB",), rule_ink=rule_ink)
        return frame

    @staticmethod
    def _neutralise_table_style(table):
        """Swap Office's blue banded default for 'No Style, No Grid'."""
        tbl = table._tbl
        tbl_pr = tbl.find(qn("a:tblPr"))
        if tbl_pr is None:
            tbl_pr = parse_xml("<a:tblPr %s/>" % nsdecls("a"))
            tbl.insert(0, tbl_pr)
        tbl_pr.set("firstRow", "1")
        tbl_pr.set("bandRow", "0")
        for attr in ("firstCol", "lastCol", "lastRow", "bandCol"):
            if attr in tbl_pr.attrib:
                del tbl_pr.attrib[attr]
        style_id = tbl_pr.find(qn("a:tableStyleId"))
        if style_id is None:
            style_id = parse_xml("<a:tableStyleId %s/>" % nsdecls("a"))
            tbl_pr.append(style_id)
        style_id.text = TABLE_STYLE_NO_GRID

    @staticmethod
    def _column_widths(header, rows, total_w):
        # type: (List[str], List[List[str]], float) -> List[float]
        n_cols = len(header)
        if n_cols <= 0:
            return []
        weights = []
        for col in range(n_cols):
            longest = len(header[col])
            for row in rows:
                longest = max(longest, len(row[col]))
            weights.append(float(max(8, min(44, longest))))
        total = sum(weights) or float(n_cols)
        return [total_w * (weight / total) for weight in weights]

    @staticmethod
    def _numeric_columns(header, rows):
        # type: (List[str], List[List[str]]) -> set
        numeric = set()
        for col in range(len(header)):
            values = [row[col] for row in rows if row[col].strip()]
            if not values:
                continue
            hits = sum(1 for v in values if _NUMERIC_CELL.match(v))
            if hits >= max(1, int(math.ceil(0.6 * len(values)))):
                numeric.add(col)
        return numeric

    def _fill_cell(self, cell, text, family, size_pt, ink, fill_hex,
                   pad_x, pad_y, align, edges, rule_ink):
        cell.margin_left = bl.inches(pad_x)
        cell.margin_right = bl.inches(pad_x)
        cell.margin_top = bl.inches(pad_y)
        cell.margin_bottom = bl.inches(pad_y)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        cell.fill.solid()
        cell.fill.fore_color.rgb = _rgb(fill_hex)

        frame = cell.text_frame
        frame.word_wrap = True
        para = frame.paragraphs[0]
        para.alignment = ALIGN.get(align, PP_ALIGN.LEFT)
        para.line_spacing = Pt(size_pt * bl.LEADING_FACTOR)
        run = para.add_run()
        run.text = text
        self.paint_run(run, family, size_pt,
                       self.safe_text_color(ink, fill_hex, size_pt), 0.0)
        self._cell_borders(cell, edges, rule_ink, 1.0)

    @staticmethod
    def _cell_borders(cell, edges, color_hex, width_pt):
        """python-pptx cannot set cell borders, so a:lnT / a:lnB are written here.

        CT_TableCellProperties requires lnL, lnR, lnT, lnB before any fill, so the
        elements are inserted at the head of tcPr in that canonical order.
        """
        order = ("a:lnL", "a:lnR", "a:lnT", "a:lnB")
        tc_pr = cell._tc.get_or_add_tcPr()
        for tag in order:
            existing = tc_pr.find(qn(tag))
            if existing is not None:
                tc_pr.remove(existing)
        position = 0
        emu = int(round(float(width_pt) * EMU_PER_PT))
        for tag in order:
            if tag not in edges:
                continue
            element = parse_xml(
                '<%s %s w="%d" cap="flat" cmpd="sng" algn="ctr">'
                '<a:solidFill><a:srgbClr val="%s"/></a:solidFill>'
                '<a:prstDash val="solid"/></%s>'
                % (tag, nsdecls("a"), emu, _bare(color_hex), tag))
            tc_pr.insert(position, element)
            position += 1

    # =======================================================================
    # archetype renderers
    # =======================================================================

    def _hero_panel(self, slide, region):
        # type: (Any, Dict[str, Any]) -> Tuple[str, str]
        """Paint the brand gradient hero. Returns (text ink, surface colour)."""
        x, y, w, h = self.box(region)
        stops, angle = self.hero_gradient()
        self.add_rect(slide, x, y, w, h, gradient=stops, gradient_angle=angle,
                      radius_in=self.radius_of(str(region.get("radius") or "panel"), 0.28))
        return self.text_on(stops[0]), stops[0]

    def r_cover(self, slide, sd, regions, page_no, arche):
        ink, panel = self._hero_panel(slide, regions["panel"])
        if "logo" in regions:
            self.place_logo(slide, regions["logo"])
        if "rule" in regions:
            x, y, w, h = self.box(regions["rule"])
            self.add_line(slide, x, y, w, h, ink, 1.5)
        self.add_text(slide, regions.get("title"), sd.get("title"), "cover",
                      color=ink, anchor="top", background=panel,
                      where="slide %d cover title" % page_no)
        self.add_text(slide, regions.get("subtitle"), sd.get("subtitle"), "subtitle",
                      color=ink, anchor="top", background=panel,
                      where="slide %d cover subtitle" % page_no)
        self.add_text(slide, regions.get("meta"), sd.get("meta"), "caption",
                      color=ink, anchor="top", background=panel,
                      where="slide %d cover meta" % page_no)

    def r_closing(self, slide, sd, regions, page_no, arche):
        ink, panel = self._hero_panel(slide, regions["panel"])
        if "logo" in regions:
            self.place_logo(slide, regions["logo"])
        if "rule" in regions:
            x, y, w, h = self.box(regions["rule"])
            self.add_line(slide, x, y, w, h, ink, 1.5)
        self.add_text(slide, regions.get("title"), sd.get("title"), "cover",
                      color=ink, anchor="top", background=panel,
                      where="slide %d closing title" % page_no)
        self.add_text(slide, regions.get("cta"), sd.get("cta"), "subtitle",
                      color=ink, anchor="top", background=panel,
                      where="slide %d closing cta" % page_no)
        self.add_text(slide, regions.get("contact"), sd.get("contact"), "body",
                      color=ink, anchor="top", background=panel,
                      where="slide %d closing contact" % page_no)

    def r_section_break(self, slide, sd, regions, page_no, arche):
        art = None
        if "art" in regions:
            art = self.resolve_asset((sd.get("visual") or {}).get("src")
                                     if isinstance(sd.get("visual"), dict) else sd.get("art"))
        ink, panel = self._hero_panel(slide, regions["panel"])
        if art:
            x, y, w, h = self.box(regions["art"])
            self.add_picture_cover(slide, art, x, y, w, h)
        if "logo" in regions:
            self.place_logo(slide, regions["logo"])
        self.add_text(slide, regions.get("title"), sd.get("title"), "section",
                      color=ink, anchor="top", background=panel,
                      where="slide %d section title" % page_no)
        self.add_text(slide, regions.get("kicker"), sd.get("kicker"), "body",
                      color=ink, anchor="top", background=panel,
                      where="slide %d section kicker" % page_no)

    def r_full_bleed(self, slide, sd, regions, page_no, arche):
        image = self.resolve_asset(sd.get("image"))
        x, y, w, h = self.box(regions["image"])
        if image:
            self.add_picture_cover(slide, image, x, y, w, h)
        else:
            self.warn("slide %d full-bleed image '%s' not found; using the brand gradient"
                      % (page_no, sd.get("image")))
            stops, angle = self.hero_gradient()
            self.add_rect(slide, x, y, w, h, gradient=stops, gradient_angle=angle)

        scrim_ink = self.color("neutral.900", "#12141C")
        if "scrim" in regions and (_s(sd.get("title")) or _s(sd.get("caption"))):
            sx, sy, sw, sh = self.box(regions["scrim"])
            shape = self.add_rect(slide, sx, sy, sw, sh)
            self.apply_gradient(shape, [(scrim_ink, 0.0), (scrim_ink, 78.0)], 90.0)
        if "logo" in regions:
            self.place_logo(slide, regions["logo"])
        ink = self.color("neutral.0", "#FFFFFF")
        self.add_text(slide, regions.get("title"), sd.get("title"), "title",
                      color=ink, anchor="top", background=scrim_ink,
                      where="slide %d full-bleed title" % page_no)
        self.add_text(slide, regions.get("caption"), sd.get("caption"), "body",
                      color=ink, anchor="top", background=scrim_ink,
                      where="slide %d full-bleed caption" % page_no)

    def r_agenda(self, slide, sd, regions, page_no, arche):
        items = [_s(i) for i in (sd.get("items") or []) if _s(i)]
        capacity = int((arche.get("capacity") or {}).get("totalItems", len(items)) or len(items))
        if len(items) > capacity:
            self.warn("slide %d agenda has %d items, over the grammar capacity of %d; truncating"
                      % (page_no, len(items), capacity))
            items = items[:capacity]
        lists = [regions[k] for k in ("listA", "listB") if k in regions]
        if not lists:
            return
        if not self._side_panel:
            # The grammar seats the lists to the right of an optional side panel.
            # With no panel that leaves the content measure half empty, so the
            # columns are re-spread across the full content width instead.
            self._spread_columns(lists)
        per_column = int(math.ceil(float(len(items)) / len(lists))) if items else 0
        per_column = min(per_column, int(lists[0].get("maxItems", per_column) or per_column)) or 1
        spec = arche.get("itemSpec") or {}
        counter = 0
        for column_index, region in enumerate(lists):
            chunk = items[column_index * per_column:(column_index + 1) * per_column]
            if not chunk:
                continue
            self._numbered_list(slide, region, spec, chunk, counter, page_no)
            counter += len(chunk)

    def r_index(self, slide, sd, regions, page_no, arche):
        if "rule" in regions:
            x, y, w, h = self.box(regions["rule"])
            # The grammar runs the rule from the header to the canvas edge, which
            # would strike through the chrome title and the footer band. Clamp it
            # to the content band it actually divides.
            top = max(y, self.content_top(sd))
            band = self.chrome_spec.get("footerBand") or {}
            floor = float(band.get("y", self.canvas.get("h", 7.5)))
            bottom = min(y + h, floor)
            if bottom - top > 0.1:
                self.add_line(slide, x, top, w, bottom - top,
                              self.color("neutral.200", "#DCE0EA"), 1.0)
        label_region = regions.get("label")
        if label_region:
            self.add_text(slide, label_region, sd.get("label"), "section",
                          color=self.ink_secondary, max_lines=1,
                          align=label_region.get("align", "right"), anchor="top",
                          where="slide %d index label" % page_no)
        items = [_s(i) for i in (sd.get("items") or []) if _s(i)]
        region = regions.get("list")
        if not region or not items:
            return
        capacity = int(region.get("maxItems", len(items)) or len(items))
        if len(items) > capacity:
            self.warn("slide %d index has %d items, over the grammar capacity of %d; truncating"
                      % (page_no, len(items), capacity))
            items = items[:capacity]
        self._numbered_list(slide, region, arche.get("itemSpec") or {}, items, 0, page_no)

    def _spread_columns(self, regions):
        # type: (List[Dict[str, Any]]) -> None
        """Re-spread a set of side-by-side regions across the content measure."""
        if not regions:
            return
        left = float(self.grid.get("contentLeft", 0.869))
        measure = float(self.grid.get("contentWidth", 11.595))
        count = len(regions)
        if count == 1:
            gap = 0.0
        else:
            gap = float(regions[1].get("x", 0.0)) - (float(regions[0].get("x", 0.0))
                                                     + float(regions[0].get("w", 0.0)))
            gap = max(gap, self.space(5, 0.333))
        width = (measure - gap * (count - 1)) / count
        for index, region in enumerate(regions):
            region["x"] = left + index * (width + gap)
            region["w"] = width

    def _numbered_list(self, slide, region, spec, items, offset, page_no):
        x, y, w, h = self.box(region)
        item_h = float(region.get("itemHeight", h / max(1, len(items))))
        item_h = min(item_h, h / max(1, len(items)))
        badge_spec = spec.get("badge") or {}
        label_spec = spec.get("label") or {}
        badge_size = float(badge_spec.get("w", 0.31))
        dx = float(label_spec.get("dx", badge_size * 2.0))
        label_h = float(label_spec.get("h", 0.34))
        label_w = max(w - dx, 0.5)
        badge_fill = self.color("brand.blue", "#0000FF")
        badge_ink = self.text_on(badge_fill)
        for index, item in enumerate(items):
            top = y + index * item_h
            self.add_circle(slide, x, top, badge_size, fill_hex=badge_fill)
            self.add_text(slide, None, str(offset + index + 1), "caption",
                          color=badge_ink, align="center", anchor="middle",
                          background=badge_fill, max_lines=1,
                          box_override=(x, top, badge_size, badge_size),
                          where="slide %d list badge" % page_no)
            self.add_text(slide, None, item, "label", color=self.ink, anchor="middle",
                          max_lines=1,
                          box_override=(x + dx, top + (badge_size - label_h) / 2.0,
                                        label_w, label_h),
                          where="slide %d list item %d" % (page_no, offset + index + 1))

    def r_title_body(self, slide, sd, regions, page_no, arche):
        self.add_text(slide, regions.get("body"), sd.get("body"), "body",
                      color=self.ink, anchor="top", bullets=True,
                      where="slide %d body" % page_no)

    def r_text_visual(self, slide, sd, regions, page_no, arche):
        self.add_text(slide, regions.get("body"), sd.get("body"), "body",
                      color=self.ink, anchor="top", bullets=True,
                      where="slide %d body" % page_no)
        if "visual" in regions:
            self.render_visual(slide, regions["visual"], sd.get("visual"), True,
                               "slide %d visual" % page_no)

    r_visual_text = r_text_visual

    def r_icon_grid(self, slide, sd, regions, page_no, arche):
        self.add_text(slide, regions.get("intro"), sd.get("intro"), "body",
                      color=self.ink_secondary, anchor="top", bullets=True,
                      where="slide %d intro" % page_no)
        cells = [c for c in (sd.get("cells") or []) if isinstance(c, dict)]
        region = regions.get("grid")
        if not region or not cells:
            return
        capacity = int((arche.get("capacity") or {}).get("cells", len(cells)) or len(cells))
        if len(cells) > capacity:
            self.warn("slide %d icon-grid has %d cells, over the capacity of %d; truncating"
                      % (page_no, len(cells), capacity))
            cells = cells[:capacity]

        x, y, w, h = self.box(region)
        cols, rows = _grid_layout(len(cells), region.get("cols", 2), region.get("rows", 2))
        col_gap = float(region.get("colGap", 0.55))
        row_gap = float(region.get("rowGap", 0.60))
        cell_w = (w - col_gap * (cols - 1)) / cols
        cell_h = (h - row_gap * (rows - 1)) / rows

        spec = arche.get("cellSpec") or {}
        icon_spec = spec.get("icon") or {}
        halo_spec = spec.get("halo") or {}
        sub_spec = spec.get("subtitle") or {}
        body_spec = spec.get("body") or {}
        icon_size = float(icon_spec.get("w", 0.36))
        sub_dx = float(sub_spec.get("dx", 0.64))
        sub_dy = float(sub_spec.get("dy", 0.02))
        body_dx = float(body_spec.get("dx", 0.64))
        body_dy = float(body_spec.get("dy", 0.46))
        # Clamp the subtitle so it cannot cross into the body block.
        sub_h = min(float(sub_spec.get("h", 0.5)), max(body_dy - sub_dy, 0.16))
        body_h = min(float(body_spec.get("h", 0.72)), cell_h - body_dy)

        for index, cell in enumerate(cells):
            col = index % cols
            row = index // cols
            cx = x + col * (cell_w + col_gap)
            cy = y + row * (cell_h + row_gap)
            self.place_icon(slide, cell.get("icon"), cx, cy, icon_size,
                            tint_hex=self.accent_graphic(index),
                            halo=dict(halo_spec) if halo_spec else None)
            self.add_text(slide, None, cell.get("subtitle"), "cardTitle", color=self.ink,
                          anchor="top",
                          box_override=(cx + sub_dx, cy + sub_dy,
                                        min(float(sub_spec.get("w", cell_w - sub_dx)),
                                            cell_w - sub_dx), sub_h),
                          where="slide %d cell %d subtitle" % (page_no, index + 1))
            self.add_text(slide, None, cell.get("body"), "bodySmall",
                          color=self.ink_secondary, anchor="top",
                          box_override=(cx + body_dx, cy + body_dy,
                                        min(float(body_spec.get("w", cell_w - body_dx)),
                                            cell_w - body_dx), body_h),
                          where="slide %d cell %d body" % (page_no, index + 1))

    def r_icon_rows(self, slide, sd, regions, page_no, arche):
        self.add_text(slide, regions.get("intro"), sd.get("intro"), "subtitle",
                      color=self.ink_secondary, anchor="top",
                      where="slide %d intro" % page_no)
        rows = [r for r in (sd.get("rows") or []) if isinstance(r, dict)]
        region = regions.get("rows")
        if not region or not rows:
            return
        capacity = int(region.get("maxRows",
                                  (arche.get("capacity") or {}).get("rows", len(rows))) or len(rows))
        if len(rows) > capacity:
            self.warn("slide %d icon-rows has %d rows, over the capacity of %d; truncating"
                      % (page_no, len(rows), capacity))
            rows = rows[:capacity]

        visual = sd.get("visual") if isinstance(sd.get("visual"), dict) else None
        extra = 0.0
        if visual and "visual" in regions:
            self.render_visual(slide, regions["visual"], visual, True,
                               "slide %d visual" % page_no)
        else:
            # No side visual: let the rows use the full content measure.
            extra = self.widen_to_content(region)

        x, y, w, h = self.box(region)
        w += extra
        row_h = float(region.get("rowHeight", h / max(1, len(rows))))
        row_h = min(row_h, h / max(1, len(rows)))

        spec = arche.get("rowSpec") or {}
        icon_spec = spec.get("icon") or {}
        halo_spec = spec.get("halo") or {}
        sub_spec = spec.get("subtitle") or {}
        body_spec = spec.get("body") or {}
        div_spec = spec.get("divider") or {}
        icon_size = float(icon_spec.get("w", 0.36))
        sub_dx = float(sub_spec.get("dx", 0.63))
        sub_dy = float(sub_spec.get("dy", 0.02))
        body_dx = float(body_spec.get("dx", 0.63))
        body_dy = float(body_spec.get("dy", 0.36))
        sub_h = min(float(sub_spec.get("h", 0.30)), max(body_dy - sub_dy, 0.16))
        body_h = min(float(body_spec.get("h", 0.72)),
                     max(float(div_spec.get("dy", row_h)) - body_dy, 0.2))

        for index, row in enumerate(rows):
            top = y + index * row_h
            self.place_icon(slide, row.get("icon"), x, top, icon_size,
                            tint_hex=self.accent_graphic(index),
                            halo=dict(halo_spec) if halo_spec else None)
            self.add_text(slide, None, row.get("subtitle"), "cardTitle", color=self.ink,
                          anchor="top",
                          box_override=(x + sub_dx, top + sub_dy,
                                        min(float(sub_spec.get("w", w - sub_dx)) + extra,
                                            w - sub_dx), sub_h),
                          where="slide %d row %d subtitle" % (page_no, index + 1))
            self.add_text(slide, None, row.get("body"), "bodySmall",
                          color=self.ink_secondary, anchor="top",
                          box_override=(x + body_dx, top + body_dy,
                                        min(float(body_spec.get("w", w - body_dx)) + extra,
                                            w - body_dx), body_h),
                          where="slide %d row %d body" % (page_no, index + 1))
            if div_spec and index < len(rows) - 1:
                dx = float(div_spec.get("dx", 0.63))
                dy = min(float(div_spec.get("dy", row_h)), row_h - 0.02)
                self.add_line(slide, x + dx, top + dy,
                              min(float(div_spec.get("w", w - dx)) + extra, w - dx), 0.0,
                              self.color("neutral.200", "#DCE0EA"), 0.75)

    def r_columns(self, slide, sd, regions, page_no, arche):
        self.add_text(slide, regions.get("intro"), sd.get("intro"), "subtitle",
                      color=self.ink_secondary, anchor="top",
                      where="slide %d intro" % page_no)
        columns = [c for c in (sd.get("columns") or []) if isinstance(c, dict)]
        region = regions.get("columns")
        if not region or not columns:
            return
        capacity = int(region.get("count",
                                  (arche.get("capacity") or {}).get("columns", len(columns)))
                       or len(columns))
        capacity = max(capacity, 1)
        if len(columns) > capacity:
            self.warn("slide %d columns has %d entries, over the capacity of %d; truncating"
                      % (page_no, len(columns), capacity))
            columns = columns[:capacity]

        x, y, w, h = self.box(region)
        gap = float(region.get("gap", 0.41))
        count = len(columns)
        col_w = (w - gap * (count - 1)) / count

        spec = arche.get("columnSpec") or {}
        sub_spec = spec.get("subtitle") or {}
        body_spec = spec.get("body") or {}
        accent_spec = spec.get("accent") or {}
        body_dy = float(body_spec.get("dy", 0.48))
        sub_h = min(float(sub_spec.get("h", 0.42)), max(body_dy, 0.2))
        body_h = min(float(body_spec.get("h", 2.85)), h - body_dy)
        accent_h = float(accent_spec.get("h", 0.03))
        # The grammar gives the accent a height but no offset: seat it above the
        # column, one spacing step clear of the subtitle, so nothing overlaps.
        accent_dy = float(accent_spec.get("dy", -(accent_h + self.space(2, 0.125))))

        for index, column in enumerate(columns):
            cx = x + index * (col_w + gap)
            if accent_spec:
                self.add_rect(slide, cx, y + accent_dy, col_w, accent_h,
                              fill_hex=self.accent_graphic(index))
            self.add_text(slide, None, column.get("subtitle"), "cardTitle",
                          color=self.ink, anchor="top",
                          box_override=(cx, y, col_w, sub_h),
                          where="slide %d column %d subtitle" % (page_no, index + 1))
            self.add_text(slide, None, column.get("body"), "bodySmall",
                          color=self.ink_secondary, anchor="top", bullets=True,
                          box_override=(cx, y + body_dy, col_w, body_h),
                          where="slide %d column %d body" % (page_no, index + 1))

    def r_steps(self, slide, sd, regions, page_no, arche):
        steps = [s for s in (sd.get("steps") or []) if isinstance(s, dict)]
        region = regions.get("canvas")
        if not region or not steps:
            return
        capacity = int((arche.get("capacity") or {}).get("steps", len(steps)) or len(steps))
        if len(steps) > capacity:
            self.warn("slide %d steps has %d entries, over the capacity of %d; truncating"
                      % (page_no, len(steps), capacity))
            steps = steps[:capacity]

        x, y, w, h = self.box(region)
        cols, rows = _grid_layout(len(steps), region.get("cols", 2), region.get("rows", 2))
        col_gap = float(region.get("colGap", 0.42))
        row_gap = float(region.get("rowGap", 0.50))
        cell_w = (w - col_gap * (cols - 1)) / cols
        cell_h = (h - row_gap * (rows - 1)) / rows

        spec = arche.get("stepSpec") or {}
        icon_spec = spec.get("icon") or {}
        num_spec = spec.get("number") or {}
        body_spec = spec.get("body") or {}
        icon_size = float(icon_spec.get("w", 0.41))
        num_dy = float(num_spec.get("dy", 0.55))
        body_dy = float(body_spec.get("dy", 0.88))
        num_h = min(float(num_spec.get("h", 0.30)), max(body_dy - num_dy, 0.16))
        body_h = min(float(body_spec.get("h", 1.08)), cell_h - body_dy)

        for index, step in enumerate(steps):
            col = index % cols
            row = index // cols
            cx = x + col * (cell_w + col_gap)
            cy = y + row * (cell_h + row_gap)
            if step.get("icon"):
                self.place_icon(slide, step.get("icon"), cx, cy, icon_size,
                                tint_hex=self.accent_graphic(index))
            else:
                bar_h = self.radius_of("sm", 0.042)
                self.add_rect(slide, cx, cy + icon_size - bar_h, icon_size, bar_h,
                              fill_hex=self.accent_graphic(index), radius_in=bar_h / 2.0)
            self.add_text(slide, None, step.get("number"), "cardTitle", color=self.ink,
                          anchor="top",
                          box_override=(cx, cy + num_dy, cell_w, num_h),
                          where="slide %d step %d number" % (page_no, index + 1))
            self.add_text(slide, None, step.get("body"), "bodySmall",
                          color=self.ink_secondary, anchor="top",
                          box_override=(cx, cy + body_dy, cell_w, body_h),
                          where="slide %d step %d body" % (page_no, index + 1))

    def r_process_band(self, slide, sd, regions, page_no, arche):
        stages = [s for s in (sd.get("stages") or []) if isinstance(s, dict)]
        region = regions.get("stages")
        if not region or not stages:
            return
        capacity = int((arche.get("capacity") or {}).get("stages", len(stages)) or len(stages))
        if len(stages) > capacity:
            self.warn("slide %d process-band has %d stages, over the capacity of %d; truncating"
                      % (page_no, len(stages), capacity))
            stages = stages[:capacity]

        band = regions.get("band")
        band_box = None
        if band:
            bx, by, bw, bh = self.box(band)
            band_box = (bx, by, bw, bh)
            self.add_rect(slide, bx, by, bw, bh,
                          fill_hex=self.color("sky.100", "#EBF6F9"),
                          radius_in=self.radius_of(str(band.get("radius") or "lg"), 0.125))

        x, y, w, h = self.box(region)
        gap = float(region.get("gap", 0.30))
        count = len(stages)
        stage_w = (w - gap * (count - 1)) / count

        spec = arche.get("stageSpec") or {}
        icon_spec = spec.get("icon") or {}
        label_spec = spec.get("label") or {}
        body_spec = spec.get("body") or {}
        icon_size = float(icon_spec.get("w", 0.50))
        label_dy = float(label_spec.get("dy", 0.62))
        body_dy = float(body_spec.get("dy", 2.10))
        label_h = min(float(label_spec.get("h", 0.40)), max(body_dy - label_dy, 0.2))

        icon_y = y
        body_y = y + body_dy
        if band_box:
            # Centre the icon and label optically inside the band, and keep the
            # body one spacing step clear of the band's lower edge.
            bx, by, bw, bh = band_box
            icon_y = by + max((bh - (label_dy + label_h)) / 2.0, 0.0)
            body_y = max(body_y, by + bh + self.space(4, 0.25))
        body_h = min(float(body_spec.get("h", 0.91)), max((y + h) - body_y, 0.2))

        for index, stage in enumerate(stages):
            cx = x + index * (stage_w + gap)
            self.place_icon(slide, stage.get("icon"), cx, icon_y, icon_size,
                            tint_hex=self.accent_graphic(index))
            self.add_text(slide, None, stage.get("label"), "cardTitle", color=self.ink,
                          anchor="top",
                          box_override=(cx, icon_y + label_dy, stage_w, label_h),
                          where="slide %d stage %d label" % (page_no, index + 1))
            self.add_text(slide, None, stage.get("body"), "caption",
                          color=self.ink_secondary, anchor="top",
                          box_override=(cx, body_y, stage_w, body_h),
                          where="slide %d stage %d body" % (page_no, index + 1))

    def r_table(self, slide, sd, regions, page_no, arche):
        self.add_text(slide, regions.get("intro"), sd.get("intro"), "subtitle",
                      color=self.ink_secondary, anchor="top",
                      where="slide %d intro" % page_no)
        payload = sd.get("table")
        region = regions.get("table")
        if not region or not isinstance(payload, dict):
            self.warn("slide %d table archetype has no table payload" % page_no)
            return
        self.render_table(slide, region, arche.get("tableSpec") or {}, payload,
                          "slide %d table" % page_no)

    def r_stats(self, slide, sd, regions, page_no, arche):
        self.add_text(slide, regions.get("intro"), sd.get("intro"), "subtitle",
                      color=self.ink_secondary, anchor="top",
                      where="slide %d intro" % page_no)
        stats = [s for s in (sd.get("stats") or []) if isinstance(s, dict)]
        region = regions.get("stats")
        if not region or not stats:
            return
        capacity = int((arche.get("capacity") or {}).get("stats", len(stats)) or len(stats))
        if len(stats) > capacity:
            self.warn("slide %d stats has %d entries, over the capacity of %d; truncating"
                      % (page_no, len(stats), capacity))
            stats = stats[:capacity]

        x, y, w, h = self.box(region)
        gap = float(region.get("gap", 0.42))
        count = len(stats)
        stat_w = (w - gap * (count - 1)) / count

        spec = arche.get("statSpec") or {}
        value_spec = spec.get("value") or {}
        label_spec = spec.get("label") or {}
        caption_spec = spec.get("caption") or {}
        rule_spec = spec.get("rule") or {}
        label_dy = float(label_spec.get("dy", 0.80))
        caption_dy = float(caption_spec.get("dy", 1.18))
        rule_dy = float(rule_spec.get("dy", 1.90)) if rule_spec else None
        value_h = min(float(value_spec.get("h", 0.72)), max(label_dy, 0.3))
        label_h = min(float(label_spec.get("h", 0.34)), max(caption_dy - label_dy, 0.2))
        caption_limit = rule_dy if rule_dy is not None else h
        caption_h = min(float(caption_spec.get("h", 0.60)),
                        max(caption_limit - caption_dy - self.space(1, 0.083), 0.2))

        for index, stat in enumerate(stats):
            cx = x + index * (stat_w + gap)
            self.add_text(slide, None, stat.get("value"), "statNumber",
                          color=self.accent(index), anchor="top", max_lines=1,
                          box_override=(cx, y, stat_w, value_h),
                          where="slide %d stat %d value" % (page_no, index + 1))
            self.add_text(slide, None, stat.get("label"), "cardTitle", color=self.ink,
                          anchor="top",
                          box_override=(cx, y + label_dy, stat_w, label_h),
                          where="slide %d stat %d label" % (page_no, index + 1))
            self.add_text(slide, None, stat.get("caption"), "caption",
                          color=self.ink_secondary, anchor="top",
                          box_override=(cx, y + caption_dy, stat_w, caption_h),
                          where="slide %d stat %d caption" % (page_no, index + 1))
            if rule_dy is not None and y + rule_dy <= y + h:
                self.add_line(slide, cx, y + rule_dy, stat_w, 0.0,
                              self.color("neutral.200", "#DCE0EA"), 1.0)

    def r_quote(self, slide, sd, regions, page_no, arche):
        quote = _s(sd.get("quote"))
        if quote:
            marks = _dig(self.brand, "voice.quoteMarks") or [u"“", u"”"]
            if not quote.startswith(marks[0]):
                quote = "%s%s%s" % (marks[0], quote, marks[-1])
        self.add_text(slide, regions.get("quote"), quote, "section",
                      color=self.ink, anchor="top", where="slide %d quote" % page_no)
        self.add_text(slide, regions.get("attrib"), sd.get("attrib"), "label",
                      color=self.ink, anchor="top", where="slide %d attribution" % page_no)
        self.add_text(slide, regions.get("attribSub"), sd.get("attribSub"), "caption",
                      color=self.ink_secondary, anchor="top",
                      where="slide %d attribution detail" % page_no)

    def r_photo_trio(self, slide, sd, regions, page_no, arche):
        self.add_text(slide, regions.get("body"), sd.get("body"), "body",
                      color=self.ink, anchor="top", bullets=True,
                      where="slide %d body" % page_no)
        photos = [p for p in (sd.get("photos") or []) if isinstance(p, dict)]
        region = regions.get("photos")
        if not region or not photos:
            return
        capacity = int(region.get("count",
                                  (arche.get("capacity") or {}).get("photos", len(photos)))
                       or len(photos))
        if len(photos) > capacity:
            self.warn("slide %d photo-trio has %d photos, over the capacity of %d; truncating"
                      % (page_no, len(photos), capacity))
            photos = photos[:capacity]

        x, y, w, h = self.box(region)
        gap = float(region.get("gap", 0.39))
        count = len(photos)
        photo_w = (w - gap * (count - 1)) / count

        spec = arche.get("photoSpec") or {}
        image_spec = spec.get("image") or {}
        caption_spec = spec.get("caption") or {}
        caption_dy = float(caption_spec.get("dy", h))
        image_h = min(float(image_spec.get("h", h)), max(caption_dy - self.space(1, 0.083), 0.5))
        caption_h = float(caption_spec.get("h", 0.66))
        radius = self.radius_of(str(image_spec.get("radius") or "lg"), 0.125)

        for index, photo in enumerate(photos):
            cx = x + index * (photo_w + gap)
            path = self.resolve_asset(photo.get("image"))
            if path:
                self.add_picture_cover(slide, path, cx, y, photo_w, image_h)
            else:
                self.warn("slide %d photo '%s' not found; drawing a placeholder panel"
                          % (page_no, photo.get("image")))
                self.add_rect(slide, cx, y, photo_w, image_h,
                              fill_hex=self.color("sky.100", "#EBF6F9"), radius_in=radius)
            self.add_text(slide, None, photo.get("caption"), "caption",
                          color=self.ink_secondary, anchor="top",
                          box_override=(cx, y + caption_dy, photo_w, caption_h),
                          where="slide %d photo %d caption" % (page_no, index + 1))

    RENDERERS = {
        "cover": "r_cover",
        "agenda": "r_agenda",
        "index": "r_index",
        "section-break": "r_section_break",
        "title-body": "r_title_body",
        "text-visual": "r_text_visual",
        "visual-text": "r_visual_text",
        "icon-grid": "r_icon_grid",
        "icon-rows": "r_icon_rows",
        "columns": "r_columns",
        "steps": "r_steps",
        "process-band": "r_process_band",
        "table": "r_table",
        "stats": "r_stats",
        "quote": "r_quote",
        "photo-trio": "r_photo_trio",
        "full-bleed": "r_full_bleed",
        "closing": "r_closing",
    }

    # =======================================================================
    # build
    # =======================================================================

    def build(self):
        # type: () -> Any
        slides = self.ir.get("slides")
        if not isinstance(slides, list) or not slides:
            raise BuildError("deck IR has no 'slides' array")

        missing = sorted(set(self.archetypes) - set(self.RENDERERS))
        if missing:
            raise BuildError("no renderer for grammar archetype(s): %s" % ", ".join(missing))

        for index, sd in enumerate(slides):
            page_no = index + 1
            if not isinstance(sd, dict):
                raise BuildError("slide %d is not an object" % page_no)
            arche_id = _s(sd.get("archetype"))
            if not arche_id:
                raise BuildError("slide %d has no 'archetype'" % page_no)
            arche = self.archetypes.get(arche_id)
            if arche is None:
                raise BuildError("slide %d uses unknown archetype '%s'. Known: %s"
                                 % (page_no, arche_id, ", ".join(sorted(self.archetypes))))

            slide = self.prs.slides.add_slide(self._blank)
            regions = self.regions_for(arche, sd)

            left_override = None
            self._side_panel = False
            if arche_id == "agenda":
                visual = sd.get("visual") if isinstance(sd.get("visual"), dict) else None
                panel = regions.get("visual")
                if visual and panel:
                    self._side_panel = True
                    px, py, pw, ph = self.box(panel)
                    path = self.resolve_asset(visual.get("src"))
                    if path:
                        self.add_picture_cover(slide, path, px, py, pw, ph)
                    else:
                        stops, angle = self.hero_gradient()
                        self.add_rect(slide, px, py, pw, ph,
                                      gradient=stops, gradient_angle=angle)
                    left_override = px + pw + float(self.grid.get("gutterLeft", 0.869))

            if arche.get("chrome"):
                self.render_chrome(slide, sd, page_no, left_override, arche)

            getattr(self, self.RENDERERS[arche_id])(slide, sd, regions, page_no, arche)
            self._attach_notes(slide, page_no)

        self._set_core_properties()
        return self.prs

    def _attach_notes(self, slide, page_no):
        notes = self.ir.get("notes") or {}
        text = _s(notes.get(str(page_no))) or _s(notes.get(page_no))
        if not text:
            return
        frame = slide.notes_slide.notes_text_frame
        frame.text = text

    def _set_core_properties(self):
        meta = self.ir.get("meta") or {}
        props = self.prs.core_properties
        props.title = _s(meta.get("title"), "Untitled deck")
        props.author = _s(meta.get("author"), _s(self.brand.get("name"), "Unknown"))
        props.last_modified_by = props.author
        props.comments = _s(meta.get("confidentiality"))
        props.subject = _s(meta.get("client"))
        props.category = _s(self.brand.get("id"))
        keywords = [k for k in (props.category, _s(meta.get("date"))) if k]
        props.keywords = " ".join(keywords)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_ir(path):
    # type: (str) -> Dict[str, Any]
    if not os.path.isfile(path):
        raise BuildError("deck IR not found: %s" % path)
    try:
        with open(path, "r") as handle:
            data = json.load(handle)
    except ValueError as exc:
        raise BuildError("%s is not valid JSON: %s" % (path, exc))
    if not isinstance(data, dict):
        raise BuildError("%s must contain a JSON object" % path)
    return data


def build_deck(ir_path, out_path, brand_id=None, strict=False):
    # type: (str, str, Optional[str], bool) -> Tuple[int, int, str]
    """Build ``ir_path`` into ``out_path``. Returns (slides, warnings, out_path)."""
    ir = _load_ir(ir_path)
    resolved = brand_id or _s(ir.get("brand"))
    if not resolved:
        raise BuildError("no brand: pass --brand or set 'brand' in the deck IR")
    try:
        brand = bl.load_brand(resolved)
    except bl.BrandNotFound as exc:
        raise BuildError(str(exc))
    grammar = bl.load_grammar()

    builder = DeckBuilder(ir, brand, grammar, ir_path=ir_path, strict=strict)
    prs = builder.build()

    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    prs.save(out_path)
    return len(prs.slides._sldIdLst), len(builder.warnings), os.path.abspath(out_path)


def main(argv=None):
    # type: (Optional[Sequence[str]]) -> int
    parser = argparse.ArgumentParser(
        prog="build_deck.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ir", help="path to the Deck IR JSON document")
    parser.add_argument("--brand", help="brand id (defaults to the IR's 'brand' field)")
    parser.add_argument("--out", help="output .pptx path")
    parser.add_argument("--strict", action="store_true",
                        help="treat any capacity overflow as a hard error instead "
                             "of shrinking and truncating")
    parser.add_argument("--list-archetypes", action="store_true",
                        help="list the archetype ids this engine can render, then exit")
    args = parser.parse_args(argv)

    if args.list_archetypes:
        try:
            grammar = bl.load_grammar()
        except Exception as exc:
            sys.stderr.write("build_deck: %s\n" % exc)
            return 1
        known = [a.get("id") for a in (grammar.get("archetypes") or []) if a.get("id")]
        for arche_id in known:
            mark = "ok " if arche_id in DeckBuilder.RENDERERS else "MISSING"
            sys.stdout.write("%-8s %s\n" % (mark, arche_id))
        return 0

    if not args.ir or not args.out:
        parser.error("--ir and --out are both required (or use --list-archetypes)")

    try:
        slides, warnings, out_path = build_deck(args.ir, args.out, args.brand, args.strict)
    except BuildError as exc:
        sys.stderr.write("build_deck: %s\n" % exc)
        return 1
    except Exception as exc:  # pragma: no cover - unexpected, still must exit non-zero
        sys.stderr.write("build_deck: unexpected failure: %s: %s\n"
                         % (type(exc).__name__, exc))
        return 1

    sys.stderr.write("build_deck: %d slides, %d warning%s -> %s\n"
                     % (slides, warnings, "" if warnings == 1 else "s", out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
