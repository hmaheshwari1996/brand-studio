#!/usr/bin/env python3
"""brand-studio validation dispatcher.

This is the single entry point the PostToolUse hook calls after any artifact is
written. It looks at the file extension, picks the right specialist validator,
forwards the arguments, and proxies the child's stdout and exit code verbatim.
It merges nothing and reformats nothing -- FROZEN CONTRACT C belongs to the
child validators, and this script must never be able to change their verdict.

Dispatch table
    .pptx .potx   ->  scripts/validate_deck.py    (kind: deck)
    .mp4 .mov .m4v ->  scripts/validate_video.py  (kind: video)
    anything else ->  exit 0 silently, so the hook never blocks unrelated work.

Sidecars are attached automatically. If a video (or deck) has a sibling
``<artifact>.timeline.json`` / ``<basename>.timeline.json`` or
``<basename>.srt`` / ``<artifact>.srt`` on disk, the path is handed to the
child -- but only after probing the child's --help to confirm it advertises a
flag for it, and only when the caller did not already pass one. A validator
that does not want sidecars is never handed one.

Exit codes
    0   the artifact passed, or the extension is not ours (silent skip), or --help
    1   internal failure -- bad usage, missing artifact, missing/unrunnable child
    2   the artifact failed validation (counts.error > 0), proxied from the child
    *   any other code the child returns is proxied unchanged

Usage:
    validate.py deck.pptx
    validate.py deck.pptx --brand example --format human
    validate.py film.mp4 --brand example          # picks up film.srt if present
    validate.py film.mp4 --dry-run                    # print the child command only
    validate.py notes.txt                             # exit 0, prints nothing
"""

import argparse
import os
import shlex
import subprocess
import sys

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_VIOLATIONS = 2

# extension -> (child script basename, kind label)
DISPATCH = {
    ".pptx": ("validate_deck.py", "deck"),
    ".potx": ("validate_deck.py", "deck"),
    ".mp4": ("validate_video.py", "video"),
    ".mov": ("validate_video.py", "video"),
    ".m4v": ("validate_video.py", "video"),
}

# Sidecars to look for, per kind, as (suffix, candidate flag names). The first
# flag the child advertises in its --help wins.
#
# The candidates are deliberately scoped by kind rather than shared. Both
# validators accept an "IR" but they mean different documents: validate_deck.py
# --ir wants the Deck IR (contract A) and validate_video.py --timeline wants the
# Video IR (contract B). Handing a deck a <name>.timeline.json under --ir would
# feed it the wrong contract entirely, so a deck is never offered --ir for
# anything but its own .ir.json.
SRT_FLAGS = ("--srt", "--captions", "--caption-file", "--subtitles")
SIDECAR_RULES = {
    "deck": (
        (".ir.json", ("--ir", "--deck-ir")),
        (".timeline.json", ("--timeline", "--timeline-json")),
        (".srt", SRT_FLAGS),
    ),
    "video": (
        (".timeline.json", ("--timeline", "--timeline-json", "--video-ir", "--ir")),
        (".srt", SRT_FLAGS),
    ),
}


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on usage errors, which collides with 'validation failed'."""

    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write("%s: error: %s\n" % (self.prog, message))
        raise SystemExit(EXIT_INTERNAL)


def script_dir():
    # type: () -> str
    return os.path.dirname(os.path.abspath(__file__))


def find_python():
    # type: () -> str
    """The interpreter used to run the child validators.

    Honours $BRAND_STUDIO_PYTHON, then the plugin venv, then whatever is
    running this script.
    """
    env = os.environ.get("BRAND_STUDIO_PYTHON")
    if env and os.path.isfile(env) and os.access(env, os.X_OK):
        return env
    venv = os.path.join(os.path.expanduser("~"), ".cache", "brand-studio", "venv", "bin", "python")
    if os.path.isfile(venv) and os.access(venv, os.X_OK):
        return venv
    return sys.executable or "python3"


def sidecar_candidates(artifact, suffix):
    # type: (str, str) -> list
    """Both spellings of a sidecar: full-path suffix and replaced extension."""
    stem = os.path.splitext(artifact)[0]
    out = []
    for cand in (artifact + suffix, stem + suffix):
        if cand not in out:
            out.append(cand)
    return out


def find_sidecar(artifact, suffix):
    # type: (str, str) -> str
    """First existing sidecar path for ``suffix``, or '' when there is none."""
    for cand in sidecar_candidates(artifact, suffix):
        if os.path.isfile(cand):
            return cand
    return ""


def child_help(python, child):
    # type: (str, str) -> str
    """Capture the child's --help so we only pass flags it actually accepts.

    Returns '' when the probe fails for any reason; the caller then simply
    skips the optional passthrough rather than risking an unknown-flag abort.
    """
    try:
        proc = subprocess.Popen(
            [python, child, "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        out, _ = proc.communicate()
    except (OSError, ValueError):
        return ""
    if proc.returncode != 0:
        return ""
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    return out or ""


def advertises(help_text, flag):
    # type: (str, str) -> bool
    """True when --help lists ``flag`` as a whole token.

    Substring matching would see '--srt' inside '--srt-style' and pass a flag
    the child does not have, which argparse turns into an abort.
    """
    idx = 0
    while True:
        idx = help_text.find(flag, idx)
        if idx < 0:
            return False
        end = idx + len(flag)
        nxt = help_text[end:end + 1]
        if nxt == "" or not (nxt.isalnum() or nxt in "-_"):
            return True
        idx = end


def pick_flag(help_text, candidates, already_used):
    # type: (str, tuple, set) -> str
    """First candidate flag the child advertises, or '' when we must not guess.

    An explicit flag from the caller always wins: if they named this sidecar
    themselves, we add nothing.
    """
    if not help_text:
        return ""
    for flag in candidates:
        if flag in already_used:
            return ""
    for flag in candidates:
        if advertises(help_text, flag):
            return flag
    return ""


def flags_in(extras):
    # type: (list) -> set
    """Every flag-looking token the caller passed, with '--x=y' normalised."""
    used = set()
    for tok in extras:
        if isinstance(tok, str) and tok.startswith("-"):
            used.add(tok.split("=", 1)[0])
    return used


def build_command(args, extras):
    # type: (argparse.Namespace, list) -> tuple
    """Return (cmd, note) or (None, reason) when this artifact is not ours."""
    artifact = os.path.abspath(args.artifact)
    ext = os.path.splitext(artifact)[1].lower()

    entry = DISPATCH.get(ext)
    if entry is None:
        return (None, "extension %s is not handled by brand-studio" % (ext or "(none)"))

    child_name, kind = entry
    child = os.path.join(script_dir(), child_name)
    if not os.path.isfile(child):
        raise RuntimeError(
            "validator %s is missing. Expected it at %s." % (child_name, child))

    if not os.path.isfile(artifact):
        raise RuntimeError("artifact not found: %s" % artifact)

    python = find_python()
    cmd = [python, child, artifact]

    used = flags_in(extras)
    if args.brand and "--brand" not in used:
        cmd += ["--brand", args.brand]
    if "--format" not in used and "-f" not in used:
        cmd += ["--format", args.format]

    found = []
    for suffix, candidates in SIDECAR_RULES.get(kind, ()):
        path = find_sidecar(artifact, suffix)
        if path:
            found.append((path, candidates))

    attached = []
    if found:
        # Probe the child once rather than guessing: a flag it does not
        # advertise would abort the run, and a validator that wants no sidecar
        # must never be handed one.
        help_text = child_help(python, child)
        for path, candidates in found:
            flag = pick_flag(help_text, candidates, used)
            if flag:
                cmd += [flag, path]
                attached.append("%s %s" % (flag, path))

    cmd += list(extras)
    note = "%s -> %s%s" % (
        kind, child_name,
        ("  [sidecars: %s]" % "; ".join(attached)) if attached else "")
    return (cmd, note)


def main(argv=None):
    # type: (list) -> int
    ap = _Parser(
        prog="validate.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("artifact", help="path to the .pptx/.potx or .mp4/.mov/.m4v to validate")
    ap.add_argument("--brand", default=None, help="brand id, forwarded to the validator")
    ap.add_argument("--format", default="json", choices=["json", "human"],
                    help="output format, forwarded to the validator (default: json)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the child command that would run, then exit 0")

    args, extras = ap.parse_known_args(argv)

    try:
        cmd, note = build_command(args, extras)
    except RuntimeError as exc:
        sys.stderr.write("validate: %s\n" % exc)
        return EXIT_INTERNAL

    if cmd is None:
        # Not an artifact we own. Stay silent so the hook never blocks work.
        return EXIT_OK

    if args.dry_run:
        sys.stdout.write("%s\n" % note)
        # Quoted so the line can be pasted straight into a shell; paths in this
        # plugin routinely contain spaces.
        sys.stdout.write("%s\n" % " ".join(shlex.quote(c) for c in cmd))
        return EXIT_OK

    try:
        # No capture: the child owns stdout and stderr, and its exit code is
        # returned untouched.
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        sys.stderr.write("validate: cannot run %s: %s\n" % (cmd[1], exc))
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
