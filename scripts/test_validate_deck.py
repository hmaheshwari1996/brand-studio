#!/usr/bin/env python
"""Regression test for validate_deck's surface and logo resolution.

WHY THIS FILE EXISTS. Three false-positive sources were fixed in
validate_deck.py on 2026-09-01, and on one real 112-slide deck they accounted
for 106 of 167 reported errors. Every one of those fixes REMOVES findings, and
a change that removes findings has an obvious failure mode: remove too many and
the checker goes quiet instead of going right. Nothing would catch that — a
blinded checker and a correct one both print zero.

So each fix is pinned from BOTH sides here: a case that must stop being
reported (precision) and a case that must still be reported (recall). The recall
half is the point. The precision half only proves the bug is gone.

  1. effective_background(): a shape carrying its own text is its own nearest
     surface. White on a ink button must PASS; white on mint must still FAIL.
  2. LogoIndex.identify(): alt text is prose, not an identifier. A screenshot
     described as containing the logo must NOT be treated as the logo; the real
     logo, stretched, must still be caught.

Self-contained: builds its own deck against the in-repo `example` brand, so
it needs no client brand and no committed fixture.

    python scripts/test_validate_deck.py
"""
from __future__ import print_function

import json
import os
import struct
import subprocess
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BRAND = "example"

INK  = "1B2430"   # white on this passes AA comfortably
ACCENT = "7FE3C0" # white on this is ~1.6:1 and must never pass

failures = []


def check(name, condition, detail=""):
    if condition:
        print("  ok    %s" % name)
    else:
        print("  FAIL  %s%s" % (name, ("\n          " + detail) if detail else ""))
        failures.append(name)


def solid_png(path, width, height, rgb):
    """A PNG with no dependencies — the not-a-logo image."""
    raw = b""
    for _ in range(height):
        raw += b"\x00" + bytes(bytearray(rgb)) * width

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(png)
    return path


def build_deck(path, tmp):
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    slide = prs.slides.add_slide(prs.slide_layouts[6])

    def button(left, top, fill_hex, label):
        shp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                     Inches(left), Inches(top), Inches(2.2), Inches(0.6))
        shp.fill.solid()
        shp.fill.fore_color.rgb = RGBColor.from_string(fill_hex)
        shp.line.fill.background()
        tf = shp.text_frame
        tf.text = label
        run = tf.paragraphs[0].runs[0]
        run.font.size = Pt(18)
        run.font.color.rgb = RGBColor.from_string("FFFFFF")
        return shp

    # (1) precision + recall for the own-fill rule
    button(0.6, 0.6, INK, "On ink")     # must PASS
    button(3.4, 0.6, ACCENT, "On accent")     # must still FAIL

    # (2) nearest-surface: a white card, a accent circle on top of it, and a
    #     white numeral on the circle. The nearest surface is the CIRCLE.
    card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                  Inches(0.6), Inches(2.0), Inches(3.0), Inches(1.4))
    card.fill.solid()
    card.fill.fore_color.rgb = RGBColor.from_string("FFFFFF")
    card.line.fill.background()
    dot = slide.shapes.add_shape(MSO_SHAPE.OVAL,
                                 Inches(0.9), Inches(2.3), Inches(0.5), Inches(0.5))
    dot.fill.solid()
    dot.fill.fore_color.rgb = RGBColor.from_string(ACCENT)
    dot.line.fill.background()
    box = slide.shapes.add_textbox(Inches(0.9), Inches(2.3), Inches(0.5), Inches(0.5))
    box.text_frame.text = "7"
    r = box.text_frame.paragraphs[0].runs[0]
    r.font.size = Pt(18)
    r.font.color.rgb = RGBColor.from_string("FFFFFF")

    # (3) logo precision: not the logo, but its alt text says so
    shot = solid_png(os.path.join(tmp, "screen.png"), 40, 88, (200, 40, 40))
    pic = slide.shapes.add_picture(shot, Inches(6.4), Inches(2.0),
                                   Inches(1.2), Inches(2.64))
    pic._element._nvXxPr.cNvPr.set("descr",
                                   "App screenshot showing the Example Brand logo at the top")

    # (3) logo recall: the REAL mark, stretched well past tolerance
    real = os.path.join(ROOT, "brands", BRAND, "assets", "logos",
                        "example-logo-primary.png")
    slide.shapes.add_picture(real, Inches(8.6), Inches(2.0), Inches(3.0), Inches(2.4))

    prs.save(path)


def main():
    tmp = tempfile.mkdtemp(prefix="brandstudio-regression-")
    deck = os.path.join(tmp, "regression.pptx")
    build_deck(deck, tmp)

    out = subprocess.run(
        [sys.executable, os.path.join(HERE, "validate.py"), deck,
         "--brand", BRAND, "--format", "json"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        report = json.loads(out.stdout.decode("utf-8"))
    except ValueError:
        print("validator produced no JSON:\n%s" % out.stderr.decode("utf-8")[:800])
        return 1

    v = report.get("violations", [])
    contrast = [x for x in v if x["id"] == "A11Y.CONTRAST"]
    found = " | ".join(x["found"] for x in contrast)
    logo = [x for x in v if x["id"] == "LOGO.DISTORTED"]

    print("validate_deck regression")

    # -- 1. own fill is the nearest surface --------------------------------
    # Asserted as "EVERY finding is on mint", not "no finding mentions navy".
    # The weaker form passes vacuously when the rule regresses: reverting the
    # fix makes the ink button resolve to the slide instead, so it is reported
    # as "#FFFFFF on #FFFFFF" and never mentions navy at all. Mutation-testing
    # this file is what exposed that — the check looked right and proved
    # nothing.
    check("every contrast finding resolves to the shape's own fill",
          contrast and all(ACCENT in x["found"].upper() for x in contrast),
          "a finding resolved to some other surface: %s" % (found or "(none at all)"))
    check("white on a accent button IS still reported",
          any(ACCENT in x["found"].upper() for x in contrast),
          "nothing flagged the accent — the rule may be blind, not fixed: %s" % found)

    # -- 2. text stacked over two surfaces resolves to the near one --------
    # A numeral on an accent circle on a white card. It must resolve to the
    # CIRCLE; "#FFFFFF on #FFFFFF" is the signature of reaching past it to the
    # card. NOTE this exercises the own-fill rule, not the z-ordering one:
    # rec.z is assigned in traversal order and ctx.recs is built in that same
    # order, so "last qualifying candidate" and "highest z" are always the same
    # shape. The z comparison in effective_background() is defensive — it makes
    # the code match its docstring and holds if recs ever stop being ordered
    # (group flattening already reuses a parent's z) — but it changes no result
    # today, and no test here can pretend otherwise.
    check("text over two stacked surfaces resolves to the nearer one",
          not any("#FFFFFF on #FFFFFF" in x["found"] for x in contrast),
          "reached past the circle to the card: %s" % found)

    # -- 3. alt text is not an identifier ----------------------------------
    check("a screenshot whose ALT TEXT says 'logo' is not treated as the logo",
          not any(abs(_aspect(x) - 0.4545) < 0.02 for x in logo),
          "the 1.2x2.64in screenshot was measured as a logo: %s"
          % " | ".join(x["found"] for x in logo))
    check("the REAL logo, stretched, is still caught",
          any(abs(_aspect(x) - 1.25) < 0.02 for x in logo),
          "a 3.0x2.4in logo (aspect 1.25 vs 4.0) went unreported — recall lost: %s"
          % (" | ".join(x["found"] for x in logo) or "no LOGO.DISTORTED at all"))

    if failures:
        print("\n%d check(s) failed" % len(failures))
        return 1
    print("\nall checks passed")
    return 0


def _aspect(finding):
    # found looks like: aspect 0.455 (2.760 x 6.062 in)
    try:
        return float(finding["found"].split()[1])
    except (IndexError, ValueError):
        return -1.0


if __name__ == "__main__":
    sys.exit(main())
