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
#   5. OPTIONALLY pre-installs Piper neural voices, only when --voices asks for
#      them by name. Nothing is downloaded by default: a voice is about 63 MB,
#      and an unattended nightly build is the wrong place to discover that.
#      Pre-install the ones a brand actually narrates in, once, here.
#   6. Prints a PASS/FAIL table and exits non-zero if anything essential failed.
#
# Usage:
#   bootstrap.sh              # set up and report
#   bootstrap.sh --force      # rebuild the venv from scratch
#   bootstrap.sh --no-fonts   # skip the Poppins install
#   bootstrap.sh --voices "hi_IN-pratham-medium,en_GB-alba-medium"
#                             # pre-download these Piper voices (comma separated)
#   bootstrap.sh --quiet      # only print the table and failures
#   bootstrap.sh -h

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
CACHE_DIR="${HOME}/.cache/brand-studio"
VENV_DIR="${CACHE_DIR}/venv"
VENV_PY="${VENV_DIR}/bin/python"
FONT_SRC="${PLUGIN_ROOT}/brands/example/assets/fonts"
# Where make_voice.py looks for Piper models. Kept identical on purpose.
VOICES_DIR="${CACHE_DIR}/voices"
PIPER_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main"
# The likeliest second language for this agency. Named in the hint, never
# downloaded unless someone asks for it.
VOICE_SUGGESTION="hi_IN-pratham-medium"

FORCE=0
DO_FONTS=1
QUIET=0
VOICES=""

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
        --voices)
            shift
            [ "$#" -gt 0 ] || { printf 'bootstrap: --voices needs a comma-separated list, e.g. --voices "%s"\n' "${VOICE_SUGGESTION}" >&2; exit 1; }
            VOICES="$1"
            ;;
        --voices=*)    VOICES="${1#--voices=}" ;;
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
# 6. Piper voices (optional, and off by default)
# ---------------------------------------------------------------------------
#
# make_voice.py can fetch a voice on demand, but a nightly run is exactly the
# wrong moment to discover that a 63 MB model is missing: it stalls an
# unattended build behind a download that may not even be reachable. So the
# voices a brand actually narrates in are pulled here, once, deliberately.
#
# NOTHING is downloaded unless --voices names something. The table always says
# what is already on the disk either way.

# 'hi_IN-pratham-medium' -> hi hi_IN pratham medium
voice_parts() {
    local id="$1" locale rest name quality lang
    case "${id}" in
        *-*-*) : ;;
        *) return 1 ;;
    esac
    locale="${id%%-*}"
    rest="${id#*-}"
    quality="${rest##*-}"
    name="${rest%-*}"
    lang="$(printf '%s' "${locale%%_*}" | tr '[:upper:]' '[:lower:]')"
    [ -n "${lang}" ] && [ -n "${name}" ] && [ -n "${quality}" ] || return 1
    printf '%s %s %s %s\n' "${lang}" "${locale}" "${name}" "${quality}"
}

installed_voices() {
    local f base
    [ -d "${VOICES_DIR}" ] || return 0
    for f in "${VOICES_DIR}"/*.onnx; do
        [ -e "${f}" ] || continue
        base="$(basename -- "${f}" .onnx)"
        # A model without its config is unusable, so it does not count.
        [ -f "${VOICES_DIR}/${base}.onnx.json" ] || continue
        printf '%s\n' "${base}"
    done
}

# fetch <url> <destination> -- to a .part file first, so an interrupted download
# can never masquerade as an installed voice.
fetch_voice_file() {
    local url="$1" dest="$2"
    if ! curl -fsSL --retry 2 --connect-timeout 20 -o "${dest}.part" "${url}"; then
        rm -f "${dest}.part"
        return 1
    fi
    if [ ! -s "${dest}.part" ]; then
        rm -f "${dest}.part"
        return 1
    fi
    mv -f "${dest}.part" "${dest}"
}

VOICE_FAILED=""
if [ -n "${VOICES}" ]; then
    if ! command -v curl >/dev/null 2>&1; then
        record "piper voices" "WARN" "curl is not on PATH; cannot pre-install voices"
    else
        mkdir -p "${VOICES_DIR}"
        WANTED="$(printf '%s' "${VOICES}" | tr ',' ' ')"
        PENDING=""
        for vid in ${WANTED}; do
            [ -n "${vid}" ] || continue
            # Already on disk: skip it without touching the network.
            if [ -f "${VOICES_DIR}/${vid}.onnx" ] && [ -f "${VOICES_DIR}/${vid}.onnx.json" ]; then
                say "Piper voice ${vid} is already installed"
                continue
            fi
            # Reject a bad id here, before anyone is warned about megabytes.
            if ! voice_parts "${vid}" >/dev/null; then
                VOICE_FAILED="${VOICE_FAILED} ${vid}(malformed id)"
                continue
            fi
            PENDING="${PENDING} ${vid}"
        done
        if [ -n "${PENDING// /}" ]; then
            # Say what this is about to cost before it costs it.
            COUNT="$(printf '%s\n' ${PENDING} | wc -l | tr -d ' ')"
            printf 'bootstrap: downloading %s Piper voice(s) --%s\n' "${COUNT}" "${PENDING}" >&2
            printf '           about 63 MB each (%s MB total), one time, from huggingface.co/rhasspy/piper-voices\n' \
                   "$((COUNT * 63))" >&2
            printf '           into %s\n' "${VOICES_DIR}" >&2
        fi
        for vid in ${PENDING}; do
            PARTS="$(voice_parts "${vid}")"
            # shellcheck disable=SC2086
            set -- ${PARTS}
            LANG_DIR="$1"; LOCALE_DIR="$2"; NAME_DIR="$3"; QUALITY_DIR="$4"
            OK=1
            # The config is tiny and is fetched first: when a voice id is wrong,
            # this fails in a second instead of after 63 MB.
            for ext in ".onnx.json" ".onnx"; do
                DEST="${VOICES_DIR}/${vid}${ext}"
                if [ -f "${DEST}" ]; then
                    continue
                fi
                URL="${PIPER_BASE}/${LANG_DIR}/${LOCALE_DIR}/${NAME_DIR}/${QUALITY_DIR}/${vid}${ext}?download=true"
                say "  fetching ${vid}${ext}"
                if ! fetch_voice_file "${URL}" "${DEST}"; then
                    OK=0
                    break
                fi
            done
            if [ "${OK}" -eq 1 ] && [ -f "${VOICES_DIR}/${vid}.onnx" ] && \
               [ -f "${VOICES_DIR}/${vid}.onnx.json" ]; then
                say "Installed ${vid} into ${VOICES_DIR}"
            else
                # A half-installed voice is worse than none: make_voice would
                # find the model and then fail on the missing config.
                rm -f "${VOICES_DIR}/${vid}.onnx" "${VOICES_DIR}/${vid}.onnx.json"
                VOICE_FAILED="${VOICE_FAILED} ${vid}"
            fi
        done
    fi
fi

PRESENT="$(installed_voices | tr '\n' ' ')"
PRESENT="${PRESENT% }"
if [ -n "${VOICE_FAILED// /}" ]; then
    record "piper voices" "WARN" \
           "could not install:${VOICE_FAILED} -- check the id at huggingface.co/rhasspy/piper-voices; present: ${PRESENT:-none}"
elif [ -n "${PRESENT}" ]; then
    record "piper voices" "PASS" "${PRESENT}"
else
    record "piper voices" "PASS" "none installed; make_voice.py falls back to macOS 'say'"
fi

# ---------------------------------------------------------------------------
# 7. plugin self-check
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
# 8. git hooks
# ---------------------------------------------------------------------------
# Wires .githooks/ so the pre-push version/tag guard actually runs. Only
# meaningful in a git clone -- the runtime copy under ~/.claude/plugins/cache/
# is an unpacked tarball, not a repo, so that case is reported and skipped.
# Never FAIL: someone who only *runs* the plugin has no reason to care whether
# the commit-side guard is wired.

if [ ! -d "${PLUGIN_ROOT}/.githooks" ]; then
    record "git hooks" "WARN" "no .githooks/ in ${PLUGIN_ROOT}"
elif ! command -v git >/dev/null 2>&1; then
    record "git hooks" "WARN" "git not on PATH"
elif ! git -C "${PLUGIN_ROOT}" rev-parse --git-dir >/dev/null 2>&1; then
    record "git hooks" "PASS" "not a git clone; nothing to wire"
elif git -C "${PLUGIN_ROOT}" config core.hooksPath .githooks >/dev/null 2>&1; then
    record "git hooks" "PASS" "core.hooksPath -> .githooks"
else
    record "git hooks" "WARN" "could not set core.hooksPath"
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
# Always visible, whatever happened above: adding a language is one line, and
# doing it here beats discovering it missing halfway through a nightly run.
printf 'bootstrap: add Piper voices with: %s --voices "%s"\n' \
       "${BASH_SOURCE[0]}" "${VOICE_SUGGESTION}"
exit 0
