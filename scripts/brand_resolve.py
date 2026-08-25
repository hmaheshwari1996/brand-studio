#!/usr/bin/env python3
"""Resolve a human-typed brand name to a brand-studio profile, and summarise it.

The skills call this before they build anything. The point of the human output
is that a person can read it in five seconds and say "yes, that is the brand" --
so it shows the things that actually go wrong when the wrong profile is used:
the colours, the type family and its PPTX weight names, where the logo goes and
whether the logo files are really on disk, the video kit, the storyline arc,
and whatever the brand has learned since it was created.

Exit codes are the whole point of the interface:
    0   exact match  -- an id, a display name, or a registered alias
    3   fuzzy match  -- something close was found; the skill must confirm it
    4   no match     -- the skill should run brand intake (new_brand.py)
    1   internal failure or bad usage

Usage:
    brand_resolve.py channelplay
    brand_resolve.py channel play              # words are joined into one name
    brand_resolve.py "channelplay" --json
    brand_resolve.py --list
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

import brandlib  # noqa: E402

EXIT_EXACT = 0
EXIT_INTERNAL = 1
EXIT_FUZZY = 3
EXIT_NONE = 4

_HEADING_RE = re.compile(r"^(#{2,6})\s+(.*\S)\s*$")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
# Rule ids as they appear in rules.local.json and are cited in LEARNED.md.
# Matched whole, then compared as a SET -- never as a substring, so
# LOCAL.NO_SOLUTION cannot silently satisfy LOCAL.NO_SOLUTIONS.
_RULE_ID_RE = re.compile(r"\bLOCAL\.[A-Za-z0-9_]+")


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write("%s: error: %s\n" % (self.prog, message))
        raise SystemExit(EXIT_INTERNAL)


# ---------------------------------------------------------------------------
# LEARNED.md
# ---------------------------------------------------------------------------

def read_learned_entries(path, limit=3):
    # type: (str, int) -> list
    """Most recent entries from a brand's LEARNED.md.

    Understands two shapes, because both get written by hand:
      '## 2026-07-30 - Mint never carries text'  + following prose
      '- 2026-07-30  Mint never carries text'
    Entries carrying an ISO date sort newest-first; undated entries keep file
    order with the newest (last appended) first.
    """
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r") as fh:
            lines = fh.read().splitlines()
    except (IOError, OSError):
        return []

    entries = []
    current = None
    for idx, raw in enumerate(lines):
        m = _HEADING_RE.match(raw)
        if m:
            if current is not None:
                entries.append(current)
            title = m.group(2).strip()
            current = {"title": title, "date": _first_date(title), "body": [], "order": idx}
            continue
        if current is not None:
            text = raw.strip()
            if text:
                current["body"].append(text)
    if current is not None:
        entries.append(current)

    if not entries:
        # Fall back to dated bullets.
        for idx, raw in enumerate(lines):
            text = raw.strip()
            if not text.startswith(("-", "*")):
                continue
            text = text.lstrip("-*").strip()
            if not text:
                continue
            entries.append({"title": text, "date": _first_date(text), "body": [], "order": idx})

    dated = [e for e in entries if e["date"]]
    if dated and len(dated) == len(entries):
        entries.sort(key=lambda e: (e["date"], e["order"]), reverse=True)
    else:
        entries.sort(key=lambda e: e["order"], reverse=True)

    out = []
    for e in entries[:max(0, limit)]:
        summary = " ".join(e["body"])
        if len(summary) > 160:
            summary = summary[:157].rstrip() + "..."
        out.append({"title": e["title"], "date": e["date"] or "", "summary": summary})
    return out


def _first_date(text):
    # type: (str) -> str
    m = _DATE_RE.search(text or "")
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# summary assembly
# ---------------------------------------------------------------------------

def _hex_or_blank(value):
    # type: (object) -> str
    try:
        return brandlib.normalize_hex(value) if brandlib.is_hex(value) else ""
    except ValueError:
        return ""


def pick_surface(brand):
    # type: (dict) -> str
    """The light surface body text is judged against."""
    color = brand.get("color") or {}
    neutral = color.get("neutral") or {}
    for cand in (neutral.get("0"), (color.get("brand") or {}).get("surface"), "#FFFFFF"):
        h = _hex_or_blank(cand)
        if h and brandlib.is_light(h):
            return h
    return "#FFFFFF"


def brand_colors(brand):
    # type: (dict) -> dict
    """primary / secondary / accents, in the order the profile declares them."""
    palette = (brand.get("color") or {}).get("brand") or {}
    rows = []
    for name, value in palette.items():
        if str(name).startswith("$"):
            continue
        h = _hex_or_blank(value)
        if h:
            rows.append({"token": str(name), "hex": h})

    primary = None
    secondary = None
    for row in rows:
        low = row["token"].lower()
        if primary is None and low == "primary":
            primary = row
        elif secondary is None and low == "secondary":
            secondary = row
    rest = [r for r in rows if r is not primary and r is not secondary]
    if primary is None and rest:
        primary = rest.pop(0)
    if secondary is None and rest:
        secondary = rest.pop(0)
    return {"primary": primary, "secondary": secondary, "accents": rest, "all": rows}


def type_summary(brand):
    # type: (dict) -> dict
    t = brand.get("type") or {}
    mapping = t.get("weightToPptxFamily") or {}
    weights = []
    for w in t.get("approvedWeights") or sorted(mapping.keys()):
        key = str(w)
        weights.append({"weight": key, "family": str(mapping.get(key, t.get("family", "")))})
    return {
        "family": str(t.get("family", "") or ""),
        "fallback": list(t.get("fallback") or []),
        "weights": weights,
        "minBodyPt": t.get("minBodyPt"),
        "italicsAllowed": bool(t.get("italicsAllowed")),
        "syntheticBoldAllowed": bool(t.get("syntheticBoldAllowed")),
        "roles": sorted((t.get("deckScalePt") or {}).keys()),
        "unverified": bool(t.get("$unverified")),
    }


def logo_summary(brand):
    # type: (dict) -> dict
    logo = brand.get("logo") or {}
    bdir = brand.get("_dir") or ""
    variants = []
    for name, spec in (logo.get("variants") or {}).items():
        if not isinstance(spec, dict):
            continue
        rel = str(spec.get("file") or "")
        full = os.path.join(bdir, rel) if rel and not os.path.isabs(rel) else rel
        variants.append({
            "name": str(name),
            "use": str(spec.get("use") or ""),
            "file": rel,
            "path": full,
            "onDisk": bool(full) and os.path.isfile(full),
        })
    variants.sort(key=lambda v: v["name"])
    return {
        "placement": str(logo.get("placement") or ""),
        "minWidthIn": logo.get("minWidthIn"),
        "clearSpaceRatio": logo.get("clearSpaceRatio"),
        "vectorAvailable": bool(logo.get("vectorAvailable")),
        "variants": variants,
        "missing": [v["name"] for v in variants if not v["onDisk"]],
        "unverified": bool(logo.get("$unverified")),
    }


def video_summary(brand):
    # type: (dict) -> dict
    v = brand.get("video") or {}
    res = v.get("resolution") or {}
    intro = v.get("intro") or {}
    outro = v.get("outro") or {}
    music = v.get("music") or {}
    vo = v.get("voiceover") or {}
    caps = v.get("captions") or {}
    story = v.get("storyline") or {}
    return {
        "resolution": "%sx%s" % (res.get("w", "?"), res.get("h", "?")),
        "fps": v.get("fps"),
        "container": str(v.get("container") or ""),
        "vcodec": str(v.get("vcodec") or ""),
        "acodec": str(v.get("acodec") or ""),
        "intro": {"enabled": bool(intro), "type": str(intro.get("type") or ""),
                  "durationSec": intro.get("durationSec"), "style": str(intro.get("style") or ""),
                  "file": intro.get("file")},
        "outro": {"enabled": bool(outro), "type": str(outro.get("type") or ""),
                  "durationSec": outro.get("durationSec"), "style": str(outro.get("style") or ""),
                  "file": outro.get("file")},
        "music": {"enabled": bool(music.get("enabled")), "file": music.get("file"),
                  "targetLufs": music.get("targetLufs"),
                  "duckUnderVoiceDb": music.get("duckUnderVoiceDb"),
                  "mood": list(music.get("mood") or [])},
        "voiceover": {"enabled": bool(vo.get("enabled")), "engine": str(vo.get("engine") or ""),
                      "voice": str(vo.get("voice") or ""), "rateWpm": vo.get("rateWpm")},
        "captions": {"enabled": bool(caps.get("enabled")), "required": bool(caps.get("required")),
                     "burnIn": bool(caps.get("burnIn")), "sidecar": str(caps.get("sidecar") or ""),
                     "font": str(caps.get("font") or ""), "sizePt": caps.get("sizePt"),
                     "maxCharsPerLine": caps.get("maxCharsPerLine"),
                     "maxLines": caps.get("maxLines")},
        "storyline": {"required": bool(story.get("required")), "arc": list(story.get("arc") or []),
                      "rules": list(story.get("rules") or [])},
        "slideHoldSec": v.get("slideHoldSec") or {},
        "unverified": bool(v.get("$unverified")),
    }


def learned_health(md_path, rules):
    # type: (str, object) -> list
    """Cross-tier consistency, using only invariants the files themselves state.

    rules.local.json declares it in its own header: *every rule here must also
    have a dated entry in LEARNED.md explaining who asked for it and why*.
    Nothing enforced that, so a tier-2 rule could outlive the reason for it and
    the next review re-litigates a decision that was already made.

    Only SILENT failures are reported. A learned rule that contradicts
    brand.json -- a forbid_color on a palette colour, say -- fails loudly on the
    very next build and needs no check here.

    Both directions are reported, because both are silent:
      * a rule with no ledger entry -- the reason for it is lost, and the next
        review re-litigates a decision that was already made.
      * a ledger-cited rule absent from rules.local.json -- a correction the
        ledger says is enforced, which nothing enforces. Deliberately retiring a
        rule looks identical from the text, so the wording names both readings
        rather than asserting a fault.

    Display only. The caller's exit code is decided by match quality and this
    must never alter it.
    """
    notes = []
    ids = set()
    if isinstance(rules, list):
        for rule in rules:
            if isinstance(rule, dict) and rule.get("id"):
                ids.add(str(rule["id"]).strip().upper())

    if not md_path or not os.path.isfile(md_path):
        if ids:
            notes.append("%d local rule(s) but no ledger file on disk" % len(ids))
        return notes

    try:
        with open(md_path, "r") as fh:
            text = fh.read()
    except (IOError, OSError):
        return notes

    cited = set(m.group(0).upper() for m in _RULE_ID_RE.finditer(text))
    orphans = sorted(i for i in ids if i not in cited)
    if orphans:
        notes.append("no ledger entry for %s" % ", ".join(orphans))

    absent = sorted(c for c in cited if c not in ids)
    if absent:
        notes.append("ledger cites %s, absent from rules.local.json (retired, or never added?)"
                     % ", ".join(absent))

    undated = 0
    for raw in text.splitlines():
        m = _HEADING_RE.match(raw)
        if m and not _first_date(m.group(2)):
            undated += 1
    if undated:
        notes.append("%d undated ## entr%s; newest-first sorting falls back to file order"
                     % (undated, "y" if undated == 1 else "ies"))
    return notes


def learned_summary(brand):
    # type: (dict) -> dict
    learned = brand.get("learnedRules") or {}
    rules = learned.get("rules")
    if isinstance(rules, list):
        count = len(rules)
    elif isinstance(rules, dict):
        count = len(rules)
    else:
        count = len([k for k in learned.keys() if not str(k).startswith("$")])
    bdir = brand.get("_dir") or ""
    md_name = ((brand.get("learned") or {}).get("file")) or "LEARNED.md"
    md_path = os.path.join(bdir, str(md_name)) if bdir else ""
    return {
        "ruleCount": count,
        "file": md_path,
        "fileExists": bool(md_path) and os.path.isfile(md_path),
        "recent": read_learned_entries(md_path, 3),
        "health": learned_health(md_path, rules if isinstance(rules, list) else []),
    }


def build_summary(brand):
    # type: (dict) -> dict
    surface = pick_surface(brand)
    rules = brand.get("colorRules") or {}
    body_text = _hex_or_blank(rules.get("defaultText")) or ""
    secondary_text = _hex_or_blank(rules.get("secondaryText")) or ""

    def ratio(fg):
        if not fg:
            return None
        try:
            return round(brandlib.contrast_ratio(fg, surface), 2)
        except ValueError:
            return None

    unverified = []
    for section in ("color", "type", "logo", "voice", "video"):
        if isinstance(brand.get(section), dict) and brand[section].get("$unverified"):
            unverified.append(section)
    for path in (brand.get("$unverifiedFields") or []):
        if path not in unverified:
            unverified.append(str(path))

    return {
        "id": str(brand.get("id") or ""),
        "name": str(brand.get("name") or ""),
        "kind": str(brand.get("kind") or ""),
        "version": str(brand.get("version") or ""),
        "updated": str(brand.get("updated") or ""),
        "description": str(brand.get("description") or ""),
        "aliases": list(brand.get("aliases") or []),
        "dir": str(brand.get("_dir") or ""),
        "profile": os.path.join(str(brand.get("_dir") or ""), "brand.json"),
        "color": {
            "surface": surface,
            "bodyText": body_text,
            "bodyTextContrast": ratio(body_text),
            "secondaryText": secondary_text,
            "secondaryTextContrast": ratio(secondary_text),
            "supersededCount": len(brandlib.superseded_map(brand)),
            "chartSeries": [c for c in (_hex_or_blank(x) for x in (rules.get("chartSeries") or [])) if c],
            "unverified": bool((brand.get("color") or {}).get("$unverified")),
        },
        "palette": brand_colors(brand),
        "type": type_summary(brand),
        "logo": logo_summary(brand),
        "voice": {
            "case": str((brand.get("voice") or {}).get("case") or ""),
            "forbiddenChars": list((brand.get("voice") or {}).get("forbiddenChars") or []),
            "forbiddenPhraseCount": len((brand.get("voice") or {}).get("forbiddenPhrases") or []),
            "guidance": str((brand.get("voice") or {}).get("guidance") or ""),
            "unverified": bool((brand.get("voice") or {}).get("$unverified")),
        },
        "video": video_summary(brand),
        "learned": learned_summary(brand),
        "unverifiedSections": unverified,
    }


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def _row(label, value):
    # type: (str, object) -> str
    return "  %-12s %s" % (label, value)


def _fmt_num(value, suffix=""):
    # type: (object, str) -> str
    if value is None:
        return "-"
    if isinstance(value, float) and value == int(value):
        value = int(value)
    return "%s%s" % (value, suffix)


def render_human(summary, match, score, candidates):
    # type: (dict, str, float, list) -> str
    L = []
    head = "%s  (%s)" % (summary["name"] or summary["id"], summary["id"])
    L.append(head)
    L.append("=" * max(24, len(head)))
    if match == "exact":
        L.append(_row("match", "exact"))
    else:
        L.append(_row("match", "FUZZY %.0f%% - confirm before using this brand" % (score * 100)))
    if summary["kind"]:
        L.append(_row("kind", summary["kind"]))
    if summary["aliases"]:
        L.append(_row("aliases", ", ".join(summary["aliases"])))
    L.append(_row("updated", "%s   version %s" % (summary["updated"] or "-", summary["version"] or "-")))
    L.append(_row("profile", summary["profile"]))
    if summary["description"]:
        L.append(_row("about", summary["description"]))
    if summary["unverifiedSections"]:
        L.append(_row("INCOMPLETE", "unverified: %s  (validator will warn)"
                      % ", ".join(summary["unverifiedSections"])))
    L.append("")

    pal = summary["palette"]
    c = summary["color"]
    L.append("Colour")
    if pal["primary"]:
        L.append(_row("primary", "%s  %s" % (pal["primary"]["hex"], pal["primary"]["token"])))
    if pal["secondary"]:
        L.append(_row("secondary", "%s  %s" % (pal["secondary"]["hex"], pal["secondary"]["token"])))
    # The surface is already reported on the body-text line; repeating it under
    # "accents" reads as though the page background were a brand accent.
    accents = [a for a in pal["accents"]
               if a["token"].lower() not in ("surface", "background", "bg", "paper")]
    if accents:
        L.append(_row("accents", ", ".join("%s %s" % (a["token"], a["hex"]) for a in accents)))
    if c["bodyText"]:
        cr = c["bodyTextContrast"]
        verdict = "-" if cr is None else ("AA pass" if cr >= 4.5 else "AA FAIL")
        L.append(_row("body text", "%s on %s   %s:1  %s"
                      % (c["bodyText"], c["surface"], _fmt_num(cr), verdict)))
    if c["secondaryText"]:
        cr = c["secondaryTextContrast"]
        verdict = "-" if cr is None else ("AA pass" if cr >= 4.5 else "AA FAIL")
        L.append(_row("muted text", "%s on %s   %s:1  %s"
                      % (c["secondaryText"], c["surface"], _fmt_num(cr), verdict)))
    if c["chartSeries"]:
        L.append(_row("charts", " ".join(c["chartSeries"])))
    if c["supersededCount"]:
        L.append(_row("superseded", "%d template colour(s) mapped to canonical tokens"
                      % c["supersededCount"]))
    L.append("")

    t = summary["type"]
    L.append("Type")
    L.append(_row("family", t["family"] or "-"))
    if t["weights"]:
        L.append(_row("weights", ",  ".join("%s -> %s" % (w["weight"], w["family"])
                                            for w in t["weights"])))
    L.append(_row("rules", "min body %spt   italics %s   synthetic bold %s"
                  % (_fmt_num(t["minBodyPt"]),
                     "yes" if t["italicsAllowed"] else "no",
                     "yes" if t["syntheticBoldAllowed"] else "no")))
    if t["roles"]:
        L.append(_row("roles", "%d: %s" % (len(t["roles"]), ", ".join(t["roles"]))))
    L.append("")

    lg = summary["logo"]
    L.append("Logo")
    L.append(_row("placement", "%s   min width %sin   clear space %sx height"
                  % (lg["placement"] or "-", _fmt_num(lg["minWidthIn"]),
                     _fmt_num(lg["clearSpaceRatio"]))))
    if lg["variants"]:
        for i, v in enumerate(lg["variants"]):
            mark = "on disk" if v["onDisk"] else "MISSING"
            L.append("  %-12s %-11s %-46s [%s]"
                     % ("variants" if i == 0 else "", v["name"], v["file"] or "-", mark))
    else:
        L.append(_row("variants", "none declared"))
    if not lg["vectorAvailable"]:
        L.append(_row("vector", "raster only - request SVG/EPS before print or large format"))
    L.append("")

    vd = summary["video"]
    L.append("Video kit")
    L.append(_row("format", "%s @%sfps  %s (%s/%s)"
                  % (vd["resolution"], _fmt_num(vd["fps"]), vd["container"] or "-",
                     vd["vcodec"] or "-", vd["acodec"] or "-")))
    L.append(_row("intro", "%s %ss  %s" % (vd["intro"]["type"] or "-",
                                           _fmt_num(vd["intro"]["durationSec"]),
                                           vd["intro"]["style"] or "")))
    L.append(_row("outro", "%s %ss  %s" % (vd["outro"]["type"] or "-",
                                           _fmt_num(vd["outro"]["durationSec"]),
                                           vd["outro"]["style"] or "")))
    music = vd["music"]
    L.append(_row("music", "%s   %s   target %s LUFS, duck %s dB"
                  % ("on" if music["enabled"] else "off",
                     music["file"] or "no file bound",
                     _fmt_num(music["targetLufs"]), _fmt_num(music["duckUnderVoiceDb"]))))
    vo = vd["voiceover"]
    L.append(_row("voiceover", "%s   %s / %s @%swpm"
                  % ("on" if vo["enabled"] else "off", vo["engine"] or "-",
                     vo["voice"] or "-", _fmt_num(vo["rateWpm"]))))
    cap = vd["captions"]
    L.append(_row("captions", "%s   %s sidecar%s   %s %spt   %s chars x %s lines"
                  % ("required" if cap["required"] else ("on" if cap["enabled"] else "off"),
                     cap["sidecar"] or "-",
                     ", burned in" if cap["burnIn"] else "",
                     cap["font"] or "-", _fmt_num(cap["sizePt"]),
                     _fmt_num(cap["maxCharsPerLine"]), _fmt_num(cap["maxLines"]))))
    hold = vd["slideHoldSec"] or {}
    if hold:
        L.append(_row("hold", "min %ss  default %ss  max %ss"
                      % (_fmt_num(hold.get("min")), _fmt_num(hold.get("default")),
                         _fmt_num(hold.get("max")))))
    arc = vd["storyline"]["arc"]
    L.append(_row("storyline", " -> ".join(arc) if arc else "none declared"))
    L.append("")

    ln = summary["learned"]
    L.append("Learned")
    L.append(_row("rules", "%d local rule(s)" % ln["ruleCount"]))
    if ln["recent"]:
        for i, e in enumerate(ln["recent"]):
            title = e["title"]
            if e["summary"]:
                title = "%s - %s" % (title, e["summary"])
            L.append("  %-12s %s" % ("recent" if i == 0 else "", title))
    else:
        L.append(_row("recent", "no LEARNED.md entries yet"
                                if ln["fileExists"] else "no LEARNED.md on disk"))
    for i, note in enumerate(ln.get("health") or []):
        L.append("  %-12s %s" % ("DRIFT" if i == 0 else "", note))

    others = [c for c in (candidates or []) if c.get("id") != summary["id"]]
    if match != "exact" and others:
        L.append("")
        L.append("Other candidates")
        for cand in others:
            L.append("  %-12s %s  (%.0f%%)" % (cand["id"], cand["name"], cand["score"] * 100))
    L.append("")
    return "\n".join(L)


def render_no_match(query, candidates):
    # type: (str, list) -> str
    L = ["No brand matched %r." % query, ""]
    if candidates:
        L.append("Closest entries in the registry:")
        for c in candidates:
            L.append("  %-16s %-28s %.0f%%" % (c["id"], c["name"], c["score"] * 100))
        L.append("")
    L.append("Run brand intake to create it:")
    L.append("  new_brand.py --id <slug> --name %r" % query)
    L.append("")
    return "\n".join(L)


def render_list(rows):
    # type: (list) -> str
    if not rows:
        return "No brands registered. Create one with new_brand.py.\n"
    L = ["%-18s %-30s %-12s %s" % ("ID", "NAME", "UPDATED", "ALIASES"),
         "-" * 92]
    for r in rows:
        L.append("%-18s %-30s %-12s %s"
                 % (r["id"], r["name"], r["updated"] or "-",
                    ", ".join(r.get("aliases") or []) or "-"))
    L.append("")
    L.append("%d brand(s)." % len(rows))
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    # type: (list) -> int
    ap = _Parser(
        prog="brand_resolve.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("name", nargs="*", help="the brand name as a person typed it")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="machine-readable output")
    ap.add_argument("--list", action="store_true", dest="do_list",
                    help="print the brand registry and exit 0")
    args = ap.parse_args(argv)

    try:
        if args.do_list:
            rows = brandlib.list_brands()
            if args.as_json:
                json.dump({"brands": rows, "count": len(rows)}, sys.stdout, indent=2)
                sys.stdout.write("\n")
            else:
                sys.stdout.write(render_list(rows))
            return EXIT_EXACT

        query = " ".join(args.name).strip()
        if not query:
            ap.error("give a brand name, or use --list")

        result = brandlib.resolve_brand(query)
        match = result.get("match") or "none"
        candidates = result.get("candidates") or []

        if match == "none" or not result.get("brand"):
            if args.as_json:
                json.dump({"query": query, "match": "none", "brand": None,
                           "score": result.get("score", 0.0), "candidates": candidates,
                           "summary": None, "exitCode": EXIT_NONE},
                          sys.stdout, indent=2)
                sys.stdout.write("\n")
            else:
                sys.stdout.write(render_no_match(query, candidates))
            return EXIT_NONE

        brand = brandlib.load_brand(result["brand"])
        summary = build_summary(brand)
        code = EXIT_EXACT if match == "exact" else EXIT_FUZZY

        if args.as_json:
            json.dump({"query": query, "match": match, "brand": result["brand"],
                       "score": result.get("score", 0.0), "candidates": candidates,
                       "summary": summary, "exitCode": code},
                      sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            sys.stdout.write(render_human(summary, match, result.get("score", 0.0), candidates))
        return code

    except brandlib.BrandNotFound as exc:
        sys.stderr.write("brand_resolve: %s\n" % exc)
        return EXIT_NONE
    except (IOError, OSError, ValueError) as exc:
        sys.stderr.write("brand_resolve: %s\n" % exc)
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
