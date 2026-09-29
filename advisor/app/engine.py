"""Signal rules from URS section 4: day-trade calls, long-term ratings, position sizing."""
from __future__ import annotations

import math

import pandas as pd

from . import config as C
from .indicators import swings


def fmt(x: float) -> str:
    """Price formatting that works for BTC (60000) and small coins (0.00001234)."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    ax = abs(x)
    if ax >= 1000:
        return f"{x:,.1f}"
    if ax >= 1:
        return f"{x:,.3f}".rstrip("0").rstrip(".") if ax < 100 else f"{x:,.2f}"
    digits = max(4, -int(math.floor(math.log10(ax))) + 3) if ax > 0 else 4
    return f"{x:.{digits}f}"


def trend_of(df: pd.DataFrame) -> str:
    last = df.iloc[-1]
    if pd.isna(last["ema50"]):
        return "flat"
    if last["close"] > last["ema50"] and last["ema20"] > last["ema50"]:
        return "up"
    if last["close"] < last["ema50"] and last["ema20"] < last["ema50"]:
        return "down"
    return "flat"


def regime_from_btc(btc_4h: pd.DataFrame) -> str:
    return {"up": "Bull", "down": "Bear", "flat": "Neutral"}[trend_of(btc_4h)]


# --------------------------------------------------------------------- day trade
def _check(name: str, passed: bool, detail: str, applies: bool = True) -> dict:
    return {"name": name, "passed": bool(passed), "detail": detail, "applies": applies}


def _setup(d: pd.DataFrame, side: str) -> tuple[bool, str, str, float | None]:
    """Pullback to EMA 20 / VWAP that holds, or a close through the last swing level."""
    last = d.iloc[-1]
    atr = last["atr"]
    recent = d.tail(3)
    sh, sl = swings(d.iloc[:-2]) if len(d) > 10 else ([], [])
    for level_name in ("ema20", "vwap"):
        level = last[level_name]
        if pd.isna(level):
            continue
        label = "EMA 20" if level_name == "ema20" else "VWAP"
        if side == "long" and recent["low"].min() <= level + C.PULLBACK_ATR * atr and last["close"] > level:
            return True, "pullback", f"1h pullback to {label} ({fmt(level)}) held; close {fmt(last['close'])}", float(level)
        if side == "short" and recent["high"].max() >= level - C.PULLBACK_ATR * atr and last["close"] < level:
            return True, "pullback", f"1h rally to {label} ({fmt(level)}) failed; close {fmt(last['close'])}", float(level)
    if side == "long" and sh:
        lvl = sh[-1][0]
        if last["close"] > lvl and recent["close"].iloc[0] <= lvl * 1.002:
            return True, "breakout", f"1h close {fmt(last['close'])} broke above swing high {fmt(lvl)}", float(lvl)
    if side == "short" and sl:
        lvl = sl[-1][0]
        if last["close"] < lvl and recent["close"].iloc[0] >= lvl * 0.998:
            return True, "breakdown", f"1h close {fmt(last['close'])} broke below swing low {fmt(lvl)}", float(lvl)
    want = "a pullback to EMA 20/VWAP or a breakout" if side == "long" else "a failed rally or a breakdown"
    return False, "", f"No 1h setup yet; waiting for {want}", None


def _levels(side: str, entry: float, setup_df: pd.DataFrame, trend_df: pd.DataFrame, setup_kind: str,
            setup_level: float | None = None) -> dict:
    atr = float(setup_df.iloc[-1]["atr"])
    sh1, sl1 = swings(setup_df)
    sh4, sl4 = swings(trend_df)
    sign = 1 if side == "long" else -1
    if side == "long":
        below = [p for p, _ in sl1 if p < entry]
        stop = below[-1] - C.STOP_BUFFER_ATR * atr if below else entry - C.STOP_DEFAULT_ATR * atr
    else:
        above = [p for p, _ in sh1 if p > entry]
        stop = above[-1] + C.STOP_BUFFER_ATR * atr if above else entry + C.STOP_DEFAULT_ATR * atr
    if C.STOP_LEVEL_BUFFER_ATR and setup_level is not None:
        # keep the stop clearly beyond the level the setup bounced from (EMA 20, VWAP or the broken swing)
        beyond = setup_level - sign * C.STOP_LEVEL_BUFFER_ATR * atr
        stop = min(stop, beyond) if side == "long" else max(stop, beyond)
    dist = abs(entry - stop)
    dist = min(max(dist, C.STOP_MIN_ATR * atr), C.STOP_MAX_ATR * atr)
    stop = entry - sign * dist
    risk = dist
    t1 = entry + sign * C.TARGET1_R * risk
    t2 = entry + sign * C.TARGET2_R * risk
    # nearest opposing level (resistance for longs, support for shorts) from 1h and 4h swings
    if side == "long":
        opp = sorted(p for p, _ in sh1 + sh4 if p > entry * 1.001)
    else:
        opp = sorted((p for p, _ in sl1 + sl4 if p < entry * 0.999), reverse=True)
    blocker = opp[0] if opp else None
    rr = C.TARGET2_R
    if blocker is not None:
        room = abs(blocker - entry) / risk
        if room < C.TARGET1_R:
            rr = room
        elif room < C.TARGET2_R:
            t2, rr = blocker, room
    ema20 = float(setup_df.iloc[-1]["ema20"])
    if setup_kind == "pullback" and (ema20 - stop) * sign > 0 and (entry - ema20) * sign > 0:
        zone = sorted([ema20, entry])
    else:
        zone = sorted([entry, entry - sign * 0.25 * atr])
    return {"entry": entry, "entry_zone": zone, "stop": stop, "t1": t1, "t2": t2, "rr": round(rr, 2),
            "risk_pct": round(risk / entry * 100, 2), "blocker": blocker, "atr": atr}


def evaluate_side(side: str, frames: dict, regime: str, positioning: dict | None,
                  pct24: float, is_gainer: bool, market: str) -> dict:
    t_df, s_df, e_df = (frames[C.DAY_TRADE[k]] for k in ("direction", "setup", "entry"))
    s_last, s_prev2 = s_df.iloc[-1], s_df.iloc[-3]
    e_last = e_df.iloc[-1]
    t_last = t_df.iloc[-1]
    long = side == "long"
    trend = trend_of(t_df)
    checks = []

    ok = trend == ("up" if long else "down")
    checks.append(_check("trend", ok,
        f"4h close {fmt(t_last['close'])} {'above' if t_last['close'] > t_last['ema50'] else 'below'} EMA 50 "
        f"({fmt(t_last['ema50'])}); EMA 20 {'above' if t_last['ema20'] > t_last['ema50'] else 'below'} EMA 50"))

    setup_ok, setup_kind, setup_text, setup_level = _setup(s_df, side)
    checks.append(_check("setup", setup_ok, setup_text))

    lo, hi = C.RSI_LONG_RANGE if long else C.RSI_SHORT_RANGE
    rsi, rsi_prev = s_last["rsi"], s_prev2["rsi"]
    rsi_ok = lo <= rsi <= hi and (rsi > rsi_prev if long else rsi < rsi_prev)
    checks.append(_check("rsi", rsi_ok,
        f"1h RSI 14 = {rsi:.0f} ({'rising' if rsi > rsi_prev else 'falling'}); wanted {lo}–{hi} and "
        f"{'rising' if long else 'falling'}"))

    h, hp = s_last["macd_hist"], s_df.iloc[-2]["macd_hist"]
    macd_ok = h > hp if long else h < hp
    checks.append(_check("macd", macd_ok,
        f"1h MACD histogram {'turning up' if h > hp else 'turning down'} ({h:+.4g})"))

    ratios = (s_df["volume"] / s_df["vol_avg"]).tail(3)
    vr = float(ratios.max()) if ratios.notna().any() else 0.0
    checks.append(_check("volume", vr >= C.VOLUME_SPIKE,
        f"Best recent 1h volume = {vr:.1f}× its 20-candle average (wanted ≥ {C.VOLUME_SPIKE}×)"))

    green = e_last["close"] > e_last["open"]
    timing_ok = (e_last["close"] > e_last["ema20"] and green) if long else (e_last["close"] < e_last["ema20"] and not green)
    checks.append(_check("timing", timing_ok,
        f"15m close {fmt(e_last['close'])} {'above' if e_last['close'] > e_last['ema20'] else 'below'} "
        f"15m EMA 20; last candle {'green' if green else 'red'}"))

    regime_ok = regime != ("Bear" if long else "Bull")
    checks.append(_check("regime", regime_ok, f"BTC 4h regime: {regime}"))

    if market == "futures" and positioning:
        f = positioning.get("funding")
        oi = positioning.get("oi_change")
        f_ok = f is not None and (f <= C.FUNDING_LONG_MAX if long else f >= C.FUNDING_SHORT_MIN)
        oi_ok = oi is None or (oi > 0 and (pct24 > 0 if long else pct24 < 0))
        f_txt = f"{f * 100:+.4f}%/8h" if f is not None else "n/a"
        oi_txt = f"{oi:+.1f}%" if oi is not None else "n/a"
        checks.append(_check("positioning", f_ok and oi_ok,
            f"Funding {f_txt}; open interest 24h {oi_txt}; price 24h {pct24:+.1f}%"))
    else:
        checks.append(_check("positioning", False, "Spot market: not used", applies=False))

    total = sum(C.CHECK_WEIGHTS[c["name"]] for c in checks if c["applies"])
    got = sum(C.CHECK_WEIGHTS[c["name"]] for c in checks if c["applies"] and c["passed"])
    confidence = round(got / total * 100)

    result = {"side": side, "checks": checks, "confidence": confidence, "setup_kind": setup_kind,
              "trend": trend, "actionable": False, "reason": ""}
    if not checks[0]["passed"]:
        result["reason"] = f"4h trend is {trend}, not {'up' if long else 'down'}"
        return result
    if not setup_ok:
        result["reason"] = setup_text
        return result
    if long and is_gainer and pct24 > C.GAINER_CHASE_PCT and rsi > C.GAINER_CHASE_RSI:
        result["reason"] = (f"Up {pct24:.0f}% in 24h with 1h RSI {rsi:.0f}: don't chase, "
                            f"wait for a pullback to 1h EMA 20 ({fmt(s_last['ema20'])})")
        return result
    if not long and is_gainer:
        sh, _ = swings(s_df)
        lower_high = len(sh) >= 2 and sh[-1][0] < sh[-2][0]
        vol_falling = float((s_df["volume"] / s_df["vol_avg"]).tail(3).mean()) < 1
        if not (lower_high and vol_falling):
            result["reason"] = "Top gainer: short only after a 1h lower high on falling volume"
            return result
    entry = float(e_last["close"])
    if C.CHASE_MAX_ATR and setup_level is not None:
        away = abs(entry - setup_level) / float(s_last["atr"])
        if away > C.CHASE_MAX_ATR:
            result["reason"] = (f"Price is {away:.1f} ATR away from the setup level {fmt(setup_level)}: "
                                f"too late, wait for a pullback")
            return result
    if C.REGIME_REQUIRED and not regime_ok:
        result["reason"] = f"BTC regime is {regime}: only trades with the BTC trend"
        return result
    lv = _levels(side, entry, s_df, t_df, setup_kind, setup_level)
    result.update(lv)
    if lv["rr"] < C.MIN_REWARD_RISK:
        result["reason"] = (f"Reward-to-risk {lv['rr']:.1f} below {C.MIN_REWARD_RISK}: "
                            f"{'resistance' if long else 'support'} at {fmt(lv['blocker'])} is too close")
        return result
    if confidence < C.MIN_CONFIDENCE:
        result["reason"] = f"Confidence {confidence} below {C.MIN_CONFIDENCE}"
        return result
    result["actionable"] = True
    result["reason"] = setup_text
    return result


def day_trade_call(frames: dict, regime: str, market: str, positioning: dict | None,
                   pct24: float, is_gainer: bool) -> dict:
    """Futures → Long / Short / Wait. Spot → Buy / Exit / Wait."""
    long_r = evaluate_side("long", frames, regime, positioning, pct24, is_gainer, market)
    short_r = evaluate_side("short", frames, regime, positioning, pct24, is_gainer, market)
    trend = long_r["trend"]
    lean = long_r if trend == "up" else short_r if trend == "down" else (
        long_r if long_r["confidence"] >= short_r["confidence"] else short_r)

    if market == "futures":
        call = ("Long" if lean["side"] == "long" else "Short") if lean["actionable"] else "Wait"
        res = dict(lean)
    else:
        if long_r["actionable"]:
            call, res = "Buy", dict(long_r)
        elif trend == "down" and short_r["checks"][1]["passed"]:
            call, res = "Exit", dict(short_r)
            res["reason"] = "4h trend down and 1h setup points lower: sell what you hold"
            for k in ("entry", "entry_zone", "stop", "t1", "t2", "rr", "risk_pct", "blocker"):
                res.pop(k, None)
        else:
            call, res = "Wait", dict(lean)
            if lean["side"] == "short" and lean["trend"] == "down":
                res["reason"] = "4h trend down: no spot buys, stay out"
    res["call"] = call
    res["market"] = market
    if call == "Wait" and trend == "flat":
        res["reason"] = "4h trend is mixed (price and EMAs disagree): wait for a clear direction"
    return res


# --------------------------------------------------------------------- long term
def long_term_rating(daily: pd.DataFrame, weekly: pd.DataFrame, btc_daily: pd.DataFrame, is_btc: bool) -> dict:
    d = daily.iloc[-1]
    close = float(d["close"])
    out = {"close": close, "reasons": []}
    if len(daily) < 200 or pd.isna(d["sma200"]):
        out.update(rating="New", reasons=[f"Only {len(daily)} days of history; 200 needed"])
        return out

    def ret(df, days):
        return float(df["close"].iloc[-1] / df["close"].iloc[-days - 1] - 1) * 100 if len(df) > days else None

    r90, r180 = ret(daily, 90), ret(daily, 180)
    btc90 = ret(btc_daily, 90)
    rs = (r90 - btc90) if (r90 is not None and btc90 is not None and not is_btc) else r90
    high1y = float(daily["high"].tail(365).max())
    dd = (close / high1y - 1) * 100
    above200 = close > d["sma200"]
    golden = d["sma50"] > d["sma200"]
    w = weekly.iloc[-1]
    w_falling = bool(len(weekly) > 4 and w["close"] < w["ema20"] and w["ema20"] < weekly["ema20"].iloc[-4])

    r = out["reasons"]
    r.append(f"Close {fmt(close)} {'above' if above200 else 'below'} 200-day average {fmt(d['sma200'])}")
    r.append(f"50-day average {'above' if golden else 'below'} 200-day")
    if rs is not None:
        r.append(f"90-day return {r90:+.1f}%" + ("" if is_btc else f", {rs:+.1f} pts vs BTC"))
    r.append(f"Weekly trend {'falling' if w_falling else 'not falling'}")

    if above200 and golden and rs is not None and rs > 0:
        rating = "Accumulate"
        lo, hi = sorted([float(d["ema20"]), float(d["sma50"])])
        out["zone"] = [lo, hi]
    elif (not above200) and w_falling:
        rating = "Reduce"
    else:
        rating = "Hold"
    out.update(rating=rating, ret90=r90, ret180=r180, rs_btc=None if is_btc else rs,
               drawdown=dd, sma200=float(d["sma200"]), sma50=float(d["sma50"]))
    return out


# --------------------------------------------------------------------- sizing
def position_size(entry: float, stop: float, side: str, market: str, s: dict) -> dict:
    account = float(s["account_size"])
    risk_amt = account * float(s["risk_pct"]) / 100
    dist = abs(entry - stop)
    if dist <= 0 or entry <= 0:
        return {}
    qty = risk_amt / dist
    notional = qty * entry
    warnings = []
    out = {"risk_usdt": risk_amt}
    max_pos = account * float(s["max_position_pct"]) / 100
    if market == "spot":
        if notional > account:
            qty, notional = account / entry, account
            warnings.append("Full risk would need more than your whole account; size capped at 100%")
        if notional > max_pos:
            warnings.append(f"Position is {notional / account * 100:.0f}% of account "
                            f"(limit {float(s['max_position_pct']):.0f}%)")
        out.update(qty=qty, notional=notional)
    else:
        max_lev = float(s["max_leverage"])
        margin = min(notional, max_pos)
        lev = notional / margin if margin else 1
        if lev > max_lev:
            notional = margin * max_lev
            qty = notional / entry
            lev = max_lev
            warnings.append(f"Size cut to stay within {max_lev:.0f}x on {float(s['max_position_pct']):.0f}% "
                            f"margin; risk now {qty * dist:.2f} USDT")
        lev = max(1.0, lev)
        mm = C.MAINTENANCE_MARGIN
        liq = entry * (1 - 1 / lev + mm) if side == "long" else entry * (1 + 1 / lev - mm)
        if lev > 1 and abs(entry - liq) < 1.5 * dist:
            warnings.append(f"Liquidation {fmt(liq)} is closer than 1.5× the stop distance")
        out.update(qty=qty, notional=notional, margin=notional / lev, leverage=round(lev, 1),
                   liquidation=liq if lev > 1 else None)
    out["warnings"] = warnings
    return out
