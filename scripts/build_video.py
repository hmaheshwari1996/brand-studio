#!/usr/bin/env python3
"""Build a branded MP4 (plus an SRT sidecar) from a Video IR and a brand profile.

Two pipelines, selected by the IR's ``kind``:

  deck-video   An existing .pptx is converted to PDF with LibreOffice and
               rasterised to one PNG per slide, fitted to the delivery canvas
               (pdftoppm at 144dpi == 1920x1080 for a 13.333x7.5in slide on the
               landscape canvas; on a vertical canvas the slide is fitted and
               letterboxed rather than stretched). Scenes reference slides by
               1-based index and are animated with a subtle push-in plus
               crossfades.

  explainer    Scenes are rendered from the HTML motion templates in
               templates/video/ with headless Chrome. Each scene becomes a PNG
               frame sequence: the template reads ``?t=<0..1>`` from its own URL
               and positions its animation from that value, so every frame is
               deterministic and no CSS animation is involved.

Both pipelines share everything downstream: voiceover via ``say``, real
durations measured with ffprobe, an SRT built from the resolved timeline,
music ducked under narration, and a single ffmpeg invocation that assembles
the lot with xfade transitions.

DELIVERY FORMAT. Everything geometric is driven by one resolved format, taken
from ``--format NAME``, else the IR's ``meta.format``, else the brand's default,
and looked up in ``brand.video.formats``. It sets the render canvas, the
scale/pad filters, the ``--canvas-*`` and ``--safe-*`` custom properties the
motion templates lay out against, deck rasterisation, the Ken Burns geometry,
the caption size and the caption position -- including the top/bottom reserve a
format declares for platform chrome, so a reel's captions sit above the lower
band rather than under it. A brand that declares no ``formats`` block keeps
working exactly as before, at ``video.resolution``. The intro and outro are
cached PER FORMAT (``intro.mp4``, ``intro-vertical.mp4``), so a reel and a
landscape film built the same day cannot overwrite each other's bookends.

The intro and outro are BRAND assets, not per-video ones. By default they carry
nothing from the film -- the brand's logo reveal, the brand name, the brand's
standard contact -- so every video for a brand opens and closes identically.
Each is rendered once into ``brands/<id>/video/generated/`` and reused verbatim
by every later build, until the logo bytes, gradient, duration, style,
resolution or fps change (which invalidates it automatically). Re-rendering is
otherwise an explicit act: --regenerate-intro / --regenerate-outro /
--regenerate-brand-assets. A single film can opt out with meta.introTitle,
meta.outroTitle or meta.contact, which personalises that film's bookend and
takes it off the shared brand asset.

MUSIC. When the brand enables music and nobody supplies a track, the bed is
GENERATED for this film by ``make_music.py`` -- synthesised from music theory,
so it is copyright-free -- at the film's exact total duration and the brand's
target loudness, in the mood the brand's ``video.music.mood`` / ``avoid`` imply.
Generation is the default, not an opt-in: a brand that asks for music gets
music. Priority is --music FILE, then the IR's ``music.file``, then the brand's
``video.music.file``, then generation; --no-music silences the film outright.
Beds are cached under ``brands/<id>/audio/generated/`` on a fingerprint of
everything that defines them (mood, key, mode, bpm, seed, duration, target
loudness and the brand's music settings), so a reel and a film of the same
length and mood built on the same night share one bed instead of paying for it
twice. --regenerate-music ignores the cache. Everything downstream is unchanged:
the bed is mastered with the same two-pass loudnorm, ducked under narration by
the brand's ``duckUnderVoiceDb``, and faded with its fadeInSec / fadeOutSec.
A generator that fails is a warning, never a failed build -- the film ships with
narration only.

Nothing here reaches the network and nothing beyond python-pptx / pillow is
imported. ffmpeg, ffprobe, soffice, pdftoppm and Chrome are located on PATH
(or in the usual macOS application directories) and their absence is reported
before any work starts.

Usage:
    build_video.py --ir video.json --out out.mp4 --deck deck.pptx
    build_video.py --ir explainer.json --out out.mp4
    build_video.py --ir video.json --out out.mp4 --dry-run
    build_video.py --ir video.json --out out.mp4 --music-mood calm
    build_video.py --help
"""

import argparse
import concurrent.futures
import datetime
import json
import math
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

from typing import Any, Dict, List, Optional, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from lib import brandlib  # type: ignore
except ImportError:  # pragma: no cover - fallback for odd sys.path setups
    sys.path.insert(0, os.path.join(_HERE, "lib"))
    import brandlib  # type: ignore

try:
    import asset_cache  # type: ignore
except ImportError:  # pragma: no cover - the cache is an optimisation, never a dependency
    asset_cache = None  # type: ignore


PROG = "build_video.py"

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]

# Seconds of silence appended after narration before a scene may cut away.
VO_TAIL_SEC = 0.4
# Seconds of animation rendered for a motion scene; the final frame then holds.
MOTION_ANIM_SEC = 1.6
# Push-in applied to still slides. 1.03 == a 3% zoom across the whole hold.
KENBURNS_ZOOM = 1.03
# Level that loudness-normalised narration presents to the ducking detector,
# measured against real `say` output normalised to -16 LUFS.
SIDECHAIN_LEVEL_DBFS = -11.5
# Headroom kept above the requested duck depth so the compressor has room to
# work. Also fixes the ratio: ratio == (depth + margin) / margin.
DUCK_MARGIN_DB = 8.0

# ---- the generated music bed ----------------------------------------------
# make_music.py synthesises the bed from music theory. It is driven as a
# subprocess through its CLI rather than imported: the CLI is its stable
# interface, it owns every musical decision, and a generator that dies cannot
# take the build down with it.
MAKE_MUSIC = os.path.join(_HERE, "make_music.py")
# The AssetCache kind generated beds belong to. Slots are numbered from it.
MUSIC_BED_KIND = "music-bed"
# m4a, not wav: a bed is stored in the brand directory and shared with the team
# through the repo, and 196 kbps AAC is a twentieth of the size of the same bed
# as 44.1 kHz stereo PCM. make_music lands both formats on the requested
# duration to the millisecond, and the bed sits 23 LU under a narration mix, so
# nothing here can hear the difference.
MUSIC_BED_EXT = "m4a"
# Cache slots for beds. A film's bed is keyed on its exact length, so the slots
# are a small ring: a new bed takes a free slot, and when they are all taken it
# evicts the oldest. That bounds a repo-shared directory at a handful of files
# while still letting a reel and a film of the same length share one bed.
MUSIC_BED_SLOTS = 6
# make_music's own default. Same seed + same parameters == byte-identical bed.
MUSIC_BED_SEED = 7
# A 30-second bed renders in about 2 seconds; the ceiling is for a long film on
# a loaded machine, and exists only so a wedged generator cannot hang a build.
MUSIC_TIMEOUT_SEC = 900.0
# A cached bed this far off the film's length is treated as unusable.
MUSIC_DURATION_TOLERANCE_SEC = 0.25
# Moods make_music ships. Used for help text only -- make_music validates the
# real thing, so a mood added there works here without an edit.
MUSIC_MOODS = ("confident", "calm", "urgent", "warm", "neutral", "uplifting")

VALID_KINDS = ("deck-video", "explainer")
VALID_VISUAL_KINDS = ("slide", "motion", "image")

# The bookends. Both are brand assets: rendered once per brand, cached under
# brands/<id>/video/generated/ and reused byte-identically by every later build.
BRAND_ASSET_KINDS = ("intro", "outro")
# Encoding settings for a cached bookend. Deliberately constants rather than the
# run's --crf / --preset: the cached artifact must not vary with per-run flags,
# or two videos for the same brand would open differently. Higher quality than
# the final master because it is re-encoded once more on the way in.
BRAND_ASSET_CRF = 14
BRAND_ASSET_PRESET = "medium"
# IR meta keys that personalise a bookend for ONE video. Setting any of them is
# an explicit opt-out of the shared brand asset, so that render is not cached.
PERSONAL_META_KEYS = {
    "intro": ("introTitle", "introEyebrow"),
    "outro": ("outroTitle", "contact", "outroEyebrow"),
}
# Where a brand's standard closing contact is read from, most specific first.
# The IR is not consulted: meta.contact personalises a single film instead.
BRAND_CONTACT_PATHS = ("outro.contact", "contact")

TRANSITION_MAP = {
    "crossfade": "fade",
    "fade": "fade",
    "dissolve": "dissolve",
    "fadeblack": "fadeblack",
    "fadewhite": "fadewhite",
    "wipe": "wipeleft",
    "wipeleft": "wipeleft",
    "wiperight": "wiperight",
    "slide": "slideleft",
    "slideleft": "slideleft",
    "slideright": "slideright",
    "smoothleft": "smoothleft",
    "none": None,
    "cut": None,
}

DEFAULT_VIDEO = {
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
    },
    "voiceover": {
        "enabled": True,
        "engine": "say",
        "voice": None,
        "rateWpm": 165,
        "targetLufs": -16.0,
    },
    "captions": {
        "enabled": True,
        "required": True,
        "burnIn": False,
        "sidecar": "srt",
        "font": "Poppins Medium",
        "sizePt": 28,
        "color": "#FFFFFF",
        "background": "#0F0A6C",
        "backgroundOpacity": 0.82,
        "position": "bottom-center",
        "bottomMarginPct": 8,
        "maxCharsPerLine": 42,
        "maxLines": 2,
        "minDurationSec": 1.2,
    },
    "transition": {"type": "crossfade", "durationSec": 0.4},
    "slideHoldSec": {"min": 3.0, "default": 5.0, "max": 12.0},
}

# weight -> filename fragments looked for in brands/<id>/assets/fonts/
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


class BuildError(Exception):
    """Anything that should stop the build with a human-readable message."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def cfg(node, path, default=None):
    # type: (Any, str, Any) -> Any
    """Walk a dotted path through nested dicts, returning ``default`` if absent."""
    cur = node
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    if cur is None:
        return default
    return cur


def as_float(value, default):
    # type: (Any, float) -> float
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(default)
    if out != out or out in (float("inf"), float("-inf")):
        return float(default)
    return out


def as_int(value, default):
    # type: (Any, int) -> int
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return int(default)


def clean_text(value):
    # type: (Any) -> str
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def which(name):
    # type: (str) -> Optional[str]
    return shutil.which(name)


def find_chrome():
    # type: () -> Optional[str]
    env = os.environ.get("BRAND_STUDIO_CHROME")
    if env and os.path.exists(env):
        return env
    for candidate in CHROME_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    for name in ("google-chrome", "chromium", "chromium-browser"):
        found = which(name)
        if found:
            return found
    return None


def file_url(path):
    # type: (str) -> str
    """Absolute filesystem path -> a file:// URL safe to embed in HTML/CSS."""
    abspath = os.path.abspath(path)
    out = []
    for ch in abspath:
        if ch.isalnum() or ch in "/-_.~":
            out.append(ch)
        else:
            out.append("".join("%%%02X" % b for b in ch.encode("utf-8")))
    return "file://" + "".join(out)


def resolve_path(raw, search_dirs):
    # type: (Optional[str], Sequence[str]) -> Optional[str]
    """Resolve a possibly-relative asset path against a list of base directories."""
    if not raw:
        return None
    candidate = os.path.expanduser(str(raw))
    if os.path.isabs(candidate):
        return candidate if os.path.exists(candidate) else None
    for base in search_dirs:
        joined = os.path.abspath(os.path.join(base, candidate))
        if os.path.exists(joined):
            return joined
    return None


def run(cmd, what, cwd=None, timeout=None):
    # type: (Sequence[str], str, Optional[str], Optional[float]) -> str
    """Run a subprocess, raising BuildError with useful context on failure."""
    try:
        proc = subprocess.run(
            list(cmd), cwd=cwd, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise BuildError("%s failed to start: %s" % (what, exc))
    except subprocess.TimeoutExpired:
        raise BuildError("%s timed out after %ss" % (what, timeout))
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()
        out = (proc.stdout or b"").decode("utf-8", "replace").strip()
        tail = "\n".join((err or out).splitlines()[-25:])
        raise BuildError("%s failed (exit %d)\n%s" % (what, proc.returncode, tail))
    return (proc.stdout or b"").decode("utf-8", "replace")


LOUDNORM_LRA = 11.0
LOUDNORM_TP_VO = -1.5
LOUDNORM_TP_MUSIC = -2.0
LOUDNORM_TP_MASTER = -1.0
_LOUDNORM_KEYS = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")


def measure_loudness(ffmpeg, path, target_i, target_tp):
    # type: (str, str, float, float) -> Optional[Dict[str, str]]
    """Analysis pass for loudnorm. Returns the measured values, or None.

    Single-pass loudnorm runs a 3-second lookahead limiter that mangles short
    clips and truncates the tail of a music bed, so every normalisation here is
    two-pass: measure first, then apply a linear (constant-gain) correction.
    """
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-i", path, "-vn",
           "-af", "loudnorm=I=%0.2f:TP=%0.2f:LRA=%0.2f:print_format=json"
                  % (target_i, target_tp, LOUDNORM_LRA),
           "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (proc.stderr or b"").decode("utf-8", "replace")
    blocks = re.findall(r"\{[^{}]*\}", text)
    for block in reversed(blocks):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if not isinstance(data, dict) or not all(k in data for k in _LOUDNORM_KEYS):
            continue
        measured = {}
        for key in _LOUDNORM_KEYS:
            try:
                value = float(data[key])
            except (TypeError, ValueError):
                return None
            if value != value or value in (float("inf"), float("-inf")):
                return None  # silent or unmeasurable input
            measured[key] = "%0.2f" % value
        return measured
    return None


def loudnorm_filter(target_i, target_tp, measured):
    # type: (float, float, Optional[Dict[str, str]]) -> str
    base = "loudnorm=I=%0.2f:TP=%0.2f:LRA=%0.2f" % (target_i, target_tp, LOUDNORM_LRA)
    if not measured:
        return base
    return (base + ":measured_I=%s:measured_TP=%s:measured_LRA=%s:measured_thresh=%s"
                   ":offset=%s:linear=true"
            % (measured["input_i"], measured["input_tp"], measured["input_lra"],
               measured["input_thresh"], measured["target_offset"]))


def measure_programme_loudness(tools, elements, plan, log):
    # type: (Dict[str, Optional[str]], List[Dict[str, Any]], Dict[str, Any], Any) -> Optional[Dict[str, str]]
    """Run the audio graph on its own to measure the finished mix.

    Individually normalised narration and a ducked bed do not add up to the
    brand's programme loudness -- the bed pulls the integrated figure down by
    however much of the film is not narrated. Measuring the mix here lets the
    real render carry a single, exact master gain.
    """
    audio_inputs, audio_parts, premaster = build_audio_graph(elements, plan, 0)
    if not premaster:
        return None
    # The analysis stage joins the complex graph: ffmpeg refuses a simple -af
    # filter on a stream that already comes out of a filter_complex.
    parts = list(audio_parts) + [
        "[%s]loudnorm=I=%0.2f:TP=%0.2f:LRA=%0.2f:print_format=json[measured]"
        % (premaster, plan["programmeLufs"], LOUDNORM_TP_MASTER, LOUDNORM_LRA)]
    cmd = [tools["ffmpeg"], "-hide_banner", "-nostats"] + audio_inputs + [
        "-filter_complex", ";".join(parts), "-map", "[measured]",
        "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=900)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    text = (proc.stderr or b"").decode("utf-8", "replace")
    for block in reversed(re.findall(r"\{[^{}]*\}", text)):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if not isinstance(data, dict) or not all(k in data for k in _LOUDNORM_KEYS):
            continue
        measured = {}
        for key in _LOUDNORM_KEYS:
            try:
                value = float(data[key])
            except (TypeError, ValueError):
                return None
            if value != value or value in (float("inf"), float("-inf")):
                return None
            measured[key] = "%0.2f" % value
        log("  mixed programme measures %s LUFS, mastering to %0.1f"
            % (measured["input_i"], plan["programmeLufs"]))
        return measured
    return None


def ffprobe_duration(ffprobe, path):
    # type: (str, str) -> float
    out = run([ffprobe, "-v", "error", "-show_entries", "format=duration",
               "-of", "default=noprint_wrappers=1:nokey=1", path],
              "ffprobe on %s" % os.path.basename(path))
    try:
        return float(out.strip())
    except ValueError:
        raise BuildError("ffprobe returned no duration for %s" % path)


def link_or_copy(src, dst):
    # type: (str, str) -> None
    """Cheapest possible duplicate: symlink, else hardlink, else copy."""
    if os.path.exists(dst):
        return
    try:
        os.symlink(src, dst)
        return
    except (OSError, NotImplementedError):
        pass
    try:
        os.link(src, dst)
        return
    except OSError:
        shutil.copyfile(src, dst)


# ---------------------------------------------------------------------------
# delivery formats
#
# A brand may declare several delivery canvases under video.formats -- landscape
# for a client presentation, vertical for a reel, square for an in-feed post --
# each with its own safe margin and caption size. ONE of them is resolved per
# build and it then drives everything downstream: the render canvas, the
# scale/pad filters, the --canvas-*/--safe-* custom properties handed to the
# motion templates, deck rasterisation, the Ken Burns geometry, the caption
# style and the cache slot the bookends are stored in.
#
# A brand that declares no formats keeps working unchanged: the canvas is
# video.resolution and the safe margin is video.safeMarginPct, exactly as before.
# ---------------------------------------------------------------------------

# The reserve a format keeps clear for platform chrome is stated in prose in the
# format's own $note ("Reserve the top 12% and bottom 20% for platform chrome"),
# because brand.json declares no structured field for it. That sentence is the
# only declaration there is, so it is what gets read -- the numbers are never
# assumed here.
_CHROME_TOP_RE = re.compile(r"top\s+([0-9]+(?:\.[0-9]+)?)\s*%", re.IGNORECASE)
_CHROME_BOTTOM_RE = re.compile(r"bottom\s+([0-9]+(?:\.[0-9]+)?)\s*%", re.IGNORECASE)


def declared_formats(video):
    # type: (Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]
    """(name, spec) for every delivery format the brand declares, in order.

    Keys starting with '$' are documentation ($note), never formats, and a spec
    without usable pixel dimensions is not a canvas anyone can render onto.
    """
    node = video.get("formats")
    if not isinstance(node, dict):
        return []
    out = []  # type: List[Tuple[str, Dict[str, Any]]]
    for name, spec in node.items():
        if not isinstance(name, str) or name.startswith("$"):
            continue
        if not isinstance(spec, dict):
            continue
        if as_int(spec.get("w"), 0) > 0 and as_int(spec.get("h"), 0) > 0:
            out.append((name, spec))
    return out


def default_format_name(video):
    # type: (Dict[str, Any]) -> Optional[str]
    """The brand's default delivery format, or None when it declares no formats.

    Derived, never invented: the default is whichever declared format matches
    video.resolution -- the canvas the brand already masters at. A brand whose
    resolution matches nothing falls back to a format literally called
    'landscape', then to the first one declared.
    """
    formats = declared_formats(video)
    if not formats:
        return None
    res_w = as_int(cfg(video, "resolution.w", 0), 0)
    res_h = as_int(cfg(video, "resolution.h", 0), 0)
    if res_w > 0 and res_h > 0:
        for name, spec in formats:
            if as_int(spec.get("w"), 0) == res_w and as_int(spec.get("h"), 0) == res_h:
                return name
    for name, _spec in formats:
        if name == "landscape":
            return name
    return formats[0][0]


def chrome_reserve(spec):
    # type: (Dict[str, Any]) -> Tuple[float, float]
    """(top%, bottom%) of the frame a format keeps clear for platform chrome.

    Prefers the structured ``chromeTopPct`` / ``chromeBottomPct`` fields. Falls
    back to parsing the format's ``$note`` prose only for older brand profiles
    that predate those fields -- reading layout numbers out of an English
    sentence means rewording the sentence silently drops the reserve to zero,
    so the structured fields are authoritative wherever they exist.

    A format that declares neither reserves nothing, which is the right answer
    for landscape and square.
    """
    top = spec.get("chromeTopPct")
    bottom = spec.get("chromeBottomPct")
    if isinstance(top, (int, float)) or isinstance(bottom, (int, float)):
        return (float(top) if isinstance(top, (int, float)) else 0.0,
                float(bottom) if isinstance(bottom, (int, float)) else 0.0)

    note = ""
    for key in ("$note", "note"):
        value = spec.get(key)
        if isinstance(value, str) and value.strip():
            note = value
            break
    if not note:
        return (0.0, 0.0)
    top_m = _CHROME_TOP_RE.search(note)
    bottom_m = _CHROME_BOTTOM_RE.search(note)
    return (float(top_m.group(1)) if top_m else 0.0,
            float(bottom_m.group(1)) if bottom_m else 0.0)


def resolve_delivery_format(video, requested, source):
    # type: (Dict[str, Any], Optional[str], str) -> Dict[str, Any]
    """Resolve one delivery format into every number the build needs.

    ``requested`` is the name asked for (or None to take the brand's default)
    and ``source`` says where it came from, purely so an error can name it.

    A name the brand does not declare is FATAL. Quietly falling back to
    landscape is precisely how a night's worth of reels comes out 1920x1080.
    """
    formats = declared_formats(video)
    lookup = dict(formats)
    name = (requested or "").strip() or None
    if name is None:
        name = default_format_name(video)
        source = "the brand default"

    if name is not None and name not in lookup:
        if formats:
            raise BuildError(
                "unknown delivery format %r (from %s). This brand declares: %s.\n"
                "Building the wrong canvas silently is worse than not building at "
                "all, so this stops here."
                % (name, source, ", ".join(n for n, _ in formats)))
        raise BuildError(
            "delivery format %r was requested (from %s) but this brand declares no "
            "video.formats block.\nAdd one, or drop the format request to build at "
            "video.resolution (%dx%d)."
            % (name, source, as_int(cfg(video, "resolution.w", 1920), 1920),
               as_int(cfg(video, "resolution.h", 1080), 1080)))

    base_safe = as_float(video.get("safeMarginPct"), 5.0)
    base_caption = as_float(cfg(video, "captions.sizePt"), 28.0)
    base_bottom = as_float(cfg(video, "captions.bottomMarginPct"), 8.0)

    if name is None:
        # An older brand profile with no formats block at all. Behave exactly as
        # this script did before formats existed.
        return {
            "name": None,
            "declared": False,
            "isDefault": True,
            "width": as_int(cfg(video, "resolution.w", 1920), 1920),
            "height": as_int(cfg(video, "resolution.h", 1080), 1080),
            "ratio": None,
            "use": None,
            "safeMarginPct": base_safe,
            "captionSizePt": base_caption,
            "chromeTopPct": 0.0,
            "chromeBottomPct": 0.0,
            "captionBottomMarginPct": base_bottom,
            "source": "video.resolution (the brand declares no formats)",
        }

    spec = lookup[name]
    top_pct, bottom_pct = chrome_reserve(spec)
    return {
        "name": name,
        "declared": True,
        "isDefault": name == default_format_name(video),
        "width": as_int(spec.get("w"), 1920),
        "height": as_int(spec.get("h"), 1080),
        "ratio": str(spec.get("ratio") or "") or None,
        "use": str(spec.get("use") or "") or None,
        "safeMarginPct": as_float(spec.get("safeMarginPct"), base_safe),
        "captionSizePt": as_float(spec.get("captionSizePt"), base_caption),
        "chromeTopPct": top_pct,
        "chromeBottomPct": bottom_pct,
        # Captions clear the brand's own bottom margin AND whatever the format
        # reserves for platform chrome, so a reel's captions sit above the
        # like/share/caption band rather than under it.
        "captionBottomMarginPct": max(base_bottom, bottom_pct),
        "source": source,
    }


def format_label(fmt):
    # type: (Dict[str, Any]) -> str
    """One-line description of a resolved format, for logs and summaries."""
    return "%s %dx%d%s (%s)" % (
        fmt.get("name") or "resolution",
        fmt["width"], fmt["height"],
        " %s" % fmt["ratio"] if fmt.get("ratio") else "",
        fmt.get("source") or "?")


# ---------------------------------------------------------------------------
# colour helpers used for the injected CSS and the caption style
# ---------------------------------------------------------------------------

def safe_hex(value, default):
    # type: (Any, str) -> str
    if brandlib.is_hex(value):
        return brandlib.normalize_hex(value)
    return default


def ass_colour(hex_value, opacity=1.0):
    # type: (str, float) -> str
    """#RRGGBB -> libass &HAABBGGRR (alpha is inverted: 00 == opaque)."""
    r, g, b = brandlib.hex_to_rgb(hex_value)
    alpha = int(round((1.0 - max(0.0, min(1.0, opacity))) * 255.0))
    return "&H%02X%02X%02X%02X" % (alpha, b, g, r)


def css_var_name(path_parts):
    # type: (Sequence[str]) -> str
    slug = "-".join(str(p) for p in path_parts)
    slug = re.sub(r"[^0-9A-Za-z]+", "-", slug).strip("-").lower()
    return "--c-" + slug


def collect_color_vars(brand):
    # type: (Dict[str, Any]) -> List[Tuple[str, str]]
    """Every approved brand colour as a CSS custom property, superseded excluded."""
    out = []  # type: List[Tuple[str, str]]
    seen = set()
    color = brand.get("color") or {}
    if not isinstance(color, dict):
        return out

    def walk(node, path):
        if isinstance(node, dict):
            for key in node:
                if str(key).startswith("$") or key == "superseded":
                    continue
                walk(node[key], list(path) + [str(key)])
        elif brandlib.is_hex(node):
            name = css_var_name(path)
            if name not in seen:
                seen.add(name)
                out.append((name, brandlib.normalize_hex(node)))

    for key in color:
        if str(key).startswith("$") or key == "superseded":
            continue
        walk(color[key], [str(key)])
    return out


#: Where the first stop of a two-stop brand gradient stops being pure. Holding
#: it to here keeps a real brand colour dominant across the frame instead of
#: filling it edge to edge with intermediate mixtures that belong to no token.
GRADIENT_HOLD_PCT = 42.0


def gradient_css(brand, name, fallback):
    # type: (Dict[str, Any], str, str) -> str
    spec = cfg(brand, "color.gradient." + name, None)
    if not isinstance(spec, dict):
        return fallback
    stops = [brandlib.normalize_hex(s) for s in spec.get("stops", []) if brandlib.is_hex(s)]
    if len(stops) < 2:
        return fallback
    angle = as_float(spec.get("angle"), 135.0)
    if len(stops) == 2:
        parts = ["%s 0%%" % stops[0],
                 "%s %g%%" % (stops[0], GRADIENT_HOLD_PCT),
                 "%s 100%%" % stops[1]]
    else:
        parts = list(stops)
    return "linear-gradient(%gdeg, %s)" % (angle, ", ".join(parts))


def gradient_field_css(brand, name, fallback):
    # type: (Dict[str, Any], str, str) -> str
    """The same gradient as a full-bleed *field* rather than a corner-to-corner ramp.

    A linear wash puts its last stop hard into one corner, which leaves the four
    frame edges carrying wildly different colours. Broadcast-safe-area detection
    reads that as content pressed against the edge, and the frame no longer has a
    single dominant brand colour either. So the field form keeps the whole border
    on the first stop and floats the second as a soft wash inside it, offset along
    the brand's own gradient angle so the light still comes from the right place.
    """
    spec = cfg(brand, "color.gradient." + name, None)
    if not isinstance(spec, dict):
        return fallback
    stops = [brandlib.normalize_hex(s) for s in spec.get("stops", []) if brandlib.is_hex(s)]
    if len(stops) < 2:
        return fallback
    angle = math.radians(as_float(spec.get("angle"), 135.0))
    cx = 50.0 + 6.0 * math.sin(angle)
    cy = 50.0 + 6.0 * -math.cos(angle)
    return ("radial-gradient(closest-side at %.1f%% %.1f%%, %s 0%%, %s 82%%)"
            % (cx, cy, stops[-1], stops[0]))


def font_face_rules(brand, warnings):
    # type: (Dict[str, Any], List[str]) -> Tuple[str, Dict[str, str]]
    """@font-face blocks for the brand's approved weights, from local TTF files."""
    fonts_dir = os.path.join(brand.get("_dir", ""), "assets", "fonts")
    weight_map = cfg(brand, "type.weightToPptxFamily", {}) or {}
    families = {}  # type: Dict[str, str]
    blocks = []
    if not os.path.isdir(fonts_dir):
        warnings.append(
            "no assets/fonts directory for brand '%s'; HTML templates fall back to "
            "an installed copy of %s" % (brand.get("id"), cfg(brand, "type.family", "the brand face")))
        for weight, family in sorted(weight_map.items(), key=lambda kv: str(kv[0])):
            families[str(weight)] = str(family)
        return "", families

    available = [f for f in sorted(os.listdir(fonts_dir)) if f.lower().endswith((".ttf", ".otf"))]
    for weight_raw in sorted(weight_map.keys(), key=lambda k: as_int(k, 400)):
        weight = as_int(weight_raw, 400)
        family = str(weight_map[weight_raw])
        families[str(weight)] = family
        fragments = FONT_WEIGHT_FILES.get(weight, ())
        chosen = None
        for frag in fragments:
            for fname in available:
                stem = os.path.splitext(fname)[0].lower()
                if stem.endswith("-" + frag.lower()) or stem.endswith(frag.lower()):
                    chosen = os.path.join(fonts_dir, fname)
                    break
            if chosen:
                break
        if not chosen:
            warnings.append("no font file found for weight %d in %s" % (weight, fonts_dir))
            continue
        fmt = "opentype" if chosen.lower().endswith(".otf") else "truetype"
        # The family name is registered twice: once under the PPTX-style family
        # name the brand profile uses, once under the base family at its real
        # numeric weight, so templates can address it either way.
        blocks.append(
            "@font-face{font-family:'%s';src:url('%s') format('%s');"
            "font-weight:400;font-style:normal;font-display:block;}"
            % (family, file_url(chosen), fmt))
        base = str(cfg(brand, "type.family", family))
        if base != family:
            blocks.append(
                "@font-face{font-family:'%s';src:url('%s') format('%s');"
                "font-weight:%d;font-style:normal;font-display:block;}"
                % (base, file_url(chosen), fmt, weight))
    return "\n".join(blocks), families


def build_brand_vars(brand, video, warnings, fmt=None):
    # type: (Dict[str, Any], Dict[str, Any], List[str], Optional[Dict[str, Any]]) -> str
    """The <style> block substituted into every template's {{BRAND_VARS}} marker.

    ``fmt`` is the resolved delivery format and supplies the canvas and the safe
    margin. Omitted, the brand's default format is resolved here, so callers
    that do not care about formats (preview_motion.py) keep working unchanged.
    """
    if fmt is None:
        fmt = resolve_delivery_format(video, None, "the brand default")
    width = int(fmt["width"])
    height = int(fmt["height"])
    safe_pct = as_float(fmt.get("safeMarginPct"), 5.0)
    safe_x = round(width * safe_pct / 100.0, 2)
    safe_y = round(height * safe_pct / 100.0, 2)
    # What the format keeps clear for platform chrome, and the usable band once
    # both that and the safe margin are honoured.
    chrome_top = round(height * as_float(fmt.get("chromeTopPct"), 0.0) / 100.0, 2)
    chrome_bottom = round(height * as_float(fmt.get("chromeBottomPct"), 0.0) / 100.0, 2)

    faces, families = font_face_rules(brand, warnings)

    blue = safe_hex(cfg(brand, "color.brand.blue"), "#0000FF")
    navy = safe_hex(cfg(brand, "color.brand.navy"), "#0F0A6C")
    mint = safe_hex(cfg(brand, "color.brand.mint"), "#41E7AB")
    teal = safe_hex(cfg(brand, "color.brand.teal"), mint)
    tint = safe_hex(cfg(brand, "color.brand.tint"), "#EBF6F9")
    white = safe_hex(cfg(brand, "color.neutral.0"), "#FFFFFF")
    rule = safe_hex(cfg(brand, "color.neutral.200"), tint)
    ink = safe_hex(cfg(brand, "colorRules.defaultText"), navy)
    ink_soft = safe_hex(cfg(brand, "colorRules.secondaryText"), navy)

    on_navy = brandlib.on_surface_text(brand, navy) or white
    on_blue = brandlib.on_surface_text(brand, blue) or white
    on_mint = brandlib.on_surface_text(brand, mint) or navy
    on_tint = brandlib.on_surface_text(brand, tint) or ink
    on_white = brandlib.on_surface_text(brand, white) or ink

    # A mint-family colour that is legal as text on light surfaces.
    mint_text = safe_hex(cfg(brand, "color.mint.700"), ink)
    forbidden = set(brandlib.forbidden_text_colors(brand).keys())
    if mint_text in forbidden:
        mint_text = ink

    role_vars = [
        ("--surface-light", white),
        ("--surface-tint", tint),
        ("--surface-brand", blue),
        ("--surface-dark", navy),
        ("--surface-accent", mint),
        ("--accent", mint),
        ("--accent-2", teal),
        ("--accent-text", mint_text),
        ("--ink", ink),
        ("--ink-soft", ink_soft),
        ("--ink-on-light", on_white),
        ("--ink-on-tint", on_tint),
        ("--ink-on-brand", on_blue),
        ("--ink-on-dark", on_navy),
        ("--ink-on-accent", on_mint),
        ("--rule", rule),
        ("--grad-primary", gradient_css(brand, "blue", "linear-gradient(135deg, %s, %s)" % (navy, blue))),
        ("--grad-accent", gradient_css(brand, "mint", "linear-gradient(135deg, %s, %s)" % (mint, teal))),
        ("--grad-field", gradient_field_css(
            brand, "blue",
            "radial-gradient(closest-side at 54%% 54%%, %s 0%%, %s 82%%)" % (blue, navy))),
        ("--font-regular", "'%s'" % families.get("400", cfg(brand, "type.family", "sans-serif"))),
        ("--font-medium", "'%s'" % families.get("500", families.get("400", "sans-serif"))),
        ("--font-semibold", "'%s'" % families.get("600", families.get("500", "sans-serif"))),
        ("--font-fallback", ", ".join(cfg(brand, "type.fallback", ["sans-serif"]) or ["sans-serif"])),
        ("--canvas-w", "%dpx" % width),
        ("--canvas-h", "%dpx" % height),
        ("--safe-x", "%gpx" % safe_x),
        ("--safe-y", "%gpx" % safe_y),
        ("--safe-pct", "%g" % safe_pct),
        # Additive, for templates that want to reflow for a chrome-bearing
        # canvas. --safe-top/--safe-bottom already fold the reserve into the
        # safe margin, so a template can use them in place of --safe-y and be
        # correct on every format.
        ("--chrome-top", "%gpx" % chrome_top),
        ("--chrome-bottom", "%gpx" % chrome_bottom),
        ("--safe-top", "%gpx" % max(safe_y, chrome_top)),
        ("--safe-bottom", "%gpx" % max(safe_y, chrome_bottom)),
        ("--caption-size", "%gpx" % as_float(fmt.get("captionSizePt"), 28.0)),
    ]

    for variant in ("primary", "reversed", "monoWhite"):
        rel = cfg(brand, "logo.variants.%s.file" % variant)
        var = "--logo-" + re.sub(r"([A-Z])", r"-\1", variant).lower()
        if rel:
            path = resolve_path(rel, [brand.get("_dir", "")])
            if path:
                role_vars.append((var, "url('%s')" % file_url(path)))
                role_vars.append((var + "-aspect", "%g" % as_float(
                    cfg(brand, "logo.variants.%s.aspect" % variant), 4.0)))
            else:
                warnings.append("logo variant '%s' not found at %s" % (variant, rel))

    lines = [":root{"]
    for name, value in collect_color_vars(brand):
        lines.append("  %s: %s;" % (name, value))
    for name, value in role_vars:
        lines.append("  %s: %s;" % (name, value))
    lines.append("}")

    return "<style id=\"brand-vars\">\n%s\n%s\n</style>" % (faces, "\n".join(lines))


# ---------------------------------------------------------------------------
# IR loading and validation
# ---------------------------------------------------------------------------

def load_ir(path):
    # type: (str) -> Dict[str, Any]
    if not os.path.isfile(path):
        raise BuildError("IR file not found: %s" % path)
    try:
        with open(path, "r") as handle:
            data = json.load(handle)
    except ValueError as exc:
        raise BuildError("%s is not valid JSON: %s" % (path, exc))
    if not isinstance(data, dict):
        raise BuildError("%s must contain a JSON object at the top level" % path)
    return data


def validate_ir(ir, ir_path):
    # type: (Dict[str, Any], str) -> None
    kind = ir.get("kind")
    if kind not in VALID_KINDS:
        raise BuildError(
            "%s: kind must be one of %s (found %r)"
            % (ir_path, " / ".join(VALID_KINDS), kind))
    scenes = ir.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise BuildError("%s: 'scenes' must be a non-empty array" % ir_path)
    for index, scene in enumerate(scenes, 1):
        where = "%s: scene %d" % (ir_path, index)
        if not isinstance(scene, dict):
            raise BuildError("%s must be an object" % where)
        visual = scene.get("visual")
        if not isinstance(visual, dict):
            raise BuildError("%s ('%s') has no 'visual' object" % (where, scene.get("id", "?")))
        vkind = visual.get("kind")
        if vkind not in VALID_VISUAL_KINDS:
            raise BuildError(
                "%s ('%s'): visual.kind must be one of %s (found %r)"
                % (where, scene.get("id", "?"), " / ".join(VALID_VISUAL_KINDS), vkind))
        if vkind == "slide" and visual.get("slide") in (None, ""):
            raise BuildError(
                "%s ('%s'): visual.kind is 'slide' but visual.slide is null"
                % (where, scene.get("id", "?")))
        if vkind == "image" and not visual.get("src"):
            raise BuildError(
                "%s ('%s'): visual.kind is 'image' but visual.src is empty"
                % (where, scene.get("id", "?")))


# ---------------------------------------------------------------------------
# deck rendering: pptx -> pdf -> one PNG per slide
# ---------------------------------------------------------------------------

def count_pptx_slides(deck_path):
    # type: (str) -> Optional[int]
    try:
        from pptx import Presentation  # type: ignore
    except ImportError:
        return None
    try:
        return len(Presentation(deck_path).slides)
    except Exception as exc:  # pragma: no cover - corrupt deck
        raise BuildError("could not open %s with python-pptx: %s" % (deck_path, exc))


def deck_aspect(deck_path):
    # type: (str) -> Optional[float]
    """The deck's own width/height ratio, or None when it cannot be read."""
    try:
        from pptx import Presentation  # type: ignore
    except ImportError:
        return None
    try:
        pres = Presentation(deck_path)
        slide_w = float(pres.slide_width or 0)
        slide_h = float(pres.slide_height or 0)
    except Exception:  # noqa: BLE001 - an unreadable deck is handled downstream
        return None
    if slide_w <= 0 or slide_h <= 0:
        return None
    return slide_w / slide_h


def fit_box(aspect, width, height):
    # type: (Optional[float], int, int) -> Tuple[int, int]
    """Largest box inside ``width`` x ``height`` that preserves ``aspect``.

    A 16:9 deck rasterised straight onto a 1080x1920 vertical canvas would be
    stretched to twice its height. Rasterising into the fitted box instead keeps
    the slide's own geometry and lets the scale/pad stage letterbox it, which is
    what it already does for every other still.
    """
    if not aspect or aspect <= 0 or width <= 0 or height <= 0:
        return (width, height)
    canvas = float(width) / float(height)
    if abs(aspect - canvas) < 1e-6:
        return (width, height)
    if aspect > canvas:
        return (width, max(2, int(round(width / aspect))))
    return (max(2, int(round(height * aspect))), height)


def render_deck_slides(tools, deck_path, work_dir, width, height, log):
    # type: (Dict[str, Optional[str]], str, str, int, int, Any) -> List[str]
    """Convert a .pptx to one PNG per slide, fitted to the delivery canvas."""
    soffice = tools.get("soffice")
    if not soffice:
        raise BuildError(
            "LibreOffice (soffice) is required to rasterise %s but was not found on PATH.\n"
            "Install it (brew install --cask libreoffice) or set BRAND_STUDIO_SOFFICE."
            % os.path.basename(deck_path))

    expected = count_pptx_slides(deck_path)
    raster_w, raster_h = fit_box(deck_aspect(deck_path), width, height)
    if (raster_w, raster_h) != (width, height):
        log("  the deck's aspect does not match the %dx%d canvas; rasterising at "
            "%dx%d and letterboxing on the way in"
            % (width, height, raster_w, raster_h))
    out_dir = os.path.join(work_dir, "slides")
    os.makedirs(out_dir, exist_ok=True)
    profile = os.path.join(work_dir, "lo-profile")

    log("  converting %s to PDF with LibreOffice" % os.path.basename(deck_path))
    run([soffice, "-env:UserInstallation=%s" % file_url(profile),
         "--headless", "--norestore", "--convert-to", "pdf",
         "--outdir", out_dir, deck_path],
        "soffice --convert-to pdf", timeout=600)

    stem = os.path.splitext(os.path.basename(deck_path))[0]
    pdf_path = os.path.join(out_dir, stem + ".pdf")
    if not os.path.isfile(pdf_path):
        produced = [f for f in os.listdir(out_dir) if f.lower().endswith(".pdf")]
        if not produced:
            raise BuildError(
                "LibreOffice produced no PDF for %s. Check that the deck opens cleanly."
                % deck_path)
        pdf_path = os.path.join(out_dir, produced[0])

    frames = []  # type: List[str]
    pdftoppm = tools.get("pdftoppm")
    if pdftoppm:
        # 144dpi renders a 13.333x7.5in slide at exactly 1920x1080; -scale-to-*
        # then pins it to the fitted box for whatever canvas is being delivered.
        log("  rasterising the PDF with pdftoppm at %dx%d" % (raster_w, raster_h))
        prefix = os.path.join(out_dir, "slide")
        run([pdftoppm, "-r", "144", "-png", "-scale-to-x", str(raster_w),
             "-scale-to-y", str(raster_h), pdf_path, prefix],
            "pdftoppm", timeout=900)
        frames = sorted(
            os.path.join(out_dir, f) for f in os.listdir(out_dir)
            if f.startswith("slide-") and f.lower().endswith(".png"))
    else:
        log("  pdftoppm not found; falling back to soffice --convert-to png")
        png_dir = os.path.join(work_dir, "slides-png")
        os.makedirs(png_dir, exist_ok=True)
        run([tools["soffice"], "-env:UserInstallation=%s" % file_url(profile),
             "--headless", "--norestore", "--convert-to",
             "png:impress_png_Export:{\"PixelWidth\":{\"type\":\"long\",\"value\":%d},"
             "\"PixelHeight\":{\"type\":\"long\",\"value\":%d}}" % (raster_w, raster_h),
             "--outdir", png_dir, deck_path],
            "soffice --convert-to png", timeout=900)
        frames = sorted(
            os.path.join(png_dir, f) for f in os.listdir(png_dir)
            if f.lower().endswith(".png"))

    if not frames:
        raise BuildError("no slide PNGs were produced from %s" % deck_path)

    if expected is not None and len(frames) != expected:
        raise BuildError(
            "slide rasterisation mismatch for %s: the deck has %d slides but %d PNG(s) "
            "were produced.\n%s"
            % (os.path.basename(deck_path), expected, len(frames),
               "Install poppler for a reliable multi-page rasteriser: brew install poppler"
               if not pdftoppm else
               "Re-run with a clean work directory, or inspect %s" % out_dir))

    log("  rendered %d slide frame(s)" % len(frames))
    return frames


# ---------------------------------------------------------------------------
# HTML rendering: templates -> deterministic PNG frame sequences
# ---------------------------------------------------------------------------

def template_path(name):
    # type: (str) -> str
    root = brandlib.plugin_root()
    stem = os.path.splitext(str(name))[0]
    stem = re.sub(r"[^0-9A-Za-z_.-]", "", stem) or "scene"
    path = os.path.join(root, "templates", "video", stem + ".html")
    if not os.path.isfile(path):
        available = []
        tdir = os.path.join(root, "templates", "video")
        if os.path.isdir(tdir):
            available = sorted(
                os.path.splitext(f)[0] for f in os.listdir(tdir) if f.endswith(".html"))
        raise BuildError(
            "motion template '%s' not found at %s. Available: %s"
            % (name, path, ", ".join(available) or "(none)"))
    return path


def write_scene_html(template, brand_vars, data, dest):
    # type: (str, str, Dict[str, Any], str) -> str
    with open(template, "r") as handle:
        html = handle.read()
    # Substitution is a plain str.replace, so it hits EVERY occurrence. A marker
    # written a second time in a descriptive comment would take a copy of the
    # whole brand style block -- @font-face rules and ~60 custom properties --
    # into that comment, and would break the comment outright the moment a
    # substituted value contained '-->'. Fail loudly instead.
    for marker in ("{{BRAND_VARS}}", "{{SCENE_DATA}}"):
        count = html.count(marker)
        if count == 0 and marker == "{{BRAND_VARS}}":
            raise BuildError("template %s has no %s marker" % (template, marker))
        if count > 1:
            raise BuildError(
                "template %s contains %s %d times; substitution replaces every "
                "occurrence, so the substituted block would be injected %d times. "
                "Refer to the marker without writing it literally in comments."
                % (template, marker, count, count))
    payload = json.dumps(data, ensure_ascii=False)
    payload = payload.replace("</", "<\\/")  # never terminate the script tag early
    html = html.replace("{{BRAND_VARS}}", brand_vars)
    html = html.replace("{{SCENE_DATA}}", payload)
    with open(dest, "w") as handle:
        handle.write(html)
    return dest


CHROME_BASE_FLAGS = [
    "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--disable-extensions", "--disable-lcd-text", "--allow-file-access-from-files",
    "--hide-scrollbars", "--force-device-scale-factor=1", "--virtual-time-budget=1500",
]
# Old headless took --default-background-color=0; the current one insists on
# 8 hex digits. Try the modern spelling, then the legacy one, then neither.
CHROME_BG_FLAGS = ["--default-background-color=00000000", "--default-background-color=0", None]
_CHROME_BG = {"flag": CHROME_BG_FLAGS[0], "locked": False}


def png_is_complete(path):
    # type: (str) -> bool
    """True once a PNG has both its signature and a terminating IEND chunk."""
    try:
        size = os.path.getsize(path)
        if size < 24:
            return False
        with open(path, "rb") as handle:
            if handle.read(8) != b"\x89PNG\r\n\x1a\n":
                return False
            handle.seek(-12, os.SEEK_END)
            return handle.read(12)[4:8] == b"IEND"
    except OSError:
        return False


def chrome_shot_once(chrome, url, out_png, width, height, profile_dir, background, timeout):
    # type: (str, str, str, int, int, Optional[str], Optional[str], float) -> bool
    """One screenshot attempt. Returns True when a complete PNG landed.

    Headless Chrome reliably writes the screenshot but does not always exit
    afterwards, so the process is driven to the file rather than to its exit
    code: as soon as a complete PNG appears the browser is torn down.
    """
    cmd = [chrome] + list(CHROME_BASE_FLAGS)
    if profile_dir:
        # Concurrent instances contend on a shared profile lock, so every
        # worker gets its own user-data-dir.
        cmd.append("--user-data-dir=%s" % profile_dir)
    if background:
        cmd.append(background)
    cmd += ["--window-size=%d,%d" % (width, height),
            "--screenshot=%s" % out_png, url]

    if os.path.exists(out_png):
        try:
            os.remove(out_png)
        except OSError:
            pass

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise BuildError("headless Chrome failed to start: %s" % exc)

    deadline = time.time() + timeout
    done = False
    try:
        while time.time() < deadline:
            if png_is_complete(out_png):
                done = True
                break
            if proc.poll() is not None:
                done = png_is_complete(out_png)
                break
            time.sleep(0.05)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
    return done


def chrome_shot(chrome, url, out_png, width, height, profile_dir=None):
    # type: (str, str, str, int, int, Optional[str]) -> None
    if _CHROME_BG["locked"]:
        attempts = [_CHROME_BG["flag"], _CHROME_BG["flag"]]  # the flag plus one retry
    else:
        attempts = list(CHROME_BG_FLAGS)
    for background in attempts:
        if chrome_shot_once(chrome, url, out_png, width, height,
                            profile_dir, background, 90.0):
            if not _CHROME_BG["locked"]:
                _CHROME_BG["flag"] = background
                _CHROME_BG["locked"] = True
            return
    raise BuildError(
        "headless Chrome produced no screenshot for %s\n"
        "Check that the template renders standalone: open it in a browser." % url)


def render_motion_sequence(chrome, html_path, frames_dir, total_frames, anim_frames,
                           width, height, jobs, log, profile_root):
    # type: (str, str, str, int, int, int, int, int, Any, str) -> str
    """Render ``anim_frames`` unique frames, then hold the last one."""
    os.makedirs(frames_dir, exist_ok=True)
    anim_frames = max(1, min(anim_frames, total_frames))
    base_url = file_url(html_path)

    profiles = queue.Queue()  # type: Any
    for worker in range(max(1, jobs)):
        path = os.path.join(profile_root, "w%d" % worker)
        os.makedirs(path, exist_ok=True)
        profiles.put(path)

    def one(index):
        t = 1.0 if anim_frames <= 1 else float(index) / float(anim_frames - 1)
        out_png = os.path.join(frames_dir, "f_%06d.png" % index)
        profile = profiles.get()
        try:
            chrome_shot(chrome, "%s?t=%.6f&frame=%d&of=%d" % (base_url, t, index, anim_frames),
                        out_png, width, height, profile)
        finally:
            profiles.put(profile)
        return out_png

    # Frame 0 renders on its own: it validates the template and settles which
    # Chrome flag spelling this build accepts before workers fan out.
    one(0)
    errors = []  # type: List[BaseException]
    if jobs > 1 and anim_frames > 2:
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = [pool.submit(one, i) for i in range(1, anim_frames)]
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except BaseException as exc:  # noqa: BLE001 - re-raised below
                    errors.append(exc)
    else:
        for i in range(1, anim_frames):
            one(i)
    if errors:
        raise errors[0]

    last = os.path.join(frames_dir, "f_%06d.png" % (anim_frames - 1))
    for index in range(anim_frames, total_frames):
        link_or_copy(last, os.path.join(frames_dir, "f_%06d.png" % index))
    log("    %d frame(s) (%d rendered, %d held)"
        % (total_frames, anim_frames, total_frames - anim_frames))
    return os.path.join(frames_dir, "f_%06d.png")


# ---------------------------------------------------------------------------
# voiceover
# ---------------------------------------------------------------------------

def available_say_voices(say_bin):
    # type: (str) -> List[str]
    try:
        proc = subprocess.run([say_bin, "-v", "?"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return []
    text = (proc.stdout or b"").decode("utf-8", "replace")
    voices = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.match(r"^([^\s]+(?:\s[^\s]+)?)\s{2,}", line)
        if match:
            voices.append(match.group(1).strip())
    return voices


def estimate_vo_seconds(text, wpm):
    # type: (str, float) -> float
    words = brandlib.word_count(text)
    if not words:
        return 0.0
    rate = max(60.0, as_float(wpm, 165.0))
    return round(words / rate * 60.0, 3)


def synth_voiceover(tools, text, dest_wav, voice, wpm, log):
    # type: (Dict[str, Optional[str]], str, str, Optional[str], float, Any) -> float
    say_bin = tools.get("say")
    if not say_bin:
        raise BuildError(
            "the brand asks for voiceover engine 'say' but /usr/bin/say was not found.\n"
            "Re-run with --no-audio, or set brand.video.voiceover.enabled to false.")
    aiff = os.path.splitext(dest_wav)[0] + ".aiff"
    cmd = [say_bin]
    if voice:
        cmd += ["-v", voice]
    cmd += ["-r", str(int(round(as_float(wpm, 165.0)))), "-o", aiff, "--", text]
    run(cmd, "say", timeout=300)
    if not os.path.isfile(aiff):
        raise BuildError("say produced no audio at %s" % aiff)
    run([tools["ffmpeg"], "-hide_banner", "-loglevel", "error", "-y",
         "-i", aiff, "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", dest_wav],
        "ffmpeg (voiceover conversion)", timeout=300)
    return ffprobe_duration(tools["ffprobe"], dest_wav)


# ---------------------------------------------------------------------------
# captions
# ---------------------------------------------------------------------------

def wrap_caption_lines(text, max_chars):
    # type: (str, int) -> List[str]
    lines = []  # type: List[str]
    current = ""
    for word in clean_text(text).split():
        candidate = word if not current else current + " " + word
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        while len(word) > max_chars:
            lines.append(word[:max_chars])
            word = word[max_chars:]
        current = word
    if current:
        lines.append(current)
    return lines


def split_sentences(text):
    # type: (str) -> List[str]
    text = clean_text(text)
    if not text:
        return []
    parts = re.split(r"(?<=[.?!;:])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def caption_cues_for(text, max_chars, max_lines):
    # type: (str, int, int) -> List[List[str]]
    """Split caption text into cues of at most ``max_lines`` wrapped lines."""
    max_chars = max(12, as_int(max_chars, 42))
    max_lines = max(1, as_int(max_lines, 2))
    cues = []  # type: List[List[str]]
    pending = []  # type: List[str]
    for sentence in split_sentences(text) or ([clean_text(text)] if clean_text(text) else []):
        lines = wrap_caption_lines(sentence, max_chars)
        if not lines:
            continue
        if len(lines) > max_lines:
            if pending:
                cues.append(pending)
                pending = []
            for start in range(0, len(lines), max_lines):
                cues.append(lines[start:start + max_lines])
            continue
        if len(pending) + len(lines) <= max_lines:
            pending = pending + lines
        else:
            cues.append(pending)
            pending = lines
    if pending:
        cues.append(pending)
    return cues


def distribute_durations(weights, window, min_dur):
    # type: (Sequence[float], float, float) -> List[float]
    count = len(weights)
    if count == 0:
        return []
    window = max(0.001, float(window))
    if min_dur * count >= window:
        return [window / count] * count
    total = float(sum(weights)) or float(count)
    durs = [window * (float(w) / total) for w in weights]
    for _ in range(24):
        for i in range(count):
            if durs[i] < min_dur:
                durs[i] = min_dur
        diff = sum(durs) - window
        if abs(diff) < 1e-6:
            break
        if diff > 0:
            slack_idx = [i for i in range(count) if durs[i] > min_dur + 1e-9]
            slack = sum(durs[i] - min_dur for i in slack_idx)
            if slack <= 1e-9:
                break
            take = min(diff, slack)
            for i in slack_idx:
                durs[i] -= take * (durs[i] - min_dur) / slack
        else:
            for i in range(count):
                durs[i] += (-diff) / count
    return durs


def build_captions(elements, captions_cfg, warnings):
    # type: (List[Dict[str, Any]], Dict[str, Any], List[str]) -> List[Dict[str, Any]]
    max_chars = as_int(captions_cfg.get("maxCharsPerLine"), 42)
    max_lines = as_int(captions_cfg.get("maxLines"), 2)
    min_dur = as_float(captions_cfg.get("minDurationSec"), 1.2)

    cues = []  # type: List[Dict[str, Any]]
    for element in elements:
        if element["kind"] != "scene":
            continue
        text = clean_text(element.get("caption") or element.get("vo"))
        if not text:
            continue
        chunks = caption_cues_for(text, max_chars, max_lines)
        if not chunks:
            continue
        window_start = element.get("voStart")
        window_end = element.get("voEnd")
        if window_start is None or window_end is None or window_end - window_start < 0.2:
            window_start = element["visibleStart"]
            window_end = element["end"]
        window = max(0.4, float(window_end) - float(window_start))
        weights = [max(1.0, float(sum(len(l) for l in c))) for c in chunks]
        durs = distribute_durations(weights, window, min_dur)
        if min_dur * len(chunks) > window + 1e-6:
            warnings.append(
                "scene '%s': %d caption cue(s) do not fit %0.2fs at the brand minimum "
                "of %0.2fs each; they were split evenly instead"
                % (element.get("id"), len(chunks), window, min_dur))
        # The last cue of a scene holds until the picture changes rather than
        # cutting out with the narration: the slide is still on screen and still
        # being read, and a blank caption band under a live frame reads as a bug.
        hold_until = float(element["end"]) - 0.05
        cursor = float(window_start)
        for position, (chunk, dur) in enumerate(zip(chunks, durs)):
            start = cursor
            end = cursor + dur
            if position == len(chunks) - 1 and hold_until > end:
                end = hold_until
            cues.append({
                "index": len(cues) + 1,
                "scene": element.get("id"),
                "start": round(start, 3),
                "end": round(end, 3),
                "durationSec": round(dur, 3),
                "lines": chunk,
                "maxLineChars": max(len(l) for l in chunk),
            })
            cursor = end
        element["captionCues"] = [c["index"] for c in cues[-len(chunks):]]
    return cues


def write_srt(cues, dest):
    # type: (List[Dict[str, Any]], str) -> str
    blocks = []
    for cue in cues:
        blocks.append("%d\n%s --> %s\n%s\n" % (
            cue["index"],
            brandlib.srt_timestamp(cue["start"]),
            brandlib.srt_timestamp(cue["end"]),
            "\n".join(cue["lines"])))
    with open(dest, "w") as handle:
        handle.write("\n".join(blocks))
    return dest


def vtt_timestamp(seconds):
    # type: (float) -> str
    """Seconds -> 'HH:MM:SS.mmm' (WebVTT). Same instant as srt_timestamp."""
    return brandlib.srt_timestamp(seconds).replace(",", ".")


def write_vtt(cues, dest):
    # type: (List[Dict[str, Any]], str) -> str
    """A WebVTT sidecar carrying the same frame-snapped timings as the SRT."""
    blocks = ["WEBVTT", ""]
    for cue in cues:
        blocks.append("%d" % cue["index"])
        blocks.append("%s --> %s" % (vtt_timestamp(cue["start"]),
                                     vtt_timestamp(cue["end"])))
        blocks.extend(cue["lines"])
        blocks.append("")
    with open(dest, "w") as handle:
        handle.write("\n".join(blocks))
    return dest


# ---------------------------------------------------------------------------
# frame-accurate caption burn-in
#
# Captions are drawn with pillow, from the brand's own .ttf, into full-canvas
# RGBA PNGs, and composited with ffmpeg's `overlay` filter. That is not a
# workaround for a missing libass -- it is the only thing that can work here and
# it is the better of the two anyway:
#
#   * this ffmpeg is built without libass, freetype, fontconfig or harfbuzz, so
#     neither the `subtitles` nor the `drawtext` filter exists at all;
#   * libass would resolve "Poppins Medium" through fontconfig, which is also
#     missing, so it would silently substitute a system face even if it were
#     present. Pillow opens the brand's own file directly;
#   * the box radius, padding, colour and opacity come out exactly as the brand
#     declares them rather than as ASS BorderStyle approximates them.
#
# Everything is placed on the FRAME GRID. SRT is millisecond-based and a frame
# at 30fps is 33.333ms, so an unsnapped cue turns on part-way through a frame:
# the burned text and the sidecar then disagree about which frame carries the
# caption, and a boundary that lands mid-frame flickers. Every boundary is
# rounded to a frame number, and the frame number is what both the overlay and
# the sidecars are written from.
# ---------------------------------------------------------------------------

#: Beyond this many cues the per-cue overlay chain (one ffmpeg input and one
#: filter stage each) stops being reasonable, and the captions are pre-composited
#: into a single frame sequence instead: one input, one overlay.
CAPTION_OVERLAY_MAX = 40

#: Caption box geometry, as multiples of the caption size. The brand declares no
#: padding or radius for video captions, so they are derived from the type size
#: -- which is what keeps the box proportional across formats that set different
#: caption sizes.
CAPTION_PAD_X_EM = 0.62
CAPTION_PAD_Y_EM = 0.34
CAPTION_RADIUS_EM = 0.28
CAPTION_LINE_EM = 1.34


def snap_cues_to_frames(cues, fps, min_dur, warnings):
    # type: (List[Dict[str, Any]], int, float, List[str]) -> List[Dict[str, Any]]
    """Put every cue boundary on the frame grid, in place.

    ``f = round(t * fps)`` and then ``t = f / fps``. The snapped values are used
    for BOTH the burned overlay windows and the SRT/VTT sidecars, so the sidecar
    and the burned text always describe the same frames.

    Two rules resolve the collisions that rounding creates:

      * ``minDurationSec`` is enforced afterwards, in whole frames, by pushing
        the OUT edge later -- never by moving the IN edge;
      * where two cues would then be lit on the same frame the EARLIER one
        yields. A cue is never delayed past the moment it was written for, so
        narration and caption stay in step; the cue in front of it is shortened
        instead, down to a single frame if it comes to that.
    """
    if not cues or fps <= 0:
        return cues

    fps = int(fps)
    count = len(cues)
    min_frames = max(1, int(math.ceil(float(min_dur) * fps - 1e-9)))

    f_in = [int(round(float(c["start"]) * fps)) for c in cues]
    f_out = [int(round(float(c["end"]) * fps)) for c in cues]

    # minDurationSec, in whole frames, by extending the OUT edge.
    for i in range(count):
        if f_out[i] < f_in[i] + min_frames:
            f_out[i] = f_in[i] + min_frames

    # De-overlap from the back, so the earlier cue is the one that gives way.
    squeezed = 0
    for i in range(count - 2, -1, -1):
        if f_out[i] > f_in[i + 1]:
            f_out[i] = f_in[i + 1]
        if f_out[i] <= f_in[i]:
            # Both cues rounded onto the same frame. The earlier one keeps the
            # single frame before the later one lights up.
            f_in[i] = max(0, f_in[i + 1] - 1)
            f_out[i] = max(f_in[i] + 1, f_in[i + 1])
        if f_out[i] - f_in[i] < min_frames:
            squeezed += 1

    for i in range(count):
        if f_in[i] < 0:
            f_in[i] = 0
        if f_out[i] <= f_in[i]:
            f_out[i] = f_in[i] + 1
        cue = cues[i]
        cue["startFrame"] = f_in[i]
        cue["endFrame"] = f_out[i]
        cue["frameCount"] = f_out[i] - f_in[i]
        cue["start"] = round(f_in[i] / float(fps), 6)
        cue["end"] = round(f_out[i] / float(fps), 6)
        cue["durationSec"] = round(cue["frameCount"] / float(fps), 6)
        cue["text"] = " ".join(cue.get("lines") or [])

    if squeezed:
        warnings.append(
            "%d caption cue(s) were shortened below the brand minimum of %.2fs so that "
            "no two cues share a frame; the narration they belong to is unchanged"
            % (squeezed, min_dur))
    for i in range(count - 1):
        if cues[i]["endFrame"] > cues[i + 1]["startFrame"]:
            warnings.append(
                "caption cues %d and %d still overlap after frame snapping (frames %d-%d "
                "and %d-%d); the later cue draws on top"
                % (cues[i]["index"], cues[i + 1]["index"],
                   cues[i]["startFrame"], cues[i]["endFrame"],
                   cues[i + 1]["startFrame"], cues[i + 1]["endFrame"]))
    return cues


def caption_enable_window(cue, fps):
    # type: (Dict[str, Any], int) -> Tuple[float, float]
    """(A, B) for overlay's ``enable='between(t,A,B)'``.

    Half a frame is taken off both edges, so the window brackets frames
    startFrame .. endFrame-1 and nothing else. No frame ever carries a PTS on
    the boundary itself, so two adjacent cues -- where one ends on exactly the
    frame the next begins -- can never both be lit, even though `between` is
    inclusive at both ends.
    """
    return ((cue["startFrame"] - 0.5) / float(fps),
            (cue["endFrame"] - 0.5) / float(fps))


def caption_font_file(brand, captions_cfg):
    # type: (Dict[str, Any], Dict[str, Any]) -> Optional[str]
    """The font file captions are drawn with, from the brand's own assets.

    ``captions.font`` names a face ("Poppins Medium"); the brand ships the file
    ("Poppins-Medium.ttf"). Matched on the name with everything but letters and
    digits stripped, then by weight fragment, then by whatever face is there.
    """
    fonts_dir = os.path.join(brand.get("_dir", ""), "assets", "fonts")
    if not os.path.isdir(fonts_dir):
        return None
    available = [f for f in sorted(os.listdir(fonts_dir))
                 if f.lower().endswith((".ttf", ".otf"))]
    if not available:
        return None

    def norm(text):
        return re.sub(r"[^a-z0-9]", "", str(text).lower())

    wanted = norm(captions_cfg.get("font") or cfg(brand, "type.family", ""))
    if wanted:
        for fname in available:
            if norm(os.path.splitext(fname)[0]) == wanted:
                return os.path.join(fonts_dir, fname)
    for weight in (500, 400):
        for frag in FONT_WEIGHT_FILES.get(weight, ()):
            for fname in available:
                if norm(os.path.splitext(fname)[0]).endswith(norm(frag)):
                    return os.path.join(fonts_dir, fname)
    return os.path.join(fonts_dir, available[0])


def hex_rgb(value, default):
    # type: (Any, str) -> Tuple[int, int, int]
    text = safe_hex(value, default).lstrip("#")
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def caption_style(brand, video, fmt, font_file):
    # type: (Dict[str, Any], Dict[str, Any], Dict[str, Any], Optional[str]) -> Dict[str, Any]
    """Everything the pillow renderer needs, resolved for one delivery format.

    The size is the FORMAT's captionSizePt, read as pixels on that format's own
    canvas -- which is exactly how libass would have read the brand's sizePt
    against the render height. The bottom margin is the format's, so it already
    clears whatever the format reserves for platform chrome.
    """
    caps = video.get("captions") or {}
    width = int(fmt["width"])
    height = int(fmt["height"])
    size_px = max(8, int(round(as_float(fmt.get("captionSizePt"),
                                        as_float(caps.get("sizePt"), 28.0)))))
    return {
        "fontFile": font_file,
        "sizePx": size_px,
        "fg": hex_rgb(caps.get("color"), "#FFFFFF"),
        "bg": hex_rgb(caps.get("background"),
                      safe_hex(cfg(brand, "color.brand.navy"), "#0F0A6C")),
        "opacity": max(0.0, min(1.0, as_float(caps.get("backgroundOpacity"), 0.82))),
        "bottomPx": int(round(as_float(fmt.get("captionBottomMarginPct"),
                                       as_float(caps.get("bottomMarginPct"), 8.0))
                              / 100.0 * height)),
        "safePx": int(round(as_float(fmt.get("safeMarginPct"),
                                     as_float(video.get("safeMarginPct"), 5.0))
                            / 100.0 * width)),
        "width": width,
        "height": height,
    }


def render_caption_png(cue, dest, style):
    # type: (Dict[str, Any], str, Dict[str, Any]) -> str
    """One full-canvas RGBA frame carrying a single cue's caption block.

    Full canvas rather than a cropped box so the composite is a plain
    ``overlay=0:0``: the placement is baked into the image and there is no
    second coordinate system to keep in step with the format.
    """
    from PIL import Image, ImageDraw, ImageFont  # noqa: N813

    width = style["width"]
    height = style["height"]
    lines = [str(line) for line in (cue.get("lines") or []) if str(line).strip()]
    if not lines:
        lines = [""]

    font = ImageFont.truetype(style["fontFile"], style["sizePx"])
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    widths = [draw.textlength(line, font=font) for line in lines]
    ascent, descent = font.getmetrics()
    line_h = int(round(style["sizePx"] * CAPTION_LINE_EM))
    pad_x = int(round(style["sizePx"] * CAPTION_PAD_X_EM))
    pad_y = int(round(style["sizePx"] * CAPTION_PAD_Y_EM))
    radius = int(round(style["sizePx"] * CAPTION_RADIUS_EM))

    max_box = max(1, width - 2 * style["safePx"])
    box_w = int(min(max_box, max(widths) + 2 * pad_x))
    box_h = line_h * len(lines) + 2 * pad_y
    box_x0 = int(round((width - box_w) / 2.0))
    # bottomPx is the margin that must stay CLEAR, and rounded_rectangle draws
    # its end coordinate inclusively, so the last row the box may occupy is one
    # above the margin. Without the -1 a vertical caption puts a single row of
    # box into the band the format reserves for platform chrome.
    box_y1 = height - style["bottomPx"] - 1
    box_y0 = box_y1 - box_h
    if box_y0 < 0:
        box_y0, box_y1 = 0, min(box_h, height - 1)

    draw.rounded_rectangle([box_x0, box_y0, box_x0 + box_w, box_y1],
                           radius=radius, fill=style["bg"] +
                           (int(round(255 * style["opacity"])),))
    for index, line in enumerate(lines):
        text_x = (width - widths[index]) / 2.0
        text_y = box_y0 + pad_y + index * line_h + (line_h - (ascent + descent)) / 2.0
        draw.text((text_x, text_y), line, font=font, fill=style["fg"] + (255,))

    image.save(dest)
    return dest


def render_caption_overlays(cues, work_dir, style, fps, total_sec, log):
    # type: (List[Dict[str, Any]], str, Dict[str, Any], int, float, Any) -> Dict[str, Any]
    """Draw the captions and decide how they reach the filtergraph.

    Up to CAPTION_OVERLAY_MAX cues each become one PNG, one ffmpeg input and one
    overlay stage gated on its own frame-snapped window. Past that the same PNGs
    are pre-composited into a single frame sequence -- one PNG per frame of the
    film, hard-linked, so the disk cost is a handful of images -- which enters
    the graph as ONE input and ONE overlay. The second path is frame-exact by
    construction: frame N of the caption track is frame N of the film.
    """
    caption_dir = os.path.join(work_dir, "captions")
    os.makedirs(caption_dir, exist_ok=True)

    rows = []  # type: List[Dict[str, Any]]
    for cue in cues:
        dest = os.path.join(caption_dir, "cue-%04d.png" % cue["index"])
        render_caption_png(cue, dest, style)
        enable_from, enable_to = caption_enable_window(cue, fps)
        rows.append({"index": cue["index"], "file": dest,
                     "startFrame": cue["startFrame"], "endFrame": cue["endFrame"],
                     "enableFrom": enable_from, "enableTo": enable_to})

    if len(rows) <= CAPTION_OVERLAY_MAX:
        log("  drew %d caption frame(s) with pillow; composited as %d overlay(s)"
            % (len(rows), len(rows)))
        return {"mode": "per-cue", "rows": rows, "track": None}

    from PIL import Image  # noqa: N813

    blank = os.path.join(caption_dir, "blank.png")
    Image.new("RGBA", (style["width"], style["height"]), (0, 0, 0, 0)).save(blank)

    frames_dir = os.path.join(caption_dir, "track")
    os.makedirs(frames_dir, exist_ok=True)
    total_frames = max(1, int(round(float(total_sec) * fps)))
    source = [blank] * total_frames
    for row in rows:
        for frame in range(max(0, row["startFrame"]),
                           min(row["endFrame"], total_frames)):
            source[frame] = row["file"]
    for frame in range(total_frames):
        link_or_copy(source[frame], os.path.join(frames_dir, "f_%06d.png" % frame))

    log("  drew %d caption frame(s) with pillow; pre-composited into a single "
        "%d-frame caption track (over the %d-cue overlay limit)"
        % (len(rows), total_frames, CAPTION_OVERLAY_MAX))
    return {"mode": "track", "rows": rows,
            "track": os.path.join(frames_dir, "f_%06d.png")}


# ---------------------------------------------------------------------------
# timeline
# ---------------------------------------------------------------------------

def resolve_transition(video, warnings):
    # type: (Dict[str, Any], List[str]) -> Tuple[Optional[str], float, str]
    raw = str(cfg(video, "transition.type", "crossfade")).strip().lower()
    duration = as_float(cfg(video, "transition.durationSec", 0.4), 0.4)
    if raw not in TRANSITION_MAP:
        warnings.append(
            "unknown transition type '%s'; falling back to crossfade" % raw)
        raw = "crossfade"
    xfade = TRANSITION_MAP[raw]
    if xfade is None or duration <= 0.001:
        return None, 0.0, raw
    return xfade, duration, raw


def build_elements(ir, brand, video, warnings):
    # type: (Dict[str, Any], Dict[str, Any], Dict[str, Any], List[str]) -> List[Dict[str, Any]]
    """Ordered list of timeline elements with resolved durations (no offsets yet)."""
    hold_min = as_float(cfg(video, "slideHoldSec.min", 3.0), 3.0)
    hold_default = as_float(cfg(video, "slideHoldSec.default", 5.0), 5.0)
    hold_max = as_float(cfg(video, "slideHoldSec.max", 12.0), 12.0)

    elements = []  # type: List[Dict[str, Any]]

    if ir.get("intro", False):
        duration = as_float(cfg(video, "intro.durationSec", 3.0), 3.0)
        data, personal_keys = intro_data(ir, brand, video)
        elements.append({
            "kind": "intro", "id": "intro", "role": "intro",
            "duration": max(0.4, duration), "declaredHold": duration,
            "holdSource": "brand.intro.durationSec",
            "visualKind": "motion", "template": "intro",
            "data": data, "personalisedBy": personal_keys,
            "mediaFile": cfg(video, "intro.file"),
        })

    for index, scene in enumerate(ir.get("scenes") or [], 1):
        visual = scene.get("visual") or {}
        vo_text = clean_text(scene.get("vo"))
        declared = as_float(scene.get("holdSec"), hold_default)
        duration = declared
        source = "declared"
        if duration < hold_min - 1e-6:
            duration = hold_min
            source = "brand.slideHoldSec.min"
        if declared > hold_max + 1e-6:
            warnings.append(
                "scene '%s' holds for %0.1fs, above the brand maximum of %0.1fs"
                % (scene.get("id", index), declared, hold_max))
        elements.append({
            "kind": "scene", "index": index,
            "id": str(scene.get("id") or "s%d" % index),
            "role": str(scene.get("role") or ""),
            "duration": duration, "declaredHold": declared, "holdSource": source,
            "visualKind": str(visual.get("kind")),
            "slide": visual.get("slide"),
            "template": (visual.get("template") or "scene"
                         if visual.get("kind") == "motion" else None),
            "src": visual.get("src"),
            "data": visual.get("data") if isinstance(visual.get("data"), dict) else {},
            "vo": vo_text,
            "caption": clean_text(scene.get("caption")) or vo_text,
        })

    if ir.get("outro", False):
        duration = as_float(cfg(video, "outro.durationSec", 3.5), 3.5)
        data, personal_keys = outro_data(ir, brand, video)
        if not personal_keys and not data.get("contact"):
            warnings.append(
                "the brand has no standard closing contact, so the shared outro "
                "carries only the lockup; set video.outro.contact in brand.json so "
                "every film closes on the same next step (meta.contact personalises "
                "a single film instead)")
        elements.append({
            "kind": "outro", "id": "outro", "role": "outro",
            "duration": max(0.4, duration), "declaredHold": duration,
            "holdSource": "brand.outro.durationSec",
            "visualKind": "motion", "template": "outro",
            "data": data, "personalisedBy": personal_keys,
            "mediaFile": cfg(video, "outro.file"),
        })

    return elements


def personalised_by(ir, kind):
    # type: (Dict[str, Any], str) -> List[str]
    """The meta keys, if any, with which this IR opts out of the shared bookend."""
    meta = ir.get("meta") or {}
    return [key for key in PERSONAL_META_KEYS.get(kind, ())
            if clean_text(meta.get(key))]


def brand_name_may_be_typeset(brand):
    # type: (Dict[str, Any]) -> bool
    """False when the brand forbids retyping its wordmark beside the logo.

    A lockup logo already spells the name; setting it again in live type is
    exactly the 'retype wordmark' violation brands list under logo.forbidden,
    and on the intro card the two would sit one above the other.
    """
    forbidden = (brand.get("logo") or {}).get("forbidden") or []
    return "retype wordmark" not in [str(item).strip().lower() for item in forbidden]


def brand_contact_line(brand, video):
    # type: (Dict[str, Any], Dict[str, Any]) -> str
    """The brand's standard closing contact, from the brand profile.

    A brand writes this once (``video.outro.contact`` is its most specific home)
    and every outro carries it. The IR is deliberately not consulted here:
    ``meta.contact`` personalises a single film and is handled as an opt-out.
    """
    for path in BRAND_CONTACT_PATHS:
        value = cfg(video, path)
        if isinstance(value, str) and value.strip():
            return clean_text(value)
    value = brand.get("contact")
    if isinstance(value, str) and value.strip():
        return clean_text(value)
    if isinstance(value, dict):
        for key in ("video", "line", "primary"):
            if isinstance(value.get(key), str) and value[key].strip():
                return clean_text(value[key])
    return ""


def intro_data(ir, brand, video):
    # type: (Dict[str, Any], Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]
    """Scene data for the intro, plus the meta keys that personalised it.

    The intro is a BRAND asset: by default it is the logo reveal on the brand
    gradient carrying the brand name, and nothing at all from this particular
    film. That is what makes it identical across every video for the brand, and
    therefore cacheable -- putting the film's own title here would mean a fresh
    render, and a different opening, for every video ever made.

    A film that genuinely needs its own opening card sets ``meta.introTitle``
    (and optionally ``meta.introEyebrow``). Doing so is an explicit opt-out: the
    returned key list is non-empty and the caller skips the cache.
    """
    meta = ir.get("meta") or {}
    keys = personalised_by(ir, "intro")
    data = {
        "variant": "intro",
        "theme": "gradient",
        # The intro template has no brand-name slot of its own, so the name
        # goes in the title -- unless the brand forbids retyping its wordmark,
        # in which case the revealed logo is the name and the card stays clean.
        "title": (clean_text(brand.get("name"))
                  if brand_name_may_be_typeset(brand) else ""),
        "eyebrow": "",
        "logo": "reversed",
        "brandName": clean_text(brand.get("name")),
    }
    if clean_text(meta.get("introTitle")):
        data["title"] = clean_text(meta.get("introTitle"))
    if clean_text(meta.get("introEyebrow")):
        data["eyebrow"] = clean_text(meta.get("introEyebrow"))
    return data, keys


def outro_data(ir, brand, video):
    # type: (Dict[str, Any], Dict[str, Any], Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]
    """Scene data for the outro, plus the meta keys that personalised it.

    Like the intro, the default outro is pure brand: the lockup, the brand name
    and the brand's standard contact from the brand profile. No call to action
    is scraped out of the film, because that would make the closing card
    video-specific and defeat the cache.

    ``meta.outroTitle`` (or ``meta.contact`` / ``meta.outroEyebrow``) opts a
    single film out. Only then is the old behaviour restored: an explicit
    outroTitle wins, and if the film sets one the call-to-action fallback fills
    in from meta.cta or the last call-to-action scene.
    """
    meta = ir.get("meta") or {}
    keys = personalised_by(ir, "outro")
    data = {
        "variant": "outro",
        "theme": "navy",
        "title": "",
        "contact": brand_contact_line(brand, video),
        "eyebrow": "",
        "logo": "reversed",
        "brandName": clean_text(brand.get("name")),
    }
    if not keys:
        return data, keys

    title = clean_text(meta.get("outroTitle"))
    if not title:
        # Personalised some other way (contact or eyebrow) but with no headline
        # of its own: the film's call to action fills it, exactly as the outro
        # always did before the bookends became brand assets.
        title = clean_text(meta.get("cta"))
        if not title:
            for scene in reversed(ir.get("scenes") or []):
                if str(scene.get("role")) == "call-to-action":
                    title = clean_text(scene.get("caption")) or clean_text(scene.get("vo"))
                    break
    data["title"] = title
    if clean_text(meta.get("contact")):
        data["contact"] = clean_text(meta.get("contact"))
    if clean_text(meta.get("outroEyebrow")):
        data["eyebrow"] = clean_text(meta.get("outroEyebrow"))
    return data, keys


def apply_voiceover_durations(elements, vo_info):
    # type: (List[Dict[str, Any]], Dict[str, Dict[str, Any]]) -> None
    for element in elements:
        info = vo_info.get(element.get("id")) if element["kind"] == "scene" else None
        if not info:
            continue
        element["voFile"] = info.get("file")
        element["voDurationSec"] = round(float(info["duration"]), 3)
        element["voDurationSource"] = info.get("source", "measured")
        needed = float(info["duration"]) + VO_TAIL_SEC
        if element["duration"] < needed - 1e-6:
            element["duration"] = round(needed, 3)
            element["holdSource"] = "voiceover"


def lay_out_timeline(elements, transition_sec, warnings):
    # type: (List[Dict[str, Any]], float, List[Dict[str, Any]]) -> Tuple[float, float]
    """Assign start / visibleStart / end to every element. Returns (total, transition)."""
    if not elements:
        raise BuildError("nothing to render: the timeline has no elements")

    shortest = min(e["duration"] for e in elements)
    effective = transition_sec
    if len(elements) > 1 and effective > 0:
        cap = max(0.0, shortest * 0.4)
        if effective > cap:
            warnings.append(
                "transition shortened from %0.2fs to %0.2fs so it fits the shortest "
                "clip (%0.2fs)" % (effective, cap, shortest))
            effective = cap

    merged = elements[0]["duration"]
    elements[0]["start"] = 0.0
    elements[0]["visibleStart"] = 0.0
    elements[0]["end"] = round(merged, 3)
    elements[0]["xfadeOffset"] = None
    for element in elements[1:]:
        offset = merged - effective
        element["start"] = round(offset, 3)
        element["visibleStart"] = round(merged, 3)
        element["xfadeOffset"] = round(offset, 3)
        merged = merged + element["duration"] - effective
        element["end"] = round(merged, 3)

    for element in elements:
        element["duration"] = round(element["duration"], 3)
        if element["kind"] == "scene" and element.get("voDurationSec"):
            element["voStart"] = element["visibleStart"]
            element["voEnd"] = round(element["visibleStart"] + element["voDurationSec"], 3)

    return round(merged, 3), effective


# ---------------------------------------------------------------------------
# ffmpeg graph
# ---------------------------------------------------------------------------

def escape_filter_value(value):
    # type: (str) -> str
    out = str(value)
    out = out.replace("\\", "\\\\")
    out = out.replace(":", "\\:")
    out = out.replace("'", "\\'")
    out = out.replace("[", "\\[").replace("]", "\\]")
    out = out.replace(",", "\\,")
    return out


def duck_params(duck_db):
    # type: (Any) -> Optional[Tuple[float, float]]
    """Turn the brand's 'duck the music by N dB' into (threshold, ratio).

    A compressor reduces gain by ``headroom * (1 - 1/ratio)`` where headroom is
    how far the sidechain sits above the threshold. Narration normalised to the
    brand's voiceover target presents roughly SIDECHAIN_LEVEL_DBFS to the
    detector, so the threshold is placed ``depth + DUCK_MARGIN_DB`` below that
    and the ratio solves the equation exactly. Measured against real ``say``
    narration this lands within 0.1 dB of the requested depth.

    Returns None when no ducking was asked for.
    """
    depth = abs(as_float(duck_db, -18.0))
    if depth < 0.5:
        return None
    headroom = depth + DUCK_MARGIN_DB
    threshold = 10.0 ** ((SIDECHAIN_LEVEL_DBFS - headroom) / 20.0)
    threshold = max(0.001, min(0.5, threshold))
    ratio = max(1.5, min(20.0, headroom / DUCK_MARGIN_DB))
    return (round(threshold, 5), round(ratio, 2))


def video_input_args(element, fps, anim_fps):
    # type: (Dict[str, Any], int, int) -> List[str]
    source = element["source"]
    if element.get("sourceKind") == "sequence":
        return ["-framerate", str(anim_fps), "-start_number", "0", "-i", source]
    if element.get("sourceKind") == "video":
        return ["-t", "%.4f" % element["duration"], "-i", source]
    return ["-loop", "1", "-framerate", str(fps), "-t", "%.4f" % element["duration"],
            "-i", source]


def video_chain(index, element, fps, width, height, pad_colour, kenburns):
    # type: (int, Dict[str, Any], int, int, int, str, bool) -> str
    label = "v%d" % index
    steps = []
    use_kb = kenburns and element.get("sourceKind") == "still" and element["kind"] == "scene"
    if use_kb:
        big_w, big_h = width * 2, height * 2
        frames = max(1, int(round(element["duration"] * fps)) - 1)
        amount = KENBURNS_ZOOM - 1.0
        steps.append("scale=%d:%d:force_original_aspect_ratio=decrease:flags=lanczos"
                     % (big_w, big_h))
        steps.append("pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=%s" % (big_w, big_h, pad_colour))
        steps.append(
            "zoompan=z='min(1+%0.6f*in/%d,%0.4f)':x='iw/2-(iw/zoom/2)':"
            "y='ih/2-(ih/zoom/2)':d=1:s=%dx%d:fps=%d"
            % (amount, frames, KENBURNS_ZOOM, width, height, fps))
    else:
        steps.append("scale=%d:%d:force_original_aspect_ratio=decrease:flags=lanczos"
                     % (width, height))
        steps.append("pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=%s" % (width, height, pad_colour))
    steps.append("fps=%d" % fps)
    steps.append("format=yuv420p")
    steps.append("setsar=1")
    steps.append("settb=AVTB")
    steps.append("trim=duration=%.4f" % element["duration"])
    steps.append("setpts=PTS-STARTPTS")
    return "[%d:v]%s[%s]" % (index, ",".join(steps), label)


def build_ffmpeg_command(tools, elements, plan, args):
    # type: (Dict[str, Optional[str]], List[Dict[str, Any]], Dict[str, Any], Any) -> List[str]
    fps = plan["fps"]
    width = plan["width"]
    height = plan["height"]
    total = plan["totalDurationSec"]

    cmd = [tools["ffmpeg"], "-hide_banner", "-y"]
    filters = []  # type: List[str]

    for element in elements:
        cmd += video_input_args(element, fps, plan["animFps"])

    # Caption inputs sit between the video and the audio, so the video indices
    # (which are the element indices) and the audio indices both stay derivable.
    caption_base = len(elements)
    caption_count = 0
    caption_track = plan.get("captionTrack") if plan["burnIn"] else None
    caption_rows = (plan.get("captionOverlays") or []) if plan["burnIn"] else []
    if caption_track:
        cmd += ["-framerate", str(fps), "-start_number", "0", "-i", caption_track]
        caption_count = 1
    else:
        for row in caption_rows:
            cmd += ["-i", row["file"]]
            caption_count += 1

    for index, element in enumerate(elements):
        filters.append(video_chain(index, element, fps, width, height,
                                   plan["padColour"], plan["kenburns"]))

    # --- video: chain the crossfades -------------------------------------
    if len(elements) == 1:
        vlabel = "v0"
    elif plan["xfade"] is None:
        filters.append("%sconcat=n=%d:v=1:a=0[vcat]"
                       % ("".join("[v%d]" % i for i in range(len(elements))), len(elements)))
        vlabel = "vcat"
    else:
        vlabel = "v0"
        for index in range(1, len(elements)):
            out = "x%d" % index
            filters.append(
                "[%s][v%d]xfade=transition=%s:duration=%.4f:offset=%.4f[%s]"
                % (vlabel, index, plan["xfade"], plan["transitionSec"],
                   elements[index]["xfadeOffset"], out))
            vlabel = out

    # --- captions: composited, never libass -------------------------------
    # This ffmpeg has no subtitles or drawtext filter (no libass, freetype or
    # fontconfig), so the caption frames pillow drew are overlaid instead. Every
    # window is expressed in frames, half a frame off the grid on both edges, so
    # exactly the frames the sidecar names carry the caption and no frame ever
    # carries two.
    if caption_track:
        filters.append("[%d:v]format=rgba,fps=%d,settb=AVTB,setpts=PTS-STARTPTS[captrack]"
                       % (caption_base, fps))
        filters.append("[%s][captrack]overlay=0:0:format=auto:eof_action=repeat[vcap]"
                       % vlabel)
        vlabel = "vcap"
    elif caption_rows:
        for offset, row in enumerate(caption_rows):
            out = "vcap%d" % offset
            filters.append(
                "[%s][%d:v]overlay=0:0:format=auto:enable='between(t,%.6f,%.6f)'[%s]"
                % (vlabel, caption_base + offset, row["enableFrom"], row["enableTo"], out))
            vlabel = out

    filters.append("[%s]format=yuv420p[vout]" % vlabel)

    # --- audio ------------------------------------------------------------
    audio_label = None  # type: Optional[str]
    if plan["audioEnabled"]:
        audio_inputs, audio_parts, premaster = build_audio_graph(
            elements, plan, plan["videoInputCount"] + caption_count)
        cmd += audio_inputs
        filters.extend(audio_parts)
        if premaster:
            filters.append(
                "[%s]%s,alimiter=limit=0.95[aout]"
                % (premaster,
                   loudnorm_filter(plan["programmeLufs"], LOUDNORM_TP_MASTER,
                                   plan.get("masterLoudnorm"))))
            audio_label = "aout"

    cmd += ["-filter_complex", ";".join(filters)]
    cmd += ["-map", "[vout]"]
    if audio_label:
        cmd += ["-map", "[%s]" % audio_label]
    cmd += [
        "-c:v", plan["vcodec"], "-preset", plan["preset"], "-crf", str(plan["crf"]),
        "-pix_fmt", "yuv420p", "-r", str(fps), "-g", str(fps * 2),
        "-movflags", "+faststart",
    ]
    if audio_label:
        cmd += ["-c:a", plan["acodec"], "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    else:
        cmd += ["-an"]
    cmd += ["-t", "%.4f" % total, plan["partPath"]]
    return cmd


def build_audio_graph(elements, plan, first_input_index):
    # type: (List[Dict[str, Any]], Dict[str, Any], int) -> Tuple[List[str], List[str], Optional[str]]
    """The audio half of the graph, up to but not including the master stage.

    Returned separately from the video so the same graph can be run once for
    analysis (audio only, no encode) and once for the real render.
    """
    total = plan["totalDurationSec"]
    inputs = []  # type: List[str]
    filters = []  # type: List[str]

    vo_labels = []  # type: List[str]
    for element in elements:
        if not element.get("voFile"):
            continue
        input_index = first_input_index + len(vo_labels)
        inputs += ["-i", element["voFile"]]
        label = "a%d" % len(vo_labels)
        delay_ms = int(round(float(element["voStart"]) * 1000.0))
        filters.append(
            "[%d:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            "%s,adelay=%d:all=1[%s]"
            % (input_index,
               loudnorm_filter(plan["voLufs"], LOUDNORM_TP_VO, element.get("voLoudnorm")),
               delay_ms, label))
        vo_labels.append(label)

    vo_out = None  # type: Optional[str]
    if vo_labels:
        if len(vo_labels) == 1:
            filters.append("[%s]anull[vomix]" % vo_labels[0])
        else:
            filters.append("%samix=inputs=%d:normalize=0:dropout_transition=0[vomix]"
                           % ("".join("[%s]" % l for l in vo_labels), len(vo_labels)))
        filters.append("[vomix]apad,atrim=0:%.4f,asetpts=N/SR/TB[voa]" % total)
        vo_out = "voa"

    music_out = None  # type: Optional[str]
    if plan["musicFile"]:
        music_index = first_input_index + len(vo_labels)
        inputs += ["-stream_loop", "-1", "-i", plan["musicFile"]]
        fade_in = max(0.0, plan["musicFadeIn"])
        fade_out = max(0.0, plan["musicFadeOut"])
        fade_out_start = max(0.0, total - fade_out)
        # loudnorm carries several seconds of internal latency. The branch is
        # re-padded and re-trimmed on the far side of it so that the bed is
        # still exactly `total` long when it reaches the sidechain -- without
        # that, the compressor runs out of programme and truncates the tail.
        filters.append(
            "[%d:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            "apad,atrim=0:%.4f,asetpts=N/SR/TB,%s,"
            "apad,atrim=0:%.4f,asetpts=N/SR/TB,"
            "afade=t=in:st=0:d=%.3f,afade=t=out:st=%.4f:d=%.3f[music]"
            % (music_index, total,
               loudnorm_filter(plan["musicLufs"], LOUDNORM_TP_MUSIC,
                               plan.get("musicLoudnorm")),
               total, fade_in, fade_out_start, fade_out))
        music_out = "music"

    if vo_out and music_out:
        duck = plan["duck"]
        if duck:
            filters.append("[%s]asplit=2[vo_mix][vo_sc]" % vo_out)
            filters.append(
                "[%s][vo_sc]sidechaincompress=threshold=%0.5f:ratio=%0.2f:attack=20:"
                "release=400:makeup=1:level_sc=1[ducked0]"
                % (music_out, duck[0], duck[1]))
            # sidechaincompress ends with whichever branch runs out first, so
            # the bed is topped back up to the full timeline length.
            filters.append("[ducked0]apad,atrim=0:%.4f,asetpts=N/SR/TB[ducked]" % total)
            bed, voice = "ducked", "vo_mix"
        else:
            bed, voice = music_out, vo_out
        filters.append(
            "[%s][%s]amix=inputs=2:normalize=0:dropout_transition=0[premaster]"
            % (bed, voice))
        return inputs, filters, "premaster"
    if vo_out:
        return inputs, filters, vo_out
    if music_out:
        return inputs, filters, music_out
    return inputs, filters, None


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

def format_timeline(elements, plan, cues, warnings):
    # type: (List[Dict[str, Any]], Dict[str, Any], List[Dict[str, Any]], List[str]) -> str
    lines = []
    lines.append("")
    lines.append("  IR           %s" % plan["irPath"])
    lines.append("  brand        %s (%s)" % (plan["brandId"], plan["brandName"]))
    lines.append("  pipeline     %s" % plan["kind"])
    lines.append("  output       %s" % plan["outPath"])
    lines.append("  captions     %s" % plan["srtPath"])
    lines.append("  timeline     %s" % plan["timelinePath"])
    lines.append("  work dir     %s" % plan["workDir"])
    fmt = plan.get("format") or {}
    lines.append("  format       %s" % (format_label(fmt) if fmt else "-"))
    lines.append("  canvas       %dx%d @ %dfps, %s crf %d %s, %s"
                 % (plan["width"], plan["height"], plan["fps"], plan["vcodec"],
                    plan["crf"], plan["preset"],
                    "%s 192k" % plan["acodec"] if plan["audioEnabled"] else "no audio"))
    lines.append("  safe area    %g%% margin%s"
                 % (as_float(fmt.get("safeMarginPct"), 5.0),
                    ", chrome reserve top %g%% / bottom %g%%"
                    % (as_float(fmt.get("chromeTopPct"), 0.0),
                       as_float(fmt.get("chromeBottomPct"), 0.0))
                    if (fmt.get("chromeTopPct") or fmt.get("chromeBottomPct")) else ""))
    lines.append("  transition   %s (xfade=%s) %0.2fs"
                 % (plan["transitionName"], plan["xfade"] or "none", plan["transitionSec"]))
    lines.append("  voiceover    %s" % plan["voSummary"])
    lines.append("  music        %s" % plan["musicSummary"])
    lines.append("  caption rule %d chars x %d lines, min %0.2fs, %gpt, %g%% up from "
                 "the bottom, burn-in %s"
                 % (plan["capMaxChars"], plan["capMaxLines"], plan["capMinDur"],
                    as_float(fmt.get("captionSizePt"), 28.0),
                    as_float(fmt.get("captionBottomMarginPct"), 8.0),
                    ("on (%s, pillow + overlay, %s)"
                     % (plan.get("captionMode") or "off",
                        os.path.basename(str(plan.get("captionFont") or "no font"))))
                    if plan["burnIn"] else "off"))
    if plan.get("brandAssets"):
        lines.append("")
        lines.append("BRAND ASSETS (rendered once per brand, then reused)")
        lines.extend(format_brand_assets(plan["brandAssets"]))
    if plan.get("musicBed"):
        lines.append("")
        lines.append("MUSIC BED (synthesised once per brand, mood and film length)")
        lines.extend(format_music_bed(plan["musicBed"]))
    if plan.get("dryRun"):
        lines.append("  note         a real build measures every clip with loudnorm first "
                     "and substitutes the measured_* values below")
    lines.append("")
    lines.append("TIMELINE")
    header = ("  %-3s %-6s %-10s %-14s %8s %8s %7s %-28s %7s %5s  %s"
              % ("#", "kind", "id", "role", "start", "end", "dur",
                 "hold source", "vo", "cues", "visual"))
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for index, element in enumerate(elements):
        vo = element.get("voDurationSec")
        visual = element.get("visualKind", "")
        if visual == "slide":
            visual = "slide %s" % element.get("slide")
        elif visual == "motion":
            visual = "motion %s" % element.get("template")
        elif visual == "image":
            visual = "image %s" % os.path.basename(str(element.get("src") or ""))
        lines.append(
            "  %-3d %-6s %-10s %-14s %8.2f %8.2f %7.2f %-28s %7s %5d  %s"
            % (index, element["kind"], element["id"][:10], (element.get("role") or "")[:14],
               element["start"], element["end"], element["duration"],
               element.get("holdSource", "")[:28],
               ("%0.2f" % vo) if vo else "-",
               len(element.get("captionCues") or []), visual))
    target = plan.get("durationTargetSec")
    delta = ""
    if target:
        drift = (plan["totalDurationSec"] - float(target)) / float(target) * 100.0
        delta = " (target %ss, %+0.1f%%)" % (target, drift)
    lines.append("  TOTAL %0.2fs%s" % (plan["totalDurationSec"], delta))
    lines.append("")
    lines.append("CAPTIONS (%d cue(s), snapped to the %dfps frame grid)"
                 % (len(cues), plan["fps"]))
    for cue in cues:
        lines.append("  %-4d %s --> %s   frames %d-%d (%d)   [%s]"
                     % (cue["index"], brandlib.srt_timestamp(cue["start"]),
                        brandlib.srt_timestamp(cue["end"]),
                        cue.get("startFrame", -1), cue.get("endFrame", -1),
                        cue.get("frameCount", 0), cue["scene"]))
        for line in cue["lines"]:
            lines.append("       %s  (%d)" % (line, len(line)))
    if warnings:
        lines.append("")
        lines.append("WARNINGS")
        for warning in warnings:
            lines.append("  - %s" % warning)
    return "\n".join(lines)


def format_command(cmd):
    # type: (Sequence[str]) -> str
    """Shell-quoted, one flag (with its value) per line so a human can read it."""
    flag = re.compile(r"^-[A-Za-z]")
    lines = []  # type: List[str]
    index = 0
    tokens = [str(t) for t in cmd]
    while index < len(tokens):
        token = tokens[index]
        if flag.match(token) and index + 1 < len(tokens) and not flag.match(tokens[index + 1]):
            lines.append("%s %s" % (shlex.quote(token), shlex.quote(tokens[index + 1])))
            index += 2
        else:
            lines.append(shlex.quote(token))
            index += 1
    return "  " + " \\\n    ".join(lines)


# ---------------------------------------------------------------------------
# main build
# ---------------------------------------------------------------------------

def preflight(need_deck, need_vo, burn_in, allow_no_burn_in, warnings,
              caption_font=None):
    # type: (bool, bool, bool, bool, List[str], Optional[str]) -> Tuple[Dict[str, Optional[str]], bool]
    tools = {
        "ffmpeg": os.environ.get("BRAND_STUDIO_FFMPEG") or which("ffmpeg"),
        "ffprobe": os.environ.get("BRAND_STUDIO_FFPROBE") or which("ffprobe"),
        "soffice": os.environ.get("BRAND_STUDIO_SOFFICE") or which("soffice"),
        "pdftoppm": which("pdftoppm"),
        "chrome": find_chrome(),
        "say": which("say"),
    }  # type: Dict[str, Optional[str]]

    missing = []
    if not tools["ffmpeg"]:
        missing.append("ffmpeg (brew install ffmpeg)")
    if not tools["ffprobe"]:
        missing.append("ffprobe (ships with ffmpeg)")
    if need_deck and not tools["soffice"]:
        missing.append("soffice / LibreOffice (brew install --cask libreoffice)")
    if need_vo and not tools["say"]:
        missing.append("say (macOS speech synthesis; use --no-audio elsewhere)")
    if missing:
        raise BuildError(
            "required tool(s) not found:\n  - %s" % "\n  - ".join(missing))

    if need_deck and not tools["pdftoppm"]:
        warnings.append(
            "pdftoppm not found; falling back to LibreOffice PNG export, which only "
            "exports the first slide on most builds. Install poppler for reliable "
            "multi-slide rasterisation: brew install poppler")

    # Burn-in does not go through libass. Captions are drawn with pillow from
    # the brand's own font file and composited with `overlay`, so what has to be
    # present is pillow and a font -- not an ffmpeg built with libass, freetype
    # and fontconfig, which is a far rarer thing to have.
    burn_in_ok = burn_in
    if burn_in:
        problems = []
        try:
            from PIL import Image, ImageDraw, ImageFont  # noqa: F401
        except ImportError as exc:
            problems.append("pillow is not importable (%s); pip install pillow" % exc)
        if not caption_font:
            problems.append(
                "no .ttf or .otf caption face was found in the brand's assets/fonts")
        elif not os.path.isfile(caption_font):
            problems.append("the caption face %s is missing" % caption_font)
        if problems:
            message = (
                "captions.burnIn is true but the caption renderer cannot run:\n"
                "  - %s\n"
                "Captions are drawn with pillow from the brand's own font file, "
                "because this ffmpeg has no subtitles or drawtext filter. Fix the "
                "above, set captions.burnIn to false, or pass --allow-no-burn-in to "
                "ship the SRT and VTT sidecars only." % "\n  - ".join(problems))
            if not allow_no_burn_in:
                raise BuildError(message)
            warnings.append(message + " Burn-in was skipped.")
            burn_in_ok = False
    return tools, burn_in_ok


_FILTER_CACHE = {}  # type: Dict[str, bool]


def ffmpeg_has_filter(ffmpeg, name):
    # type: (str, str) -> bool
    key = ffmpeg + "|" + name
    if key in _FILTER_CACHE:
        return _FILTER_CACHE[key]
    try:
        proc = subprocess.run([ffmpeg, "-hide_banner", "-filters"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        text = (proc.stdout or b"").decode("utf-8", "replace")
        found = re.search(r"^\s*\S+\s+%s\s" % re.escape(name), text, re.M) is not None
    except (OSError, subprocess.TimeoutExpired):
        found = False
    _FILTER_CACHE[key] = found
    return found


def needs_chrome(elements):
    # type: (List[Dict[str, Any]]) -> bool
    """True when something still has to be drawn by headless Chrome.

    An element that already resolved to a source -- a plate the brand supplied,
    or a cached brand bookend -- never reaches Chrome, so it does not make
    Chrome a requirement for this build.
    """
    for element in elements:
        if element.get("source"):
            continue
        if element.get("visualKind") == "motion" and not element.get("mediaFile"):
            return True
    return False


def require_chrome(tools, what):
    # type: (Dict[str, Optional[str]], str) -> str
    chrome = tools.get("chrome")
    if not chrome:
        raise BuildError(
            "Google Chrome (or Chromium) is required to render %s but was not "
            "found.\nInstall Chrome, or set BRAND_STUDIO_CHROME to a Chromium "
            "binary." % what)
    return chrome


# ---------------------------------------------------------------------------
# brand bookends: rendered once per brand, reused by every video
# ---------------------------------------------------------------------------

BRAND_ASSET_STATE_TEXT = {
    "REUSED": "REUSED from cache",
    "RENDERED": "RENDERED now",
    "REUSE": "will REUSE from cache",
    "RENDER": "will RENDER",
    "PERSONALISED": "RENDERED for this video",
    "SUPPLIED": "SUPPLIED by the brand",
    "NONE": "not in this film",
}


def brand_generated_dir(brand):
    # type: (Dict[str, Any]) -> str
    """Where AssetCache keeps a brand's generated assets.

    Mirrored here only so a --dry-run can probe for the directory without
    creating it; every real interaction goes through AssetCache itself.
    """
    return os.path.join(brandlib.plugin_root(), "brands",
                        str(brand.get("id") or ""), "video", "generated")


def brand_asset_cache_kind(kind, fmt):
    # type: (str, Dict[str, Any]) -> str
    """The cache slot a bookend is stored in: one per delivery format.

    The brand's default format keeps the bare name, so an existing intro.mp4
    stays addressable; every other format gets its own file (intro-vertical.mp4).
    Sharing one slot would be actively dangerous here -- the owner builds a
    vertical reel and a landscape film on the same day, and whichever ran second
    would overwrite the first, leaving the other film opening on a bookend
    rendered for the wrong canvas.
    """
    name = fmt.get("name")
    if not name or fmt.get("isDefault"):
        return kind
    return "%s-%s" % (kind, name)


def brand_asset_inputs(brand, kind, element, fmt):
    # type: (Dict[str, Any], str, Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
    """What genuinely defines a brand's cached bookend.

    ``asset_cache.intro_inputs`` supplies the drawing inputs: the logo file's
    CONTENT hash, the gradient stops and angle, the declared duration, the
    style, the canvas and the fps. Change one of those and the clip rebuilds
    itself on the next build; change anything else in brand.json and the cached
    clip stands.

    The canvas it reports is the brand's video.resolution, which is only ever
    the DEFAULT format -- so the resolved format's own dimensions and safe
    margin are substituted in here. Both are drawn (the templates lay out
    against --canvas-* and --safe-*), so both belong in the fingerprint. Between
    that and the per-format cache slot, a vertical intro can neither be
    mistaken for nor overwrite the landscape one.

    The resolved on-screen copy is folded in alongside, because it is drawn into
    the asset and -- since a personalised bookend never reaches the cache at all
    -- it is brand-constant by construction. Without it, editing the brand's
    standard contact would leave every video still closing on the old one.
    """
    inputs = dict(asset_cache.intro_inputs(brand, kind))
    inputs["resolution"] = {"w": int(fmt["width"]), "h": int(fmt["height"])}
    inputs["format"] = fmt.get("name") or ""
    inputs["safeMarginPct"] = as_float(fmt.get("safeMarginPct"), 5.0)
    data = element.get("data") or {}
    inputs["text"] = json.dumps(
        dict((key, data.get(key) or "")
             for key in ("title", "eyebrow", "contact", "brandName")),
        sort_keys=True)
    return inputs


def brand_clip_is_usable(tools, path, duration):
    # type: (Dict[str, Optional[str]], str, float) -> bool
    """Cheap sanity check before a build leans on a cached clip.

    A cache that has gone missing, been truncated or been replaced by something
    unreadable must cost a re-render, never the build.
    """
    try:
        if not os.path.isfile(path) or os.path.getsize(path) < 1024:
            return False
        probe = tools.get("ffprobe")
        if not probe:
            return True
        return ffprobe_duration(probe, path) >= float(duration) - 0.05
    except Exception:  # noqa: BLE001 - any doubt at all means re-render
        return False


def render_brand_clip(tools, element, brand_vars, work_dir, width, height, fps,
                      jobs, log, dest):
    # type: (Dict[str, Optional[str]], Dict[str, Any], str, str, int, int, int, int, Any, str) -> None
    """Render one bookend to a standalone mp4 at ``dest``.

    A clip is what gets cached, rather than a directory of frames: it is a
    single file to store, validate and hand to ffmpeg, it drops into the graph
    as an ordinary video input (the same path a brand-supplied plate takes), and
    it cannot go half-missing the way a frame sequence can.

    Always rendered at the brand's own canvas and fps, never at --anim-fps: the
    cached artifact is a property of the brand, not of the run that happened to
    build it first.
    """
    kind = element["kind"]
    chrome = require_chrome(tools, "the brand %s" % kind)
    stage = os.path.join(work_dir, "brand-assets", kind)
    os.makedirs(stage, exist_ok=True)
    template = template_path(element.get("template") or kind)
    html_path = write_scene_html(template, brand_vars, dict(element.get("data") or {}),
                                 os.path.join(stage, "%s.html" % kind))
    duration = float(element["duration"])
    # Round UP: a clip a frame short of its slot leaves the xfade nothing to
    # chew on at the join.
    frames = max(1, int(duration * fps + 0.999999))
    log("  rendering the brand %s once (%s, %d frame(s)); every later video for "
        "this brand reuses it" % (kind, os.path.basename(template), frames))
    pattern = render_motion_sequence(
        chrome, html_path, os.path.join(stage, "frames"), frames, frames,
        width, height, jobs, log, os.path.join(stage, "chrome-profiles"))
    run([tools["ffmpeg"], "-hide_banner", "-y",
         "-framerate", str(fps), "-start_number", "0", "-i", pattern,
         "-frames:v", str(frames),
         "-c:v", "libx264", "-crf", str(BRAND_ASSET_CRF),
         "-preset", BRAND_ASSET_PRESET, "-pix_fmt", "yuv420p",
         "-r", str(fps), "-an", dest],
        "encoding the cached brand %s" % kind)


def resolve_brand_assets(elements, brand, brand_vars, tools, args, work_dir,
                         width, height, fps, log, warnings, fmt):
    # type: (List[Dict[str, Any]], Dict[str, Any], str, Dict[str, Optional[str]], Any, str, int, int, int, Any, List[str], Dict[str, Any]) -> List[Dict[str, Any]]
    """Point the intro and outro at the brand's cached clips, rendering once.

    Video renders are the most expensive thing this plugin does, and a brand's
    bookends do not vary between films, so they are produced once and then
    reused verbatim. Reuse is the default; a re-render happens only when the
    caller asks for one (--regenerate-intro / --regenerate-outro /
    --regenerate-brand-assets) or when the fingerprint of what is drawn changed.

    Bookends are cached PER DELIVERY FORMAT: a vertical reel's intro lives in
    its own slot and its own file, so building a reel and a landscape film on
    the same day cannot leave either one wearing the other's bookend.

    Anything that goes wrong with the cache is downgraded to a warning and the
    bookend falls through to the ordinary per-video motion path. A cache problem
    must never break a build.

    Returns one report row per bookend, for the run summary and the dry run.
    """
    rows = []  # type: List[Dict[str, Any]]
    by_kind = {}  # type: Dict[str, Dict[str, Any]]
    for element in elements:
        if element["kind"] in BRAND_ASSET_KINDS:
            by_kind[element["kind"]] = element

    for kind in BRAND_ASSET_KINDS:
        element = by_kind.get(kind)
        if element is None:
            rows.append({"kind": kind, "state": "NONE", "path": None,
                         "detail": "the IR sets '%s' to false" % kind})
            continue

        if element.get("source"):
            rows.append({"kind": kind, "state": "SUPPLIED", "path": element["source"],
                         "detail": "brand.video.%s.file; nothing to render" % kind})
            continue

        personal = list(element.get("personalisedBy") or [])
        if personal:
            note = ("the %s is personalised by %s, so it is rendered for this video "
                    "only and bypasses the brand cache"
                    % (kind, ", ".join("meta." + key for key in personal)))
            log("note: " + note)
            rows.append({"kind": kind, "state": "PERSONALISED", "path": None,
                         "detail": note})
            continue

        force = bool(getattr(args, "regenerate_" + kind, False))
        slot = brand_asset_cache_kind(kind, fmt)
        expected = os.path.join(brand_generated_dir(brand), "%s.mp4" % slot)

        cache = None
        inputs = None
        if asset_cache is None:
            warnings.append(
                "asset_cache.py could not be imported, so the %s cannot be cached; "
                "it is rendered for this video only" % kind)
        else:
            try:
                # A dry run must not write anything, not even an empty directory.
                if not (args.dry_run and not os.path.isdir(brand_generated_dir(brand))):
                    cache = asset_cache.AssetCache(str(brand.get("id") or ""))
                    inputs = brand_asset_inputs(brand, kind, element, fmt)
            except Exception as exc:  # noqa: BLE001 - a cache fault never fails a build
                cache = None
                warnings.append(
                    "the brand asset cache is unreadable (%s); the %s is rendered "
                    "for this video instead" % (exc, kind))

        if args.dry_run:
            hit = None
            if cache is not None and not force:
                try:
                    hit = cache.lookup(slot, inputs)
                except Exception as exc:  # noqa: BLE001
                    warnings.append("the cached %s could not be read (%s)" % (slot, exc))
            if hit:
                # Report -- and encode -- exactly what a real build would use.
                element["source"] = hit
                element["sourceKind"] = "video"
                element["cacheState"] = "reused"
                rows.append({"kind": kind, "slot": slot, "format": fmt.get("name"),
                             "state": "REUSE", "path": hit,
                             "detail": "the cached clip is current; a real build "
                                       "re-renders nothing"})
            else:
                rows.append({
                    "kind": kind, "slot": slot, "format": fmt.get("name"),
                    "state": "RENDER", "path": expected,
                    "detail": ("a re-render was requested on the command line"
                               if force else
                               "nothing valid is cached yet for this brand at the %s "
                               "canvas, or the logo, colours, duration, style or "
                               "canvas changed" % (fmt.get("name") or "delivery"))})
            continue

        def produce(dest, _element=element):
            render_brand_clip(tools, _element, brand_vars, work_dir, width, height,
                              fps, max(1, as_int(args.jobs, 4)), log, dest)

        path = None  # type: Optional[str]
        reused = False
        if cache is not None:
            stamp = datetime.datetime.now().replace(microsecond=0).isoformat()
            try:
                path, reused = cache.get_or_create(slot, inputs, produce, ext="mp4",
                                                   force=force, stamp=stamp)
                if reused and not brand_clip_is_usable(tools, path, element["duration"]):
                    warnings.append(
                        "the cached %s at %s is missing or unusable; it was rendered "
                        "again" % (slot, path))
                    path, reused = cache.get_or_create(slot, inputs, produce, ext="mp4",
                                                       force=True, stamp=stamp)
            except BuildError:
                raise  # a genuine render failure, not a cache fault
            except Exception as exc:  # noqa: BLE001 - the cache must not break a build
                # The clip itself may well have landed even though the manifest
                # could not be written. Use it when it is there and sound.
                if brand_clip_is_usable(tools, expected, element["duration"]):
                    path, reused = expected, True
                    warnings.append(
                        "the %s cache index could not be updated (%s); the clip "
                        "already on disk was used" % (kind, exc))
                else:
                    path = None
                    warnings.append(
                        "the %s could not be cached (%s); it is rendered for this "
                        "video only" % (kind, exc))

        if not path:
            # Fall through to the ordinary motion path, which renders frames for
            # this video only. Slower, but the film still ships.
            rows.append({"kind": kind, "slot": slot, "format": fmt.get("name"),
                         "state": "RENDERED", "path": None,
                         "detail": "rendered for this video only; no usable cache"})
            continue

        element["source"] = path
        element["sourceKind"] = "video"
        element["cacheState"] = "reused" if reused else "rendered"
        rows.append({
            "kind": kind, "slot": slot, "format": fmt.get("name"),
            "state": "REUSED" if reused else "RENDERED", "path": path,
            "detail": ("no render needed" if reused else
                       ("a re-render was requested on the command line" if force else
                        "first build for this brand at the %s canvas, or its logo, "
                        "colours, duration, style or canvas changed"
                        % (fmt.get("name") or "delivery")))})
    return rows


def format_brand_assets(rows):
    # type: (List[Dict[str, Any]]) -> List[str]
    """One state line per bookend, plus why, so reuse is visible at a glance."""
    lines = []  # type: List[str]
    for row in rows or []:
        lines.append("  %-17s%-24s%s"
                     % (row.get("slot") or row["kind"],
                        BRAND_ASSET_STATE_TEXT.get(row["state"], row["state"]),
                        row.get("path") or "-"))
        if row.get("detail"):
            lines.append("  %-17s%s" % ("", row["detail"]))
    return lines


# ---------------------------------------------------------------------------
# the music bed: synthesised by make_music.py, cached per brand
# ---------------------------------------------------------------------------

MUSIC_STATE_TEXT = {
    "SUPPLIED": "SUPPLIED track",
    "REUSED": "REUSED from cache",
    "GENERATED": "GENERATED now",
    "REUSE": "will REUSE from cache",
    "GENERATE": "will GENERATE",
    "FAILED": "FAILED; narration only",
    "OFF": "off",
}


def music_generated_dir(brand_id):
    # type: (str) -> str
    """Where generated music beds live: brands/<id>/audio/generated/.

    Their own directory, deliberately. A bed is a brand asset in the same sense
    the bookends are, but it is audio keyed on a film's LENGTH rather than a
    clip keyed on a canvas, and giving it its own manifest and its own filename
    space means a bed and a bookend can never collide or invalidate each other.
    """
    return os.path.join(brandlib.plugin_root(), "brands", brand_id, "audio", "generated")


if asset_cache is not None:
    class MusicBedCache(asset_cache.AssetCache):  # type: ignore[misc,name-defined]
        """AssetCache, pointed at brands/<id>/audio/generated.

        AssetCache hard-codes video/generated, so the directory and the manifest
        path are set here rather than by ``super().__init__`` -- which is also
        why that constructor is not called: it would create an unrelated empty
        video/generated on the way past. Everything that matters is inherited
        untouched: fingerprint(), lookup(), get_or_create() and the atomic,
        merging _save() that makes concurrent nightly builds safe.
        """

        def __init__(self, brand_id, root=None):
            # type: (str, Optional[str]) -> None
            self.brand_id = brand_id
            self.root = root or brandlib.plugin_root()
            self.dir = music_generated_dir(brand_id)
            if not os.path.isdir(self.dir):
                os.makedirs(self.dir)
            self.manifest_path = os.path.join(self.dir, asset_cache.MANIFEST)
            self.manifest = self._load()
else:  # pragma: no cover - only when asset_cache.py is missing entirely
    MusicBedCache = None  # type: ignore


def music_bed_slots():
    # type: () -> List[str]
    """The cache slots beds may occupy, best first.

    The first is the bare ``music-bed`` kind AssetCache already reserves, so a
    brand with one film length keeps one obvious file.
    """
    return [MUSIC_BED_KIND] + ["%s-%d" % (MUSIC_BED_KIND, n)
                               for n in range(2, MUSIC_BED_SLOTS + 1)]


def music_bed_inputs(brand_id, music_cfg, spec, duration, lufs):
    # type: (str, Dict[str, Any], Dict[str, Any], float, float) -> Dict[str, Any]
    """Everything that genuinely defines a generated bed.

    A bed is a pure function of these, so two films that agree on all of them
    can share one file and a film that changes any of them gets a new one. The
    brand's mood and avoid arrays are in here rather than the mood they resolve
    to, because they are what is actually fed to make_music -- rewrite them and
    the bed must be rebuilt even if the resolved mood happens to land the same.

    ``generator_file`` is hashed by CONTENT (AssetCache hashes any key ending in
    _file that way), so editing the synthesiser invalidates every bed it wrote
    instead of leaving films wearing music the current code would not produce.
    """
    return {
        "kind": MUSIC_BED_KIND,
        "brand": brand_id,
        "generator_file": MAKE_MUSIC,
        "mood": spec.get("mood") or "",
        "key": spec.get("key") or "",
        "mode": spec.get("mode") or "",
        "bpm": spec.get("bpm") or None,
        "seed": int(spec.get("seed") or MUSIC_BED_SEED),
        "durationSec": round(float(duration), 3),
        "targetLufs": round(float(lufs), 2),
        "brandMood": [str(m) for m in (music_cfg.get("mood") or [])],
        "brandAvoid": [str(a) for a in (music_cfg.get("avoid") or [])],
        # make_music bakes the brand's fades into the bed itself.
        "fadeInSec": as_float(music_cfg.get("fadeInSec"), 1.0),
        "fadeOutSec": as_float(music_cfg.get("fadeOutSec"), 2.0),
        "format": MUSIC_BED_EXT,
    }


def pick_music_slot(cache, inputs):
    # type: (Any, Dict[str, Any]) -> Tuple[str, bool]
    """(slot, hit): the slot already holding this exact bed, else the one to write.

    A free slot is taken before an occupied one; when every slot is taken the
    oldest is evicted. Eviction is an overwrite rather than a delete, so the
    directory and the manifest both stay bounded however many nights run.
    """
    assets = cache.manifest.get("assets") or {}
    for slot in music_bed_slots():
        if cache.lookup(slot, inputs):
            return slot, True
    for slot in music_bed_slots():
        if slot not in assets:
            return slot, False
    return min(music_bed_slots(),
               key=lambda s: (str(assets[s].get("created") or ""), s)), False


def resolve_music_mood(music_cfg, requested):
    # type: (Dict[str, Any], Optional[str]) -> Tuple[Optional[str], str]
    """The mood a generated bed will carry, and why.

    make_music owns this decision and records it in the sidecar it writes beside
    every bed -- but a --dry-run has to name the mood before anything has been
    generated, so its scoring function is asked directly. The import is guarded
    down to SystemExit because make_music EXITS rather than raises when numpy is
    missing; a missing numpy must degrade this report, not the build.
    """
    if requested:
        return str(requested), "requested for this film"
    try:
        import make_music  # type: ignore
        mood, why = make_music.mood_from_brand(music_cfg)
        return str(mood), str(why)
    except (Exception, SystemExit):  # noqa: BLE001 - a report must never fail a build
        return None, ("chosen by make_music.py from the brand's video.music.mood %s"
                      % json.dumps([str(m) for m in (music_cfg.get("mood") or [])]))


def run_make_music(brand_id, dest, duration, spec, lufs, log):
    # type: (str, str, float, Dict[str, Any], float, Any) -> Dict[str, Any]
    """Synthesise one bed at ``dest`` and return make_music's sidecar.

    Driven through the CLI on purpose: that is make_music's stable interface,
    and running it out of process means a numpy blow-up, a wedge or a hard exit
    is a subprocess failure rather than a dead build.

    The duration passed is the film's EXACT total. make_music guarantees it to
    the millisecond, so nothing here pads, trims or loops the result.

    The bed is written to a temporary name in the destination directory and
    renamed into place, which is atomic on POSIX: two nightly builds racing for
    the same cache slot can then only produce a whole file, never half of one.
    """
    tmp = "%s.%d.tmp.%s" % (os.path.splitext(dest)[0], os.getpid(), MUSIC_BED_EXT)
    cmd = [sys.executable, MAKE_MUSIC,
           "--duration", "%.3f" % float(duration),
           "--brand", brand_id,
           "--lufs", "%.2f" % float(lufs),
           "--seed", str(int(spec.get("seed") or MUSIC_BED_SEED)),
           "--out", tmp, "--json"]
    if spec.get("mood"):
        cmd += ["--mood", str(spec["mood"])]
    if spec.get("key"):
        cmd += ["--key", str(spec["key"])]
    if spec.get("mode"):
        cmd += ["--mode", str(spec["mode"])]
    if spec.get("bpm"):
        cmd += ["--bpm", "%g" % float(spec["bpm"])]

    log("  synthesising a %0.2fs music bed with make_music.py (copyright-free, "
        "no samples and no model)" % float(duration))
    try:
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=MUSIC_TIMEOUT_SEC)
        except OSError as exc:
            raise RuntimeError("make_music.py failed to start: %s" % exc)
        except subprocess.TimeoutExpired:
            raise RuntimeError("make_music.py timed out after %gs" % MUSIC_TIMEOUT_SEC)
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", "replace").strip()
            tail = "; ".join(err.splitlines()[-3:]) or "no output"
            raise RuntimeError("make_music.py exited %d: %s" % (proc.returncode, tail))
        if not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
            raise RuntimeError("make_music.py wrote nothing to %s" % tmp)

        # The sidecar is the record of what was played: mood, key, bpm, seed, the
        # measured loudness. It travels with the bed so a cache hit can still say
        # exactly what is in the film.
        for suffix in ("", ".music.json"):
            source = tmp + suffix
            if os.path.exists(source):
                os.replace(source, dest + suffix)
    finally:
        # A half-written bed must not be left behind in a directory that is
        # shared with the team through the repo.
        for suffix in ("", ".music.json"):
            if os.path.exists(tmp + suffix):
                try:
                    os.remove(tmp + suffix)
                except OSError:
                    pass
    try:
        with open(dest + ".music.json", encoding="utf-8") as handle:
            return json.load(handle)
    except (IOError, ValueError):
        try:
            return json.loads((proc.stdout or b"").decode("utf-8", "replace"))
        except ValueError:
            return {}


def read_music_sidecar(path):
    # type: (Optional[str]) -> Dict[str, Any]
    """make_music's sidecar for a bed already on disk, or {}."""
    if not path:
        return {}
    try:
        with open(path + ".music.json", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (IOError, ValueError):
        return {}


def music_bed_is_usable(tools, path, duration):
    # type: (Dict[str, Optional[str]], Optional[str], float) -> bool
    """Cheap sanity check before a build leans on a cached bed."""
    try:
        if not path or not os.path.isfile(path) or os.path.getsize(path) < 1024:
            return False
        probe = tools.get("ffprobe")
        if not probe:
            return True
        measured = ffprobe_duration(probe, path)
        return abs(measured - float(duration)) <= MUSIC_DURATION_TOLERANCE_SEC
    except Exception:  # noqa: BLE001 - any doubt at all means generate it again
        return False


def music_bed_label(row, sidecar):
    # type: (Dict[str, Any], Dict[str, Any]) -> str
    """One line describing what the bed actually is, for the summary."""
    mood = sidecar.get("mood") or row.get("mood") or "the brand's mood"
    parts = ["%s mood" % mood]
    if sidecar.get("key") and sidecar.get("mode"):
        parts.append("%s %s" % (sidecar["key"], sidecar["mode"]))
    if sidecar.get("bpm"):
        parts.append("%.1f bpm" % float(sidecar["bpm"]))
    if sidecar.get("seed") is not None:
        parts.append("seed %s" % sidecar["seed"])
    parts.append("%0.3fs" % as_float(row.get("durationSec"), 0.0))
    parts.append("%0.1f LUFS" % as_float(row.get("lufs"), -23.0))
    return ", ".join(parts)


def resolve_music_bed(brand, music_cfg, ir_music, args, duration, work_dir, tools,
                      log, warnings):
    # type: (Dict[str, Any], Dict[str, Any], Dict[str, Any], Any, float, str, Dict[str, Optional[str]], Any, List[str]) -> Tuple[Optional[str], Dict[str, Any]]
    """Generate (or reuse) the film's music bed. Returns ``(path, report row)``.

    Called only when the brand wants music and nobody supplied a track. Every
    failure here is downgraded to a warning: a nightly build that produced a
    good film must not be failed by a missing bed, so the film ships with
    narration only and the summary says so in as many words.
    """
    brand_id = str(brand.get("id") or "")
    duration = round(float(duration), 3)
    lufs = as_float(music_cfg.get("targetLufs"), -23.0)
    requested = args.music_mood or ir_music.get("mood")
    mood, mood_why = resolve_music_mood(music_cfg, requested)
    spec = {
        "mood": str(requested) if requested else "",
        "key": ir_music.get("key") or "",
        "mode": ir_music.get("mode") or "",
        "bpm": as_float(ir_music.get("bpm"), 0.0) or None,
        "seed": as_int(ir_music.get("seed"), MUSIC_BED_SEED),
    }
    inputs = music_bed_inputs(brand_id, music_cfg, spec, duration, lufs)
    force = bool(getattr(args, "regenerate_music", False))
    row = {
        "kind": MUSIC_BED_KIND, "slot": MUSIC_BED_KIND, "state": "GENERATE",
        "path": None, "mood": mood, "moodWhy": mood_why,
        "durationSec": duration, "lufs": lufs, "detail": "", "label": "",
        "generator": os.path.basename(MAKE_MUSIC),
    }  # type: Dict[str, Any]

    # ---- the cache ---------------------------------------------------------
    cache = None
    if asset_cache is None or MusicBedCache is None:
        warnings.append(
            "asset_cache.py could not be imported, so the music bed cannot be "
            "cached; it is generated for this video only")
    elif args.dry_run and not os.path.isdir(music_generated_dir(brand_id)):
        pass  # a dry run must not write anything, not even an empty directory
    else:
        try:
            cache = MusicBedCache(brand_id)
        except Exception as exc:  # noqa: BLE001 - a cache fault never fails a build
            warnings.append("the music bed cache is unreadable (%s); the bed is "
                            "generated for this video instead" % exc)

    slot, hit = MUSIC_BED_KIND, False
    if cache is not None:
        try:
            slot, hit = pick_music_slot(cache, inputs)
        except Exception as exc:  # noqa: BLE001
            cache, slot, hit = None, MUSIC_BED_KIND, False
            warnings.append("the music bed cache could not be read (%s); the bed is "
                            "generated for this video instead" % exc)
    expected = os.path.join(music_generated_dir(brand_id),
                            "%s.%s" % (slot, MUSIC_BED_EXT))
    row["slot"] = slot

    # ---- a dry run reports, it does not synthesise -------------------------
    if args.dry_run:
        if hit and not force:
            path = cache.lookup(slot, inputs)
            sidecar = read_music_sidecar(path)
            row.update({"state": "REUSE", "path": path,
                        "mood": sidecar.get("mood") or mood,
                        "label": music_bed_label(row, sidecar),
                        "detail": "the cached bed matches this film exactly; a real "
                                  "build synthesises nothing"})
            return path, row
        row.update({"state": "GENERATE", "path": expected,
                    "label": music_bed_label(row, {}),
                    "detail": ("a fresh bed was requested on the command line"
                               if force else
                               "nothing cached matches this film's length, mood and "
                               "loudness yet (%s)" % mood_why)})
        return expected, row

    # ---- synthesise --------------------------------------------------------
    produced = []  # type: List[Dict[str, Any]]

    def produce(dest):
        # type: (str) -> None
        produced.append(run_make_music(brand_id, dest, duration, spec, lufs, log))

    stamp = datetime.datetime.now().replace(microsecond=0).isoformat()
    path = None  # type: Optional[str]
    reused = False
    try:
        if cache is not None:
            path, reused = cache.get_or_create(slot, inputs, produce,
                                               ext=MUSIC_BED_EXT, force=force,
                                               stamp=stamp)
            if reused and not music_bed_is_usable(tools, path, duration):
                warnings.append(
                    "the cached music bed at %s is missing or the wrong length; it "
                    "was synthesised again" % path)
                path, reused = cache.get_or_create(slot, inputs, produce,
                                                   ext=MUSIC_BED_EXT, force=True,
                                                   stamp=stamp)
        else:
            path = os.path.join(work_dir, "music", "bed.%s" % MUSIC_BED_EXT)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            produce(path)
    except Exception as exc:  # noqa: BLE001 - music must never fail a build
        warnings.append(
            "the music bed could not be generated (%s); the film ships with "
            "narration only" % exc)
        row.update({"state": "FAILED", "path": None, "detail": str(exc)})
        return None, row

    sidecar = produced[0] if produced else read_music_sidecar(path)
    row.update({
        "state": "REUSED" if reused else "GENERATED",
        "path": path,
        "mood": sidecar.get("mood") or mood,
        "sidecar": (path + ".music.json") if path and os.path.exists(
            str(path) + ".music.json") else None,
    })
    row["label"] = music_bed_label(row, sidecar)
    row["detail"] = (
        "nothing was synthesised; the cached bed matches this film exactly"
        if reused else
        ("a fresh bed was requested on the command line" if force else
         "no cached bed matched this film's length, mood and loudness (%s)" % mood_why))
    if not reused:
        error_ms = ((sidecar.get("duration") or {}) if isinstance(
            sidecar.get("duration"), dict) else {}).get("errorMs")
        if error_ms is not None and float(error_ms) > 50.0:
            warnings.append("the generated music bed is %.0f ms off the film's length"
                            % float(error_ms))
    return path, row


def format_music_bed(row):
    # type: (Optional[Dict[str, Any]]) -> List[str]
    """The bed's state, what it is and where it lives -- reuse at a glance."""
    if not row:
        return []
    lines = ["  %-17s%-24s%s" % (row.get("slot") or MUSIC_BED_KIND,
                                 MUSIC_STATE_TEXT.get(row["state"], row["state"]),
                                 row.get("path") or "-")]
    if row.get("label"):
        lines.append("  %-17s%s" % ("", row["label"]))
    if row.get("detail"):
        lines.append("  %-17s%s" % ("", row["detail"]))
    return lines


def build(args):
    # type: (Any) -> int
    warnings = []  # type: List[str]
    log = (lambda msg: None) if args.quiet else (lambda msg: print(msg, file=sys.stderr))

    ir_path = os.path.abspath(args.ir)
    ir = load_ir(ir_path)
    validate_ir(ir, ir_path)

    brand_id = args.brand or ir.get("brand")
    if not brand_id:
        raise BuildError("no brand: neither --brand nor the IR's 'brand' key is set")
    try:
        brand = brandlib.load_brand(str(brand_id))
    except brandlib.BrandNotFound as exc:
        raise BuildError(str(exc))

    video = brandlib.deep_merge(DEFAULT_VIDEO, brand.get("video") or {})
    kind = str(ir.get("kind"))

    # ---- delivery format ---------------------------------------------------
    # --format wins, then the IR's meta.format, then the brand's default. The
    # result drives the canvas, the safe margin, the caption size and position,
    # and the cache slot the bookends land in.
    ir_format = cfg(ir, "meta.format")
    if args.format:
        requested, format_source = str(args.format), "--format"
    elif ir_format:
        requested, format_source = str(ir_format), "the IR's meta.format"
    else:
        requested, format_source = None, "the brand default"
    fmt = resolve_delivery_format(video, requested, format_source)

    width = int(fmt["width"])
    height = int(fmt["height"])
    fps = max(1, as_int(video.get("fps"), 30))
    anim_fps = fps if not args.anim_fps else max(1, min(fps, as_int(args.anim_fps, fps)))
    log("format       %s" % format_label(fmt))

    captions_cfg = video.get("captions") or {}
    captions_on = bool(captions_cfg.get("enabled", True))
    burn_in = bool(captions_cfg.get("burnIn", False)) and captions_on

    music_cfg = video.get("music") or {}
    vo_cfg = video.get("voiceover") or {}
    ir_music = ir.get("music") if isinstance(ir.get("music"), dict) else {}
    music_enabled = (bool(music_cfg.get("enabled", False))
                     and bool(ir_music.get("enabled", True))
                     and not args.no_audio
                     and not args.no_music)
    # A track named on the command line is an explicit instruction, so it turns
    # music on for this film even when the brand or the IR has it switched off.
    if args.music and not (args.no_audio or args.no_music):
        music_enabled = True
    vo_enabled = bool(vo_cfg.get("enabled", True)) and not args.no_audio

    elements = build_elements(ir, brand, video, warnings)
    scenes_with_vo = [e for e in elements if e["kind"] == "scene" and e.get("vo")]
    need_vo = vo_enabled and bool(scenes_with_vo)
    slide_scenes = [e for e in elements if e.get("visualKind") == "slide"]
    if kind == "deck-video" and not slide_scenes:
        warnings.append(
            "kind is 'deck-video' but no scene references a deck slide; the whole "
            "film is built from motion templates and images")
    elif kind == "explainer" and slide_scenes:
        warnings.append(
            "kind is 'explainer' but %d scene(s) reference deck slides; a .pptx is "
            "required to build it" % len(slide_scenes))

    caption_font = caption_font_file(brand, captions_cfg)
    tools, burn_in = preflight(bool(slide_scenes), need_vo and not args.dry_run,
                               burn_in, args.allow_no_burn_in, warnings,
                               caption_font)

    # Scene motion is always rendered for this video, so a missing Chrome is
    # fatal up front. The bookends are checked after the cache has spoken: a
    # brand whose intro and outro are already cached needs no browser at all.
    if not args.dry_run and needs_chrome([e for e in elements if e["kind"] == "scene"]):
        require_chrome(tools, "the HTML motion templates")

    out_path = os.path.abspath(args.out)
    out_dir = os.path.dirname(out_path) or "."
    stem = os.path.splitext(out_path)[0]
    srt_path = stem + ".srt"
    vtt_path = stem + ".vtt"
    timeline_path = out_path + ".timeline.json"
    part_path = out_path + ".part.mp4"

    if args.work_dir:
        work_dir = os.path.abspath(args.work_dir)
        os.makedirs(work_dir, exist_ok=True)
        temp_owner = False
    elif args.dry_run:
        work_dir = os.path.join(tempfile.gettempdir(), "brand-studio-video-DRYRUN")
        temp_owner = False
    else:
        work_dir = tempfile.mkdtemp(prefix="brand-studio-video-")
        temp_owner = True

    search_dirs = [os.path.dirname(ir_path), brand.get("_dir", ""), brandlib.plugin_root(), os.getcwd()]
    brand_vars = build_brand_vars(brand, video, warnings, fmt)

    # ---- resolve the deck --------------------------------------------------
    slide_frames = []  # type: List[str]
    deck_path = None  # type: Optional[str]
    if slide_scenes:
        raw_deck = args.deck or ir.get("deck") or cfg(ir, "meta.deck")
        deck_path = resolve_path(raw_deck, search_dirs) if raw_deck else None
        if not deck_path:
            raise BuildError(
                "%d scene(s) reference deck slides but no .pptx was given.\n"
                "Pass --deck /path/to/deck.pptx%s"
                % (len(slide_scenes),
                   " (looked for %r)" % raw_deck if raw_deck else ""))
        if not args.dry_run:
            log("[1/6] rendering deck slides")
            slide_frames = render_deck_slides(tools, deck_path, work_dir, width, height, log)
            for element in slide_scenes:
                index = as_int(element.get("slide"), 0)
                if index < 1 or index > len(slide_frames):
                    raise BuildError(
                        "scene '%s' asks for slide %s but %s has %d slide(s)"
                        % (element["id"], element.get("slide"),
                           os.path.basename(deck_path), len(slide_frames)))
                element["source"] = slide_frames[index - 1]
                element["sourceKind"] = "still"
        else:
            for element in slide_scenes:
                element["source"] = os.path.join(work_dir, "slides",
                                                 "slide-%02d.png" % as_int(element.get("slide"), 0))
                element["sourceKind"] = "still"

    # ---- resolve images ----------------------------------------------------
    for element in elements:
        if element.get("visualKind") != "image":
            continue
        resolved = resolve_path(element.get("src"), search_dirs)
        if not resolved:
            raise BuildError(
                "scene '%s': image not found: %s" % (element["id"], element.get("src")))
        element["source"] = resolved
        element["sourceKind"] = "still"

    # ---- intro / outro supplied as files -----------------------------------
    for element in elements:
        if element["kind"] not in ("intro", "outro"):
            continue
        supplied = resolve_path(element.get("mediaFile"), search_dirs) if element.get("mediaFile") else None
        if supplied:
            element["source"] = supplied
            ext = os.path.splitext(supplied)[1].lower()
            element["sourceKind"] = "video" if ext in (".mp4", ".mov", ".m4v", ".webm") else "still"
            element["visualKind"] = "image"
            element["template"] = None

    # ---- brand intro / outro (rendered once per brand, then reused) ---------
    brand_asset_rows = resolve_brand_assets(
        elements, brand, brand_vars, tools, args, work_dir,
        width, height, fps, log, warnings, fmt)

    if not args.dry_run and needs_chrome(elements):
        require_chrome(tools, "the HTML motion templates")

    # ---- motion frames -----------------------------------------------------
    motion_elements = [e for e in elements
                       if e.get("visualKind") == "motion" and not e.get("source")]
    if motion_elements and not args.dry_run:
        log("[2/6] rendering %d motion element(s) with headless Chrome" % len(motion_elements))
        html_dir = os.path.join(work_dir, "html")
        os.makedirs(html_dir, exist_ok=True)
        for element in motion_elements:
            template = template_path(element.get("template") or "scene")
            data = dict(element.get("data") or {})
            if element["kind"] == "scene":
                data.setdefault("eyebrow", "")
                data.setdefault("title", element.get("caption") or "")
                data.setdefault("role", element.get("role"))
            image_ref = data.get("image")
            if image_ref:
                resolved = resolve_path(image_ref, search_dirs)
                if not resolved:
                    raise BuildError("scene '%s': data.image not found: %s"
                                     % (element["id"], image_ref))
                data["image"] = file_url(resolved)
            html_path = write_scene_html(
                template, brand_vars, data,
                os.path.join(html_dir, "%s.html" % re.sub(r"[^0-9A-Za-z_-]", "_", element["id"])))
            total_frames = max(1, int(round(element["duration"] * anim_fps)))
            if element["kind"] in ("intro", "outro"):
                anim_seconds = element["duration"]
            else:
                anim_seconds = min(element["duration"], as_float(args.motion_anim_sec, MOTION_ANIM_SEC))
            anim_frames = max(1, int(round(anim_seconds * anim_fps)))
            log("  %s (%s)" % (element["id"], os.path.basename(template)))
            element["source"] = render_motion_sequence(
                tools["chrome"], html_path,
                os.path.join(work_dir, "frames", re.sub(r"[^0-9A-Za-z_-]", "_", element["id"])),
                total_frames, anim_frames, width, height, max(1, as_int(args.jobs, 4)), log,
                os.path.join(work_dir, "chrome-profiles"))
            element["sourceKind"] = "sequence"
            element["htmlPath"] = html_path
    elif motion_elements:
        for element in motion_elements:
            element["source"] = os.path.join(
                work_dir, "frames", re.sub(r"[^0-9A-Za-z_-]", "_", element["id"]), "f_%06d.png")
            element["sourceKind"] = "sequence"

    for element in elements:
        if not element.get("source"):
            raise BuildError("element '%s' resolved to no visual source" % element["id"])

    # ---- voiceover ---------------------------------------------------------
    vo_info = {}  # type: Dict[str, Dict[str, Any]]
    vo_voice = vo_cfg.get("voice")
    vo_wpm = as_float(vo_cfg.get("rateWpm"), 165.0)
    if need_vo and not args.dry_run:
        log("[3/6] synthesising %d voiceover clip(s)" % len(scenes_with_vo))
        engine = str(vo_cfg.get("engine") or "say").lower()
        if engine != "say":
            raise BuildError(
                "unsupported voiceover engine '%s'. Only 'say' is implemented; "
                "set brand.video.voiceover.engine to 'say' or pass --no-audio." % engine)
        if vo_voice and tools["say"]:
            voices = available_say_voices(tools["say"])
            if voices and vo_voice not in voices:
                warnings.append(
                    "voice '%s' is not installed; falling back to the system default "
                    "voice" % vo_voice)
                vo_voice = None
        vo_dir = os.path.join(work_dir, "vo")
        os.makedirs(vo_dir, exist_ok=True)
        vo_lufs = as_float(vo_cfg.get("targetLufs"), -16.0)
        for element in scenes_with_vo:
            dest = os.path.join(vo_dir, "%s.wav" % re.sub(r"[^0-9A-Za-z_-]", "_", element["id"]))
            duration = synth_voiceover(tools, element["vo"], dest, vo_voice, vo_wpm, log)
            loud = measure_loudness(tools["ffmpeg"], dest, vo_lufs, LOUDNORM_TP_VO)
            if loud is None:
                warnings.append(
                    "could not measure the loudness of the '%s' voiceover; it is mixed "
                    "without normalisation" % element["id"])
            element["voLoudnorm"] = loud
            vo_info[element["id"]] = {"file": dest, "duration": duration, "source": "measured"}
            log("  %s  %0.2fs" % (element["id"], duration))
    elif need_vo:
        for element in scenes_with_vo:
            vo_info[element["id"]] = {
                "file": os.path.join(work_dir, "vo", "%s.wav" % element["id"]),
                "duration": estimate_vo_seconds(element["vo"], vo_wpm),
                "source": "estimated",
            }

    apply_voiceover_durations(elements, vo_info)

    # ---- timeline ----------------------------------------------------------
    xfade, transition_sec, transition_name = resolve_transition(video, warnings)
    total, transition_sec = lay_out_timeline(elements, transition_sec, warnings)
    if xfade is None:
        transition_sec = 0.0

    cues = build_captions(elements, captions_cfg, warnings) if captions_on else []
    # Everything downstream -- the SRT, the VTT and the burned overlay windows --
    # reads the SNAPPED timings, so all three describe the same frames.
    snap_cues_to_frames(cues, fps, as_float(captions_cfg.get("minDurationSec"), 1.2),
                        warnings)

    cap_style = caption_style(brand, video, fmt, caption_font)
    caption_overlays = []  # type: List[Dict[str, Any]]
    caption_track = None  # type: Optional[str]
    caption_mode = "off"
    if burn_in and cues:
        caption_mode = ("per-cue" if len(cues) <= CAPTION_OVERLAY_MAX else "track")
        if not args.dry_run:
            log("  drawing %d burnt-in caption frame(s)" % len(cues))
            composited = render_caption_overlays(
                cues, work_dir, cap_style, fps, total, log)
            caption_mode = composited["mode"]
            caption_overlays = composited["rows"]
            caption_track = composited["track"]

    # ---- music -------------------------------------------------------------
    # Priority: --music, then the IR's music.file, then the brand's, then a bed
    # synthesised by make_music.py for this film's exact length. Generation is
    # the default, so a brand that enables music always gets music.
    music_file = None  # type: Optional[str]
    music_loudnorm = None  # type: Optional[Dict[str, str]]
    music_summary = "disabled"
    music_bed = None  # type: Optional[Dict[str, Any]]
    if music_enabled:
        if args.music:
            raw_music, music_source = args.music, "--music"
        else:
            raw_music = ir_music.get("file") or music_cfg.get("file")
            music_source = ("the IR's music.file" if ir_music.get("file")
                            else "brand.video.music.file")
        if raw_music:
            music_file = resolve_path(raw_music, search_dirs)
            if not music_file:
                raise BuildError("music track not found: %s" % raw_music)
            music_bed = {
                "kind": MUSIC_BED_KIND, "slot": "-", "state": "SUPPLIED",
                "path": music_file, "mood": None,
                "label": "%s, %0.1f LUFS" % (os.path.basename(music_file),
                                             as_float(music_cfg.get("targetLufs"), -23.0)),
                "detail": "supplied by %s; nothing was generated" % music_source,
            }
        else:
            music_file, music_bed = resolve_music_bed(
                brand, music_cfg, ir_music, args, total, work_dir, tools, log, warnings)
        if music_file and not args.dry_run:
            music_loudnorm = measure_loudness(
                tools["ffmpeg"], music_file,
                as_float(music_cfg.get("targetLufs"), -23.0), LOUDNORM_TP_MUSIC)
            if music_loudnorm is None:
                warnings.append(
                    "could not measure the loudness of %s; the bed is mixed without "
                    "normalisation" % os.path.basename(music_file))
        if music_file:
            music_summary = "%s - %s, ducked %0.1f dB under narration" % (
                MUSIC_STATE_TEXT.get(music_bed["state"], music_bed["state"]),
                music_bed.get("label") or os.path.basename(music_file),
                as_float(music_cfg.get("duckUnderVoiceDb"), -18.0))
        else:
            music_summary = "generation failed; narration only (see the warning below)"
    elif args.no_music:
        music_summary = "off (--no-music)"
    elif args.no_audio:
        music_summary = "off (--no-audio)"
    elif not bool(music_cfg.get("enabled", False)):
        music_summary = "off (brand.video.music.enabled is false)"
    else:
        music_summary = "off (the IR sets music.enabled to false)"

    if need_vo:
        vo_summary = "say%s at %d wpm, %0.1f LUFS%s" % (
            " -v %s" % vo_voice if vo_voice else " (default voice)",
            int(round(vo_wpm)), as_float(vo_cfg.get("targetLufs"), -16.0),
            " [durations ESTIMATED]" if args.dry_run else "")
    elif args.no_audio:
        vo_summary = "off (--no-audio)"
    else:
        vo_summary = "off (brand.video.voiceover.enabled is false)" if not vo_enabled else "no narration in the IR"

    audio_enabled = bool(vo_info) or bool(music_file)

    plan = {
        "irPath": ir_path,
        "brandId": brand.get("id"),
        "brandName": brand.get("name"),
        "kind": kind,
        "outPath": out_path,
        "partPath": part_path,
        "srtPath": srt_path,
        "vttPath": vtt_path,
        "timelinePath": timeline_path,
        "workDir": work_dir,
        "width": width, "height": height, "fps": fps, "animFps": anim_fps,
        "format": fmt,
        "vcodec": str(video.get("vcodec") or "libx264"),
        "acodec": str(video.get("acodec") or "aac"),
        "crf": as_int(args.crf, 18), "preset": str(args.preset),
        "padColour": plan_pad_colour(brand),
        "kenburns": not args.no_kenburns,
        "xfade": xfade, "transitionSec": transition_sec, "transitionName": transition_name,
        "totalDurationSec": total,
        "durationTargetSec": cfg(ir, "meta.durationTargetSec"),
        "audioEnabled": audio_enabled,
        "videoInputCount": len(elements),
        "voLufs": as_float(vo_cfg.get("targetLufs"), -16.0),
        "musicFile": music_file,
        "musicLoudnorm": music_loudnorm,
        "masterLoudnorm": None,
        "programmeLufs": (as_float(vo_cfg.get("targetLufs"), -16.0) if vo_info
                          else as_float(music_cfg.get("targetLufs"), -23.0)),
        "musicLufs": as_float(music_cfg.get("targetLufs"), -23.0),
        "musicFadeIn": as_float(music_cfg.get("fadeInSec"), 1.0),
        "musicFadeOut": as_float(music_cfg.get("fadeOutSec"), 2.0),
        "duck": duck_params(music_cfg.get("duckUnderVoiceDb")),
        "burnIn": burn_in,
        "burnInStyle": burn_in_style(video, brand, fmt),
        "captionOverlays": caption_overlays,
        "captionTrack": caption_track,
        "captionMode": caption_mode,
        "captionFont": caption_font,
        "captionSizePx": cap_style["sizePx"],
        "captionBottomPx": cap_style["bottomPx"],
        "fontsDir": os.path.join(brand.get("_dir", ""), "assets", "fonts"),
        "capMaxChars": as_int(captions_cfg.get("maxCharsPerLine"), 42),
        "capMaxLines": as_int(captions_cfg.get("maxLines"), 2),
        "capMinDur": as_float(captions_cfg.get("minDurationSec"), 1.2),
        "voSummary": vo_summary,
        "musicSummary": music_summary,
        "musicBed": music_bed,
        "brandAssets": brand_asset_rows,
        "deck": deck_path,
        "dryRun": bool(args.dry_run),
    }

    if audio_enabled and not args.dry_run:
        log("[4/6] measuring the mixed programme")
        plan["masterLoudnorm"] = measure_programme_loudness(tools, elements, plan, log)
        if plan["masterLoudnorm"] is None:
            warnings.append(
                "could not measure the mixed programme; the master ships without a "
                "final loudness correction")

    cmd = build_ffmpeg_command(tools, elements, plan, args)

    if args.dry_run:
        print("%s - DRY RUN (nothing executed, no files written)" % PROG)
        print(format_timeline(elements, plan, cues, warnings))
        print("")
        print("FFMPEG COMMAND")
        print(format_command(cmd))
        print("")
        return 0

    # ---- captions on disk --------------------------------------------------
    os.makedirs(out_dir, exist_ok=True)
    if captions_on and cues:
        write_srt(cues, srt_path)
        write_vtt(cues, vtt_path)
        log("[5/6] wrote %d caption cue(s) to %s and %s"
            % (len(cues), srt_path, vtt_path))
    elif captions_on:
        warnings.append("captions are enabled but no scene carries caption or vo text")

    if burn_in and not (caption_overlays or caption_track):
        raise BuildError(
            "burn-in was requested but no caption frames were drawn; there is nothing "
            "to composite")

    # ---- encode ------------------------------------------------------------
    log("[6/6] encoding %s" % out_path)
    if os.path.exists(part_path):
        os.remove(part_path)
    log_path = os.path.join(work_dir, "ffmpeg.log")
    try:
        with open(log_path, "wb") as handle:
            proc = subprocess.run(cmd, stdout=handle, stderr=subprocess.STDOUT)
    except OSError as exc:
        raise BuildError("ffmpeg failed to start: %s" % exc)
    if proc.returncode != 0:
        tail = ""
        try:
            with open(log_path, "r") as handle:
                tail = "\n".join(handle.read().splitlines()[-40:])
        except OSError:
            pass
        if os.path.exists(part_path):
            os.remove(part_path)
        raise BuildError("ffmpeg exited %d\n%s\n\nfull log: %s"
                         % (proc.returncode, tail, log_path))
    if not os.path.isfile(part_path) or os.path.getsize(part_path) == 0:
        raise BuildError("ffmpeg produced no output at %s" % part_path)

    measured = ffprobe_duration(tools["ffprobe"], part_path)
    os.replace(part_path, out_path)

    # ---- sidecar -----------------------------------------------------------
    payload = timeline_payload(ir, brand, video, plan, elements, cues, cmd,
                               measured, warnings, tools)
    with open(timeline_path, "w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")

    if temp_owner and not args.keep_temp:
        shutil.rmtree(work_dir, ignore_errors=True)
    elif not args.quiet:
        log("  work files kept in %s" % work_dir)

    if not args.quiet:
        print("%s  %0.2fs  %s %dx%d @ %dfps"
              % (out_path, measured, fmt.get("name") or "resolution",
                 width, height, fps), file=sys.stderr)
        print("%s  %d cue(s)%s" % (srt_path, len(cues),
                                   "  burnt in (%s)" % caption_mode
                                   if burn_in else ""), file=sys.stderr)
        if cues:
            print("%s  %d cue(s)" % (vtt_path, len(cues)), file=sys.stderr)
        print("%s" % timeline_path, file=sys.stderr)
        if brand_asset_rows:
            print("brand assets (rendered once per brand, then reused)",
                  file=sys.stderr)
            for line in format_brand_assets(brand_asset_rows):
                print(line, file=sys.stderr)
        if music_bed:
            print("music bed (synthesised once per brand, mood and film length)",
                  file=sys.stderr)
            for line in format_music_bed(music_bed):
                print(line, file=sys.stderr)
        for warning in warnings:
            print("warning: %s" % warning, file=sys.stderr)
    return 0


def plan_pad_colour(brand):
    # type: (Dict[str, Any]) -> str
    navy = safe_hex(cfg(brand, "color.brand.navy"), "#0F0A6C")
    return "0x" + navy.lstrip("#")


def burn_in_style(video, brand, fmt=None):
    # type: (Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]]) -> str
    """The libass force_style string, sized and placed for the delivery format.

    Everything geometric comes from the resolved format: its canvas, its safe
    margin, its captionSizePt, and its bottom margin -- which is the larger of
    the brand's own captions.bottomMarginPct and whatever the format reserves
    for platform chrome. On a vertical reel that lifts the caption band clear of
    the bottom 20% the platform covers with its own UI, instead of burying it.
    """
    if fmt is None:
        fmt = resolve_delivery_format(video, None, "the brand default")
    caps = video.get("captions") or {}
    height = int(fmt["height"])
    width = int(fmt["width"])
    margin_v = int(round(as_float(fmt.get("captionBottomMarginPct"),
                                  as_float(caps.get("bottomMarginPct"), 8.0))
                         / 100.0 * height))
    margin_h = int(round(as_float(fmt.get("safeMarginPct"),
                                  as_float(video.get("safeMarginPct"), 5.0))
                         / 100.0 * width))
    fg = safe_hex(caps.get("color"), "#FFFFFF")
    bg = safe_hex(caps.get("background"), safe_hex(cfg(brand, "color.brand.navy"), "#0F0A6C"))
    opacity = as_float(caps.get("backgroundOpacity"), 0.82)
    parts = [
        "FontName=%s" % str(caps.get("font") or cfg(brand, "type.family", "Poppins")),
        "FontSize=%d" % int(round(as_float(fmt.get("captionSizePt"),
                                           as_float(caps.get("sizePt"), 28.0)))),
        "Bold=0", "Italic=0",
        "PrimaryColour=%s" % ass_colour(fg, 1.0),
        "BackColour=%s" % ass_colour(bg, opacity),
        "OutlineColour=%s" % ass_colour(bg, opacity),
        "BorderStyle=4", "Outline=0", "Shadow=0",
        "Alignment=2",
        "MarginV=%d" % margin_v,
        "MarginL=%d" % margin_h,
        "MarginR=%d" % margin_h,
    ]
    return ",".join(parts)


def timeline_payload(ir, brand, video, plan, elements, cues, cmd, measured, warnings, tools):
    # type: (Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], Sequence[str], float, List[str], Dict[str, Optional[str]]) -> Dict[str, Any]
    captions_cfg = video.get("captions") or {}
    fmt = plan.get("format") or {}
    element_rows = []
    for index, element in enumerate(elements):
        row = {
            "index": index,
            "kind": element["kind"],
            "id": element["id"],
            "role": element.get("role") or None,
            "startSec": element["start"],
            "visibleStartSec": element["visibleStart"],
            "endSec": element["end"],
            "durationSec": element["duration"],
            "declaredHoldSec": round(as_float(element.get("declaredHold"), 0.0), 3),
            "holdSource": element.get("holdSource"),
            "visualKind": element.get("visualKind"),
            "slide": element.get("slide"),
            "template": element.get("template"),
            "source": element.get("source"),
            "sourceKind": element.get("sourceKind"),
        }
        if element["kind"] == "scene":
            row["vo"] = element.get("vo") or ""
            row["caption"] = element.get("caption") or ""
            row["voFile"] = element.get("voFile")
            row["voDurationSec"] = element.get("voDurationSec")
            row["voStartSec"] = element.get("voStart")
            row["voEndSec"] = element.get("voEnd")
            row["captionCueIndexes"] = element.get("captionCues") or []
        if element["kind"] in BRAND_ASSET_KINDS:
            row["cacheState"] = element.get("cacheState") or "per-video"
            row["personalisedBy"] = list(element.get("personalisedBy") or [])
        element_rows.append(row)

    return {
        "version": 1,
        "generator": PROG,
        "generatedAt": datetime.datetime.now().replace(microsecond=0).isoformat(),
        "brand": brand.get("id"),
        "brandName": brand.get("name"),
        "kind": plan["kind"],
        "target": plan["outPath"],
        "irPath": plan["irPath"],
        "deck": plan.get("deck"),
        "meta": ir.get("meta") or {},
        "video": {
            "width": plan["width"], "height": plan["height"], "fps": plan["fps"],
            "container": str(video.get("container") or "mp4"),
            "vcodec": plan["vcodec"], "acodec": plan["acodec"],
            "crf": plan["crf"], "preset": plan["preset"],
            # The resolved delivery format. validate_video.py checks the render
            # against THIS, not against the brand default, so a vertical reel is
            # validated as a vertical reel.
            "format": fmt.get("name"),
            "formatSource": fmt.get("source"),
            "formatRatio": fmt.get("ratio"),
            "formatDeclared": bool(fmt.get("declared")),
            "safeMarginPct": as_float(fmt.get("safeMarginPct"),
                                      as_float(video.get("safeMarginPct"), 5.0)),
            "chromeTopPct": as_float(fmt.get("chromeTopPct"), 0.0),
            "chromeBottomPct": as_float(fmt.get("chromeBottomPct"), 0.0),
            "kenBurns": plan["kenburns"],
            "padColour": plan["padColour"],
        },
        "transition": {
            "type": plan["transitionName"],
            "xfade": plan["xfade"],
            "durationSec": plan["transitionSec"],
        },
        "totalDurationSec": plan["totalDurationSec"],
        "measuredDurationSec": round(measured, 3),
        "durationTargetSec": plan.get("durationTargetSec"),
        "audio": {
            "enabled": plan["audioEnabled"],
            "voiceover": {
                "enabled": bool([e for e in elements if e.get("voFile")]),
                "engine": str(cfg(video, "voiceover.engine", "say")),
                "voice": cfg(video, "voiceover.voice"),
                "rateWpm": as_float(cfg(video, "voiceover.rateWpm"), 165.0),
                "targetLufs": plan["voLufs"],
                "tailSec": VO_TAIL_SEC,
                "loudnessMode": ("two-pass-linear"
                                 if all(e.get("voLoudnorm") for e in elements
                                        if e.get("voFile"))
                                 else "mixed"),
            },
            "music": {
                "enabled": bool(plan["musicFile"]),
                "file": plan["musicFile"],
                # Where the bed came from: SUPPLIED / REUSED / GENERATED, plus
                # what it is, so a film can be traced back to its music without
                # re-deriving anything.
                "source": (plan.get("musicBed") or {}).get("state") or "OFF",
                "mood": (plan.get("musicBed") or {}).get("mood"),
                "cacheSlot": (plan.get("musicBed") or {}).get("slot"),
                "generator": (plan.get("musicBed") or {}).get("generator"),
                "sidecar": (plan.get("musicBed") or {}).get("sidecar"),
                "targetLufs": plan["musicLufs"],
                "duckUnderVoiceDb": as_float(cfg(video, "music.duckUnderVoiceDb"), -18.0),
                "duckThreshold": plan["duck"][0] if plan["duck"] else None,
                "duckRatio": plan["duck"][1] if plan["duck"] else None,
                "fadeInSec": plan["musicFadeIn"],
                "fadeOutSec": plan["musicFadeOut"],
                "loudnessMode": ("two-pass-linear" if plan.get("musicLoudnorm")
                                 else "unmeasured"),
            },
        },
        "captions": {
            "enabled": bool(captions_cfg.get("enabled", True)),
            "sidecar": plan["srtPath"],
            "sidecarVtt": plan.get("vttPath"),
            "burnIn": plan["burnIn"],
            # How the burn-in reached the picture, and what drew it. Captions are
            # composited from pillow-rendered RGBA frames; this ffmpeg has no
            # subtitles or drawtext filter to burn them any other way.
            "burnInMode": plan.get("captionMode") or "off",
            "burnInRenderer": "pillow+overlay",
            "font": plan.get("captionFont"),
            "fps": plan["fps"],
            # As actually applied for this format, not as declared on the brand.
            "sizePt": as_float(fmt.get("captionSizePt"),
                               as_float(captions_cfg.get("sizePt"), 28.0)),
            "bottomMarginPct": as_float(
                fmt.get("captionBottomMarginPct"),
                as_float(captions_cfg.get("bottomMarginPct"), 8.0)),
            "position": str(captions_cfg.get("position") or "bottom-center"),
            "maxCharsPerLine": plan["capMaxChars"],
            "maxLines": plan["capMaxLines"],
            "minDurationSec": plan["capMinDur"],
            "count": len(cues),
            "cues": cues,
        },
        "storyline": {
            "arc": cfg(video, "storyline.arc", []),
            "roles": [e.get("role") for e in elements if e["kind"] == "scene"],
        },
        "brandAssets": plan.get("brandAssets") or [],
        "elements": element_rows,
        "ffmpeg": {
            "binary": tools.get("ffmpeg"),
            "args": [str(t) for t in cmd],
            "command": " ".join(shlex.quote(str(t)) for t in cmd),
        },
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    # type: () -> argparse.ArgumentParser
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Build a branded MP4 and SRT from a Video IR and a brand profile.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "pipelines:\n"
            "  deck-video  a .pptx is rasterised to one PNG per slide and animated\n"
            "  explainer   HTML motion templates are rendered to PNG frame sequences\n\n"
            "delivery formats:\n"
            "  --format names one of brand.video.formats and drives the canvas,\n"
            "  the safe margin, the caption size and position, and the cache slot\n"
            "  the bookends live in. Priority: --format, then the IR's\n"
            "  meta.format, then the brand default. Without a formats block the\n"
            "  canvas is video.resolution, exactly as before.\n\n"
            "brand intro and outro:\n"
            "  Both are BRAND assets, not per-video ones. They are rendered once\n"
            "  into brands/<id>/video/generated/ and REUSED BY DEFAULT by every\n"
            "  later build, so every film for a brand opens and closes identically\n"
            "  and the most expensive step is paid for once. The cached clip is\n"
            "  invalidated automatically when the logo bytes, gradient, duration,\n"
            "  style, resolution or fps change -- so regenerate by hand only when\n"
            "  the user explicitly asks for it. An IR can personalise one film with\n"
            "  meta.introTitle / meta.outroTitle / meta.contact, which takes that\n"
            "  film off the shared asset and re-renders its bookend.\n"
            "  Inspect or drop the cache with: asset_cache.py --brand <id> --list\n\n"
            "music:\n"
            "  A brand that enables music GETS music. When no track is supplied,\n"
            "  make_music.py synthesises a copyright-free bed at this film's exact\n"
            "  length, in the mood the brand's video.music.mood/avoid imply, and it\n"
            "  is cached under brands/<id>/audio/generated/ on a fingerprint of the\n"
            "  mood, key, mode, bpm, seed, duration, target loudness and the brand's\n"
            "  music settings -- so a reel and a film of the same length and mood\n"
            "  built the same night share one bed. Priority: --music, the IR's\n"
            "  music.file, brand.video.music.file, then generation. The run summary\n"
            "  and --dry-run both say SUPPLIED / REUSED / GENERATED, with the mood\n"
            "  and the cache path. A generator that fails is a warning: the film\n"
            "  ships with narration only.\n\n"
            "examples:\n"
            "  %(prog)s --ir video.json --deck deck.pptx --out out.mp4\n"
            "  %(prog)s --ir video.json --deck deck.pptx --out out.mp4 --dry-run\n"
            "  %(prog)s --ir explainer.json --out out.mp4 --no-audio\n"
            "  %(prog)s --ir reel.json --out reel.mp4 --format vertical\n"
            "  %(prog)s --ir video.json --out out.mp4 --regenerate-brand-assets\n"
            "  %(prog)s --ir video.json --out out.mp4 --music-mood calm\n"
            "  %(prog)s --ir video.json --out out.mp4 --music bed.wav\n"
            "  %(prog)s --ir video.json --out out.mp4 --no-music\n"))
    parser.add_argument("--ir", required=True, help="path to the Video IR JSON")
    parser.add_argument("--brand", default=None,
                        help="brand id override (defaults to the IR's 'brand')")
    parser.add_argument("--out", required=True, help="output .mp4 path")
    parser.add_argument("--format", default=None, metavar="NAME",
                        help="delivery format to build, named in brand.video.formats "
                             "(e.g. landscape, square, vertical). Overrides the IR's "
                             "meta.format; without either, the brand's default is "
                             "used. It sets the canvas, the safe margin, the caption "
                             "size and position, and the cache slot the intro and "
                             "outro are stored in. A name the brand does not declare "
                             "is an error, never a silent fall back to landscape.")
    parser.add_argument("--deck", default=None,
                        help="source .pptx for scenes whose visual.kind is 'slide'")
    parser.add_argument("--no-audio", action="store_true",
                        help="skip voiceover and music; encode a silent video")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the computed timeline and the exact ffmpeg command "
                             "without executing anything (voiceover durations are "
                             "estimated from the brand's words-per-minute rate)")
    parser.add_argument("--anim-fps", type=int, default=0,
                        help="frame rate at which HTML motion SCENES are rendered "
                             "(default: the brand's fps; lower renders faster). The "
                             "cached brand intro and outro always render at the "
                             "brand's fps, so they stay a property of the brand.")

    intro_group = parser.add_mutually_exclusive_group()
    intro_group.add_argument("--reuse-intro", dest="regenerate_intro",
                             action="store_false", default=False,
                             help="reuse the brand's cached intro (THE DEFAULT; "
                                  "listed only so it can be stated explicitly)")
    intro_group.add_argument("--regenerate-intro", dest="regenerate_intro",
                             action="store_true",
                             help="re-render the brand's intro and replace the cached "
                                  "clip. Only on an explicit request from the user: a "
                                  "changed logo or palette already invalidates it "
                                  "automatically.")

    outro_group = parser.add_mutually_exclusive_group()
    outro_group.add_argument("--reuse-outro", dest="regenerate_outro",
                             action="store_false", default=False,
                             help="reuse the brand's cached outro (THE DEFAULT)")
    outro_group.add_argument("--regenerate-outro", dest="regenerate_outro",
                             action="store_true",
                             help="re-render the brand's outro and replace the cached "
                                  "clip. Only on an explicit request from the user.")

    parser.add_argument("--regenerate-brand-assets", action="store_true",
                        help="re-render BOTH bookends: the same as "
                             "--regenerate-intro --regenerate-outro. Reuse is the "
                             "default and should stay that way unless the user asks "
                             "for a re-render; a changed logo, gradient, duration, "
                             "style, resolution or fps invalidates the cache on its "
                             "own.")
    music_group = parser.add_mutually_exclusive_group()
    music_group.add_argument("--music", default=None, metavar="FILE",
                             help="use this audio file as the bed. The highest "
                                  "priority source there is: it beats the IR's "
                                  "music.file and the brand's, and it turns music on "
                                  "for this film even when the brand has it off. "
                                  "Nothing is generated and nothing is cached.")
    music_group.add_argument("--no-music", action="store_true",
                             help="ship this film with narration only, whatever the "
                                  "brand says. Nothing is generated and no cached bed "
                                  "is touched.")
    parser.add_argument("--music-mood", default=None, metavar="NAME",
                        help="mood for the GENERATED bed, overriding the one implied "
                             "by the brand's video.music.mood/avoid for this film "
                             "only (make_music.py ships: %s). Ignored when a track is "
                             "supplied." % ", ".join(MUSIC_MOODS))
    parser.add_argument("--regenerate-music", action="store_true",
                        help="synthesise a fresh bed and replace the cached one. "
                             "Reuse is the default and should stay that way: the "
                             "cache is already invalidated by a change of mood, "
                             "length, loudness, brand music settings or the "
                             "generator itself.")
    parser.add_argument("--motion-anim-sec", type=float, default=MOTION_ANIM_SEC,
                        help="seconds of animation rendered per motion scene before "
                             "the final frame holds (default: %0.1f)" % MOTION_ANIM_SEC)
    parser.add_argument("--jobs", type=int, default=4,
                        help="parallel Chrome renders (default: 4)")
    parser.add_argument("--no-kenburns", action="store_true",
                        help="disable the subtle push-in applied to still slides")
    parser.add_argument("--crf", type=int, default=18, help="x264 CRF (default: 18)")
    parser.add_argument("--preset", default="medium", help="x264 preset (default: medium)")
    parser.add_argument("--allow-no-burn-in", action="store_true",
                        help="when the brand asks for burnt-in captions but the "
                             "renderer cannot run (pillow missing, or no font file in "
                             "the brand's assets/fonts), downgrade it to a warning and "
                             "ship the SRT and VTT sidecars only")
    parser.add_argument("--work-dir", default=None,
                        help="reuse this directory for intermediates instead of a "
                             "temporary one")
    parser.add_argument("--keep-temp", action="store_true",
                        help="keep the temporary work directory after a successful build")
    parser.add_argument("--quiet", action="store_true", help="suppress progress output")
    return parser


def main(argv=None):
    # type: (Optional[Sequence[str]]) -> int
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if not args.anim_fps or args.anim_fps < 1:
            args.anim_fps = 0  # 0 means "use the brand fps"; resolved inside build()
        if args.jobs < 1:
            parser.error("--jobs must be at least 1")
        if args.regenerate_brand_assets:
            args.regenerate_intro = True
            args.regenerate_outro = True
        return build(args)
    except BuildError as exc:
        print("%s: error: %s" % (PROG, exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("%s: interrupted" % PROG, file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
