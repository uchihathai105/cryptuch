"""Refresh cycle: coin lists, candle cache, calls, active-call tracking, alerts."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math
import time

import numpy as np
import pandas as pd

from . import config as C
from . import engine, store, telegram
from .binance import Binance, BinanceError
from .indicators import add_indicators

log = logging.getLogger("advisor")
VN = dt.timezone(dt.timedelta(hours=7))


def clean(o):
    """Make results JSON-safe (numpy numbers, NaN)."""
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def next_refresh_at(now: float | None = None) -> float:
    now = now or time.time()
    step = C.REFRESH_MINUTES * 60
    return (math.floor(now / step) + 1) * step + C.REFRESH_DELAY_SECONDS


class Advisor:
    def __init__(self, api: Binance | None = None) -> None:
        self.api = api or Binance()
        self.raw: dict[tuple, pd.DataFrame] = {}      # (market, symbol, interval) -> closed candles
        self.frames: dict[tuple, pd.DataFrame] = {}   # same, with indicators
        self.futures_set: set[str] = set()
        self.futures_set_at = 0.0
        self.oi_cache: dict[str, tuple[float, float | None]] = {}
        self._lock: asyncio.Lock | None = None
        self.state: dict = {
            "last_refresh": None, "last_error": None, "refreshing": False,
            "regime": None, "lists": {"top_volume": [], "top_gainers": [], "watchlist": []},
            "coins": {}, "next_refresh": next_refresh_at(),
        }

    @property
    def lock(self) -> asyncio.Lock:
        # created inside the running loop (Python 3.9 binds locks to the loop at creation)
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    # ------------------------------------------------------------ candles
    async def candles(self, market: str, symbol: str, interval: str) -> pd.DataFrame:
        key = (market, symbol, interval)
        limit = C.CANDLE_LIMITS[interval]
        step_ms = C.INTERVAL_MINUTES[interval] * 60_000
        now_ms = int(time.time() * 1000)
        old = self.raw.get(key)
        if old is not None and len(old):
            # a newer candle has closed once a full interval has passed since the last cached close
            missing = int((now_ms - int(old["close_time"].iloc[-1])) // step_ms)
            if missing <= 0:
                return self.frames[key]
            fetch = min(limit, missing + 2)
            new = await self.api.klines(market, symbol, interval, fetch)
            df = pd.concat([old, new]).drop_duplicates("open_time", keep="last")
            df = df.sort_values("open_time").tail(limit).reset_index(drop=True)
        else:
            df = await self.api.klines(market, symbol, interval, limit)
        self.raw[key] = df
        self.frames[key] = add_indicators(df)
        return self.frames[key]

    async def _safe_candles(self, market, symbol, interval):
        try:
            return await self.candles(market, symbol, interval)
        except BinanceError as e:
            log.warning("%s %s %s: %s", market, symbol, interval, e)
            if "rate limit" in str(e).lower():
                raise
            return self.frames.get((market, symbol, interval))

    async def _oi(self, symbol: str) -> float | None:
        cached = self.oi_cache.get(symbol)
        if cached and time.time() - cached[0] < 600:
            return cached[1]
        try:
            v = await self.api.open_interest_change(symbol)
        except BinanceError:
            v = cached[1] if cached else None
        self.oi_cache[symbol] = (time.time(), v)
        return v

    # ------------------------------------------------------------ lists
    @staticmethod
    def eligible(t: dict) -> bool:
        s = t["symbol"]
        if not s.endswith("USDT") or s.endswith(C.EXCLUDED_SUFFIXES):
            return False
        base = s[:-4]
        return base not in C.STABLE_BASES and float(t.get("quoteVolume") or 0) >= C.MIN_QUOTE_VOLUME_USDT \
            and int(t.get("count") or 1) > 0

    # ------------------------------------------------------------ main cycle
    async def refresh(self) -> None:
        if self.lock.locked():
            return
        async with self.lock:
            self.state["refreshing"] = True
            started = time.time()
            try:
                await self._refresh()
                self.state["last_refresh"] = time.time()
                self.state["last_error"] = None
                log.info("Refresh done in %.1fs", time.time() - started)
            except Exception as e:  # keep yesterday's data visible (URS N06)
                log.exception("Refresh failed")
                self.state["last_error"] = str(e)
            finally:
                self.state["refreshing"] = False
                self.state["next_refresh"] = next_refresh_at()

    async def _refresh(self) -> None:
        settings = store.get_settings()
        tickers = await self.api.spot_tickers()
        by_symbol = {t["symbol"]: t for t in tickers}
        if time.time() - self.futures_set_at > 3600 or not self.futures_set:
            self.futures_set = await self.api.futures_symbols()
            self.futures_set_at = time.time()
        funding = await self.api.premium_index()

        pool = [t for t in tickers if self.eligible(t)]
        top_volume = [t["symbol"] for t in sorted(pool, key=lambda t: -float(t["quoteVolume"]))[:C.LIST_SIZE]]
        top_gainers = [t["symbol"] for t in sorted(pool, key=lambda t: -float(t["priceChangePercent"]))[:C.LIST_SIZE]]
        watchlist = [s for s in settings["watchlist"] if s in by_symbol]
        active = store.active_calls()
        universe = list(dict.fromkeys(["BTCUSDT", *watchlist, *top_volume, *top_gainers,
                                       *(c["symbol"] for c in active)]))
        long_term_set = set(["BTCUSDT", *watchlist, *top_volume])

        # candles: spot 15m/1h/4h for all, 1d/1w for long-term set, futures 15m/1h/4h where listed
        jobs = []
        for s in universe:
            for iv in ("15m", "1h", "4h"):
                jobs.append(("spot", s, iv))
                if s in self.futures_set:
                    jobs.append(("futures", s, iv))
            if s in long_term_set:
                jobs += [("spot", s, "1d"), ("spot", s, "1w")]
        await asyncio.gather(*(self._safe_candles(*j) for j in jobs))
        oi = dict(zip(
            [s for s in universe if s in self.futures_set],
            await asyncio.gather(*(self._oi(s) for s in universe if s in self.futures_set)),
        ))

        btc4 = self.frames.get(("spot", "BTCUSDT", "4h"))
        regime = engine.regime_from_btc(btc4) if btc4 is not None and len(btc4) > 60 else "Neutral"
        btc_daily = self.frames.get(("spot", "BTCUSDT", "1d"))
        today = dt.datetime.now(VN).strftime("%Y-%m-%d")

        coins, alerts = {}, []
        gainers = set(top_gainers)
        for s in universe:
            t = by_symbol.get(s, {})
            pct24 = float(t.get("priceChangePercent") or 0)
            coin = {"symbol": s, "base": s[:-4], "price": float(t.get("lastPrice") or 0), "pct24": pct24,
                    "quote_volume": float(t.get("quoteVolume") or 0), "futures_listed": s in self.futures_set,
                    "is_gainer": s in gainers}
            for market in ("spot", "futures"):
                if market == "futures" and s not in self.futures_set:
                    coin["futures"] = None
                    continue
                frames = {iv: self.frames.get((market, s, iv)) for iv in ("15m", "1h", "4h")}
                if any(f is None or len(f) < 60 for f in frames.values()):
                    coin[market] = {"call": "Wait", "reason": "Not enough candle history yet", "checks": [],
                                    "confidence": 0, "market": market}
                    continue
                pos = {"funding": funding.get(s), "oi_change": oi.get(s)} if market == "futures" else None
                coin[market] = engine.day_trade_call(frames, regime, market, pos, pct24, s in gainers)
                coin[market]["funding"] = pos["funding"] if pos else None
                coin[market]["oi_change"] = pos["oi_change"] if pos else None
            if s in long_term_set and btc_daily is not None:
                d, w = self.frames.get(("spot", s, "1d")), self.frames.get(("spot", s, "1w"))
                if d is not None and w is not None and len(d) and len(w):
                    lt = engine.long_term_rating(d, w, btc_daily, s == "BTCUSDT")
                    prev = store.rating_change(s, lt["rating"])
                    last_saved = store._q("SELECT rating FROM ratings WHERE symbol=? ORDER BY day DESC LIMIT 1",
                                          (s,)).fetchone()
                    if last_saved and last_saved["rating"] != lt["rating"]:
                        alerts.append(f"📊 <b>{s}</b> long-term rating: {last_saved['rating']} → <b>{lt['rating']}</b>")
                    store.save_rating(today, s, lt["rating"], lt["close"])
                    lt["change"] = prev
                    coin["long_term"] = lt
            coins[s] = coin

        alerts += self._track_active(coins)
        alerts += self._open_new(coins)

        self.state.update(regime=regime, coins=clean(coins),
                          lists={"top_volume": top_volume, "top_gainers": top_gainers, "watchlist": watchlist})
        self.state["used_weight"] = dict(self.api.used_weight)
        if alerts and telegram.configured():
            await telegram.send("\n\n".join(alerts[:15]))

    # ------------------------------------------------------------ call tracking (URS F11, F23)
    def _open_new(self, coins: dict) -> list[str]:
        out = []
        for s, coin in coins.items():
            for market in ("futures", "spot"):
                res = coin.get(market)
                if not res or res.get("call") not in ("Long", "Short", "Buy"):
                    continue
                if store.has_active(s, market):
                    continue
                store.open_call(s, market, res)
                icon = "🟢" if res["side"] == "long" else "🔴"
                f = engine.fmt
                out.append(
                    f"{icon} <b>{res['call']} {s}</b> ({market}) · confidence {res['confidence']}\n"
                    f"Entry {f(res['entry_zone'][0])}–{f(res['entry_zone'][1])} · Stop {f(res['stop'])}\n"
                    f"T1 {f(res['t1'])} · T2 {f(res['t2'])} · R:R {res['rr']:.1f}\n{res['reason']}")
        return out

    def _track_active(self, coins: dict) -> list[str]:
        out = []
        f = engine.fmt
        for c in store.active_calls():
            e15 = self.frames.get((c["market"], c["symbol"], "15m"))
            e1h = self.frames.get((c["market"], c["symbol"], "1h"))
            e4h = self.frames.get((c["market"], c["symbol"], "4h"))
            if e15 is None or e1h is None:
                continue
            long = c["side"] == "long"
            sign = 1 if long else -1
            entry, R = c["entry"], abs(c["entry"] - c["stop"]) or 1e-12
            stop = entry if c["t1_hit"] else c["stop"]
            t1_hit = bool(c["t1_hit"])
            closed = None
            new = e15[e15["open_time"] >= max(c["created_at"], c["checked_until"])]
            for _, k in new.iterrows():
                hit_stop = k["low"] <= stop if long else k["high"] >= stop
                if hit_stop:
                    r = 0.5 * C.TARGET1_R if t1_hit else -1.0
                    closed = ("Stopped at entry after T1" if t1_hit else "Stop-loss hit", stop, r)
                    break
                if not t1_hit and (k["high"] >= c["t1"] if long else k["low"] <= c["t1"]):
                    t1_hit, stop = True, entry
                if k["high"] >= c["t2"] if long else k["low"] <= c["t2"]:
                    closed = ("Target 2 reached", c["t2"],
                              0.5 * C.TARGET1_R + 0.5 * abs(c["t2"] - entry) / R)
                    break
            checked = int(new["close_time"].iloc[-1]) if len(new) else c["checked_until"]
            if not closed:
                h = e1h[e1h["open_time"] >= c["created_at"]]
                last1h = e1h.iloc[-1]
                through = len(h) and ((last1h["close"] < last1h["ema20"]) if long else (last1h["close"] > last1h["ema20"]))
                flipped = e4h is not None and engine.trend_of(e4h) != ("up" if long else "down")
                if through or flipped:
                    px = float(e15.iloc[-1]["close"])
                    move_r = (px - entry) * sign / R
                    r = 0.5 * C.TARGET1_R + 0.5 * move_r if t1_hit else move_r
                    why = "1h close back through EMA 20" if through else "4h trend no longer supports it"
                    closed = (f"Exit signal: {why}", px, r)
            if closed:
                outcome, px, r = closed
                store.update_call(c["id"], status="closed", closed_at=int(time.time() * 1000), outcome=outcome,
                                  exit_price=px, result_r=round(r, 2), advice="Exit", advice_note=outcome,
                                  t1_hit=int(t1_hit), checked_until=checked)
                icon = "✅" if r > 0 else "⛔"
                out.append(f"{icon} <b>Exit {c['symbol']}</b> ({c['market']} {c['call']}): {outcome} at {f(px)} "
                           f"· result {r:+.2f}R")
            else:
                last1h = e1h.iloc[-1]
                note = (f"1h close {f(last1h['close'])} {'above' if long else 'below'} EMA 20 "
                        f"({f(last1h['ema20'])}); stop {f(stop)}")
                if t1_hit and not c["t1_hit"]:
                    out.append(f"🎯 <b>{c['symbol']}</b> ({c['market']}) hit T1 {f(c['t1'])}: take half, "
                               f"stop moves to entry {f(entry)}")
                if t1_hit:
                    note = "T1 reached; stop moved to entry. " + note
                store.update_call(c["id"], t1_hit=int(t1_hit), advice="Hold", advice_note=note, checked_until=checked)
        return out

    # ------------------------------------------------------------ chart data
    async def chart(self, symbol: str, market: str, interval: str) -> list[dict]:
        df = self.frames.get((market, symbol, interval))
        if df is None:
            df = await self.candles(market, symbol, interval)
        cols = ["open_time", "open", "high", "low", "close", "volume", "ema20", "ema50", "vwap", "sma200"]
        out = []
        for row in df[cols].itertuples(index=False):
            out.append({"t": int(row.open_time // 1000), "o": row.open, "h": row.high, "l": row.low, "c": row.close,
                        "v": row.volume, "ema20": row.ema20, "ema50": row.ema50,
                        "vwap": row.vwap if interval in ("15m", "1h") else None,
                        "sma200": row.sma200 if interval == "1d" else None})
        return clean(out)
