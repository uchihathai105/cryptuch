#!/bin/bash
# Start the Crypto Trading Advisor:  bash run.sh
# First run creates a private Python environment in .venv (about 1–2 minutes).
set -e
cd "$(dirname "$0")"

# Find Python 3.9 or newer (Homebrew, python.org or Apple's command line tools).
PY=""
for c in "$HOME/.homebrew/bin/python3" /opt/homebrew/bin/python3 /usr/local/bin/python3 python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.9 or newer is needed. Install it from https://www.python.org/downloads/macos/ and run this again."
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "Setting up for the first time with $($PY --version)…"
  "$PY" -m venv .venv
fi

REQ_HASH="$(shasum requirements.txt | cut -d' ' -f1)"
if [ "$(cat .venv/.req_hash 2>/dev/null)" != "$REQ_HASH" ]; then
  echo "Installing packages…"
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -r requirements.txt
  echo "$REQ_HASH" > .venv/.req_hash
fi

PORT="${ADVISOR_PORT:-8000}"
echo ""
echo "Crypto Trading Advisor is starting at http://localhost:$PORT"
echo "Keep this window open. Press Ctrl+C to stop."
echo ""
(sleep 4; open "http://localhost:$PORT") >/dev/null 2>&1 &

# caffeinate keeps the Mac awake while the app runs, so the 15-minute refresh never pauses.
exec caffeinate -i .venv/bin/python -m app.main
