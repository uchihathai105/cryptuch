"""All tunable settings live here (URS N08): indicator lengths, rule thresholds, limits.

Change a value, restart the app, and every call uses the new rule. Log rule
changes in CHANGELOG.md so old calls can be compared fairly (URS N07).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "advisor.db"
LOCAL_CONFIG = ROOT / "config.local.json"

RULES_VERSION = "1.1 (2026-09-29)"

# ---------------------------------------------------------------- Binance
SPOT_BASE = "https://api.binance.com"
SPOT_FALLBACK = "https://data-api.binance.vision"  # public market-data mirror
FUTURES_BASE = "https://fapi.binance.com"
MAX_CONCURRENT_REQUESTS = 8
REFRESH_DELAY_SECONDS = 5          # wait after a 15m close so Binance finalises the candle
REFRESH_MINUTES = 15

# ---------------------------------------------------------------- Coin lists (URS A4, F02)
LIST_SIZE = 10
MIN_QUOTE_VOLUME_USDT = 20_000_000
STABLE_BASES = {
    "USDC", "FDUSD", "TUSD", "USDP", "DAI", "BUSD", "USDE", "USD1", "PYUSD", "EUR",
    "EURI", "AEUR", "XUSD", "USDS", "RLUSD", "BFUSD", "GBP", "TRY", "BRL",
}
EXCLUDED_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")

# ---------------------------------------------------------------- Timeframes (URS section 4)
DAY_TRADE = {"direction": "4h", "setup": "1h", "entry": "15m"}
CANDLE_LIMITS = {"15m": 200, "1h": 200, "4h": 200, "1d": 400, "1w": 104}  # 1d: a full year + margin
INTERVAL_MINUTES = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}

# ---------------------------------------------------------------- Indicators
EMA_FAST, EMA_SLOW = 20, 50
SMA_MID, SMA_LONG = 50, 200
RSI_LEN = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
ATR_LEN = 14
VOL_AVG_LEN = 20
SWING_BARS = 3            # a swing high/low needs this many lower/higher bars on each side
SWING_LOOKBACK = 60       # candles searched for swing levels

# ---------------------------------------------------------------- Day-trade rules
RSI_LONG_RANGE = (40, 65)
RSI_SHORT_RANGE = (35, 60)
VOLUME_SPIKE = 1.5
PULLBACK_ATR = 0.3        # how close a pullback must get to EMA 20 / VWAP, in ATRs
FUNDING_LONG_MAX = 0.0005     # +0.05% per 8h
FUNDING_SHORT_MIN = -0.0005
STOP_MAX_ATR = 2.0
STOP_DEFAULT_ATR = 1.5
STOP_MIN_ATR = 0.5
STOP_BUFFER_ATR = 0.1
TARGET1_R, TARGET2_R = 1.5, 3.0
MIN_REWARD_RISK = 1.5
MIN_CONFIDENCE = 50
GAINER_CHASE_PCT = 30.0
GAINER_CHASE_RSI = 80.0
CHECK_WEIGHTS = {
    "trend": 20, "setup": 20, "rsi": 10, "macd": 10, "volume": 10,
    "timing": 10, "regime": 10, "positioning": 10,
}

# ---------------------------------------------------------------- Altcoin screener (runs once a day)
SCREEN_MIN_VOLUME_USDT = 10_000_000   # 24h quote volume, so you can get in and out
SCREEN_MIN_HISTORY_DAYS = 365         # listed at least a year: skips fresh listings
SCREEN_MAX_DRAWDOWN = -50.0           # at least 50% below its 1-year high = "still low"
SCREEN_MAX_UP_FROM_LOW = 100.0        # not already doubled off its 1-year low
SCREEN_RSI_RANGE = (45, 70)           # momentum turning up, not overheated
SCREEN_VOLUME_RATIO = 1.2             # 20-day average volume vs 90-day average
SCREEN_LIST_SIZE = 25
SCREEN_WEIGHTS = {
    "above_sma50": 20, "sma50_rising": 15, "higher_low": 20, "beats_btc": 15,
    "volume_returning": 10, "rsi_ok": 10, "not_extended": 10,
}

# ---------------------------------------------------------------- Risk defaults (URS F17, F18)
DEFAULT_SETTINGS = {
    "account_size": 1000.0,
    "risk_pct": 1.0,
    "max_leverage": 5,
    "max_position_pct": 25.0,
    "watchlist": ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"],
}
MAINTENANCE_MARGIN = 0.005   # approximate; real Binance tiers vary by coin and size

# ---------------------------------------------------------------- Server
HOST = os.environ.get("ADVISOR_HOST", "127.0.0.1")
PORT = int(os.environ.get("ADVISOR_PORT", "8000"))


def local_config() -> dict:
    """Secrets such as the Telegram bot token stay in config.local.json on this Mac only."""
    try:
        return json.loads(LOCAL_CONFIG.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
