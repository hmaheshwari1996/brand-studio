#!/usr/bin/env bash
#
# brand-studio Stop hook -- the "do not stop until it is right" gate.
#
# hooks/validate-artifact.sh records every artifact that failed brand
# validation in a marker file. This hook runs when the agent tries to end its
# turn: it re-validates whatever is still listed, drops the ones that now pass,
# and blocks the stop (exit 2, stderr fed back to the model) while anything is
# still failing.
#
# Usage:
#   session-gate.sh              # normally invoked by Claude Code as a Stop hook
#   session-gate.sh --status     # show the marker state, change nothing, exit 0
#   session-gate.sh --clear      # forget the pending artifacts, exit 0
#   session-gate.sh --override   # user-approved escape hatch: stop blocking
#   session-gate.sh --help
#
# Exit codes:
#   0   nothing pending, everything passes now, or the loop guard escalated
#   1   internal failure (no interpreter, validator unrunnable) -- NON blocking
#   2   artifacts still fail -- blocks the stop and tells the model what to fix
#
# Loop guard:
#   The marker carries an attempt counter. After BRAND_STUDIO_MAX_STOP_ATTEMPTS
#   blocked stops (default 5) the gate stops blocking, says plainly that it is
#   escalating to the user, and exits 0. The agent can never be trapped here.
#
# Override:
#   If the first line of the marker file is the single word OVERRIDE, this gate
#   exits 0 immediately and hooks/validate-artifact.sh stops blocking too. Use
#   it when a violation is a deliberate, user-approved exception -- a client
#   logo that carries a retired colour, a legal line that must stay verbatim.
#   THE USER DECIDES THIS, NOT THE AGENT: ask, get an explicit yes, then run
#   `session-gate.sh --override`. Deleting the marker re-arms the gate.
#
# Environment:
#   BRAND_STUDIO_PYTHON             interpreter (default: the plugin venv)
#   BRAND_STUDIO_MAX_STOP_ATTEMPTS  blocked stops before escalating (default 5)
#   CLAUDE_PLUGIN_ROOT              plugin root (default: parent of this script)
#   CLAUDE_SESSION_ID               session id, scopes the marker file

set -euo pipefail

usage() {
    awk 'NR==1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "${BASH_SOURCE[0]}"
}

ROOT="${CLAUDE_PLUGIN_ROOT:-}"
if [ -z "${ROOT}" ]; then
    ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
fi

MARKER_DIR="${TMPDIR:-/tmp}"
MARKER_DIR="${MARKER_DIR%/}"
MARKER="${MARKER_DIR}/brand-studio-pending-${CLAUDE_SESSION_ID:-default}.txt"

MODE="gate"
for arg in "$@"; do
    case "${arg}" in
        -h|--help)  usage; exit 0 ;;
        --status)   MODE="status" ;;
        --clear)    MODE="clear" ;;
        --override) MODE="override" ;;
        *)
            printf 'session-gate: unknown option %s\n' "${arg}" >&2
            exit 1
            ;;
    esac
done

# --------------------------------------------------------- manual modes -----

if [ "${MODE}" = "clear" ]; then
    rm -f -- "${MARKER}"
    printf 'brand-studio: pending artifacts cleared (%s).\n' "${MARKER}"
    exit 0
fi

if [ "${MODE}" = "override" ]; then
    if [ -f "${MARKER}" ]; then
        { printf 'OVERRIDE\n'; cat -- "${MARKER}"; } > "${MARKER}.tmp"
        mv -- "${MARKER}.tmp" "${MARKER}"
    else
        printf 'OVERRIDE\n' > "${MARKER}"
    fi
    printf 'brand-studio: validation gate overridden for this session (%s).\n' "${MARKER}"
    printf 'Run session-gate.sh --clear to re-arm it.\n'
    exit 0
fi

if [ "${MODE}" = "status" ]; then
    if [ ! -f "${MARKER}" ]; then
        printf 'brand-studio: nothing pending.\n'
        exit 0
    fi
    printf 'brand-studio: marker %s\n' "${MARKER}"
    cat -- "${MARKER}"
    exit 0
fi

# ------------------------------------------------------------ fast path -----
# No marker means nothing ever failed in this session. Ending here keeps the
# hook free for every session that has nothing to do with brand-studio. Note
# that the Stop payload on stdin is deliberately not read: this gate's state
# lives in the marker file, and not reading stdin is what makes the common case
# cost nothing.

if [ ! -f "${MARKER}" ]; then
    exit 0
fi

first_line=""
IFS= read -r first_line < "${MARKER}" 2>/dev/null || first_line=""
if [ "${first_line}" = "OVERRIDE" ]; then
    exit 0
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

if [ -z "${PY}" ] || [ ! -f "${VALIDATE}" ]; then
    # Cannot re-check, so cannot honestly block. Say so and let the stop happen.
    printf 'brand-studio: cannot re-validate pending artifacts (interpreter or validator missing).\n' >&2
    printf '  Pending list: %s\n' "${MARKER}" >&2
    exit 1
fi

# ------------------------------------------------------------- re-check -----

# Assigned with `read -d ''` rather than $(cat <<EOF): bash 3.2 -- what macOS
# ships -- mis-parses quotes inside a heredoc nested in a command substitution.
GATE_PY=""
IFS= read -r -d '' GATE_PY <<'PYEOF' || true
import json
import os
import subprocess
import sys

LINE_CAP = 40
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


def trunc(text, width=110):
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
    lines = read_text(path).splitlines()
    if not lines:
        return (False, 0, [])
    head = lines[0].strip()
    if head == "OVERRIDE":
        return (True, 0, [])
    attempts = 0
    body = lines
    if head.startswith(MARKER_HEADER):
        try:
            attempts = int(head[len(MARKER_HEADER):].strip() or "0")
        except ValueError:
            attempts = 0
        body = lines[1:]
    seen = set()
    paths = []
    for line in body:
        line = line.strip()
        if not line or line in seen:
            continue
        seen.add(line)
        paths.append(line)
    return (False, attempts, paths)


def write_marker(path, attempts, paths):
    if not paths:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    tmp = path + ".tmp"
    fh = open(tmp, "w")
    try:
        fh.write("\n".join(["%s%d" % (MARKER_HEADER, attempts)] + list(paths)) + "\n")
    finally:
        fh.close()
    os.rename(tmp, path)


def validate(python, validate_py, artifact):
    """-> (rc, parsed report or None)"""
    try:
        proc = subprocess.Popen(
            [python, validate_py, artifact, "--format", "json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, _err = proc.communicate()
    except (OSError, ValueError):
        return (1, None)
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    data = None
    if out.strip():
        try:
            data = json.loads(out)
        except ValueError:
            data = None
    if not isinstance(data, dict):
        data = None
    return (proc.returncode, data)


def summarise(data):
    """-> (errors, warns, 'ID xN, ID xM')"""
    if not isinstance(data, dict):
        return (0, 0, "")
    counts = data.get("counts") or {}
    def as_int(key):
        try:
            return int(counts.get(key) or 0)
        except (TypeError, ValueError):
            return 0
    tally = {}
    order = []
    for v in (data.get("violations") or []):
        if not isinstance(v, dict):
            continue
        if str(v.get("severity") or "").lower() != "error":
            continue
        vid = str(v.get("id") or "UNKNOWN")
        if vid not in tally:
            tally[vid] = 0
            order.append(vid)
        tally[vid] += 1
    order.sort(key=lambda vid: -tally[vid])
    return (as_int("error"), as_int("warn"),
            ", ".join("%s x%d" % (vid, tally[vid]) for vid in order[:6]))


def main(argv):
    if len(argv) < 5:
        sys.stderr.write("usage: gate.py <marker> <python> <validate.py> <max-attempts>\n")
        return 1
    marker, python, validate_py = argv[1], argv[2], argv[3]
    try:
        max_attempts = int(argv[4])
    except ValueError:
        max_attempts = 5
    cwd = os.getcwd()

    override, attempts, paths = read_marker(marker)
    if override:
        return 0
    if not paths:
        try:
            os.remove(marker)
        except OSError:
            pass
        return 0

    still_failing = []
    for artifact in paths:
        if not os.path.isfile(artifact):
            # Deleted or renamed since it failed. Nothing left to gate on.
            continue
        rc, data = validate(python, validate_py, artifact)
        if rc == 0:
            continue
        if rc not in (0, 2) and data is None:
            # The validator itself broke. Do not manufacture a block out of it.
            sys.stderr.write(
                "brand-studio: validator exited %d on %s; not blocking on it.\n"
                % (rc, artifact))
            continue
        still_failing.append((artifact, data))

    if not still_failing:
        try:
            os.remove(marker)
        except OSError:
            pass
        return 0

    attempts += 1
    write_marker(marker, attempts, [p for p, _d in still_failing])

    lines = []
    if attempts >= max_attempts:
        lines.append(
            "brand-studio: ESCALATING TO THE USER. %d artifact(s) still fail brand "
            "validation after %d attempts." % (len(still_failing), attempts))
        lines.append("The gate is no longer blocking. Tell the user plainly what is still")
        lines.append("wrong and let them decide: fix it, accept it, or override the gate.")
    else:
        lines.append(
            "brand-studio: cannot finish -- %d artifact(s) still fail brand validation."
            % len(still_failing))
    lines.append("")

    budget = LINE_CAP - len(lines) - 6
    per_artifact = max(2, budget // max(1, len(still_failing)))
    for position, pair in enumerate(still_failing):
        artifact, data = pair
        if budget <= 0:
            lines.append("... %d more not shown." % (len(still_failing) - position))
            break
        errs, warns, ids = summarise(data)
        if data is None:
            lines.append("  %s -- still failing (report unreadable)" % short(artifact, cwd))
            budget -= 1
            continue
        lines.append("  %s -- %d error(s), %d warning(s)" % (short(artifact, cwd), errs, warns))
        budget -= 1
        if ids and per_artifact >= 2:
            lines.append("      %s" % trunc(ids))
            budget -= 1

    if attempts < max_attempts:
        lines.append("")
        lines.append("Fix the source IR, rebuild the artifact, then re-run:")
        lines.append("  %s %s %s --format human"
                     % (python, validate_py, json.dumps(still_failing[0][0])))
        lines.append("Attempt %d of %d. After %d blocked stops this gate escalates to the "
                     "user and stops blocking." % (attempts, max_attempts, max_attempts))
        lines.append("If a violation is a deliberate, user-approved exception, ask the user "
                     "first, then run:")
        lines.append("  %s --override" % os.environ.get("BRAND_STUDIO_GATE", "session-gate.sh"))

    sys.stdout.write("\n".join(lines[:LINE_CAP]) + "\n")
    return 0 if attempts >= max_attempts else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
PYEOF

MAX_ATTEMPTS="${BRAND_STUDIO_MAX_STOP_ATTEMPTS:-5}"
export BRAND_STUDIO_GATE="${ROOT}/hooks/session-gate.sh"

set +e
OUTPUT="$("${PY}" -c "${GATE_PY}" "${MARKER}" "${PY}" "${VALIDATE}" "${MAX_ATTEMPTS}" 2>&1)"
STATUS=$?
set -e

if [ -n "${OUTPUT}" ]; then
    printf '%s\n' "${OUTPUT}" >&2
fi

exit "${STATUS}"
