"""Technical indicators computed from closed candles."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c = df["close"]
    df["ema20"] = ema(c, C.EMA_FAST)
    df["ema50"] = ema(c, C.EMA_SLOW)
    df["sma50"] = c.rolling(C.SMA_MID).mean()
    df["sma200"] = c.rolling(C.SMA_LONG).mean()

    delta = c.diff()
    gain = wilder(delta.clip(lower=0), C.RSI_LEN)
    loss = wilder(-delta.clip(upper=0), C.RSI_LEN)
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = (100 - 100 / (1 + rs)).fillna(100.0).where(gain.notna())

    macd = ema(c, C.MACD_FAST) - ema(c, C.MACD_SLOW)
    df["macd_hist"] = macd - ema(macd, C.MACD_SIGNAL)

    prev_close = c.shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    df["atr"] = wilder(tr, C.ATR_LEN)
    df["vol_avg"] = df["volume"].rolling(C.VOL_AVG_LEN).mean().shift()  # average of the previous 20

    # Session VWAP, reset each UTC day (meaningful on 15m and 1h candles).
    day = df["open_time"] // 86_400_000
    typical = (df["high"] + df["low"] + c) / 3
    pv = (typical * df["volume"]).groupby(day).cumsum()
    vv = df["volume"].groupby(day).cumsum()
    df["vwap"] = pv / vv.replace(0, np.nan)
    return df


def swings(df: pd.DataFrame, n: int = C.SWING_BARS, lookback: int = C.SWING_LOOKBACK):
    """Confirmed swing highs and lows (price, index), oldest first."""
    part = df.tail(lookback)
    highs, lows = part["high"].to_numpy(), part["low"].to_numpy()
    idx = part.index.to_numpy()
    sh, sl = [], []
    for i in range(n, len(part) - n):
        if highs[i] == highs[i - n:i + n + 1].max():
            sh.append((float(highs[i]), int(idx[i])))
        if lows[i] == lows[i - n:i + n + 1].min():
            sl.append((float(lows[i]), int(idx[i])))
    return sh, sl
