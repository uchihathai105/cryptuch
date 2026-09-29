"""Backtest the day-trade rules on recent Binance history.

Run from the advisor folder (the app can keep running):
    .venv/bin/python -m app.backtest            # 20 coins, 90 days
    .venv/bin/python -m app.backtest 30 120     # 30 coins, 120 days

It downloads 15m / 1h / 4h spot candles for the top coins by 24h volume, then steps through
history one 15-minute close at a time, calling the same engine the live app uses, and follows
each call with the same exit rules as Active calls (stop, half at T1 then stop to entry, T2,
1h close back through EMA 20, 4h trend flip). One position per coin at a time. Results are in R
after fees. Spot candles are used for both longs and shorts, and funding / open interest are
not replayed (Binance only keeps 30 days of open interest), so the futures positioning check is
left out.

Results print here and are saved to data/backtest_latest.txt and data/backtest_latest.json.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import config as C
from . import engine
from .binance import Binance, klines_to_frame
from .indicators import add_indicators

WARMUP_DAYS = 20          # extra history so 4h EMA 50 and swings are ready on day one
STEP_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}

VARIANTS = [
    ("A  Current rules (v1.1)", {}),
    ("B  Wider stop", {"STOP_MIN_ATR": 1.0, "STOP_LEVEL_BUFFER_ATR": 0.5}),
    ("C  Wider stop + no chasing", {"STOP_MIN_ATR": 1.0, "STOP_LEVEL_BUFFER_ATR": 0.5, "CHASE_MAX_ATR": 1.0}),
    ("D  C + only with BTC trend", {"STOP_MIN_ATR": 1.0, "STOP_LEVEL_BUFFER_ATR": 0.5, "CHASE_MAX_ATR": 1.0,
                                    "REGIME_REQUIRED": True}),
]
BASE = {k: getattr(C, k) for k in ("STOP_MIN_ATR", "STOP_LEVEL_BUFFER_ATR", "CHASE_MAX_ATR", "REGIME_REQUIRED")}


# ------------------------------------------------------------------ data
def eligible(t: dict) -> bool:
    """Same filter as the live coin lists (URS A4), without touching the app's database."""
    s = t["symbol"]
    if not s.endswith("USDT") or s.endswith(C.EXCLUDED_SUFFIXES) or s[:-4] in C.STABLE_BASES:
        return False
    return float(t.get("quoteVolume") or 0) >= C.MIN_QUOTE_VOLUME_USDT


async def fetch_range(api: Binance, symbol: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    rows, cur = [], start_ms
    while cur < end_ms:
        data = await api._get(api.spot_base, "/api/v3/klines",
                              {"symbol": symbol, "interval": interval, "startTime": cur, "endTime": end_ms,
                               "limit": 1000})
        if not data:
            break
        rows += data
        cur = int(data[-1][0]) + STEP_MS[interval]
        if len(data) < 1000:
            break
    df = klines_to_frame(rows)
    return df.drop_duplicates("open_time").reset_index(drop=True)


async def load(n_coins: int, days: int):
    api = Binance()
    try:
        tickers = await api.spot_tickers()
        pool = [t for t in tickers if eligible(t) and t["symbol"] != "BTCUSDT"]
        pool.sort(key=lambda t: -float(t["quoteVolume"]))
        symbols = ["BTCUSDT"] + [t["symbol"] for t in pool[:n_coins]]
        end = int(time.time() * 1000)
        start = end - (days + WARMUP_DAYS) * 86_400_000
        print(f"Downloading {days + WARMUP_DAYS} days of 15m / 1h / 4h candles for {len(symbols)} coins…", flush=True)
        jobs = [(s, iv) for s in symbols for iv in ("15m", "1h", "4h")]
        frames = await asyncio.gather(*(fetch_range(api, s, iv, start, end) for s, iv in jobs))
        data: dict[str, dict[str, pd.DataFrame]] = {}
        for (s, iv), df in zip(jobs, frames):
            data.setdefault(s, {})[iv] = add_indicators(df)
        print(f"Downloaded. Binance weight used this minute: spot {api.used_weight.get('spot', '?')} / 6000", flush=True)
        return data, end - days * 86_400_000
    finally:
        await api.close()


# ------------------------------------------------------------------ replay
def trend_array(df: pd.DataFrame) -> np.ndarray:
    c, e20, e50 = df["close"].to_numpy(), df["ema20"].to_numpy(), df["ema50"].to_numpy()
    up = (c > e50) & (e20 > e50)
    down = (c < e50) & (e20 < e50)
    return np.where(up, 1, np.where(down, -1, 0))


@dataclass
class Candidate:
    i: int                 # index of the 15m candle whose close triggered the check
    side: str
    k1: int                # number of closed 1h candles at that time
    k4: int                # number of closed 4h candles at that time
    regime: str


@dataclass
class Trade:
    symbol: str
    side: str
    t_open: int
    t_close: int
    entry: float
    stop: float
    risk_pct: float
    r_gross: float
    r_net: float
    outcome: str
    confidence: int


@dataclass
class Stats:
    trades: list = field(default_factory=list)


def candidates_for(sym: str, d: dict, btc4: pd.DataFrame, start_ms: int) -> list[Candidate]:
    """Steps where the 4h trend and the 1h setup agree: the only places a call can fire."""
    m15, h1, h4 = d["15m"], d["1h"], d["4h"]
    ct15 = m15["close_time"].to_numpy()
    ct1, ct4 = h1["close_time"].to_numpy(), h4["close_time"].to_numpy()
    btc_ct4 = btc4["close_time"].to_numpy()
    tr4, btc_tr = trend_array(h4), trend_array(btc4)
    out, last_setup_key = [], None
    for i in range(len(m15)):
        t = ct15[i]
        if t < start_ms or i < 60:
            continue
        k1 = int(np.searchsorted(ct1, t, side="right"))
        k4 = int(np.searchsorted(ct4, t, side="right"))
        if k1 < 80 or k4 < 60:
            continue
        tr = tr4[k4 - 1]
        if tr == 0:
            continue
        side = "long" if tr == 1 else "short"
        kb = int(np.searchsorted(btc_ct4, t, side="right"))
        regime = {1: "Bull", -1: "Bear", 0: "Neutral"}[int(btc_tr[kb - 1])] if kb else "Neutral"
        # the 1h setup only changes when a 1h candle closes: evaluate it once per 1h candle and side
        key = (k1, side)
        if key != last_setup_key:
            ok = engine._setup(h1.iloc[:k1], side)[0]
            last_setup_key = key
            setup_ok = ok
        if setup_ok:
            out.append(Candidate(i, side, k1, k4, regime))
    return out


def simulate(sym: str, d: dict, c: Candidate, res: dict) -> Trade:
    """Follow one call with the same rules as the live Active calls tracker."""
    m15, h1, h4 = d["15m"], d["1h"], d["4h"]
    lo, hi, cl = m15["low"].to_numpy(), m15["high"].to_numpy(), m15["close"].to_numpy()
    ct15 = m15["close_time"].to_numpy()
    ot1, ct1 = h1["open_time"].to_numpy(), h1["close_time"].to_numpy()
    c1, e1 = h1["close"].to_numpy(), h1["ema20"].to_numpy()
    ct4 = h4["close_time"].to_numpy()
    tr4 = d.get("_tr4")
    if tr4 is None:
        tr4 = d["_tr4"] = trend_array(h4)
    long = c.side == "long"
    sign = 1 if long else -1
    entry, stop0, t1, t2 = res["entry"], res["stop"], res["t1"], res["t2"]
    R = abs(entry - stop0)
    stop, t1_hit = stop0, False
    t_open = int(ct15[c.i])
    outcome, r, j = "Still open at the end", None, c.i
    for j in range(c.i + 1, len(m15)):
        if (lo[j] <= stop) if long else (hi[j] >= stop):
            r = 0.5 * C.TARGET1_R if t1_hit else -1.0
            outcome = "Stopped at entry after T1" if t1_hit else "Stop-loss hit"
            break
        if not t1_hit and ((hi[j] >= t1) if long else (lo[j] <= t1)):
            t1_hit, stop = True, entry
        if (hi[j] >= t2) if long else (lo[j] <= t2):
            r = 0.5 * C.TARGET1_R + 0.5 * abs(t2 - entry) / R
            outcome = "Target 2 reached"
            break
        t = ct15[j]
        k1 = int(np.searchsorted(ct1, t, side="right"))
        k4 = int(np.searchsorted(ct4, t, side="right"))
        through = k1 and ot1[k1 - 1] >= t_open and ((c1[k1 - 1] < e1[k1 - 1]) if long else (c1[k1 - 1] > e1[k1 - 1]))
        flipped = k4 and tr4[k4 - 1] != sign
        if through or flipped:
            move_r = (cl[j] - entry) * sign / R
            r = 0.5 * C.TARGET1_R + 0.5 * move_r if t1_hit else move_r
            outcome = "Exit: 1h close back through EMA 20" if through else "Exit: 4h trend flipped"
            break
    if r is None:
        move_r = (cl[j] - entry) * sign / R
        r = 0.5 * C.TARGET1_R + 0.5 * move_r if t1_hit else move_r
    risk_pct = R / entry * 100
    fee_r = C.FEE_ROUNDTRIP_PCT / risk_pct
    return Trade(sym, c.side, t_open, int(ct15[j]), entry, stop0, risk_pct, r, r - fee_r, outcome, res["confidence"])


def run_variant(data: dict, cands: dict, overrides: dict) -> list[Trade]:
    for k, v in BASE.items():
        setattr(C, k, overrides.get(k, v))
    trades = []
    for sym, clist in cands.items():
        d = data[sym]
        m15, h1, h4 = d["15m"], d["1h"], d["4h"]
        busy_until = -1
        for c in clist:
            if c.i <= busy_until:
                continue
            frames = {"15m": m15.iloc[:c.i + 1], "1h": h1.iloc[:c.k1], "4h": h4.iloc[:c.k4]}
            res = engine.evaluate_side(c.side, frames, c.regime, None, 0.0, False, "spot")
            if not res["actionable"]:
                continue
            tr = simulate(sym, d, c, res)
            trades.append(tr)
            busy_until = int(np.searchsorted(m15["close_time"].to_numpy(), tr.t_close, side="left"))
    for k, v in BASE.items():
        setattr(C, k, v)
    return trades


# ------------------------------------------------------------------ report
def summarize(trades: list[Trade]) -> dict:
    if not trades:
        return {"trades": 0}
    rn = np.array([t.r_net for t in trades])
    order = np.argsort([t.t_close for t in trades])
    curve = np.cumsum(rn[order])
    dd = float((np.maximum.accumulate(np.r_[0, curve]) - np.r_[0, curve]).max())
    hold = np.array([(t.t_close - t.t_open) / 3_600_000 for t in trades])
    outcomes: dict[str, int] = {}
    for t in trades:
        outcomes[t.outcome] = outcomes.get(t.outcome, 0) + 1
    longs = [t for t in trades if t.side == "long"]
    shorts = [t for t in trades if t.side == "short"]
    return {
        "trades": len(trades), "win_rate": round(float((rn > 0).mean() * 100), 1),
        "total_r": round(float(rn.sum()), 1), "avg_r": round(float(rn.mean()), 3),
        "max_drawdown_r": round(dd, 1), "avg_hold_h": round(float(hold.mean()), 1),
        "avg_stop_pct": round(float(np.mean([t.risk_pct for t in trades])), 2),
        "fees_r": round(float(sum(t.r_gross - t.r_net for t in trades)), 1),
        "long": {"n": len(longs), "total_r": round(sum(t.r_net for t in longs), 1)},
        "short": {"n": len(shorts), "total_r": round(sum(t.r_net for t in shorts), 1)},
        "outcomes": outcomes,
    }


def report(results: dict, meta: dict) -> str:
    lines = [f"Backtest {meta['run_at']} · {meta['coins']} coins · {meta['days']} days · "
             f"fees {C.FEE_ROUNDTRIP_PCT}% per trade · results in R after fees", ""]
    hdr = f"{'Variant':<30}{'Trades':>7}{'Win %':>7}{'Total R':>9}{'Avg R':>8}{'Max DD':>8}{'Stop %':>8}{'Hold h':>8}"
    lines += [hdr, "-" * len(hdr)]
    for name, s in results.items():
        if not s.get("trades"):
            lines.append(f"{name:<30}{0:>7}")
            continue
        lines.append(f"{name:<30}{s['trades']:>7}{s['win_rate']:>7}{s['total_r']:>9}{s['avg_r']:>8}"
                     f"{s['max_drawdown_r']:>8}{s['avg_stop_pct']:>8}{s['avg_hold_h']:>8}")
    lines.append("")
    for name, s in results.items():
        if s.get("trades"):
            oc = ", ".join(f"{k} {v}" for k, v in sorted(s["outcomes"].items(), key=lambda x: -x[1]))
            lines.append(f"{name}: longs {s['long']['n']} ({s['long']['total_r']:+}R), "
                         f"shorts {s['short']['n']} ({s['short']['total_r']:+}R), fees {s['fees_r']}R. {oc}")
    lines += ["", "Total R: sum of all trade results. Avg R above about +0.1 after fees is a usable edge.",
              "Max DD: worst drop from a peak, in R. Past results do not guarantee future ones."]
    return "\n".join(lines)


def main() -> None:
    n_coins = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 90
    t0 = time.time()
    data, start_ms = asyncio.run(load(n_coins, days))
    btc4 = data["BTCUSDT"]["4h"]
    print("Finding setups…", flush=True)
    cands = {s: candidates_for(s, d, btc4, start_ms) for s, d in data.items()}
    print(f"{sum(len(v) for v in cands.values())} moments where trend and setup agree", flush=True)
    results, all_trades = {}, {}
    for name, ov in VARIANTS:
        print(f"Replaying {name.strip()}…", flush=True)
        trades = run_variant(data, cands, ov)
        results[name] = summarize(trades)
        all_trades[name] = [t.__dict__ for t in trades]
    meta = {"run_at": time.strftime("%Y-%m-%d %H:%M"), "coins": len(data), "days": days,
            "symbols": list(data), "seconds": round(time.time() - t0)}
    text = report(results, meta)
    print("\n" + text + f"\n\nDone in {meta['seconds']} s. Saved to data/backtest_latest.txt")
    (C.DATA_DIR / "backtest_latest.txt").write_text(text + "\n")
    (C.DATA_DIR / "backtest_latest.json").write_text(json.dumps(
        {"meta": meta, "results": results, "trades": all_trades}, default=float))


if __name__ == "__main__":
    main()
