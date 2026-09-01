#!/usr/bin/env bash
#
# Every test in this repo, in one command.
#
# DISCOVERED, not listed: any scripts/test_*.py is picked up by writing it.
# There is no list to keep in sync, because a hand-kept list is how a test comes
# to exist, pass, and run nowhere — which is the same failure the tests
# themselves are written to catch.
#
# Python choice mirrors scripts/bootstrap.sh: prefer the venv it builds, since
# that is the interpreter proven to import python-pptx on this machine, and fall
# back to whatever python3 is on PATH (CI, a fresh clone).
#
#   scripts/test.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$HOME/.cache/brand-studio/venv/bin/python"
PY="${BRAND_STUDIO_PYTHON:-}"
[ -z "$PY" ] && [ -x "$VENV" ] && PY="$VENV"
[ -z "$PY" ] && PY="$(command -v python3)"

echo "brand-studio tests"
echo "  python: $PY"

shopt -s nullglob
tests=("$ROOT"/scripts/test_*.py)
if [ ${#tests[@]} -eq 0 ]; then
  # An empty run must FAIL. A discovery loop that finds nothing and exits 0 is
  # indistinguishable from a suite that passed, and that is precisely how a
  # broken glob reports success forever.
  echo "  no scripts/test_*.py found — discovery is broken, or the tests are gone" >&2
  exit 1
fi

failed=0
for t in "${tests[@]}"; do
  echo
  echo "── $(basename "$t")"
  if ! "$PY" "$t"; then
    failed=$(( failed + 1 ))
  fi
done

echo
if [ "$failed" -gt 0 ]; then
  echo "✗ $failed of ${#tests[@]} test file(s) failed"
  exit 1
fi
echo "✓ ${#tests[@]} test file(s) passed"
