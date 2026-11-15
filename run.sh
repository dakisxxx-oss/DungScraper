#!/usr/bin/env bash
# Start the local DungScraper API (and playground) on this computer only.
#
#   ./run.sh                 # runs in this terminal, Ctrl+C stops it
#   ./run.sh --background    # runs detached, logs go to run.out.log
#   PORT=9000 ./run.sh
set -euo pipefail
cd "$(dirname "$0")"

CLI=".venv/bin/dungscraper"
[ -x "$CLI" ] || CLI=".venv/Scripts/dungscraper.exe"
if [ ! -x "$CLI" ]; then
  echo "DungScraper is not installed here. Run ./install.sh first." >&2
  exit 1
fi

export DUNGSCRAPER_HOST=127.0.0.1
export DUNGSCRAPER_PORT="${PORT:-8741}"
HEALTH="http://127.0.0.1:${DUNGSCRAPER_PORT}/health"

if curl -fsS -m 3 "$HEALTH" >/dev/null 2>&1; then
  echo "Already running at http://127.0.0.1:${DUNGSCRAPER_PORT}"
  exit 0
fi

if [ "${1:-}" = "--background" ]; then
  nohup "$CLI" serve >run.out.log 2>&1 &
  for _ in $(seq 1 30); do
    sleep 1
    if curl -fsS -m 2 "$HEALTH" >/dev/null 2>&1; then
      echo "Started. Playground: http://127.0.0.1:${DUNGSCRAPER_PORT}  API docs: http://127.0.0.1:${DUNGSCRAPER_PORT}/docs"
      exit 0
    fi
  done
  echo "The server did not answer in 30 seconds. See run.out.log." >&2
  exit 1
fi

echo "Starting on http://127.0.0.1:${DUNGSCRAPER_PORT} (Ctrl+C to stop)"
exec "$CLI" serve
