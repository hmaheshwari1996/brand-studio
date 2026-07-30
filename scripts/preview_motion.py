#!/usr/bin/env python3
"""Render a motion template on its own, so you can look at it before building a film.

Authoring an explainer without this means rendering a whole video to see whether one
scene works -- minutes of ffmpeg to check a layout. This renders just the template,
at whatever points on its clock you ask for, using the real brand variables the
builder would inject.

It also checks the two things that quietly break a template:

  determinism  the same ``t`` must always produce the same pixels. A template using
               Math.random() or a CSS animation looks fine frame by frame and
               strobes in the finished film.
  progression  the frames must actually differ. A template whose clock is not wired
               up renders the same picture 180 times and reads as a still.

Usage:
    preview_motion.py --template counter --brand channelplay --data examples/motion/counter.json
    preview_motion.py --template kinetic-type --data-inline '{"lines":["One.","Two."]}'
    preview_motion.py --template chart-reveal --t 0,0.5,1 --check
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from lib import brandlib as bl            # noqa: E402
import build_video as bv                  # noqa: E402
from render_icon import find_chrome       # noqa: E402


def render_frame(chrome, html_path, t, out_png, width, height, frame=0, of=1):
    url = "file://%s?t=%.6f&frame=%d&of=%d" % (html_path, t, frame, of)
    cmd = [
        chrome, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
        "--force-device-scale-factor=1",
        "--screenshot=%s" % os.path.abspath(out_png),
        "--window-size=%d,%d" % (width, height),
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if not os.path.exists(out_png) or os.path.getsize(out_png) == 0:
        raise RuntimeError("Chrome rendered nothing at t=%s\n%s"
                           % (t, proc.stderr.decode("utf-8", "replace")[-500:]))
    return out_png


def image_signature(path):
    """A cheap fingerprint that still notices a layout change."""
    try:
        from PIL import Image
    except ImportError:
        return (os.path.getsize(path), None)
    im = Image.open(path).convert("RGB")
    small = im.resize((32, 18))
    return (os.path.getsize(path), tuple(small.getdata()))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--template", required=True, help="template stem, e.g. counter")
    ap.add_argument("--brand", default="channelplay", help="brand id (default channelplay)")
    ap.add_argument("--data", help="path to a JSON file holding visual.data")
    ap.add_argument("--data-inline", help="visual.data as an inline JSON string")
    ap.add_argument("--t", default="0,0.25,0.5,0.75,1.0",
                    help="comma-separated clock positions (default 0,0.25,0.5,0.75,1.0)")
    ap.add_argument("--out", help="output directory (default a temp dir, path printed)")
    ap.add_argument("--check", action="store_true",
                    help="also verify determinism and that the frames progress")
    ap.add_argument("--keep-html", action="store_true", help="keep the substituted HTML")
    a = ap.parse_args(argv)

    try:
        brand = bl.load_brand(a.brand)
    except Exception as exc:
        sys.stderr.write("preview_motion: %s\n" % exc)
        return 1

    data = {}
    if a.data:
        with open(a.data, encoding="utf-8") as fh:
            data = json.load(fh)
        # Accept either a bare data object or a whole scene/IR fragment.
        if isinstance(data, dict) and "visual" in data:
            data = (data.get("visual") or {}).get("data") or {}
    if a.data_inline:
        data = json.loads(a.data_inline)

    video = brand.get("video") or {}
    res = video.get("resolution") or {}
    width, height = int(res.get("w", 1920)), int(res.get("h", 1080))

    warnings = []
    brand_vars = bv.build_brand_vars(brand, video, warnings)
    for w in warnings:
        sys.stderr.write("  brand-vars warning: %s\n" % w)

    outdir = a.out or tempfile.mkdtemp(prefix="brand-studio-motion-")
    if not os.path.isdir(outdir):
        os.makedirs(outdir)

    html_path = os.path.join(outdir, "%s.html" % a.template)
    try:
        # template_path() resolves the stem and raises with the available list,
        # so an unknown name gives the same helpful error the builder gives.
        src = bv.template_path(a.template)
        bv.write_scene_html(src, brand_vars, data, html_path)
    except Exception as exc:
        sys.stderr.write("preview_motion: %s\n" % exc)
        return 1

    chrome = find_chrome()
    if chrome is None:
        sys.stderr.write("preview_motion: headless Chrome not found\n")
        return 1

    ts = []
    for part in a.t.split(","):
        part = part.strip()
        if part:
            ts.append(max(0.0, min(1.0, float(part))))

    print("template  %s" % a.template)
    print("brand     %s (%s)" % (brand.get("name"), a.brand))
    print("canvas    %dx%d" % (width, height))
    print("out       %s" % outdir)
    print("")

    sigs = []
    for i, t in enumerate(ts):
        png = os.path.join(outdir, "t-%s.png" % ("%.3f" % t).replace(".", "_"))
        render_frame(chrome, html_path, t, png, width, height, frame=i, of=len(ts))
        sig = image_signature(png)
        sigs.append(sig)
        print("  t=%-6.3f %8d bytes  %s" % (t, os.path.getsize(png), png))

    exit_code = 0
    if a.check:
        print("")
        # progression: consecutive frames must not be pixel-identical
        identical = [i for i in range(1, len(sigs)) if sigs[i] == sigs[i - 1]]
        if identical:
            print("  FAIL  progression: frames %s are identical to the one before them."
                  % ", ".join("t=%.3f" % ts[i] for i in identical))
            print("        The template's clock is not driving anything at those points.")
            exit_code = 2
        else:
            print("  ok    progression: every frame differs from the one before it")

        # determinism: render a midpoint twice
        mid = ts[len(ts) // 2]
        d1 = os.path.join(outdir, "_det1.png")
        d2 = os.path.join(outdir, "_det2.png")
        render_frame(chrome, html_path, mid, d1, width, height)
        render_frame(chrome, html_path, mid, d2, width, height)
        same = image_signature(d1) == image_signature(d2)
        os.remove(d1)
        os.remove(d2)
        if same:
            print("  ok    determinism: t=%.3f renders identically twice" % mid)
        else:
            print("  FAIL  determinism: t=%.3f rendered differently on two runs." % mid)
            print("        Something is non-deterministic -- Math.random(), a CSS animation or")
            print("        transition, or a Date-based value. The film will strobe.")
            exit_code = 2

    if not a.keep_html:
        try:
            os.remove(html_path)
        except OSError:
            pass

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
