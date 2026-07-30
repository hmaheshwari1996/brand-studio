#!/usr/bin/env python3
"""Render a branded 3D product shot or film from a scene description.

This is CPU rendering. There is no GPU and no Blender in this pipeline: headless
Chrome runs WebGL 2 through SwiftShader, three.js draws the scene, and Chrome
screenshots it. That is slow per frame and completely portable -- it needs nothing
installed that the rest of this plugin does not already need.

THE FRAME SHEET
---------------
The expensive part of a frame is not drawing it, it is starting Chrome. A 1920x1080
frame costs roughly 9.5s end to end, of which about 7s is process startup and
SwiftShader initialisation. Launching Chrome once per frame would put a 15-second
reel at over an hour.

So a render does not screenshot one frame at a time. ``templates/cgi/product.html``
reads ``?t0=&dt=&n=&cols=``, lays out n cells in a grid, renders the scene once per
cell into a single sheet canvas, and Chrome takes ONE screenshot of the lot. This
script then slices the sheet back into n PNGs with pillow. The startup cost is paid
once per sheet instead of once per frame, and the marginal cost of a frame collapses
to the cost of actually drawing it. Combined with a worker pool over sheets, that is
the difference between an overnight job and a coffee break.

Usage:
    render_cgi.py --data scene.json --out shot.png --still 0.35
    render_cgi.py --data scene.json --out look.png --preview
    render_cgi.py --data scene.json --out film.mp4 --frames 150 --fps 30 --yes
    render_cgi.py --data scene.json --out frames/ --frames 90 --jobs 6 --per-sheet 8
"""

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from lib import brandlib as bl            # noqa: E402
from render_icon import find_chrome       # noqa: E402


class RenderError(Exception):
    pass


# The verified SwiftShader flag set. --use-gl=swiftshader plus the unsafe-swiftshader
# opt-in is what gets WebGL 2 without a GPU; --allow-file-access-from-files is what
# lets GLTFLoader's XHR read a .glb sitting next to the page on file://.
CHROME_FLAGS = [
    "--headless",
    "--no-sandbox",
    "--disable-gpu",
    "--use-gl=swiftshader",
    "--enable-unsafe-swiftshader",
    "--hide-scrollbars",
    "--force-device-scale-factor=1",
    "--allow-file-access-from-files",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--disable-lcd-text",
    "--mute-audio",
]

#: How much a second worker actually buys. SwiftShader is already multi-threaded within
#: a single render, so extra Chrome processes mostly contend for the same cores.
#: Fitted against two measured runs on an 8-core M-series Mac: 3 workers ~1.6x and
#: 5 workers ~2.2x the single-worker throughput, not 3x and 5x.
PARALLEL_EFFICIENCY = 0.30
MAX_EFFECTIVE_WORKERS = 3.0

#: A frame with fewer distinct colours than this did not render. A real product frame
#: has thousands; a blank one has one or two. This is the failure mode that survives
#: every downstream check, so it is caught here rather than discovered in a deck.
MIN_DISTINCT_COLOURS = 8


# ---------------------------------------------------------------------------
# The GLTFLoader shim
# ---------------------------------------------------------------------------

#: BufferGeometryUtils is not vendored, and GLTFLoader imports exactly one function
#: from it, used only for the TRIANGLE_STRIP / TRIANGLE_FAN primitive modes that
#: almost no exporter emits. Reimplemented here rather than vendoring a second file.
TO_TRIANGLES_SHIM = r"""
function toTrianglesDrawMode(geometry, drawMode) {
  if (drawMode === THREE.TrianglesDrawMode) return geometry;
  if (drawMode === THREE.TriangleFanDrawMode || drawMode === THREE.TriangleStripDrawMode) {
    var index = geometry.getIndex();
    if (index === null) {
      var ids = [];
      var pos = geometry.getAttribute('position');
      if (pos === undefined) return geometry;
      for (var i = 0; i < pos.count; i++) ids.push(i);
      geometry.setIndex(ids);
      index = geometry.getIndex();
    }
    var numberOfTriangles = index.count - 2;
    var newIndices = [];
    if (drawMode === THREE.TriangleFanDrawMode) {
      for (var j = 1; j <= numberOfTriangles; j++) {
        newIndices.push(index.getX(0), index.getX(j), index.getX(j + 1));
      }
    } else {
      for (var k = 0; k < numberOfTriangles; k++) {
        if (k % 2 === 0) {
          newIndices.push(index.getX(k), index.getX(k + 1), index.getX(k + 2));
        } else {
          newIndices.push(index.getX(k + 2), index.getX(k + 1), index.getX(k));
        }
      }
    }
    var newGeometry = geometry.clone();
    newGeometry.setIndex(newIndices);
    newGeometry.clearGroups();
    return newGeometry;
  }
  return geometry;
}
"""


def build_gltf_shim(esm_path):
    # type: (str) -> str
    """Turn the vendored ESM GLTFLoader into a plain script that hangs off `THREE`.

    ES modules do not load over file:// without relaxing Chrome's security, and every
    template here must render offline from a plain <script> tag. The source is a single
    import block, one helper import, and one export, so the conversion is mechanical:
    bind the named imports out of the UMD global, drop the export, publish the class.
    """
    with open(esm_path, "r", encoding="utf-8") as fh:
        src = fh.read()

    m = re.search(r"^import\s*\{(.*?)\}\s*from\s*['\"]three['\"];\s*$",
                  src, re.S | re.M)
    if not m:
        raise RenderError(
            "could not find the three import block in %s; the vendored GLTFLoader is "
            "not the shape this shim expects." % esm_path)
    names = [n.strip() for n in m.group(1).split(",") if n.strip()]
    if not names:
        raise RenderError("the three import block in %s is empty" % esm_path)

    body = src[:m.start()] + src[m.end():]
    # The one non-three import, satisfied by the shim above.
    body = re.sub(r"^import\s*\{[^}]*\}\s*from\s*['\"][^'\"]*BufferGeometryUtils\.js['\"];\s*$",
                  "", body, flags=re.M)
    body = re.sub(r"^export\s*\{[^}]*\};\s*$", "", body, flags=re.M)
    if "export " in body:
        raise RenderError("unconverted `export` left in the GLTFLoader shim")

    binds = "\n".join("var %s = THREE.%s;" % (n, n) for n in names)
    return (
        "/* Generated by scripts/render_cgi.py from vendor/GLTFLoader.esm.js.\n"
        "   Do not edit: regenerated on every render that needs a model. */\n"
        "(function (THREE) {\n'use strict';\n"
        + binds + "\n" + TO_TRIANGLES_SHIM + "\n" + body
        + "\nTHREE.GLTFLoader = GLTFLoader;\n})(window.THREE);\n"
    )


# ---------------------------------------------------------------------------
# Scene + brand plumbing
# ---------------------------------------------------------------------------

PRIMITIVES = ("bottle", "box", "can", "tube", "sachet", "phone", "jar")
STUDIOS = ("gradient", "seamless", "dark", "pedestal", "floating")
MOVES = ("turntable", "dolly-in", "orbit", "hero-reveal", "rise", "static")
LIGHTINGS = ("studio", "dramatic", "soft", "retail")


def load_scene(path):
    # type: (str) -> dict
    if not os.path.isfile(path):
        raise RenderError("scene file not found: %s" % path)
    with open(path, "r", encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except ValueError as exc:
            raise RenderError("scene file is not valid JSON (%s): %s" % (path, exc))
    if not isinstance(data, dict):
        raise RenderError("scene file must hold a JSON object, got %s"
                          % type(data).__name__)
    # Accept a Video IR fragment as well as a bare scene, so a scene lifted straight
    # out of an IR works without re-typing it.
    if "visual" in data and isinstance(data.get("visual"), dict):
        data = data["visual"].get("data") or {}
    return data


def validate_scene(scene, scene_dir, warnings):
    # type: (dict, str, list) -> dict
    """Normalise the scene and resolve any file references to absolute file:// URLs."""
    out = dict(scene)

    for key, allowed in (("primitive", PRIMITIVES), ("studio", STUDIOS),
                         ("move", MOVES), ("lighting", LIGHTINGS)):
        val = out.get(key)
        if val is not None and str(val).lower() not in allowed:
            warnings.append("%s '%s' is not one of %s; the template will fall back "
                            "to its default" % (key, val, ", ".join(allowed)))

    def resolve(raw, what):
        if not raw:
            return None
        if str(raw).startswith("file://"):
            return str(raw)
        path = raw if os.path.isabs(raw) else os.path.join(scene_dir, raw)
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            warnings.append("%s not found: %s" % (what, path))
            return None
        return "file://" + path

    if out.get("model"):
        resolved = resolve(out["model"], "model")
        out["model"] = resolved
        if resolved is None:
            prim = str(out.get("primitive") or "").lower()
            warnings.append("no model, so a primitive will be used instead: %s"
                            % (prim if prim in PRIMITIVES else "bottle (the default)"))
    label = out.get("label")
    if isinstance(label, dict) and label.get("image"):
        label = dict(label)
        label["image"] = resolve(label["image"], "label image")
        out["label"] = label
    return out


def brand_style_block(brand, warnings):
    # type: (dict, list) -> str
    """The brand <style> block, built by the video builder so CGI and motion agree.

    build_video.py owns the brand-variable contract that every template in this plugin
    reads. Rebuilding it here would mean two definitions of the same thing drifting
    apart, so it is imported. The three-argument call is the form preview_motion.py
    already relies on.
    """
    try:
        import build_video as bv
    except Exception as exc:                                     # pragma: no cover
        raise RenderError("could not import build_video for the brand variables: %s" % exc)
    video = brand.get("video") or {}
    return bv.build_brand_vars(brand, video, warnings)


def write_page(template, brand_vars, scene, dest):
    # type: (str, str, dict, str) -> str
    """Substitute the two markers into the template.

    Deliberately not delegated to build_video.write_scene_html: that function resolves
    templates and markers for the motion pipeline, and coupling the CGI renderer to it
    for eight lines of str.replace buys nothing. The duplicate-marker guard is the part
    that matters, and it is reproduced here in full -- substitution replaces every
    occurrence, so a marker written a second time in a comment would inject the entire
    brand style block into that comment.
    """
    with open(template, "r", encoding="utf-8") as fh:
        html = fh.read()
    for marker in ("{{" + "BRAND_VARS}}", "{{" + "SCENE_DATA}}"):
        count = html.count(marker)
        if count == 0:
            raise RenderError("template %s has no %s marker" % (template, marker))
        if count > 1:
            raise RenderError(
                "template %s contains %s %d times; substitution replaces every "
                "occurrence." % (template, marker, count))
    payload = json.dumps(scene, ensure_ascii=False).replace("</", "<\\/")
    html = html.replace("{{" + "BRAND_VARS}}", brand_vars)
    html = html.replace("{{" + "SCENE_DATA}}", payload)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(html)
    return dest


def prepare_workdir(work, scene, brand_vars):
    # type: (str, dict, str) -> str
    """Stage the page and its vendor directory, and return the page path.

    The page loads three.js with a relative <script src>, so vendor/ has to sit beside
    the substituted HTML. Symlinking keeps a 670KB file from being copied per render.
    """
    root = bl.plugin_root()
    cgi_dir = os.path.join(root, "templates", "cgi")
    template = os.path.join(cgi_dir, "product.html")
    if not os.path.isfile(template):
        raise RenderError("CGI template not found at %s" % template)

    vendor_src = os.path.join(cgi_dir, "vendor")
    vendor_dst = os.path.join(work, "vendor")
    os.makedirs(vendor_dst, exist_ok=True)

    three_src = os.path.join(vendor_src, "three.min.js")
    if not os.path.isfile(three_src):
        raise RenderError(
            "three.js is not vendored at %s. A CGI scene never fetches from the "
            "network, so the build cannot continue." % three_src)
    three_dst = os.path.join(vendor_dst, "three.min.js")
    try:
        os.symlink(three_src, three_dst)
    except OSError:
        shutil.copy2(three_src, three_dst)

    # The page always references the loader. When no model is in play a stub is written
    # rather than leaving the tag to 404 -- the shim is 100KB of parsing that a
    # primitive-only scene has no use for, and it is paid once per sheet.
    shim_path = os.path.join(vendor_dst, "gltfloader.umd.js")
    if scene.get("model"):
        esm = os.path.join(vendor_src, "GLTFLoader.esm.js")
        if not os.path.isfile(esm):
            raise RenderError("a model was requested but %s is not vendored" % esm)
        with open(shim_path, "w", encoding="utf-8") as fh:
            fh.write(build_gltf_shim(esm))
    else:
        with open(shim_path, "w", encoding="utf-8") as fh:
            fh.write("/* No model in this scene, so the GLTFLoader shim is not built. */\n")

    page = os.path.join(work, "product.html")
    write_page(template, brand_vars, scene, page)
    return page


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def sheet_grid(n):
    # type: (int) -> int
    """Columns for an n-cell sheet, kept near square so no dimension runs away."""
    return max(1, int(math.ceil(math.sqrt(n))))


def png_is_complete(path):
    # type: (str) -> bool
    """True once a PNG has both its signature and a terminating IEND chunk."""
    try:
        if os.path.getsize(path) < 24:
            return False
        with open(path, "rb") as handle:
            if handle.read(8) != b"\x89PNG\r\n\x1a\n":
                return False
            handle.seek(-12, os.SEEK_END)
            return handle.read(12)[4:8] == b"IEND"
    except OSError:
        return False


def shoot_sheet(chrome, page, out_png, width, height, t0, dt, n, cols, timeout):
    # type: (str, str, str, int, int, float, float, int, int, int) -> None
    """One Chrome launch, one screenshot, n frames on it.

    Headless Chrome writes the screenshot reliably but frequently does not exit
    afterwards -- a SwiftShader teardown it never finishes. Waiting on the exit code
    would add a full timeout to every single sheet, so the process is driven to the
    FILE instead: poll until a complete PNG lands, then tear the browser down. This
    is the same approach build_video.py takes for motion frames, for the same reason.
    """
    rows = int(math.ceil(float(n) / cols))
    url = ("file://%s?w=%d&h=%d&t0=%.6f&dt=%.6f&n=%d&cols=%d"
           % (page, width, height, t0, dt, n, cols))
    # Each worker needs its own profile: parallel Chromes sharing one user-data-dir
    # serialise behind a lock, which would silently undo the whole point of --jobs.
    profile = tempfile.mkdtemp(prefix="cgi-profile-")
    # Enough virtual time for SwiftShader to warm, the fonts to land and any model
    # to be fetched and re-rendered, scaled by how many cells are on the sheet.
    budget = min(180000, 9000 + 1500 * n)
    cmd = [chrome] + list(CHROME_FLAGS) + [
        "--user-data-dir=%s" % profile,
        "--virtual-time-budget=%d" % budget,
        "--screenshot=%s" % os.path.abspath(out_png),
        "--window-size=%d,%d" % (width * cols, height * rows),
        url,
    ]
    if os.path.exists(out_png):
        try:
            os.remove(out_png)
        except OSError:
            pass

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise RenderError("headless Chrome failed to start: %s" % exc)

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
        shutil.rmtree(profile, ignore_errors=True)

    if not done:
        raise RenderError(
            "headless Chrome produced no screenshot for a %d-frame sheet at t0=%.4f "
            "within %ds. Try a smaller --per-sheet, or --keep-work and open the page "
            "in a browser to see what the scene is doing." % (n, t0, timeout))


def slice_sheet(sheet_png, width, height, n, cols, dest_paths):
    # type: (str, int, int, int, int, list) -> list
    """Cut a sheet into its frames. Returns the distinct-colour count of each."""
    from PIL import Image
    im = Image.open(sheet_png).convert("RGB")
    rows = int(math.ceil(float(n) / cols))
    if im.size != (width * cols, height * rows):
        raise RenderError(
            "sheet came back %dx%d, expected %dx%d. Chrome did not honour the window "
            "size, so the slices would not line up with the frames."
            % (im.size[0], im.size[1], width * cols, height * rows))
    counts = []
    for i in range(n):
        left = (i % cols) * width
        top = (i // cols) * height
        cell = im.crop((left, top, left + width, top + height))
        cell.save(dest_paths[i])
        colours = cell.getcolors(maxcolors=1 << 22)
        counts.append(len(colours) if colours else (1 << 22))
    im.close()
    return counts


def distinct_colours(path):
    # type: (str) -> int
    from PIL import Image
    im = Image.open(path).convert("RGB")
    colours = im.getcolors(maxcolors=1 << 22)
    im.close()
    return len(colours) if colours else (1 << 22)


def render_frames(chrome, page, frames_dir, width, height, ts, per_sheet, jobs,
                  timeout, log, on_sheet=None):
    # type: (...) -> dict
    """Render every t in `ts` into frames_dir as %06d.png. Returns timing stats."""
    batches = [ts[i:i + per_sheet] for i in range(0, len(ts), per_sheet)]
    results = {"sheets": 0, "frames": 0, "seconds": 0.0, "thin": []}
    lock_paths = {}

    def one(batch_index):
        batch = batches[batch_index]
        base = batch_index * per_sheet
        n = len(batch)
        cols = sheet_grid(n)
        # The template steps t linearly from t0, so a batch has to be evenly spaced.
        t0 = batch[0]
        dt = (batch[1] - batch[0]) if n > 1 else 0.0
        sheet = os.path.join(frames_dir, "_sheet-%04d.png" % batch_index)
        dests = [os.path.join(frames_dir, "%06d.png" % (base + k)) for k in range(n)]
        started = time.time()
        shoot_sheet(chrome, page, sheet, width, height, t0, dt, n, cols, timeout)
        counts = slice_sheet(sheet, width, height, n, cols, dests)
        os.remove(sheet)
        elapsed = time.time() - started
        thin = [(base + k, counts[k]) for k in range(n)
                if counts[k] < MIN_DISTINCT_COLOURS]
        lock_paths[batch_index] = (n, elapsed, thin)
        if on_sheet:
            on_sheet(batch_index, n, elapsed)
        return batch_index

    if jobs <= 1:
        for i in range(len(batches)):
            one(i)
    else:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            list(pool.map(one, range(len(batches))))

    for n, elapsed, thin in lock_paths.values():
        results["sheets"] += 1
        results["frames"] += n
        results["seconds"] += elapsed
        results["thin"].extend(thin)
    return results


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def contact_sheet(frame_paths, dest, labels, cols=2, max_width=2400):
    # type: (list, str, list, int, int) -> None
    """Tile stills into one image, so a look can be judged in a single glance.

    Downscaled to `max_width` overall: a preview exists to be looked at quickly, and a
    3852px-wide contact sheet of 1080p stills is not quicker to look at than the render.
    """
    from PIL import Image, ImageDraw, ImageFont
    ims = [Image.open(p).convert("RGB") for p in frame_paths]
    rows = int(math.ceil(float(len(ims)) / cols))
    w, h = ims[0].size
    scale = min(1.0, float(max_width) / max(1, cols * w))
    w, h = max(1, int(w * scale)), max(1, int(h * scale))
    if scale < 1.0:
        ims = [im.resize((w, h), Image.LANCZOS) for im in ims]

    pad = max(4, w // 120)
    try:
        font = ImageFont.load_default(size=max(11, int(h * 0.035)))
    except TypeError:                       # Pillow < 10.1 has no size argument
        font = ImageFont.load_default()

    sheet = Image.new("RGB", (cols * w + pad * (cols + 1), rows * h + pad * (rows + 1)),
                      (18, 18, 22))
    draw = ImageDraw.Draw(sheet)
    for i, im in enumerate(ims):
        x = pad + (i % cols) * (w + pad)
        y = pad + (i // cols) * (h + pad)
        sheet.paste(im, (x, y))
        if i < len(labels):
            # A dark plate behind the caption so it stays legible on a light backdrop.
            tw = int(h * 0.035) * len(labels[i]) * 0.62 + pad
            draw.rectangle([x, y, x + tw + pad, y + int(h * 0.055)], fill=(18, 18, 22))
            draw.text((x + pad * 0.6, y + pad * 0.3), labels[i],
                      fill=(255, 255, 255), font=font)
        im.close()
    sheet.save(dest)


def encode_mp4(frames_dir, dest, fps, width, height, vcodec, log):
    # type: (str, str, float, int, int, str, callable) -> None
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RenderError("ffmpeg is not on PATH, so an mp4 cannot be written. "
                          "Point --out at a directory to keep the PNG sequence.")
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-framerate", "%g" % fps,
        "-i", os.path.join(frames_dir, "%06d.png"),
        "-c:v", vcodec or "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", "17",
        "-preset", "medium",
        "-movflags", "+faststart",
        "-r", "%g" % fps,
        dest,
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not os.path.isfile(dest):
        raise RenderError("ffmpeg failed:\n%s"
                          % proc.stderr.decode("utf-8", "replace")[-1200:])


def human_time(seconds):
    # type: (float) -> str
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return "%.0fs" % seconds
    if seconds < 5400:
        return "%.1f min" % (seconds / 60.0)
    return "%.1f hours" % (seconds / 3600.0)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, help="path to the scene JSON")
    ap.add_argument("--brand", default="channelplay", help="brand id (default channelplay)")
    ap.add_argument("--out", required=True,
                    help="output: a directory (PNG sequence), a .mp4, or a .png")
    ap.add_argument("--frames", type=int, default=90, help="frames to render (default 90)")
    ap.add_argument("--fps", type=float, default=None, help="frame rate (default: the brand's)")
    ap.add_argument("--width", type=int, default=None, help="frame width (default: the brand's)")
    ap.add_argument("--height", type=int, default=None, help="frame height (default: the brand's)")
    ap.add_argument("--per-sheet", type=int, default=6,
                    help="frames per Chrome launch (default 6). Higher amortises startup "
                         "further but needs a bigger window and more memory.")
    ap.add_argument("--jobs", type=int, default=None,
                    help="parallel Chrome workers (default: CPU count minus 2)")
    ap.add_argument("--still", type=float, default=None,
                    help="render one frame at this point on the clock (0..1) and stop")
    ap.add_argument("--preview", action="store_true",
                    help="render 4 stills across the clock into one contact sheet")
    ap.add_argument("--timeout", type=int, default=600, help="per-sheet Chrome timeout (s)")
    ap.add_argument("--yes", action="store_true",
                    help="do not pause for confirmation before a long render")
    ap.add_argument("--json", action="store_true", help="emit the result summary as JSON")
    ap.add_argument("--keep-work", action="store_true", help="keep the work directory")
    a = ap.parse_args(argv)

    quiet = a.json
    lines = []

    def log(msg=""):
        lines.append(msg)
        if not quiet:
            sys.stdout.write(msg + "\n")
            sys.stdout.flush()

    try:
        return run(a, log, quiet)
    except RenderError as exc:
        if a.json:
            sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}, indent=2) + "\n")
        else:
            sys.stderr.write("render_cgi: %s\n" % exc)
        return 1
    except bl.BrandNotFound as exc:
        sys.stderr.write("render_cgi: %s\n" % exc)
        return 1


def run(a, log, quiet):
    started_all = time.time()

    brand = bl.load_brand(a.brand)
    video = brand.get("video") or {}
    res = video.get("resolution") or {}
    width = int(a.width or res.get("w", 1920))
    height = int(a.height or res.get("h", 1080))
    fps = float(a.fps or video.get("fps", 30))
    vcodec = video.get("vcodec") or "libx264"

    scene_dir = os.path.dirname(os.path.abspath(a.data))
    raw_scene = load_scene(a.data)
    warnings = []
    scene = validate_scene(raw_scene, scene_dir, warnings)

    brand_warnings = []
    brand_vars = brand_style_block(brand, brand_warnings)
    warnings.extend(brand_warnings)

    chrome = find_chrome()
    if chrome is None:
        raise RenderError(
            "headless Chrome was not found. Set BRAND_STUDIO_CHROME to its path.")

    jobs = a.jobs if a.jobs else max(1, (os.cpu_count() or 4) - 2)
    per_sheet = max(1, int(a.per_sheet))

    out = os.path.abspath(a.out)
    out_ext = os.path.splitext(out)[1].lower()
    is_still = a.still is not None
    is_preview = bool(a.preview)
    if is_still and is_preview:
        raise RenderError("--still and --preview do the same job two different ways; "
                          "pick one.")

    log("brand      %s (%s)" % (brand.get("name"), a.brand))
    log("product    %s" % (scene.get("model") or
                           ("primitive: " + str(scene.get("primitive") or "bottle"))))
    log("studio     %s / %s / %s"
        % (scene.get("studio") or "gradient", scene.get("move") or "turntable",
           scene.get("lighting") or "studio"))
    log("canvas     %dx%d" % (width, height))
    for w in warnings:
        log("  warning: %s" % w)
    log("")

    work = tempfile.mkdtemp(prefix="brand-studio-cgi-")
    frames_dir = os.path.join(work, "frames")
    os.makedirs(frames_dir)
    summary = {"ok": True, "brand": a.brand, "scene": scene, "width": width,
               "height": height, "fps": fps, "seed": scene.get("seed", 7),
               "warnings": warnings, "chrome": chrome}

    try:
        page = prepare_workdir(work, scene, brand_vars)

        # ---------------------------------------------------------- single still
        if is_still:
            t = max(0.0, min(1.0, float(a.still)))
            dest = out
            if out_ext == "":
                os.makedirs(out, exist_ok=True)
                dest = os.path.join(out, "still.png")
            started = time.time()
            sheet = os.path.join(frames_dir, "still-sheet.png")
            shoot_sheet(chrome, page, sheet, width, height, t, 0.0, 1, 1, a.timeout)
            slice_sheet(sheet, width, height, 1, 1, [dest])
            elapsed = time.time() - started
            colours = distinct_colours(dest)
            log("still      t=%.3f  %s  %.1fs  %d distinct colours"
                % (t, dest, elapsed, colours))
            if colours < MIN_DISTINCT_COLOURS:
                raise RenderError(
                    "the frame is blank (%d distinct colours). The scene did not draw -- "
                    "check the fault banner in the PNG, or run with --keep-work and open "
                    "the page." % colours)
            summary.update({"mode": "still", "t": t, "out": dest,
                            "seconds": round(elapsed, 2), "colours": colours})

        # ---------------------------------------------------------- preview sheet
        elif is_preview:
            ts = [0.0, 0.34, 0.67, 1.0]
            dest = out
            if out_ext == "":
                os.makedirs(out, exist_ok=True)
                dest = os.path.join(out, "preview.png")
            started = time.time()
            # All four in one Chrome launch: the point of a preview is that it is cheap.
            sheet = os.path.join(frames_dir, "preview-sheet.png")
            shoot_sheet(chrome, page, sheet, width, height, 0.0, 1.0 / 3.0, 4, 2, a.timeout)
            stills = [os.path.join(frames_dir, "p%d.png" % i) for i in range(4)]
            counts = slice_sheet(sheet, width, height, 4, 2, stills)
            contact_sheet(stills, dest, ["t=%.2f" % t for t in ts], cols=2)
            elapsed = time.time() - started
            log("preview    4 stills at t=%s" % ", ".join("%.2f" % t for t in ts))
            log("           %s  %.1fs" % (dest, elapsed))
            blank = [i for i, c in enumerate(counts) if c < MIN_DISTINCT_COLOURS]
            if blank:
                raise RenderError(
                    "stills %s came back blank. The scene did not draw."
                    % ", ".join("t=%.2f" % ts[i] for i in blank))
            # Progression: if the clock is not wired to anything the four stills come
            # back byte-identical, which reads as a still in the finished film.
            digests = set()
            for p in stills:
                with open(p, "rb") as fh:
                    digests.add(hashlib.sha256(fh.read()).hexdigest())
            if len(digests) == 1 and (scene.get("move") or "turntable") != "static":
                log("  note: all four stills are identical, but the move is '%s'. The "
                    "clock is not driving anything." % (scene.get("move") or "turntable"))
            summary.update({"mode": "preview", "out": dest, "t": ts,
                            "seconds": round(elapsed, 2), "colours": counts})

        # ---------------------------------------------------------- full render
        else:
            frames = max(1, int(a.frames))
            ts = [i / float(frames - 1) if frames > 1 else 0.0 for i in range(frames)]

            # Calibrate on the first sheet. It is real output, not a throwaway probe,
            # so the measurement costs nothing and the projection is from this scene on
            # this machine rather than from a table in a README.
            probe_n = min(per_sheet, frames)
            probe_ts = ts[:probe_n]
            log("calibrating on the first sheet (%d frames)..." % probe_n)
            cal = render_frames(chrome, page, frames_dir, width, height, probe_ts,
                                per_sheet, 1, a.timeout, log)
            if cal["thin"]:
                raise RenderError(
                    "frame %d came back blank (%d distinct colours). The scene did not "
                    "draw, so the render was stopped before it wasted an hour."
                    % (cal["thin"][0][0], cal["thin"][0][1]))

            per_sheet_sec = cal["seconds"]
            serial_per_frame = per_sheet_sec / max(1, probe_n)
            remaining = frames - probe_n
            sheets_left = int(math.ceil(remaining / float(per_sheet))) if remaining else 0
            active = min(jobs, max(1, sheets_left))
            # The calibration sheet had the machine to itself, and SwiftShader already
            # spreads ONE render across several cores. So n workers do not give n times
            # the throughput -- measured here, 5 workers on 8 cores bought about 2x, not
            # 5x. Projecting on a linear speedup understates a long render roughly
            # two-fold, which is exactly the promise this projection exists not to break.
            eff = min(float(active), 1.0 + (active - 1) * PARALLEL_EFFICIENCY,
                      MAX_EFFECTIVE_WORKERS)
            projected = (sheets_left * per_sheet_sec) / eff if sheets_left else 0.0
            solo_fpm = probe_n * 60.0 / per_sheet_sec
            throughput = solo_fpm * eff

            log("")
            log("  measured   %.1fs per %d-frame sheet  (%.2fs/frame within a sheet)"
                % (per_sheet_sec, probe_n, serial_per_frame))
            log("  throughput ~%.0f frames/min at --jobs %d --per-sheet %d "
                "(%.0f/min single-threaded)" % (throughput, jobs, per_sheet, solo_fpm))
            log("  remaining  %d frames in %d sheets across %d worker(s)"
                % (remaining, sheets_left, active))
            log("  projected  ~%s for the remaining frames "
                "-- %.1fs of film at %g fps" % (human_time(projected), frames / fps, fps))
            log("")

            if remaining and projected > 180 and not a.yes:
                if not sys.stdin.isatty():
                    raise RenderError(
                        "this render is projected to take %s. Re-run with --yes to "
                        "commit to it unattended, or lower --frames."
                        % human_time(projected))
                sys.stdout.write("  continue? [y/N] ")
                sys.stdout.flush()
                if (sys.stdin.readline() or "").strip().lower() not in ("y", "yes"):
                    log("stopped before the long render; nothing was written.")
                    summary.update({"mode": "aborted", "projected_seconds": projected})
                    if a.json:
                        sys.stdout.write(json.dumps(summary, indent=2) + "\n")
                    return 0

            done = [probe_n]
            total_sheet_seconds = [per_sheet_sec]

            def progress(idx, n, elapsed):
                done[0] += n
                total_sheet_seconds[0] += elapsed
                log("  %4d/%d frames" % (done[0], frames))

            if remaining:
                rest_ts = ts[probe_n:]
                # Offset the filenames past the calibration frames.
                sub = os.path.join(work, "rest")
                os.makedirs(sub, exist_ok=True)
                res2 = render_frames(chrome, page, sub, width, height, rest_ts,
                                     per_sheet, jobs, a.timeout, log, on_sheet=progress)
                for k in range(len(rest_ts)):
                    shutil.move(os.path.join(sub, "%06d.png" % k),
                                os.path.join(frames_dir, "%06d.png" % (probe_n + k)))
                if res2["thin"]:
                    bad = ", ".join("#%d" % (probe_n + i) for i, _ in res2["thin"][:6])
                    log("  warning: frames %s look blank" % bad)

            wall = time.time() - started_all
            log("")
            log("  rendered   %d frames in %s  (%.1f frames/min wall clock)"
                % (frames, human_time(wall), frames * 60.0 / max(0.001, wall)))

            if out_ext in (".mp4", ".mov", ".webm", ".m4v"):
                encode_mp4(frames_dir, out, fps, width, height, vcodec, log)
                log("  wrote      %s" % out)
                summary.update({"mode": "video", "out": out, "frames": frames})
            elif out_ext == ".png":
                shutil.copy2(os.path.join(frames_dir, "%06d.png" % (frames // 2)), out)
                log("  wrote      %s (middle frame; --out a dir or .mp4 for the sequence)"
                    % out)
                summary.update({"mode": "still", "out": out, "frames": frames})
            else:
                os.makedirs(out, exist_ok=True)
                for i in range(frames):
                    shutil.copy2(os.path.join(frames_dir, "%06d.png" % i),
                                 os.path.join(out, "%06d.png" % i))
                log("  wrote      %d PNGs to %s" % (frames, out))
                summary.update({"mode": "sequence", "out": out, "frames": frames})

            summary.update({
                "seconds": round(wall, 2),
                "frames_per_min": round(frames * 60.0 / max(0.001, wall), 1),
                "per_sheet": per_sheet, "jobs": jobs,
                "sheet_seconds": round(per_sheet_sec, 2),
            })

        # ---------------------------------------------------------- sidecar
        sidecar = (os.path.splitext(summary.get("out", out))[0] + ".cgi.json")
        summary["rendered_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        summary["template"] = "templates/cgi/product.html"
        summary["command"] = " ".join(sys.argv)
        try:
            with open(sidecar, "w", encoding="utf-8") as fh:
                json.dump(summary, fh, indent=2, ensure_ascii=False)
            log("  sidecar    %s" % sidecar)
        except OSError as exc:
            log("  warning: could not write the sidecar (%s)" % exc)

    finally:
        if a.keep_work:
            sys.stderr.write("work kept at %s\n" % work)
        else:
            shutil.rmtree(work, ignore_errors=True)

    if a.json:
        sys.stdout.write(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
