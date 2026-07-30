#!/usr/bin/env python3
"""Rasterise a brand-agnostic icon from grammar/icons/ to a tinted PNG.

The icon library ships as SVG with every fill normalised to ``currentColor``, so the
same 86 icons serve every brand -- the colour is applied at render time from the
active brand profile. Rendering goes through headless Chrome because it is already
present on every machine that runs this plugin, and it avoids adding cairosvg or
librsvg as a dependency.

Results are cached under ~/.cache/brand-studio/icons/ keyed by (icon, colour, size),
so a deck that reuses an icon twenty times rasterises it once.

Usage:
    render_icon.py --name anchor --color '#0000FF' --size 256 --out icon.png
    render_icon.py --list
    render_icon.py --name anchor --color '#0000FF'          # prints the cache path
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]

HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def plugin_root():
    d = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(d)


def find_chrome():
    env = os.environ.get("BRAND_STUDIO_CHROME")
    if env and os.path.exists(env):
        return env
    for c in CHROME_CANDIDATES:
        if os.path.exists(c):
            return c
    return None


def icon_dir():
    return os.path.join(plugin_root(), "grammar", "icons")


def list_icons():
    d = icon_dir()
    if not os.path.isdir(d):
        return []
    return sorted(f[:-4] for f in os.listdir(d) if f.endswith(".svg"))


def cache_path(name, color, size):
    key = hashlib.md5(("%s|%s|%d" % (name, color.upper(), size)).encode()).hexdigest()[:16]
    d = os.path.join(os.path.expanduser("~"), ".cache", "brand-studio", "icons")
    if not os.path.isdir(d):
        os.makedirs(d)
    return os.path.join(d, "%s-%s.png" % (name, key))


def render(name, color, size, out=None, force=False):
    """Rasterise ``name`` tinted ``color`` at ``size`` px square. Returns the PNG path."""
    if not HEX_RE.match(color):
        raise ValueError("colour must be #RRGGBB, got %r" % color)

    svg_path = os.path.join(icon_dir(), name + ".svg")
    if not os.path.exists(svg_path):
        near = [i for i in list_icons() if name.split("-")[0] in i][:6]
        hint = (" Did you mean: %s" % ", ".join(near)) if near else ""
        raise FileNotFoundError("no icon %r in %s.%s" % (name, icon_dir(), hint))

    target = out or cache_path(name, color, size)
    if os.path.exists(target) and not force and out is None:
        return target

    chrome = find_chrome()
    if chrome is None:
        raise RuntimeError(
            "headless Chrome not found. Install Google Chrome, or set BRAND_STUDIO_CHROME "
            "to a Chromium-based binary."
        )

    with open(svg_path, encoding="utf-8") as fh:
        svg = fh.read()

    html = (
        '<!doctype html><meta charset="utf-8"><style>'
        "html,body{margin:0;padding:0;background:transparent;}"
        "svg{width:%dpx;height:%dpx;color:%s;display:block;}"
        "</style>%s" % (size, size, color, svg)
    )

    tmpdir = os.path.join(os.path.expanduser("~"), ".cache", "brand-studio", "tmp")
    if not os.path.isdir(tmpdir):
        os.makedirs(tmpdir)
    tmphtml = os.path.join(tmpdir, "icon-%s.html" % os.path.basename(target)[:-4])
    with open(tmphtml, "w", encoding="utf-8") as fh:
        fh.write(html)

    tdir = os.path.dirname(os.path.abspath(target))
    if tdir and not os.path.isdir(tdir):
        os.makedirs(tdir)

    cmd = [
        chrome, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
        "--default-background-color=00000000",
        "--screenshot=%s" % os.path.abspath(target),
        "--window-size=%d,%d" % (size, size),
        "file://%s" % tmphtml,
    ]
    proc = subprocess.run(cmd, capture_output=True)
    try:
        os.remove(tmphtml)
    except OSError:
        pass

    if not os.path.exists(target) or os.path.getsize(target) == 0:
        raise RuntimeError(
            "Chrome produced no output for icon %r.\n%s" % (name, proc.stderr.decode("utf-8", "replace")[-600:])
        )
    return target


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", help="icon name, e.g. anchor")
    ap.add_argument("--color", default="#0000FF", help="tint as #RRGGBB (default #0000FF)")
    ap.add_argument("--size", type=int, default=256, help="square px (default 256)")
    ap.add_argument("--out", help="output PNG path (default: cache)")
    ap.add_argument("--force", action="store_true", help="ignore the cache")
    ap.add_argument("--list", action="store_true", help="list available icon names")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)

    if a.list:
        names = list_icons()
        if a.json:
            json.dump({"count": len(names), "icons": names}, sys.stdout, indent=1)
            sys.stdout.write("\n")
        else:
            for n in names:
                print(n)
            sys.stderr.write("%d icons\n" % len(names))
        return 0

    if not a.name:
        ap.error("--name is required unless --list is given")

    try:
        path = render(a.name, a.color, a.size, a.out, a.force)
    except Exception as exc:
        sys.stderr.write("render_icon: %s\n" % exc)
        return 1

    if a.json:
        json.dump({"icon": a.name, "color": a.color, "size": a.size, "path": path}, sys.stdout)
        sys.stdout.write("\n")
    else:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
