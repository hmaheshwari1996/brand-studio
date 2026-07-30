#!/usr/bin/env bash
#
# Render a deck to one PNG per slide so it can be eyeballed without opening
# PowerPoint. LibreOffice converts the .pptx to PDF, then pdftoppm splits the
# PDF into pages. Both are already required by the plugin.
#
# Usage:
#   render_preview.sh <deck.pptx> [outdir]
#   render_preview.sh deck.pptx                  # -> deck.preview/ beside the deck
#   render_preview.sh deck.pptx /tmp/shots
#   render_preview.sh deck.pptx --dpi 150 --keep-pdf
#   render_preview.sh -h
#
# Options:
#   --dpi N        raster resolution, default 110 (a 13.33in slide -> ~1467px)
#   --keep-pdf     keep the intermediate PDF next to the PNGs
#   --quiet        print only the resulting file paths
#
# Prints the absolute path of every PNG it produced, one per line, in slide
# order. Exits non-zero if the conversion produced nothing.

set -euo pipefail

DPI=110
KEEP_PDF=0
QUIET=0
DECK=""
OUTDIR=""

# Print the header comment block: everything after the shebang up to the first
# non-comment line, with the leading '# ' stripped.
usage() {
    awk 'NR==1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "${BASH_SOURCE[0]}"
}

die() {
    printf 'render_preview: %s\n' "$*" >&2
    exit 1
}

say() { [ "${QUIET}" -eq 1 ] || printf '%s\n' "$*" >&2; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help)  usage; exit 0 ;;
        --dpi)      [ "$#" -ge 2 ] || die "--dpi needs a value"; DPI="$2"; shift ;;
        --dpi=*)    DPI="${1#--dpi=}" ;;
        -r)         [ "$#" -ge 2 ] || die "-r needs a value"; DPI="$2"; shift ;;
        --keep-pdf) KEEP_PDF=1 ;;
        -q|--quiet) QUIET=1 ;;
        -*)         die "unknown option $1" ;;
        *)
            if [ -z "${DECK}" ]; then
                DECK="$1"
            elif [ -z "${OUTDIR}" ]; then
                OUTDIR="$1"
            else
                die "unexpected argument $1"
            fi
            ;;
    esac
    shift
done

[ -n "${DECK}" ] || { usage >&2; exit 1; }
[ -f "${DECK}" ] || die "deck not found: ${DECK}"

case "${DPI}" in
    ''|*[!0-9]*) die "--dpi must be a whole number, got '${DPI}'" ;;
esac
[ "${DPI}" -ge 20 ] && [ "${DPI}" -le 600 ] || die "--dpi must be between 20 and 600"

command -v soffice   >/dev/null 2>&1 || die "soffice not on PATH. brew install --cask libreoffice"
command -v pdftoppm  >/dev/null 2>&1 || die "pdftoppm not on PATH. brew install poppler"

DECK_ABS="$(cd -- "$(dirname -- "${DECK}")" && pwd)/$(basename -- "${DECK}")"
DECK_BASE="$(basename -- "${DECK_ABS}")"
DECK_STEM="${DECK_BASE%.*}"

if [ -z "${OUTDIR}" ]; then
    OUTDIR="$(dirname -- "${DECK_ABS}")/${DECK_STEM}.preview"
fi
mkdir -p -- "${OUTDIR}"
OUTDIR_ABS="$(cd -- "${OUTDIR}" && pwd)"

# LibreOffice refuses to run a second headless instance against the same user
# profile, so give this run a private one. Without it the command silently does
# nothing whenever the user already has LibreOffice open.
WORK="$(mktemp -d "${TMPDIR:-/tmp}/brand-studio-preview.XXXXXX")"
LO_PROFILE="${WORK}/loprofile"
cleanup() { rm -rf -- "${WORK}"; }
trap cleanup EXIT

say "Converting ${DECK_BASE} to PDF ..."
set +e
soffice \
    --headless --norestore --invisible --nolockcheck --nodefault --nologo \
    -env:UserInstallation="file://${LO_PROFILE}" \
    --convert-to pdf \
    --outdir "${WORK}" \
    "${DECK_ABS}" >"${WORK}/soffice.log" 2>&1
SOFFICE_RC=$?
set -e

PDF="${WORK}/${DECK_STEM}.pdf"
if [ ! -f "${PDF}" ]; then
    printf 'render_preview: LibreOffice produced no PDF (exit %d).\n' "${SOFFICE_RC}" >&2
    sed -n '1,40p' "${WORK}/soffice.log" >&2 || true
    exit 1
fi

# Clear any previous run so stale slides from a longer deck cannot linger.
rm -f -- "${OUTDIR_ABS}/${DECK_STEM}-"*.png

say "Rasterising at ${DPI} dpi ..."
pdftoppm -png -r "${DPI}" -- "${PDF}" "${OUTDIR_ABS}/${DECK_STEM}"

if [ "${KEEP_PDF}" -eq 1 ]; then
    cp -- "${PDF}" "${OUTDIR_ABS}/${DECK_STEM}.pdf"
    say "Kept ${OUTDIR_ABS}/${DECK_STEM}.pdf"
fi

COUNT=0
while IFS= read -r png; do
    printf '%s\n' "${png}"
    COUNT=$((COUNT + 1))
done < <(find "${OUTDIR_ABS}" -maxdepth 1 -name "${DECK_STEM}-*.png" -type f | sort -V)

[ "${COUNT}" -gt 0 ] || die "pdftoppm produced no PNGs from ${PDF}"

say "${COUNT} slide(s) -> ${OUTDIR_ABS}"
exit 0
