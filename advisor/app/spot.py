"""Spot tab (v1.3): when in the Vietnam day the 1h candles make the day's low and high, and a buy-today verdict.

Pure functions: no network, no database.

What is measured, and how far to trust it:
* For each complete Vietnam day (00:00-24:00), the hour of the lowest low and of the highest high among the 24 one-hour
  candles. Counted over ~90 days this gives how often the day's low / high was set in each hour. With ~90 days each hour
  has only a handful of hits, so the pattern is a tendency of the past, not a schedule. The page says so.
* The verdict combines trend, momentum and where the price sits in today's range; the time of day only adds a small
  weight. Spot means buy and hold: the only actions are Buy now, Buy on dip, Wait and Avoid.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C
from .hourly import VN_OFFSET_H, vn_day, vn_hour


# ---------------------------------------------------------------- low / high hour statistics
def extremes(df: pd.DataFrame) -> dict | None:
    """df columns: open_time, open, high, low, close (closed 1h candles). None when too short."""
    if df is None or len(df) < 24 * C.SPOT_MIN_DAYS:
        return None
    d = df.copy()
    d["hour"], d["day"] = vn_hour(d["open_time"]), vn_day(d["open_time"])
    full = d.groupby("day")["hour"].transform("nunique") == 24
    d = d[full]
    days = sorted(d["day"].unique())
    if len(days) < C.SPOT_MIN_DAYS:
        return None
    low_n, high_n = np.zeros(24), np.zeros(24)
    ranges, low_first = [], 0
    for _, g in d.groupby("day"):
        g = g.sort_values("open_time")
        li, hi = int(g["low"].values.argmin()), int(g["high"].values.argmax())
        low_n[int(g["hour"].values[li])] += 1
        high_n[int(g["hour"].values[hi])] += 1
        low_first += li < hi
        ranges.append((g["high"].max() / g["low"].min() - 1) * 100)
    n = len(days)
    low_p, high_p = low_n / n * 100, high_n / n * 100
    # the VN day starts at 00:00, so the hours in order 0..23 are also the order inside the day
    cum_low, cum_high = np.cumsum(low_p), np.cumsum(high_p)
    return {
        "days": n, "first_day": int(days[0]), "last_day": int(days[-1]),
        "low": [float(x) for x in low_p], "high": [float(x) for x in high_p],
        "low_n": [int(x) for x in low_n], "high_n": [int(x) for x in high_n],
        "cum_low": [float(x) for x in cum_low], "cum_high": [float(x) for x in cum_high],
        "low_top": [int(h) for h in np.argsort(-low_p, kind="stable")[:3]],
        "high_top": [int(h) for h in np.argsort(-high_p, kind="stable")[:3]],
        "low_first_pct": float(low_first / n * 100),       # share of days where the low came before the high
        "avg_range_pct": float(np.mean(ranges)), "median_range_pct": float(np.median(ranges)),
    }


def windows(p: dict) -> dict:
    """Plain-language reading of the statistics: best buy hours, best sell hours, and how much to trust them."""
    n = p["days"]
    flat = 100 / 24
    top_low = [(h, p["low"][h]) for h in p["low_top"] if p["low"][h] > 0]
    top_high = [(h, p["high"][h]) for h in p["high_top"] if p["high"][h] > 0]
    lowsum, highsum = sum(v for _, v in top_low), sum(v for _, v in top_high)
    # expected share of the three best hours if the low were set at random: 12.5%
    strong = lowsum >= 35 and highsum >= 35
    notes = [
        f"Over the last {n} Vietnam days, the day's lowest 1h candle was most often at "
        + ", ".join(f"{_hh(h)} ({v:.0f}%)" for h, v in top_low)
        + f"; the highest at " + ", ".join(f"{_hh(h)} ({v:.0f}%)" for h, v in top_high) + ".",
        f"The day's low came before its high on {p['low_first_pct']:.0f}% of days (above 50% means most days rose from a morning dip to an afternoon or evening high). "
        f"The typical day's range from low to high was {p['median_range_pct']:.1f}%.",
    ]
    if not strong:
        notes.append("The three most common hours hold less than a third of the lows or highs (a random day would give 12.5% to any 3 hours "
                     f"of 24; an even share is {flat:.1f}% per hour): the timing is spread over the whole day. Do not wait for one hour.")
    else:
        notes.append(f"The three most common hours hold {lowsum:.0f}% of the lows and {highsum:.0f}% of the highs, clearly above the "
                     f"12.5% a random day would give. It is a tendency of the past: it can fade.")
    return {"notes": notes, "strong": bool(strong)}


def _hh(h: int) -> str:
    return f"{h:02d}:00–{(h + 1) % 24:02d}:00"


# ---------------------------------------------------------------- buy-today advice
def vn_day_start_ms(now_ms: int) -> int:
    off = VN_OFFSET_H * 3_600_000
    return (now_ms + off) // 86_400_000 * 86_400_000 - off


def advise(df: pd.DataFrame, price: float, now_ms: int, stats: dict | None, settings: dict | None = None) -> dict | None:
    """df: closed 1h candles with indicators (ema20, ema50, rsi, atr). price: latest trade. Returns the day's advice or None."""
    if df is None or len(df) < 60 or not price or price <= 0:
        return None
    last = df.iloc[-1]
    atr = float(last["atr"]) if pd.notna(last["atr"]) else None
    if not atr or atr <= 0:
        return None
    ema20, ema50, rsi = float(last["ema20"]), float(last["ema50"]), float(last["rsi"])
    start = vn_day_start_ms(now_ms)
    today = df[df["open_time"] >= start]
    lo_t = min(price, float(today["low"].min())) if len(today) else price
    hi_t = max(price, float(today["high"].max())) if len(today) else price
    # when the day has just started, use the last 24 hours as the range so the position in range means something
    last24 = df.tail(24)
    rng_lo, rng_hi = (lo_t, hi_t) if len(today) >= 4 else (min(price, float(last24["low"].min())), max(price, float(last24["high"].max())))
    pos = (price - rng_lo) / (rng_hi - rng_lo) if rng_hi > rng_lo else 0.5
    hour = int(((now_ms // 3_600_000) + VN_OFFSET_H) % 24)

    # time of day: how often the day's low is already in by now, and the high still to come
    low_done = stats["cum_low"][hour] if stats else None
    high_done = stats["cum_high"][hour] if stats else None

    up = ema20 > ema50
    above50 = price > ema50
    down = (not up) and (not above50)
    checks = [
        ("Trend: 1h EMA 20 above EMA 50", up, 20, f"EMA20 {ema20:.4g} vs EMA50 {ema50:.4g}"),
        ("Price above the 1h EMA 50", above50, 15, f"price {price:.4g}, EMA50 {ema50:.4g}"),
        ("Momentum: RSI 14 on 1h between 40 and 65", 40 <= rsi <= C.SPOT_RSI_HOT - 5, 15, f"RSI {rsi:.0f}"),
        ("Price in the cheaper part of today's range", pos <= C.SPOT_RANGE_LOW + 0.15, 20,
         f"at {pos * 100:.0f}% of {'today' if len(today) >= 4 else 'the last 24 hours'}'s range"),
        ("Not stretched: price within 1.5 ATR of the 1h EMA 20", (price - ema20) <= 1.5 * atr, 15,
         f"{(price - ema20) / atr:+.1f} ATR from EMA20"),
        ("Volume of the last 3 hours at or above average", bool(df["volume"].tail(3).mean() >= (last["vol_avg"] if pd.notna(last["vol_avg"]) else 0)), 15, "liquidity is there"),
    ]
    score = sum(w for _, ok, w, _ in checks if ok)

    # levels: buy zone and stop
    swing_low = float(df["low"].tail(12).min())
    zone_hi = min(price, max(ema20, rng_lo + 0.25 * (rng_hi - rng_lo))) if not down else min(price, ema20)
    zone_lo = max(rng_lo, zone_hi - 0.5 * atr)
    if zone_lo >= zone_hi:
        zone_lo = zone_hi - 0.25 * atr

    def levels(entry: float) -> dict | None:
        stop = min(swing_low, rng_lo, entry - C.SPOT_STOP_ATR * atr) - 0.1 * atr
        risk = entry - stop
        if risk <= 0:
            return None
        t1, t2 = entry + C.SPOT_T1_R * risk, entry + C.SPOT_T2_R * risk
        cap = max(hi_t, float(last24["high"].max()))
        rr_room = (cap - entry) / risk          # how many R the day's own high already offered above this entry
        return {"entry": entry, "stop": stop, "t1": t1, "t2": t2, "risk_pct": risk / entry * 100,
                "rr_to_day_high": rr_room}

    reasons = []
    if down:
        verdict = "Avoid"
        reasons.append("Price is below the 1h EMA 50 and EMA 20 is under EMA 50: the short-term trend is down. On spot you can only lose to a falling price, so wait for the trend to turn.")
        lv = None
    elif rsi >= C.SPOT_RSI_HOT:
        verdict = "Buy on dip"
        reasons.append(f"RSI is {rsi:.0f} (hot): do not buy at market. Place a limit order in the zone below and cancel it if the day ends without a dip.")
        lv = levels((zone_lo + zone_hi) / 2)
    elif score >= C.SPOT_BUY_MIN_SCORE and pos <= C.SPOT_RANGE_HIGH and (price - ema20) <= 1.5 * atr:
        lv_now = levels(price)
        if lv_now and lv_now["risk_pct"] <= 6:
            verdict, lv = "Buy now", lv_now
            reasons.append("Trend is up, momentum is not stretched and the price is not in the expensive part of the day.")
        else:
            verdict, lv = "Buy on dip", levels((zone_lo + zone_hi) / 2)
            reasons.append("The setup is fine but the stop would be too far from the current price. A limit order in the zone gives a tighter stop.")
    elif score >= C.SPOT_BUY_MIN_SCORE - 15 and up:
        verdict, lv = "Buy on dip", levels((zone_lo + zone_hi) / 2)
        reasons.append("The trend is up but the price is high in today's range or a little stretched: wait for a pullback into the zone.")
    else:
        verdict, lv = "Wait", None
        reasons.append("The checks are mixed. No edge: staying in cash is a position too.")

    if lv:
        if verdict == "Buy now" and low_done is not None and low_done < 50 and pos > 0.5:
            reasons.append(f"Time of day: the day's low was already set by now on only {low_done:.0f}% of past days, so a lower price later today is common. Consider buying half now and half on a dip.")
    if low_done is not None:
        reasons.append(f"At {hour:02d}:xx Vietnam time, the day's low was already in on {low_done:.0f}% of past days and the day's high on {high_done:.0f}%.")

    sizing = None
    if lv and settings:
        from .engine import position_size
        sizing = position_size(lv["entry"], lv["stop"], "long", "spot", settings)
    # a recommendation needs room: reward to risk of the first target is fixed at SPOT_T1_R by construction
    if lv and lv["risk_pct"] > 8:
        verdict, reasons = "Wait", reasons + [f"The stop would be {lv['risk_pct']:.1f}% away: too wide for a small account. Skip it."]
        lv, sizing = None, None
    return {
        "verdict": verdict, "score": int(score), "price": price, "hour": hour, "rsi": rsi, "atr": atr, "ema20": ema20, "ema50": ema50,
        "today_low": lo_t, "today_high": hi_t, "pos_in_range": pos,
        "dist_from_low_pct": (price / lo_t - 1) * 100, "dist_from_high_pct": (price / hi_t - 1) * 100,
        "zone": [zone_lo, zone_hi] if verdict in ("Buy on dip", "Buy now") else None,
        "levels": lv, "sizing": sizing, "reasons": reasons,
        "checks": [{"name": n, "ok": bool(ok), "weight": w, "detail": d} for n, ok, w, d in checks],
        "pct24": float((price / float(last24["open"].iloc[0]) - 1) * 100),
    }


STRATEGY = [
    {"title": "1. Decide the coin first, the hour second",
     "points": ["Spot is buy-and-hold: only the price rising pays you. Buy coins whose daily and 1h trend is up (price above the 1h EMA 50). Never average down on a falling coin.",
                "The hour statistics tell you when inside the day to place orders. They never tell you what to buy."]},
    {"title": "2. Enter in two or three parts",
     "points": ["Split the planned amount into 2–3 limit orders: one near the current price, the others in the buy zone or near the day's low-prone hours. Cancel the unfilled ones at the end of the Vietnam day (00:00) if the setup changed.",
                "A limit order saves the spread and stops you from chasing a candle that has already run."]},
    {"title": "3. Risk per trade, not per coin",
     "points": ["The size comes from the stop: you risk the Settings percent (default 1%) of the account between entry and stop. The tab shows quantity and cost in USDT.",
                "Keep total open spot positions to a few coins; NEAR and ZEC are altcoins and fall together with BTC."]},
    {"title": "4. Take profit in steps",
     "points": ["Sell half at target 1 (1.5R) and move the stop to your entry. Let the rest run to target 2 (3R) or the day's/week's high.",
                "On spot a stop is a stop-limit sell order: place it on the exchange, not just in your head."]},
    {"title": "5. Time of day: a small tilt, not a rule",
     "points": ["If the day's low usually comes before a given hour and it is past it, the odds of a lower price later today are smaller. If it is early in the day, expect dips and use limit orders.",
                "Avoid market buys in the thin night hours (see the Hours tab) and right at big events (US data, FOMC): spreads widen and the move can reverse in seconds."]},
    {"title": "6. When to do nothing",
     "points": ["Verdict Avoid or Wait, BTC falling hard, or RSI above 70 on 1h: skip the day. Missing a trade costs nothing; a bad entry costs money.",
                "Log every real trade in the Journal and review the mistakes after 30 trades before changing anything."]},
]
