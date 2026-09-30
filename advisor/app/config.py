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

RULES_VERSION = "1.2 (2026-09-30)"

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
CANDLE_LIMITS = {"1m": 180, "5m": 300, "15m": 200, "1h": 200, "4h": 200, "1d": 400, "1w": 104}  # 1d: a full year + margin; 1m/5m: Watch tab
INTERVAL_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}

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
# Optional rules (off = version 1.0 behaviour). The backtest compares them before any goes live.
STOP_LEVEL_BUFFER_ATR = None   # e.g. 0.5: stop at least 0.5 ATR beyond the level the setup bounced from
CHASE_MAX_ATR = None           # e.g. 1.0: no entry once price is more than 1 ATR past the setup level
REGIME_REQUIRED = False        # True: never trade against the BTC 4h trend
FEE_ROUNDTRIP_PCT = 0.10       # backtest cost per trade: Binance futures taker 0.05% in + 0.05% out
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

# ---------------------------------------------------------------- Watch tab (v1.2): volatile coins you type in
# Every watched coin always shows a side: Long/Short on futures, Buy/Sell on spot (same direction).
# A trend score from -100 (strong down) to +100 (strong up) picks the side; its size gives the strength.
WATCH_REFRESH_MINUTES = 5          # analysis runs 5 s after every 5m candle close
WATCH_MAX_COINS = 20               # keeps Binance request weight low
WATCH_MIN_CANDLES = 120            # 5m candles needed before a coin can be scored (10 hours)
WATCH_EMA_FAST, WATCH_EMA_SLOW = 9, 21
WATCH_ADX_LEN = 14
WATCH_ADX_RANGE_MAX = 20           # ADX at or below this = ranging: mean-reversion rules
WATCH_ADX_TREND_MIN = 25           # ADX at or above this = trending: trend-following rules (blend in between)
WATCH_POS_WINDOWS = (48, 288)      # 5m candles for the 4h and 24h range used for "position in range"
# Component weights (each component is scaled -1..+1, weights add up to 100 in each regime)
WATCH_WEIGHTS_TREND = {"ema": 30, "macd": 20, "rsi": 15, "position": 10, "volume": 10, "htf": 15}
WATCH_WEIGHTS_RANGE = {"position": 35, "rsi": 25, "macd": 15, "ema": 5, "volume": 10, "htf": 10}
WATCH_FLIP_THRESHOLD = 10          # the side only changes once the score passes this on the other side
WATCH_MIN_HOLD_BARS = 3            # 5m candles a side is kept before it may flip (15 minutes)
WATCH_STRONG, WATCH_MEDIUM = 50, 25   # |score| >= 50 Strong, >= 25 Medium, else Weak
# Levels from ATR 5m: R = max(stop ATR x ATR, min stop %); T1 and T2 are multiples of R
WATCH_STOP_ATR = 1.0
WATCH_MIN_STOP_PCT = 0.3
WATCH_T1_R, WATCH_T2_R = 1.0, 2.0
# Signal life cycle: scored at 1 h (the official result), followed until 2 h, stop and targets never moved
WATCH_EXPIRE_MIN = 60
WATCH_MAX_TRACK_MIN = 120
WATCH_REOPEN_COOLDOWN_MIN = 15     # after a signal ends with the side unchanged, wait before opening the next
# Costs per round trip, in percent of the entry price (subtracted from every result)
WATCH_FEE_PCT = {"futures": 0.10, "spot": 0.20}
WATCH_SLIPPAGE_PCT = 0.10          # raise for thin coins
# Evaluation
WATCH_MIN_SAMPLES = 30             # fewer signals than this in a group = "not enough data"
WATCH_QUALITY_WINDOW, WATCH_QUALITY_MIN = 50, 20    # banner when the last 50 signals average below 0R
WATCH_COIN_WINDOW, WATCH_COIN_MIN = 20, 10          # a coin averaging below 0R over its last 20 is shown as Weak
WATCH_SWING_PCT = 1.5              # a reversal counts once price moves this % against the last extreme (24h)
WATCH_ALERT_MIN_STRENGTH = "Strong"  # Telegram alert when a side flips at this strength or higher; None = off

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
