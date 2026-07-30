#!/usr/bin/env bash
#
# brand-studio PostToolUse hook -- the enforcement loop.
#
# Runs after every Bash / Write / Edit tool call. If that call produced or
# touched a deck (.pptx/.potx) or a video (.mp4/.mov/.m4v), the artifact is run
# through scripts/validate.py. Errors are reported on stderr with exit 2, which
# Claude Code feeds back to the model -- that is what forces the
# fix -> rebuild -> re-validate loop instead of shipping a broken deck.
#
# Usage:
#   validate-artifact.sh            # reads the hook JSON payload on stdin
#   validate-artifact.sh --help
#   echo '{"tool_name":"Bash","tool_input":{"command":"... deck.pptx"}}' \
#       | validate-artifact.sh
#
# Exit codes:
#   0   nothing to check, or every artifact passed  (silent, no token cost)
#   1   internal failure (no interpreter, validator unrunnable) -- NON blocking,
#       the message goes to the user, not to the model
#   2   at least one artifact has errors -- blocking, stderr is fed to the model
#
# State:
#   Failing paths are recorded in
#     ${TMPDIR:-/tmp}/brand-studio-pending-${CLAUDE_SESSION_ID:-default}.txt
#   which hooks/session-gate.sh reads to block the session from ending dirty.
#   Marker format: line 1 is "attempts=N" (or the single word OVERRIDE),
#   every following line is one absolute artifact path.
#
# Escape hatch:
#   Writing OVERRIDE as the first line of the marker disables blocking for the
#   rest of the session. That is a decision for the user to make, not the agent.
#
# Environment:
#   BRAND_STUDIO_PYTHON      interpreter to use (default: the plugin venv)
#   BRAND_STUDIO_HOOK_MAX_AGE  seconds an artifact may be stale (default 600)
#   CLAUDE_PLUGIN_ROOT       plugin root (default: the parent of this script)
#   CLAUDE_SESSION_ID        session id, used to scope the marker file

set -euo pipefail

# ---------------------------------------------------------------- usage ----

usage() {
    awk 'NR==1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "${BASH_SOURCE[0]}"
}

for arg in "$@"; do
    case "$arg" in
        -h|--help) usage; exit 0 ;;
    esac
done

# ------------------------------------------------------------ locations ----

ROOT="${CLAUDE_PLUGIN_ROOT:-}"
if [ -z "${ROOT}" ]; then
    ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
fi

MARKER_DIR="${TMPDIR:-/tmp}"
MARKER_DIR="${MARKER_DIR%/}"
MARKER="${MARKER_DIR}/brand-studio-pending-${CLAUDE_SESSION_ID:-default}.txt"

# ---------------------------------------------------------------- stdin ----
# A hook is always fed JSON on stdin. When a human runs the script by hand the
# terminal is on stdin and reading it would hang, so that case is treated as an
# empty payload.

PAYLOAD=""
if [ ! -t 0 ]; then
    PAYLOAD="$(cat 2>/dev/null || true)"
fi

# ------------------------------------------------------------- fast path ----
# This hook fires on every Bash/Write/Edit call in the session, so the common
# case -- a tool call that has nothing to do with a deck or a video -- must cost
# nothing at all. No interpreter is started unless an artifact extension is
# literally present in the payload.

case "${PAYLOAD}" in
    *.pptx*|*.potx*|*.mp4*|*.mov*|*.m4v*) ;;
    *) exit 0 ;;
esac

# A user-approved override switches the whole gate off for this session.
if [ -f "${MARKER}" ]; then
    first_line=""
    IFS= read -r first_line < "${MARKER}" 2>/dev/null || first_line=""
    if [ "${first_line}" = "OVERRIDE" ]; then
        exit 0
    fi
fi

# -------------------------------------------------------------- python ------

PY=""
for candidate in \
    "${BRAND_STUDIO_PYTHON:-}" \
    "${HOME}/.cache/brand-studio/venv/bin/python" \
    "$(command -v python3 2>/dev/null || true)" \
    "/usr/bin/python3"
do
    if [ -n "${candidate}" ] && [ -x "${candidate}" ]; then
        PY="${candidate}"
        break
    fi
done

VALIDATE="${ROOT}/scripts/validate.py"

if [ -z "${PY}" ]; then
    printf 'brand-studio: no Python interpreter found; brand validation is OFF.\n' >&2
    printf '  Looked for $BRAND_STUDIO_PYTHON, ~/.cache/brand-studio/venv/bin/python, python3.\n' >&2
    exit 1
fi

if [ ! -f "${VALIDATE}" ]; then
    printf 'brand-studio: validator missing at %s; brand validation is OFF.\n' "${VALIDATE}" >&2
    printf '  Set CLAUDE_PLUGIN_ROOT to the brand-studio checkout.\n' >&2
    exit 1
fi

# ------------------------------------------------- candidate extraction -----
# Pull every deck/video path the tool call could have touched out of the hook
# payload, keep the ones that exist on disk and were written in the last ten
# minutes, and print them one per line.

# Assigned with `read -d ''` rather than $(cat <<EOF): bash 3.2 -- which is what
# macOS ships -- mis-parses quotes inside a heredoc nested in a command
# substitution, and this script has to run there.
EXTRACT_PY=""
IFS= read -r -d '' EXTRACT_PY <<'PYEOF' || true
import json
import os
import re
import shlex
import sys
import time

EXTS = (".pptx", ".potx", ".mp4", ".mov", ".m4v")
try:
    MAX_AGE = float(os.environ.get("BRAND_STUDIO_HOOK_MAX_AGE", "600"))
except ValueError:
    MAX_AGE = 600.0

# Keys on tool_input that name a file directly (Write, Edit, MultiEdit, and the
# handful of spellings other tools use).
PATH_KEYS = ("file_path", "filePath", "path", "notebook_path", "output",
             "output_path", "outputPath", "destination")


def clean(tok):
    """Strip shell noise off a token so a path can be recognised."""
    if not isinstance(tok, str):
        return ""
    tok = tok.strip()
    # redirections and pipes glued to the path: >deck.pptx, 2>out.mp4
    tok = tok.lstrip("<>|;&")
    # --out=deck.pptx / -o=film.mp4
    if tok.startswith("-") and "=" in tok:
        tok = tok.split("=", 1)[1]
    return tok.strip().strip("'\"")


def command_tokens(cmd):
    """Every token in a shell command line that could be a path.

    shlex handles quoted paths with spaces correctly; when the command does not
    lex (unbalanced quotes, heredocs) a regex sweep picks up the obvious cases
    so a real artifact is never missed because of an unrelated syntax quirk.
    """
    out = []
    try:
        out.extend(shlex.split(cmd, posix=True))
    except ValueError:
        pass
    for m in re.finditer(r'"([^"]+)"|\'([^\']+)\'|([^\s"\'<>|;&]+)', cmd):
        out.append(m.group(1) or m.group(2) or m.group(3))
    return out


def collect(payload):
    cands = []

    def push(value):
        value = clean(value)
        if value:
            cands.append(value)

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}

    for key in PATH_KEYS:
        push(tool_input.get(key))

    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, dict):
                for key in PATH_KEYS:
                    push(edit.get(key))

    command = tool_input.get("command")
    if isinstance(command, str) and command:
        for tok in command_tokens(command):
            push(tok)

    # Some tools report the file they actually wrote in the response.
    response = payload.get("tool_response")
    if isinstance(response, dict):
        for key in PATH_KEYS:
            push(response.get(key))
        for key in ("filenames", "files"):
            value = response.get(key)
            if isinstance(value, list):
                for item in value:
                    push(item)

    return cands


def main():
    try:
        raw = sys.stdin.read()
    except Exception:
        return 0
    if not raw or not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0

    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        cwd = os.getcwd()

    now = time.time()
    seen = set()
    out = []
    for cand in collect(payload):
        if "\n" in cand or "\r" in cand:
            continue
        if not cand.lower().endswith(EXTS):
            continue
        path = os.path.expanduser(cand)
        if not os.path.isabs(path):
            path = os.path.join(cwd, path)
        path = os.path.abspath(path)
        key = os.path.normcase(os.path.realpath(path))
        if key in seen:
            continue
        seen.add(key)
        try:
            if not os.path.isfile(path):
                continue
            if MAX_AGE > 0 and (now - os.path.getmtime(path)) > MAX_AGE:
                continue
            if os.path.getsize(path) <= 0:
                continue
        except OSError:
            continue
        out.append(path)

    for path in out:
        sys.stdout.write(path + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # An extraction bug must never break the user's tool call.
        sys.exit(0)
PYEOF

CANDIDATES="$(printf '%s' "${PAYLOAD}" | "${PY}" -c "${EXTRACT_PY}" 2>/dev/null || true)"

if [ -z "${CANDIDATES}" ]; then
    exit 0
fi

# ------------------------------------------------------------ validate ------

WORKDIR="$(mktemp -d "${MARKER_DIR}/brand-studio-hook.XXXXXX")"
cleanup() { rm -rf -- "${WORKDIR}"; }
trap cleanup EXIT INT TERM

index=0
while IFS= read -r artifact; do
    [ -n "${artifact}" ] || continue
    index=$((index + 1))
    printf '%s' "${artifact}" > "${WORKDIR}/${index}.path"
    set +e
    "${PY}" "${VALIDATE}" "${artifact}" --format json \
        > "${WORKDIR}/${index}.json" 2> "${WORKDIR}/${index}.err"
    printf '%s' "$?" > "${WORKDIR}/${index}.rc"
    set -e
done <<< "${CANDIDATES}"

if [ "${index}" -eq 0 ]; then
    exit 0
fi

# ------------------------------------------------- report + marker state ----

REPORT_PY=""
IFS= read -r -d '' REPORT_PY <<'PYEOF' || true
import json
import os
import subprocess
import sys

LINE_CAP = 40          # hard ceiling so a big failure cannot flood the context
MAX_WIDTH = 118

MARKER_HEADER = "attempts="


def read_text(path):
    try:
        fh = open(path, "r")
    except IOError:
        return ""
    try:
        return fh.read()
    finally:
        fh.close()


def trunc(text, width=MAX_WIDTH):
    # Plain ASCII only: hooks can run under LC_ALL=C, where writing a unicode
    # ellipsis to stdout raises UnicodeEncodeError and kills the report.
    text = " ".join(str(text or "").split())
    if len(text) <= width:
        return text
    return text[:max(1, width - 3)] + "..."


def short(path, cwd):
    try:
        rel = os.path.relpath(path, cwd)
    except ValueError:
        return path
    if not rel.startswith(".." + os.sep) and rel != ".." and len(rel) < len(path):
        return rel
    return path


def read_marker(path):
    """-> (override, attempts, [paths])"""
    if not os.path.isfile(path):
        return (False, 0, [])
    lines = read_text(path).splitlines()
    if not lines:
        return (False, 0, [])
    attempts = 0
    body = lines
    head = lines[0].strip()
    if head == "OVERRIDE":
        return (True, 0, [ln.strip() for ln in lines[1:] if ln.strip()])
    if head.startswith(MARKER_HEADER):
        try:
            attempts = int(head[len(MARKER_HEADER):].strip() or "0")
        except ValueError:
            attempts = 0
        body = lines[1:]
    return (False, attempts, [ln.strip() for ln in body if ln.strip()])


def write_marker(path, attempts, paths):
    if not paths:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    payload = ["%s%d" % (MARKER_HEADER, attempts)] + list(paths)
    tmp = path + ".tmp"
    fh = open(tmp, "w")
    try:
        fh.write("\n".join(payload) + "\n")
    finally:
        fh.close()
    os.rename(tmp, path)


def human_tail(python, validate_py, artifact, limit=10):
    """Fallback detail when the JSON report could not be parsed."""
    try:
        proc = subprocess.Popen(
            [python, validate_py, artifact, "--format", "human"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out, _ = proc.communicate()
    except (OSError, ValueError):
        return []
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    lines = [ln.rstrip() for ln in out.splitlines() if ln.strip()]
    return lines[:limit]


def load_results(run_dir, python, validate_py):
    results = []
    names = []
    for name in os.listdir(run_dir):
        if name.endswith(".rc"):
            stem = name[:-3]
            try:
                names.append((int(stem), stem))
            except ValueError:
                names.append((1 << 30, stem))
    names.sort()

    for _order, stem in names:
        artifact = read_text(os.path.join(run_dir, stem + ".path")).strip()
        if not artifact:
            continue
        try:
            rc = int(read_text(os.path.join(run_dir, stem + ".rc")).strip() or "1")
        except ValueError:
            rc = 1
        raw = read_text(os.path.join(run_dir, stem + ".json"))
        err = read_text(os.path.join(run_dir, stem + ".err")).strip()
        data = None
        if raw.strip():
            try:
                data = json.loads(raw)
            except ValueError:
                data = None
        if not isinstance(data, dict):
            data = None

        if rc == 0:
            status = "pass"
        elif rc == 2:
            status = "fail"
        else:
            status = "internal"

        detail = []
        if status == "fail" and data is None:
            detail = human_tail(python, validate_py, artifact)

        results.append({
            "path": artifact,
            "rc": rc,
            "status": status,
            "data": data,
            "err": err,
            "detail": detail,
        })
    return results


def group_errors(data):
    """[(violation id, count, example dict)] for errors only, biggest first."""
    groups = {}
    order = []
    for v in (data.get("violations") or []):
        if not isinstance(v, dict):
            continue
        if str(v.get("severity") or "").lower() != "error":
            continue
        vid = str(v.get("id") or "UNKNOWN")
        if vid not in groups:
            groups[vid] = {"count": 0, "example": v}
            order.append(vid)
        groups[vid]["count"] += 1
    rows = [(vid, groups[vid]["count"], groups[vid]["example"]) for vid in order]
    rows.sort(key=lambda row: -row[1])
    return rows


def counts_of(data):
    counts = data.get("counts") if isinstance(data, dict) else None
    if not isinstance(counts, dict):
        return (0, 0)
    def as_int(key):
        try:
            return int(counts.get(key) or 0)
        except (TypeError, ValueError):
            return 0
    return (as_int("error"), as_int("warn"))


def render(results, python, validate_py, marker, cwd):
    failing = [r for r in results if r["status"] == "fail"]
    broken = [r for r in results if r["status"] == "internal"]
    if not failing and not broken:
        return []

    total_errors = 0
    for r in failing:
        if r["data"] is not None:
            total_errors += counts_of(r["data"])[0]
    if total_errors == 0:
        total_errors = len(failing)

    lines = []
    if failing:
        lines.append(
            "brand-studio: BLOCKED. %d artifact(s) failed brand validation, "
            "%d error(s) total." % (len(failing), total_errors))
    else:
        lines.append("brand-studio: validator could not run on %d artifact(s)."
                     % len(broken))
    lines.append("")

    # Reserve room for the footer before spending any budget on detail.
    footer = []
    if failing:
        footer.append("")
        footer.append("Next action: fix the source IR, rebuild the artifact, then re-run:")
        footer.append("  %s %s %s --format human" % (
            python, validate_py, json.dumps(failing[0]["path"])))
        footer.append("Errors block the session from ending. Marker: %s" % marker)

    budget = LINE_CAP - len(lines) - len(footer)
    sections = failing + broken
    per_artifact = max(3, budget // max(1, len(sections)))

    for position, r in enumerate(sections):
        if budget <= 0:
            lines.append("... %d more artifact(s) not shown." % (
                len(sections) - position))
            break
        spent = 0
        label = short(r["path"], cwd)

        if r["status"] == "internal":
            lines.append("ERROR  %s -- validator exited %d" % (label, r["rc"]))
            spent += 1
            note = r["err"].splitlines()[-1] if r["err"] else "no diagnostic output"
            lines.append("       %s" % trunc(note))
            spent += 1
            budget -= spent
            continue

        if r["data"] is None:
            lines.append("FAIL   %s -- report was not valid JSON" % label)
            spent += 1
            for line in r["detail"]:
                if spent >= per_artifact:
                    break
                lines.append("       %s" % trunc(line))
                spent += 1
            budget -= spent
            continue

        errs, warns = counts_of(r["data"])
        lines.append("FAIL   %s -- %d error(s), %d warning(s)" % (label, errs, warns))
        spent += 1

        rows = group_errors(r["data"])
        shown = 0
        for vid, count, example in rows:
            if spent + 2 > per_artifact:
                break
            where = trunc(example.get("where") or "", 46)
            found = trunc(example.get("found") or "", 34)
            expected = trunc(example.get("expected") or "", 34)
            head = "%s x%d" % (vid, count)
            if where:
                head += "  at %s" % where
            if found:
                head += "  found %s" % found
            if expected:
                head += " -> expected %s" % expected
            # trunc() collapses whitespace, so indent after truncating.
            lines.append("  " + trunc(head, MAX_WIDTH - 2))
            spent += 1
            fix = trunc(example.get("fix") or example.get("rule") or "", MAX_WIDTH - 9)
            if fix:
                lines.append("      fix: %s" % fix)
                spent += 1
            shown += 1
        if shown < len(rows):
            lines.append("  ... %d more violation id(s) on this artifact."
                         % (len(rows) - shown))
            spent += 1
        budget -= spent

    # The footer names the next action, so it is never the thing that gets cut.
    body = lines[:max(1, LINE_CAP - len(footer))]
    return body + footer


def main(argv):
    if len(argv) < 5:
        sys.stderr.write("usage: report.py <run-dir> <marker> <python> <validate.py>\n")
        return 1
    run_dir, marker, python, validate_py = argv[1], argv[2], argv[3], argv[4]
    cwd = os.getcwd()

    results = load_results(run_dir, python, validate_py)
    if not results:
        return 0

    passing = set()
    failing = []
    for r in results:
        if r["status"] == "pass":
            passing.add(os.path.abspath(r["path"]))
        elif r["status"] == "fail":
            failing.append(os.path.abspath(r["path"]))

    override, attempts, listed = read_marker(marker)
    if not override:
        # Keep anything the marker already tracked unless we just proved it
        # passes, then add whatever failed in this run.
        keep = [p for p in listed if os.path.abspath(p) not in passing]
        for path in failing:
            if path not in keep:
                keep.append(path)
        write_marker(marker, attempts, keep)

    lines = render(results, python, validate_py, marker, cwd)
    if lines:
        sys.stdout.write("\n".join(lines) + "\n")

    if any(r["status"] == "fail" for r in results):
        return 2
    if any(r["status"] == "internal" for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
PYEOF

set +e
REPORT="$("${PY}" -c "${REPORT_PY}" "${WORKDIR}" "${MARKER}" "${PY}" "${VALIDATE}" 2>&1)"
STATUS=$?
set -e

case "${STATUS}" in
    0)
        # Everything passed. Silence is the point: no output, no token cost.
        exit 0
        ;;
    2)
        [ -n "${REPORT}" ] && printf '%s\n' "${REPORT}" >&2
        exit 2
        ;;
    *)
        [ -n "${REPORT}" ] && printf '%s\n' "${REPORT}" >&2
        printf 'brand-studio: validation could not complete (non-blocking).\n' >&2
        exit 1
        ;;
esac
