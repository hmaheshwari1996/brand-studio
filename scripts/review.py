#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""brand-studio morning review -- the half of the daily loop that learns.

The nightly runner (daily_run.py) writes a packet of artifacts and stops. This
script is what happens the next morning: the human watches what was made, gives
notes, and every note is converted into something durable so the SAME NOTE IS
NEVER GIVEN TWICE. Then it measures whether that is actually happening.

    review.py show    [--brand ID] [--date YYYY-MM-DD]
    review.py note    --text "..." [--target ID|KIND|all|both] [--category C]
    review.py apply   [--date ...] [--rebuild] [--force]
    review.py metrics [--brand ID] [--days 30]
    review.py accept  [--date ...]

THE PACKET (written by daily_run.py, consumed here -- never invented here)

    brands/<id>/daily/<YYYY-MM-DD>/
        packet.json  concept.md  script.md  review.md  validation.json  run.log
        <jobId>.ir.json  <jobId>.mp4|.pptx  <jobId>.srt  <jobId>.contact.png

    packet.json artifacts is a MAP keyed by job id -- "reel", "video", "deck-1",
    "deck-2", ... Each entry carries its own "kind" (reel|video|deck). A day is
    not two artifacts; it is N artifacts of mixed kind, and may contain no video
    at all. Everything here iterates that map generically.

THE THREE TIERS (identical to the plugin's learn protocol)

    1  brands/<id>/LEARNED.md      always, for every note
    2  brands/<id>/rules.local.json  when the note is mechanically checkable
    3  brands/<id>/brand.json      when the note is a durable brand FACT

Both validators read tier 2 rules, but not the same kinds. The text kinds --
forbid_text, require_text, regex -- are enforced on decks and on film. The colour
and font-size kinds are deck-only; validate_video.py reports them as info,
because colour on film is checked against sampled frames and a rendered film
exposes no type sizes.

A proposed rule carries the `formats` it applies to, inferred from what the note
says and, failing that, from the artifact it was filed against. A note about a
reel therefore does not silently govern decks. Spanning several kinds, or naming
none, leaves `formats` off so the rule applies everywhere -- broad and loud beats
narrow and silently disabled.

Exit codes: 0 fine, 1 could not run (no packet, bad JSON, missing brand),
2 a rebuild produced an artifact that fails validation.
"""

from __future__ import absolute_import

import argparse
import datetime
import io
import json
import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from lib import brandlib as bl  # noqa: E402

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_VIOLATIONS = 2

DEFAULT_BRAND = "example"

CATEGORIES = ("copy", "pacing", "visual", "structure", "audio", "caption", "other")

#: Text-bearing keys per IR contract. Used for deterministic find/replace edits.
VIDEO_TEXT_KEYS = ("vo", "caption", "title", "subtitle", "eyebrow", "text",
                   "label", "body", "headline", "value", "note")
DECK_TEXT_KEYS = ("title", "subtitle", "eyebrow", "body", "items", "meta",
                  "label", "caption", "quote", "name", "role", "stat", "value",
                  "text", "headline")

SPARK = u"▁▂▃▄▅▆▇█"

_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}

_STOPWORDS = set((
    "the a an and or of to in on for is are was were be been being it its this "
    "that with at as by from we you i our your not but if then than so very "
    "just really quite too much more most less do does did can could should "
    "would will shall may might have has had here there what when where why how"
).split())


# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------

def _out(s):
    sys.stdout.write(s + "\n")


def _err(s):
    sys.stderr.write("review: %s\n" % s)


class ReviewError(Exception):
    """Anything that should stop the command with exit 1 and a clear message."""


def today_iso():
    return datetime.date.today().isoformat()


def now_iso():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def parse_date(s):
    try:
        return datetime.datetime.strptime(str(s), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ReviewError("bad date %r -- expected YYYY-MM-DD" % (s,))


def read_json(path, default=None):
    if not os.path.isfile(path):
        return default
    try:
        with io.open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as exc:
        raise ReviewError("%s is not valid JSON: %s" % (path, exc))


def write_json(path, payload):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        fh.write(_u(json.dumps(payload, indent=2, ensure_ascii=False)) + u"\n")
    os.rename(tmp, path)


def _u(s):
    if isinstance(s, bytes):
        return s.decode("utf-8", "replace")
    return s if isinstance(s, str) else str(s)


def find_python():
    """The interpreter used for build_* and validate.py subprocesses."""
    env = os.environ.get("BRAND_STUDIO_PYTHON")
    if env and os.path.isfile(env) and os.access(env, os.X_OK):
        return env
    venv = os.path.join(os.path.expanduser("~"), ".cache", "brand-studio",
                        "venv", "bin", "python")
    if os.path.isfile(venv) and os.access(venv, os.X_OK):
        return venv
    return sys.executable or "python3"


def natkey(s):
    """Sort 'deck-2' before 'deck-10'."""
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", str(s))]


def clip(s, n):
    s = _u(s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + u"…"


def _wrap(s, width):
    """Soft wrap that never truncates -- the tier explanation must stay readable."""
    words = _u(s or "").split()
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w) if cur else w
    if cur:
        lines.append(cur)
    return lines or [""]


def _to_number(tok):
    tok = str(tok).strip().lower()
    if tok in _NUM_WORDS:
        return _NUM_WORDS[tok]
    try:
        return float(tok) if "." in tok else int(tok)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# packet layer -- the contract, consumed generically
# ---------------------------------------------------------------------------

def daily_dir(brand_id):
    return os.path.join(bl.brand_dir(brand_id), "daily")


def packet_dir(brand_id, date):
    return os.path.join(daily_dir(brand_id), str(date))


def list_packet_dates(brand_id):
    """Every date directory that carries a packet.json, oldest first."""
    root = daily_dir(brand_id)
    if not os.path.isdir(root):
        return []
    out = []
    for name in os.listdir(root):
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", name):
            continue
        if os.path.isfile(os.path.join(root, name, "packet.json")):
            out.append(name)
    return sorted(out)


def resolve_date(brand_id, date, prefer_unreviewed=True):
    """The date to act on: the one asked for, else the latest sensible packet."""
    if date:
        parse_date(date)
        if not os.path.isfile(os.path.join(packet_dir(brand_id, date), "packet.json")):
            raise ReviewError(
                "no packet at %s. Run the nightly build first, or pass a date "
                "that exists: %s" % (packet_dir(brand_id, date),
                                     ", ".join(list_packet_dates(brand_id)) or "(none)"))
        return date
    dates = list_packet_dates(brand_id)
    if not dates:
        raise ReviewError(
            "no packets under %s yet. The morning review needs a nightly run "
            "first (brand-daily / daily_run.py)." % daily_dir(brand_id))
    if prefer_unreviewed:
        for d in reversed(dates):
            pkt = read_json(os.path.join(packet_dir(brand_id, d), "packet.json"), {}) or {}
            if str(pkt.get("status") or "") != "reviewed":
                return d
    return dates[-1]


def load_packet(brand_id, date):
    path = os.path.join(packet_dir(brand_id, date), "packet.json")
    pkt = read_json(path)
    if not isinstance(pkt, dict):
        raise ReviewError("packet.json missing or malformed at %s" % path)
    return pkt


def infer_kind(job_id, art):
    """kind is authoritative; fall back to the job id, then the file extension."""
    k = str((art or {}).get("kind") or "").strip().lower()
    if k in ("reel", "video", "deck"):
        return k
    jid = str(job_id or "").lower()
    for cand in ("reel", "video", "deck"):
        if jid == cand or jid.startswith(cand + "-") or jid.startswith(cand + "_"):
            return cand
    ext = os.path.splitext(str((art or {}).get("path") or ""))[1].lower()
    if ext in (".pptx", ".potx"):
        return "deck"
    if ext in (".mp4", ".mov", ".m4v"):
        return "video"
    return "other"


def norm_validation(art):
    """(label, errors, warns) from whatever shape the runner wrote."""
    v = (art or {}).get("validation")
    errors = warns = None
    label = ""
    if isinstance(v, dict):
        counts = v.get("counts") or {}
        errors = counts.get("error")
        warns = counts.get("warn")
        if v.get("pass") is True:
            label = "PASS"
        elif v.get("pass") is False:
            label = "FAIL"
    elif isinstance(v, bool):
        label = "PASS" if v else "FAIL"
    elif isinstance(v, str):
        s = v.strip().lower()
        label = {"pass": "PASS", "ok": "PASS", "clean": "PASS",
                 "fail": "FAIL", "error": "FAIL"}.get(s, v.strip().upper()[:6])

    viol = (art or {}).get("violations")
    if errors is None:
        if isinstance(viol, int):
            errors = viol
        elif isinstance(viol, list):
            errors = len([x for x in viol
                          if str((x or {}).get("severity", "error")) == "error"])
            warns = len([x for x in viol
                         if str((x or {}).get("severity", "")) == "warn"])
    if not label:
        if (art or {}).get("error"):
            label = "BUILD-FAIL"
        elif errors is not None:
            label = "PASS" if not errors else "FAIL"
        else:
            label = "?"
    return (label, errors, warns)


def artifact_rows(packet):
    """Normalised artifact records, ordered reel -> video -> deck -> other."""
    arts = packet.get("artifacts")
    rows = []
    if isinstance(arts, dict):
        items = list(arts.items())
    elif isinstance(arts, list):
        items = [(str((a or {}).get("id") or i), a) for i, a in enumerate(arts)]
    else:
        items = []
    for jid, art in items:
        if not isinstance(art, dict):
            art = {"path": str(art)}
        rows.append({"id": str(jid), "kind": infer_kind(jid, art), "art": art})
    order = {"reel": 0, "video": 1, "deck": 2, "other": 3}
    rows.sort(key=lambda r: (order.get(r["kind"], 9), natkey(r["id"])))
    return rows


def abspath_in(pdir, path):
    if not path:
        return ""
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(pdir, path))


def artifact_paths(pdir, row):
    """Every path this artifact owns, resolved against the packet dir."""
    art = row["art"]
    jid = row["id"]
    main = abspath_in(pdir, art.get("path") or "")
    stem = os.path.splitext(main)[0] if main else os.path.join(pdir, jid)
    ir = abspath_in(pdir, art.get("ir") or "")
    if not ir or not os.path.isfile(ir):
        for cand in (os.path.join(pdir, jid + ".ir.json"), stem + ".ir.json"):
            if os.path.isfile(cand):
                ir = cand
                break
        else:
            ir = os.path.join(pdir, jid + ".ir.json")
    srt = abspath_in(pdir, art.get("srt") or "") or (stem + ".srt")
    contact = abspath_in(pdir, art.get("contact") or "") or (stem + ".contact.png")
    return {"main": main, "ir": ir, "srt": srt, "contact": contact,
            "deck": abspath_in(pdir, art.get("deck") or "")}


# ---------------------------------------------------------------------------
# notes store
# ---------------------------------------------------------------------------

def notes_path(brand_id, date):
    return os.path.join(packet_dir(brand_id, date), "notes.json")


def load_notes(brand_id, date):
    doc = read_json(notes_path(brand_id, date))
    if not isinstance(doc, dict):
        doc = {}
    doc.setdefault("date", str(date))
    doc.setdefault("brand", brand_id)
    doc.setdefault("notes", [])
    doc.setdefault("accepted", False)
    doc.setdefault("acceptedAt", None)
    if not isinstance(doc["notes"], list):
        doc["notes"] = []
    return doc


def save_notes(brand_id, date, doc):
    write_json(notes_path(brand_id, date), doc)


# ---------------------------------------------------------------------------
# CLASSIFIER -- deterministic, keyword-weighted. No model call.
# ---------------------------------------------------------------------------

#: (category, weight, pattern). Weights let a specific signal beat a vague one:
#: "the caption is too long" is a caption note, not a pacing note.
_CAT_PATTERNS = [
    ("caption", 3, r"\bcaptions?\b|\bsubtitles?\b|\bsrt\b|\bcues?\b|\bburn(?:ed|t)?[- ]?in\b"),
    ("caption", 2, r"\bline length\b|\bon[- ]screen text\b"),
    ("audio", 3, r"\bvoice ?over\b|\bnarrat(?:ion|or|ed)\b|\bmusic\b|\baudio\b|\bsound\b"),
    ("audio", 2, r"\bloud(?:ness)?\b|\bvolume\b|\bducking\b|\bmix\b|\bpronounc\w*\b|\bwpm\b|\bvo\b"),
    ("pacing", 3, r"\bpac(?:e|ing)\b|\btoo (?:fast|slow|quick|rushed)\b|\bdrags?\b|\bdragging\b"),
    ("pacing", 2, r"\bhold(?:s|ing)?\b|\blingers?\b|\bruntime\b|\bdurations?\b|\btempo\b|\bbreathe?\b"),
    ("pacing", 1, r"\btoo (?:long|short)\b|\btiming\b|\bsnappy\b|\brushed?\b|\bseconds?\b|\bsecs?\b"),
    ("visual", 3, r"\bcolou?rs?\b|\btemplates?\b|\blayouts?\b|\blogos?\b|\bfonts?\b|\btypefaces?\b"),
    ("visual", 2, r"\bbackgrounds?\b|\bimages?\b|\bicons?\b|\banimations?\b|\bmotion\b|"
                  r"\btransitions?\b|\bvisuals?\b|\bcharts?\b|\bgradients?\b|\bcontrast\b|"
                  r"\balignment\b|\bspacing\b|#[0-9a-f]{6}\b"),
    ("structure", 3, r"\bstructure\b|\bthe arc\b|\bbeat order\b|\breorder\b|\bsequence\b"),
    ("structure", 2, r"\blead(?:s)? with\b|\bshould (?:open|start|lead|end|close)\b|"
                     r"\b(?:always|must)\s+(?:end|open|start|close|lead)\b|"
                     r"\bmove the\b|\bfirst (?:scene|slide|beat)\b|\blast (?:scene|slide|beat)\b|"
                     r"\bbeats?\b|\bswap the order\b|\bsplit (?:it|the|this)\b|\btoo dense\b"),
    ("copy", 3, r"\bwords?\b|\bwording\b|\bphrase\b|\bscript\b|\bcopy\b|\btone\b"),
    ("copy", 2, r"\breplace\b|\brename\b|\brewrite\b|\bre-?word\b|\bswap\b"),
    ("copy", 2, r"\bsays?\b|\bsaying\b|\bsentence\b|\bjargon\b|\bcall it\b|\bheadline\b|"
                r"\bclaim\b|\bnever use\b|\bnever say\b"),
    ("copy", 1, r"\bline\b|\btitles?\b|\btext\b|\bsalesy\b|\bpunchy\b"),
]
_CAT_RX = [(c, w, re.compile(p, re.IGNORECASE)) for c, w, p in _CAT_PATTERNS]

#: Tie-break order when two categories score the same. More specific first.
_CAT_PRIORITY = ("caption", "audio", "visual", "structure", "pacing", "copy", "other")


def classify(text):
    """Return (category, {category: score}). Deterministic keyword scoring."""
    scores = dict((c, 0) for c in CATEGORIES)
    for cat, weight, rx in _CAT_RX:
        hits = len(rx.findall(text or ""))
        if hits:
            scores[cat] += weight * min(hits, 3)
    best = max(scores.values())
    if best <= 0:
        return ("other", scores)
    winners = [c for c in _CAT_PRIORITY if scores.get(c, 0) == best]
    return (winners[0], scores)


# ---------------------------------------------------------------------------
# MECHANISABILITY -- can this note become a rule the validator enforces?
# ---------------------------------------------------------------------------

_BAN_RX = re.compile(
    r"\b(?:never|do not|don'?t|dont|stop|avoid|no longer|drop the|kill the|"
    r"cut the|lose the|ban)\b", re.IGNORECASE)
_QUOTED_RX = re.compile(u"[\"'“‘]([^\"'”’]{1,60})[\"'”’]")
_TRAILING_JUNK = re.compile(
    r"\b(?:anymore|again|ever|any ?more|at all|in the script|in the copy|"
    r"in captions?|please)\b.*$", re.IGNORECASE)


def _clean_term(term):
    term = _u(term or "").strip()
    term = _TRAILING_JUNK.sub("", term)
    term = term.strip().strip(u".,;:!?…\"'“”‘’ ")
    # "the word solutions" -> "solutions"
    term = re.sub(r"^(?:the|a|an|any|that)\s+", "", term, flags=re.IGNORECASE).strip()
    term = re.sub(r"^(?:words?|phrases?|terms?)\s+", "", term, flags=re.IGNORECASE).strip()
    return term


def _scope_from(text):
    t = (text or "").lower()
    if re.search(r"\bhooks?\b|\bopening line\b|\bfirst line\b|\btitles?\b|"
                 r"\bheadlines?\b|\bheadings?\b|\bopener\b", t):
        return "title"
    if re.search(r"\beyebrows?\b|\bkickers?\b", t):
        return "eyebrow"
    if re.search(r"\bbody\b|\bparagraphs?\b|\bbullets?\b", t):
        return "body"
    return "any"


def _formats_from(text, kinds=None):
    """Which artifacts a note is about -> a rules.local.json `formats` list.

    Two sources, in order of trust:

    1. What the note SAYS. "on reels", "in the deck" is the author scoping the
       correction out loud, and it wins.
    2. What the note is ATTACHED TO. A note filed against tonight's reel is a
       reel note even when the sentence never says so, and this is the reliable
       signal -- it comes from the packet, not from prose.

    Returns [] when the note spans more than one kind, or names none. Empty
    means "applies everywhere", which is the correct default: a rule that is
    wrongly narrowed is silently disabled, and that is the worse failure.
    """
    t = (text or "").lower()
    if re.search(r"\breels?\b|\bstor(?:y|ies)\b|\bshorts?\b|\bvertical\b|\b9:16\b", t):
        return ["vertical"]
    if re.search(r"\bsquares?\b|\b1:1\b|\bin-?feed\b", t):
        return ["square"]
    if re.search(r"\blandscapes?\b|\b16:9\b|\bin-?room\b", t):
        return ["landscape"]
    if re.search(r"\bdecks?\b|\bslides?\b|\bpresentations?\b|\bpptx?\b", t):
        return ["deck"]
    if re.search(r"\bvideos?\b|\bfilms?\b|\bexplainers?\b", t):
        return ["video"]

    mapped = set()
    for kind in (kinds or []):
        name = str(kind).strip().lower()
        if name == "reel":
            mapped.add("vertical")
        elif name == "deck":
            mapped.add("deck")
        elif name == "video":
            mapped.add("video")
    return sorted(mapped) if len(mapped) == 1 else []


def _slug(term):
    s = re.sub(r"[^A-Za-z0-9]+", "_", _u(term)).strip("_").upper()
    return (s or "RULE")[:32]


def _detect_rule(text, date):
    """Propose a rules.local.json rule for ``text``, or None.

    Only the seven kinds validate_deck.py implements are ever produced:
    forbid_text, require_text, forbid_color, min_font_size, max_font_size,
    forbid_font_size, regex. A note whose value cannot be extracted with
    certainty returns None -- guessing a rule is worse than not having one.
    """
    t = _u(text or "")
    low = t.lower()
    scope = _scope_from(t)
    # --- word-count limits -> regex --------------------------------------
    m = re.search(r"\b(under|below|less than|fewer than|shorter than|"
                  r"at most|no more than|max(?:imum)?(?: of)?|keep(?: it)? to|"
                  r"cap(?:ped)?(?: it)? at|within)\s+"
                  r"(\d+|[a-z]+)\s+words?\b", low)
    if m:
        n = _to_number(m.group(2))
        if n and n >= 1:
            strict = m.group(1) in ("under", "below", "less than", "fewer than",
                                    "shorter than", "within")
            violates_at = int(n) if strict else int(n) + 1
            repeat = max(1, violates_at - 1)
            allowed = violates_at - 1
            return {
                "id": "LOCAL.%s_MAX_%d_WORDS" % (
                    "TITLE" if scope == "title" else scope.upper(), allowed),
                "kind": "regex",
                "scope": scope,
                "value": r"^\W*(?:\S+\s+){%d,}\S+" % repeat,
                "severity": "warn",
                "rule": "%s copy runs to at most %d words." % (
                    scope.capitalize() if scope != "any" else "This", allowed),
                "fix": "Cut to %d words or fewer. The regex fires at %d words "
                       "and up." % (allowed, violates_at),
                "added": date,
                "source": "morning review %s" % date,
            }

    # --- forbidden colour -------------------------------------------------
    hexes = re.findall(r"#[0-9a-fA-F]{6}\b", t)
    if hexes and _BAN_RX.search(t):
        vals = sorted(set(bl.normalize_hex(h) for h in hexes))
        return {
            "id": "LOCAL.NO_%s" % _slug(vals[0].lstrip("#")),
            "kind": "forbid_color",
            "scope": scope,
            "value": vals if len(vals) > 1 else vals[0],
            "severity": "error",
            "rule": "%s is not used in this brand's artifacts." % ", ".join(vals),
            "fix": "Recolour to an approved brand token.",
            "added": date,
            "source": "morning review %s" % date,
        }

    # --- font sizes -------------------------------------------------------
    m = re.search(r"\b(?:never|not|no)\s+(?:smaller|less)\s+than\s+([\d.]+)\s*"
                  r"(?:pt|point)", low) or \
        re.search(r"\bminimum\s+(?:of\s+)?([\d.]+)\s*(?:pt|point)", low)
    if m:
        v = _to_number(m.group(1))
        if v:
            return {"id": "LOCAL.MIN_%s_PT" % _slug(str(v)), "kind": "min_font_size",
                    "scope": scope, "value": v, "severity": "warn",
                    "rule": "%s text never goes below %gpt." % (scope, v),
                    "fix": "Cut the copy rather than shrinking past the floor.",
                    "added": date, "source": "morning review %s" % date}
    m = re.search(r"\b(?:no (?:bigger|larger)|never (?:bigger|larger)|at most|"
                  r"max(?:imum)?|cap(?:ped)?(?: it)? at)\s*(?:than\s+)?"
                  r"([\d.]+)\s*(?:pt|point)", low)
    if m:
        v = _to_number(m.group(1))
        if v:
            return {"id": "LOCAL.MAX_%s_PT" % _slug(str(v)), "kind": "max_font_size",
                    "scope": scope, "value": v, "severity": "warn",
                    "rule": "%s text stays at or below %gpt." % (scope, v),
                    "fix": "Lower the type scale in brand.json rather than "
                           "fighting it per artifact.",
                    "added": date, "source": "morning review %s" % date}
    m = re.search(r"\b(?:never use|don'?t use|stop using|no)\s+([\d.]+)\s*"
                  r"(?:pt|point)", low)
    if m:
        v = _to_number(m.group(1))
        if v:
            return {"id": "LOCAL.NO_%s_PT" % _slug(str(v)), "kind": "forbid_font_size",
                    "scope": scope, "value": v, "severity": "warn",
                    "rule": "%gpt is not a step of this brand's type scale." % v,
                    "fix": "Pick the nearest role in type.deckScalePt.",
                    "added": date, "source": "morning review %s" % date}

    # --- required text ----------------------------------------------------
    if re.search(r"\balways\b|\bevery (?:video|reel|deck|film)\b|\bmust (?:end|close|"
                 r"include|carry|say|mention)\b", low):
        q = _QUOTED_RX.search(t)
        term = _clean_term(q.group(1)) if q else ""
        if not term:
            m = re.search(r"\b(?:end (?:on|with)|close (?:on|with)|include|mention|"
                          r"carry|say)\s+(?:the\s+)?([\w .'\-]{2,50})", low)
            if m:
                cand = _clean_term(m.group(1))
                # A pronoun-ish target ("the website") is only usable when the
                # brand supplies the literal. detect_rule cannot see the brand,
                # so it is resolved later by _resolve_placeholder().
                term = cand
        if term:
            return {
                "id": "LOCAL.REQUIRE_%s" % _slug(term),
                "kind": "require_text",
                "scope": "any",
                "value": term,
                "severity": "warn",
                "rule": "Every artifact for this brand carries %r." % term,
                "fix": "Add %r to the closing beat." % term,
                "added": date,
                "source": "morning review %s" % date,
                "_placeholder": term,
            }

    # --- forbidden text ---------------------------------------------------
    if _BAN_RX.search(t) and re.search(
            r"\b(?:say|says|saying|use|using|used|word|words|phrase|phrases|"
            r"term|terms|write|writing|call it)\b", low):
        q = _QUOTED_RX.search(t)
        term = _clean_term(q.group(1)) if q else ""
        if not term:
            m = re.search(r"\b(?:words?|phrases?|terms?)\s+(?:is\s+|are\s+)?"
                          r"([\w'\-]+(?:\s+[\w'\-]+){0,3})", low)
            if m:
                term = _clean_term(m.group(1))
        if not term:
            m = re.search(r"\b(?:say|saying|use|using|write|call it)\s+"
                          r"(?:the\s+)?(?:words?\s+|phrases?\s+|terms?\s+)?"
                          r"([\w'\-]+(?:\s+[\w'\-]+){0,2})", low)
            if m:
                term = _clean_term(m.group(1))
        # Guard against capturing the ban verb itself or an empty husk.
        if term and term.lower() not in ("the", "it", "that", "this", "word",
                                         "words", "phrase", "term", "say", "use"):
            return {
                "id": "LOCAL.NO_%s" % _slug(term),
                "kind": "forbid_text",
                "scope": scope,
                "value": term,
                "severity": "warn",
                "rule": "%r is not used in this brand's copy." % term,
                "fix": "Rewrite the line without %r -- say what the team "
                       "actually does." % term,
                "added": date,
                "source": "morning review %s" % date,
            }

    return None


def detect_rule(text, date, kinds=None):
    """``_detect_rule`` plus the `formats` scoping the note implies.

    Kept as a wrapper rather than threading `formats` through every proposal
    inside _detect_rule: the matcher has seven return sites and each one would
    have to remember to stamp the field. One place that cannot be forgotten
    beats seven that can.
    """
    rule = _detect_rule(text, date)
    if not rule:
        return rule
    formats = _formats_from(text, kinds)
    if formats:
        rule["formats"] = list(formats)
    return rule


def _resolve_placeholder(rule, brand):
    """Turn 'the website' into the brand's literal contact line, or drop it."""
    if not rule or "_placeholder" not in rule:
        return rule
    term = str(rule.pop("_placeholder", "")).strip().lower()
    aliases = {"website", "the website", "our website", "url", "the url",
               "site", "the site", "contact", "the contact", "contact line",
               "the contact line", "web address"}
    if term in aliases:
        contact = (((brand.get("video") or {}).get("outro") or {}).get("contact") or "")
        if not contact:
            return None
        rule["value"] = contact
        rule["id"] = "LOCAL.REQUIRE_%s" % _slug(contact)
        rule["rule"] = "Every artifact for this brand closes on %s." % contact
        rule["fix"] = ("Set meta.contact in the IR, or keep the brand outro, so "
                       "%s appears." % contact)
    elif len(term.split()) > 4 or term in ("a", "the"):
        return None
    return rule


# ---------------------------------------------------------------------------
# BRAND FACTS -- tier 3. A fact about what the brand IS, not a preference.
# ---------------------------------------------------------------------------

def detect_brand_fact(text):
    """Propose a brand.json (or queue) edit, or None.

    Returns {"file","path","value","label"} where "path" is a dotted key. Only
    a handful of fields are auto-editable, and every one of them needs an
    explicit value in the note -- a vague complaint is never a brand fact.
    """
    t = _u(text or "")
    low = t.lower()

    m = re.search(r"\b(?:contact(?: line)?|website|url|end card|outro)\b[^.]*?"
                  r"\b(?:is now|should be|changed? to|use|reads?)\s+"
                  r"([\w\-]+(?:\.[\w\-]+)+)", low)
    if m:
        return {"file": "brand", "path": "video.outro.contact", "value": m.group(1),
                "label": "the closing contact line"}

    m = re.search(r"\b(intro|outro)\b[^.]*?\b(?:should be|should run|to|at|is now)\s+"
                  r"([\d.]+)\s*(?:s\b|secs?\b|seconds?\b)", low)
    if m:
        v = _to_number(m.group(2))
        if v:
            return {"file": "brand", "path": "video.%s.durationSec" % m.group(1),
                    "value": float(v), "label": "the %s length" % m.group(1)}

    m = re.search(r"\bcaptions?\b[^.]*?\b([\d.]+)\s*(?:pt|point)", low)
    if m:
        v = _to_number(m.group(1))
        if v:
            return {"file": "brand", "path": "video.captions.sizePt", "value": float(v),
                    "label": "the caption size"}

    m = re.search(r"\bcaptions?\b[^.]*?\b(?:keep(?:ing)?|max(?:imum)?|no more than|"
                  r"at most|limit(?:ed)? to|to)\s+(?:them\s+)?(?:to\s+)?"
                  r"(\d+|one|two|three|four)\s+lines?\b", low)
    if m:
        v = _to_number(m.group(1))
        if v:
            return {"file": "brand", "path": "video.captions.maxLines", "value": int(v),
                    "label": "the caption line limit"}

    m = re.search(r"\bcaptions?\b[^.]*?\b(?:max(?:imum)?|no more than|at most|"
                  r"limit(?:ed)? to|under)\s+(\d+)\s*(?:chars?|characters?)", low)
    if m:
        v = _to_number(m.group(1))
        if v:
            return {"file": "brand", "path": "video.captions.maxCharsPerLine",
                    "value": int(v), "label": "the caption line length"}

    m = re.search(r"\b([\d.]+)\s*(?:wpm\b|words per minute\b)", low)
    if m:
        v = _to_number(m.group(1))
        if v:
            return {"file": "brand", "path": "video.voiceover.rateWpm", "value": float(v),
                    "label": "the narration rate"}

    m = re.search(r"\bhold\b[^.]*?\b(?:at least|minimum(?: of)?|min|no less than)\s*"
                  r"([\d.]+)\s*(?:s\b|secs?\b|seconds?\b)", low)
    if m:
        v = _to_number(m.group(1))
        if v:
            return {"file": "brand", "path": "video.slideHoldSec.min", "value": float(v),
                    "label": "the minimum scene hold"}

    if re.search(r"\b(?:no|drop the|remove the|kill the|lose the|without)\s+music\b", low):
        return {"file": "brand", "path": "video.music.enabled", "value": False,
                "label": "background music"}
    if re.search(r"\b(?:add|bring back|turn on)\s+(?:the\s+)?music\b", low):
        return {"file": "brand", "path": "video.music.enabled", "value": True,
                "label": "background music"}

    m = re.search(r"\b(reels?|videos?)\b[^.]*?\b(?:should (?:be|run)|target|to)\s+"
                  r"([\d.]+)\s*(?:s\b|secs?\b|seconds?\b)", low)
    if m:
        v = _to_number(m.group(2))
        if v:
            kind = "reel" if m.group(1).startswith("reel") else "video"
            return {"file": "queue", "path": "defaults.%s.durationSec" % kind,
                    "value": float(v), "label": "the %s runtime target" % kind}

    return None


def dotted_set(doc, path, value):
    """Set a dotted key, creating dicts. Returns the previous value."""
    parts = str(path).split(".")
    node = doc
    for p in parts[:-1]:
        if not isinstance(node.get(p), dict):
            node[p] = {}
        node = node[p]
    prev = node.get(parts[-1])
    node[parts[-1]] = value
    return prev


# ---------------------------------------------------------------------------
# TARGET RESOLUTION -- job id | kind | all | both | reel | video | deck
# ---------------------------------------------------------------------------

def resolve_target(spec, rows):
    """(job_ids, canonical_label). A deck note never lands on a video."""
    raw = _u(spec or "all").strip().lower()
    ids = [r["id"] for r in rows]
    by_id = dict((r["id"].lower(), r["id"]) for r in rows)
    kinds = {}
    for r in rows:
        kinds.setdefault(r["kind"], []).append(r["id"])

    selected = []
    tokens = [t.strip() for t in re.split(r"[,\s]+", raw) if t.strip()]
    for tok in tokens:
        if tok in ("all", "*", "everything"):
            selected.extend(ids)
        elif tok == "both":
            selected.extend(kinds.get("reel", []) + kinds.get("video", []))
        elif tok in kinds:
            selected.extend(kinds[tok])
        elif tok in by_id:
            selected.append(by_id[tok])
        elif tok in ("reel", "video", "deck"):
            selected.extend([])      # a valid kind with nothing that day
        else:
            raise ReviewError(
                "unknown target %r. Use a job id (%s), a kind (%s), 'both' or "
                "'all'." % (tok, ", ".join(ids) or "none",
                            ", ".join(sorted(kinds.keys())) or "none"))

    seen = set()
    out = [i for i in selected if not (i in seen or seen.add(i))]
    if not out:
        raise ReviewError(
            "target %r matched no artifact in this packet. Available: %s"
            % (spec, ", ".join("%s (%s)" % (r["id"], r["kind"]) for r in rows) or "none"))
    label = raw if raw in ("all", "both") or raw in kinds else ", ".join(out)
    return (out, label)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------

def _fmt_size(row):
    art = row["art"]
    if art.get("durationSec") is not None:
        try:
            return "%.1fs" % float(art["durationSec"])
        except (TypeError, ValueError):
            pass
    for key in ("slides", "slideCount"):
        if art.get(key) is not None:
            return "%s slides" % art[key]
    if art.get("words") is not None:
        return "%s words" % art["words"]
    return "-"


def _fmt_target(row, queue_defaults):
    art = row["art"]
    for key in ("targetSec", "durationTargetSec", "targetDurationSec"):
        if art.get(key) is not None:
            try:
                return "%gs" % float(art[key])
            except (TypeError, ValueError):
                pass
    d = (queue_defaults.get(row["kind"]) or {}).get("durationSec")
    if d is not None:
        try:
            return "%gs" % float(d)
        except (TypeError, ValueError):
            pass
    return "-"


def cmd_show(args):
    brand_id = args.brand or DEFAULT_BRAND
    date = resolve_date(brand_id, args.date)
    pdir = packet_dir(brand_id, date)
    pkt = load_packet(brand_id, date)
    rows = artifact_rows(pkt)
    queue = read_json(os.path.join(bl.brand_dir(brand_id), "content-queue.json"), {}) or {}
    defaults = queue.get("defaults") or {}

    item = pkt.get("queueItem") or {}
    concept = pkt.get("concept") or {}
    status = str(pkt.get("status") or "?")

    _out(u"brand-studio · morning review · %s · %s%s"
         % (brand_id, date, "  [FALLBACK topic]" if pkt.get("fallback") else ""))
    topic = item.get("topic") or concept.get("premise") or "-"
    _out("  topic    %s   (angle: %s)" % (clip(topic, 62),
                                          concept.get("angle") or item.get("angle") or "-"))
    one = concept.get("oneThing") or item.get("oneThing")
    if one:
        _out("  oneThing %s" % clip(one, 70))
    proof = concept.get("proof") or item.get("proof")
    if isinstance(proof, dict):
        proof = proof.get("claim")
    if proof:
        _out("  proof    %s" % clip(proof, 70))

    reuse = pkt.get("reuse") or {}
    reused = [k for k in ("intro", "outro") if reuse.get(k)]
    _out("  built    %d artifact(s)%s · bookends %s · status %s"
         % (len(rows),
            (" in %s" % _dur(pkt.get("totalSeconds"))) if pkt.get("totalSeconds") else "",
            ("reused: " + "+".join(reused)) if reused else "re-rendered",
            status))
    _out("")

    _out("  %-9s %-6s %-12s %-8s %s" % ("id", "kind", "size", "target", "validation"))
    for r in rows:
        label, errors, warns = norm_validation(r["art"])
        vtxt = label
        if errors:
            vtxt += " %d error" % errors
        if warns:
            vtxt += "%s%d warn" % (" / " if errors else " ", warns)
        if r["art"].get("error"):
            vtxt = "BUILD-FAIL " + clip(r["art"]["error"], 28)
        _out("  %-9s %-6s %-12s %-8s %s"
             % (clip(r["id"], 9), r["kind"], _fmt_size(r),
                _fmt_target(r, defaults), vtxt))
    _out("")

    sheets = []
    for r in rows:
        p = artifact_paths(pdir, r)
        if os.path.isfile(p["contact"]):
            sheets.append(os.path.basename(p["contact"]))
    _out("  dir      %s" % pdir)
    if sheets:
        _out("  sheets   %s" % clip(" ".join(sheets), 72))
    _out("  files    %s" % clip(" ".join(os.path.basename(artifact_paths(pdir, r)["main"])
                                         for r in rows if artifact_paths(pdir, r)["main"]), 72))
    _out("")

    has_motion = any(r["kind"] in ("reel", "video") for r in rows)
    _out("WATCH THEM FIRST. A contact sheet is a grid of stills; it cannot show you")
    _out("pacing, a clipped voiceover or a caption that lands a beat late. Open the")
    _out("%s." % ("mp4s -- 15 seconds is cheaper than a wrong note"
                  if has_motion else "decks full-screen"))
    _out("")
    prompts = ["Does the first beat earn the next one?",
               "Is the one thing the only thing, or did a second idea creep in?",
               "Anything to cut -- a word, a beat, a scene, a slide?",
               "Anything off-brand -- colour, type, logo, tone?"]
    if has_motion:
        prompts.append("Pacing: anything you waited for, or missed?")
        prompts.append("Captions: readable, in sync, the right words?")
    for i, p in enumerate(prompts, 1):
        _out("  %d  %s" % (i, p))
    _out("")
    _out("  note it   review.py note --text \"...\" --target %s"
         % (rows[0]["id"] if rows else "all"))
    _out("  nothing?  review.py accept --date %s   (a zero-note day is the goal)" % date)
    return EXIT_OK


def _dur(sec):
    try:
        sec = float(sec)
    except (TypeError, ValueError):
        return "-"
    if sec < 90:
        return "%.0fs" % sec
    return "%dm %02ds" % (int(sec // 60), int(sec % 60))


# ---------------------------------------------------------------------------
# note
# ---------------------------------------------------------------------------

def cmd_note(args):
    brand_id = args.brand or DEFAULT_BRAND
    date = resolve_date(brand_id, args.date)
    pkt = load_packet(brand_id, date)
    rows = artifact_rows(pkt)
    text = _u(args.text or "").strip()
    if not text:
        raise ReviewError("--text is required and must not be empty")

    ids, label = resolve_target(args.target, rows)
    kinds = sorted(set(r["kind"] for r in rows if r["id"] in ids))

    category, scores = classify(text)
    if args.category:
        if args.category not in CATEGORIES:
            raise ReviewError("--category must be one of %s" % ", ".join(CATEGORIES))
        category = args.category

    brand = bl.load_brand(brand_id)
    fact = detect_brand_fact(text)
    rule = _resolve_placeholder(detect_rule(text, date, kinds), brand)

    doc = load_notes(brand_id, date)
    nid = "n%d" % (len(doc["notes"]) + 1)
    note = {
        "id": nid,
        "text": text,
        "target": label,
        "targets": ids,
        "kinds": kinds,
        "category": category,
        "categoryScores": dict((k, v) for k, v in scores.items() if v),
        "categorySource": "user" if args.category else "classifier",
        "mechanisable": bool(rule),
        "proposedRule": rule,
        "brandFact": fact,
        "createdAt": now_iso(),
        "applied": False,
        "appliedAt": None,
        "tier": None,
    }
    doc["notes"].append(note)
    doc["accepted"] = False
    doc["acceptedAt"] = None
    save_notes(brand_id, date, doc)

    if fact:
        tier = "3 (brand fact: %s -> %r)" % (fact["path"], fact["value"])
    elif rule:
        tier = "2 (%s %s, scope %s)" % (rule["id"], rule["kind"], rule["scope"])
    else:
        tier = "1 (ledger only -- judgement, not mechanically checkable)"
    _out("noted %s  [%s]  target %s (%s)" % (nid, category, label, ", ".join(kinds) or "-"))
    _out("  will land in tier %s" % tier)
    _out("  %d note(s) for %s. Run: review.py apply --date %s [--rebuild]"
         % (len(doc["notes"]), date, date))
    return EXIT_OK


# ---------------------------------------------------------------------------
# apply -- the part that makes tomorrow cheaper than today
# ---------------------------------------------------------------------------

def _statement(text):
    first = re.split(r"(?<=[.!?])\s+", _u(text).strip())[0]
    first = first.strip().rstrip(".")
    first = first[:1].upper() + first[1:] if first else "Review note"
    return clip(first, 96)


def append_learned(brand_id, date, note, tier_text, changed_text):
    """Tier 1. Always. Appended in the ledger's existing entry format."""
    path = os.path.join(bl.brand_dir(brand_id), "LEARNED.md")
    scope = ", ".join(note.get("kinds") or []) or note.get("target") or "both"
    entry = [
        u"",
        u"## %s — %s" % (date, _statement(note["text"])),
        u"",
        u"- **Scope:** %s (%s)" % (scope, note.get("target") or "all"),
        u"- **Changed:** %s" % changed_text,
        u"- **Why:** morning review note on the %s packet, category *%s*: “%s”"
        % (date, note.get("category") or "other", clip(note["text"], 220)),
        u"- **Tier:** %s" % tier_text,
        u"- **Ruled by:** brand owner, morning review %s" % date,
        u"",
    ]
    existing = u""
    if os.path.isfile(path):
        with io.open(path, "r", encoding="utf-8") as fh:
            existing = fh.read()
    if existing and not existing.endswith(u"\n"):
        existing += u"\n"
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(existing + u"\n".join(entry))
    return path


def add_local_rule(brand_id, rule):
    """Tier 2. Returns (added, reason). Never writes a duplicate id."""
    path = os.path.join(bl.brand_dir(brand_id), "rules.local.json")
    doc = read_json(path, {}) or {}
    if not isinstance(doc, dict):
        doc = {"rules": doc if isinstance(doc, list) else []}
    rules = doc.get("rules")
    if not isinstance(rules, list):
        rules = []
    for existing in rules:
        if isinstance(existing, dict) and existing.get("id") == rule["id"]:
            return (False, "already enforced as %s" % rule["id"])
    rules.append(dict((k, v) for k, v in rule.items() if not k.startswith("_")))
    doc["rules"] = rules
    write_json(path, doc)
    return (True, path)


def edit_brand_json(brand_id, path_key, value, why, date):
    """Tier 3. Bumps version, sets updated, records provenance."""
    path = os.path.join(bl.brand_dir(brand_id), "brand.json")
    doc = read_json(path)
    if not isinstance(doc, dict):
        raise ReviewError("brand.json missing or malformed at %s" % path)
    prev = dotted_set(doc, path_key, value)
    ver = str(doc.get("version") or "1.0.0").split(".")
    while len(ver) < 3:
        ver.append("0")
    try:
        ver[2] = str(int(ver[2]) + 1)
    except ValueError:
        ver[2] = "1"
    doc["version"] = ".".join(ver[:3])
    doc["updated"] = date
    prov = doc.get("provenance")
    if not isinstance(prov, dict):
        prov = {}
        doc["provenance"] = prov
    prov["review-%s" % date] = "%s: %s was %r, now %r." % (why, path_key, prev, value)
    write_json(path, doc)
    return (path, prev)


def edit_queue(brand_id, path_key, value):
    path = os.path.join(bl.brand_dir(brand_id), "content-queue.json")
    doc = read_json(path)
    if not isinstance(doc, dict):
        return (False, "content-queue.json missing")
    dotted_set(doc, path_key, value)
    write_json(path, doc)
    return (True, path)


# --- deterministic IR edits ------------------------------------------------

_SUB_QUOTED = re.compile(
    u"(?:replace|change|swap|rename)\\s+[\"'“‘]([^\"'”’]+)"
    u"[\"'”’]\\s+(?:with|to|for)\\s+[\"'“‘]([^\"'”’]+)"
    u"[\"'”’]", re.IGNORECASE)
_SUB_BARE = re.compile(
    r"(?:replace|change|swap|rename)\s+(?:the\s+word\s+)?([\w'\-]+)\s+"
    r"(?:with|to|for)\s+(?:the\s+word\s+)?([\w'\-]+)\b", re.IGNORECASE)
_INSTEAD = re.compile(
    u"\\b(?:say|use|write|call it)\\s+[\"'“‘]?([\\w '\\-]{1,40}?)"
    u"[\"'”’]?\\s+instead\\b", re.IGNORECASE)
_HOLD = re.compile(r"\bhold\s+(?:each\s+)?(?:scene|slide)s?\s+(?:for\s+)?"
                   r"([\d.]+)\s*(?:s\b|secs?\b|seconds?\b)", re.IGNORECASE)


def derive_edits(note):
    """Deterministic IR edits a note authorises. Never invents copy."""
    text = _u(note["text"])
    subs = []
    for rx in (_SUB_QUOTED, _SUB_BARE):
        for m in rx.finditer(text):
            subs.append((m.group(1).strip(), m.group(2).strip()))
        if subs:
            break
    rule = note.get("proposedRule") or {}
    if not subs and rule.get("kind") == "forbid_text":
        m = _INSTEAD.search(text)
        if m:
            banned = rule.get("value")
            banned = banned[0] if isinstance(banned, list) else banned
            if banned:
                subs.append((str(banned), m.group(1).strip()))
    hold = None
    m = _HOLD.search(text)
    if m:
        hold = _to_number(m.group(1))
    return {"subs": subs, "holdSec": hold}


def _apply_subs_node(node, keys, subs, changed):
    """Recursively substitute in text-bearing keys. Records which keys changed."""
    if isinstance(node, dict):
        for k, v in list(node.items()):
            if isinstance(v, str) and k in keys:
                new = v
                for old, rep in subs:
                    new = re.sub(re.escape(old), rep, new, flags=re.IGNORECASE)
                if new != v:
                    node[k] = new
                    changed.add(k)
            else:
                _apply_subs_node(v, keys, subs, changed)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, str):
                new = v
                for old, rep in subs:
                    new = re.sub(re.escape(old), rep, new, flags=re.IGNORECASE)
                if new != v:
                    node[i] = new
                    changed.add("items")
            else:
                _apply_subs_node(v, keys, subs, changed)


def edit_ir(ir_path, kind, edits):
    """Mutate the stored IR in place. Returns the set of keys touched."""
    doc = read_json(ir_path)
    if not isinstance(doc, dict):
        return (None, set())
    keys = DECK_TEXT_KEYS if kind == "deck" else VIDEO_TEXT_KEYS
    changed = set()
    if edits["subs"]:
        _apply_subs_node(doc, keys, edits["subs"], changed)
    if edits["holdSec"] and kind in ("reel", "video"):
        for scene in doc.get("scenes") or []:
            if isinstance(scene, dict):
                scene["holdSec"] = edits["holdSec"]
                changed.add("holdSec")
    if changed:
        write_json(ir_path, doc)
    return (doc, changed)


def rewrite_srt(srt_path, subs):
    if not subs or not os.path.isfile(srt_path):
        return False
    with io.open(srt_path, "r", encoding="utf-8") as fh:
        text = fh.read()
    new = text
    for old, rep in subs:
        new = re.sub(re.escape(old), rep, new, flags=re.IGNORECASE)
    if new == text:
        return False
    with io.open(srt_path, "w", encoding="utf-8") as fh:
        fh.write(new)
    return True


def run(cmd):
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out, _ = proc.communicate()
    except OSError as exc:
        return (1, "cannot run %s: %s" % (cmd[1] if len(cmd) > 1 else cmd[0], exc))
    return (proc.returncode, _u(out or ""))


def rebuild_one(brand_id, pdir, row, edits, python):
    """Rebuild one artifact. Returns a one-line human summary."""
    paths = artifact_paths(pdir, row)
    jid, kind = row["id"], row["kind"]
    if not os.path.isfile(paths["ir"]):
        return "%s: no IR at %s -- cannot rebuild" % (jid, os.path.basename(paths["ir"]))

    _doc, changed = edit_ir(paths["ir"], kind, edits)
    if not changed:
        return "%s: reused (no mechanical edit derivable from the notes)" % jid

    # Caption-only change on a film: rewrite the sidecar, keep the encode.
    if kind in ("reel", "video") and changed == set(["caption"]) \
            and os.path.isfile(paths["srt"]):
        if rewrite_srt(paths["srt"], edits["subs"]):
            return ("%s: rewrote %s only (caption text) -- mp4 reused, not re-encoded"
                    % (jid, os.path.basename(paths["srt"])))
        return "%s: caption edit produced no change -- reused" % jid

    if kind == "deck":
        cmd = [python, os.path.join(_HERE, "build_deck.py"), "--ir", paths["ir"],
               "--brand", brand_id, "--out", paths["main"]]
    else:
        cmd = [python, os.path.join(_HERE, "build_video.py"), "--ir", paths["ir"],
               "--brand", brand_id, "--out", paths["main"], "--quiet"]
        if paths["deck"] and os.path.isfile(paths["deck"]):
            cmd += ["--deck", paths["deck"]]
    code, out = run(cmd)
    if code != 0:
        tail = [ln for ln in out.strip().splitlines() if ln.strip()][-1:] or [""]
        return "%s: REBUILD FAILED (exit %d) %s" % (jid, code, clip(tail[0], 70))

    vcode, vout = run([python, os.path.join(_HERE, "validate.py"), paths["main"],
                       "--brand", brand_id, "--format", "json"])
    verdict = "validated clean" if vcode == 0 else "validation exit %d" % vcode
    try:
        payload = json.loads(vout)
        c = payload.get("counts") or {}
        verdict = "%d error / %d warn" % (c.get("error", 0), c.get("warn", 0))
    except ValueError:
        pass
    return "%s: rebuilt from %s (%s) -- %s" % (
        jid, os.path.basename(paths["ir"]), ", ".join(sorted(changed)), verdict)


def cmd_apply(args):
    brand_id = args.brand or DEFAULT_BRAND
    date = resolve_date(brand_id, args.date)
    pdir = packet_dir(brand_id, date)
    pkt = load_packet(brand_id, date)
    rows = artifact_rows(pkt)
    by_id = dict((r["id"], r) for r in rows)
    doc = load_notes(brand_id, date)

    pending = [n for n in doc["notes"] if args.force or not n.get("applied")]
    if not pending:
        _out("nothing to apply for %s (%d note(s), all already applied). "
             "Use --force to re-apply." % (date, len(doc["notes"])))
        return EXIT_OK

    brand = bl.load_brand(brand_id)
    summary = []
    touched = set()
    all_edits = {"subs": [], "holdSec": None}

    for note in pending:
        fact = note.get("brandFact")
        rule = note.get("proposedRule")
        tier_bits = []
        changed_bits = []

        if fact:
            if fact["file"] == "brand":
                _p, prev = edit_brand_json(brand_id, fact["path"], fact["value"],
                                           "morning review changed %s" % fact["label"],
                                           date)
                tier_bits.append("3 — `brand.json` `%s`" % fact["path"])
                changed_bits.append("`%s` %r → %r" % (fact["path"], prev, fact["value"]))
            else:
                ok, where = edit_queue(brand_id, fact["path"], fact["value"])
                tier_bits.append("3 — `content-queue.json` `%s`" % fact["path"]
                                 if ok else "1 — %s" % where)
                changed_bits.append("`%s` → %r" % (fact["path"], fact["value"]))
            tier_label = "3"
        elif rule:
            added, where = add_local_rule(brand_id, rule)
            tier_bits.append("2 — `rules.local.json` rule `%s` (%s, scope %s, %s%s)"
                             % (rule["id"], rule["kind"], rule["scope"], rule["severity"],
                                ", formats %s" % ", ".join(rule["formats"])
                                if rule.get("formats") else ""))
            changed_bits.append("added `%s`" % rule["id"] if added
                                else "`%s` was already enforced" % rule["id"])
            tier_label = "2"
            # The coverage mirror that used to sit here -- copying a forbid_text
            # ban into brand.json voice.forbiddenPhrases -- was retired on
            # 2026-08-25. It existed only because validate_video.py could not
            # read learnedRules; it now can. Beyond being redundant it reported a
            # voice ban under CONTENT.PLACEHOLDER, which means "template
            # scaffolding leaked" and was simply the wrong thing to say, and it
            # quietly promoted a warn rule to an error while writing a tier 3
            # fact from a tier 2 decision. A ban that should block a build is a
            # rule with "severity": "error", which video honours.
        else:
            tier_bits.append("1 — `LEARNED.md` only (judgement, not mechanically "
                             "checkable)")
            changed_bits.append("recorded as guidance for the next author")
            tier_label = "1"

        append_learned(brand_id, date, note, "; ".join(tier_bits),
                       "; ".join(changed_bits))
        note["applied"] = True
        note["appliedAt"] = now_iso()
        note["tier"] = tier_label

        edits = derive_edits(note)
        if edits["subs"] or edits["holdSec"]:
            all_edits["subs"].extend(edits["subs"])
            if edits["holdSec"]:
                all_edits["holdSec"] = edits["holdSec"]
            touched.update(note.get("targets") or [])
        summary.append((note, tier_label, tier_bits))

    doc["appliedAt"] = now_iso()
    save_notes(brand_id, date, doc)

    _out("applied %d note(s) for %s" % (len(pending), date))
    for note, tier_label, tier_bits in summary:
        _out("  %-4s tier %-4s %-9s %s"
             % (note["id"], tier_label, note.get("category", "?"),
                clip(note["text"], 52)))
        for bit in tier_bits:
            for ln in _wrap(re.sub(r"[`*]", "", bit), 84):
                _out("        %s" % ln)
    _out("  ledger: %s" % os.path.join(bl.brand_dir(brand_id), "LEARNED.md"))

    if not args.rebuild:
        _out("  (no --rebuild: artifacts untouched. The rules take effect on the "
             "next build.)")
        return EXIT_OK

    _out("")
    if not (all_edits["subs"] or all_edits["holdSec"]):
        _out("rebuild: nothing rebuilt -- all %d artifact(s) reused." % len(rows))
        _out("  None of these notes carries a deterministic edit (a 'replace X with Y',")
        _out("  a 'say Y instead', or an explicit hold in seconds). Rewriting copy is an")
        _out("  authoring step; this script will not invent words. Edit the IR, then")
        _out("  rerun with --rebuild --force.")
        return EXIT_OK

    python = find_python()
    _out("rebuild: %s" % ", ".join(sorted(touched)))
    worst = EXIT_OK
    for jid in sorted(touched, key=natkey):
        row = by_id.get(jid)
        if row is None:
            _out("  %s: not in this packet -- skipped" % jid)
            continue
        line = rebuild_one(brand_id, pdir, row, all_edits, python)
        _out("  " + line)
        if "FAILED" in line or re.search(r"[1-9]\d* error", line):
            worst = EXIT_VIOLATIONS
    reused = [r["id"] for r in rows if r["id"] not in touched]
    if reused:
        _out("  reused unchanged: %s" % ", ".join(reused))
    return worst


# ---------------------------------------------------------------------------
# accept
# ---------------------------------------------------------------------------

def mark_reviewed(brand_id, date):
    path = os.path.join(packet_dir(brand_id, date), "packet.json")
    pkt = read_json(path)
    if isinstance(pkt, dict):
        pkt["status"] = "reviewed"
        pkt["reviewedAt"] = now_iso()
        write_json(path, pkt)


def cmd_accept(args):
    brand_id = args.brand or DEFAULT_BRAND
    date = resolve_date(brand_id, args.date)
    doc = load_notes(brand_id, date)
    open_notes = [n for n in doc["notes"] if not n.get("applied")]
    if open_notes:
        raise ReviewError(
            "%d note(s) for %s are recorded but not applied. Run "
            "'review.py apply --date %s' first, or delete the notes."
            % (len(open_notes), date, date))
    doc["accepted"] = True
    doc["acceptedAt"] = now_iso()
    save_notes(brand_id, date, doc)
    mark_reviewed(brand_id, date)
    n = len(doc["notes"])
    if n == 0:
        _out("accepted %s with ZERO notes. That is the goal state, and it is "
             "counted." % date)
    else:
        _out("accepted %s. %d note(s) were applied first; the day is closed." % (date, n))
    _out("  trend: review.py metrics --brand %s" % brand_id)
    return EXIT_OK


# ---------------------------------------------------------------------------
# metrics -- the proof the loop works
# ---------------------------------------------------------------------------

def fingerprint(text):
    toks = re.findall(r"[a-z0-9']+", _u(text).lower())
    return frozenset(t for t in toks if len(t) >= 3 and t not in _STOPWORDS)


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def sparkline(values):
    if not values:
        return ""
    hi = max(values)
    if hi <= 0:
        return SPARK[0] * len(values)
    out = []
    for v in values:
        idx = int(round((float(v) / hi) * (len(SPARK) - 1)))
        out.append(SPARK[max(0, min(idx, len(SPARK) - 1))])
    return u"".join(out)


def collect_days(brand_id, days):
    """Reviewed days only: a day with a packet. Missing days are not zeros."""
    end = datetime.date.today()
    start = end - datetime.timedelta(days=int(days) - 1)
    out = []
    for d in list_packet_dates(brand_id):
        dd = parse_date(d)
        if dd < start or dd > end:
            continue
        notes_doc = read_json(notes_path(brand_id, d), {}) or {}
        notes = notes_doc.get("notes") if isinstance(notes_doc, dict) else []
        if not isinstance(notes, list):
            notes = []
        pkt = read_json(os.path.join(packet_dir(brand_id, d), "packet.json"), {}) or {}
        reviewed = bool(notes) or bool(notes_doc.get("accepted")) \
            or str(pkt.get("status") or "") == "reviewed"
        out.append({"date": d, "notes": notes, "reviewed": reviewed,
                    "artifacts": artifact_rows(pkt)})
    return out


def _kinds_for_note(note, kind_by_id):
    kinds = note.get("kinds")
    if isinstance(kinds, list) and kinds:
        return kinds
    return sorted(set(kind_by_id.get(i, "other") for i in (note.get("targets") or []))) \
        or ["other"]


def cmd_metrics(args):
    brand_id = args.brand or DEFAULT_BRAND
    days = max(1, int(args.days))
    rows = collect_days(brand_id, days)
    reviewed = [r for r in rows if r["reviewed"]]

    rules_doc = read_json(os.path.join(bl.brand_dir(brand_id), "rules.local.json"), {}) or {}
    rule_list = rules_doc.get("rules") if isinstance(rules_doc, dict) else []
    rule_count = len(rule_list) if isinstance(rule_list, list) else 0

    _out(u"brand-studio · review metrics · %s · last %d days" % (brand_id, days))
    _out("")

    if not rows:
        _out("  No packets in this window. Nothing to measure yet -- the trend needs")
        _out("  at least two reviewed days. Enforced rules today: %d." % rule_count)
        write_json(os.path.join(daily_dir(brand_id), "metrics.json"), {
            "brand": brand_id, "generatedAt": now_iso(), "windowDays": days,
            "reviewedDays": 0, "totalNotes": 0, "perDay": [], "byCategory": {},
            "byKind": {}, "mechanised": 0, "ledgerOnly": 0, "ruleCount": rule_count,
            "rolling7": None, "previous7": None, "verdict": "no data",
        })
        return EXIT_OK

    # --- per day ---------------------------------------------------------
    per_day = [{"date": r["date"], "notes": len(r["notes"]),
                "reviewed": r["reviewed"]} for r in rows]
    counts = [p["notes"] for p in per_day if p["reviewed"]]
    _out("  notes per day (%d reviewed day(s))" % len(reviewed))
    tail = per_day[-14:]
    for p in tail:
        bar = ("#" * min(p["notes"], 40)) if p["notes"] else "."
        flag = "" if p["reviewed"] else "   (not reviewed)"
        _out("    %s  %2d  %s%s" % (p["date"], p["notes"], bar, flag))
    if len(per_day) > len(tail):
        _out("    ... %d earlier day(s) folded into the sparkline" % (len(per_day) - len(tail)))
    _out("    trend %s  (oldest %s newest)"
         % (sparkline([p["notes"] for p in per_day]), u"→"))
    _out("")

    # --- 7 vs previous 7 --------------------------------------------------
    cur = counts[-7:]
    prev = counts[-14:-7]
    cur_avg = (sum(cur) / float(len(cur))) if cur else None
    prev_avg = (sum(prev) / float(len(prev))) if prev else None
    if cur_avg is None:
        verdict = "no reviewed days yet -- nothing to trend"
        _out("  %s" % verdict)
    elif prev_avg is None:
        verdict = ("%.1f notes/day over %d day(s). No prior week to compare -- "
                   "come back after 7 more days." % (cur_avg, len(cur)))
        _out("  %s" % verdict)
    else:
        delta = cur_avg - prev_avg
        if delta <= -0.5 or (prev_avg > 0 and delta / prev_avg <= -0.15):
            verdict = ("%.1f notes/day, down from %.1f -- the loop is working."
                       % (cur_avg, prev_avg))
        elif delta >= 0.5:
            verdict = ("%.1f notes/day, UP from %.1f -- review burden is rising. "
                       "Notes are not becoming rules." % (cur_avg, prev_avg))
        else:
            verdict = ("%.1f notes/day, flat against %.1f -- notes are not becoming "
                       "rules. Look at the repeat table below and mechanise the top "
                       "row." % (cur_avg, prev_avg))
        _out("  %s" % verdict)
    _out("")

    # --- by category and by kind -----------------------------------------
    def bucket(fn):
        cur_b, prev_b, all_b = {}, {}, {}
        cur_dates = set(p["date"] for p in per_day if p["reviewed"])
        ordered = sorted(cur_dates)
        recent = set(ordered[-7:])
        earlier = set(ordered[-14:-7])
        for r in rows:
            kind_by_id = dict((a["id"], a["kind"]) for a in r["artifacts"])
            for note in r["notes"]:
                for key in fn(note, kind_by_id):
                    all_b[key] = all_b.get(key, 0) + 1
                    if r["date"] in recent:
                        cur_b[key] = cur_b.get(key, 0) + 1
                    elif r["date"] in earlier:
                        prev_b[key] = prev_b.get(key, 0) + 1
        return all_b, cur_b, prev_b

    def render(title, all_b, cur_b, prev_b, total):
        _out("  %s" % title)
        if not all_b:
            _out("    (none)")
            return
        for key in sorted(all_b, key=lambda k: (-all_b[k], k)):
            n = all_b[key]
            c, p = cur_b.get(key, 0), prev_b.get(key, 0)
            if c > p:
                arrow = u"↑ worse"
            elif c < p:
                arrow = u"↓ better"
            else:
                arrow = u"→ flat"
            share = (100.0 * n / total) if total else 0.0
            _out("    %-10s %3d  %4.0f%%   last7 %d vs prev7 %d  %s"
                 % (key, n, share, c, p, arrow))

    total_notes = sum(len(r["notes"]) for r in rows)
    a, c, p = bucket(lambda n, k: [n.get("category") or "other"])
    render("by category", a, c, p, total_notes)
    _out("")
    a2, c2, p2 = bucket(_kinds_for_note)
    render("by artifact kind (a note may name more than one kind)",
           a2, c2, p2, total_notes)
    _out("")

    # --- mechanised vs ledger --------------------------------------------
    mech = sum(1 for r in rows for n in r["notes"] if n.get("tier") in ("2", "2+3", "3"))
    ledger = sum(1 for r in rows for n in r["notes"]
                 if n.get("tier") == "1" or n.get("tier") is None)
    pct = (100.0 * mech / total_notes) if total_notes else 0.0
    _out("  enforcement")
    _out("    %d of %d notes (%.0f%%) became an enforced rule or a brand fact; "
         "%d are ledger-only." % (mech, total_notes, pct, ledger))
    _out("    %d rule(s) in rules.local.json today." % rule_count)
    if total_notes and pct < 40:
        _out("    Under 40% mechanised -- that is why the same note keeps coming back.")
    _out("")

    # --- repeats ----------------------------------------------------------
    groups = {}
    for r in rows:
        for note in r["notes"]:
            cat = note.get("category") or "other"
            fp = fingerprint(note.get("text"))
            placed = False
            for g in groups.setdefault(cat, []):
                if jaccard(g["fp"], fp) >= 0.6:
                    g["days"].add(r["date"])
                    g["texts"].append(note.get("text"))
                    placed = True
                    break
            if not placed:
                groups[cat].append({"fp": fp, "days": set([r["date"]]),
                                    "texts": [note.get("text")]})
    repeats = []
    for cat, gs in groups.items():
        for g in gs:
            if len(g["days"]) >= 2:
                repeats.append((len(g["days"]), cat, g["texts"][0]))
    repeats.sort(key=lambda t: (-t[0], t[1]))
    _out("  repeat notes -- the clearest signal of what to mechanise next")
    if not repeats:
        _out("    None. No note has been given on two separate days.")
    else:
        for n, cat, text in repeats[:6]:
            _out("    %-10s given on %d days: %s" % (cat, n, clip(text, 52)))
        _out("    A repeat is a rule you have not written yet.")
    _out("")

    payload = {
        "brand": brand_id, "generatedAt": now_iso(), "windowDays": days,
        "reviewedDays": len(reviewed), "totalNotes": total_notes,
        "perDay": per_day, "byCategory": a, "byKind": a2,
        "byCategoryLast7": c, "byCategoryPrev7": p,
        "byKindLast7": c2, "byKindPrev7": p2,
        "mechanised": mech, "ledgerOnly": ledger, "ruleCount": rule_count,
        "rolling7": cur_avg, "previous7": prev_avg, "verdict": verdict,
        "repeats": [{"days": n, "category": cat, "example": clip(t, 120)}
                    for n, cat, t in repeats],
    }
    path = os.path.join(daily_dir(brand_id), "metrics.json")
    write_json(path, payload)
    _out("  written %s" % path)
    return EXIT_OK


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(
        prog="review.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--brand", default=None,
                       help="brand id (default: %s)" % DEFAULT_BRAND)

    p = sub.add_parser("show", help="the morning briefing for one packet")
    common(p)
    p.add_argument("--date", default=None,
                   help="YYYY-MM-DD (default: the latest packet awaiting review)")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("note", help="record one review note against the day")
    common(p)
    p.add_argument("--text", required=True, help="the note, in the reviewer's words")
    p.add_argument("--target", default="all",
                   help="job id (deck-3), kind (deck|video|reel), 'both' "
                        "(reel+video) or 'all'. Comma-separated is allowed.")
    p.add_argument("--category", default=None, choices=list(CATEGORIES),
                   help="override the deterministic classifier")
    p.add_argument("--date", default=None)
    p.set_defaults(func=cmd_note)

    p = sub.add_parser("apply", help="persist the day's notes into the three tiers")
    common(p)
    p.add_argument("--date", default=None)
    p.add_argument("--rebuild", action="store_true",
                   help="also regenerate the artifacts the notes actually touched")
    p.add_argument("--force", action="store_true",
                   help="re-apply notes already applied")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("metrics", help="is the review burden actually falling?")
    common(p)
    p.add_argument("--days", type=int, default=30)
    p.set_defaults(func=cmd_metrics)

    p = sub.add_parser("accept", help="close the day with zero notes")
    common(p)
    p.add_argument("--date", default=None)
    p.set_defaults(func=cmd_accept)
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return EXIT_OK
    try:
        return args.func(args)
    except ReviewError as exc:
        _err(str(exc))
        return EXIT_INTERNAL
    except bl.BrandNotFound as exc:
        _err(str(exc))
        return EXIT_INTERNAL
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
