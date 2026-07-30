#!/usr/bin/env python3
"""Per-brand asset cache: render once, reuse forever.

A brand's intro, outro, music bed and slide renders do not change between videos.
Regenerating them on every run is the single most expensive habit in this plugin --
it burns wall-clock, CPU and tokens, and it risks two videos for the same brand
opening differently. So they are produced once, stored under
``brands/<id>/video/generated/`` and reused until something that actually defines
them changes.

Reuse is keyed on a *fingerprint* of the inputs that genuinely determine the asset --
the logo file's bytes, the brand colours it draws with, the declared duration and
style. Change the logo or the palette and the fingerprint changes, so the asset is
rebuilt automatically. Change something unrelated and the cached file stands.

Generated assets live inside the brand directory rather than in ~/.cache on purpose:
they are shared with the team through the repo, so everyone's videos open with the
byte-identical intro.

Usage:
    asset_cache.py --brand channelplay --list
    asset_cache.py --brand channelplay --invalidate intro
    asset_cache.py --brand channelplay --invalidate all --yes

From Python:
    from asset_cache import AssetCache
    cache = AssetCache("channelplay")
    path, reused = cache.get_or_create("intro", inputs, produce_fn, force=False)
"""

import argparse
import hashlib
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import brandlib as bl  # noqa: E402

MANIFEST = "manifest.json"
KINDS = ("intro", "outro", "music-bed", "slides", "logo-plate")


def _hash_file(path):
    """Content hash of a file, or None when it is absent."""
    if not path or not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()[:16]


def fingerprint(inputs):
    """Stable fingerprint of a dict of asset-defining inputs.

    Any value whose key ends in ``_file`` or ``_path`` is hashed by CONTENT rather
    than by name, so replacing a logo with a different image of the same filename
    still invalidates the asset.
    """
    norm = {}
    for key in sorted(inputs):
        value = inputs[key]
        if isinstance(value, str) and (key.endswith("_file") or key.endswith("_path")):
            norm[key] = _hash_file(value) or ("missing:" + os.path.basename(value))
        elif isinstance(value, float):
            norm[key] = round(value, 4)
        else:
            norm[key] = value
    blob = json.dumps(norm, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class AssetCache(object):
    """Generated-asset store for one brand."""

    def __init__(self, brand_id, root=None):
        self.brand_id = brand_id
        self.root = root or bl.plugin_root()
        self.dir = os.path.join(self.root, "brands", brand_id, "video", "generated")
        if not os.path.isdir(self.dir):
            os.makedirs(self.dir)
        self.manifest_path = os.path.join(self.dir, MANIFEST)
        self.manifest = self._load()

    def _load(self):
        if not os.path.exists(self.manifest_path):
            return {"version": 1, "brand": self.brand_id, "assets": {}}
        try:
            with open(self.manifest_path, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data.get("assets"), dict):
                data["assets"] = {}
            return data
        except (ValueError, IOError):
            # A corrupt manifest must never block a build; rebuild everything instead.
            return {"version": 1, "brand": self.brand_id, "assets": {}}

    def _save(self):
        """Write the manifest atomically, merging anything another run added.

        A nightly run builds several artifacts at once -- a reel and a video for
        the same brand touch this file concurrently. A plain truncating write
        loses whichever entry lost the race and can leave a half-written file
        that the next run cannot parse. So: re-read, merge entries we do not
        hold, write to a temp file in the same directory, then rename, which is
        atomic on POSIX.
        """
        merged = dict(self.manifest)
        assets = dict(merged.get("assets") or {})
        try:
            with open(self.manifest_path, encoding="utf-8") as fh:
                on_disk = json.load(fh)
            for kind, entry in (on_disk.get("assets") or {}).items():
                # Ours wins for kinds we just wrote; theirs survives otherwise.
                if kind not in assets:
                    assets[kind] = entry
        except (IOError, ValueError):
            pass
        merged["assets"] = assets
        self.manifest = merged

        tmp = self.manifest_path + ".tmp.%d" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, indent=1, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, self.manifest_path)

    def path_for(self, kind, ext="mp4"):
        return os.path.join(self.dir, "%s.%s" % (kind, ext))

    def lookup(self, kind, inputs):
        """Return the cached path when it is present AND still valid, else None."""
        entry = self.manifest["assets"].get(kind)
        if not entry:
            return None
        path = os.path.join(self.dir, entry.get("file", ""))
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return None
        if entry.get("fingerprint") != fingerprint(inputs):
            return None
        return path

    def get_or_create(self, kind, inputs, produce, ext="mp4", force=False, stamp=None):
        """Return ``(path, reused)``.

        ``produce`` is called as ``produce(target_path)`` only when the asset is
        missing, stale or ``force`` is set. It must write the file at that path.
        """
        if not force:
            hit = self.lookup(kind, inputs)
            if hit:
                return hit, True

        target = self.path_for(kind, ext)
        produce(target)
        if not os.path.exists(target) or os.path.getsize(target) == 0:
            raise RuntimeError("producer for %r wrote nothing to %s" % (kind, target))

        self.manifest["assets"][kind] = {
            "file": os.path.basename(target),
            "fingerprint": fingerprint(inputs),
            "inputs": {k: (os.path.basename(v) if isinstance(v, str) and
                           (k.endswith("_file") or k.endswith("_path")) else v)
                       for k, v in inputs.items()},
            "bytes": os.path.getsize(target),
            "created": stamp or "",
        }
        self._save()
        return target, False

    def invalidate(self, kind):
        """Drop one cached asset. Returns True when something was removed."""
        entry = self.manifest["assets"].pop(kind, None)
        removed = False
        if entry:
            path = os.path.join(self.dir, entry.get("file", ""))
            if os.path.exists(path):
                try:
                    os.remove(path)
                    removed = True
                except OSError:
                    pass
            self._save()
        return removed

    def invalidate_all(self):
        kinds = list(self.manifest["assets"].keys())
        for kind in kinds:
            self.invalidate(kind)
        return len(kinds)

    def status(self):
        """[(kind, file, bytes, exists)] for every recorded asset."""
        rows = []
        for kind in sorted(self.manifest["assets"]):
            entry = self.manifest["assets"][kind]
            path = os.path.join(self.dir, entry.get("file", ""))
            rows.append((kind, entry.get("file", "-"), entry.get("bytes", 0),
                         os.path.exists(path)))
        return rows


def intro_inputs(brand, kind="intro"):
    """The inputs that genuinely define a brand's intro or outro.

    Deliberately narrow: only what is drawn. An unrelated edit elsewhere in
    brand.json must not throw away a valid intro.
    """
    spec = (brand.get("video") or {}).get(kind) or {}
    variants = (brand.get("logo") or {}).get("variants") or {}
    reversed_spec = variants.get("reversed") or variants.get("primary") or {}
    logo_rel = reversed_spec.get("file")
    logo_abs = None
    if logo_rel:
        logo_abs = os.path.join(bl.plugin_root(), "brands", brand.get("id", ""), logo_rel)
    gradient = ((brand.get("color") or {}).get("gradient") or {}).get("blue") or {}
    video = brand.get("video") or {}
    return {
        "kind": kind,
        "style": spec.get("style"),
        "duration": spec.get("durationSec"),
        "supplied_file": spec.get("file") or "",
        "logo_file": logo_abs or "",
        "gradient": gradient.get("stops"),
        "angle": gradient.get("angle"),
        "resolution": (video.get("resolution") or {}),
        "fps": video.get("fps"),
        "brand_name": brand.get("name"),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--brand", required=True, help="brand id")
    ap.add_argument("--list", action="store_true", help="show cached assets")
    ap.add_argument("--invalidate", metavar="KIND",
                    help="drop a cached asset: %s, or 'all'" % ", ".join(KINDS))
    ap.add_argument("--yes", action="store_true", help="skip the confirmation for 'all'")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)

    try:
        brand = bl.load_brand(a.brand)
    except Exception as exc:
        sys.stderr.write("asset_cache: %s\n" % exc)
        return 1

    cache = AssetCache(brand.get("id", a.brand))

    if a.invalidate:
        if a.invalidate == "all":
            if not a.yes:
                sys.stderr.write(
                    "Refusing to drop every generated asset for %r without --yes.\n"
                    "The next video build will re-render the intro and outro from scratch.\n"
                    % a.brand)
                return 1
            n = cache.invalidate_all()
            print("dropped %d cached asset(s) for %s" % (n, a.brand))
        else:
            ok = cache.invalidate(a.invalidate)
            print("%s %r for %s" % ("dropped" if ok else "nothing cached for",
                                    a.invalidate, a.brand))
        return 0

    rows = cache.status()
    if a.json:
        json.dump({"brand": a.brand, "dir": cache.dir,
                   "assets": [{"kind": k, "file": f, "bytes": b, "present": p}
                              for k, f, b, p in rows]}, sys.stdout, indent=1)
        sys.stdout.write("\n")
        return 0

    print("generated assets for %s" % a.brand)
    print("  dir: %s" % cache.dir)
    if not rows:
        print("  (nothing cached yet - the first video build will create the intro and outro)")
    for kind, fname, size, present in rows:
        print("  %-10s %-18s %8d bytes  %s" % (kind, fname, size,
                                               "ok" if present else "MISSING ON DISK"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
