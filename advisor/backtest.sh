#!/bin/bash
# Replay the day-trade rules on recent Binance history:  bash backtest.sh   (or: bash backtest.sh 30 120)
# Arguments: number of coins (default 20), number of days (default 90). Takes about 3–6 minutes.
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then echo "Run 'bash run.sh' once first to set up."; exit 1; fi
exec .venv/bin/python -m app.backtest "$@"
