#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate_video.py - deterministic brand-compliance validator for rendered video.

Probes a finished .mp4 with ffprobe/ffmpeg, samples frames with pillow, and reports
every brand breach it can prove. Nothing here is a judgement call: each check is a
measurement against the active brand profile (brands/<id>/brand.json), so the same
file always produces the same report.

    validate_video.py out/film.mp4 --brand channelplay
    validate_video.py out/film.mp4 --srt out/film.srt --timeline out/film.timeline.json
    validate_video.py out/film.mp4 --format human --frames 24
    validate_video.py out/reel.mp4 --delivery-format vertical

DELIVERY FORMAT. A brand may declare several delivery canvases under
video.formats -- landscape, square, vertical -- each with its own safeMarginPct
and captionSizePt. The render under test was built for exactly one of them, so
one is resolved per run and drives VIDEO.RESOLUTION, VIDEO.SAFE_MARGIN and the
caption geometry. Resolution order: --delivery-format, then the format the
builder recorded in the timeline sidecar, then a Video IR's meta.format, then
the brand's default. Note that --format is the OUTPUT format (json/human) and
predates this; the delivery format has its own flag rather than overloading it.

Output is FROZEN CONTRACT C on stdout:

    {"target":..., "brand":..., "kind":"video", "pass":bool,
     "counts":{"error":n,"warn":n,"info":n},
     "violations":[{"id","severity","where","found","expected","rule","fix"}, ...]}

Exit status
    0   no errors (warnings and info do not fail the render)
    2   at least one error
    1   the validator itself could not run (missing file, no ffprobe, unparseable input)

Checks implemented
    VIDEO.RESOLUTION  VIDEO.FPS  VIDEO.CODEC  VIDEO.NO_INTRO  VIDEO.NO_OUTRO
    VIDEO.DURATION    VIDEO.SAFE_MARGIN       VIDEO.OFF_PALETTE
    VIDEO.FORMAT_UNKNOWN
    AUDIO.MISSING     AUDIO.LOUDNESS          AUDIO.CLIPPING   AUDIO.SILENCE
    CAPTION.MISSING   CAPTION.LINE_LEN        CAPTION.LINE_COUNT
    CAPTION.TOO_FAST  CAPTION.OVERLAP         CAPTION.GAP      CAPTION.POSITION
    CONTENT.PLACEHOLDER   VOICE.EXCLAMATION   STRUCTURE.STORYLINE
"""

from __future__ import absolute_import

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from lib import brandlib as B  # noqa: E402

try:
    from PIL import Image, ImageChops, ImageStat
except ImportError as _exc:  # pragma: no cover - environment guard
    sys.stderr.write("validate_video.py: pillow is required (%s)\n" % _exc)
    sys.exit(1)


# ---------------------------------------------------------------------------
# constants / tuning
# ---------------------------------------------------------------------------

DEFAULT_BRAND = "channelplay"

#: Width in pixels that sampled frames are downscaled to before pixel analysis.
#: Small enough to stay fast in pure Python, large enough that a 5% safe-margin
#: band is still ~19px wide.
SAMPLE_WIDTH = 384

#: A quantised colour must occupy at least this share of a frame to count as
#: "dominant" for VIDEO.OFF_PALETTE.
OFF_PALETTE_MIN_SHARE = 0.08

#: CIE76 distance beyond which a dominant colour is considered off-palette.
OFF_PALETTE_DELTA_E = 12.0

#: Off-palette observations closer than this are folded into one violation.
OFF_PALETTE_CLUSTER_DELTA_E = 6.0

#: Never emit more than this many OFF_PALETTE clusters; photographic footage
#: would otherwise bury every other finding.
OFF_PALETTE_MAX_CLUSTERS = 12

#: A frame's dominant colour must be this close to a brand gradient stop for the
#: intro/outro to count as branded.
INTRO_DELTA_E = 10.0

#: Colours at or above this share of a frame are treated as "the dominant colour"
#: for the intro/outro test (in addition to the single largest).
INTRO_MIN_SHARE = 0.25

#: Frames whose content mask covers more than this fraction are full-bleed
#: photography or gradient washes; a safe-margin test on them is meaningless.
SAFE_MARGIN_FULLBLEED_DENSITY = 0.60

#: Fractional slack when testing the content bbox against the safe-margin band.
SAFE_MARGIN_TOLERANCE = 0.005

#: Tolerance, in LU, around the brand's integrated loudness target.
LOUDNESS_TOLERANCE_LU = 2.0

#: dBTP above which the master is considered to be clipping.
TRUE_PEAK_CEILING_DBTP = -1.0

#: Leading/trailing silence longer than this is worth mentioning.
SILENCE_LIMIT_SEC = 1.5

#: Caption dead air longer than this, while narration should be running.
CAPTION_GAP_LIMIT_SEC = 3.0

#: Rendered duration may drift this far from the timeline before it is reported.
DURATION_TOLERANCE_SEC = 1.0

#: Chroma (max channel - min channel) at or below which a colour reads as grey.
NEUTRAL_CHROMA = 20

#: HSV envelope for human skin, wide enough to cover the full Fitzpatrick range
#: (#FFDBAC through #8D5524) while still excluding saturated brand oranges and
#: reds, which sit above SKIN_SAT_MAX.
SKIN_HUE_MAX_DEG = 55.0
SKIN_SAT_MIN = 0.10
SKIN_SAT_MAX = 0.85
SKIN_VALUE_MIN = 0.18

#: Per-check emission caps so a badly broken file stays readable.
MAX_CUE_VIOLATIONS = 15
MAX_GAP_VIOLATIONS = 10

#: ffprobe/ffmpeg codec_name values accepted for a brand's configured encoder.
CODEC_ALIASES = {
    "libx264": ("h264", "libx264", "x264", "avc1"),
    "libx265": ("hevc", "libx265", "h265", "hvc1"),
    "libvpx-vp9": ("vp9", "libvpx-vp9"),
    "libvpx": ("vp8", "libvpx"),
    "libaom-av1": ("av1", "libaom-av1", "libsvtav1"),
    "prores_ks": ("prores", "prores_ks"),
    "aac": ("aac", "libfdk_aac", "aac_at"),
    "libmp3lame": ("mp3", "libmp3lame"),
    "libopus": ("opus", "libopus"),
    "pcm_s16le": ("pcm_s16le",),
}

try:
    _MEDIANCUT = Image.Quantize.MEDIANCUT
except AttributeError:  # pragma: no cover - very old pillow
    _MEDIANCUT = getattr(Image, "MEDIANCUT", 0)

try:
    _BILINEAR = Image.Resampling.BILINEAR
except AttributeError:  # pragma: no cover
    _BILINEAR = getattr(Image, "BILINEAR", 2)


class ValidatorError(Exception):
    """The validator could not run. Maps to exit status 1."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _run(cmd, timeout=900):
    # type: (Sequence[str], int) -> Tuple[int, bytes, bytes]
    """Run an argv list (never a shell string) and return (rc, stdout, stderr)."""
    try:
        proc = subprocess.run(list(cmd), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout)
    except FileNotFoundError:
        raise ValidatorError("%s was not found on PATH" % cmd[0])
    except subprocess.TimeoutExpired:
        raise ValidatorError("%s timed out after %ds" % (cmd[0], timeout))
    return proc.returncode, proc.stdout or b"", proc.stderr or b""


def _decode(raw):
    # type: (bytes) -> str
    return raw.decode("utf-8", "replace")


def _ts(seconds):
    # type: (float) -> str
    """Seconds -> 'HH:MM:SS.mmm' for human-readable frame/cue addresses."""
    s = max(0.0, float(seconds))
    hh = int(s // 3600)
    mm = int((s % 3600) // 60)
    return "%02d:%02d:%06.3f" % (hh, mm, s % 60)


def _num(value, default=None):
    # type: (Any, Optional[float]) -> Optional[float]
    try:
        if value is None:
            return default
        f = float(value)
    except (TypeError, ValueError):
        return default
    if f != f:  # NaN
        return default
    return f


def _first(mapping, keys, default=None):
    # type: (Dict[str, Any], Sequence[str], Any) -> Any
    for k in keys:
        if isinstance(mapping, dict) and k in mapping and mapping[k] is not None:
            return mapping[k]
    return default


def _pct(fraction):
    # type: (float) -> str
    return "%.1f%%" % (100.0 * float(fraction))


def _sample_times(t0, t1, count):
    # type: (float, float, int) -> List[float]
    """`count` evenly spaced midpoints strictly inside [t0, t1]."""
    n = max(0, int(count))
    if n == 0:
        return []
    span = max(0.0, float(t1) - float(t0))
    if span <= 0.0:
        return [max(0.0, float(t0))]
    return [float(t0) + span * ((i + 0.5) / float(n)) for i in range(n)]


def _cap(items, limit):
    # type: (Sequence[Any], int) -> Tuple[List[Any], int]
    """(first `limit` items, number suppressed)."""
    seq = list(items)
    if len(seq) <= limit:
        return seq, 0
    return seq[:limit], len(seq) - limit


def _suffix(extra):
    # type: (int) -> str
    return "" if extra <= 0 else "  (+%d more not listed)" % extra


def _tail_suffix(index, shown, extra):
    # type: (int, Sequence[Any], int) -> str
    """The '+N more' note, attached to the last item actually emitted."""
    return _suffix(extra) if index == len(shown) - 1 else ""


# ---------------------------------------------------------------------------
# colour predicates
# ---------------------------------------------------------------------------

def is_neutral(hex_color):
    # type: (str) -> bool
    """True for greys, near-white and near-black: chroma at or below NEUTRAL_CHROMA."""
    r, g, b = B.hex_to_rgb(hex_color)
    return (max(r, g, b) - min(r, g, b)) <= NEUTRAL_CHROMA


def is_skin_tone(hex_color):
    # type: (str) -> bool
    """True for plausible human skin: warm hue, moderate saturation, R >= G >= B."""
    r, g, b = B.hex_to_rgb(hex_color)
    mx, mn = max(r, g, b), min(r, g, b)
    if mx == 0 or mx == mn:
        return False
    if not (r >= g >= b):
        return False
    delta = float(mx - mn)
    if mx == r:
        hue = ((g - b) / delta) % 6.0
    elif mx == g:
        hue = ((b - r) / delta) + 2.0
    else:
        hue = ((r - g) / delta) + 4.0
    hue = (hue * 60.0) % 360.0
    sat = delta / float(mx)
    val = mx / 255.0
    return (0.0 <= hue <= SKIN_HUE_MAX_DEG
            and SKIN_SAT_MIN <= sat <= SKIN_SAT_MAX
            and SKIN_VALUE_MIN <= val <= 1.0)


def hero_colors(brand):
    # type: (Dict[str, Any]) -> Dict[str, str]
    """{'#HEX': 'token'} of the colours an intro/outro is allowed to open on.

    Every gradient stop plus the primary brand hues. The intro style is a brand
    gradient reveal, so a frame that is dominated by none of these is not the
    brand's intro.
    """
    out = {}  # type: Dict[str, str]
    color = (brand.get("color") or {})
    gradients = color.get("gradient") or {}
    if isinstance(gradients, dict):
        for gname, gdef in gradients.items():
            stops = (gdef or {}).get("stops") if isinstance(gdef, dict) else None
            for i, stop in enumerate(stops or []):
                if B.is_hex(stop):
                    out.setdefault(B.normalize_hex(stop), "gradient.%s.%d" % (gname, i))
    for key in ("blue", "navy", "mint", "teal"):
        val = (color.get("brand") or {}).get(key)
        if B.is_hex(val):
            out.setdefault(B.normalize_hex(val), "brand.%s" % key)
    return out


# ---------------------------------------------------------------------------
# delivery formats
#
# A brand may declare several delivery canvases under video.formats. The render
# under test was built for exactly ONE of them, and every geometric check has to
# be made against that one: a correct 1080x1920 reel is not a failed 1920x1080
# film. The format is resolved the same way build_video.py resolves it, so the
# validator and the builder cannot disagree about what was asked for.
# ---------------------------------------------------------------------------

# Read out of a format's own $note ("Reserve the top 12% and bottom 20% for
# platform chrome"), which is where brand.json states the reserve. There is no
# structured field for it, so the sentence is the declaration.
_CHROME_TOP_RE = re.compile(r"top\s+([0-9]+(?:\.[0-9]+)?)\s*%", re.IGNORECASE)
_CHROME_BOTTOM_RE = re.compile(r"bottom\s+([0-9]+(?:\.[0-9]+)?)\s*%", re.IGNORECASE)

#: Conservative average glyph advance, as a fraction of the caption size, used
#: only to prove that a brand's maxCharsPerLine physically cannot fit inside a
#: format's safe width. Deliberately generous: it must never tighten the limit
#: for a format whose captions do fit.
CAPTION_ADVANCE_EM = 0.52


def declared_formats(brand):
    # type: (Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]
    """(name, spec) for every delivery format the brand declares, in order."""
    node = ((brand.get("video") or {}).get("formats"))
    if not isinstance(node, dict):
        return []
    out = []  # type: List[Tuple[str, Dict[str, Any]]]
    for name, spec in node.items():
        if not isinstance(name, str) or name.startswith("$"):
            continue
        if not isinstance(spec, dict):
            continue
        if (_num(spec.get("w"), 0) or 0) > 0 and (_num(spec.get("h"), 0) or 0) > 0:
            out.append((name, spec))
    return out


def default_format_name(brand):
    # type: (Dict[str, Any]) -> Optional[str]
    """The brand's default delivery format, or None when it declares none.

    Whichever declared format matches video.resolution -- the canvas the brand
    already masters at -- then one literally called 'landscape', then the first
    declared. Derived from what the brand says; nothing is invented here.
    """
    formats = declared_formats(brand)
    if not formats:
        return None
    res = (brand.get("video") or {}).get("resolution") or {}
    res_w = _num(res.get("w"), 0) or 0
    res_h = _num(res.get("h"), 0) or 0
    if res_w > 0 and res_h > 0:
        for name, spec in formats:
            if (_num(spec.get("w"), 0) == res_w and _num(spec.get("h"), 0) == res_h):
                return name
    for name, _spec in formats:
        if name == "landscape":
            return name
    return formats[0][0]


def chrome_reserve(spec):
    # type: (Dict[str, Any]) -> Tuple[float, float]
    """(top%, bottom%) a format keeps clear for platform chrome, from its $note."""
    note = ""
    for key in ("$note", "note"):
        value = spec.get(key)
        if isinstance(value, str) and value.strip():
            note = value
            break
    if not note:
        return (0.0, 0.0)
    top = _CHROME_TOP_RE.search(note)
    bottom = _CHROME_BOTTOM_RE.search(note)
    return (float(top.group(1)) if top else 0.0,
            float(bottom.group(1)) if bottom else 0.0)


def format_request(explicit, raw_timeline):
    # type: (Optional[str], Optional[Dict[str, Any]]) -> Tuple[Optional[str], str]
    """(name, source) of the delivery format this render should be judged against.

    Explicit flag first, then the format the builder RECORDED in the timeline
    sidecar, then a Video IR's meta.format, then the brand's default.
    """
    if explicit and str(explicit).strip():
        return (str(explicit).strip(), "--delivery-format")
    if isinstance(raw_timeline, dict):
        video = raw_timeline.get("video")
        if isinstance(video, dict):
            name = video.get("format")
            if isinstance(name, str) and name.strip():
                return (name.strip(), "the timeline's recorded format")
        meta = raw_timeline.get("meta")
        if isinstance(meta, dict):
            name = meta.get("format")
            if isinstance(name, str) and name.strip():
                return (name.strip(), "the IR's meta.format")
    return (None, "the brand default")


def resolve_format(brand, name, source):
    # type: (Dict[str, Any], Optional[str], str) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]
    """(format, unknown) for the canvas to validate against.

    Never raises: an undeclared name is REPORTED as VIDEO.FORMAT_UNKNOWN and the
    remaining checks fall back to the brand default, so one bad name still
    produces a full report rather than an aborted run.
    """
    formats = declared_formats(brand)
    lookup = dict(formats)
    video = brand.get("video") or {}
    unknown = None  # type: Optional[Dict[str, Any]]

    if name is not None and name not in lookup:
        unknown = {"requested": name, "source": source,
                   "declared": [n for n, _ in formats]}
        name = None
        source = "the brand default (after an unknown format was requested)"

    if name is None:
        fallback = default_format_name(brand)
        if fallback is not None:
            name = fallback
            if unknown is None:
                source = "the brand default"

    base_safe = _num(video.get("safeMarginPct"), 5.0) or 5.0
    captions = video.get("captions") or {}
    base_caption = _num(captions.get("sizePt"), 28.0) or 28.0
    base_bottom = _num(captions.get("bottomMarginPct"), 8.0) or 8.0

    if name is None or name not in lookup:
        # The brand declares no formats at all: the canvas is video.resolution,
        # exactly as it was before formats existed.
        res = video.get("resolution") or {}
        return ({
            "name": None,
            "declared": False,
            "width": _num(res.get("w"), 0),
            "height": _num(res.get("h"), 0),
            "ratio": None,
            "safeMarginPct": base_safe,
            "captionSizePt": base_caption,
            "chromeTopPct": 0.0,
            "chromeBottomPct": 0.0,
            "captionBottomMarginPct": base_bottom,
            "source": "video.resolution (the brand declares no formats)",
        }, unknown)

    spec = lookup[name]
    top_pct, bottom_pct = chrome_reserve(spec)
    return ({
        "name": name,
        "declared": True,
        "width": _num(spec.get("w"), 0),
        "height": _num(spec.get("h"), 0),
        "ratio": str(spec.get("ratio") or "") or None,
        "safeMarginPct": _num(spec.get("safeMarginPct"), base_safe) or base_safe,
        "captionSizePt": _num(spec.get("captionSizePt"), base_caption) or base_caption,
        "chromeTopPct": top_pct,
        "chromeBottomPct": bottom_pct,
        "captionBottomMarginPct": max(base_bottom, bottom_pct),
        "source": source,
    }, unknown)


def format_label(fmt):
    # type: (Dict[str, Any]) -> str
    """How a resolved format is named inside a violation."""
    return "%s %dx%d%s" % (
        fmt.get("name") or "video.resolution",
        int(fmt.get("width") or 0), int(fmt.get("height") or 0),
        " %s" % fmt["ratio"] if fmt.get("ratio") else "")


# ---------------------------------------------------------------------------
# ffprobe / ffmpeg
# ---------------------------------------------------------------------------

def probe(path):
    # type: (str) -> Dict[str, Any]
    """ffprobe -print_format json -show_format -show_streams, parsed."""
    rc, out, err = _run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ], timeout=180)
    if rc != 0:
        raise ValidatorError("ffprobe failed on %s: %s"
                             % (path, _decode(err).strip()[-500:] or "rc=%d" % rc))
    try:
        data = json.loads(_decode(out) or "{}")
    except ValueError as exc:
        raise ValidatorError("ffprobe did not return JSON for %s: %s" % (path, exc))
    if not isinstance(data, dict):
        raise ValidatorError("ffprobe returned an unexpected payload for %s" % path)
    return data


def streams_of(info, codec_type):
    # type: (Dict[str, Any], str) -> List[Dict[str, Any]]
    return [s for s in (info.get("streams") or [])
            if isinstance(s, dict) and s.get("codec_type") == codec_type]


def parse_rate(value):
    # type: (Any) -> Optional[float]
    """'30000/1001' -> 29.97. Returns None for '0/0' and unparseable input."""
    if value is None:
        return None
    text = str(value).strip()
    if "/" in text:
        num, _, den = text.partition("/")
        n = _num(num)
        d = _num(den)
        if n is None or d is None or d == 0.0:
            return None
        return n / d
    return _num(text)


def container_duration(info):
    # type: (Dict[str, Any]) -> float
    """Best available duration in seconds, 0.0 when nothing reports one."""
    dur = _num((info.get("format") or {}).get("duration"))
    if dur and dur > 0:
        return dur
    for stream in streams_of(info, "video") + streams_of(info, "audio"):
        sdur = _num(stream.get("duration"))
        if sdur and sdur > 0:
            return sdur
        frames = _num(stream.get("nb_frames"))
        rate = parse_rate(stream.get("avg_frame_rate")) or parse_rate(stream.get("r_frame_rate"))
        if frames and rate and rate > 0:
            return frames / rate
    return 0.0


def extract_frame(path, when, out_path, width=SAMPLE_WIDTH):
    # type: (str, float, str, int) -> bool
    """Grab a single frame at `when` seconds, downscaled to `width` px. True on success."""
    seek = "%.3f" % max(0.0, float(when))
    base = ["ffmpeg", "-nostdin", "-v", "error", "-y"]
    tail = ["-frames:v", "1", "-vf", "scale=%d:-1:flags=area" % int(width),
            "-f", "image2", out_path]
    # Fast seek first (input-side -ss), then an accurate seek as a fallback for
    # containers whose keyframe index is sparse near the tail.
    attempts = [
        base + ["-ss", seek, "-i", path] + tail,
        base + ["-i", path, "-ss", seek] + tail,
    ]
    for cmd in attempts:
        rc, _out, _err = _run(cmd, timeout=120)
        if rc == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
            return True
    return False


def dominant_colors(image_path, ncolors=8):
    # type: (str, int) -> List[Tuple[str, float]]
    """[(hex, share_of_pixels), ...] sorted by share, from a median-cut quantise."""
    with Image.open(image_path) as raw:
        img = raw.convert("RGB")
    quant = img.quantize(colors=int(ncolors), method=_MEDIANCUT)
    palette = quant.getpalette() or []
    counts = quant.getcolors(int(ncolors) * 8) or []
    total = float(sum(c for c, _ in counts)) or 1.0
    out = []  # type: List[Tuple[str, float]]
    for count, index in counts:
        base = int(index) * 3
        if base + 2 >= len(palette):
            continue
        out.append((B.rgb_to_hex(palette[base:base + 3]), count / total))
    out.sort(key=lambda pair: -pair[1])
    return out


def content_bbox(image_path):
    # type: (str) -> Optional[Tuple[Tuple[float, float, float, float], float, str]]
    """Bounding box of non-background pixels, as fractions of the frame.

    The background reference is the per-channel median of the outer 2% ring, and
    the detection threshold adapts to how much that ring already varies, so a
    gradient wash does not read as content. Returns
    ((left, top, right, bottom), content_density, background_hex) or None when
    the frame is uniform.
    """
    with Image.open(image_path) as raw:
        img = raw.convert("RGB")
    w, h = img.size
    if w < 8 or h < 8:
        return None

    band = max(1, int(round(min(w, h) * 0.02)))
    pixels = img.load()
    ring = []
    for y in range(h):
        if y < band or y >= h - band:
            xs = range(0, w)
        else:
            xs = list(range(0, band)) + list(range(w - band, w))
        for x in xs:
            ring.append(pixels[x, y])
    if not ring:
        return None

    median = []
    for channel in range(3):
        values = sorted(p[channel] for p in ring)
        median.append(values[len(values) // 2])
    med = (median[0], median[1], median[2])

    spread = sorted(max(abs(p[0] - med[0]), abs(p[1] - med[1]), abs(p[2] - med[2]))
                    for p in ring)
    p90 = spread[min(len(spread) - 1, int(round(0.90 * (len(spread) - 1))))]
    threshold = max(24.0, float(p90) + 12.0)

    background = Image.new("RGB", img.size, med)
    diff = ImageChops.difference(img, background)
    rr, gg, bb = diff.split()
    channel_max = ImageChops.lighter(ImageChops.lighter(rr, gg), bb)
    mask = channel_max.point(lambda p: 255 if p > threshold else 0)

    density = float(ImageStat.Stat(mask).mean[0]) / 255.0
    box = mask.getbbox()
    if box is None:
        return ((0.0, 0.0, 0.0, 0.0), 0.0, B.rgb_to_hex(med))
    left, top, right, bottom = box
    frac = (left / float(w), top / float(h), right / float(w), bottom / float(h))
    return (frac, density, B.rgb_to_hex(med))


def measure_loudness(path, target_lufs):
    # type: (str, float) -> Optional[Dict[str, float]]
    """EBU R128 measurement via ffmpeg's loudnorm analysis pass.

    Returns {'input_i','input_tp','input_lra','input_thresh'} in LUFS/dBTP, or
    None when the filter produced no report.
    """
    rc, _out, err = _run([
        "ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", path,
        "-map", "0:a:0",
        "-af", "loudnorm=I=%.1f:TP=%.1f:LRA=11:print_format=json"
               % (float(target_lufs), TRUE_PEAK_CEILING_DBTP),
        "-f", "null", "-",
    ], timeout=900)
    text = _decode(err)
    blocks = [m for m in re.findall(r"\{[^{}]*\}", text) if "input_i" in m]
    if not blocks:
        if rc != 0:
            raise ValidatorError("ffmpeg loudness analysis failed: %s"
                                 % (text.strip()[-500:] or "rc=%d" % rc))
        return None
    try:
        raw = json.loads(blocks[-1])
    except ValueError:
        return None
    out = {}  # type: Dict[str, float]
    for key in ("input_i", "input_tp", "input_lra", "input_thresh"):
        value = str(raw.get(key, "")).strip()
        if not value:
            continue
        try:
            out[key] = float(value)
        except ValueError:
            out[key] = float("-inf") if value.lstrip("-").lower().startswith("inf") else 0.0
    return out or None


def detect_silence(path, noise_db=-50.0, min_dur=0.5):
    # type: (str, float, float) -> List[Tuple[float, Optional[float]]]
    """[(silence_start, silence_end or None), ...] from ffmpeg's silencedetect."""
    rc, _out, err = _run([
        "ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", path,
        "-map", "0:a:0",
        "-af", "silencedetect=noise=%.1fdB:d=%.2f" % (float(noise_db), float(min_dur)),
        "-f", "null", "-",
    ], timeout=900)
    text = _decode(err)
    if rc != 0 and "silence_start" not in text:
        raise ValidatorError("ffmpeg silence analysis failed: %s"
                             % (text.strip()[-500:] or "rc=%d" % rc))
    spans = []  # type: List[Tuple[float, Optional[float]]]
    pending = None  # type: Optional[float]
    for token, value in re.findall(r"silence_(start|end):\s*(-?[\d.]+)", text):
        seconds = _num(value)
        if seconds is None:
            continue
        if token == "start":
            if pending is not None:
                spans.append((pending, None))
            pending = seconds
        else:
            spans.append((pending if pending is not None else 0.0, seconds))
            pending = None
    if pending is not None:
        spans.append((pending, None))
    return spans


def extract_embedded_srt(path):
    # type: (str) -> str
    """Text of the first embedded subtitle stream, converted to SubRip. '' on failure."""
    rc, out, _err = _run([
        "ffmpeg", "-nostdin", "-v", "error", "-i", path,
        "-map", "0:s:0", "-f", "srt", "-",
    ], timeout=300)
    if rc != 0:
        return ""
    return _decode(out)


# ---------------------------------------------------------------------------
# SRT
# ---------------------------------------------------------------------------

_SRT_TIME_RE = re.compile(r"(\d{1,3}):(\d{2}):(\d{2})[,.](\d{1,3})")
_SRT_MARKUP_RE = re.compile(r"</?[A-Za-z][^>]*>|\{\\[^}]*\}")


def _srt_seconds(groups):
    # type: (Sequence[str]) -> float
    hh, mm, ss, ms = groups
    millis = (ms + "000")[:3]
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(millis) / 1000.0


def parse_srt(text):
    # type: (str) -> List[Dict[str, Any]]
    """Parse SubRip text into [{index, start, end, lines, text}], tolerant of
    missing indices, '.' millisecond separators and CRLF."""
    if not text:
        return []
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    if body.startswith("\ufeff"):
        body = body[1:]
    cues = []  # type: List[Dict[str, Any]]
    for block in re.split(r"\n[ \t]*\n", body.strip()):
        lines = block.split("\n")
        timing_at = None
        for i, line in enumerate(lines):
            if "-->" in line:
                timing_at = i
                break
        if timing_at is None:
            continue
        stamps = _SRT_TIME_RE.findall(lines[timing_at])
        if len(stamps) < 2:
            continue
        index = None
        if timing_at > 0:
            m = re.match(r"^\s*(\d+)\s*$", lines[timing_at - 1])
            if m:
                index = int(m.group(1))
        payload = [ln.strip() for ln in lines[timing_at + 1:]]
        while payload and not payload[-1]:
            payload.pop()
        visible = [_SRT_MARKUP_RE.sub("", ln).strip() for ln in payload]
        visible = [ln for ln in visible if ln]
        cues.append({
            "index": index if index is not None else len(cues) + 1,
            "start": _srt_seconds(stamps[0]),
            "end": _srt_seconds(stamps[1]),
            "lines": visible,
            "text": " ".join(visible),
        })
    return cues


# ---------------------------------------------------------------------------
# timeline
# ---------------------------------------------------------------------------

def _segment_seconds(node, default):
    # type: (Any, float) -> float
    """Duration of an intro/outro declaration: bool, number or {'durationSec': n}."""
    if node is None or node is False:
        return 0.0
    if node is True:
        return float(default)
    if isinstance(node, dict):
        value = _num(_first(node, ("durationSec", "duration", "sec", "seconds")), None)
        if value is None:
            enabled = node.get("enabled")
            return float(default) if enabled is not False else 0.0
        return float(value)
    value = _num(node, None)
    return float(value) if value is not None else 0.0


def read_timeline_json(path):
    # type: (str) -> Dict[str, Any]
    """Read and sanity-check a timeline file. Raises ValidatorError on anything odd."""
    if not os.path.isfile(path):
        raise ValidatorError("timeline not found: %s" % path)
    try:
        with open(path, "r") as fh:
            raw = json.load(fh)
    except (IOError, OSError) as exc:
        raise ValidatorError("cannot read timeline %s: %s" % (path, exc))
    except ValueError as exc:
        raise ValidatorError("timeline %s is not valid JSON: %s" % (path, exc))
    if not isinstance(raw, dict):
        raise ValidatorError("timeline %s must contain a JSON object" % path)
    return raw


def load_timeline(path, brand, raw=None):
    # type: (str, Dict[str, Any], Optional[Dict[str, Any]]) -> Dict[str, Any]
    """Normalise a Video IR or a rendered timeline sidecar into one shape.

    Accepts FROZEN CONTRACT B (scenes carry holdSec) and builder-written
    timelines (scenes carry explicit start/end seconds), wrapped or bare.
    Pass ``raw`` to reuse an already-parsed document instead of re-reading.
    """
    if raw is None:
        raw = read_timeline_json(path)

    doc = raw
    for key in ("timeline", "video", "ir"):
        inner = raw.get(key)
        if isinstance(inner, dict) and ("scenes" in inner or "segments" in inner):
            doc = inner
            break

    video_cfg = brand.get("video") or {}
    intro_default = _num((video_cfg.get("intro") or {}).get("durationSec"), 0.0) or 0.0
    outro_default = _num((video_cfg.get("outro") or {}).get("durationSec"), 0.0) or 0.0

    # Scene rows come from `scenes` (contract B), `segments`, or `elements`
    # (the sidecar build_video.py writes, where intro and outro are rows too).
    scenes_raw = None
    element_span = None  # type: Optional[float]
    element_bookends = {}  # type: Dict[str, float]
    for key in ("scenes", "segments"):
        if isinstance(doc.get(key), list):
            scenes_raw = doc[key]
            break
    if scenes_raw is None and isinstance(doc.get("elements"), list):
        rows = [r for r in doc["elements"] if isinstance(r, dict)]
        scenes_raw = [r for r in rows
                      if str(r.get("kind") or "scene").strip().lower() == "scene"]
        for row in rows:
            kind = str(row.get("kind") or "").strip().lower()
            if kind in ("intro", "outro"):
                seconds = _num(_first(row, ("durationSec", "duration")), None)
                if seconds is not None:
                    element_bookends[kind] = element_bookends.get(kind, 0.0) + float(seconds)
            end = _num(_first(row, ("endSec", "end")), None)
            if end is not None and (element_span is None or end > element_span):
                element_span = float(end)
    if scenes_raw is None:
        scenes_raw = []

    if doc.get("intro") is not None:
        intro_sec = _segment_seconds(doc.get("intro"), intro_default)
    else:
        intro_sec = element_bookends.get("intro", 0.0)
    if doc.get("outro") is not None:
        outro_sec = _segment_seconds(doc.get("outro"), outro_default)
    else:
        outro_sec = element_bookends.get("outro", 0.0)

    scenes = []  # type: List[Dict[str, Any]]
    explicit = False
    for i, raw_scene in enumerate(scenes_raw):
        if not isinstance(raw_scene, dict):
            continue
        start = _num(_first(raw_scene, ("startSec", "start", "tStart", "inSec",
                                        "offsetSec", "t0", "atSec")), None)
        end = _num(_first(raw_scene, ("endSec", "end", "tEnd", "outSec", "t1")), None)
        dur = _num(_first(raw_scene, ("durationSec", "duration", "holdSec", "hold",
                                      "lengthSec", "secs")), None)
        if start is not None:
            explicit = True
        if dur is None and start is not None and end is not None:
            dur = max(0.0, end - start)
        scenes.append({
            "n": i + 1,
            "id": str(raw_scene.get("id") or "s%d" % (i + 1)),
            "role": str(raw_scene.get("role") or "").strip().lower(),
            "start": start,
            "end": end,
            "duration": dur,
            "vo": str(raw_scene.get("vo") or ""),
            "caption": str(raw_scene.get("caption") or ""),
        })

    # Fill in timings. Explicit starts win; otherwise lay the scenes out
    # end to end behind the intro.
    cursor = intro_sec
    for scene in scenes:
        if scene["start"] is None:
            scene["start"] = cursor
        if scene["duration"] is None:
            scene["duration"] = 0.0
        if scene["end"] is None:
            scene["end"] = scene["start"] + scene["duration"]
        cursor = scene["end"]

    if explicit and scenes and intro_sec > 0.0:
        earliest = min(s["start"] for s in scenes)
        if earliest < intro_sec * 0.5:
            # The builder wrote scene-relative times; shift them behind the intro.
            for scene in scenes:
                scene["start"] += intro_sec
                scene["end"] += intro_sec

    # A storyline block can still carry the arc when no scene rows survived.
    storyline = doc.get("storyline") if isinstance(doc.get("storyline"), dict) else {}
    if not scenes and isinstance(storyline.get("roles"), list):
        for i, role in enumerate(storyline["roles"]):
            if not str(role or "").strip():
                continue
            scenes.append({"n": i + 1, "id": "s%d" % (i + 1),
                           "role": str(role).strip().lower(),
                           "start": 0.0, "end": 0.0, "duration": 0.0,
                           "vo": "", "caption": ""})

    # `measuredDurationSec` is deliberately not consulted: it is read back off the
    # render, so checking the render against it would always agree.
    total_explicit = _num(_first(doc, ("totalSec", "totalDurationSec", "total",
                                       "durationSec", "renderedSec")), None)
    if total_explicit is None:
        meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
        total_explicit = _num(_first(meta, ("totalSec", "renderedDurationSec")), None)

    audio = doc.get("audio") if isinstance(doc.get("audio"), dict) else {}

    music_enabled = None
    for node in (doc.get("music"), audio.get("music")):
        if isinstance(node, dict) and "enabled" in node:
            music_enabled = bool(node.get("enabled"))
            break

    vo_enabled = None
    vo_node = audio.get("voiceover")
    if isinstance(vo_node, dict) and "enabled" in vo_node:
        vo_enabled = bool(vo_node.get("enabled"))
    elif scenes:
        vo_enabled = any(s["vo"].strip() for s in scenes)

    captions = doc.get("captions") if isinstance(doc.get("captions"), dict) else {}
    sidecar = captions.get("sidecar")
    caption_sidecar = None
    if isinstance(sidecar, str) and sidecar.strip():
        candidate = sidecar if os.path.isabs(sidecar) else os.path.join(
            os.path.dirname(os.path.abspath(path)), sidecar)
        caption_sidecar = candidate if os.path.isfile(candidate) else None

    return {
        "path": path,
        "raw": raw,
        "brand": str(doc.get("brand") or raw.get("brand") or "") or None,
        "kind": str(doc.get("kind") or raw.get("kind") or "") or None,
        "scenes": scenes,
        "intro": intro_sec > 0.0,
        "outro": outro_sec > 0.0,
        "introSec": intro_sec,
        "outroSec": outro_sec,
        "explicitTiming": explicit,
        "totalExplicit": total_explicit,
        "elementSpan": element_span,
        "musicEnabled": music_enabled,
        "voEnabled": vo_enabled,
        "captionSidecar": caption_sidecar,
        "targetSec": _num(((doc.get("meta") or {}) if isinstance(doc.get("meta"), dict)
                           else {}).get("durationTargetSec"), None),
    }


def timeline_duration_window(timeline, brand):
    # type: (Dict[str, Any], Dict[str, Any]) -> Optional[Tuple[float, float, str]]
    """(min_seconds, max_seconds, derivation) the render should land inside."""
    if timeline is None:
        return None
    transition = _num(((brand.get("video") or {}).get("transition") or {}).get("durationSec"),
                      0.0) or 0.0

    if timeline["totalExplicit"] is not None:
        total = float(timeline["totalExplicit"])
        return (total, total, "the timeline's own total of %.2fs" % total)

    scenes = timeline["scenes"]
    intro = float(timeline["introSec"])
    outro = float(timeline["outroSec"])

    if timeline.get("elementSpan") is not None:
        span = float(timeline["elementSpan"])
        return (span, span, "the last timeline element ends at %.2fs" % span)

    if scenes and not any(float(s["duration"]) > 0.0 for s in scenes) \
            and timeline["targetSec"] is None:
        return None  # role-only stubs carry no timing to check against

    if scenes and timeline["explicitTiming"]:
        longest = max(float(s["end"]) for s in scenes)
        upper = longest + outro
        lower = upper - (transition if outro > 0.0 else 0.0)
        return (lower, upper, "last scene ends %.2fs + outro %.2fs (crossfade %.2fs)"
                % (longest, outro, transition))

    if not scenes:
        target = timeline["targetSec"]
        if target is None:
            return None
        return (float(target), float(target), "meta.durationTargetSec %.2fs" % float(target))

    segments = ([intro] if intro > 0.0 else []) \
        + [float(s["duration"]) for s in scenes] \
        + ([outro] if outro > 0.0 else [])
    upper = sum(segments)
    lower = upper - max(0, len(segments) - 1) * transition
    return (lower, upper,
            "%d segment(s) summing to %.2fs, less up to %d crossfade(s) of %.2fs"
            % (len(segments), upper, max(0, len(segments) - 1), transition))


def narration_windows(timeline):
    # type: (Optional[Dict[str, Any]]) -> List[Tuple[float, float]]
    """Time ranges during which the timeline says a voiceover is playing."""
    if not timeline:
        return []
    out = []
    for scene in timeline["scenes"]:
        if scene["vo"].strip():
            start = float(scene["start"])
            end = float(scene["end"])
            if end > start:
                out.append((start, end))
    return out


def _overlap(a, b):
    # type: (Tuple[float, float], Tuple[float, float]) -> float
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


# ---------------------------------------------------------------------------
# checks - container / streams
# ---------------------------------------------------------------------------

def check_format(report, fmt, unknown):
    # type: (B.Report, Dict[str, Any], Optional[Dict[str, Any]]) -> None
    """VIDEO.FORMAT_UNKNOWN - a delivery format the brand does not declare.

    An error, not a warning. A name nobody recognises means the render was
    judged against the wrong canvas, and every geometric finding below it is
    answering a different question than the one that was asked.
    """
    if not unknown:
        return
    declared = unknown.get("declared") or []
    report.add("VIDEO.FORMAT_UNKNOWN", severity="error", where="delivery format",
               found="%r, named by %s" % (unknown["requested"], unknown["source"]),
               expected=("one of: %s" % ", ".join(declared)) if declared else
                        "a brand that declares video.formats",
               rule="A delivery format must be declared in brand.video.formats before "
                    "anything can be validated against it. Falling back to the default "
                    "silently is how a whole night's reels come out landscape.",
               fix=("Use one of %s, or add %r to brand.video.formats with its own w, h, "
                    "safeMarginPct and captionSizePt."
                    % (", ".join(declared), unknown["requested"])) if declared else
                   ("Add a video.formats block to the brand, declaring %r with its w, h, "
                    "safeMarginPct and captionSizePt." % unknown["requested"]))


def check_streams(report, info, brand, path, fmt):
    # type: (B.Report, Dict[str, Any], Dict[str, Any], str, Dict[str, Any]) -> Dict[str, Any]
    """VIDEO.RESOLUTION, VIDEO.FPS, VIDEO.CODEC. Returns the chosen video stream."""
    video_cfg = brand.get("video") or {}
    vstreams = streams_of(info, "video")
    if not vstreams:
        raise ValidatorError("%s carries no video stream - this is not a video file" % path)
    vstream = vstreams[0]

    # The RESOLVED delivery format, never the brand default: a vertical reel is
    # a correct 1080x1920 render, not a failed 1920x1080 one.
    want_w = _num(fmt.get("width"))
    want_h = _num(fmt.get("height"))
    got_w = _num(vstream.get("width"))
    got_h = _num(vstream.get("height"))
    if want_w and want_h and got_w and got_h:
        if int(got_w) != int(want_w) or int(got_h) != int(want_h):
            report.add("VIDEO.RESOLUTION", severity="error", where="video stream",
                       found="%dx%d" % (int(got_w), int(got_h)),
                       expected="%dx%d - the %s format, taken from %s"
                                % (int(want_w), int(want_h), format_label(fmt),
                                   fmt.get("source") or "the brand"),
                       rule="Every brand film is mastered at the delivery format it was "
                            "built for. A brand may ship several (landscape, square, "
                            "vertical); this render is judged against the one it names.",
                       fix="Re-render at %dx%d with build_video.py --format %s, or add a "
                           "scale=%d:%d:flags=lanczos pass. Do not upscale a smaller "
                           "master - re-render from source."
                           % (int(want_w), int(want_h),
                              fmt.get("name") or "<name>", int(want_w), int(want_h)))

    want_fps = _num(video_cfg.get("fps"))
    got_fps = parse_rate(vstream.get("avg_frame_rate")) or parse_rate(vstream.get("r_frame_rate"))
    if want_fps and got_fps is not None:
        if abs(got_fps - want_fps) > 0.5:
            report.add("VIDEO.FPS", severity="error", where="video stream",
                       found="%.3f fps" % got_fps,
                       expected="%.3f fps (tolerance 0.5)" % want_fps,
                       rule="Frame rate is fixed per brand so cuts, holds and captions "
                            "land on the same grid across every film.",
                       fix="Re-encode with -r %g. Converting an existing master with "
                           "-vf fps=%g is acceptable for whole-number conversions."
                           % (want_fps, want_fps))

    want_codec = str(video_cfg.get("vcodec") or "").strip()
    got_codec = str(vstream.get("codec_name") or "").strip().lower()
    if want_codec:
        accepted = CODEC_ALIASES.get(want_codec.lower(), (want_codec.lower(),))
        if got_codec and got_codec not in accepted:
            report.add("VIDEO.CODEC", severity="warn", where="video stream",
                       found=got_codec,
                       expected="%s (ffprobe reports it as one of: %s)"
                                % (want_codec, ", ".join(accepted)),
                       rule="The brand ships a single video codec so playback is "
                            "predictable in browsers, Keynote and PowerPoint.",
                       fix="Re-encode with -c:v %s." % want_codec)

    pix_fmt = str(vstream.get("pix_fmt") or "").strip().lower()
    if pix_fmt and pix_fmt != "yuv420p":
        report.add("VIDEO.CODEC", severity="warn", where="video stream / pix_fmt",
                   found=pix_fmt, expected="yuv420p",
                   rule="Only yuv420p is decodable everywhere; 4:2:2, 4:4:4 and 10-bit "
                        "chroma fail silently in Office and some browsers.",
                   fix="Re-encode with -pix_fmt yuv420p.")

    want_container = str(video_cfg.get("container") or "").strip().lower()
    format_name = str((info.get("format") or {}).get("format_name") or "").lower()
    if want_container and format_name and want_container not in format_name.split(","):
        report.add("VIDEO.CODEC", severity="warn", where="container",
                   found=format_name, expected=want_container,
                   rule="The brand delivers a single container format.",
                   fix="Remux with: ffmpeg -i <in> -c copy <out>.%s" % want_container)

    want_acodec = str(video_cfg.get("acodec") or "").strip()
    astreams = streams_of(info, "audio")
    if want_acodec and astreams:
        got_acodec = str(astreams[0].get("codec_name") or "").strip().lower()
        accepted_a = CODEC_ALIASES.get(want_acodec.lower(), (want_acodec.lower(),))
        if got_acodec and got_acodec not in accepted_a:
            report.add("VIDEO.CODEC", severity="warn", where="audio stream",
                       found=got_acodec, expected=want_acodec,
                       rule="The brand ships a single audio codec.",
                       fix="Re-encode with -c:a %s -b:a 192k." % want_acodec)

    return vstream


def check_duration(report, info, timeline, brand):
    # type: (B.Report, Dict[str, Any], Optional[Dict[str, Any]], Dict[str, Any]) -> None
    """VIDEO.DURATION - the render must match what the timeline asked for."""
    window = timeline_duration_window(timeline, brand) if timeline else None
    if window is None:
        return
    lower, upper, derivation = window
    actual = container_duration(info)
    if actual <= 0.0:
        return
    if actual < lower - DURATION_TOLERANCE_SEC:
        drift = lower - actual
    elif actual > upper + DURATION_TOLERANCE_SEC:
        drift = actual - upper
    else:
        return
    report.add("VIDEO.DURATION", severity="warn", where="container",
               found="%.2fs (%.2fs outside the timeline window)" % (actual, drift),
               expected="%.2fs to %.2fs - %s" % (lower, upper, derivation),
               rule="The rendered film must match the timeline it was built from, "
                    "within %.1fs." % DURATION_TOLERANCE_SEC,
               fix="Re-render from the timeline. If the drift is deliberate, update the "
                   "scene holdSec values so the timeline stays the source of truth.")


# ---------------------------------------------------------------------------
# checks - frames
# ---------------------------------------------------------------------------

def _dominant_set(colors):
    # type: (Sequence[Tuple[str, float]]) -> List[Tuple[str, float]]
    """The largest colour plus anything else holding a dominant share."""
    if not colors:
        return []
    out = [colors[0]]
    for hex_color, share in colors[1:]:
        if share >= INTRO_MIN_SHARE:
            out.append((hex_color, share))
    return out


def _branded_frame(image_path, heroes):
    # type: (str, Dict[str, str]) -> Optional[Tuple[str, float, str, float]]
    """(hex, share, hero_token, delta_e) when a dominant colour is a brand hero."""
    best = None
    for hex_color, share in _dominant_set(dominant_colors(image_path)):
        token, hero_hex, dist = B.nearest_token(hex_color, heroes)
        if hero_hex is None:
            continue
        if dist <= INTRO_DELTA_E and (best is None or dist < best[3]):
            best = (hex_color, share, "%s %s" % (token, hero_hex), dist)
    return best


def check_bookend(report, path, workdir, brand, duration, timeline, which):
    # type: (B.Report, str, str, Dict[str, Any], float, Optional[Dict[str, Any]], str) -> None
    """VIDEO.NO_INTRO / VIDEO.NO_OUTRO."""
    video_cfg = brand.get("video") or {}
    cfg = video_cfg.get(which) or {}
    wanted_sec = _num(cfg.get("durationSec"), 0.0) or 0.0
    if timeline is not None:
        if not timeline[which]:
            return
        wanted_sec = float(timeline["%sSec" % which]) or wanted_sec
    if wanted_sec <= 0.0 or duration <= 0.0:
        return

    span = min(wanted_sec, duration)
    if which == "intro":
        t0, t1 = 0.0, span
    else:
        t0, t1 = max(0.0, duration - span), duration

    heroes = hero_colors(brand)
    if not heroes:
        return

    hit = None
    checked = []
    for i, when in enumerate(_sample_times(t0, t1, 4)):
        frame = os.path.join(workdir, "%s-%02d.png" % (which, i))
        if not extract_frame(path, when, frame):
            continue
        checked.append(when)
        found = _branded_frame(frame, heroes)
        if found is not None:
            hit = (when, found)
            break
    if hit is not None or not checked:
        return

    vid = "VIDEO.NO_INTRO" if which == "intro" else "VIDEO.NO_OUTRO"
    style = str(cfg.get("style") or "").strip()
    report.add(vid, severity="warn",
               where="%s window %.2fs-%.2fs" % (which, t0, t1),
               found="no sampled frame (%s) is dominated by a brand hero colour"
                     % ", ".join(_ts(t) for t in checked),
               expected="a dominant colour within delta E %.0f of %s"
                        % (INTRO_DELTA_E,
                           ", ".join("%s (%s)" % (h, t) for h, t in sorted(heroes.items()))),
               rule="Every brand film opens and closes on the brand: a gradient field "
                    "carrying the logo lockup%s."
                    % ((", style '%s'" % style) if style else ""),
               fix="Render the %s segment (%.2fs) from the brand template and concatenate "
                   "it, or set %s to false in the video IR if this film genuinely ships "
                   "without one." % (which, wanted_sec, which))


def check_frames(report, path, workdir, brand, duration, frames, fmt):
    # type: (B.Report, str, str, Dict[str, Any], float, int, Dict[str, Any]) -> None
    """VIDEO.SAFE_MARGIN and VIDEO.OFF_PALETTE over evenly spaced frames."""
    if frames <= 0 or duration <= 0.0:
        return

    palette = B.palette_index(brand)
    # The format's own safe margin: a reel is held to 8%, a square post to 6%,
    # a landscape master to 5%.
    margin_pct = _num(fmt.get("safeMarginPct"),
                      _num((brand.get("video") or {}).get("safeMarginPct"), 0.0) or 0.0) or 0.0
    margin = margin_pct / 100.0

    observations = []  # type: List[Tuple[float, str, float]]
    margin_hits = []  # type: List[Dict[str, Any]]
    for i, when in enumerate(_sample_times(0.0, duration, frames)):
        frame = os.path.join(workdir, "frame-%03d.png" % i)
        if not extract_frame(path, when, frame):
            continue

        # --- VIDEO.OFF_PALETTE -------------------------------------------
        if palette:
            for hex_color, share in dominant_colors(frame):
                if share < OFF_PALETTE_MIN_SHARE:
                    continue
                if is_neutral(hex_color) or is_skin_tone(hex_color):
                    continue
                _token, _thex, dist = B.nearest_token(hex_color, palette)
                if dist > OFF_PALETTE_DELTA_E:
                    observations.append((when, hex_color, share))

        # --- VIDEO.SAFE_MARGIN -------------------------------------------
        if margin > 0.0:
            measured = content_bbox(frame)
            if measured is None:
                continue
            (left, top, right, bottom), density, bg_hex = measured
            if density <= 0.0:
                continue
            if density >= SAFE_MARGIN_FULLBLEED_DENSITY:
                continue  # full-bleed photography or a gradient wash
            tol = SAFE_MARGIN_TOLERANCE
            edges = {}
            if left < margin - tol:
                edges["left"] = left
            if top < margin - tol:
                edges["top"] = top
            if right > 1.0 - margin + tol:
                edges["right"] = 1.0 - right
            if bottom > 1.0 - margin + tol:
                edges["bottom"] = 1.0 - bottom
            if edges:
                margin_hits.append({"when": when, "edges": edges,
                                    "density": density, "bg": bg_hex})

    _report_safe_margin(report, margin_hits, margin, margin_pct, fmt)
    _report_off_palette(report, observations, palette)


def _report_safe_margin(report, hits, margin, margin_pct, fmt):
    # type: (B.Report, Sequence[Dict[str, Any]], float, float, Dict[str, Any]) -> None
    """Fold per-frame safe-margin breaches into one violation per edge signature."""
    grouped = {}  # type: Dict[str, Dict[str, Any]]
    order = []  # type: List[str]
    for hit in hits:
        key = "+".join(sorted(hit["edges"].keys()))
        bucket = grouped.get(key)
        if bucket is None:
            bucket = {"edges": dict(hit["edges"]), "times": [], "bg": hit["bg"],
                      "density": hit["density"]}
            grouped[key] = bucket
            order.append(key)
        bucket["times"].append(hit["when"])
        for edge, inset in hit["edges"].items():
            if inset < bucket["edges"].get(edge, 1.0):
                bucket["edges"][edge] = inset
                bucket["bg"] = hit["bg"]
                bucket["density"] = hit["density"]

    for key in order:
        bucket = grouped[key]
        worst = "; ".join("%s %s from the edge" % (edge, _pct(bucket["edges"][edge]))
                          for edge in sorted(bucket["edges"].keys()))
        stamps, extra = _cap(sorted(bucket["times"]), 5)
        report.add("VIDEO.SAFE_MARGIN", severity="warn",
                   where="frame @ %s%s" % (", ".join(_ts(t) for t in stamps),
                                           _suffix(extra)),
                   found="content crosses the %s band in %d sampled frame(s); worst case "
                         "%s (background %s, %s of the frame is content)"
                         % (key.replace("+", "/"), len(bucket["times"]), worst,
                            bucket["bg"], _pct(bucket["density"])),
                   expected="all content inside the central %s, i.e. a %.1f%% margin on "
                            "every edge - the %s format's safe area"
                            % (_pct(1.0 - 2.0 * margin), margin_pct, format_label(fmt)),
                   rule="Content outside the safe area is cropped by broadcast, LinkedIn "
                        "and in-room 4:3 projection. The margin is per delivery format. "
                        "Frames that are entirely content (full-bleed photography, "
                        "gradient washes) are exempt.",
                   fix="Inset the offending element to at least %.1f%% from the edge, or "
                       "scale the whole composition to %s and centre it."
                       % (margin_pct, _pct(1.0 - 2.0 * margin)))


def _report_off_palette(report, observations, palette):
    # type: (B.Report, Sequence[Tuple[float, str, float]], Dict[str, str]) -> None
    """Fold per-frame off-palette hits into one violation per distinct colour."""
    clusters = []  # type: List[Dict[str, Any]]
    for when, hex_color, share in observations:
        placed = False
        for cluster in clusters:
            if B.delta_e(cluster["key"], hex_color) <= OFF_PALETTE_CLUSTER_DELTA_E:
                cluster["times"].append(when)
                if share > cluster["share"]:
                    cluster["share"] = share
                    cluster["rep"] = hex_color
                placed = True
                break
        if not placed:
            clusters.append({"key": hex_color, "rep": hex_color,
                             "share": share, "times": [when]})

    clusters.sort(key=lambda c: (-len(c["times"]), -c["share"]))
    shown, suppressed = _cap(clusters, OFF_PALETTE_MAX_CLUSTERS)
    for cluster in shown:
        token, thex, dist = B.nearest_token(cluster["rep"], palette)
        stamps, extra = _cap(sorted(cluster["times"]), 5)
        report.add("VIDEO.OFF_PALETTE", severity="warn",
                   where="frame @ %s%s" % (", ".join(_ts(t) for t in stamps),
                                           _suffix(extra)),
                   found="%s holds up to %s of the frame in %d sampled frame(s)"
                         % (cluster["rep"], _pct(cluster["share"]), len(cluster["times"])),
                   expected="a brand colour - nearest is %s %s at delta E %.1f "
                            "(limit %.0f)" % (token, thex, dist, OFF_PALETTE_DELTA_E),
                   rule="Large flat areas of non-brand colour read as another company's "
                        "film. Greys, near-white, near-black and skin tones are exempt; "
                        "photography legitimately trips this, so confirm the timestamp "
                        "before recolouring.",
                   fix="If it is a graphic, recolour it to %s. If it is photography or "
                       "licensed footage, no change is needed - clear the finding by hand."
                       % thex)
    if suppressed:
        report.add("VIDEO.OFF_PALETTE", severity="warn", where="frame sampling",
                   found="%d further off-palette colour(s) not listed" % suppressed,
                   expected="at most %d distinct off-palette colours in a report"
                            % OFF_PALETTE_MAX_CLUSTERS,
                   rule="Reporting is capped so one photographic sequence cannot bury "
                        "the rest of the findings.",
                   fix="Fix the listed colours and re-run, or re-run with a smaller "
                       "--frames to narrow the sample.")


# ---------------------------------------------------------------------------
# checks - audio
# ---------------------------------------------------------------------------

def check_audio(report, path, info, brand, timeline, duration):
    # type: (B.Report, str, Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]], float) -> None
    """AUDIO.MISSING, AUDIO.LOUDNESS, AUDIO.CLIPPING, AUDIO.SILENCE."""
    video_cfg = brand.get("video") or {}
    music_cfg = video_cfg.get("music") or {}
    vo_cfg = video_cfg.get("voiceover") or {}

    music_on = bool(music_cfg.get("enabled"))
    vo_on = bool(vo_cfg.get("enabled"))
    if timeline is not None:
        if timeline["musicEnabled"] is not None:
            music_on = timeline["musicEnabled"]
        if timeline["voEnabled"] is not None:
            vo_on = timeline["voEnabled"]

    astreams = streams_of(info, "audio")
    if not astreams:
        if music_on or vo_on:
            wants = ", ".join([w for w, on in (("voiceover", vo_on), ("music", music_on)) if on])
            report.add("AUDIO.MISSING", severity="error", where="container",
                       found="no audio stream",
                       expected="one %s audio stream carrying %s"
                                % (video_cfg.get("acodec") or "aac", wants),
                       rule="A film that declares narration or music must ship with it; "
                            "a silent master is a failed render, not a style choice.",
                       fix="Re-run the build so the voiceover and music mix is muxed in, "
                           "or set music.enabled/voiceover to false in the video IR.")
        return

    target = _num(vo_cfg.get("targetLufs")) if vo_on else None
    if target is None:
        target = _num(music_cfg.get("targetLufs"))
    if target is None:
        target = -16.0
    target_label = "voiceover" if vo_on and _num(vo_cfg.get("targetLufs")) is not None else "music"

    measured = measure_loudness(path, target)
    if measured is not None:
        integrated = measured.get("input_i")
        if integrated is not None:
            if integrated == float("-inf"):
                report.add("AUDIO.LOUDNESS", severity="warn", where="audio stream",
                           found="-inf LUFS (the audio stream is silent)",
                           expected="%.1f LUFS +/- %.1f LU" % (target, LOUDNESS_TOLERANCE_LU),
                           rule="Integrated loudness is measured to EBU R128 so every "
                                "brand film plays back at the same level.",
                           fix="The mix is empty. Re-run the build and confirm the "
                               "voiceover and music inputs actually resolved.")
            elif abs(integrated - target) > LOUDNESS_TOLERANCE_LU:
                delta = integrated - target
                report.add("AUDIO.LOUDNESS", severity="warn", where="audio stream",
                           found="%.1f LUFS (%+.1f LU against the %s target)"
                                 % (integrated, delta, target_label),
                           expected="%.1f LUFS +/- %.1f LU" % (target, LOUDNESS_TOLERANCE_LU),
                           rule="Integrated loudness is measured to EBU R128 so every "
                                "brand film plays back at the same level.",
                           fix="Re-master with: ffmpeg -i <in> -af "
                               "loudnorm=I=%.1f:TP=%.1f:LRA=11 -c:v copy <out>"
                               % (target, TRUE_PEAK_CEILING_DBTP))

        true_peak = measured.get("input_tp")
        if true_peak is not None and true_peak != float("-inf"):
            if true_peak > TRUE_PEAK_CEILING_DBTP:
                report.add("AUDIO.CLIPPING", severity="warn", where="audio stream",
                           found="%.2f dBTP true peak" % true_peak,
                           expected="at or below %.1f dBTP" % TRUE_PEAK_CEILING_DBTP,
                           rule="Inter-sample peaks above -1 dBTP clip on consumer "
                                "playback and on every lossy transcode a platform applies.",
                           fix="Add a limiter or re-master with "
                               "loudnorm=I=%.1f:TP=%.1f:LRA=11."
                               % (target, TRUE_PEAK_CEILING_DBTP))

    spans = detect_silence(path)
    if spans and duration > 0.0:
        for start, end in spans:
            stop = duration if end is None else end
            length = max(0.0, stop - start)
            if length <= SILENCE_LIMIT_SEC:
                continue
            if start <= 0.10:
                report.add("AUDIO.SILENCE", severity="info", where="audio stream / head",
                           found="%.2fs of silence before the first sound" % length,
                           expected="at most %.1fs of leading silence" % SILENCE_LIMIT_SEC,
                           rule="Dead air at the head reads as a broken file in autoplay "
                                "feeds, where the first two seconds are all a viewer gets.",
                           fix="Trim the head, or start the music bed under the intro so "
                               "the film has a voice from frame one.")
            elif end is None or stop >= duration - 0.10:
                report.add("AUDIO.SILENCE", severity="info", where="audio stream / tail",
                           found="%.2fs of silence after the last sound" % length,
                           expected="at most %.1fs of trailing silence" % SILENCE_LIMIT_SEC,
                           rule="A long silent tail makes the film feel unfinished and "
                                "inflates the runtime reported to the client.",
                           fix="Trim the tail, or extend the music bed with the brand "
                               "fade-out of %.1fs over the outro."
                               % (_num(music_cfg.get("fadeOutSec"), 2.0) or 2.0))


# ---------------------------------------------------------------------------
# checks - captions
# ---------------------------------------------------------------------------

def caption_line_budget(fmt, declared_max):
    # type: (Dict[str, Any], int) -> Tuple[int, Optional[str]]
    """(effective max chars per line, why) for the resolved delivery format.

    The brand states one maxCharsPerLine, but a line is only as long as the
    format's safe width can hold at the format's captionSizePt. A vertical reel
    is half as wide as a landscape master and sets its captions larger, so the
    brand's own number can stop being physically achievable. Returns the tighter
    of the two, and the derivation when the format is what bound it.

    The estimate is deliberately generous (CAPTION_ADVANCE_EM), so it never
    tightens a format whose captions genuinely fit.
    """
    width = _num(fmt.get("width"), 0) or 0
    size = _num(fmt.get("captionSizePt"), 0) or 0
    margin = _num(fmt.get("safeMarginPct"), 0) or 0
    if width <= 0 or size <= 0:
        return (declared_max, None)
    safe_width = width * (1.0 - 2.0 * margin / 100.0)
    fits = int(safe_width / (size * CAPTION_ADVANCE_EM))
    if fits >= declared_max or fits < 1:
        return (declared_max, None)
    return (fits, "%d characters at %gpt is all that fits inside the %s format's "
                  "%.0fpx safe width" % (fits, size, format_label(fmt), safe_width))


def check_captions(report, path, info, brand, srt_path, timeline, duration, fmt):
    # type: (B.Report, str, Dict[str, Any], Dict[str, Any], Optional[str], Optional[Dict[str, Any]], float, Dict[str, Any]) -> List[Dict[str, Any]]
    """CAPTION.* plus the content checks that run over caption text."""
    cfg = (brand.get("video") or {}).get("captions") or {}
    required = bool(cfg.get("required"))
    declared_max = int(_num(cfg.get("maxCharsPerLine"), 42) or 42)
    max_chars, chars_derivation = caption_line_budget(fmt, declared_max)
    max_lines = int(_num(cfg.get("maxLines"), 2) or 2)
    min_dur = _num(cfg.get("minDurationSec"), 1.2) or 1.2

    # Explicit --srt wins, then an embedded subtitle stream, then the sidecar the
    # timeline says it wrote.
    source = None
    text = ""
    if srt_path:
        if not os.path.isfile(srt_path):
            raise ValidatorError("caption file not found: %s" % srt_path)
        try:
            with open(srt_path, "r") as fh:
                text = fh.read()
        except (IOError, OSError) as exc:
            raise ValidatorError("cannot read %s: %s" % (srt_path, exc))
        source = srt_path
    elif streams_of(info, "subtitle"):
        text = extract_embedded_srt(path)
        source = "embedded subtitle stream 0"
    elif timeline is not None and timeline.get("captionSidecar"):
        sidecar = timeline["captionSidecar"]
        try:
            with open(sidecar, "r") as fh:
                text = fh.read()
        except (IOError, OSError) as exc:
            raise ValidatorError("cannot read %s (named by the timeline): %s"
                                 % (sidecar, exc))
        source = "%s (from the timeline)" % sidecar

    cues = parse_srt(text)

    if not cues:
        if required:
            if source is None:
                found = ("no --srt sidecar, no embedded subtitle stream, and no "
                         "readable captions.sidecar in the timeline")
            else:
                found = "%s produced no parseable cues" % source
            report.add("CAPTION.MISSING", severity="error", where="captions",
                       found=found,
                       expected="a %s sidecar, or an embedded subtitle stream"
                                % str(cfg.get("sidecar") or "srt"),
                       rule="Captions are mandatory for this brand: most social playback "
                            "is muted, and captions are an accessibility requirement.",
                       fix="Pass --srt <file.srt>, or mux the subtitles in with "
                           "-c:s mov_text. Burned-in captions cannot be validated - ship "
                           "the sidecar as well.")
        return []

    where_prefix = source or "captions"

    long_lines = []
    many_lines = []
    too_fast = []
    for cue in cues:
        for line_no, line in enumerate(cue["lines"], start=1):
            if len(line) > max_chars:
                long_lines.append((cue, line_no, line))
        if len(cue["lines"]) > max_lines:
            many_lines.append(cue)
        if (cue["end"] - cue["start"]) < min_dur - 1e-6:
            too_fast.append(cue)

    shown, extra = _cap(long_lines, MAX_CUE_VIOLATIONS)
    for i, (cue, line_no, line) in enumerate(shown):
        report.add("CAPTION.LINE_LEN", severity="warn",
                   where="cue %d line %d @ %s%s"
                         % (cue["index"], line_no, B.srt_timestamp(cue["start"]),
                            _tail_suffix(i, shown, extra)),
                   found="%d characters: %s" % (len(line), line),
                   expected="at most %d characters per line%s"
                            % (max_chars,
                               " (%s)" % chars_derivation if chars_derivation else ""),
                   rule="Long caption lines overflow the safe area and force the reader "
                        "to track across the whole frame. The limit is whichever is "
                        "tighter: the brand's maxCharsPerLine, or what the delivery "
                        "format's safe width holds at its caption size.",
                   fix="Break the line at a clause boundary, or split the cue in two.")

    shown, extra = _cap(many_lines, MAX_CUE_VIOLATIONS)
    for i, cue in enumerate(shown):
        report.add("CAPTION.LINE_COUNT", severity="warn",
                   where="cue %d @ %s%s" % (cue["index"], B.srt_timestamp(cue["start"]),
                                            _tail_suffix(i, shown, extra)),
                   found="%d lines" % len(cue["lines"]),
                   expected="at most %d lines per cue" % max_lines,
                   rule="A caption block deeper than %d lines covers the lower third and "
                        "the brand footer band." % max_lines,
                   fix="Split the cue across two cues, each held at least %.1fs." % min_dur)

    shown, extra = _cap(too_fast, MAX_CUE_VIOLATIONS)
    for i, cue in enumerate(shown):
        report.add("CAPTION.TOO_FAST", severity="warn",
                   where="cue %d @ %s%s" % (cue["index"], B.srt_timestamp(cue["start"]),
                                            _tail_suffix(i, shown, extra)),
                   found="%.2fs on screen for %d character(s)"
                         % (cue["end"] - cue["start"], len(cue["text"])),
                   expected="at least %.2fs per cue" % min_dur,
                   rule="A cue held under %.2fs cannot be read before it is replaced."
                        % min_dur,
                   fix="Extend the cue, merge it into its neighbour, or cut words from it.")

    ordered = sorted(cues, key=lambda c: (c["start"], c["end"]))
    for prev, nxt in zip(ordered, ordered[1:]):
        if nxt["start"] < prev["end"] - 1e-6:
            report.add("CAPTION.OVERLAP", severity="error",
                       where="cues %d and %d" % (prev["index"], nxt["index"]),
                       found="cue %d ends at %s but cue %d starts at %s"
                             % (prev["index"], B.srt_timestamp(prev["end"]),
                                nxt["index"], B.srt_timestamp(nxt["start"])),
                       expected="cue %d to start at or after %s"
                                % (nxt["index"], B.srt_timestamp(prev["end"])),
                       rule="Overlapping cues stack on top of each other in every player "
                            "and make both unreadable.",
                       fix="Move cue %d to start at %s, or shorten cue %d."
                           % (nxt["index"], B.srt_timestamp(prev["end"]), prev["index"]))
        if nxt["end"] < nxt["start"]:
            report.add("CAPTION.OVERLAP", severity="error",
                       where="cue %d" % nxt["index"],
                       found="ends at %s before it starts at %s"
                             % (B.srt_timestamp(nxt["end"]), B.srt_timestamp(nxt["start"])),
                       expected="end time after start time",
                       rule="A cue with an inverted timing is dropped by most players.",
                       fix="Correct the timing line for cue %d." % nxt["index"])

    _check_caption_gaps(report, ordered, timeline, duration)
    _check_caption_position(report, timeline, fmt)
    _check_caption_text(report, ordered, brand, where_prefix)
    return ordered


def _check_caption_position(report, timeline, fmt):
    # type: (B.Report, Optional[Dict[str, Any]], Dict[str, Any]) -> None
    """CAPTION.POSITION - burnt-in captions sitting inside the chrome reserve.

    Only meaningful for burnt-in captions: a sidecar is positioned by whatever
    is playing it. When the format reserves a bottom band for platform chrome
    (a reel's like/share/caption UI), a caption burnt in below that band is
    covered on the platform it was made for.
    """
    reserve = _num(fmt.get("chromeBottomPct"), 0.0) or 0.0
    if reserve <= 0.0 or timeline is None:
        return
    raw = timeline.get("raw") if isinstance(timeline, dict) else None
    captions = raw.get("captions") if isinstance(raw, dict) else None
    if not isinstance(captions, dict) or not captions.get("burnIn"):
        return
    placed = _num(captions.get("bottomMarginPct"), None)
    if placed is None or placed >= reserve - 1e-6:
        return
    report.add("CAPTION.POSITION", severity="warn", where="captions / burn-in",
               found="burnt in %g%% up from the bottom" % placed,
               expected="at least %g%% up from the bottom - the %s format's chrome "
                        "reserve" % (reserve, format_label(fmt)),
               rule="A vertical delivery reserves the lower band for the platform's own "
                    "UI. A caption burnt in below it is covered wherever it is posted.",
               fix="Re-render with build_video.py --format %s, which lifts burnt-in "
                   "captions clear of the reserve automatically."
                   % (fmt.get("name") or "<name>"))


def _check_caption_gaps(report, cues, timeline, duration):
    # type: (B.Report, Sequence[Dict[str, Any]], Optional[Dict[str, Any]], float) -> None
    """CAPTION.GAP - dead captions while the timeline says narration is running."""
    windows = narration_windows(timeline)
    if not windows or not cues:
        return

    gaps = []  # type: List[Tuple[float, float]]
    if cues[0]["start"] > 0.0:
        gaps.append((0.0, cues[0]["start"]))
    for prev, nxt in zip(cues, cues[1:]):
        if nxt["start"] > prev["end"]:
            gaps.append((prev["end"], nxt["start"]))
    if duration > 0.0 and duration > cues[-1]["end"]:
        gaps.append((cues[-1]["end"], duration))

    reported = []
    for gap in gaps:
        if (gap[1] - gap[0]) <= CAPTION_GAP_LIMIT_SEC:
            continue
        covered = sum(_overlap(gap, w) for w in windows)
        if covered <= 0.5:
            continue
        reported.append((gap, covered))

    shown, extra = _cap(reported, MAX_GAP_VIOLATIONS)
    for i, (gap, covered) in enumerate(shown):
        report.add("CAPTION.GAP", severity="info",
                   where="%s to %s%s" % (B.srt_timestamp(gap[0]), B.srt_timestamp(gap[1]),
                                         _tail_suffix(i, shown, extra)),
                   found="%.2fs without a caption, %.2fs of which the timeline is "
                         "narrating" % (gap[1] - gap[0], covered),
                   expected="a caption for every second of narration (gaps up to %.1fs "
                            "are ignored)" % CAPTION_GAP_LIMIT_SEC,
                   rule="Muted playback is the default on social; uncaptioned narration "
                        "is narration nobody receives.",
                   fix="Add cues covering %s to %s, or split the neighbouring scene's "
                       "caption so it tracks the voiceover."
                       % (B.srt_timestamp(gap[0]), B.srt_timestamp(gap[1])))


def _check_caption_text(report, cues, brand, where_prefix):
    # type: (B.Report, Sequence[Dict[str, Any]], Dict[str, Any], str) -> None
    """CONTENT.PLACEHOLDER and VOICE.EXCLAMATION over the shipped caption text."""
    voice = brand.get("voice") or {}
    phrases = voice.get("forbiddenPhrases") or []
    chars = voice.get("forbiddenChars") or ["!"]

    hits = {}  # type: Dict[str, List[Dict[str, Any]]]
    for cue in cues:
        for phrase in B.find_forbidden_phrases(cue["text"], phrases):
            hits.setdefault(phrase, []).append(cue)
    for phrase in sorted(hits.keys()):
        matched = hits[phrase]
        shown, extra = _cap(matched, MAX_CUE_VIOLATIONS)
        report.add("CONTENT.PLACEHOLDER", severity="error",
                   where="%s / cue %s%s"
                         % (where_prefix, ", ".join(str(c["index"]) for c in shown),
                            _suffix(extra)),
                   found="%r in %d cue(s), e.g. %r"
                         % (phrase, len(matched), matched[0]["text"][:120]),
                   expected="real copy - the phrase must not ship",
                   rule="Template scaffolding and placeholder copy must never reach a "
                        "client. This phrase is on the brand's forbidden list.",
                   fix="Rewrite the cue, then re-render the caption sidecar from the "
                       "video IR so the burned and sidecar copy stay identical.")

    flagged = []
    for cue in cues:
        found_chars = [ch for ch in chars if ch and ch in cue["text"]]
        if found_chars:
            flagged.append((cue, found_chars))
    shown, extra = _cap(flagged, MAX_CUE_VIOLATIONS)
    for i, (cue, found_chars) in enumerate(shown):
        report.add("VOICE.EXCLAMATION", severity="error",
                   where="%s / cue %d @ %s%s"
                         % (where_prefix, cue["index"], B.srt_timestamp(cue["start"]),
                            _tail_suffix(i, shown, extra)),
                   found="%s in %r" % (", ".join(repr(c) for c in found_chars),
                                       cue["text"][:120]),
                   expected="no %s" % ", ".join(repr(c) for c in chars),
                   rule="The brand voice is plain, operational and never salesy. "
                        "Exclamation marks are forbidden.",
                   fix="Delete the mark. If the line needs emphasis, make the claim "
                       "specific instead - a number does the work.")


# ---------------------------------------------------------------------------
# checks - structure
# ---------------------------------------------------------------------------

def check_storyline(report, brand, timeline):
    # type: (B.Report, Dict[str, Any], Optional[Dict[str, Any]]) -> None
    """STRUCTURE.STORYLINE - the scene roles must walk the brand's narrative arc."""
    if timeline is None:
        return
    cfg = ((brand.get("video") or {}).get("storyline") or {})
    if not cfg.get("required"):
        return

    # Short form cannot carry the long-form arc. A 15s reel has room for roughly
    # three beats, so judging it against a six-stage arc warns on every single
    # reel -- and a warning that always fires teaches people to ignore warnings.
    # The brand may declare arcByFormat, and a duration under shortFormMaxSec
    # falls back to the vertical arc when the format itself is not overridden.
    arc_source = "arc"
    arc_list = cfg.get("arc") or []
    by_format = cfg.get("arcByFormat") or {}
    # The normalised timeline keeps the untouched sidecar under "raw"; the
    # delivery format is recorded there by build_video.
    raw = timeline.get("raw") if isinstance(timeline.get("raw"), dict) else {}
    fmt = ""
    for holder in (raw.get("video"), raw, raw.get("meta")):
        if not isinstance(holder, dict):
            continue
        for key in ("format", "deliveryFormat"):
            value = holder.get(key)
            if isinstance(value, str) and value.strip():
                fmt = value.strip().lower()
                break
        if fmt:
            break
    if fmt and isinstance(by_format.get(fmt), list) and by_format[fmt]:
        arc_list = by_format[fmt]
        arc_source = "arcByFormat.%s" % fmt
    else:
        try:
            total = float(timeline.get("totalExplicit")
                          or timeline.get("elementSpan") or 0)
        except (TypeError, ValueError):
            total = 0.0
        short_max = cfg.get("shortFormMaxSec")
        if total and isinstance(short_max, (int, float)) and total <= float(short_max):
            for candidate in ("vertical", "short"):
                if isinstance(by_format.get(candidate), list) and by_format[candidate]:
                    arc_list = by_format[candidate]
                    arc_source = "arcByFormat.%s (film is %.1fs, under shortFormMaxSec %s)" % (
                        candidate, total, short_max)
                    break

    arc = [str(r).strip().lower() for r in arc_list if str(r).strip()]
    if not arc:
        return
    _ = arc_source  # surfaced in the messages below

    roles = [s["role"] for s in timeline["scenes"] if s["role"]]
    if not roles:
        report.add("STRUCTURE.STORYLINE", severity="warn", where="timeline / scenes",
                   found="no scene declares a role",
                   expected=" -> ".join(arc),
                   rule="Every brand film follows the same narrative arc, and the role "
                        "is what makes that checkable.",
                   fix="Set \"role\" on each scene to one of: %s." % ", ".join(arc))
        return

    present = set(roles)
    missing = [r for r in arc if r not in present]
    if missing:
        report.add("STRUCTURE.STORYLINE", severity="warn", where="timeline / scenes",
                   found="roles present: %s" % " -> ".join(roles),
                   expected="every stage of the arc: %s  [from %s]" % (" -> ".join(arc), arc_source),
                   rule="A film that skips a stage of the arc leaves the viewer without "
                        "the problem, the proof, or the next step. Missing: %s."
                        % ", ".join(missing),
                   fix="Add a scene for each missing stage (%s), or -- if this is short form -- "
                       "declare the right arc under brand.video.storyline.arcByFormat rather than "
                       "measuring a reel against a long-form arc." % ", ".join(missing))

    firsts = [(r, roles.index(r)) for r in arc if r in present]
    disordered = [(firsts[i][0], firsts[i + 1][0])
                  for i in range(len(firsts) - 1)
                  if firsts[i][1] > firsts[i + 1][1]]
    if disordered:
        report.add("STRUCTURE.STORYLINE", severity="warn", where="timeline / scenes",
                   found="'%s' first appears after '%s' (order on screen: %s)"
                         % (disordered[0][0], disordered[0][1], " -> ".join(roles)),
                   expected=" -> ".join(arc),
                   rule="The arc is ordered: the problem lands before the approach, and "
                        "proof lands before the ask.",
                   fix="Reorder the scenes so each stage first appears in arc order.")

    unknown = []
    for role in roles:
        if role not in arc and role not in unknown:
            unknown.append(role)
    if unknown:
        report.add("STRUCTURE.STORYLINE", severity="warn", where="timeline / scenes",
                   found="unrecognised role(s): %s" % ", ".join(unknown),
                   expected="one of: %s" % ", ".join(arc),
                   rule="A role outside the arc cannot be validated and will not be "
                        "carried by the storyline.",
                   fix="Map each scene onto an arc stage, or extend "
                       "brand.video.storyline.arc.")


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def resolve_brand_id(explicit, timeline_hint):
    # type: (Optional[str], Optional[str]) -> str
    """Explicit --brand wins, then the timeline's own brand key, then the default."""
    for rank, candidate in enumerate((explicit, timeline_hint, DEFAULT_BRAND)):
        if not candidate:
            continue
        result = B.resolve_brand(candidate)
        if result["brand"]:
            return result["brand"]
        if rank == 0:
            # An explicit --brand that matches nothing is a mistake worth stopping on.
            names = ", ".join(c["id"] for c in result["candidates"]) or "(none registered)"
            raise ValidatorError("no brand matches %r. Known brands: %s"
                                 % (candidate, names))
    raise ValidatorError("could not resolve a brand; pass --brand <id>")


def validate(video_path, brand_id=None, srt_path=None, timeline_path=None, frames=12,
             format_name=None):
    # type: (str, Optional[str], Optional[str], Optional[str], int, Optional[str]) -> B.Report
    """Run every check and return the populated Report."""
    if not os.path.isfile(video_path):
        raise ValidatorError("video not found: %s" % video_path)
    if shutil.which("ffprobe") is None:
        raise ValidatorError("ffprobe was not found on PATH")
    if shutil.which("ffmpeg") is None:
        raise ValidatorError("ffmpeg was not found on PATH")

    timeline_hint = None
    raw_timeline = None
    if timeline_path:
        raw_timeline = read_timeline_json(timeline_path)
        hint = raw_timeline.get("brand")
        timeline_hint = str(hint) if hint else None

    resolved = resolve_brand_id(brand_id, timeline_hint)
    brand = B.load_brand(resolved)

    timeline = (load_timeline(timeline_path, brand, raw=raw_timeline)
                if timeline_path else None)

    # Which delivery canvas this render is judged against. Explicit flag, then
    # the format the builder recorded in the sidecar, then a Video IR's
    # meta.format, then the brand's default.
    requested, format_source = format_request(format_name, raw_timeline)
    fmt, unknown_format = resolve_format(brand, requested, format_source)

    report = B.Report(target=video_path, brand=brand.get("id", resolved), kind="video")

    check_format(report, fmt, unknown_format)

    info = probe(video_path)
    check_streams(report, info, brand, video_path, fmt)
    duration = container_duration(info)
    check_duration(report, info, timeline, brand)

    workdir = tempfile.mkdtemp(prefix="brand-studio-video-")
    try:
        if frames > 0 and duration > 0.0:
            check_bookend(report, video_path, workdir, brand, duration, timeline, "intro")
            check_bookend(report, video_path, workdir, brand, duration, timeline, "outro")
            check_frames(report, video_path, workdir, brand, duration, frames, fmt)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    check_audio(report, video_path, info, brand, timeline, duration)
    check_captions(report, video_path, info, brand, srt_path, timeline, duration, fmt)
    check_storyline(report, brand, timeline)
    return report


class _ArgumentParser(argparse.ArgumentParser):
    """argparse exits 2 on a usage error, which contract C reserves for 'this file
    has errors'. A bad invocation is an internal failure, so it exits 1."""

    def error(self, message):
        # type: (str) -> None
        self.print_usage(sys.stderr)
        sys.stderr.write("%s: error: %s\n" % (self.prog, message))
        raise SystemExit(1)


def main(argv=None):
    # type: (Optional[Sequence[str]]) -> int
    parser = _ArgumentParser(
        prog="validate_video.py",
        description="Validate a rendered video against a brand profile. Emits FROZEN "
                    "CONTRACT C JSON on stdout.",
        epilog="Exit status: 0 clean, 2 at least one error, 1 the validator could not run.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video", help="path to the rendered video, e.g. out/film.mp4")
    parser.add_argument("--brand", default=None,
                        help="brand id or name (default: the timeline's brand, else %s)"
                             % DEFAULT_BRAND)
    parser.add_argument("--format", choices=("json", "human"), default="json",
                        help="OUTPUT format: json (default, contract C) or human "
                             "(readable report). For the delivery format the film was "
                             "built for, see --delivery-format.")
    parser.add_argument("--delivery-format", "--video-format", dest="delivery_format",
                        default=None, metavar="NAME",
                        help="delivery format to validate against, named in "
                             "brand.video.formats (landscape, square, vertical). "
                             "Without it, the format the timeline sidecar recorded is "
                             "used, then a Video IR's meta.format, then the brand's "
                             "default. It sets the expected resolution, the safe margin "
                             "and the caption geometry. A name the brand does not "
                             "declare is reported as VIDEO.FORMAT_UNKNOWN.")
    parser.add_argument("--srt", default=None,
                        help="caption sidecar to validate; without it, an embedded "
                             "subtitle stream is used when present")
    parser.add_argument("--timeline", default=None,
                        help="the video IR or timeline sidecar the film was built from; "
                             "enables the duration, caption-gap and storyline checks")
    parser.add_argument("--frames", type=int, default=12, metavar="N",
                        help="evenly spaced frames to sample for the pixel checks "
                             "(default 12; 0 skips all frame analysis)")
    args = parser.parse_args(argv)

    if args.frames < 0:
        parser.error("--frames must be zero or greater")
    if args.frames > 600:
        parser.error("--frames above 600 is not useful and takes minutes")

    try:
        report = validate(args.video, brand_id=args.brand, srt_path=args.srt,
                          timeline_path=args.timeline, frames=args.frames,
                          format_name=args.delivery_format)
    except ValidatorError as exc:
        sys.stderr.write("validate_video.py: %s\n" % exc)
        return 1
    except B.BrandNotFound as exc:
        sys.stderr.write("validate_video.py: %s\n" % exc)
        return 1
    except KeyboardInterrupt:  # pragma: no cover
        sys.stderr.write("validate_video.py: interrupted\n")
        return 1
    except Exception as exc:  # pragma: no cover - defensive
        sys.stderr.write("validate_video.py: internal failure: %s: %s\n"
                         % (type(exc).__name__, exc))
        return 1

    if args.format == "human":
        sys.stdout.write(report.to_human())
    else:
        json.dump(report.to_json(), sys.stdout, indent=2)
        sys.stdout.write("\n")
    return report.exit_code()


if __name__ == "__main__":
    sys.exit(main())
