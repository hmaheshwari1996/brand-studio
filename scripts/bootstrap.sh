#!/usr/bin/env bash
#
# brand-studio environment bootstrap. Idempotent -- safe to run any number of
# times, and it does no network work when everything is already in place.
#
# What it does
#   1. Finds a Python that actually works. A stock Homebrew python3.14 on this
#      machine imports but blows up on xml.parsers.expat, which python-pptx
#      needs, so every candidate is probed by importing the modules we depend on
#      rather than by trusting its version number. The first one that passes
#      wins, which is normally /usr/bin/python3.
#   2. Creates ~/.cache/brand-studio/venv and installs python-pptx and pillow.
#   3. Verifies ffmpeg, ffprobe, soffice and headless Chrome.
#   4. Installs the bundled Poppins TTFs into the user font directory when they
#      are not already there.
#   5. Prints a PASS/FAIL table and exits non-zero if anything essential failed.
#
# Usage:
#   bootstrap.sh              # set up and report
#   bootstrap.sh --force      # rebuild the venv from scratch
#   bootstrap.sh --no-fonts   # skip the Poppins install
#   bootstrap.sh --quiet      # only print the table and failures
#   bootstrap.sh -h

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
CACHE_DIR="${HOME}/.cache/brand-studio"
VENV_DIR="${CACHE_DIR}/venv"
VENV_PY="${VENV_DIR}/bin/python"
FONT_SRC="${PLUGIN_ROOT}/brands/channelplay/assets/fonts"

FORCE=0
DO_FONTS=1
QUIET=0

# Print the header comment block: everything after the shebang up to the first
# non-comment line, with the leading '# ' stripped.
usage() {
    awk 'NR==1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "${BASH_SOURCE[0]}"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help)     usage; exit 0 ;;
        --force)       FORCE=1 ;;
        --no-fonts)    DO_FONTS=0 ;;
        -q|--quiet)    QUIET=1 ;;
        *) printf 'bootstrap: unknown option %s\n\n' "$1" >&2; usage >&2; exit 1 ;;
    esac
    shift
done

# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

ROWS=()
FAILED=0

say() { [ "${QUIET}" -eq 1 ] || printf '%s\n' "$*"; }

# record <name> <PASS|WARN|FAIL> <detail>
record() {
    local name="$1" status="$2" detail="${3:-}"
    ROWS+=("${name}|${status}|${detail}")
    if [ "${status}" = "FAIL" ]; then
        FAILED=$((FAILED + 1))
    fi
}

print_table() {
    printf '\n'
    printf '  %-22s %-6s %s\n' "COMPONENT" "STATUS" "DETAIL"
    printf '  %-22s %-6s %s\n' "----------------------" "------" "----------------------------------------"
    local row name status detail
    for row in "${ROWS[@]}"; do
        name="${row%%|*}"
        row="${row#*|}"
        status="${row%%|*}"
        detail="${row#*|}"
        printf '  %-22s %-6s %s\n' "${name}" "${status}" "${detail}"
    done
    printf '\n'
}

# ---------------------------------------------------------------------------
# 1. Python discovery
# ---------------------------------------------------------------------------

# A candidate is only good if it can import everything the plugin actually uses.
# Version numbers lie; imports do not.
python_works() {
    local py="$1"
    [ -n "${py}" ] || return 1
    [ -x "${py}" ] || return 1
    "${py}" - <<'PY' >/dev/null 2>&1
import sys
if sys.version_info[:2] < (3, 8):
    raise SystemExit(1)
import xml.parsers.expat   # python-pptx parses OOXML through this
import venv                # we need to build a virtualenv with it
import ensurepip           # ... and that virtualenv needs pip
import zlib, sqlite3, ssl  # wheels and downloads need these
PY
}

candidate_pythons() {
    local c
    printf '%s\n' "${BRAND_STUDIO_PYTHON:-}"
    printf '%s\n' /usr/bin/python3
    for c in python3.13 python3.12 python3.11 python3.10 python3.9; do
        command -v "${c}" 2>/dev/null || true
    done
    printf '%s\n' /usr/local/bin/python3
    command -v python3 2>/dev/null || true
}

HOST_PY=""
REJECTED=""
while IFS= read -r cand; do
    [ -n "${cand}" ] || continue
    [ -x "${cand}" ] || continue
    if python_works "${cand}"; then
        HOST_PY="${cand}"
        break
    fi
    REJECTED="${REJECTED} ${cand}"
done < <(candidate_pythons)

if [ -z "${HOST_PY}" ]; then
    record "python (host)" "FAIL" "no working interpreter found; tried:${REJECTED:- none}"
    print_table
    printf 'bootstrap: no Python with a working xml.parsers.expat was found.\n' >&2
    printf '           Install the python.org build, or run: xcode-select --install\n' >&2
    exit 1
fi

HOST_VER="$("${HOST_PY}" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
if [ -n "${REJECTED// /}" ]; then
    record "python (host)" "PASS" "${HOST_PY} ${HOST_VER}  (skipped:${REJECTED})"
else
    record "python (host)" "PASS" "${HOST_PY} ${HOST_VER}"
fi
say "Using ${HOST_PY} (${HOST_VER})"

# ---------------------------------------------------------------------------
# 2. virtualenv
# ---------------------------------------------------------------------------

mkdir -p "${CACHE_DIR}"

venv_healthy() {
    [ -x "${VENV_PY}" ] || return 1
    "${VENV_PY}" -c 'import xml.parsers.expat' >/dev/null 2>&1 || return 1
    return 0
}

# A rebuild needs the network, and the network is exactly the thing that fails.
# The old venv is moved aside rather than deleted, so a failed rebuild -- on a
# plane, behind a proxy, mid-outage -- leaves the machine no worse than it was.
STASH=""
restore_stash() {
    if [ -n "${STASH}" ] && [ -d "${STASH}" ]; then
        printf 'bootstrap: rebuild failed; restoring the previous venv\n' >&2
        rm -rf "${VENV_DIR}"
        mv "${STASH}" "${VENV_DIR}"
        STASH=""
    fi
}
trap restore_stash EXIT

stash_venv() {
    [ -d "${VENV_DIR}" ] || return 0
    STASH="${VENV_DIR}.previous"
    rm -rf "${STASH}"
    mv "${VENV_DIR}" "${STASH}"
}

if [ "${FORCE}" -eq 1 ] && [ -d "${VENV_DIR}" ]; then
    say "Rebuilding venv (--force)"
    stash_venv
elif ! venv_healthy && [ -d "${VENV_DIR}" ]; then
    say "Existing venv is unusable, rebuilding it"
    stash_venv
fi

if ! venv_healthy; then
    say "Creating venv at ${VENV_DIR}"
    if ! "${HOST_PY}" -m venv "${VENV_DIR}" >/dev/null 2>&1; then
        record "venv" "FAIL" "${HOST_PY} -m venv failed at ${VENV_DIR}"
        print_table
        exit 1
    fi
fi

if ! venv_healthy; then
    record "venv" "FAIL" "${VENV_PY} exists but cannot import xml.parsers.expat"
    print_table
    exit 1
fi

VENV_VER="$("${VENV_PY}" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
record "venv" "PASS" "${VENV_DIR} (python ${VENV_VER})"

# ---------------------------------------------------------------------------
# 3. python packages
# ---------------------------------------------------------------------------

deps_present() {
    "${VENV_PY}" - <<'PY' >/dev/null 2>&1
import pptx, PIL          # noqa: F401
from pptx.util import Emu # noqa: F401
PY
}

if deps_present && [ "${FORCE}" -eq 0 ]; then
    say "python-pptx and pillow already installed"
else
    say "Installing python-pptx and pillow (this needs the network once)"
    "${VENV_PY}" -m pip install --upgrade pip >/dev/null 2>&1 || \
        say "  note: could not upgrade pip, continuing with the bundled one"
    if ! "${VENV_PY}" -m pip install --disable-pip-version-check python-pptx pillow >/tmp/brand-studio-pip.log 2>&1; then
        record "python-pptx" "FAIL" "pip install failed, see /tmp/brand-studio-pip.log"
        record "pillow" "FAIL" "pip install failed, see /tmp/brand-studio-pip.log"
        print_table
        exit 1
    fi
fi

if deps_present; then
    PPTX_VER="$("${VENV_PY}" -c 'import pptx; print(pptx.__version__)' 2>/dev/null || echo '?')"
    PIL_VER="$("${VENV_PY}" -c 'import PIL; print(PIL.__version__)' 2>/dev/null || echo '?')"
    record "python-pptx" "PASS" "${PPTX_VER}"
    record "pillow" "PASS" "${PIL_VER}"
    # The new venv works. Commit to it and drop the stashed one.
    if [ -n "${STASH}" ]; then
        rm -rf "${STASH}"
        STASH=""
    fi
else
    record "python-pptx" "FAIL" "not importable after install"
    record "pillow" "FAIL" "not importable after install"
    print_table
    exit 1
fi

# ---------------------------------------------------------------------------
# 4. external binaries
# ---------------------------------------------------------------------------

check_bin() {
    local label="$1" bin="$2" essential="$3" hint="${4:-}"
    local path
    if path="$(command -v "${bin}" 2>/dev/null)"; then
        record "${label}" "PASS" "${path}"
    elif [ "${essential}" = "essential" ]; then
        record "${label}" "FAIL" "not on PATH. ${hint}"
    else
        record "${label}" "WARN" "not on PATH. ${hint}"
    fi
}

check_bin "ffmpeg"   "ffmpeg"   "essential" "brew install ffmpeg"
check_bin "ffprobe"  "ffprobe"  "essential" "brew install ffmpeg"
check_bin "soffice"  "soffice"  "essential" "brew install --cask libreoffice"
check_bin "pdftoppm" "pdftoppm" "optional"  "brew install poppler (needed by render_preview.sh)"

CHROME=""
for c in \
    "${BRAND_STUDIO_CHROME:-}" \
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    "/Applications/Chromium.app/Contents/MacOS/Chromium" \
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" \
    "/usr/bin/google-chrome" \
    "/usr/bin/chromium" \
    "/usr/bin/chromium-browser"
do
    if [ -n "${c}" ] && [ -x "${c}" ]; then
        CHROME="${c}"
        break
    fi
done

if [ -n "${CHROME}" ]; then
    record "chrome (headless)" "PASS" "${CHROME}"
else
    record "chrome (headless)" "FAIL" "install Google Chrome, or set BRAND_STUDIO_CHROME (icons need it)"
fi

# macOS speech synthesis powers the default voiceover engine.
if [ "$(uname -s)" = "Darwin" ]; then
    check_bin "say (voiceover)" "say" "optional" "macOS only; video VO falls back to silence"
fi

# ---------------------------------------------------------------------------
# 5. fonts
# ---------------------------------------------------------------------------

font_dir() {
    if [ "$(uname -s)" = "Darwin" ]; then
        printf '%s\n' "${HOME}/Library/Fonts"
    else
        printf '%s\n' "${HOME}/.local/share/fonts"
    fi
}

font_installed() {
    local base="$1" d
    for d in "${HOME}/Library/Fonts" "/Library/Fonts" "/System/Library/Fonts" \
             "${HOME}/.fonts" "${HOME}/.local/share/fonts" "/usr/share/fonts" "/usr/local/share/fonts"
    do
        [ -d "${d}" ] || continue
        if [ -f "${d}/${base}" ]; then
            return 0
        fi
    done
    return 1
}

if [ "${DO_FONTS}" -eq 0 ]; then
    record "Poppins fonts" "WARN" "skipped (--no-fonts)"
elif [ ! -d "${FONT_SRC}" ]; then
    record "Poppins fonts" "WARN" "no bundled fonts at ${FONT_SRC}"
else
    DEST="$(font_dir)"
    mkdir -p "${DEST}"
    INSTALLED=0
    MISSING=""
    for ttf in "${FONT_SRC}"/*.ttf "${FONT_SRC}"/*.otf; do
        [ -e "${ttf}" ] || continue
        base="$(basename -- "${ttf}")"
        if font_installed "${base}"; then
            continue
        fi
        if cp -- "${ttf}" "${DEST}/${base}" 2>/dev/null; then
            INSTALLED=$((INSTALLED + 1))
            say "Installed ${base} into ${DEST}"
        else
            MISSING="${MISSING} ${base}"
        fi
    done
    if [ -n "${MISSING// /}" ]; then
        record "Poppins fonts" "WARN" "could not install:${MISSING}"
    elif [ "${INSTALLED}" -gt 0 ]; then
        record "Poppins fonts" "PASS" "${INSTALLED} installed into ${DEST}"
    else
        record "Poppins fonts" "PASS" "already present"
    fi
fi

# ---------------------------------------------------------------------------
# 6. plugin self-check
# ---------------------------------------------------------------------------

if [ -f "${SCRIPT_DIR}/lib/brandlib.py" ]; then
    if "${VENV_PY}" "${SCRIPT_DIR}/lib/brandlib.py" --quiet >/dev/null 2>&1; then
        record "brandlib self-test" "PASS" "all assertions passed"
    else
        record "brandlib self-test" "FAIL" "${VENV_PY} ${SCRIPT_DIR}/lib/brandlib.py"
    fi
else
    record "brandlib self-test" "FAIL" "scripts/lib/brandlib.py is missing"
fi

if [ -f "${PLUGIN_ROOT}/grammar/deck-grammar.json" ]; then
    record "deck grammar" "PASS" "${PLUGIN_ROOT}/grammar/deck-grammar.json"
else
    record "deck grammar" "FAIL" "grammar/deck-grammar.json is missing"
fi

BRAND_COUNT="$(find "${PLUGIN_ROOT}/brands" -maxdepth 2 -name brand.json 2>/dev/null | wc -l | tr -d ' ')"
if [ "${BRAND_COUNT}" -gt 0 ]; then
    record "brands" "PASS" "${BRAND_COUNT} profile(s) in ${PLUGIN_ROOT}/brands"
else
    record "brands" "WARN" "no brand profiles yet; create one with new_brand.py"
fi

# ---------------------------------------------------------------------------
# result
# ---------------------------------------------------------------------------

print_table

if [ "${FAILED}" -gt 0 ]; then
    printf 'bootstrap: %d essential component(s) missing. Fix the FAIL rows above and re-run.\n' "${FAILED}" >&2
    exit 1
fi

printf 'bootstrap: ready. Python -> %s\n' "${VENV_PY}"
exit 0
