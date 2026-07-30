#!/usr/bin/env python3
"""Explain what brand-studio is and how it works, from its live state.

This is deliberately a script rather than a page of prose: it reads the registry,
the grammar and the validators as they actually are right now, so the explanation
cannot drift away from the code. It is also far cheaper than asking a model to
describe the system each time.

Usage:
    explain.py                 # the whole picture, ~70 lines
    explain.py --brief         # ten lines
    explain.py --section flow  # one section: what|flow|archetypes|rules|brands|files|commands
    explain.py --json
"""

import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import brandlib as bl  # noqa: E402

ROOT = bl.plugin_root()


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except IOError:
        return ""


def violation_ids():
    """Every violation id the validators can actually emit, grouped by namespace."""
    ids = set()
    for name in ("validate_deck.py", "validate_video.py"):
        text = _read(os.path.join(ROOT, "scripts", name))
        for match in re.findall(r'["\']([A-Z0-9]{3,10}\.[A-Z0-9_]{2,30})["\']', text):
            ns = match.split(".")[0]
            if ns in ("COLOR", "TYPE", "LOGO", "LAYOUT", "CONTENT", "VOICE",
                      "STRUCTURE", "A11Y", "VIDEO", "AUDIO", "CAPTION", "LEARNED"):
                ids.add(match)
    grouped = {}
    for vid in sorted(ids):
        grouped.setdefault(vid.split(".")[0], []).append(vid)
    return grouped


def brands():
    out = []
    try:
        registry = json.loads(_read(os.path.join(ROOT, "brands", "_registry.json")) or "{}")
    except ValueError:
        registry = {}
    for entry in registry.get("brands", []):
        bid = entry.get("id")
        learned = 0
        try:
            rules = json.loads(_read(os.path.join(ROOT, "brands", bid, "rules.local.json")) or "{}")
            learned = len(rules.get("rules") or [])
        except ValueError:
            pass
        ledger = _read(os.path.join(ROOT, "brands", bid, "LEARNED.md"))
        out.append({
            "id": bid,
            "name": entry.get("name"),
            "kind": entry.get("kind", "client"),
            "updated": entry.get("updated", "-"),
            "learnedRules": learned,
            "ledgerEntries": len(re.findall(r"^##+\s", ledger, re.M)),
        })
    return out


def archetypes():
    try:
        g = json.loads(_read(os.path.join(ROOT, "grammar", "deck-grammar.json")) or "{}")
    except ValueError:
        return []
    return [(a.get("id"), a.get("use", "")) for a in g.get("archetypes", [])]


def icon_count():
    return len(glob.glob(os.path.join(ROOT, "grammar", "icons", "*.svg")))


def motion_templates():
    """Scene templates only -- intro and outro are brand bookends, not story beats."""
    names = sorted(os.path.splitext(os.path.basename(p))[0]
                   for p in glob.glob(os.path.join(ROOT, "templates", "video", "*.html")))
    return [n for n in names if n not in ("intro", "outro")]


def command_names():
    return sorted(os.path.splitext(f)[0]
                  for f in os.listdir(os.path.join(ROOT, "commands"))
                  if f.endswith(".md")) if os.path.isdir(os.path.join(ROOT, "commands")) else []


SECTIONS = ("what", "features", "flow", "archetypes", "rules", "brands", "files",
            "commands")

# The capabilities worth knowing about before using it. Kept here rather than in a
# README because this is what actually gets read.
FEATURES = [
    ("Multi-brand, one engine",
     ["Layout geometry is brand-agnostic and shared; colour, type, logo and voice are",
      "per brand. Adding a client brand costs zero layout work - it inherits all 18",
      "archetypes immediately."]),
    ("Compliance is enforced, not requested",
     ["A deterministic validator walks every text run, fill, image and caption. A hook",
      "runs it automatically on every deck or video written, and blocks on errors until",
      "they are fixed. Nothing ships on a promise that it looks right."]),
    ("It learns, permanently",
     ["Every correction is persisted in one of three tiers - a dated note, a machine-",
      "checkable rule the validator enforces from then on, or an edit to the brand's",
      "tokens. Mechanically checkable corrections become enforced rules, so the same",
      "note is never needed twice."]),
    ("Brand assets are reused, not re-rendered",
     ["A brand's intro and outro are rendered once and reused byte-identically across",
      "every video, so a brand always opens the same way. They rebuild only when the",
      "logo bytes, palette, duration or resolution actually change - tracked by a",
      "fingerprint - or when you explicitly ask. Icons cache per icon+colour+size."]),
    ("Built for low token cost",
     ["Each SKILL.md is a lean router; the deep references load only at the step that",
      "needs them. The brand is resolved once per session and shown as a compact",
      "summary, never as raw JSON. Explanations and validation are scripts, not model",
      "output, so they cost almost nothing and cannot drift from the code."]),
    ("Native, editable output",
     ["Real .pptx with real text, tables and charts - editable in PowerPoint, not images",
      "of slides. Video ships with a caption sidecar and a resolved timeline."]),
    ("Catches what a person cannot",
     ["A run with no explicit font renders as the theme font and looks fine on the",
      "machine that made it - the validator flags it. Contrast is measured against the",
      "shape actually behind the text. Palette drift is matched by colour distance, so a",
      "near-miss brand blue is caught rather than passing as close enough."]),
]


def render(section=None, brief=False):
    L = []
    add = L.append
    want = (lambda s: section is None or section == s)

    if brief:
        bs = brands()
        arch = archetypes()
        vids = violation_ids()
        total = sum(len(v) for v in vids.values())
        add("brand-studio - build and enforce brand-compliant decks and videos.")
        add("")
        add("  Author content as JSON -> a builder paints a native .pptx or .mp4 using the")
        add("  brand's tokens and a shared layout grammar -> a validator checks every run,")
        add("  fill, logo and caption against the brand -> a hook blocks you until it passes.")
        add("")
        add("  %d brands   %d slide archetypes   %d motion templates   %d icons   %d validator rules"
            % (len(bs), len(arch), len(motion_templates()), icon_count(), total))
        add("  Runs unattended overnight from a work queue; you review in the morning and it learns.")
        add("")
        add("  What it does well:")
        for title, _body in FEATURES:
            add("    - %s" % title)
        add("")
        add("  Run /brand-studio features for what each of those means in practice.")
        return "\n".join(L)

    if want("what"):
        add("=" * 74)
        add("BRAND-STUDIO")
        add("=" * 74)
        add("Produces enterprise decks and videos that are brand-compliant by construction,")
        add("for many brands, and gets stricter every time you correct it.")
        add("")
        add("The idea it is built on: a deck's LAYOUT and a brand's TOKENS are different")
        add("things. Geometry - margins, the header rule, where a title sits, how a stat row")
        add("is spaced - was extracted once from the house master template and is brand-")
        add("agnostic. Colour, type, logo and voice live per brand. One layout engine")
        add("therefore serves every client brand, and adding a brand costs no new layout work.")
        add("")

    if want("features"):
        add("WHAT IT DOES WELL")
        add("-" * 74)
        for title, body in FEATURES:
            add("  %s" % title)
            for line in body:
                add("      %s" % line)
            add("")

    if want("flow"):
        add("HOW A JOB RUNS")
        add("-" * 74)
        add("  1  BRAND      Skill asks which brand. Known -> shows a compact profile and waits")
        add("                for your approval. Unknown -> runs intake (identity, colour, type,")
        add("                logo, voice, video kit) and asks for sample/reference material.")
        add("  2  STORYLINE  Argument first. The outline is agreed before any slide is laid out.")
        add("  3  IR         Content is authored as JSON - the archetype per slide plus its text.")
        add("  4  BUILD      build_deck.py / build_video.py paint every property explicitly.")
        add("                Nothing is left to inherit, so no Office theme can leak through.")
        add("  5  VALIDATE   validate.py walks every run, fill, image and caption.")
        add("                Errors exit 2. Warnings are reported and must be justified.")
        add("  6  ENFORCE    A PostToolUse hook runs the validator whenever a deck or video is")
        add("                written. On failure it feeds the violations back and the loop")
        add("                repeats. A Stop gate refuses to end the turn while an artifact is")
        add("                still failing - with a 5-attempt guard so it can never trap you.")
        add("  7  LEARN      Any correction you give is persisted before moving on.")
        add("")

    if want("rules"):
        vids = violation_ids()
        total = sum(len(v) for v in vids.values())
        add("WHAT THE VALIDATOR ENFORCES  (%d rules)" % total)
        add("-" * 74)
        labels = {
            "COLOR": "off-palette, superseded, forbidden ink, gradient text",
            "TYPE": "wrong family, inherited font, synthetic bold, italics, below minimum",
            "LOGO": "missing, undersized, distorted, misplaced, clear space",
            "LAYOUT": "text overflow, overlap, off-canvas, off-grid",
            "CONTENT": "placeholder text left in, bullet counts, title length",
            "VOICE": "exclamation marks, title case where it is not allowed",
            "A11Y": "WCAG AA contrast against the real background, alt text",
            "STRUCTURE": "cover/closing present, section pacing, repeated archetypes",
            "VIDEO": "resolution, fps, codec, intro/outro, safe margin, palette drift",
            "AUDIO": "missing track, loudness target, clipping, silence",
            "CAPTION": "missing, line length, line count, timing, overlap",
            "LEARNED": "rules added from your own corrections",
        }
        for ns in sorted(vids):
            add("  %-10s %2d  %s" % (ns, len(vids[ns]), labels.get(ns, "")))
        add("")
        add("  Two it will catch that a person will not: a run with NO explicit font (which")
        add("  renders as the theme font, silently), and contrast measured against the shape")
        add("  actually behind the text rather than against the slide background.")
        add("")

    if want("archetypes"):
        arch = archetypes()
        add("SLIDE ARCHETYPES  (%d, with %d tintable icons)" % (len(arch), icon_count()))
        add("-" * 74)
        for aid, use in arch:
            add("  %-14s %s" % (aid, use[:56]))
        add("")

    if want("brands"):
        bs = brands()
        add("BRANDS  (%d)" % len(bs))
        add("-" * 74)
        add("  %-14s %-22s %-8s %-9s %s" % ("id", "name", "kind", "rules", "ledger"))
        for b in bs:
            add("  %-14s %-22s %-8s %-9d %d entries"
                % (b["id"], (b["name"] or "")[:22], b["kind"], b["learnedRules"],
                   b["ledgerEntries"]))
        add("")
        add("  A brand holds: tokens (brand.json), a written ledger of decisions (LEARNED.md),")
        add("  machine-checkable rules from your corrections (rules.local.json), logos, fonts,")
        add("  and a video kit. Add one with /brand-new.")
        add("")

    if want("commands"):
        add("THE DAILY LOOP")
        add("-" * 74)
        add("  The plugin is built to run a production day end to end, unattended:")
        add("")
        add("    evening   /brand-queue      say what you want built - decks, videos, reels, any mix")
        add("    night     /brand-daily      builds every queued job, validates, leaves a review packet")
        add("    morning   /brand-review     see what was made, give notes, it applies and rebuilds")
        add("    weekly    review.py metrics is the review burden actually falling?")
        add("")
        add("  Each note lands in one of three tiers. Mechanically checkable notes become validator")
        add("  rules, so tomorrow's build cannot repeat them. That is the whole point of the loop:")
        add("  the number of notes you have to give should fall week over week.")
        add("")
        add("COMMANDS AND SKILLS")
        add("-" * 74)
        add("  /brand-studio     this explanation")
        add("  /brand-queue      queue tonight's work")
        add("  /brand-daily      run the nightly build now, or --dry-run tomorrow's")
        add("  /brand-review     the morning review and learning step")
        add("  /brand-brainstorm think through a film, reel or animation before there is a brief")
        add("  /brand-new        register a brand and run intake")
        add("  /brand-check      validate any deck or video and explain each violation")
        add("  /brand-learn      persist a correction so it is enforced from now on")
        add("")
        installed = command_names()
        missing = [c for c in ("brand-studio", "brand-queue", "brand-daily", "brand-review",
                               "brand-brainstorm", "brand-new", "brand-check", "brand-learn")
                   if c not in installed]
        if missing:
            add("  not yet installed: %s" % ", ".join("/" + m for m in missing))
            add("")
        add("  Skills trigger on intent, not by name:")
        add("    brand-kit    resolve/confirm/create the brand - runs first, always")
        add("    brand-deck   decks, presentations, slides, pitches, QBRs, readouts")
        add("    brand-video  videos, explainers, walkthroughs, voiceover, captions")
        add("")

    if want("files"):
        add("WHERE THINGS LIVE")
        add("-" * 74)
        add("  grammar/       layout geometry + icons   (brand-agnostic, shared by all brands)")
        add("  brands/<id>/   tokens, logos, fonts, video kit, learned rules  (per brand)")
        add("  scripts/       builders, validators, brand tools")
        add("  skills/        the three skills; SKILL.md is lean, references load on demand")
        add("  hooks/         the enforcement loop")
        add("")
        add("HOW IT LEARNS")
        add("-" * 74)
        add("  Every correction lands in one of three tiers:")
        add("    LEARNED.md         a dated note - for judgement calls and rationale")
        add("    rules.local.json   a machine rule the validator enforces from then on")
        add("    brand.json         a durable brand fact, when the guideline itself changed")
        add("  The skill says which tier it used. Mechanically checkable corrections become")
        add("  enforced rules, so the same note is never needed twice.")
        add("")
        add("TOKEN DISCIPLINE")
        add("-" * 74)
        add("  Intro, outro and music bed are rendered once per brand and reused - see")
        add("  asset_cache.py. They rebuild only when the logo, palette or duration changes,")
        add("  or when you explicitly ask for a new one.")
        add("  Icons are rasterised once per (icon, colour, size) and cached.")
        add("  The brand is resolved once per session and shown as a compact summary,")
        add("  never as raw JSON. Reference docs load only at the step that needs them.")

    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--brief", action="store_true", help="ten-line summary")
    ap.add_argument("--section", choices=SECTIONS, help="print one section only")
    ap.add_argument("--json", action="store_true", help="machine-readable state")
    a = ap.parse_args(argv)

    if a.json:
        vids = violation_ids()
        json.dump({
            "brands": brands(),
            "archetypes": [a_[0] for a_ in archetypes()],
            "icons": icon_count(),
            "violations": vids,
            "violationCount": sum(len(v) for v in vids.values()),
        }, sys.stdout, indent=1)
        sys.stdout.write("\n")
        return 0

    print(render(a.section, a.brief))
    return 0


if __name__ == "__main__":
    sys.exit(main())
