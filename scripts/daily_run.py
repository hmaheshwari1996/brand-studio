#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""daily_run.py -- the unattended nightly producer.

Runs at 2am with nobody watching. It takes the next pending day off the brand's
content queue, builds every job that day asks for, and leaves a review packet in
``brands/<id>/daily/<date>/`` for the human to skim at breakfast.

It never prompts, never blocks on a question and never half-finishes. Every
external call carries a timeout. The packet is assembled in a temporary
directory and moved into place in one step, so a crashed run leaves no
half-written packet -- only a failure packet that says exactly what went wrong.

WHAT A DAY IS
-------------
A queue entry is a DAY and a day holds N JOBS of mixed kind::

    {"id": "2026-07-31", "status": "pending", "jobs": [
       {"kind": "reel",  "durationSec": 15, "format": "vertical",  "topic": ...},
       {"kind": "video", "durationSec": 30, "format": "landscape", "topic": ...},
       {"kind": "deck",  "title": ..., "purpose": "pitch", "slides": 12, "brief": ...}
    ]}

Supported kinds: ``reel``, ``video`` (both -> ``build_video.py``) and ``deck``
(-> ``build_deck.py``). A day of eight decks works exactly as well as a day of
one long video.

A queue entry with no ``jobs`` array is the older flat shape -- one subject with
``topic`` / ``angle`` / ``oneThing`` / ``proof``. It is read as a day carrying
the two jobs in ``defaults`` (a reel and a video), so the shipped
``content-queue.json`` keeps working untouched.

NOTHING HERE CALLS A MODEL
--------------------------
This is a pure builder. Every script, every template choice and every deck slide
is derived deterministically from the queue item, the brand profile and the
grammar. Two runs of the same queue item produce the same IR.

CONCURRENCY
-----------
Jobs are independent and are built in a small worker pool. Eight decks serially
at 2am is fine; eight videos is not, so heavy jobs (anything that spawns Chrome
and ffmpeg) hold a separate, tighter semaphore:

    --jobs        overall pool size          default 4
    --video-jobs  concurrent film builds     default 2

One job failing never sinks the day. Every job gets its own entry in
``packet.json``'s ``artifacts``, its own validation result and its own error
field, and everything that succeeded is delivered.

FORMATS
-------
The IR always carries ``meta.format`` and the format's canvas under
``meta.canvas``. ``build_video.py`` currently masters at
``brand.video.resolution`` regardless, so a vertical reel comes out landscape
until that is wired through. The runner measures the delivered file with ffprobe
and records ``formatHonoured: false`` plus a note in the packet rather than
pretending. Nothing here patches the builder.

Usage:
    daily_run.py --dry-run
    daily_run.py --brand example --date 2026-07-31 --only reel
    daily_run.py --queue-item 2026-08-02 --force

Exit codes:
    0  every job delivered and passed validation
    1  internal failure -- nothing was delivered
    2  delivered, but at least one job failed to build or failed validation
    3  nothing to build -- the queue is empty and there is no evergreen fallback
"""

from __future__ import absolute_import, print_function

import argparse
import concurrent.futures
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback

from typing import Any, Dict, List, Optional, Sequence, Tuple  # noqa: F401

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from lib import brandlib  # noqa: E402


PROG = "daily_run.py"

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_PARTIAL = 2
EXIT_NOTHING = 3

# Every external call is bounded. A nightly job that hangs is worse than one
# that fails, because nobody is awake to notice.
TIMEOUT_BUILD_VIDEO = 1800.0    # Chrome frames + say + ffmpeg for one film
TIMEOUT_BUILD_DECK = 300.0      # python-pptx only
TIMEOUT_VALIDATE = 600.0        # samples frames with ffmpeg
TIMEOUT_FFPROBE = 60.0
TIMEOUT_FFMPEG_FRAME = 120.0
TIMEOUT_SOFFICE = 600.0
TIMEOUT_PDFTOPPM = 300.0

# Worker pool. Decks are cheap (pure python-pptx); films spawn headless Chrome
# per frame plus an ffmpeg filtergraph, so they get a tighter cap of their own.
DEFAULT_JOBS = 4
DEFAULT_VIDEO_JOBS = 2

# Films shorter than this ship without the brand's intro/outro bookends. At
# the brand's 3.0s + 3.5s a bookended 15s reel would be 43% logo, which is not
# a reel. Longer films carry both, and reuse the cached renders.
BOOKEND_MIN_SEC = 20.0

# Mirrors build_video.VO_TAIL_SEC: silence after narration before a scene cuts.
VO_TAIL_SEC = 0.4

# `say` does not speak at its nominal --rate. Two things move it, both
# measurable and both deterministic, so they are modelled rather than fudged:
#
#   numbers expand    "61%" is one written word and three spoken ones
#                     ("sixty one percent"). A proof line is mostly numbers, so
#                     estimating it on written words under-reads it by ~45%.
#   sentences pause   `say` inserts a beat at every full stop.
#
# Calibrated against a real build: 36 written words estimated at 15.9s against
# 16.5s of measured `say` output, a 4% error, which SAY_SAFETY absorbs. The
# WORD BUDGET is still counted in written words -- that is what the queue's
# rules mean -- and only the runtime uses the spoken count.
SPOKEN_WORDS_PER_DIGIT = 0.8
SPOKEN_WORDS_PER_UNIT = 1.0       # "%", "x", "percent"
SENTENCE_PAUSE_SEC = 0.35
SAY_SAFETY = 1.08

CONTACT_SHEET_FRAMES = 6
CONTACT_SHEET_COLS = 3
CONTACT_TILE_W = 480

# angle -> the template that carries that argument. Straight from
# skills/brand-video/references/motion-templates.md.
ANGLE_TEMPLATE = {
    "contrast": "compare",
    "number-first": "counter",
    "problem-first": "kinetic-type",
    "process": "process-flow",
    "single-person": "kinetic-type",
    "outsider": "compare",
}

# beat -> templates in order of preference. The first one whose data can
# actually be derived from this queue item wins; adjacency is de-duplicated
# afterwards so no two neighbouring beats use the same motion.
BEAT_TEMPLATES = {
    "hook": ["kinetic-type", "compare", "scene"],
    "turn": ["compare", "counter", "chart-reveal", "kinetic-type", "scene"],
    "problem": ["compare", "chart-reveal", "kinetic-type", "scene"],
    "approach": ["process-flow", "compare", "scene"],
    "proof": ["counter", "chart-reveal", "coverage-map", "scene"],
    "payoff": ["counter", "chart-reveal", "kinetic-type", "scene"],
    "outcome": ["coverage-map", "counter", "chart-reveal", "scene"],
    "call-to-action": ["kinetic-type", "scene"],
}
DEFAULT_BEAT_TEMPLATES = ["kinetic-type", "scene"]

# Share of the word budget each beat gets. Anything unlisted splits the rest.
BEAT_WEIGHTS = {
    "hook": 0.30, "turn": 0.34, "payoff": 0.36,
    "problem": 0.28, "proof": 0.32, "call-to-action": 0.18,
    "approach": 0.25, "outcome": 0.25,
}

# A beat name is an authoring label; a ROLE is a stage of the brand's storyline
# arc, and validate_video.py checks scene roles against that arc. The queue's
# reel beats ("turn", "payoff") are the arc's "problem" and "proof" wearing
# short-form names, so the IR carries the arc stage as `role` and keeps the beat
# name alongside it as `beat` for the review packet.
BEAT_TO_ARC = {
    "hook": "hook",
    "turn": "problem",
    "problem": "problem",
    "approach": "approach",
    "proof": "proof",
    "payoff": "proof",
    "outcome": "outcome",
    "call-to-action": "call-to-action",
    "cta": "call-to-action",
}


def arc_role(beat, brand):
    # type: (str, Dict[str, Any]) -> str
    """The storyline stage a beat belongs to, if the brand declares an arc."""
    arc = [str(a) for a in
           (((brand.get("video") or {}).get("storyline") or {}).get("arc") or [])]
    beat = str(beat)
    if not arc:
        return beat
    if beat in arc:
        return beat
    mapped = BEAT_TO_ARC.get(beat)
    return mapped if mapped in arc else beat


BEAT_EYEBROW = {
    "hook": "The problem", "turn": "The turn", "payoff": "The proof",
    "problem": "The problem", "proof": "Proof", "approach": "How it works",
    "outcome": "Outcome", "call-to-action": "Next step",
}

FILM_KINDS = ("reel", "video")
DECK_KINDS = ("deck",)
ALL_KINDS = FILM_KINDS + DECK_KINDS

# Deck capacities, author-to values from skills/brand-deck/references/archetypes.md.
CAP = {
    "eyebrow": 28, "title": 42, "deckline": 160,
    "cover.title": 26, "cover.subtitle": 61, "cover.meta": 60,
    "agenda.item": 36,
    "section.title": 44, "section.kicker": 180,
    "body": 900,
    "columns.intro": 173, "columns.subtitle": 24, "columns.body": 320,
    "steps.number": 46, "steps.body": 190,
    "stats.intro": 173, "stats.value": 10, "stats.label": 24, "stats.caption": 90,
    "closing.title": 26, "closing.cta": 61, "closing.contact": 140,
}


class DailyError(Exception):
    """Anything that stops the run with a message a human can act on."""


# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------

class Log(object):
    """Everything goes to run.log and to stderr. Thread-safe; stdout is
    reserved for the single cron summary line."""

    def __init__(self, path=None, quiet=False):
        self._lock = threading.Lock()
        self._lines = []  # type: List[str]
        self._path = path
        self._quiet = quiet
        self._handle = None
        if path:
            try:
                self._handle = open(path, "a")
            except IOError:
                self._handle = None

    def __call__(self, msg):
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = "%s  %s" % (stamp, msg)
        with self._lock:
            self._lines.append(line)
            if self._handle is not None:
                try:
                    self._handle.write(line + "\n")
                    self._handle.flush()
                except IOError:
                    pass
            if not self._quiet:
                print(line, file=sys.stderr)

    def text(self):
        with self._lock:
            return "\n".join(self._lines) + "\n"

    def close(self):
        with self._lock:
            if self._handle is not None:
                try:
                    self._handle.close()
                except IOError:
                    pass
                self._handle = None


# ---------------------------------------------------------------------------
# subprocess plumbing -- never raises, always bounded
# ---------------------------------------------------------------------------

def run_cmd(cmd, timeout, label=None):
    # type: (Sequence[str], float, Optional[str]) -> Dict[str, Any]
    """Run a command with a hard timeout. Returns a result dict, never raises."""
    label = label or os.path.basename(str(cmd[0]))
    started = time.time()
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        return {"rc": 127, "stdout": "", "stderr": "%s could not be started: %s" % (label, exc),
                "seconds": 0.0, "timedOut": False, "cmd": list(cmd)}
    try:
        out, err = proc.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            out, err = proc.communicate(timeout=30)
        except Exception:  # pragma: no cover - the child refused to die
            out, err = b"", b""
        timed_out = True
        err = (err or b"") + (
            "\n%s exceeded its %ds timeout and was killed." % (label, int(timeout))).encode("utf-8")
    return {
        "rc": 124 if timed_out else proc.returncode,
        "stdout": (out or b"").decode("utf-8", "replace"),
        "stderr": (err or b"").decode("utf-8", "replace"),
        "seconds": round(time.time() - started, 2),
        "timedOut": timed_out,
        "cmd": list(cmd),
    }


def which(name):
    # type: (str) -> Optional[str]
    return shutil.which(name)


def ffprobe_stream(path):
    # type: (str) -> Dict[str, Any]
    """Duration and pixel dimensions of a media file. Empty dict on any failure."""
    ffprobe = which("ffprobe")
    if not ffprobe or not os.path.isfile(path):
        return {}
    res = run_cmd([ffprobe, "-v", "error", "-print_format", "json",
                   "-show_format", "-show_streams", path], TIMEOUT_FFPROBE, "ffprobe")
    if res["rc"] != 0:
        return {}
    try:
        info = json.loads(res["stdout"])
    except ValueError:
        return {}
    out = {}  # type: Dict[str, Any]
    try:
        out["durationSec"] = round(float((info.get("format") or {}).get("duration")), 2)
    except (TypeError, ValueError):
        out["durationSec"] = None
    for stream in info.get("streams") or []:
        if stream.get("codec_type") == "video":
            out["width"] = stream.get("width")
            out["height"] = stream.get("height")
            out["vcodec"] = stream.get("codec_name")
            break
    return out


# ---------------------------------------------------------------------------
# text plumbing
# ---------------------------------------------------------------------------

_SENTENCE_RE = re.compile(r"[^.?]+[.?]|[^.?]+$")
_NUMBER_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?\s*(?:%|x|percent)?)", re.IGNORECASE)


def clean(text):
    # type: (Any) -> str
    if text is None:
        return ""
    out = re.sub(r"\s+", " ", str(text)).strip()
    return out.replace("!", "")


def sentences(text):
    # type: (str) -> List[str]
    return [s.strip() for s in _SENTENCE_RE.findall(clean(text)) if s.strip()]


def words_of(text):
    # type: (str) -> int
    return brandlib.word_count(clean(text))


def end_stopped(text):
    # type: (str) -> str
    """Give a fragment a full stop, never a dangling comma or conjunction."""
    out = clean(text)
    if not out:
        return ""
    out = re.sub(r"[\s,;:\-]+$", "", out)
    out = re.sub(r"\s+(and|or|but|so|because|which|that|with|from|to|of|in|on)$", "",
                 out, flags=re.IGNORECASE)
    if out and out[-1] not in ".?":
        out += "."
    return out


SENTENCE_SLACK = 0.35


def trim_to_words(text, max_words):
    # type: (str, int) -> str
    """Trim to a word budget, on a sentence boundary wherever possible.

    Whole sentences first. A sentence that overruns the allocation by less than
    SENTENCE_SLACK is kept whole anyway -- chopping a clause in half to save one
    word produces copy no human would say, and the caller's fit loop tightens
    the budget until a whole sentence really does have to go. Only when not even
    the first sentence is close does the line get cut at a word boundary.
    Deterministic: same input, same output.
    """
    max_words = max(1, int(max_words))
    text = clean(text)
    if not text:
        return ""
    if words_of(text) <= max_words:
        return end_stopped(text)
    ceiling = max_words * (1.0 + SENTENCE_SLACK)
    kept = []  # type: List[str]
    used = 0
    for sentence in sentences(text):
        count = words_of(sentence)
        if kept and used + count > max_words:
            break
        if not kept and count > ceiling:
            break
        kept.append(sentence)
        used += count
        if used >= max_words:
            break
    if kept:
        return end_stopped(" ".join(kept))
    tokens = text.split(" ")
    return end_stopped(" ".join(tokens[:max_words]))


def trim_chars(text, limit):
    # type: (str, int) -> str
    """Trim to a character capacity on a word boundary. Used for deck fields."""
    text = clean(text)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" ,;:-")


def cap_field(text, limit, end_stop=False):
    # type: (str, int, bool) -> str
    out = trim_chars(text, limit)
    if end_stop and out:
        out = end_stopped(out)
        if len(out) > limit:
            out = trim_chars(out, limit)
    return out


def spoken_words(text):
    # type: (str) -> float
    """Written words, adjusted for how many words `say` will actually utter."""
    text = clean(text)
    if not text:
        return 0.0
    total = float(brandlib.word_count(text))
    for token in re.findall(r"\d[\d,.]*\s*(?:%|x\b|percent)?", text, re.IGNORECASE):
        digits = len(re.sub(r"[^0-9]", "", token))
        if digits:
            total += max(0.0, digits * SPOKEN_WORDS_PER_DIGIT - 1.0)
        if re.search(r"(%|x\b|percent)", token, re.IGNORECASE):
            total += SPOKEN_WORDS_PER_UNIT
    return total


def narration_seconds(text, wpm):
    # type: (str, float) -> float
    """How long `say` will take over this line, with a safety margin."""
    count = spoken_words(text)
    if count <= 0:
        return 0.0
    rate = max(60.0, float(wpm or 165.0))
    pauses = SENTENCE_PAUSE_SEC * max(1, len(sentences(text)))
    return round((count / rate * 60.0 + pauses) * SAY_SAFETY, 3)


def title_from(text, limit=None):
    # type: (str, Optional[int]) -> str
    """A slide title out of a sentence: a claim, not a truncated paragraph.

    Prefers the leading clause, drops the full stop (titles do not take one) and
    never leaves a dangling preposition or conjunction at the cut.
    """
    limit = CAP["title"] if limit is None else limit
    text = clean(text).rstrip(".")
    if not text:
        return ""
    if len(text) > limit and "," in text:
        head = text.split(",", 1)[0].strip()
        if len(head) >= max(12, limit // 3):
            text = head
    out = trim_chars(text, limit)
    for _ in range(4):                      # "... person in the" -> "... person"
        stripped = re.sub(
            r"\s+(and|or|but|so|because|which|that|with|from|to|of|in|on|at|by|a|an|the"
            r"|you|we|it|they|is|are|was|were|has|have)$",
            "", out, flags=re.IGNORECASE).rstrip(" ,;:-")
        if stripped == out:
            break
        out = stripped
    return out


def numbers_in(text):
    # type: (str) -> List[str]
    """Human-readable figures in the order they appear: 61%, 4,200, 3.4x, 31."""
    found = []  # type: List[str]
    for raw in _NUMBER_RE.findall(clean(text)):
        value = raw.strip()
        if not value or not re.search(r"\d", value):
            continue
        value = re.sub(r"\s+percent$", "%", value, flags=re.IGNORECASE)
        value = re.sub(r"\s+", "", value)
        if value not in found:
            found.append(value)
    return found


_FROM_TO_RE = re.compile(r"\bfrom\s+([^\s,]+)\s+to\s+([^\s,.]+)", re.IGNORECASE)

#: Verbs and prepositions that end a claim's subject.
_SUBJECT_SPLIT = re.compile(
    r"\s+(?:rose|fell|grew|dropped|moved|increased|decreased|improved|reached|ran|"
    r"held|is|are|was|were|of|from|at|by|across)\s+", re.IGNORECASE)


#: A figure whose following words start with one of these is embedded in a
#: phrase ("level 3 or above"), not a metric of its own.
_NOT_A_METRIC = ("or", "and", "above", "below", "but", "than", "plus")


def label_after(text, number):
    # type: (str, str) -> str
    """The words that follow a figure, as its label.

    Stops at the first comma, because the next clause usually belongs to the
    next figure: in "4,200 stores, 19 states" the label for 4,200 is "stores".
    """
    bare = re.escape(number.replace("%", "").replace("x", ""))
    match = re.search(bare + r"[%x]?\s+([^,.;]{1,40})", clean(text), re.IGNORECASE)
    if not match:
        return ""
    tail = clean(match.group(1))
    tail = re.sub(r"^(of|in|to|from|at|and|the|a|an)\s+", "", tail, flags=re.IGNORECASE)
    if tail.split(" ")[0].lower() in _NOT_A_METRIC:
        return ""
    return title_from(" ".join(tail.split(" ")[:2]), 24)


def figure_stats(claim, source, limit=3):
    # type: (str, str, int) -> List[Dict[str, str]]
    """Figures in a claim, each with a label that is not itself a figure.

    A figure nobody can label is dropped rather than captioned "Measured" --
    unless it is the only one, in which case the claim's subject carries it.
    """
    claim = clean(claim)
    stats = []  # type: List[Dict[str, str]]
    figures = numbers_in(claim)
    subject = claim_subject(claim)
    for index, value in enumerate(figures):
        if len(stats) >= limit:
            break
        label = label_after(claim, value)
        if label and re.search(r"\d", label):
            label = ""
        if not label:
            if index > 0 or not subject:
                continue
            label = subject
        stats.append({"value": value, "label": label,
                      "caption": cap_field(source, 40)})
    if not stats and figures:
        stats.append({"value": figures[0], "label": subject or "Measured",
                      "caption": cap_field(source, 40)})
    return stats


def claim_subject(text):
    # type: (str) -> str
    """What a claim is about: the noun phrase before its first verb or figure."""
    head = _SUBJECT_SPLIT.split(clean(text).rstrip("."), 1)[0]
    head = re.sub(r"\d.*$", "", head).strip(" ,;:-")
    return title_from(head, 24)


# ---------------------------------------------------------------------------
# brand + queue
# ---------------------------------------------------------------------------

def resolve_house_brand(explicit):
    # type: (Optional[str]) -> Dict[str, Any]
    if explicit:
        return brandlib.load_brand(explicit)
    entries = brandlib.list_brands()
    if not entries:
        raise DailyError(
            "no brands are registered. Add one with /brand-new, or pass --brand.")
    house = []
    for entry in entries:
        try:
            profile = brandlib.load_brand(entry["id"])
        except Exception:
            continue
        if str(profile.get("kind") or "").lower() == "house":
            house.append(profile)
    if len(house) == 1:
        return house[0]
    if not house:
        if len(entries) == 1:
            return brandlib.load_brand(entries[0]["id"])
        raise DailyError(
            "no house brand in the registry and %d brands are registered. "
            "Pass --brand explicitly." % len(entries))
    raise DailyError(
        "%d house brands are registered (%s). Pass --brand explicitly."
        % (len(house), ", ".join(sorted(str(b.get("id")) for b in house))))


def queue_path(brand):
    # type: (Dict[str, Any]) -> str
    return os.path.join(brand.get("_dir") or "", "content-queue.json")


def load_queue(path):
    # type: (str) -> Dict[str, Any]
    if not os.path.isfile(path):
        raise DailyError(
            "no content queue at %s. brand-daily needs a queue to take a subject "
            "from; create one with a 'queue' array of day objects." % path)
    try:
        with open(path, "r") as handle:
            data = json.load(handle)
    except ValueError as exc:
        raise DailyError("%s is not valid JSON: %s" % (path, exc))
    if not isinstance(data, dict):
        raise DailyError("%s must contain a JSON object" % path)
    return data


def parse_date(value):
    # type: (str) -> datetime.date
    try:
        return datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        raise DailyError("--date must be YYYY-MM-DD (found %r)" % value)


def history_blocks(queue, day, run_date):
    # type: (Dict[str, Any], Dict[str, Any], datetime.date) -> Optional[str]
    """Why noRepeatWithinDays rejects this day, or None if it is clear."""
    rules = queue.get("rules") or {}
    window = rules.get("noRepeatWithinDays")
    try:
        window = int(window)
    except (TypeError, ValueError):
        return None
    if window <= 0:
        return None
    subjects = set()
    for job in day.get("jobs") or []:
        for key in ("topic", "title"):
            if clean(job.get(key)):
                subjects.add(clean(job.get(key)).lower())
    day_id = str(day.get("id") or "")
    for entry in queue.get("history") or []:
        if not isinstance(entry, dict):
            continue
        try:
            when = parse_date(entry.get("date") or "")
        except DailyError:
            continue
        age = (run_date - when).days
        if age < 0 or age >= window:
            continue
        if day_id and str(entry.get("id") or "") == day_id:
            return "ran %d day(s) ago as %s" % (age, entry.get("date"))
        topic = clean(entry.get("topic")).lower()
        if topic and topic in subjects:
            return "topic '%s' ran %d day(s) ago" % (entry.get("topic"), age)
    return None


# ---------------------------------------------------------------------------
# day / job normalisation
# ---------------------------------------------------------------------------

def normalise_day(entry, queue, source):
    # type: (Dict[str, Any], Dict[str, Any], str) -> Dict[str, Any]
    """Turn a queue entry -- new 'jobs' shape or the older flat shape -- into a day."""
    defaults = queue.get("defaults") or {}
    raw_jobs = entry.get("jobs")

    if not isinstance(raw_jobs, list) or not raw_jobs:
        # Backward compatibility: a flat item is a day carrying the two default
        # jobs, both about the same subject.
        raw_jobs = []
        for kind in FILM_KINDS:
            if kind in defaults:
                job = dict(defaults.get(kind) or {})
                job["kind"] = kind
                raw_jobs.append(job)
        if not raw_jobs:
            raw_jobs = [{"kind": "reel", "durationSec": 15, "format": "vertical"},
                        {"kind": "video", "durationSec": 30, "format": "landscape"}]

    inherited = dict((k, v) for k, v in entry.items()
                     if k not in ("jobs", "status", "id"))

    jobs = []  # type: List[Dict[str, Any]]
    counts = {}  # type: Dict[str, int]
    for raw in raw_jobs:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "video").strip().lower()
        if kind not in ALL_KINDS:
            kind = "deck" if raw.get("slides") or raw.get("purpose") else "video"
        job = {}  # type: Dict[str, Any]
        job.update(inherited)                       # day-level subject fields
        job.update(defaults.get(kind) or {})        # queue defaults for the kind
        job.update(raw)                             # the job's own fields win
        job["kind"] = kind
        counts[kind] = counts.get(kind, 0) + 1
        jobs.append(job)

    seen = {}  # type: Dict[str, int]
    for job in jobs:
        kind = job["kind"]
        explicit = clean(job.get("id"))
        if explicit and explicit != clean(entry.get("id")):
            job["jobId"] = re.sub(r"[^0-9A-Za-z_.-]+", "-", explicit).strip("-") or kind
        elif counts.get(kind, 0) > 1:
            seen[kind] = seen.get(kind, 0) + 1
            job["jobId"] = "%s-%d" % (kind, seen[kind])
        else:
            job["jobId"] = kind

    used = set()
    for job in jobs:
        base = job["jobId"]
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = "%s-%d" % (base, suffix)
            suffix += 1
        used.add(candidate)
        job["jobId"] = candidate

    return {
        "id": str(entry.get("id") or source),
        "status": str(entry.get("status") or "pending"),
        "source": source,
        "jobs": jobs,
        "raw": entry,
    }


def select_day(queue, args, run_date):
    # type: (Dict[str, Any], Any, datetime.date) -> Tuple[Optional[Dict[str, Any]], bool, List[str]]
    """The day to build. Returns (day, fallback, reasons-it-skipped-things)."""
    notes = []  # type: List[str]
    items = [i for i in (queue.get("queue") or []) if isinstance(i, dict)]

    if args.queue_item:
        for entry in items:
            if str(entry.get("id")) == str(args.queue_item):
                day = normalise_day(entry, queue, "queue")
                if str(entry.get("status") or "pending") != "pending" and not args.force:
                    notes.append("queue item %s is '%s'; pass --force to rebuild it"
                                 % (args.queue_item, entry.get("status")))
                    return None, False, notes
                return day, False, notes
        for index, entry in enumerate(queue.get("evergreen") or []):
            if str(entry.get("id") or "evergreen-%d" % (index + 1)) == str(args.queue_item):
                return normalise_day(entry, queue, "evergreen"), True, notes
        notes.append("no queue or evergreen item with id %r" % args.queue_item)
        return None, False, notes

    for entry in items:
        if str(entry.get("status") or "pending") != "pending":
            continue
        day = normalise_day(entry, queue, "queue")
        blocked = history_blocks(queue, day, run_date)
        if blocked and not args.force:
            notes.append("skipped %s: %s (rules.noRepeatWithinDays)" % (day["id"], blocked))
            continue
        return day, False, notes

    evergreen = [e for e in (queue.get("evergreen") or []) if isinstance(e, dict)]
    for index, entry in enumerate(evergreen):
        entry = dict(entry)
        entry.setdefault("id", "evergreen-%d" % (index + 1))
        day = normalise_day(entry, queue, "evergreen")
        blocked = history_blocks(queue, day, run_date)
        if blocked and not args.force:
            notes.append("skipped evergreen %s: %s" % (day["id"], blocked))
            continue
        notes.append("the queue has no pending item; fell back to evergreen '%s'" % day["id"])
        return day, True, notes

    if not items and not evergreen:
        notes.append("both 'queue' and 'evergreen' are empty")
    elif not evergreen:
        notes.append("every queue item is done or blocked, and there is no "
                     "evergreen fallback to fall back to")
    else:
        notes.append("every queue item is done or blocked and every evergreen "
                     "item is inside the noRepeatWithinDays window")
    return None, False, notes


# ---------------------------------------------------------------------------
# script derivation -- deterministic, no model call
# ---------------------------------------------------------------------------

def beat_source_text(role, job):
    # type: (str, Dict[str, Any]) -> str
    """The raw material a beat is composed from, before trimming."""
    topic = clean(job.get("topic")) or clean(job.get("title"))
    one_thing = clean(job.get("oneThing"))
    proof = job.get("proof") if isinstance(job.get("proof"), dict) else {}
    claim = clean(proof.get("claim"))
    source = clean(proof.get("source"))
    cta = clean(job.get("cta"))

    overrides = job.get("lines") if isinstance(job.get("lines"), dict) else {}
    if clean(overrides.get(role)):
        return clean(overrides.get(role))

    if role == "hook":
        return end_stopped(topic)
    if role in ("turn", "problem", "approach"):
        return end_stopped(one_thing or topic)
    if role in ("proof", "payoff", "outcome"):
        parts = [end_stopped(claim)]
        if source:
            parts.append(end_stopped("Measured across %s" % source.rstrip(".")))
        return " ".join(p for p in parts if p)
    if role == "call-to-action":
        if cta:
            return end_stopped(cta)
        return end_stopped("Start with one territory and one metric")
    return end_stopped(one_thing or topic)


def allocate_words(beats, budget):
    # type: (List[str], int) -> Dict[str, int]
    weights = [BEAT_WEIGHTS.get(b, 1.0 / max(1, len(beats))) for b in beats]
    total = sum(weights) or 1.0
    out = {}
    for role, weight in zip(beats, weights):
        out[role] = max(3, int(round(budget * weight / total)))
    return out


def compose_lines(beats, raw, budget):
    # type: (List[str], Dict[str, str], int) -> Dict[str, str]
    """Trim every beat to its share of the budget, then hand the slack back.

    A hook that is five words long should not force the proof beat to drop its
    source clause. Beats that come in under their allocation release the
    difference to beats whose source text was actually truncated, in beat order,
    until either the budget or the material runs out.
    """
    allocation = allocate_words(beats, budget)
    lines = dict((role, trim_to_words(raw[role], allocation[role])) for role in beats)
    for _ in range(4):
        used = sum(words_of(lines[r]) for r in beats)
        slack = budget - used
        if slack <= 0:
            break
        hungry = [r for r in beats if words_of(lines[r]) < words_of(raw[r])]
        if not hungry:
            break
        share = max(1, slack // len(hungry))
        moved = False
        for role in hungry:
            if slack <= 0:
                break
            grant = min(share, slack)
            candidate = trim_to_words(raw[role], allocation[role] + grant)
            gained = words_of(candidate) - words_of(lines[role])
            if gained <= 0:
                continue
            # A grant that would push the whole script past the budget is not a
            # grant. The sentence slack in trim_to_words can overshoot, so the
            # total is the authority, not the per-beat allocation.
            if used + gained > budget:
                continue
            allocation[role] += grant
            slack -= grant
            used += gained
            lines[role] = candidate
            moved = True
        if not moved:
            break
    return lines


def estimate_timeline(lines_by_beat, beats, video_cfg, with_bookends):
    # type: (Dict[str, str], List[str], Dict[str, Any], bool) -> Dict[str, Any]
    """Mirror build_video's arithmetic closely enough to size a script.

    scene = max(brand hold floor, narration + tail); the whole film loses one
    transition per join.
    """
    wpm = float(((video_cfg.get("voiceover") or {}).get("rateWpm")) or 165.0)
    hold_min = float(((video_cfg.get("slideHoldSec") or {}).get("min")) or 3.0)
    transition = float(((video_cfg.get("transition") or {}).get("durationSec")) or 0.4)
    intro_sec = float(((video_cfg.get("intro") or {}).get("durationSec")) or 3.0)
    outro_sec = float(((video_cfg.get("outro") or {}).get("durationSec")) or 3.5)

    durations = []  # type: List[float]
    scene_rows = []  # type: List[Dict[str, Any]]
    if with_bookends:
        durations.append(intro_sec)
    for role in beats:
        line = lines_by_beat.get(role, "")
        count = words_of(line)
        narration = narration_seconds(line, wpm)
        duration = max(hold_min, narration + VO_TAIL_SEC)
        durations.append(duration)
        scene_rows.append({"role": role, "words": count,
                           "spokenWords": round(spoken_words(line), 1),
                           "narrationSec": round(narration, 2),
                           "holdSec": round(duration, 2)})
    if with_bookends:
        durations.append(outro_sec)

    if not durations:
        return {"totalSec": 0.0, "scenes": [], "transitionSec": 0.0, "words": 0}
    effective = transition
    if len(durations) > 1:
        effective = min(effective, max(0.0, min(durations) * 0.4))
    total = sum(durations) - (len(durations) - 1) * effective
    return {
        "totalSec": round(total, 2),
        "scenes": scene_rows,
        "transitionSec": round(effective, 3),
        "words": sum(r["words"] for r in scene_rows),
        "introSec": intro_sec if with_bookends else 0.0,
        "outroSec": outro_sec if with_bookends else 0.0,
    }


def derive_film_script(job, brand, rules):
    # type: (Dict[str, Any], Dict[str, Any], Dict[str, Any]) -> Dict[str, Any]
    """Compose the beats, enforce the word budget and the runtime by construction."""
    video_cfg = brand.get("video") or {}
    kind = job["kind"]
    beats = [str(b) for b in (job.get("beats") or [])]
    if not beats:
        beats = (["hook", "turn", "payoff"] if kind == "reel"
                 else ["hook", "problem", "proof", "call-to-action"])
    slot = float(job.get("durationSec") or (15 if kind == "reel" else 30))

    budget_key = "reelWordBudget" if kind == "reel" else "videoWordBudget"
    budget = rules.get(budget_key)
    try:
        budget = int(budget)
    except (TypeError, ValueError):
        budget = 38 if kind == "reel" else 78
    hard_budget = budget

    with_bookends = bool(job.get("bookends", slot >= BOOKEND_MIN_SEC))

    raw = dict((role, beat_source_text(role, job)) for role in beats)

    # Shrink until BOTH the word budget and the runtime slot are satisfied. The
    # budget is a ceiling the queue sets; the slot is physics. A script that
    # would overrun its slot is a bug, so we trim rather than warn.
    notes = []  # type: List[str]
    effective = budget
    timeline = {}  # type: Dict[str, Any]
    lines = {}  # type: Dict[str, str]
    for attempt in range(24):
        lines = compose_lines(beats, raw, effective)
        total_words = sum(words_of(lines[r]) for r in beats)
        timeline = estimate_timeline(lines, beats, video_cfg, with_bookends)
        if total_words <= hard_budget and timeline["totalSec"] <= slot + 1e-6:
            break
        effective = int(max(len(beats) * 3, round(effective * 0.9)))
    else:  # pragma: no cover - 24 halvings never fails to fit in practice
        notes.append("could not fit the script into %.0fs after 24 passes; "
                     "shipping the shortest form" % slot)

    total_words = sum(words_of(lines[r]) for r in beats)
    if total_words > hard_budget:
        notes.append("word budget %d exceeded (%d) -- report this, it is a bug"
                     % (hard_budget, total_words))
    if effective < budget:
        notes.append("word budget tightened from %d to %d so the film fits its "
                     "%.0fs slot%s" % (budget, effective, slot,
                                       " with the brand bookends" if with_bookends else ""))
    if timeline["totalSec"] < slot * 0.8:
        notes.append("runs %.1fs against a %.0fs slot: the queue item supplied only "
                     "%d words of material. Add a second proof sentence or a 'lines' "
                     "override to fill the slot."
                     % (timeline["totalSec"], slot, sum(words_of(raw[r]) for r in beats)))
    if with_bookends:
        notes.append("carries the brand intro and outro (%.1fs + %.1fs)"
                     % (timeline.get("introSec") or 0.0, timeline.get("outroSec") or 0.0))
    else:
        notes.append("ships without the brand bookends: at %.0fs they would be "
                     "%.0f%% of the film" % (slot, 100.0 * (
                         float(((video_cfg.get("intro") or {}).get("durationSec")) or 3.0)
                         + float(((video_cfg.get("outro") or {}).get("durationSec")) or 3.5)) / max(1.0, slot)))

    templates = choose_templates(beats, job, lines)

    scenes = []  # type: List[Dict[str, Any]]
    for index, role in enumerate(beats):
        row = timeline["scenes"][index]
        scenes.append({
            "id": "s%d" % (index + 1),
            "role": role,
            "line": lines[role],
            "words": row["words"],
            "narrationSec": row["narrationSec"],
            "holdSec": row["holdSec"],
            "template": templates[index]["template"],
            "templateWhy": templates[index]["why"],
            "data": templates[index]["data"],
        })

    return {
        "kind": kind,
        "beats": beats,
        "slotSec": slot,
        "wordBudget": hard_budget,
        "words": total_words,
        "runtimeSec": timeline["totalSec"],
        "withBookends": with_bookends,
        "transitionSec": timeline["transitionSec"],
        "scenes": scenes,
        "notes": notes,
        "fits": total_words <= hard_budget and timeline["totalSec"] <= slot + 1e-6,
    }


# ---------------------------------------------------------------------------
# motion templates -- choice and data
# ---------------------------------------------------------------------------

def template_data(template, role, job, line):
    # type: (str, str, Dict[str, Any], str) -> Optional[Dict[str, Any]]
    """visual.data for a template, or None when this item cannot feed it.

    Returning None is how a template drops out of the running -- a chart with
    one number or a flow with one step is worse than the next choice down.
    """
    override = job.get("visualData") if isinstance(job.get("visualData"), dict) else {}
    if isinstance(override.get(role), dict):
        return dict(override[role])

    topic = clean(job.get("topic")) or clean(job.get("title"))
    one_thing = clean(job.get("oneThing"))
    proof = job.get("proof") if isinstance(job.get("proof"), dict) else {}
    claim = clean(proof.get("claim"))
    source = clean(proof.get("source"))
    eyebrow = BEAT_EYEBROW.get(role, "")
    surface = "brand" if role == "hook" else ("tint" if role in ("problem", "turn") else "light")

    if template == "kinetic-type":
        parts = sentences(line) or [line]
        if len(parts) == 1 and len(parts[0]) > 52:
            words = parts[0].split(" ")
            middle = len(words) // 2
            parts = [" ".join(words[:middle]), " ".join(words[middle:])]
        parts = [p for p in parts if p][:4]
        if not parts:
            return None
        return {"lines": parts, "emphasis": [len(parts) - 1], "eyebrow": eyebrow,
                "surface": surface if surface in ("light", "tint", "brand", "dark") else "light",
                "align": "left", "mark": True}

    if template == "counter":
        basis = claim or line
        figures = numbers_in(claim) or numbers_in(line)
        if not figures:
            return None
        stats = []
        pair = _FROM_TO_RE.search(basis)
        if pair and re.search(r"\d", pair.group(1)) and re.search(r"\d", pair.group(2)):
            # "rose from 61% to 92%" is one metric at two moments, not two
            # metrics. Label them as the moments they are.
            subject = claim_subject(basis) or "Measured"
            stats = [{"value": clean(pair.group(1)), "label": "Before", "caption": subject},
                     {"value": clean(pair.group(2)), "label": "After",
                      "caption": cap_field(source, 40) or subject}]
        else:
            stats = figure_stats(basis, source, 3)
        if not stats:
            return None
        return {"stats": stats, "eyebrow": eyebrow,
                "title": title_from(one_thing or topic, 60),
                "surface": "light", "countUp": True}

    if template == "compare":
        # The two panels must say DIFFERENT things -- a wipe that reveals the
        # same sentence again is an expensive way to show nothing. The title
        # states the principle, the before panel the assumption it challenges,
        # the after panel the evidence.
        pair = _FROM_TO_RE.search(claim)
        if pair and re.search(r"\d", pair.group(1)) and re.search(r"\d", pair.group(2)):
            before_head, after_head = clean(pair.group(1)), clean(pair.group(2))
        else:
            before_head, after_head = "Assumed", "Measured"
        before_body = clean(topic)
        after_body = clean(claim) or clean(one_thing)
        if not after_body or after_body == before_body:
            after_body = clean(one_thing) or clean(claim)
        if not before_body or before_body == after_body:
            before_body = clean(one_thing) if after_body != clean(one_thing) else clean(topic)
        if not before_body or not after_body or before_body == after_body:
            return None
        return {
            "mode": "wipe",
            "before": {"label": "Before", "headline": cap_field(before_head, 18),
                       "body": cap_field(before_body, 110, end_stop=True)},
            "after": {"label": "After", "headline": cap_field(after_head, 18),
                      "body": cap_field(after_body, 110, end_stop=True)},
            "eyebrow": eyebrow, "title": title_from(one_thing or topic, 60),
            "surface": "light", "emphasise": "after",
        }

    if template == "chart-reveal":
        # Only a genuine before/after claim becomes a chart. Three unrelated
        # figures plotted against invented categories is a chart that lies.
        pair = _FROM_TO_RE.search(claim)
        if not pair:
            return None
        values = []
        for raw in (pair.group(1), pair.group(2)):
            digits = re.sub(r"[^0-9.]", "", raw)
            if not digits:
                return None
            try:
                values.append(float(digits))
            except ValueError:
                return None
        return {
            "type": "column",
            "categories": ["Before", "After"],
            "series": [{"name": claim_subject(claim) or "Measured", "values": values}],
            "eyebrow": eyebrow, "title": title_from(topic, 60),
            "valueSuffix": "%" if "%" in claim else "",
            "highlightGap": False, "surface": "light",
        }

    if template == "process-flow":
        parts = [p for p in sentences(one_thing or line) if p]
        if len(parts) < 3:
            parts = [p.strip() for p in re.split(r"[,;]| then ", clean(one_thing or line)) if p.strip()]
        parts = [p for p in parts if len(p) > 3][:5]
        if len(parts) < 3:
            return None
        steps = []
        for part in parts:
            head = part.split(" ")
            steps.append({"label": cap_field(" ".join(head[:2]), 18),
                          "body": cap_field(part, 90, end_stop=True)})
        return {"steps": steps, "orientation": "horizontal", "numbered": True,
                "eyebrow": eyebrow, "title": title_from(topic, 60), "surface": "light"}

    if template == "coverage-map":
        figures = numbers_in(claim)
        regions = []
        for index, value in enumerate(figures[:4]):
            name = label_after(claim, value)
            if not name:
                continue
            digits = re.sub(r"[^0-9]", "", value)
            regions.append({"name": cap_field(name, 20),
                            "count": int(digits) if digits else 1,
                            "x": 0.25 + 0.2 * index, "y": 0.3 + 0.12 * (index % 3)})
        if len(regions) < 2:
            return None
        return {"regions": regions, "seed": 7, "headline": title_from(topic, 60),
                "subhead": cap_field(source, 60), "eyebrow": eyebrow}

    if template == "scene":
        return {"variant": "statement", "eyebrow": eyebrow,
                "title": cap_field(line, 120, end_stop=True),
                "theme": "light"}

    return None


def choose_templates(beats, job, lines):
    # type: (List[str], Dict[str, Any], Dict[str, str]) -> List[Dict[str, Any]]
    """One template per beat: angle-driven, data-feasible, never repeated back to back."""
    angle = clean(job.get("angle")).lower()
    preferred = ANGLE_TEMPLATE.get(angle)
    # The angle lives in the second beat -- the turn, or the problem. That is
    # the beat that carries the argument, so that is where it is honoured.
    angle_beat = 1 if len(beats) > 1 else 0

    chosen = []  # type: List[Dict[str, Any]]
    previous = None  # type: Optional[str]
    for index, role in enumerate(beats):
        order = list(BEAT_TEMPLATES.get(role, DEFAULT_BEAT_TEMPLATES))
        if preferred and index == angle_beat:
            order = [preferred] + [t for t in order if t != preferred]
        picked = None
        data = None
        why = ""
        for candidate in order:
            if candidate == previous:
                continue
            data = template_data(candidate, role, job, lines.get(role, ""))
            if data is not None:
                picked = candidate
                if preferred and index == angle_beat and candidate == preferred:
                    why = "angle '%s' maps to %s" % (angle, candidate)
                elif candidate == (BEAT_TEMPLATES.get(role) or [""])[0]:
                    why = "%s beat -> %s" % (role, candidate)
                else:
                    why = ("%s beat; %s is the first template this item can feed "
                           "without repeating the previous scene" % (role, candidate))
                break
        if picked is None:
            picked = "scene" if previous != "scene" else "kinetic-type"
            data = template_data(picked, role, job, lines.get(role, "")) or {
                "variant": "statement", "title": clean(lines.get(role, ""))}
            why = "%s beat; fell back to %s" % (role, picked)
        chosen.append({"template": picked, "data": data, "why": why})
        previous = picked
    return chosen


# ---------------------------------------------------------------------------
# IR authoring
# ---------------------------------------------------------------------------

def format_canvas(brand, name):
    # type: (Dict[str, Any], str) -> Dict[str, Any]
    formats = ((brand.get("video") or {}).get("formats") or {})
    spec = formats.get(name)
    if not isinstance(spec, dict):
        resolution = (brand.get("video") or {}).get("resolution") or {}
        return {"name": name or "landscape",
                "w": int(resolution.get("w") or 1920), "h": int(resolution.get("h") or 1080),
                "declared": False}
    return {"name": name, "w": int(spec.get("w") or 1920), "h": int(spec.get("h") or 1080),
            "ratio": spec.get("ratio"), "safeMarginPct": spec.get("safeMarginPct"),
            "captionSizePt": spec.get("captionSizePt"), "declared": True}


def build_video_ir(job, script, brand, date_str):
    # type: (Dict[str, Any], Dict[str, Any], Dict[str, Any], str) -> Dict[str, Any]
    fmt = clean(job.get("format")) or ("vertical" if job["kind"] == "reel" else "landscape")
    canvas = format_canvas(brand, fmt)
    title = cap_field(clean(job.get("topic")) or clean(job.get("title")), 70)

    scenes = []
    for scene in script["scenes"]:
        scenes.append({
            "id": scene["id"],
            "role": arc_role(scene["role"], brand),
            "beat": scene["role"],
            "visual": {"kind": "motion", "template": scene["template"],
                       "slide": None, "src": None, "data": scene["data"]},
            "vo": scene["line"],
            "caption": scene["line"],
            "holdSec": scene["holdSec"],
        })

    ir = {
        "$comment": ("Generated by %s on %s. Deterministic: derived from the queue "
                     "item, the brand profile and the beat->template map in "
                     "skills/brand-video/references/motion-templates.md." % (PROG, date_str)),
        "brand": brand.get("id"),
        "kind": "explainer",
        "meta": {
            "title": title,
            "durationTargetSec": script["slotSec"],
            # meta.format is the delivery format. build_video.py currently
            # masters at brand.video.resolution regardless; the runner measures
            # the result and records formatHonoured in packet.json.
            "format": canvas["name"],
            "canvas": {"w": canvas["w"], "h": canvas["h"]},
            "producedBy": PROG,
            "queueItem": job.get("_dayId"),
            "jobId": job.get("jobId"),
        },
        "intro": bool(script["withBookends"]),
        "outro": bool(script["withBookends"]),
        "scenes": scenes,
    }
    if canvas.get("safeMarginPct") is not None:
        ir["meta"]["safeMarginPct"] = canvas["safeMarginPct"]
    if canvas.get("captionSizePt") is not None:
        ir["meta"]["captionSizePt"] = canvas["captionSizePt"]
    return ir


# ---------------------------------------------------------------------------
# deck IR authoring
# ---------------------------------------------------------------------------

PURPOSE_EYEBROWS = {
    "pitch": ["The problem", "How it works", "Proof", "Working with us"],
    "qbr": ["The quarter", "What moved", "Proof", "Next quarter"],
    "readout": ["What we found", "How we measured", "Proof", "What to do next"],
    "capability": ["The problem", "How we work", "Proof", "Working with us"],
}


def deck_content_units(job):
    # type: (Dict[str, Any]) -> List[str]
    """The sentences a deck can be built from, in order, deduplicated."""
    units = []  # type: List[str]

    def add(text):
        for sentence in sentences(text):
            sentence = end_stopped(sentence)
            if sentence and sentence not in units:
                units.append(sentence)

    for point in (job.get("points") or []):
        add(point)
    add(job.get("brief"))
    add(job.get("oneThing"))
    proof = job.get("proof") if isinstance(job.get("proof"), dict) else {}
    add(proof.get("claim"))
    return units


def deck_stats(job):
    # type: (Dict[str, Any]) -> List[Dict[str, str]]
    proof = job.get("proof") if isinstance(job.get("proof"), dict) else {}
    claim = clean(proof.get("claim"))
    source = clean(proof.get("source"))
    explicit = job.get("stats")
    stats = []  # type: List[Dict[str, str]]
    if isinstance(explicit, list):
        for row in explicit[:5]:
            if not isinstance(row, dict):
                continue
            stats.append({"value": cap_field(row.get("value"), CAP["stats.value"]),
                          "label": cap_field(row.get("label"), CAP["stats.label"]),
                          "caption": cap_field(row.get("caption") or source,
                                               CAP["stats.caption"])})
        if len(stats) >= 3:
            return stats[:5]
    stats = []
    for row in figure_stats(claim, source, 4):
        stats.append({"value": cap_field(row["value"], CAP["stats.value"]),
                      "label": cap_field(row["label"], CAP["stats.label"]),
                      "caption": cap_field(row["caption"], CAP["stats.caption"])})
    # The stats archetype declares 3-5. Two numbers are a sentence, not a slide.
    return stats if len(stats) >= 3 else []


#: Slide keys that are machine values, not copy the audience reads.
_NON_COPY_KEYS = ("archetype", "icon", "src", "image", "variant", "mode", "type")


def copy_words(node):
    # type: (Any) -> int
    """Words of actual slide copy, ignoring archetype names and asset keys."""
    if isinstance(node, str):
        return words_of(node)
    if isinstance(node, list):
        return sum(copy_words(item) for item in node)
    if isinstance(node, dict):
        return sum(copy_words(v) for k, v in node.items()
                   if k not in _NON_COPY_KEYS and not str(k).startswith("$"))
    return 0


def build_deck_ir(job, brand, date_str):
    # type: (Dict[str, Any], Dict[str, Any], str) -> Tuple[Dict[str, Any], List[str]]
    """A Deck IR composed from the job's brief, honouring the grammar's structural rules.

    Returns (ir, notes). When the brief does not carry enough material for the
    requested slide count, the deck is shorter and a note says so -- padding a
    client deck with filler is worse than delivering ten honest slides.
    """
    notes = []  # type: List[str]
    purpose = (clean(job.get("purpose")) or "capability").lower()
    eyebrows = PURPOSE_EYEBROWS.get(purpose, PURPOSE_EYEBROWS["capability"])
    title = clean(job.get("title")) or clean(job.get("topic")) or clean(brand.get("name"))
    audience = clean(job.get("audience"))
    want = job.get("slides")
    try:
        want = max(3, int(want))
    except (TypeError, ValueError):
        want = 8

    units = deck_content_units(job)
    stats = deck_stats(job)
    contact = clean(((brand.get("video") or {}).get("outro") or {}).get("contact"))
    cta = clean(job.get("cta")) or "Pick one territory and one metric."

    slides = [{
        "archetype": "cover",
        "title": cap_field(title, CAP["cover.title"]),
        "subtitle": cap_field(clean(job.get("brief")) or clean(job.get("oneThing")) or
                              clean(brand.get("description")), CAP["cover.subtitle"], end_stop=True),
        "meta": cap_field("%s%s%s" % (
            purpose.capitalize(), " for " + audience if audience else "",
            " · " + date_str), CAP["cover.meta"]),
    }]

    body_slots = max(1, want - 2)          # cover and closing are fixed
    cursor = 0
    chapter = 0
    since_break = 0
    last_archetype = "cover"
    repeats = 0
    stats_used = False

    used_titles = set()

    def slide_title(source, fallback):
        # type: (str, str) -> str
        candidate = title_from(source)
        if not candidate or candidate.lower() in used_titles:
            candidate = title_from(fallback)
        suffix = 2
        base = candidate
        while candidate.lower() in used_titles and suffix < 9:
            candidate = title_from("%s, part %d" % (base, suffix))
            suffix += 1
        used_titles.add(candidate.lower())
        return candidate

    agenda_items = [title_from(u, CAP["agenda.item"]) for u in units[:6]]
    agenda_items = [i for i in agenda_items if i]
    if want >= 9 and len(agenda_items) >= 4:
        slides.append({"archetype": "agenda", "eyebrow": "Contents",
                       "title": cap_field("What this deck covers", CAP["title"]),
                       "items": agenda_items})
        body_slots -= 1
        last_archetype, since_break, repeats = "agenda", 1, 0

    while len(slides) - 1 < body_slots and (cursor < len(units) or (stats and not stats_used)):
        eyebrow = cap_field(eyebrows[min(chapter, len(eyebrows) - 1)], CAP["eyebrow"])

        # A section break every chapter, and never more than 8 slides without one.
        if since_break >= 7 and last_archetype != "section-break" and cursor < len(units):
            slides.append({
                "archetype": "section-break",
                "title": cap_field(eyebrows[min(chapter + 1, len(eyebrows) - 1)],
                                   CAP["section.title"]),
                "kicker": cap_field(units[cursor], CAP["section.kicker"], end_stop=True),
            })
            cursor += 1
            chapter += 1
            since_break = 0
            last_archetype, repeats = "section-break", 0
            continue

        remaining = len(units) - cursor
        archetype = None

        if stats and not stats_used and (remaining <= 3 or since_break >= 2):
            archetype = "stats"
        elif remaining >= 4 and last_archetype != "columns":
            archetype = "columns"
        elif remaining >= 4 and last_archetype != "steps":
            archetype = "steps"
        elif remaining >= 1:
            archetype = "title-body"
        else:
            break

        if archetype == last_archetype:
            repeats += 1
            if repeats >= 2:                # maxConsecutiveSameArchetype is 2
                archetype = "title-body" if last_archetype != "title-body" else "stats"
                if archetype == "stats" and (not stats or stats_used):
                    break
                repeats = 0
        else:
            repeats = 0

        if archetype == "stats":
            slides.append({
                "archetype": "stats", "eyebrow": eyebrow,
                "title": slide_title(clean(job.get("oneThing")), "What the numbers show"),
                "intro": cap_field(clean((job.get("proof") or {}).get("source")),
                                   CAP["stats.intro"], end_stop=True),
                "stats": stats[:5],
            })
            stats_used = True
        elif archetype in ("columns", "steps"):
            # The lead unit becomes the claim in the title; the ones after it
            # become the group. Nothing is said twice on one slide.
            head_unit = units[cursor]
            group = units[cursor + 1:cursor + 4]
            entries = []
            for index, unit in enumerate(group, 1):
                label = title_from(unit, CAP["columns.subtitle"] if archetype == "columns"
                                   else CAP["steps.number"] - 4)
                if archetype == "columns":
                    entries.append({"subtitle": label,
                                    "body": cap_field(unit, CAP["columns.body"], end_stop=True)})
                else:
                    entries.append({"number": cap_field("%d. %s" % (index, label),
                                                        CAP["steps.number"]),
                                    "body": cap_field(unit, CAP["steps.body"], end_stop=True)})
            slides.append({"archetype": archetype, "eyebrow": eyebrow,
                           "title": slide_title(head_unit, title),
                           ("columns" if archetype == "columns" else "steps"): entries})
            cursor += 1 + len(group)
        else:
            block = units[cursor:cursor + 3]
            slides.append({"archetype": "title-body", "eyebrow": eyebrow,
                           "title": slide_title(block[0], title),
                           "body": cap_field("\n\n".join(block), CAP["body"])})
            cursor += len(block)

        last_archetype = archetype
        since_break += 1

    slides.append({
        "archetype": "closing",
        "title": cap_field(clean(job.get("closingTitle")) or "Where to start",
                           CAP["closing.title"]),
        "cta": cap_field(cta, CAP["closing.cta"], end_stop=True),
        "contact": cap_field(contact, CAP["closing.contact"]),
    })

    if len(slides) < want:
        notes.append("the brief carried enough material for %d slides, not the %d "
                     "requested; padding a deck with filler is worse than a short "
                     "one. Add 'points' or a longer 'brief' to the queue job."
                     % (len(slides), want))
    if len(slides) < 3:
        notes.append("fewer than the grammar's 3-slide minimum")

    ir = {
        "$comment": "Generated by %s on %s from the queue job's brief." % (PROG, date_str),
        "brand": brand.get("id"),
        "meta": {
            "title": cap_field(title, 70),
            "client": audience or "Internal",
            "date": date_str,
            "author": clean(brand.get("name")),
            "confidentiality": clean(job.get("confidentiality")) or "Confidential",
        },
        "slides": slides,
    }
    return ir, notes


# ---------------------------------------------------------------------------
# building one job
# ---------------------------------------------------------------------------

def build_film_job(job, brand, rules, work_dir, date_str, log, heavy):
    # type: (...) -> Dict[str, Any]
    job_id = job["jobId"]
    started = time.time()
    script = derive_film_script(job, brand, rules)
    ir = build_video_ir(job, script, brand, date_str)

    ir_path = os.path.join(work_dir, "%s.ir.json" % job_id)
    write_json(ir_path, ir)

    out_path = os.path.join(work_dir, "%s.mp4" % job_id)
    cmd = [sys.executable, os.path.join(_HERE, "build_video.py"),
           "--ir", ir_path, "--out", out_path,
           "--brand", str(brand.get("id")),
           "--reuse-intro", "--reuse-outro"]

    artifact = {
        "jobId": job_id, "kind": job["kind"], "path": None,
        "durationSec": None, "format": ir["meta"]["format"], "words": script["words"],
        "validation": "fail", "violations": 0, "buildSeconds": 0.0, "error": None,
        "script": script, "ir": os.path.basename(ir_path),
        "formatHonoured": None, "dimensions": None,
        "reuse": {"intro": "none", "outro": "none"},
        "notes": list(script["notes"]),
    }
    if not script["fits"]:
        artifact["notes"].append(
            "the derived script does not fit its slot cleanly; review the pacing")

    log("[%s] building %s (%d words, ~%.1fs, %s)"
        % (job_id, job["kind"], script["words"], script["runtimeSec"], ir["meta"]["format"]))

    with heavy:
        result = run_cmd(cmd, TIMEOUT_BUILD_VIDEO, "build_video.py (%s)" % job_id)
    artifact["buildSeconds"] = result["seconds"]
    artifact["stdout"] = tail(result["stdout"])
    artifact["stderr"] = tail(result["stderr"])

    for kind in ("intro", "outro"):
        artifact["reuse"][kind] = bookend_state(result["stderr"], kind,
                                                script["withBookends"])

    if result["rc"] != 0 or not os.path.isfile(out_path):
        artifact["error"] = build_error_message("build_video.py", result)
        log("[%s] FAILED after %.1fs: %s" % (job_id, result["seconds"], artifact["error"]))
        return artifact

    artifact["path"] = os.path.basename(out_path)
    probe = ffprobe_stream(out_path)
    artifact["durationSec"] = probe.get("durationSec")
    if probe.get("width") and probe.get("height"):
        artifact["dimensions"] = "%dx%d" % (probe["width"], probe["height"])
        canvas = ir["meta"].get("canvas") or {}
        want = (canvas.get("w"), canvas.get("h"))
        got = (probe["width"], probe["height"])
        artifact["formatHonoured"] = (want == got)
        if not artifact["formatHonoured"]:
            artifact["notes"].append(
                "requested format '%s' (%dx%d) was not honoured: the file is %dx%d. "
                "build_video.py masters at brand.video.resolution and does not yet "
                "read meta.format." % (ir["meta"]["format"], want[0] or 0, want[1] or 0,
                                       got[0], got[1]))

    srt = os.path.splitext(out_path)[0] + ".srt"
    if os.path.isfile(srt):
        artifact["srt"] = os.path.basename(srt)

    log("[%s] built in %.1fs -> %s (%s, %ss)"
        % (job_id, result["seconds"], os.path.basename(out_path),
           artifact["dimensions"] or "?", artifact["durationSec"]))
    return artifact


def build_deck_job(job, brand, work_dir, date_str, log):
    # type: (...) -> Dict[str, Any]
    job_id = job["jobId"]
    ir, notes = build_deck_ir(job, brand, date_str)
    out_path = os.path.join(work_dir, "%s.pptx" % job_id)
    # validate.py attaches <basename>.ir.json as --ir, which is what turns on
    # the grammar's structural rules. Name it so it is found.
    ir_path = os.path.splitext(out_path)[0] + ".ir.json"
    write_json(ir_path, ir)

    artifact = {
        "jobId": job_id, "kind": "deck", "path": None,
        "durationSec": None, "format": "deck", "words": copy_words(ir["slides"]),
        "validation": "fail", "violations": 0, "buildSeconds": 0.0, "error": None,
        "slides": len(ir["slides"]), "ir": os.path.basename(ir_path),
        "archetypes": [s.get("archetype") for s in ir["slides"]],
        "slideTitles": [clean(s.get("title")) for s in ir["slides"]],
        "formatHonoured": True, "dimensions": None,
        "reuse": {"intro": "n/a", "outro": "n/a"},
        "notes": list(notes),
    }

    log("[%s] building deck (%d slides)" % (job_id, len(ir["slides"])))
    cmd = [sys.executable, os.path.join(_HERE, "build_deck.py"),
           "--ir", ir_path, "--out", out_path, "--brand", str(brand.get("id"))]
    result = run_cmd(cmd, TIMEOUT_BUILD_DECK, "build_deck.py (%s)" % job_id)
    artifact["buildSeconds"] = result["seconds"]
    artifact["stdout"] = tail(result["stdout"])
    artifact["stderr"] = tail(result["stderr"])

    if result["rc"] != 0 or not os.path.isfile(out_path):
        artifact["error"] = build_error_message("build_deck.py", result)
        log("[%s] FAILED after %.1fs: %s" % (job_id, result["seconds"], artifact["error"]))
        return artifact

    artifact["path"] = os.path.basename(out_path)
    log("[%s] built in %.1fs -> %s" % (job_id, result["seconds"], os.path.basename(out_path)))
    return artifact


def bookend_state(stderr, kind, expected):
    # type: (str, str, bool) -> str
    if not expected:
        return "none"
    for line in (stderr or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(kind):
            lowered = stripped.lower()
            if "reused" in lowered:
                return "cached"
            if "rendered" in lowered:
                return "rendered"
    return "unknown"


def build_error_message(tool, result):
    # type: (str, Dict[str, Any]) -> str
    if result["timedOut"]:
        return "%s timed out and was killed" % tool
    lines = [l.strip() for l in (result["stderr"] or "").splitlines() if l.strip()]
    detail = lines[-1] if lines else (result["stdout"] or "").strip().splitlines()[-1:] or ["no output"]
    if isinstance(detail, list):
        detail = detail[0]
    return "%s exited %s: %s" % (tool, result["rc"], detail[:400])


def tail(text, limit=4000):
    # type: (str, int) -> str
    text = text or ""
    return text if len(text) <= limit else "...\n" + text[-limit:]


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def validate_artifact(artifact, work_dir, brand, log):
    # type: (Dict[str, Any], str, Dict[str, Any], Log) -> None
    if not artifact.get("path"):
        artifact["validation"] = "skipped"
        return
    target = os.path.join(work_dir, artifact["path"])
    cmd = [sys.executable, os.path.join(_HERE, "validate.py"), target,
           "--brand", str(brand.get("id")), "--format", "json"]
    result = run_cmd(cmd, TIMEOUT_VALIDATE, "validate.py (%s)" % artifact["jobId"])
    report = None
    try:
        report = json.loads(result["stdout"])
    except ValueError:
        report = None
    artifact["validationReport"] = report
    if report is None:
        artifact["validation"] = "error"
        artifact["violations"] = 0
        artifact["validationError"] = build_error_message("validate.py", result)
        log("[%s] validation could not be read: %s"
            % (artifact["jobId"], artifact["validationError"]))
        return
    counts = report.get("counts") or {}
    errors = int(counts.get("error") or 0)
    artifact["violations"] = errors
    artifact["warnings"] = int(counts.get("warn") or 0)
    artifact["validation"] = "pass" if errors == 0 else "fail"
    log("[%s] validation %s (%d error, %d warn)"
        % (artifact["jobId"], artifact["validation"], errors, artifact.get("warnings") or 0))


# ---------------------------------------------------------------------------
# contact sheets
# ---------------------------------------------------------------------------

def _pil():
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: F401
        return Image, ImageDraw, ImageFont
    except ImportError:
        return None, None, None


def _label_font(brand, size):
    Image, ImageDraw, ImageFont = _pil()
    if ImageFont is None:
        return None
    candidate = os.path.join(brand.get("_dir") or "", "assets", "fonts", "Poppins-Medium.ttf")
    if os.path.isfile(candidate):
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def tile_contact_sheet(frames, labels, out_png, brand):
    # type: (List[str], List[str], str, Dict[str, Any]) -> bool
    Image, ImageDraw, ImageFont = _pil()
    if Image is None or not frames:
        return False
    try:
        thumbs = []
        for path in frames:
            image = Image.open(path).convert("RGB")
            ratio = CONTACT_TILE_W / float(image.width)
            thumbs.append(image.resize((CONTACT_TILE_W, max(1, int(image.height * ratio)))))
        tile_h = max(t.height for t in thumbs)
        cols = min(CONTACT_SHEET_COLS, len(thumbs))
        rows = (len(thumbs) + cols - 1) // cols
        pad, band = 12, 30
        sheet = Image.new("RGB",
                          (cols * CONTACT_TILE_W + (cols + 1) * pad,
                           rows * (tile_h + band) + (rows + 1) * pad),
                          (18, 20, 28))
        draw = ImageDraw.Draw(sheet)
        font = _label_font(brand, 18)
        for index, thumb in enumerate(thumbs):
            col, row = index % cols, index // cols
            x = pad + col * (CONTACT_TILE_W + pad)
            y = pad + row * (tile_h + band + pad)
            sheet.paste(thumb, (x, y + (tile_h - thumb.height) // 2))
            text = labels[index] if index < len(labels) else ""
            if font is not None:
                draw.text((x + 4, y + tile_h + 6), text, fill=(220, 226, 240), font=font)
            else:
                draw.text((x + 4, y + tile_h + 6), text, fill=(220, 226, 240))
        sheet.save(out_png)
        return True
    except Exception:
        return False


def contact_sheet_video(mp4_path, out_png, duration, brand, log):
    # type: (str, str, Optional[float], Dict[str, Any], Log) -> bool
    ffmpeg = which("ffmpeg")
    if not ffmpeg or not duration:
        return False
    tmp = tempfile.mkdtemp(prefix="brand-daily-sheet-")
    try:
        frames, labels = [], []
        for index in range(CONTACT_SHEET_FRAMES):
            when = duration * (index + 0.5) / CONTACT_SHEET_FRAMES
            frame = os.path.join(tmp, "f%02d.png" % index)
            res = run_cmd([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                           "-ss", "%.3f" % when, "-i", mp4_path, "-frames:v", "1",
                           "-vf", "scale=%d:-1:flags=area" % (CONTACT_TILE_W * 2), frame],
                          TIMEOUT_FFMPEG_FRAME, "ffmpeg (frame)")
            if res["rc"] == 0 and os.path.isfile(frame):
                frames.append(frame)
                labels.append("%02d:%05.2f" % (int(when // 60), when % 60))
        if not frames:
            log("  contact sheet: ffmpeg extracted no frames from %s"
                % os.path.basename(mp4_path))
            return False
        return tile_contact_sheet(frames, labels, out_png, brand)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def contact_sheet_deck(pptx_path, out_png, brand, log):
    # type: (str, str, Dict[str, Any], Log) -> bool
    soffice = which("soffice") or which("libreoffice")
    if not soffice:
        log("  contact sheet: LibreOffice not on PATH; skipping the deck sheet")
        return False
    tmp = tempfile.mkdtemp(prefix="brand-daily-deck-")
    try:
        profile = os.path.join(tmp, "lo-profile")
        res = run_cmd([soffice, "-env:UserInstallation=file://%s" % profile,
                       "--headless", "--norestore", "--convert-to", "pdf",
                       "--outdir", tmp, pptx_path], TIMEOUT_SOFFICE, "soffice")
        pdfs = [f for f in os.listdir(tmp) if f.lower().endswith(".pdf")]
        if res["rc"] != 0 or not pdfs:
            log("  contact sheet: LibreOffice produced no PDF for %s"
                % os.path.basename(pptx_path))
            return False
        pdf = os.path.join(tmp, pdfs[0])
        pdftoppm = which("pdftoppm")
        if not pdftoppm:
            log("  contact sheet: pdftoppm not on PATH; skipping the deck sheet")
            return False
        prefix = os.path.join(tmp, "page")
        res = run_cmd([pdftoppm, "-r", "45", "-png", pdf, prefix],
                      TIMEOUT_PDFTOPPM, "pdftoppm")
        pages = sorted(os.path.join(tmp, f) for f in os.listdir(tmp)
                       if f.startswith("page-") and f.lower().endswith(".png"))
        if not pages:
            return False
        if len(pages) <= CONTACT_SHEET_FRAMES:
            picked = list(range(len(pages)))
        else:
            step = (len(pages) - 1) / float(CONTACT_SHEET_FRAMES - 1)
            picked = [int(round(i * step)) for i in range(CONTACT_SHEET_FRAMES)]
        frames = [pages[i] for i in picked]
        labels = ["slide %d" % (i + 1) for i in picked]
        return tile_contact_sheet(frames, labels, out_png, brand)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# packet writing
# ---------------------------------------------------------------------------

def write_json(path, payload):
    # type: (str, Any) -> None
    with open(path, "w") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def write_text(path, text):
    # type: (str, str) -> None
    with open(path, "w") as handle:
        handle.write(text)


def concept_markdown(day, artifacts, brand, date_str, fallback, notes):
    # type: (...) -> str
    primary = None
    for job in day["jobs"]:
        if clean(job.get("topic")) or clean(job.get("oneThing")):
            primary = job
            break
    primary = primary or (day["jobs"][0] if day["jobs"] else {})
    proof = primary.get("proof") if isinstance(primary.get("proof"), dict) else {}

    out = ["# Concept - %s" % date_str, ""]
    out.append("**Brand** %s  " % clean(brand.get("name")))
    out.append("**Queue item** `%s`%s  " % (day["id"],
               " (evergreen fallback)" if fallback else ""))
    out.append("**Jobs** %s" % ", ".join(
        "%s (%s)" % (a["jobId"], a["kind"]) for a in artifacts) or "none")
    out.append("")
    out.append("## Premise")
    out.append("")
    out.append(clean(primary.get("topic")) or clean(primary.get("title")) or "-")
    out.append("")
    out.append("## Angle")
    out.append("")
    angle = clean(primary.get("angle")) or "-"
    has_film = any(a.get("script") for a in artifacts)
    if has_film and angle in ANGLE_TEMPLATE:
        out.append("`%s` -- maps to the `%s` motion template" % (angle, ANGLE_TEMPLATE[angle]))
    else:
        out.append("`%s`" % angle)
    out.append("")
    out.append("## The one thing")
    out.append("")
    out.append(clean(primary.get("oneThing")) or "-")
    out.append("")
    out.append("## The proof")
    out.append("")
    if proof:
        out.append("%s  " % clean(proof.get("claim")))
        out.append("*Source: %s*" % clean(proof.get("source")) or "-")
    else:
        out.append("-")
    out.append("")
    out.append("## Why these templates")
    out.append("")
    for artifact in artifacts:
        script = artifact.get("script")
        out.append("### %s (%s)" % (artifact["jobId"], artifact["kind"]))
        out.append("")
        if not script:
            out.append("%s slide(s). Archetypes come from the brief's material and "
                       "the grammar's structural rules: cover first, closing last, a "
                       "section break at least every eight slides, never three of the "
                       "same archetype in a row." % (artifact.get("slides") or "?"))
            out.append("")
            titles = artifact.get("slideTitles") or []
            for index, archetype in enumerate(artifact.get("archetypes") or []):
                out.append("%2d. `%s` -- %s" % (index + 1, archetype,
                                                titles[index] if index < len(titles) else ""))
            out.append("")
            for note in artifact.get("notes") or []:
                out.append("> %s" % note)
            out.append("")
            continue
        for scene in script["scenes"]:
            out.append("- **%s** -> `%s` -- %s" % (scene["role"], scene["template"],
                                                   scene["templateWhy"]))
        out.append("")
        out.append("Motion varies across every beat: %s."
                   % " -> ".join(s["template"] for s in script["scenes"]))
        out.append("")
        for note in artifact.get("notes") or []:
            out.append("> %s" % note)
        out.append("")
    if notes:
        out.append("## Selection notes")
        out.append("")
        for note in notes:
            out.append("- %s" % note)
        out.append("")
    return "\n".join(out) + "\n"


def script_markdown(artifacts, brand, date_str):
    # type: (List[Dict[str, Any]], Dict[str, Any], str) -> str
    wpm = ((brand.get("video") or {}).get("voiceover") or {}).get("rateWpm") or 165
    out = ["# Scripts - %s" % date_str, "",
           "Runtime estimates are at the brand's %s wpm, plus %.1fs of silence "
           "after each line, less one crossfade per join." % (wpm, VO_TAIL_SEC), ""]
    for artifact in artifacts:
        script = artifact.get("script")
        out.append("## %s -- %s" % (artifact["jobId"], artifact["kind"]))
        out.append("")
        if not script:
            out.append("%s slides, %s words of slide copy. Full text in `%s`."
                       % (artifact.get("slides", "?"), artifact.get("words"),
                          artifact.get("ir")))
            out.append("")
            titles = artifact.get("slideTitles") or []
            for index, archetype in enumerate(artifact.get("archetypes") or []):
                out.append("%2d. **%s** -- %s" % (index + 1, archetype,
                                                  titles[index] if index < len(titles) else ""))
            out.append("")
            continue
        out.append("**%d words** against a budget of %d · **~%.1fs** against a "
                   "%.0fs slot · format `%s`%s"
                   % (script["words"], script["wordBudget"], script["runtimeSec"],
                      script["slotSec"], artifact.get("format"),
                      " · brand bookends included" if script["withBookends"] else ""))
        out.append("")
        for scene in script["scenes"]:
            out.append("**%s** (`%s`, %d words, ~%.1fs)"
                       % (scene["role"], scene["template"], scene["words"],
                          scene["holdSec"]))
            out.append("")
            out.append("> %s" % scene["line"])
            out.append("")
        if artifact.get("durationSec"):
            out.append("Delivered at **%.2fs**%s."
                       % (artifact["durationSec"],
                          " (%s)" % artifact["dimensions"] if artifact.get("dimensions") else ""))
            out.append("")
    return "\n".join(out) + "\n"


def review_markdown(day, artifacts, brand, date_str, fallback):
    # type: (...) -> str
    out = ["# Review - %s" % date_str, "",
           "%s · queue item `%s`%s" % (clean(brand.get("name")), day["id"],
                                            " (evergreen fallback)" if fallback else ""), ""]
    out.append("## What was made")
    out.append("")
    for artifact in artifacts:
        if artifact.get("path"):
            bits = ["`%s`" % artifact["path"]]
            if artifact.get("durationSec"):
                bits.append("%.1fs" % artifact["durationSec"])
            if artifact.get("dimensions"):
                bits.append(artifact["dimensions"])
            if artifact.get("slides"):
                bits.append("%d slides" % artifact["slides"])
            bits.append("validation **%s**" % artifact["validation"])
            out.append("- **%s** -- %s" % (artifact["jobId"], " · ".join(bits)))
            if artifact.get("contactSheet"):
                out.append("  - contact sheet: `%s`" % artifact["contactSheet"])
        else:
            out.append("- **%s** -- NOT DELIVERED: %s"
                       % (artifact["jobId"], artifact.get("error") or "unknown"))
    out.append("")
    out.append("Skim the contact sheets first. Then answer these.")
    out.append("")
    out.append("## Questions")
    out.append("")
    kinds = set(a["kind"] for a in artifacts)
    questions = []  # type: List[str]
    if kinds & set(FILM_KINDS):
        questions += ["Anything wrong with the opening line?",
                      "Pacing -- too fast, too slow, or right?",
                      "Visual -- does each beat's motion carry its idea?"]
    if kinds & set(DECK_KINDS):
        questions += ["Does the cover claim the right thing?",
                      "Any slide that should not be there, or one that is missing?"]
    questions += ["Is the proof stated the way you would state it?",
                  "Is the closing line the next step you want?",
                  "Anything off-brand?"]
    questions = questions[:6]
    for index, question in enumerate(questions, 1):
        out.append("%d. %s" % (index, question))
    out.append("")
    out.append("## Your notes")
    out.append("")
    out.append("<!-- type below this line; /brand-review reads it and learns from it -->")
    out.append("")
    out.append("```")
    for index in range(1, len(questions) + 1):
        out.append("%d." % index)
    out.append("")
    out.append("anything else:")
    out.append("```")
    out.append("")
    return "\n".join(out) + "\n"


def summary_line(date_str, brand_id, artifacts, elapsed, reuse, status):
    # type: (...) -> str
    parts = []
    violations = 0
    for artifact in artifacts:
        if not artifact.get("path"):
            parts.append("%s FAILED" % artifact["jobId"])
            continue
        detail = artifact["validation"]
        if artifact.get("durationSec"):
            parts.append("%s %s (%.1fs)" % (artifact["jobId"],
                                            "ok" if detail == "pass" else detail,
                                            artifact["durationSec"]))
        else:
            parts.append("%s %s (%s slides)" % (artifact["jobId"],
                                                "ok" if detail == "pass" else detail,
                                                artifact.get("slides", "?")))
        violations += int(artifact.get("violations") or 0)
    if not parts:
        parts.append("nothing built")
    minutes, seconds = divmod(int(round(elapsed)), 60)
    reused = [k for k, v in sorted(reuse.items()) if v == "cached"]
    reuse_text = ("%s reused" % "+".join(reused)) if reused else "no bookend reuse"
    return "%s %s: %s, %d violation%s, %dm%02ds, %s [%s]" % (
        date_str, brand_id, ", ".join(parts), violations,
        "" if violations == 1 else "s", minutes, seconds, reuse_text, status)


# ---------------------------------------------------------------------------
# queue bookkeeping
# ---------------------------------------------------------------------------

def mark_done(path, day, date_str, log):
    # type: (str, Dict[str, Any], str, Log) -> None
    """Flip the item to done and append to history. Rewritten atomically."""
    try:
        queue = load_queue(path)
    except DailyError as exc:
        log("  could not reopen the queue to mark it done: %s" % exc)
        return
    touched = False
    for entry in queue.get("queue") or []:
        if isinstance(entry, dict) and str(entry.get("id")) == day["id"]:
            entry["status"] = "done"
            entry["builtOn"] = date_str
            touched = True
            break
    history = queue.setdefault("history", [])
    topics = []
    for job in day["jobs"]:
        topic = clean(job.get("topic")) or clean(job.get("title"))
        if topic and topic not in topics:
            topics.append(topic)
    history.append({"date": date_str, "id": day["id"],
                    "topic": topics[0] if topics else day["id"],
                    "topics": topics,
                    "jobs": [j["jobId"] for j in day["jobs"]],
                    "source": day["source"]})
    tmp = path + ".tmp"
    try:
        write_json(tmp, queue)
        os.replace(tmp, path)
    except OSError as exc:
        log("  could not write the queue: %s" % exc)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return
    log("  queue updated: %s%s, history now %d entr%s"
        % (day["id"], " marked done" if touched else " (evergreen, nothing to mark)",
           len(history), "y" if len(history) == 1 else "ies"))


def move_into_place(work_dir, final_dir, force):
    # type: (str, str, bool) -> str
    """One move, so a crashed run never leaves a half-written packet."""
    parent = os.path.dirname(final_dir)
    if not os.path.isdir(parent):
        os.makedirs(parent)
    retired = None
    if os.path.isdir(final_dir):
        if not force:
            stamp = datetime.datetime.now().strftime("%H%M%S")
            final_dir = "%s-%s" % (final_dir, stamp)
        else:
            retired = final_dir + ".superseded-%d" % os.getpid()
            os.rename(final_dir, retired)
    shutil.move(work_dir, final_dir)
    if retired:
        shutil.rmtree(retired, ignore_errors=True)
    return final_dir


# ---------------------------------------------------------------------------
# dry run
# ---------------------------------------------------------------------------

def print_dry_run(day, brand, rules, date_str, fallback, notes):
    # type: (...) -> int
    print("%s - DRY RUN (nothing built, nothing written)" % PROG)
    print("")
    print("brand        %s (%s)" % (brand.get("name"), brand.get("id")))
    print("date         %s" % date_str)
    print("queue item   %s%s" % (day["id"], "  [evergreen fallback]" if fallback else ""))
    print("jobs         %s" % ", ".join("%s:%s" % (j["jobId"], j["kind"]) for j in day["jobs"]))
    for note in notes:
        print("note         %s" % note)
    print("")
    for job in day["jobs"]:
        print("-" * 72)
        if job["kind"] == "deck":
            ir, deck_notes = build_deck_ir(job, brand, date_str)
            print("%s  deck  %d slide(s)" % (job["jobId"], len(ir["slides"])))
            print("")
            for index, slide in enumerate(ir["slides"], 1):
                print("  %2d  %-14s %s" % (index, slide.get("archetype"),
                                           clean(slide.get("title"))[:56]))
            for note in deck_notes:
                print("")
                print("  note: %s" % note)
            print("")
            continue

        script = derive_film_script(job, brand, rules)
        ir = build_video_ir(job, script, brand, date_str)
        canvas = ir["meta"]["canvas"]
        print("%s  %s  %s %dx%d  slot %.0fs" % (job["jobId"], job["kind"],
                                                ir["meta"]["format"], canvas["w"],
                                                canvas["h"], script["slotSec"]))
        print("  %d words / budget %d      estimated runtime %.1fs%s"
              % (script["words"], script["wordBudget"], script["runtimeSec"],
                 "   FITS" if script["fits"] else "   DOES NOT FIT"))
        print("")
        clock = 0.0
        if script["withBookends"]:
            intro = float(((brand.get("video") or {}).get("intro") or {}).get("durationSec") or 3.0)
            print("  %6.2f  %-14s %-14s %s" % (clock, "intro", "(brand)", "cached bookend"))
            clock += intro - script["transitionSec"]
        for scene in script["scenes"]:
            print("  %6.2f  %-14s %-14s %s"
                  % (clock, scene["role"], scene["template"],
                     scene["line"][:60] + ("..." if len(scene["line"]) > 60 else "")))
            print("  %6s  %-14s %d words, %.1fs" % ("", "", scene["words"], scene["holdSec"]))
            clock += scene["holdSec"] - script["transitionSec"]
        if script["withBookends"]:
            print("  %6.2f  %-14s %-14s %s" % (clock, "outro", "(brand)", "cached bookend"))
        print("")
        for note in script["notes"]:
            print("  note: %s" % note)
        print("")
    print("-" * 72)
    print("Nothing was built. Drop --dry-run to produce the packet in "
          "brands/%s/daily/%s/." % (brand.get("id"), date_str))
    return EXIT_OK


# ---------------------------------------------------------------------------
# failure packet
# ---------------------------------------------------------------------------

def write_failure_packet(final_dir, brand_id, date_str, reason, detail, log, started, force):
    # type: (...) -> None
    """The file the human reads at breakfast when there is nothing to watch."""
    work_dir = tempfile.mkdtemp(prefix="brand-daily-fail-")
    try:
        packet = {
            "date": date_str, "brand": brand_id, "queueItem": None, "fallback": False,
            "concept": {"angle": None, "premise": None, "oneThing": None, "proof": None},
            "artifacts": {}, "reuse": {"intro": "none", "outro": "none"},
            "status": "failed", "builtAt": datetime.datetime.now().replace(
                microsecond=0).isoformat(),
            "totalSeconds": round(time.time() - started, 2),
            "failure": {"reason": reason, "detail": detail},
        }
        write_json(os.path.join(work_dir, "packet.json"), packet)
        write_text(os.path.join(work_dir, "review.md"),
                   "# Review - %s\n\n**Nothing was produced.**\n\n## Why\n\n%s\n\n"
                   "```\n%s\n```\n\n## What to do\n\nFix the cause above, then run\n\n"
                   "```\nscripts/daily_run.py --brand %s --date %s\n```\n"
                   % (date_str, reason, detail or "(no further detail)",
                      brand_id or "<brand>", date_str))
        write_text(os.path.join(work_dir, "concept.md"),
                   "# Concept - %s\n\nNo subject was selected. %s\n" % (date_str, reason))
        write_text(os.path.join(work_dir, "run.log"), log.text())
        placed = move_into_place(work_dir, final_dir, force)
        log("failure packet written to %s" % placed)
    except Exception as exc:  # pragma: no cover - last-ditch
        shutil.rmtree(work_dir, ignore_errors=True)
        print("%s: could not even write the failure packet: %s" % (PROG, exc),
              file=sys.stderr)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog=PROG, description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--brand", default=None,
                        help="brand id (default: the registry's single house brand)")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    parser.add_argument("--only", default="all",
                        help="which jobs to build: all (default -- a day may hold "
                             "any mix of kinds), 'both' for reel+video only, or a "
                             "comma-separated list of kinds (reel,video,deck) or job ids")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the chosen item, the scripts, the template "
                             "choices and the timeline without building anything")
    parser.add_argument("--force", action="store_true",
                        help="rebuild a done queue item, ignore noRepeatWithinDays, "
                             "and overwrite an existing packet for the date")
    parser.add_argument("--queue-item", default=None,
                        help="build this queue (or evergreen) item id instead of the next pending one")
    parser.add_argument("--out", default=None,
                        help="packet directory (default brands/<id>/daily/<date>)")
    parser.add_argument("--jobs", type=int, default=DEFAULT_JOBS,
                        help="worker pool size across all jobs (default %d)" % DEFAULT_JOBS)
    parser.add_argument("--video-jobs", type=int, default=DEFAULT_VIDEO_JOBS,
                        help="concurrent film builds; each spawns Chrome and ffmpeg "
                             "(default %d)" % DEFAULT_VIDEO_JOBS)
    parser.add_argument("--quiet", action="store_true",
                        help="log to run.log only; keep stderr clean for cron")
    return parser.parse_args(argv)


def wanted_kinds(only):
    # type: (str) -> Optional[List[str]]
    value = (only or "all").strip().lower()
    if value in ("all", "*", ""):
        return None
    if value == "both":
        return list(FILM_KINDS)
    return [v.strip() for v in value.split(",") if v.strip()]


def main(argv=None):
    # type: (Optional[Sequence[str]]) -> int
    started = time.time()
    args = parse_args(list(argv) if argv is not None else sys.argv[1:])
    log = Log(None, quiet=args.quiet)

    run_date = parse_date(args.date) if args.date else datetime.date.today()
    date_str = run_date.isoformat()

    # ---- brand -------------------------------------------------------------
    try:
        brand = resolve_house_brand(args.brand)
    except (DailyError, brandlib.BrandNotFound, ValueError) as exc:
        log("brand could not be resolved: %s" % exc)
        final = args.out or os.path.join(
            brandlib.plugin_root(), "brands", str(args.brand or "unknown"), "daily", date_str)
        write_failure_packet(final, args.brand, date_str,
                             "the brand profile is missing or unresolvable",
                             str(exc), log, started, args.force)
        print(summary_line(date_str, args.brand or "?", [], time.time() - started,
                           {}, "failed"))
        return EXIT_INTERNAL

    brand_id = str(brand.get("id"))
    final_dir = args.out or os.path.join(brand.get("_dir"), "daily", date_str)

    # ---- queue -------------------------------------------------------------
    qpath = queue_path(brand)
    try:
        queue = load_queue(qpath)
    except DailyError as exc:
        log(str(exc))
        write_failure_packet(final_dir, brand_id, date_str,
                             "the content queue could not be read", str(exc),
                             log, started, args.force)
        print(summary_line(date_str, brand_id, [], time.time() - started, {}, "failed"))
        return EXIT_NOTHING

    rules = queue.get("rules") or {}
    day, fallback, notes = select_day(queue, args, run_date)
    if day is None:
        reason = "nothing to build: no pending queue item and no usable evergreen item"
        log(reason)
        for note in notes:
            log("  %s" % note)
        write_failure_packet(final_dir, brand_id, date_str, reason,
                             "\n".join(notes) or "the queue's 'queue' and 'evergreen' "
                             "arrays are both empty or exhausted.\nAdd items to %s"
                             % qpath, log, started, args.force)
        print(summary_line(date_str, brand_id, [], time.time() - started, {}, "nothing-to-build"))
        return EXIT_NOTHING

    keep = wanted_kinds(args.only)
    jobs = day["jobs"]
    partial_selection = False
    if keep is not None:
        jobs = [j for j in jobs if j["kind"] in keep or j["jobId"] in keep]
        partial_selection = len(jobs) < len(day["jobs"])
        if not jobs:
            reason = ("queue item %s has no job matching --only %s (it has: %s)"
                      % (day["id"], args.only,
                         ", ".join("%s:%s" % (j["jobId"], j["kind"]) for j in day["jobs"])))
            log(reason)
            write_failure_packet(final_dir, brand_id, date_str, reason, "",
                                 log, started, args.force)
            print(summary_line(date_str, brand_id, [], time.time() - started, {},
                               "nothing-to-build"))
            return EXIT_NOTHING
    for job in jobs:
        job["_dayId"] = day["id"]

    if args.dry_run:
        day_view = dict(day)
        day_view["jobs"] = jobs
        return print_dry_run(day_view, brand, rules, date_str, fallback, notes)

    # ---- work directory + run.log -----------------------------------------
    work_dir = tempfile.mkdtemp(prefix="brand-daily-%s-" % date_str)
    log.close()
    log = Log(os.path.join(work_dir, "run.log"), quiet=args.quiet)
    log("%s starting: brand=%s date=%s item=%s jobs=%s"
        % (PROG, brand_id, date_str, day["id"],
           ",".join("%s:%s" % (j["jobId"], j["kind"]) for j in jobs)))
    for note in notes:
        log("  %s" % note)
    if fallback:
        log("  this is an EVERGREEN fallback, not a queued subject")

    artifacts = []  # type: List[Dict[str, Any]]
    try:
        pool = max(1, int(args.jobs))
        heavy_cap = max(1, min(pool, int(args.video_jobs)))
        heavy = threading.Semaphore(heavy_cap)
        log("  worker pool %d, film builds capped at %d concurrent" % (pool, heavy_cap))

        def run_job(job):
            try:
                if job["kind"] == "deck":
                    return build_deck_job(job, brand, work_dir, date_str, log)
                return build_film_job(job, brand, rules, work_dir, date_str, log, heavy)
            except Exception as exc:  # one job's crash never sinks the day
                log("[%s] crashed: %s" % (job["jobId"], exc))
                log(traceback.format_exc())
                return {"jobId": job["jobId"], "kind": job["kind"], "path": None,
                        "durationSec": None, "format": clean(job.get("format")) or "?",
                        "words": 0, "validation": "skipped", "violations": 0,
                        "buildSeconds": 0.0,
                        "error": "%s: %s" % (exc.__class__.__name__, exc),
                        "notes": ["the runner itself failed on this job; see run.log"]}

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(pool, max(1, len(jobs)))) as ex:
            futures = [(job, ex.submit(run_job, job)) for job in jobs]
            for job, future in futures:
                artifacts.append(future.result())

        # ---- validate + contact sheets (cheap, serial, predictable order) --
        for artifact in artifacts:
            if not artifact.get("path"):
                continue
            validate_artifact(artifact, work_dir, brand, log)
            sheet = os.path.join(work_dir, "%s.contact.png" % artifact["jobId"])
            target = os.path.join(work_dir, artifact["path"])
            ok = False
            if artifact["kind"] == "deck":
                ok = contact_sheet_deck(target, sheet, brand, log)
            else:
                ok = contact_sheet_video(target, sheet, artifact.get("durationSec"),
                                         brand, log)
            artifact["contactSheet"] = os.path.basename(sheet) if ok else None
            if not ok:
                artifact.setdefault("notes", []).append(
                    "no contact sheet could be produced for this artifact")
            else:
                log("[%s] contact sheet: %s" % (artifact["jobId"],
                                                os.path.basename(sheet)))

        # ---- packet --------------------------------------------------------
        delivered = [a for a in artifacts if a.get("path")]
        failed_validation = [a for a in delivered if a["validation"] != "pass"]
        status = "awaiting-review" if delivered else "failed"

        reuse = {"intro": "none", "outro": "none"}
        for artifact in artifacts:
            for kind in ("intro", "outro"):
                state = (artifact.get("reuse") or {}).get(kind)
                if state in ("cached", "rendered"):
                    if reuse[kind] == "none" or state == "rendered":
                        reuse[kind] = state

        primary = jobs[0] if jobs else {}
        for job in jobs:
            if clean(job.get("topic")) or clean(job.get("oneThing")):
                primary = job
                break

        manifest_artifacts = {}
        for artifact in artifacts:
            row = {
                "path": artifact.get("path"),
                "durationSec": artifact.get("durationSec"),
                "format": artifact.get("format"),
                "words": artifact.get("words"),
                "validation": artifact.get("validation"),
                "violations": int(artifact.get("violations") or 0),
                "buildSeconds": artifact.get("buildSeconds"),
                "error": artifact.get("error"),
                "kind": artifact.get("kind"),
                "ir": artifact.get("ir"),
                "srt": artifact.get("srt"),
                "contactSheet": artifact.get("contactSheet"),
                "dimensions": artifact.get("dimensions"),
                "formatHonoured": artifact.get("formatHonoured"),
                "warnings": artifact.get("warnings"),
                "slides": artifact.get("slides"),
                "reuse": artifact.get("reuse"),
                "notes": artifact.get("notes") or [],
            }
            manifest_artifacts[artifact["jobId"]] = row

        packet = {
            "date": date_str,
            "brand": brand_id,
            "queueItem": day.get("raw"),
            "fallback": bool(fallback),
            "concept": {
                "angle": clean(primary.get("angle")) or None,
                "premise": clean(primary.get("topic")) or clean(primary.get("title")) or None,
                "oneThing": clean(primary.get("oneThing")) or None,
                "proof": primary.get("proof") if isinstance(primary.get("proof"), dict) else None,
            },
            "artifacts": manifest_artifacts,
            "reuse": reuse,
            "status": status,
            "builtAt": datetime.datetime.now().replace(microsecond=0).isoformat(),
            "totalSeconds": round(time.time() - started, 2),
            "selectionNotes": notes,
        }

        write_json(os.path.join(work_dir, "packet.json"), packet)
        write_text(os.path.join(work_dir, "concept.md"),
                   concept_markdown(day, artifacts, brand, date_str, fallback, notes))
        write_text(os.path.join(work_dir, "script.md"),
                   script_markdown(artifacts, brand, date_str))
        write_text(os.path.join(work_dir, "review.md"),
                   review_markdown(day, artifacts, brand, date_str, fallback))
        write_json(os.path.join(work_dir, "validation.json"), {
            "date": date_str, "brand": brand_id,
            "results": dict((a["jobId"], {
                "artifact": a.get("path"),
                "validation": a.get("validation"),
                "violations": int(a.get("violations") or 0),
                "warnings": a.get("warnings"),
                "error": a.get("validationError"),
                # The child validator's own JSON, verbatim. Contract C belongs
                # to validate_video.py / validate_deck.py; nothing here reshapes
                # its verdict.
                "report": a.get("validationReport"),
            }) for a in artifacts),
        })
        for artifact in artifacts:
            artifact.pop("validationReport", None)
            manifest_artifacts[artifact["jobId"]]["validationReportFile"] = "validation.json"

        log("packet assembled: %d artifact(s) delivered, %d failed validation"
            % (len(delivered), len(failed_validation)))
        write_text(os.path.join(work_dir, "run.log"), log.text())
        placed = move_into_place(work_dir, final_dir, args.force)
        work_dir = None
        log("packet at %s" % placed)

        if delivered and partial_selection:
            # --only built a subset of the day. Consuming the item here would
            # silently lose the jobs nobody asked for, so it stays pending.
            log("--only %s built %d of the day's %d jobs; %s stays pending so the "
                "rest still get made" % (args.only, len(jobs), len(day["jobs"]), day["id"]))
        elif delivered:
            mark_done(qpath, day, date_str, log)
        else:
            log("nothing delivered; the queue item stays pending for tomorrow")

        # run.log again, now that the queue lines exist. Detach the append
        # handle first: it still points into the moved directory, and writing
        # through it after a truncating rewrite would pad the file with NULs.
        log.close()
        try:
            write_text(os.path.join(placed, "run.log"), log.text())
        except IOError:
            pass

        elapsed = time.time() - started
        if not delivered:
            code, label = EXIT_INTERNAL, "failed"
        elif failed_validation or len(delivered) != len(artifacts):
            code, label = EXIT_PARTIAL, "partial"
        else:
            code, label = EXIT_OK, "ok"
        print(summary_line(date_str, brand_id, artifacts, elapsed, reuse, label))
        return code

    except Exception as exc:
        log("internal failure: %s" % exc)
        log(traceback.format_exc())
        if work_dir and os.path.isdir(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)
        write_failure_packet(final_dir, brand_id, date_str,
                             "the runner failed before it could assemble a packet",
                             "%s: %s\n\n%s" % (exc.__class__.__name__, exc,
                                               traceback.format_exc()),
                             log, started, args.force)
        print(summary_line(date_str, brand_id, [], time.time() - started, {}, "failed"))
        return EXIT_INTERNAL
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
