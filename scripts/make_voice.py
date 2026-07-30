#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_voice.py - human-quality multilingual voiceover for a brand film.

TWO ENGINES
-----------
piper  Neural TTS, running locally on the CPU. This is the default and it is
       the one that sounds like a person. 49 languages, no API, no key, no
       per-word cost, nothing leaves the machine. The catch is a one-time
       ~63 MB model download per language, kept outside the repo in
       ~/.cache/brand-studio/voices/.

say    macOS's built-in synthesiser. Instant, 184 voices already on the disk,
       and noticeably more robotic than Piper. It is not only a downgrade
       though: `say` covers seven languages Piper has no model for at all --
       Tamil, Kannada, Japanese, Thai, Malay, Croatian and Lithuanian -- which
       for an Indian agency means Tamil and Kannada work here and nowhere else.

WHY NORMALISATION MATTERS
-------------------------
The difference between narration and a screen reader is mostly numbers. A brand
script is full of "4,200 stores", "62%", "3.4x". Fed raw to a synthesiser those
come out as digits, symbols, or silence. This script expands them the way a
person says them before a single sample is generated, and prints what it did in
the sidecar so the change is auditable rather than magic.

Usage:
    make_voice.py --text "Compliance rose to 92%." --out vo.wav --json
    make_voice.py --text-file script.txt --lang hi --out vo-hi.wav
    make_voice.py --list-voices
    make_voice.py --install-voice hi_IN-pratham-medium
"""

from __future__ import absolute_import, division

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import wave

try:
    import numpy as np
except ImportError:  # pragma: no cover
    sys.stderr.write("make_voice.py needs numpy.\n")
    raise SystemExit(2)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import brandlib  # noqa: E402


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

VOICES_DIR = os.path.join(os.path.expanduser("~"), ".cache", "brand-studio", "voices")
WORK_DIR = os.path.join(os.path.expanduser("~"), ".cache", "brand-studio", "tmp")

HF_INDEX_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json"
HF_FILE_URL = ("https://huggingface.co/rhasspy/piper-voices/resolve/main/"
               "{family}/{locale}/{name}/{quality}/{voice}{ext}")
INDEX_CACHE = os.path.join(VOICES_DIR, "_index.json")
INDEX_MAX_AGE_SEC = 7 * 24 * 3600

#: Intermediate and delivery sample rate. Piper models are 22.05 kHz; the film
#: mix runs at 44.1 kHz, so every segment is resampled once, up front, rather
#: than at concat time where the rates would have to agree anyway.
WORK_SR = 44100

DEFAULT_TARGET_LUFS = -16.0
TARGET_TRUE_PEAK_DB = -1.5      # matches build_video.py's LOUDNORM_TP_VO
LOUDNORM_LRA = 11.0

#: Head/tail silence kept after trimming. The brief says "under 150 ms".
LEAD_SILENCE_SEC = 0.06
TAIL_SILENCE_SEC = 0.14
SILENCE_FLOOR_DB = -45.0

#: Silence kept at the edges of each individual segment before concatenation.
SEGMENT_EDGE_SEC = 0.015
SEGMENT_TAIL_SEC = 0.05

#: Gap inserted between paragraphs, before --pause-scale is applied.
PARAGRAPH_GAP_MS = 450

#: Calibration constant for turning --rate into a Piper length_scale. Fitted
#: against en_US-lessac-medium on a 61-word prose paragraph: with this value,
#: --rate 140/165/190 measured 145/168/186 wpm, i.e. within about 4%. Neural
#: voices are content-sensitive -- a line thick with expanded numbers runs
#: faster than prose -- so treat --rate as a request and the sidecar's
#: effectiveWpm as the fact.
PIPER_NATURAL_WPM = 198.0

#: Emphasis: how much slower, and how much louder, an [emph] span is spoken.
EMPH_SLOWDOWN = 1.12
EMPH_GAIN = 1.12
EMPH_SAY_PITCH = "+6"

#: Piper voice to install for a language when the caller names only a language.
#: Family -> (voice id, English name). Generated from the rhasspy/piper-voices
#: index; the live index is consulted first and this is the offline fallback.
PIPER_DEFAULTS = {
    "ar": ("ar_JO-kareem-medium", "Arabic"),
    "bg": ("bg_BG-dimitar-medium", "Bulgarian"),
    "bn": ("bn_BD-google-medium", "Bengali"),
    "ca": ("ca_ES-upc_ona-medium", "Catalan"),
    "cs": ("cs_CZ-jirka-medium", "Czech"),
    "cy": ("cy_GB-bu_tts-medium", "Welsh"),
    "da": ("da_DK-talesyntese-medium", "Danish"),
    "de": ("de_DE-thorsten-medium", "German"),
    "el": ("el_GR-joy-medium", "Greek"),
    "en": ("en_US-lessac-medium", "English"),
    "es": ("es_ES-davefx-medium", "Spanish"),
    "eu": ("eu_ES-antton-medium", "Basque"),
    "fa": ("fa_IR-amir-medium", "Farsi"),
    "fi": ("fi_FI-harri-medium", "Finnish"),
    "fr": ("fr_FR-siwis-medium", "French"),
    "he": ("he_IL-saspeech-medium", "Hebrew"),
    "hi": ("hi_IN-pratham-medium", "Hindi"),
    "hu": ("hu_HU-anna-medium", "Hungarian"),
    "hy": ("hy_AM-gor-medium", "Armenian"),
    "id": ("id_ID-news_tts-medium", "Indonesian"),
    "is": ("is_IS-bui-medium", "Icelandic"),
    "it": ("it_IT-paola-medium", "Italian"),
    "ka": ("ka_GE-natia-medium", "Georgian"),
    "kk": ("kk_KZ-issai-high", "Kazakh"),
    "ko": ("ko_KR-kss-medium", "Korean"),
    "ku": ("ku_TR-berfin_renas-medium", "Kurmanji Kurdish"),
    "lb": ("lb_LU-marylux-medium", "Luxembourgish"),
    "lv": ("lv_LV-aivars-medium", "Latvian"),
    "ml": ("ml_IN-arjun-medium", "Malayalam"),
    "mr": ("mr_IN-google-medium", "Marathi"),
    "ne": ("ne_NP-chitwan-medium", "Nepali"),
    "nl": ("nl_BE-nathalie-medium", "Dutch"),
    "no": ("no_NO-nvcc-medium", "Norwegian"),
    "pl": ("pl_PL-darkman-medium", "Polish"),
    "pt": ("pt_BR-faber-medium", "Portuguese"),
    "ro": ("ro_RO-mihai-medium", "Romanian"),
    "ru": ("ru_RU-irina-medium", "Russian"),
    "sk": ("sk_SK-lili-medium", "Slovak"),
    "sl": ("sl_SI-artur-medium", "Slovenian"),
    "sq": ("sq_AL-edon-medium", "Albanian"),
    "sr": ("sr_RS-serbski_institut-medium", "Serbian"),
    "sv": ("sv_SE-alma-medium", "Swedish"),
    "sw": ("sw_CD-lanfrica-medium", "Swahili"),
    "te": ("te_IN-maya-medium", "Telugu"),
    "tr": ("tr_TR-dfki-medium", "Turkish"),
    "uk": ("uk_UA-ukrainian_tts-medium", "Ukrainian"),
    "ur": ("ur_PK-fasih-medium", "Urdu"),
    "vi": ("vi_VN-vais1000-medium", "Vietnamese"),
    "zh": ("zh_CN-huayan-medium", "Chinese"),
}

#: Languages where `no` and `nb` mean the same thing, and similar aliases.
LANG_ALIASES = {"nb": "no", "nn": "no", "iw": "he", "in": "id", "ji": "yi",
                "cmn": "zh", "pan": "pa", "tel": "te", "tam": "ta"}

#: macOS voices that exist for comedy, plus the localised character voices
#: (Eddy, Flo, Grandma, ...) that ship in a dozen languages each. Matched on the
#: base name, because the real entries look like "Eddy (German (Germany))".
SAY_NOVELTY = frozenset((
    "Albert", "Bad News", "Bahh", "Bells", "Boing", "Bubbles", "Cellos",
    "Deranged", "Good News", "Hysterical", "Jester", "Junior", "Kathy",
    "Organ", "Pipe Organ", "Princess", "Ralph", "Superstar", "Trinoids",
    "Whisper", "Wobble", "Zarvox", "Fred", "Bruce", "Agnes", "Eddy", "Flo",
    "Grandma", "Grandpa", "Reed", "Rocko", "Sandy", "Shelley",
))

#: Preferred `say` voice per locale, in order. Falls back to the first
#: non-novelty voice for the language, alphabetically, so it stays deterministic.
SAY_PREFERRED = {
    "en_IN": ["Rishi", "Aman", "Tara"],
    "en_US": ["Samantha", "Alex", "Ava", "Allison", "Tom"],
    "en_GB": ["Daniel", "Serena", "Kate", "Oliver"],
    "en_AU": ["Karen", "Lee"],
    "hi_IN": ["Lekha"], "ta_IN": ["Vani"], "te_IN": ["Geeta"],
    "bn_IN": ["Piya"], "kn_IN": ["Soumya"],
    "es_ES": ["Monica"], "es_MX": ["Paulina"],
    "fr_FR": ["Thomas", "Audrey"], "de_DE": ["Anna", "Markus"],
    "it_IT": ["Alice", "Luca"], "pt_BR": ["Luciana"],
    "ja_JP": ["Kyoko", "Otoya"], "ko_KR": ["Yuna"],
    "zh_CN": ["Tingting"], "zh_TW": ["Meijia"], "zh_HK": ["Sinji"],
    "ru_RU": ["Milena"], "ar_001": ["Majed"], "th_TH": ["Kanya"],
}

#: English names for languages `say` covers but Piper does not.
SAY_ONLY_NAMES = {"hr": "Croatian", "ja": "Japanese", "lt": "Lithuanian",
                  "ms": "Malay", "ta": "Tamil", "th": "Thai", "kn": "Kannada"}


class VoiceError(Exception):
    """Anything that should stop the run with an actionable message."""


# ---------------------------------------------------------------------------
# SPEECH NORMALISATION
# ---------------------------------------------------------------------------
# Full expansion is English-only, and deliberately so: a half-correct expansion
# in a language nobody on the team reads is worse than no expansion, because it
# sounds fluent and says the wrong thing. For every other language the symbols
# that have an unambiguous word get replaced and the digits are left for the
# engine's own phonemiser, which reads them in-language. Anything not recognised
# is passed through untouched rather than guessed at.

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
         "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
         "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
         "eighty", "ninety"]
_SCALES = [(1000000000000, "trillion"), (1000000000, "billion"),
           (1000000, "million"), (1000, "thousand")]

_ORDINALS = {1: "first", 2: "second", 3: "third", 5: "fifth", 8: "eighth",
             9: "ninth", 12: "twelfth"}

_CURRENCY = {
    "₹": "rupees", "RS": "rupees", "RS.": "rupees", "INR": "rupees",
    "$": "dollars", "USD": "dollars", "US$": "dollars",
    "€": "euros", "EUR": "euros", "£": "pounds", "GBP": "pounds",
    "¥": "yen", "JPY": "yen", "AED": "dirhams", "SGD": "Singapore dollars",
}

_MAGNITUDES = {
    "K": "thousand", "M": "million", "B": "billion", "BN": "billion",
    "T": "trillion", "CR": "crore", "CRORE": "crore", "CRORES": "crore",
    "L": "lakh", "LAKH": "lakh", "LAKHS": "lakh", "LAC": "lakh", "LACS": "lakh",
}

_UNITS = {
    "km": "kilometres", "kms": "kilometres", "m": "metres", "cm": "centimetres",
    "mm": "millimetres", "kg": "kilograms", "kgs": "kilograms", "g": "grams",
    "hr": "hour", "hrs": "hours", "min": "minute", "mins": "minutes",
    "sec": "second", "secs": "seconds", "sqft": "square feet",
    "gb": "gigabytes", "mb": "megabytes", "tb": "terabytes",
}

#: Expanded BEFORE any digits are touched, so "FY2024" and "24/7" and "No. 1"
#: still have their digits when the rule that needs them runs.
_ABBREV = [
    (r"\b24\s*/\s*7\b", "twenty four seven"),
    (r"\bR\s*&\s*D\b", "R and D"),
    (r"\bvs\.?(?=\s|$)", "versus"),
    (r"\be\.\s?g\.", "for example"),
    (r"\bi\.\s?e\.", "that is"),
    (r"\betc\.", "et cetera"),
    (r"\bapprox\.", "approximately"),
    (r"\bNo\.\s*(?=\d)", "number "),
    (r"\bFY\s*(?=\d)", "financial year "),
    (r"\bQ([1-4])(?=\s|$)", r"quarter \1"),
    (r"&", " and "),
    (r"(?<=[\d%])\s*\+(?=\s|$|[.,;:])", " plus"),
]

#: Symbol handling for the non-English languages we can do safely. Each entry is
#: (percent word, currency word for INR, conjunction). Missing entries mean the
#: text is passed through untouched -- see normalize_other.
SYMBOL_WORDS = {
    "hi": {"percent": "प्रतिशत", "inr": "रुपये", "and": "और"},
    "bn": {"percent": "শতাংশ", "inr": "টাকা", "and": "এবং"},
    "mr": {"percent": "टक्के", "inr": "रुपये", "and": "आणि"},
    "te": {"percent": "శాతం", "inr": "రూపాయలు", "and": "మరియు"},
    "ta": {"percent": "சதவீதம்", "inr": "ரூபாய்", "and": "மற்றும்"},
    "kn": {"percent": "ಶೇಕಡಾ", "inr": "ರೂಪಾಯಿ", "and": "ಮತ್ತು"},
    "ml": {"percent": "ശതമാനം", "inr": "രൂപ"},
    "ur": {"percent": "فیصد", "inr": "روپے", "and": "اور"},
    "es": {"percent": "por ciento", "and": "y"},
    "fr": {"percent": "pour cent", "and": "et"},
    "de": {"percent": "Prozent", "and": "und"},
    "pt": {"percent": "por cento", "and": "e"},
    "it": {"percent": "per cento", "and": "e"},
    "nl": {"percent": "procent", "and": "en"},
    "ru": {"percent": "процентов", "and": "и"},
    "tr": {"percent": "yüzde", "and": "ve"},
}


def _say_below_thousand(n):
    if n < 20:
        return _ONES[n]
    if n < 100:
        rest = n % 10
        return _TENS[n // 10] + (" " + _ONES[rest] if rest else "")
    rest = n % 100
    out = _ONES[n // 100] + " hundred"
    if rest:
        out += " " + _say_below_thousand(rest)
    return out


def say_integer(n):
    """Spell an integer the way a person reads it aloud."""
    n = int(n)
    if n < 0:
        return "minus " + say_integer(-n)
    if n < 1000:
        return _say_below_thousand(n)
    parts = []
    for value, word in _SCALES:
        if n >= value:
            parts.append(_say_below_thousand(n // value) if value < 1000000
                         else say_integer(n // value))
            parts.append(word)
            n %= value
    if n:
        parts.append(_say_below_thousand(n))
    return " ".join(p for p in parts if p)


def say_year(n):
    """1997 -> 'nineteen ninety seven'; 2026 -> 'twenty twenty six'."""
    n = int(n)
    hi, lo = divmod(n, 100)
    if lo == 0:
        # 2000 is "two thousand", not "twenty hundred"; 1900 is "nineteen hundred"
        if hi % 10 == 0:
            return say_integer(n)
        return _say_below_thousand(hi) + " hundred"
    if lo < 10:
        return _say_below_thousand(hi) + " oh " + _ONES[lo]
    return _say_below_thousand(hi) + " " + _say_below_thousand(lo)


def say_decimal(whole, frac):
    digits = " ".join(_ONES[int(d)] for d in frac)
    return say_integer(whole) + " point " + digits


def say_ordinal(n):
    n = int(n)
    if n in _ORDINALS:
        return _ORDINALS[n]
    words = say_integer(n)
    last = words.rsplit(" ", 1)[-1].rsplit("-", 1)[-1]
    tail = {"one": "first", "two": "second", "three": "third", "five": "fifth",
            "eight": "eighth", "nine": "ninth", "twelve": "twelfth"}.get(last)
    if tail:
        return words[:len(words) - len(last)] + tail
    if last.endswith("y"):
        return words[:-1] + "ieth"
    return words + "th"


def _number_words(text):
    """'4,200' or '3.4' -> spoken words."""
    clean = text.replace(",", "").strip()
    if "." in clean:
        whole, frac = clean.split(".", 1)
        return say_decimal(int(whole or 0), frac)
    return say_integer(int(clean))


def normalize_english(text, log):
    """Expand numbers, currency, units and abbreviations for English."""
    original = text

    def note(kind, before, after):
        if before != after:
            log.append({"rule": kind, "from": before.strip(), "to": after.strip()})
        return after

    def sub(pattern, repl, kind, flags=0):
        out = []

        def wrapper(match):
            replaced = repl(match)
            out.append((match.group(0), replaced))
            return replaced
        result = re.sub(pattern, wrapper, text, flags=flags)
        for before, after in out:
            note(kind, before, after)
        return result

    # Abbreviations first, while the digits they depend on are still digits:
    # "FY2024", "No. 1", "24/7" all break if a number rule gets there first.
    for pattern, replacement in _ABBREV:
        before = text
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE
                      if pattern.startswith(r"\bapprox") or
                      pattern.startswith(r"\betc") else 0)
        if before != text:
            note("abbreviation", before, text)

    # A number never ends on a comma, and the lookaheads below never swallow
    # trailing punctuation -- getting that wrong eats the full stop and turns
    # "4,200." into "four" plus a stray ",200.".
    num = r"\d(?:[\d,]*\d)?"
    dec = num + r"(?:\.\d+)?"

    # currency, with an optional magnitude word: $1.2M, Rs 500, EUR 4.2 crore
    text = sub(
        r"(?<![\w])(₹|\$|€|£|¥|Rs\.?|INR|USD|EUR|GBP|AED)\s*"
        r"(" + dec + r")\s*"
        r"(crores?|lakhs?|lacs?|[KMBTkmbt]n?)?(?![\w])",
        lambda m: " %s %s%s " % (
            _number_words(m.group(2)),
            (_MAGNITUDES.get((m.group(3) or "").upper(), "") + " ").lstrip(),
            _CURRENCY.get(m.group(1).upper().rstrip("."), "rupees")),
        "currency")

    # bare Indian magnitudes: 4.2 crore, 12.5 lakh
    text = sub(
        r"(?<![\w.])(" + dec + r")\s*(crores?|lakhs?|lacs?)(?![\w])",
        lambda m: " %s %s " % (_number_words(m.group(1)),
                               _MAGNITUDES[m.group(2).upper()]),
        "magnitude")

    # percentages
    text = sub(r"(?<![\w])(" + dec + r")\s*%",
               lambda m: " %s percent " % _number_words(m.group(1)),
               "percent")

    # multipliers: 3.4x, 2X  (but not SKU-4200X)
    text = sub(r"(?<![\w-])(" + dec + r")\s*[xX](?![\w])",
               lambda m: " %s times " % _number_words(m.group(1)),
               "multiplier")

    # ordinals
    text = sub(r"(?<![\w])(\d+)(?:st|nd|rd|th)(?![\w])",
               lambda m: " %s " % say_ordinal(m.group(1)), "ordinal")

    # clock times
    text = sub(r"(?<![\w:])([01]?\d|2[0-3]):([0-5]\d)(?![\w:])",
               lambda m: " %s %s " % (
                   say_integer(m.group(1)),
                   "o'clock" if m.group(2) == "00" else
                   ("oh " + _ONES[int(m.group(2))] if int(m.group(2)) < 10
                    else say_integer(m.group(2)))),
               "time")

    # year ranges, only when both sides look like years
    text = sub(r"(?<![\w])(19\d{2}|20\d{2})\s*[-–—]\s*(19\d{2}|20\d{2})(?![\w])",
               lambda m: " %s to %s " % (say_year(m.group(1)), say_year(m.group(2))),
               "year-range")

    # numeric ranges written with an en or em dash
    text = sub(r"(?<![\w])(" + num + r")\s*[–—]\s*(" + num + r")(?![\w])",
               lambda m: " %s to %s " % (_number_words(m.group(1)),
                                         _number_words(m.group(2))),
               "range")

    # standalone years. Deliberately narrow: 1900-2099 only, so a plain count
    # like 1200 reads as "one thousand two hundred" and not "twelve hundred".
    text = sub(r"(?<![\w.,])(19\d{2}|20\d{2})(?![\w%])",
               lambda m: " %s " % say_year(m.group(1)), "year")

    # units
    unit_pattern = r"(?<![\w])(" + dec + r")\s*(%s)(?![\w])" % \
                   "|".join(sorted(_UNITS, key=len, reverse=True))
    text = sub(unit_pattern,
               lambda m: " %s %s " % (_number_words(m.group(1)),
                                      _UNITS[m.group(2).lower()]),
               "unit", flags=re.IGNORECASE)

    # decimals
    text = sub(r"(?<![\w.])(" + num + r")\.(\d+)(?![\d.])",
               lambda m: " %s " % say_decimal(int(m.group(1).replace(",", "")),
                                              m.group(2)),
               "decimal")

    # Everything else that is still digits. The lookarounds keep part numbers
    # intact: "SKU-4200X" must survive this pass untouched, because a client
    # who hears "S K U four thousand two hundred X" stops listening.
    text = sub(r"(?<![\w.-])(" + num + r")(?!\w)",
               lambda m: " %s " % say_integer(m.group(1).replace(",", "")),
               "integer")

    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    # expansions can start a sentence with a lower-case word ("No. 1" ->
    # "number one"); TTS does not care but the sidecar is read by humans.
    # Only touch the very start when the source was capitalised, so an [emph]
    # span lifted out of the middle of a sentence is not given a false capital.
    anchors = r"(^\s*|[.!?]\s+)" if re.match(r"\s*[A-Z]", original) else r"([.!?]\s+)"
    text = re.sub(anchors + r"([a-z])",
                  lambda m: m.group(1) + m.group(2).upper(), text)
    return text.strip() if text.strip() else original


def normalize_other(text, family, log):
    """Symbol-only pass for non-English. Digits stay for the engine to read.

    Piper phonemises through espeak-ng and macOS `say` has its own reader; both
    speak bare digits in the target language correctly. Symbols are the part
    they get wrong, and symbols are the part we can fix without knowing the
    language's number grammar. Anything not in the table is left alone -- a
    wrong expansion in a language nobody on the team reads is worse than none.
    """
    table = SYMBOL_WORDS.get(family)
    if not table:
        log.append({"rule": "passthrough", "from": family,
                    "to": "no symbol table for this language; text left as written"})
        return text

    def swap(pattern, replacement, kind):
        before = text
        after = re.sub(pattern, replacement, before)
        if after != before:
            log.append({"rule": kind, "from": before.strip(), "to": after.strip()})
        return after

    if "percent" in table:
        text = swap(r"(\d[\d,]*(?:[.,]\d+)?)\s*%", r"\1 " + table["percent"], "percent")
        text = swap(r"%", " " + table["percent"], "percent")
    if "inr" in table:
        # the currency word follows the number in every language in this table
        text = swap(r"₹\s*(\d[\d,]*(?:[.,]\d+)?)", r"\1 " + table["inr"], "currency")
        text = swap(r"₹", " " + table["inr"], "currency")
    if "and" in table:
        text = swap(r"\s*&\s*", " " + table["and"] + " ", "conjunction")
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def normalize_for_speech(text, family):
    """Returns (normalised text, list of substitutions applied)."""
    log = []
    if family == "en":
        return normalize_english(text, log), log
    return normalize_other(text, family, log), log


# ---------------------------------------------------------------------------
# SSML-LITE
# ---------------------------------------------------------------------------

_PAUSE_RE = re.compile(r"\[pause\s+(\d{1,5})\s*(?:ms)?\]", re.IGNORECASE)
_EMPH_OPEN = re.compile(r"\[emph\]", re.IGNORECASE)
_EMPH_CLOSE = re.compile(r"\[/emph\]", re.IGNORECASE)
_ANY_MARKER = re.compile(r"\[(?:pause\s+\d+\s*(?:ms)?|emph|/emph)\]", re.IGNORECASE)


def strip_markers(text):
    return _ANY_MARKER.sub(" ", text)


def parse_segments(text, ssml_lite):
    """Split the script into text and pause segments.

    Paragraph breaks always become pauses -- that is ordinary prose punctuation,
    not markup. [pause N] and [emph]...[/emph] are only honoured with
    --ssml-lite; without it the markers are stripped so they can never be read
    aloud, which is the failure mode that ends up in a client's inbox.
    """
    segments = []

    def push_text(chunk, emph):
        chunk = chunk.strip()
        if chunk:
            segments.append({"type": "text", "text": chunk, "emph": emph})

    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    for index, para in enumerate(paragraphs):
        if index:
            segments.append({"type": "pause", "ms": PARAGRAPH_GAP_MS,
                             "source": "paragraph"})
        para = re.sub(r"\s*\n\s*", " ", para)
        if not ssml_lite:
            push_text(strip_markers(para), False)
            continue

        cursor = 0
        emph = False
        while cursor < len(para):
            pause = _PAUSE_RE.search(para, cursor)
            open_m = _EMPH_OPEN.search(para, cursor)
            close_m = _EMPH_CLOSE.search(para, cursor)
            candidates = [m for m in (pause, open_m, close_m) if m]
            if not candidates:
                push_text(para[cursor:], emph)
                break
            first = min(candidates, key=lambda m: m.start())
            push_text(para[cursor:first.start()], emph)
            if first is pause:
                segments.append({"type": "pause", "ms": int(first.group(1)),
                                 "source": "marker"})
            elif first is open_m:
                emph = True
            else:
                emph = False
            cursor = first.end()
    return segments


# ---------------------------------------------------------------------------
# engine discovery
# ---------------------------------------------------------------------------

def normalize_lang(value):
    """'hi', 'hi_IN', 'hi-IN', 'Hindi' -> family code."""
    if not value:
        return None
    text = str(value).strip().replace("-", "_")
    family = text.split("_")[0].lower()
    family = LANG_ALIASES.get(family, family)
    if len(family) > 3:
        lowered = family.lower()
        for code, (_voice, name) in PIPER_DEFAULTS.items():
            if name.lower() == lowered:
                return code
        for code, name in SAY_ONLY_NAMES.items():
            if name.lower() == lowered:
                return code
        raise VoiceError("could not read %r as a language code. Use an ISO code "
                         "like hi, ta, en_IN." % value)
    return family


def split_voice_id(voice_id):
    """'hi_IN-pratham-medium' -> (family, locale, name, quality)."""
    if "-" not in voice_id:
        raise VoiceError(
            "%r is not a Piper voice id. They look like hi_IN-pratham-medium." % voice_id)
    locale, rest = voice_id.split("-", 1)
    if "-" not in rest:
        raise VoiceError(
            "%r is not a Piper voice id. They look like hi_IN-pratham-medium." % voice_id)
    name, quality = rest.rsplit("-", 1)
    family = LANG_ALIASES.get(locale.split("_")[0].lower(),
                              locale.split("_")[0].lower())
    return family, locale, name, quality


def installed_piper_voices():
    """Voice ids with both an .onnx and its .onnx.json present."""
    if not os.path.isdir(VOICES_DIR):
        return []
    found = []
    for entry in sorted(os.listdir(VOICES_DIR)):
        if not entry.endswith(".onnx"):
            continue
        voice_id = entry[:-5]
        if os.path.isfile(os.path.join(VOICES_DIR, entry + ".json")):
            found.append(voice_id)
    return found


def piper_model_path(voice_id):
    return os.path.join(VOICES_DIR, voice_id + ".onnx")


def pick_piper_voice(family, installed, locale_hint=None):
    """Deterministic choice among installed models for a language.

    An explicit locale wins (--lang en_IN should not hand back a British voice),
    then the curated default for the language, then quality, then the name --
    so the same request always resolves to the same model.
    """
    matches = []
    for voice_id in installed:
        try:
            fam, locale, _name, quality = split_voice_id(voice_id)
        except VoiceError:
            continue
        if fam != family:
            continue
        rank = {"medium": 0, "high": 1, "low": 2, "x_low": 3}.get(quality, 9)
        matches.append((locale, rank, voice_id))
    if not matches:
        return None
    if locale_hint:
        exact = [m for m in matches if m[0].lower() == locale_hint.lower()]
        if exact:
            return sorted(exact, key=lambda m: (m[1], m[2]))[0][2]
    preferred = PIPER_DEFAULTS.get(family, (None, None))[0]
    if preferred and any(m[2] == preferred for m in matches):
        return preferred
    return sorted(matches, key=lambda m: (m[1], m[2]))[0][2]


def say_binary():
    return shutil.which("say")


_SAY_RE = re.compile(r"^(?P<name>.+?)\s+(?P<locale>[a-z]{2,3}(?:_[A-Za-z0-9]{2,8})?)\s+#")


def is_novelty(name):
    """`say -v '?'` lists character voices as 'Flo (Italian (Italy))'."""
    return name.split(" (")[0].strip() in SAY_NOVELTY


def say_voices():
    """[{name, locale, family}] from `say -v '?'`. Empty list off macOS."""
    binary = say_binary()
    if not binary:
        return []
    try:
        proc = subprocess.run([binary, "-v", "?"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        match = _SAY_RE.match(line.rstrip())
        if not match:
            continue
        locale = match.group("locale")
        family = LANG_ALIASES.get(locale.split("_")[0].lower(),
                                  locale.split("_")[0].lower())
        out.append({"name": match.group("name").strip(),
                    "locale": locale, "family": family})
    return out


def pick_say_voice(family, voices, brand_voice=None, locale_hint=None):
    candidates = [v for v in voices
                  if v["family"] == family and not is_novelty(v["name"])]
    if not candidates:
        return None
    if locale_hint:
        # an explicit locale outranks the brand's default voice: asking for
        # en_IN and getting Samantha is the wrong answer, however house-standard
        # Samantha is
        exact = [v for v in candidates if v["locale"].lower() == locale_hint.lower()]
        if exact:
            candidates = exact
    names = set(v["name"] for v in candidates)
    if brand_voice and brand_voice in names:
        return brand_voice
    locales = [locale_hint] if locale_hint else []
    locales += sorted(set(v["locale"] for v in candidates))
    for locale in locales:
        for preferred in SAY_PREFERRED.get(locale, []):
            if preferred in names:
                return preferred
    return sorted(names)[0]


# ---------------------------------------------------------------------------
# model installation
# ---------------------------------------------------------------------------

def load_index(allow_network=True):
    """The rhasspy/piper-voices index, cached locally for a week."""
    if os.path.isfile(INDEX_CACHE):
        age = time.time() - os.path.getmtime(INDEX_CACHE)
        if age < INDEX_MAX_AGE_SEC:
            try:
                with open(INDEX_CACHE) as handle:
                    return json.load(handle)
            except ValueError:
                pass
    if not allow_network:
        return None
    try:
        from urllib.request import urlopen
        response = urlopen(HF_INDEX_URL + "?download=true", timeout=45)
        payload = response.read().decode("utf-8")
        data = json.loads(payload)
    except Exception:
        if os.path.isfile(INDEX_CACHE):
            try:
                with open(INDEX_CACHE) as handle:
                    return json.load(handle)
            except ValueError:
                return None
        return None
    if not os.path.isdir(VOICES_DIR):
        os.makedirs(VOICES_DIR)
    with open(INDEX_CACHE, "w") as handle:
        json.dump(data, handle)
    return data


def resolve_install_target(name, index):
    """Accept a voice id or a bare language code. Returns (voice_id, entry|None)."""
    if index and name in index:
        return name, index[name]
    if "-" in name:
        if index:
            raise VoiceError(
                "no Piper voice called %r. Run --list-voices for what is installed, "
                "or browse huggingface.co/rhasspy/piper-voices." % name)
        return name, None
    family = normalize_lang(name)
    if index:
        matches = [(v.get("quality"), k) for k, v in index.items()
                   if v.get("language", {}).get("family") == family]
        if matches:
            # the curated pick wins when it is still in the index, so
            # --install-voice hi and pick_piper_voice agree on the same model
            preferred = PIPER_DEFAULTS.get(family, (None, None))[0]
            if preferred and any(k == preferred for _q, k in matches):
                return preferred, index[preferred]
            rank = {"medium": 0, "high": 1, "low": 2, "x_low": 3}
            best = sorted(matches, key=lambda t: (rank.get(t[0], 9), t[1]))[0][1]
            return best, index[best]
    if family in PIPER_DEFAULTS:
        return PIPER_DEFAULTS[family][0], None
    raise VoiceError(
        "Piper has no voice for language %r.%s" % (
            family,
            (" macOS `say` does though -- run with --engine say --lang %s." % family)
            if family in SAY_ONLY_NAMES else ""))


def _download(url, dest, label, expect_bytes=None, expect_md5=None):
    from urllib.request import urlopen
    temp = dest + ".part"
    digest = hashlib.md5()
    got = 0
    last = 0.0
    response = urlopen(url, timeout=120)
    with open(temp, "wb") as handle:
        while True:
            chunk = response.read(262144)
            if not chunk:
                break
            handle.write(chunk)
            digest.update(chunk)
            got += len(chunk)
            now = time.time()
            if expect_bytes and (now - last > 0.4):
                last = now
                sys.stderr.write("\r  %s  %5.1f%%  %.1f/%.1f MB"
                                 % (label, 100.0 * got / expect_bytes,
                                    got / 1048576.0, expect_bytes / 1048576.0))
                sys.stderr.flush()
    if expect_bytes:
        sys.stderr.write("\r  %s  100.0%%  %.1f MB          \n"
                         % (label, got / 1048576.0))
    if expect_md5 and digest.hexdigest() != expect_md5:
        os.remove(temp)
        raise VoiceError("%s downloaded but the checksum did not match. "
                         "Retry, or the mirror is serving a bad file." % label)
    os.rename(temp, dest)
    return got


def install_voice(name, quiet=False):
    """Fetch a Piper model into VOICES_DIR. Explicit user action only."""
    index = load_index()
    voice_id, entry = resolve_install_target(name, index)
    family, locale, voice_name, quality = split_voice_id(voice_id)

    onnx = piper_model_path(voice_id)
    config = onnx + ".json"
    if os.path.isfile(onnx) and os.path.isfile(config):
        print("%s is already installed at %s" % (voice_id, onnx))
        return voice_id

    sizes = {}
    checksums = {}
    if entry:
        for path, meta in (entry.get("files") or {}).items():
            if path.endswith(".onnx"):
                sizes[".onnx"] = meta.get("size_bytes")
                checksums[".onnx"] = meta.get("md5_digest")
            elif path.endswith(".onnx.json"):
                sizes[".onnx.json"] = meta.get("size_bytes")
                checksums[".onnx.json"] = meta.get("md5_digest")

    total_mb = (sizes.get(".onnx") or 63000000) / 1048576.0
    if not os.path.isdir(VOICES_DIR):
        os.makedirs(VOICES_DIR)
    if not quiet:
        sys.stderr.write(
            "Installing Piper voice %s (%s)\n"
            "  source  huggingface.co/rhasspy/piper-voices\n"
            "  size    about %.0f MB -- this is a one-time download per voice\n"
            "  into    %s\n" % (voice_id, PIPER_DEFAULTS.get(family, ("", family))[1],
                                total_mb, VOICES_DIR))

    for ext in (".onnx.json", ".onnx"):
        dest = onnx + ".json" if ext == ".onnx.json" else onnx
        if os.path.isfile(dest):
            continue
        url = HF_FILE_URL.format(family=family, locale=locale, name=voice_name,
                                 quality=quality, voice=voice_id, ext=ext) \
            + "?download=true"
        try:
            _download(url, dest, voice_id + ext, sizes.get(ext), checksums.get(ext))
        except VoiceError:
            raise
        except Exception as exc:
            raise VoiceError(
                "could not download %s: %s\nURL: %s" % (voice_id + ext, exc, url))
    if not quiet:
        print("installed %s -> %s" % (voice_id, onnx))
    return voice_id


# ---------------------------------------------------------------------------
# synthesis
# ---------------------------------------------------------------------------

def which(name):
    found = shutil.which(name)
    if not found:
        raise VoiceError("%s is not on PATH. Install it (brew install ffmpeg)." % name)
    return found


def run(cmd, label, timeout=900):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout)
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-10:]
        raise VoiceError("%s failed (exit %d):\n  %s"
                         % (label, proc.returncode, "\n  ".join(tail)))
    return proc


def read_wav_mono(path):
    handle = wave.open(path, "rb")
    try:
        frames = handle.getnframes()
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        raw = handle.readframes(frames)
    finally:
        handle.close()
    if width != 2:
        raise VoiceError("expected 16-bit PCM at %s, got %d-bit" % (path, width * 8))
    data = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, rate


def write_wav_mono(path, data, rate):
    pcm = np.round(np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2")
    handle = wave.open(path, "wb")
    try:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())
    finally:
        handle.close()


class PiperEngine(object):
    """Neural TTS. One model load per run, reused across segments."""

    name = "piper"

    def __init__(self, voice_id):
        try:
            from piper import PiperVoice
        except ImportError:
            raise VoiceError(
                "piper-tts is not installed in this interpreter.\n"
                "  ~/.cache/brand-studio/venv/bin/python -m pip install piper-tts")
        path = piper_model_path(voice_id)
        if not os.path.isfile(path):
            raise VoiceError("Piper model missing at %s" % path)
        self.voice_id = voice_id
        self.voice = PiperVoice.load(path)
        try:
            from piper import SynthesisConfig
            self._config_cls = SynthesisConfig
        except ImportError:
            self._config_cls = None

    def synthesize(self, text, dest_wav, wpm, emph):
        length_scale = PIPER_NATURAL_WPM / max(float(wpm), 60.0)
        if emph:
            length_scale *= EMPH_SLOWDOWN
        length_scale = min(1.8, max(0.55, length_scale))
        config = None
        if self._config_cls is not None:
            config = self._config_cls(length_scale=length_scale,
                                      volume=EMPH_GAIN if emph else 1.0)
        handle = wave.open(dest_wav, "wb")
        try:
            if config is not None:
                self.voice.synthesize_wav(text, handle, syn_config=config)
            else:
                self.voice.synthesize_wav(text, handle)
        finally:
            handle.close()


class SayEngine(object):
    """macOS `say`. Writes AIFF, which ffmpeg converts on the way in."""

    name = "say"

    def __init__(self, voice_name):
        self.binary = say_binary()
        if not self.binary:
            raise VoiceError("/usr/bin/say was not found; this engine is macOS only.")
        self.voice_id = voice_name

    def synthesize(self, text, dest_wav, wpm, emph):
        rate = int(round(float(wpm) * (1.0 / EMPH_SLOWDOWN if emph else 1.0)))
        spoken = ("[[pbas %s]] %s" % (EMPH_SAY_PITCH, text)) if emph else text
        aiff = os.path.splitext(dest_wav)[0] + ".aiff"
        cmd = [self.binary]
        if self.voice_id:
            cmd += ["-v", self.voice_id]
        cmd += ["-r", str(max(60, min(400, rate))), "-o", aiff, "--", spoken]
        run(cmd, "say")
        if not os.path.isfile(aiff) or os.path.getsize(aiff) < 128:
            raise VoiceError("`say` produced no audio for a segment. "
                             "Check the voice name with --list-voices.")
        run([which("ffmpeg"), "-nostdin", "-hide_banner", "-y", "-i", aiff,
             "-ac", "1", "-c:a", "pcm_s16le", dest_wav], "ffmpeg (say convert)")
        os.remove(aiff)


def to_work_rate(ffmpeg, src, dest):
    run([ffmpeg, "-nostdin", "-hide_banner", "-y", "-i", src,
         "-ar", str(WORK_SR), "-ac", "1", "-c:a", "pcm_s16le", dest],
        "ffmpeg (resample)")


def trim_silence(data, rate, lead=None, tail=None):
    """Trim leading/trailing silence, leaving a short, deliberate head and tail."""
    lead = LEAD_SILENCE_SEC if lead is None else lead
    tail = TAIL_SILENCE_SEC if tail is None else tail
    if data.size == 0:
        return data, 0.0, 0.0
    window = max(1, int(0.010 * rate))
    usable = (data.size // window) * window
    if usable < window:
        return data, 0.0, 0.0
    frames = data[:usable].reshape(-1, window)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    peak = float(np.max(rms))
    if peak <= 1e-9:
        return data, 0.0, 0.0
    floor = max(10.0 ** (SILENCE_FLOOR_DB / 20.0), peak * 10.0 ** (-40.0 / 20.0))
    loud = np.nonzero(rms >= floor)[0]
    if loud.size == 0:
        return data, 0.0, 0.0
    start = max(0, loud[0] * window - int(lead * rate))
    end = min(data.size, (loud[-1] + 1) * window + int(tail * rate))
    return data[start:end], start / float(rate), (data.size - end) / float(rate)


def measure_loudness(ffmpeg, path, target_i, target_tp):
    proc = subprocess.run(
        [ffmpeg, "-nostdin", "-hide_banner", "-i", path,
         "-af", "loudnorm=I=%0.2f:TP=%0.2f:LRA=%0.2f:print_format=json"
                % (target_i, target_tp, LOUDNORM_LRA),
         "-f", "null", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
    text = proc.stderr.decode("utf-8", "replace")
    start = text.rfind("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


def loudnorm_filter(target_i, target_tp, measured):
    base = "loudnorm=I=%0.2f:TP=%0.2f:LRA=%0.2f" % (target_i, target_tp, LOUDNORM_LRA)
    if not measured:
        return base
    try:
        return (base + ":measured_I=%s:measured_TP=%s:measured_LRA=%s"
                       ":measured_thresh=%s:offset=%s:linear=true"
                % (measured["input_i"], measured["input_tp"], measured["input_lra"],
                   measured["input_thresh"], measured["target_offset"]))
    except KeyError:
        return base


def probe_duration(ffprobe, path):
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    try:
        return float(proc.stdout.decode("utf-8", "replace").strip())
    except ValueError:
        return None


def encode(ffmpeg, src, dest, target_i, measured):
    ext = os.path.splitext(dest)[1].lower()
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-y", "-i", src,
           "-af", loudnorm_filter(target_i, TARGET_TRUE_PEAK_DB, measured),
           "-ar", str(WORK_SR), "-ac", "1", "-map_metadata", "-1"]
    if ext in (".wav", ""):
        cmd += ["-c:a", "pcm_s16le"]
    elif ext in (".m4a", ".aac", ".mp4"):
        cmd += ["-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"]
    elif ext == ".mp3":
        cmd += ["-c:a", "libmp3lame", "-b:a", "160k"]
    elif ext == ".flac":
        cmd += ["-c:a", "flac"]
    else:
        raise VoiceError("unsupported output extension %r. Use .wav, .m4a, .mp3 or .flac."
                         % ext)
    cmd.append(dest)
    run(cmd, "ffmpeg (master)")


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

def word_count(text):
    return len([w for w in re.split(r"\s+", strip_markers(text).strip()) if w])


def print_voice_inventory():
    installed = installed_piper_voices()
    voices = say_voices()

    print("PIPER  (neural, local, default engine)")
    print("  models directory: %s" % VOICES_DIR)
    if installed:
        for voice_id in installed:
            family, locale, name, quality = split_voice_id(voice_id)
            size = os.path.getsize(piper_model_path(voice_id)) / 1048576.0
            print("    %-32s %-6s %-8s %5.0f MB  %s"
                  % (voice_id, locale, quality, size,
                     PIPER_DEFAULTS.get(family, ("", family))[1]))
    else:
        print("    (none installed)")
    missing = sorted(set(PIPER_DEFAULTS) - set(
        split_voice_id(v)[0] for v in installed))
    print("  installable without leaving Piper (%d more languages):" % len(missing))
    print("    %s" % " ".join(missing))
    print("  install one with:  make_voice.py --install-voice hi_IN-pratham-medium")
    print("                or:  make_voice.py --install-voice hi")

    print("")
    print("SAY  (macOS built-in, fallback -- instant, lower quality)")
    if not voices:
        print("    (not available; `say` is macOS only)")
    else:
        families = {}
        for voice in voices:
            if is_novelty(voice["name"]):
                continue
            families.setdefault(voice["family"], []).append(voice["name"])
        print("    %d usable voices across %d languages"
              % (sum(len(v) for v in families.values()), len(families)))
        for family in sorted(families):
            tag = ""
            if family in SAY_ONLY_NAMES:
                tag = "  <- %s: no Piper model exists, `say` is the only option" \
                      % SAY_ONLY_NAMES[family]
            names = sorted(set(families[family]))
            print("    %-4s %s%s" % (family, ", ".join(names[:6])
                                     + (" ..." if len(names) > 6 else ""), tag))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def parse_args(argv):
    p = argparse.ArgumentParser(
        prog="make_voice.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Synthesise brand-film narration. Piper (neural, local, 49 "
                    "languages) by default; macOS `say` as a fallback that adds "
                    "Tamil, Kannada, Japanese, Thai, Malay, Croatian, Lithuanian.")
    p.add_argument("--text", help="the line to speak")
    p.add_argument("--text-file", help="read the script from a file (UTF-8)")
    p.add_argument("--brand", help="brand id; reads video.voiceover for voice/rate/LUFS")
    p.add_argument("--out", help="output .wav (default), .m4a, .mp3 or .flac")
    p.add_argument("--engine", choices=["auto", "piper", "say"], default="auto")
    p.add_argument("--lang", help="ISO code: hi, ta, en_IN. Default en.")
    p.add_argument("--voice", help="explicit voice: a Piper voice id or a `say` name")
    p.add_argument("--rate", type=float,
                   help="words per minute (default: brand video.voiceover.rateWpm)")
    p.add_argument("--lufs", type=float,
                   help="integrated loudness target (default: brand targetLufs)")
    p.add_argument("--pause-scale", type=float, default=1.0,
                   help="multiply every inserted pause; 1.3 for a slower read")
    p.add_argument("--ssml-lite", action="store_true",
                   help="honour [pause 400] and [emph]...[/emph] markers")
    p.add_argument("--allow-download", action="store_true",
                   help="permit fetching a missing Piper model mid-run "
                        "(nightly runs must not; they fail with the install command)")
    p.add_argument("--list-voices", action="store_true")
    p.add_argument("--install-voice", metavar="NAME",
                   help="download a Piper voice id, or the default for a language code")
    p.add_argument("--json", action="store_true", help="print the sidecar to stdout")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if args.list_voices:
        print_voice_inventory()
        return 0
    if args.install_voice:
        install_voice(args.install_voice)
        return 0

    # ---- input -----------------------------------------------------------
    if args.text and args.text_file:
        raise VoiceError("give --text or --text-file, not both")
    if args.text_file:
        if not os.path.isfile(args.text_file):
            raise VoiceError("no such file: %s" % args.text_file)
        with open(args.text_file, "rb") as handle:
            text = handle.read().decode("utf-8")
    elif args.text:
        text = args.text
    else:
        raise VoiceError("nothing to say: pass --text or --text-file")
    text = unicodedata.normalize("NFC", text).strip()
    if not text:
        raise VoiceError("the script is empty")
    if not args.out:
        raise VoiceError("--out is required")
    out = os.path.abspath(args.out)

    ffmpeg = which("ffmpeg")
    ffprobe = which("ffprobe")

    # ---- brand -----------------------------------------------------------
    brand_id = args.brand
    if not brand_id:
        try:
            brands = brandlib.list_brands()
            brand_id = brands[0]["id"] if len(brands) == 1 else None
        except Exception:
            brand_id = None
    vo_cfg = {}
    if brand_id:
        try:
            brand = brandlib.load_brand(brand_id)
        except brandlib.BrandNotFound as exc:
            raise VoiceError(str(exc))
        vo_cfg = ((brand.get("video") or {}).get("voiceover") or {})
        brand_id = brand.get("id", brand_id)

    def cfg_float(key, fallback):
        try:
            return float(vo_cfg.get(key, fallback))
        except (TypeError, ValueError):
            return fallback

    wpm = float(args.rate) if args.rate else cfg_float("rateWpm", 165.0)
    if wpm < 60.0 or wpm > 400.0:
        raise VoiceError("--rate must be between 60 and 400 wpm; got %.0f" % wpm)
    target_lufs = args.lufs if args.lufs is not None else \
        cfg_float("targetLufs", DEFAULT_TARGET_LUFS)
    pause_scale = max(0.0, float(args.pause_scale))

    family = normalize_lang(args.lang) if args.lang else "en"
    locale_hint = None
    if args.lang and "_" in str(args.lang).replace("-", "_"):
        locale_hint = str(args.lang).replace("-", "_")

    # ---- engine ----------------------------------------------------------
    installed = installed_piper_voices()
    say_list = say_voices()
    engine_choice = args.engine
    piper_voice = None
    say_voice = None
    engine_reason = ""

    if args.voice:
        if os.path.isfile(piper_model_path(args.voice)):
            engine_choice, piper_voice = "piper", args.voice
            engine_reason = "--voice names an installed Piper model"
        elif any(v["name"] == args.voice for v in say_list):
            engine_choice, say_voice = "say", args.voice
            engine_reason = "--voice names a macOS `say` voice"
            match = [v for v in say_list if v["name"] == args.voice][0]
            if not args.lang:
                family, locale_hint = match["family"], match["locale"]
        elif engine_choice == "piper" or "-" in args.voice:
            if not args.allow_download:
                raise VoiceError(
                    "Piper voice %r is not installed.\n"
                    "  install it:  make_voice.py --install-voice %s\n"
                    "  or re-run with --allow-download to fetch it now "
                    "(about 63 MB)." % (args.voice, args.voice))
            piper_voice = install_voice(args.voice, quiet=False)
            engine_choice = "piper"
            engine_reason = "--voice downloaded on request"
            family = split_voice_id(piper_voice)[0]
        else:
            raise VoiceError(
                "no voice called %r. Run --list-voices to see what is available."
                % args.voice)

    if engine_choice in ("auto", "piper") and not piper_voice and not say_voice:
        piper_voice = pick_piper_voice(family, installed, locale_hint)
        say_has_locale = bool(locale_hint) and any(
            v["locale"].lower() == locale_hint.lower() and not is_novelty(v["name"])
            for v in say_list)
        if (piper_voice and args.engine == "auto" and say_has_locale
                and split_voice_id(piper_voice)[1].lower() != locale_hint.lower()):
            # Piper has no en_IN model at all. Serving an Indian-English request
            # with an American neural voice is a worse answer than serving it
            # with the right accent from a lesser engine, so the locale wins.
            piper_voice = None
            engine_choice = "say"
            engine_reason = ("no Piper model for locale %s; macOS `say` has the "
                             "right accent and accent beats engine here"
                             % locale_hint)
        elif piper_voice:
            engine_choice = "piper"
            engine_reason = engine_reason or \
                "a Piper model for %r is installed" % family
        elif engine_choice == "piper" or (args.allow_download and
                                          family in PIPER_DEFAULTS):
            target = PIPER_DEFAULTS.get(family, (None, None))[0]
            if not target:
                raise VoiceError(
                    "Piper has no model for language %r.%s" % (
                        family,
                        " macOS `say` does -- re-run with --engine say."
                        if family in SAY_ONLY_NAMES else ""))
            if not args.allow_download:
                raise VoiceError(
                    "no Piper model for %r is installed.\n"
                    "  install it:  make_voice.py --install-voice %s\n"
                    "  (about 63 MB, one time, stored in %s)\n"
                    "  or re-run with --allow-download, or --engine say for the "
                    "built-in macOS voice." % (family, target, VOICES_DIR))
            piper_voice = install_voice(target)
            engine_choice = "piper"
            engine_reason = "downloaded %s on request" % target

    if engine_choice in ("auto", "say") and not piper_voice:
        say_voice = say_voice or pick_say_voice(
            family, say_list, vo_cfg.get("voice") if family == "en" else None,
            locale_hint)
        if say_voice:
            engine_choice = "say"
            engine_reason = engine_reason or (
                "no Piper model for %r; macOS `say` has one" % family
                if args.engine == "auto" else "requested with --engine say")

    if engine_choice == "piper" and piper_voice:
        engine = PiperEngine(piper_voice)
        voice_label = piper_voice
    elif engine_choice == "say" and say_voice:
        engine = SayEngine(say_voice)
        voice_label = say_voice
    else:
        install_hint = PIPER_DEFAULTS.get(family)
        lines = ["cannot speak language %r with any available engine." % family]
        if install_hint:
            lines.append("  Piper has one: make_voice.py --install-voice %s"
                         % install_hint[0])
        elif family in SAY_ONLY_NAMES:
            lines.append("  macOS `say` has one but no voice is installed for it; "
                         "add it in System Settings > Accessibility > Spoken Content.")
        else:
            lines.append("  Neither Piper (49 languages) nor macOS `say` (40) "
                         "covers it. Record this one with a human.")
        lines.append("  See what is available: make_voice.py --list-voices")
        raise VoiceError("\n".join(lines))

    # ---- normalise + segment --------------------------------------------
    segments = parse_segments(text, args.ssml_lite)
    if not any(s["type"] == "text" for s in segments):
        raise VoiceError("after stripping markers there are no words left to speak")

    substitutions = []
    for segment in segments:
        if segment["type"] != "text":
            continue
        spoken, log = normalize_for_speech(segment["text"], family)
        segment["spoken"] = spoken
        substitutions.extend(log)

    normalized_text = " ".join(
        s["spoken"] if s["type"] == "text" else "..." for s in segments).strip()

    # ---- synthesise ------------------------------------------------------
    if not os.path.isdir(WORK_DIR):
        os.makedirs(WORK_DIR)
    stamp = hashlib.md5(("%s|%s|%s" % (out, voice_label, time.time()))
                        .encode("utf-8")).hexdigest()[:10]
    pieces = []
    timeline = []
    rate = WORK_SR
    cursor = 0.0
    try:
        for index, segment in enumerate(segments):
            if segment["type"] == "pause":
                seconds = (segment["ms"] * pause_scale) / 1000.0
                if seconds <= 0.0:
                    continue
                pieces.append(np.zeros(int(round(seconds * rate)), dtype=np.float64))
                timeline.append({"index": index, "type": "pause",
                                 "source": segment.get("source"),
                                 "startSec": round(cursor, 3),
                                 "endSec": round(cursor + seconds, 3)})
                cursor += seconds
                continue
            raw = os.path.join(WORK_DIR, "vo_%s_%03d_raw.wav" % (stamp, index))
            resampled = os.path.join(WORK_DIR, "vo_%s_%03d.wav" % (stamp, index))
            engine.synthesize(segment["spoken"], raw, wpm, segment.get("emph"))
            if not os.path.isfile(raw) or os.path.getsize(raw) < 128:
                raise VoiceError("the engine produced no audio for segment %d: %r"
                                 % (index, segment["spoken"][:60]))
            to_work_rate(ffmpeg, raw, resampled)
            data, rate = read_wav_mono(resampled)
            # Both engines pad every utterance with 50-150 ms of silence. Left
            # in, an inserted [pause 400] becomes 700 ms and an [emph] span
            # leaves an audible hole. Tighten each segment so the pause the
            # author asked for is the pause they get.
            data = trim_silence(data, rate, SEGMENT_EDGE_SEC, SEGMENT_TAIL_SEC)[0]
            pieces.append(data)
            seconds = data.size / float(rate)
            timeline.append({"index": index, "type": "text",
                             "text": segment["text"], "spoken": segment["spoken"],
                             "emphasis": bool(segment.get("emph")),
                             "startSec": round(cursor, 3),
                             "endSec": round(cursor + seconds, 3)})
            cursor += seconds
            for path in (raw, resampled):
                if os.path.isfile(path):
                    os.remove(path)

        assembled = np.concatenate(pieces) if pieces else np.zeros(0)
        if assembled.size == 0 or float(np.max(np.abs(assembled))) < 1e-4:
            raise VoiceError(
                "the synthesised audio is silent. The voice %r may not support "
                "language %r -- check with --list-voices." % (voice_label, family))

        trimmed, head_cut, tail_cut = trim_silence(assembled, rate)
        premaster = os.path.join(WORK_DIR, "vo_%s_premaster.wav" % stamp)
        write_wav_mono(premaster, trimmed, rate)

        measured = measure_loudness(ffmpeg, premaster, target_lufs, TARGET_TRUE_PEAK_DB)
        encode(ffmpeg, premaster, out, target_lufs, measured)
        os.remove(premaster)
    finally:
        for leftover in sorted(os.listdir(WORK_DIR)):
            if leftover.startswith("vo_%s" % stamp):
                try:
                    os.remove(os.path.join(WORK_DIR, leftover))
                except OSError:
                    pass

    # ---- report ----------------------------------------------------------
    duration = probe_duration(ffprobe, out)
    final = measure_loudness(ffmpeg, out, target_lufs, TARGET_TRUE_PEAK_DB) or {}

    def _f(key):
        try:
            return round(float(final[key]), 2)
        except (KeyError, TypeError, ValueError):
            return None

    # pacing is a function of the words that were SPOKEN, not the words that
    # were written -- "4,200" is one written token and four spoken ones
    written_words = word_count(text)
    words = word_count(normalized_text)
    effective_wpm = round(words / duration * 60.0, 1) if duration else None
    language_name = PIPER_DEFAULTS.get(family, (None, None))[1] or \
        SAY_ONLY_NAMES.get(family, family)

    # shift the timeline by whatever the silence trim removed, so the video
    # builder can line captions up against the real file
    if head_cut:
        for entry in timeline:
            entry["startSec"] = round(max(0.0, entry["startSec"] - head_cut), 3)
            entry["endSec"] = round(max(0.0, entry["endSec"] - head_cut), 3)

    sidecar = {
        "generator": "brand-studio/scripts/make_voice.py",
        "brand": brand_id,
        "engine": engine.name,
        "engineReason": engine_reason,
        "voice": voice_label,
        "language": family,
        "languageName": language_name,
        "text": text,
        "normalizedText": normalized_text,
        "normalization": {
            "applied": "full-english" if family == "en" else (
                "symbols-only" if family in SYMBOL_WORDS else "none"),
            "substitutions": substitutions,
        },
        "ssmlLite": bool(args.ssml_lite),
        "pauseScale": pause_scale,
        "segments": timeline,
        "duration": None if duration is None else round(duration, 3),
        "wordCount": words,
        "sourceWordCount": written_words,
        "requestedWpm": round(wpm, 1),
        "effectiveWpm": effective_wpm,
        "loudness": {
            "targetLufs": round(float(target_lufs), 2),
            "measuredLufs": _f("input_i"),
            "truePeakDb": _f("input_tp"),
            "truePeakCeilingDb": TARGET_TRUE_PEAK_DB,
        },
        "trimmedSilence": {"leadSec": round(head_cut, 3), "tailSec": round(tail_cut, 3)},
        "sampleRate": WORK_SR,
        "files": {"audio": out},
        "command": "make_voice.py " + " ".join(argv if argv is not None else sys.argv[1:]),
    }
    sidecar_path = out + ".voice.json"
    with open(sidecar_path, "w") as handle:
        json.dump(sidecar, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    sidecar["files"]["sidecar"] = sidecar_path

    if args.json:
        print(json.dumps(sidecar, indent=2, ensure_ascii=False))
    else:
        print("wrote     %s" % out)
        print("sidecar   %s" % sidecar_path)
        print("engine    %s / %s  (%s)" % (engine.name, voice_label, engine_reason))
        print("language  %s (%s)" % (family, language_name))
        print("spoken    %s" % normalized_text[:300])
        print("duration  %s s, %d spoken words (%d as written), %s wpm (asked %.0f)"
              % (sidecar["duration"], words, written_words, effective_wpm, wpm))
        print("loudness  %s LUFS (target %.1f), true peak %s dBTP"
              % (sidecar["loudness"]["measuredLufs"], target_lufs,
                 sidecar["loudness"]["truePeakDb"]))
        if substitutions:
            print("normalised %d span(s):" % len(substitutions))
            for item in substitutions[:6]:
                print("  %-13s %s  ->  %s"
                      % (item["rule"], item["from"][:60], item["to"][:60]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except VoiceError as exc:
        sys.stderr.write("make_voice.py: %s\n" % exc)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.stderr.write("\ninterrupted\n")
        sys.exit(130)
