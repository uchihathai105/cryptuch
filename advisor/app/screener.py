"""Altcoin screener: liquid coins still far below their 1-year high that show early signs of recovery.

It only looks at price and volume. It cannot see token unlocks, dilution, team or news, so every
coin it lists still needs a check of the project before buying.
"""
from __future__ import annotations

import pandas as pd

from . import config as C
from .engine import fmt


def _ret(df: pd.DataFrame, days: int):
    if len(df) <= days:
        return None
    return float(df["close"].iloc[-1] / df["close"].iloc[-days - 1] - 1) * 100


def screen_coin(symbol: str, daily: pd.DataFrame, btc_daily: pd.DataFrame | None,
                quote_volume: float, pct24: float) -> dict | None:
    """Return a result for coins far enough below their 1-year high, else None."""
    if daily is None or len(daily) < C.SCREEN_MIN_HISTORY_DAYS:
        return None
    d = daily.tail(365)
    last = daily.iloc[-1]
    close = float(last["close"])
    high1y, low1y = float(d["high"].max()), float(d["low"].min())
    drawdown = (close / high1y - 1) * 100
    if drawdown > C.SCREEN_MAX_DRAWDOWN:
        return None
    up_from_low = (close / low1y - 1) * 100

    sma50, ema20, rsi = float(last["sma50"]), float(last["ema20"]), float(last["rsi"])
    sma50_10d = float(daily["sma50"].iloc[-11])
    recent_low = float(daily["low"].tail(30).min())
    prior_low = float(daily["low"].iloc[-120:-30].min())
    ret30, ret90 = _ret(daily, 30), _ret(daily, 90)
    btc30 = _ret(btc_daily, 30) if btc_daily is not None else None
    rs30 = (ret30 - btc30) if (ret30 is not None and btc30 is not None) else None
    vol20 = float(daily["volume"].tail(20).mean())
    vol90 = float(daily["volume"].tail(90).mean())
    vol_ratio = vol20 / vol90 if vol90 else 0.0
    lo, hi = C.SCREEN_RSI_RANGE

    checks = [
        ("above_sma50", close > sma50,
         f"Close {fmt(close)} {'above' if close > sma50 else 'below'} the 50-day average {fmt(sma50)}"),
        ("sma50_rising", sma50 > sma50_10d,
         f"50-day average {'rising' if sma50 > sma50_10d else 'falling'} over the last 10 days"),
        ("higher_low", recent_low > prior_low,
         f"Lowest price in the last 30 days {fmt(recent_low)} vs {fmt(prior_low)} in the 90 days before"
         + (": a higher low" if recent_low > prior_low else ": still making new lows")),
        ("beats_btc", rs30 is not None and rs30 > 0,
         f"30-day return {ret30:+.1f}% vs BTC {btc30:+.1f}%" if rs30 is not None else "No BTC comparison"),
        ("volume_returning", vol_ratio >= C.SCREEN_VOLUME_RATIO,
         f"20-day volume is {vol_ratio:.2f}× the 90-day average (wanted ≥ {C.SCREEN_VOLUME_RATIO})"),
        ("rsi_ok", lo <= rsi <= hi, f"Daily RSI 14 = {rsi:.0f} (wanted {lo}–{hi})"),
        ("not_extended", up_from_low <= C.SCREEN_MAX_UP_FROM_LOW,
         f"{up_from_low:+.0f}% above its 1-year low (wanted ≤ +{C.SCREEN_MAX_UP_FROM_LOW:.0f}%)"),
    ]
    got = {name: ok for name, ok, _ in checks}
    score = round(sum(C.SCREEN_WEIGHTS[n] for n, ok, _ in checks if ok) / sum(C.SCREEN_WEIGHTS.values()) * 100)

    if got["above_sma50"] and got["sma50_rising"] and got["higher_low"]:
        stage = "Recovering"
    elif got["higher_low"]:
        stage = "Basing"
    else:
        stage = "Falling"

    out = {
        "symbol": symbol, "base": symbol[:-4], "price": close, "pct24": pct24, "quote_volume": quote_volume,
        "stage": stage, "score": score, "drawdown": drawdown, "up_from_low": up_from_low,
        "high1y": high1y, "low1y": low1y, "ret30": ret30, "ret90": ret90, "rs_btc30": rs30,
        "vol_ratio": vol_ratio, "rsi": rsi, "invalidation": recent_low,
        "invalidation_pct": (recent_low / close - 1) * 100,
        "checks": [{"name": n, "passed": bool(ok), "detail": t} for n, ok, t in checks],
    }
    if stage == "Recovering":
        zlo, zhi = sorted([ema20, sma50])
        out["zone"] = [zlo, min(zhi, close)] if zlo < close else None
        out["note"] = (f"Trend has turned up. Consider gradual buys near {fmt(zlo)}–{fmt(min(zhi, close))}; "
                       f"the idea is wrong on a daily close below {fmt(recent_low)}.")
    elif stage == "Basing":
        out["note"] = (f"Stopped making new lows but no uptrend yet. Wait for a daily close above the "
                       f"50-day average ({fmt(sma50)}) with it turning up.")
    else:
        out["note"] = "Still making new lows: no sign of a bottom yet."
    return out


def rank(results: list[dict]) -> list[dict]:
    order = {"Recovering": 0, "Basing": 1, "Falling": 2}
    listed = [r for r in results if r["stage"] != "Falling"]
    listed.sort(key=lambda r: (order[r["stage"]], -r["score"], r["drawdown"]))
    return listed[:C.SCREEN_LIST_SIZE]
