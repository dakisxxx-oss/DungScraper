#!/usr/bin/env bash
# One-command install of DungScraper on macOS or Linux. No admin rights needed. Safe to run again.
#
#   git clone https://github.com/dakisxxx-oss/DungScraper.git
#   cd DungScraper
#   ./install.sh               # add --no-browser to skip the Chromium download
set -euo pipefail
cd "$(dirname "$0")"

NO_BROWSER=0
for arg in "$@"; do
  case "$arg" in
    --no-browser) NO_BROWSER=1 ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '==> %s\n' "$1"; }
fail() { printf 'ERROR: %s\n' "$1" >&2; exit 1; }

step "Looking for Python 3.11 or newer"
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 \
     && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done
[ -n "$PYTHON" ] || fail "Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/ (or your package manager), then run this script again."
echo "Using $("$PYTHON" -c 'import sys; print(sys.version.split()[0])')"

# Virtual environment. Re-created only when it is missing or broken.
venv_python() {
  if [ -x .venv/bin/python ]; then echo .venv/bin/python
  elif [ -x .venv/Scripts/python.exe ]; then echo .venv/Scripts/python.exe
  fi
}
VENV_PY="$(venv_python || true)"
if [ -z "$VENV_PY" ] || ! "$VENV_PY" -c 'import sys' >/dev/null 2>&1; then
  step "Creating .venv"
  rm -rf .venv
  "$PYTHON" -m venv .venv || fail "Could not create the virtual environment. On Debian or Ubuntu try: sudo apt install python3-venv"
  VENV_PY="$(venv_python)"
else
  step ".venv already exists, reusing it"
fi

step "Installing DungScraper (this takes a minute the first time)"
"$VENV_PY" -m pip install --upgrade pip --quiet || fail "pip upgrade failed. Check your internet connection."
"$VENV_PY" -m pip install -e . --quiet || fail "pip install failed. Run '$VENV_PY -m pip install -e .' to see the full error."

if [ "$NO_BROWSER" -eq 1 ]; then
  step "Skipping the Chromium download (--no-browser)"
else
  step "Installing Chromium for JavaScript pages (about 150 MB, once)"
  if ! "$VENV_PY" -m playwright install chromium; then
    echo "WARNING: Chromium did not install. HTTP scraping still works. Retry later with: $VENV_PY -m playwright install chromium" >&2
  fi
fi

step "Smoke test: scraping https://example.com"
CLI=".venv/bin/dungscraper"
[ -x "$CLI" ] || CLI=".venv/Scripts/dungscraper.exe"
OUTPUT="$("$CLI" scrape --local https://example.com 2>&1 || true)"
if printf '%s' "$OUTPUT" | grep -q "Example Domain"; then
  echo "Smoke test passed."
else
  echo "WARNING: The smoke test did not return the expected page. Output:" >&2
  printf '%s\n' "$OUTPUT" >&2
  echo "If you are offline or behind a proxy, this is expected. The install itself finished." >&2
  echo "On Linux, Chromium may need system libraries: $VENV_PY -m playwright install-deps chromium (needs sudo)." >&2
fi

cat <<EOF

Done. Next steps:
  Scrape a page:        $CLI scrape --local https://example.com
  Find public contacts: $CLI contacts https://example.com --local
  Start the local API:  ./run.sh         (needed for the web playground and HTTP API)
  Optional, Claude Desktop: $CLI install-claude
  Tip: 'source .venv/bin/activate' lets you type just 'dungscraper ...'
EOF
