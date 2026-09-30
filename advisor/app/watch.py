"""Watch tab rules (v1.2): trend score, side, levels, signal life cycle and performance statistics.

Pure functions only (no network, no database) so they can be tested with made-up candles.
Every tunable value lives in app/config.py (URS N08).
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

from . import config as C
from .engine import fmt, trend_of
from .indicators import ema, wilder

FIVE_MIN_MS = 5 * 60_000
VN = dt.timezone(dt.timedelta(hours=7))
LABELS = {"long": ("Long", "Buy"), "short": ("Short", "Sell")}   # (futures, spot)


def clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(x)))


def strength_of(score: float) -> str:
    a = abs(score)
    return "Strong" if a >= C.WATCH_STRONG else "Medium" if a >= C.WATCH_MEDIUM else "Weak"


# --------------------------------------------------------------------- indicators
def adx(df: pd.DataFrame, n: int = C.WATCH_ADX_LEN) -> pd.Series:
    up, dn = df["high"].diff(), -df["low"].diff()
    plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    prev = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    atr = wilder(tr, n).replace(0, np.nan)
    pdi, mdi = 100 * wilder(plus, n) / atr, 100 * wilder(minus, n) / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return wilder(dx.fillna(0), n)


def range_pct(d: pd.DataFrame, n: int) -> float | None:
    part = d.tail(n)
    if len(part) < 2:
        return None
    lo, hi = float(part["low"].min()), float(part["high"].max())
    return (hi - lo) / lo * 100 if lo > 0 else None


def swing_count(closes: np.ndarray, pct: float) -> int:
    """Reversals of at least `pct` percent: each time price turns by that much from its last extreme."""
    if len(closes) < 2:
        return 0
    thr = pct / 100
    direction, extreme, count = 0, float(closes[0]), 0
    for p in closes[1:]:
        p = float(p)
        if direction == 0:                      # first leg of at least pct: sets the direction, not counted
            if p >= extreme * (1 + thr):
                direction, extreme = 1, p
            elif p <= extreme * (1 - thr):
                direction, extreme = -1, p
        elif direction > 0:
            if p > extreme:
                extreme = p
            elif p <= extreme * (1 - thr):
                direction, extreme, count = -1, p, count + 1
        else:
            if p < extreme:
                extreme = p
            elif p >= extreme * (1 + thr):
                direction, extreme, count = 1, p, count + 1
    return count


# --------------------------------------------------------------------- score
def analyze(f5: pd.DataFrame, f1h: pd.DataFrame | None) -> dict | None:
    """Trend score -100..+100 for the latest closed 5m candle, with every component shown."""
    if f5 is None or len(f5) < C.WATCH_MIN_CANDLES:
        return None
    d = f5.copy()
    c = d["close"]
    d["e_fast"], d["e_slow"] = ema(c, C.WATCH_EMA_FAST), ema(c, C.WATCH_EMA_SLOW)
    d["adx"] = adx(d)
    last = d.iloc[-1]
    atr = float(last["atr"])
    if not atr > 0 or pd.isna(last["e_slow"]) or pd.isna(last["adx"]) or pd.isna(last["rsi"]):
        return None
    price = float(last["close"])

    # -1..+1 components (positive = up)
    ema_v = math.tanh((last["e_fast"] - last["e_slow"]) / atr * 2)
    hist, prev_hist = float(last["macd_hist"]), float(d["macd_hist"].iloc[-2])
    macd_v = 0.7 * math.tanh(hist / atr * 5) + 0.3 * float(np.sign(hist - prev_hist))
    rsi = float(last["rsi"])
    rsi_follow = clip((rsi - 50) / 20)
    rsi_rev = clip((50 - rsi) / 25)
    poss = []
    for n in C.WATCH_POS_WINDOWS:
        part = d.tail(n)
        lo, hi = float(part["low"].min()), float(part["high"].max())
        poss.append((price - lo) / (hi - lo) if hi > lo else 0.5)
    pos = float(np.mean(poss))
    pos_follow = clip((pos - 0.5) * 2)
    va = last["vol_avg"]
    vol_ratio = float(d["volume"].tail(3).mean() / va) if va and not pd.isna(va) and va > 0 else 1.0
    direction = math.tanh((price - float(c.iloc[-4])) / atr)
    volume_v = direction * min(vol_ratio, 2.0) / 2
    htf = {"up": 1.0, "down": -1.0}.get(trend_of(f1h), 0.0) if f1h is not None and len(f1h) > 60 else 0.0

    trend_vals = {"ema": ema_v, "macd": macd_v, "rsi": rsi_follow, "position": pos_follow, "volume": volume_v, "htf": htf}
    range_vals = {"ema": ema_v, "macd": macd_v, "rsi": rsi_rev, "position": -pos_follow, "volume": volume_v, "htf": htf}
    blend = clip((float(last["adx"]) - C.WATCH_ADX_RANGE_MAX) / (C.WATCH_ADX_TREND_MIN - C.WATCH_ADX_RANGE_MAX), 0, 1)
    contrib = {}
    for k in trend_vals:
        contrib[k] = round(blend * C.WATCH_WEIGHTS_TREND.get(k, 0) * trend_vals[k]
                           + (1 - blend) * C.WATCH_WEIGHTS_RANGE.get(k, 0) * range_vals[k], 1)
    score = round(max(-100.0, min(100.0, sum(contrib.values()))), 1)

    day = d.tail(288)
    rolling = (day["high"].rolling(12).max() - day["low"].rolling(12).min()) / day["low"].rolling(12).min() * 100
    r1h = range_pct(d, 12)
    med = float(rolling.dropna().median()) if rolling.notna().any() else None
    first24 = float(day["close"].iloc[0])
    return {
        "price": price, "atr": atr, "score": score, "strength": strength_of(score),
        "state": "trend" if blend >= 0.5 else "range", "adx": round(float(last["adx"]), 1),
        "contrib": contrib, "rsi": round(rsi, 1), "vol_ratio": round(vol_ratio, 2),
        "pos_in_range": round(pos * 100), "htf": trend_of(f1h) if f1h is not None and len(f1h) > 60 else "flat",
        "range_1h": r1h, "range_4h": range_pct(d, 48), "range_24h": range_pct(d, 288),
        "range_ratio": round(r1h / med, 2) if r1h is not None and med else None,
        "swings_24h": swing_count(day["close"].to_numpy(), C.WATCH_SWING_PCT),
        "pct24": (price / first24 - 1) * 100 if first24 else None,
        "close_time": int(last["close_time"]),
    }


def decide_side(score: float, prev_side: str | None, bars_since_flip: float) -> tuple[str, bool]:
    """Always a side. The side flips only past the threshold and after the minimum hold time."""
    if prev_side is None:
        return ("long" if score >= 0 else "short"), True
    if bars_since_flip >= C.WATCH_MIN_HOLD_BARS:
        if prev_side == "long" and score <= -C.WATCH_FLIP_THRESHOLD:
            return "short", True
        if prev_side == "short" and score >= C.WATCH_FLIP_THRESHOLD:
            return "long", True
    return prev_side, False


# --------------------------------------------------------------------- levels and signal life cycle
def new_signal(side: str, entry: float, atr: float, market: str, created_at: int) -> dict:
    sign = 1 if side == "long" else -1
    R = max(C.WATCH_STOP_ATR * atr, C.WATCH_MIN_STOP_PCT / 100 * entry)
    cost_pct = C.WATCH_FEE_PCT[market] + C.WATCH_SLIPPAGE_PCT
    return {
        "side": side, "entry": entry, "stop": entry - sign * R,
        "t1": entry + sign * C.WATCH_T1_R * R, "t2": entry + sign * C.WATCH_T2_R * R,
        "created_at": created_at, "cost_r": round(cost_pct / 100 * entry / R, 4),
        "status": "open", "t1_hit": 0, "t1_hit_at": None, "expired_at": None,
        "result_1h_r": None, "result_1h_price": None, "closed_at": None, "exit_price": None,
        "result_r": None, "outcome": None, "final_status": None, "late_win": 0,
        "mfe_r": 0.0, "mae_r": 0.0, "checked_until": created_at, "last_px": entry,
    }


def mirror_of(st: dict) -> dict:
    """The exact opposite trade (same distances, other side). Baseline: is the rule better than doing the reverse?"""
    e = st["entry"]
    m = new_signal("short" if st["side"] == "long" else "long", e, 1.0, "futures", st["created_at"])
    m.update(stop=2 * e - st["stop"], t1=2 * e - st["t1"], t2=2 * e - st["t2"], cost_r=st["cost_r"])
    return m


def _gross_mtm(st: dict, px: float) -> float:
    sign = 1 if st["side"] == "long" else -1
    R = abs(st["entry"] - st["stop"]) or 1e-12
    move = (px - st["entry"]) * sign / R
    R1 = abs(st["t1"] - st["entry"]) / R
    return 0.5 * R1 + 0.5 * move if st["t1_hit"] else move


def _finish(st: dict, px: float, gross: float, outcome: str, code: str, at_ms: int) -> None:
    net = round(gross - st["cost_r"], 3)
    st.update(status="closed", closed_at=at_ms, exit_price=px, result_r=net, outcome=outcome, final_status=code)
    if st["result_1h_r"] is None:            # resolved inside the first hour: that is also the 1 h result
        st.update(result_1h_r=net, result_1h_price=px)


def advance(st: dict, k1: pd.DataFrame, now_ms: int, flip_px: float | None = None) -> dict:
    """Move one signal forward using closed 1m candles (a 5m candle often touches both stop and target).

    Rules, fixed in advance so results cannot be flattered:
    - a candle touching both the stop and a target counts as the stop;
    - T1 takes half and moves the stop to entry; T2 takes the rest;
    - unresolved at 60 min: the 1 h result is recorded at that price and the signal is followed on;
    - unresolved at 120 min: closed at that price; stop and targets are never moved;
    - the opposite side appearing closes it at the current price.
    """
    if st["status"] == "closed":
        return st
    long = st["side"] == "long"
    entry = st["entry"]
    R = abs(entry - st["stop"]) or 1e-12
    R1, R2 = abs(st["t1"] - entry) / R, abs(st["t2"] - entry) / R
    exp_ms, max_ms = C.WATCH_EXPIRE_MIN * 60_000, C.WATCH_MAX_TRACK_MIN * 60_000
    created = st["created_at"]
    new = k1[k1["open_time"] >= max(created, st["checked_until"])] if k1 is not None else []
    last_px = st.get("last_px") or entry
    for k in (new.itertuples(index=False) if len(new) else []):
        if st["status"] == "open" and k.open_time >= created + exp_ms:
            g = _gross_mtm(st, last_px)
            st.update(status="tracking", expired_at=created + exp_ms,
                      result_1h_r=round(g - st["cost_r"], 3), result_1h_price=last_px)
        if k.open_time >= created + max_ms:
            _finish(st, last_px, _gross_mtm(st, last_px), "Expired after 2 h", "expired", created + max_ms)
            break
        hi, lo = float(k.high), float(k.low)
        st["mfe_r"] = max(st["mfe_r"], ((hi - entry) if long else (entry - lo)) / R)
        st["mae_r"] = max(st["mae_r"], ((entry - lo) if long else (hi - entry)) / R)
        stop = entry if st["t1_hit"] else st["stop"]
        if (lo <= stop) if long else (hi >= stop):
            if st["t1_hit"]:
                _finish(st, stop, 0.5 * R1, "Stopped at entry after T1", "be_stop", int(k.close_time))
            else:
                _finish(st, stop, -1.0, "Stop-loss hit", "stop", int(k.close_time))
            break
        if not st["t1_hit"] and ((hi >= st["t1"]) if long else (lo <= st["t1"])):
            st.update(t1_hit=1, t1_hit_at=int(k.close_time), late_win=1 if k.open_time >= created + exp_ms else 0)
            if (lo <= entry) if long else (hi >= entry):      # touched entry in the same candle: assume stopped
                _finish(st, entry, 0.5 * R1, "Stopped at entry after T1", "be_stop", int(k.close_time))
                break
        if st["t1_hit"] and ((hi >= st["t2"]) if long else (lo <= st["t2"])):
            _finish(st, st["t2"], 0.5 * R1 + 0.5 * R2, "Target 2 reached", "t2", int(k.close_time))
            break
        last_px = float(k.close)
        st["checked_until"] = int(k.close_time)
    if st["status"] != "closed" and flip_px is not None:
        _finish(st, flip_px, _gross_mtm(st, flip_px), "Side flipped", "flip", now_ms)
    st["last_px"] = last_px
    return st


# --------------------------------------------------------------------- statistics
def metrics(vals: list[float]) -> dict:
    n = len(vals)
    if not n:
        return {"n": 0, "enough": False}
    wins = [v for v in vals if v > 0]
    gain, loss = sum(wins), -sum(v for v in vals if v <= 0)
    cum = peak = dd = 0.0
    for v in vals:
        cum += v
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return {"n": n, "enough": n >= C.WATCH_MIN_SAMPLES, "win_rate": round(len(wins) / n * 100, 1),
            "avg_r": round(cum / n, 3), "total_r": round(cum, 2),
            "profit_factor": round(gain / loss, 2) if loss > 0 else None, "max_dd_r": round(dd, 2)}


def _hour_bucket(ms: int) -> str:
    h = dt.datetime.fromtimestamp(ms / 1000, VN).hour // 4 * 4
    return f"{h:02d}:00–{h + 4:02d}:00"


def performance(rows: list[dict]) -> dict:
    """rows: signals (oldest first) with 'mirror' already decoded. Removed-coin signals are ignored."""
    rows = [r for r in rows if r.get("final_status") != "removed" and r.get("result_1h_r") is not None]
    final = [r for r in rows if r["status"] == "closed"]

    def m1(rs):
        return metrics([r["result_1h_r"] for r in rs])

    def groups(keyfn):
        out: dict[str, list] = {}
        for r in rows:
            out.setdefault(keyfn(r), []).append(r)
        return [{"key": k, **m1(v)} for k, v in sorted(out.items())]

    mirror = [r["mirror"]["result_1h_r"] for r in rows if r.get("mirror") and r["mirror"].get("result_1h_r") is not None]
    tracked = [r for r in rows if r["status"] == "tracking" or r.get("expired_at")]
    late = [r for r in final if r.get("late_win")]
    return {
        "official_1h": m1(rows),
        "final_2h": metrics([r["result_r"] for r in final]),
        "mirror_1h": metrics(mirror),
        "late_wins": len(late), "followed_on": len(tracked),
        "by_side": groups(lambda r: "Long / Buy" if r["side"] == "long" else "Short / Sell"),
        "by_strength": groups(lambda r: r["strength"]),
        "by_state": groups(lambda r: "Trending" if r["state"] == "trend" else "Ranging"),
        "by_coin": groups(lambda r: r["symbol"].replace("USDT", "")),
        "by_hour": groups(lambda r: _hour_bucket(r["created_at"])),
        "by_version": groups(lambda r: r["rules_version"] or "?"),
        "recent": m1(rows[-C.WATCH_QUALITY_WINDOW:]),
        "min_samples": C.WATCH_MIN_SAMPLES,
    }
