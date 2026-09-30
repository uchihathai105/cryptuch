"""Watch tab: every 5 minutes, score the coins you typed in, keep a side for each and grade the signals.

Uses the Advisor's candle cache and Binance client, so rate-limit handling is shared.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

from . import config as C
from . import store, telegram, watch
from .binance import BinanceError
from .service import Advisor, clean, next_refresh_at

log = logging.getLogger("watcher")
RANK = {"Weak": 0, "Medium": 1, "Strong": 2}


def normalise(sym: str) -> str:
    sym = str(sym).upper().strip().replace("/", "").replace("-", "").replace(" ", "")
    if sym and not sym.endswith("USDT"):
        sym += "USDT"
    if not re.fullmatch(r"[A-Z0-9]{2,20}USDT", sym or ""):
        raise ValueError("Enter a coin symbol such as PEPE or PEPEUSDT")
    return sym


class Watcher:
    def __init__(self, advisor: Advisor) -> None:
        self.adv = advisor
        self._lock: asyncio.Lock | None = None
        self._again = False
        self.state: dict = {"coins": {}, "last_refresh": None, "last_error": None, "refreshing": False,
                            "next_refresh": next_refresh_at(minutes=C.WATCH_REFRESH_MINUTES),
                            "banner": None}

    @property
    def lock(self) -> asyncio.Lock:
        if self._lock is None:               # created inside the running loop (Python 3.9)
            self._lock = asyncio.Lock()
        return self._lock

    def market_for(self, symbol: str) -> str:
        return "futures" if symbol in self.adv.futures_set else "spot"

    async def _futures_set(self) -> None:
        if not self.adv.futures_set:
            try:
                self.adv.futures_set = await self.adv.api.futures_symbols()
                self.adv.futures_set_at = time.time()
            except BinanceError as e:
                log.warning("Futures symbol list unavailable: %s", e)

    async def validate(self, symbol: str) -> None:
        """Raise ValueError if Binance has no such USDT pair."""
        await self._futures_set()
        try:
            await self.adv.candles(self.market_for(symbol), symbol, "5m")
        except BinanceError as e:
            if "HTTP 400" in str(e):
                raise ValueError(f"Binance has no {symbol} pair") from e
            raise

    # ------------------------------------------------------------ cycle
    async def refresh(self) -> None:
        if self.lock.locked():
            self._again = True               # a coin was added mid-cycle: go round once more
            return
        async with self.lock:
            while True:
                self._again = False
                self.state["refreshing"] = True
                try:
                    await self._refresh()
                    self.state["last_refresh"] = time.time()
                    self.state["last_error"] = None
                except Exception as e:       # keep the previous data visible
                    log.exception("Watch refresh failed")
                    self.state["last_error"] = str(e)
                finally:
                    self.state["refreshing"] = False
                    self.state["next_refresh"] = next_refresh_at(minutes=C.WATCH_REFRESH_MINUTES)
                if not self._again:
                    break

    async def _refresh(self) -> None:
        symbols = store.watch_coins()
        if not symbols:
            self.state.update(coins={}, banner=None)
            return
        await self._futures_set()
        active = {s: store.watch_active_signal(s) for s in symbols}
        jobs = []
        for s in symbols:
            m = self.market_for(s)
            jobs += [(m, s, "5m"), (m, s, "1h")]
            if active[s]:
                jobs.append((m, s, "1m"))    # 1m candles only for coins with an open signal
        await asyncio.gather(*(self.adv._safe_candles(*j) for j in jobs))

        now_ms = int(time.time() * 1000)
        coins, alerts = {}, []
        for s in symbols:
            m = self.market_for(s)
            info = watch.analyze(self.adv.frames.get((m, s, "5m")), self.adv.frames.get((m, s, "1h")))
            base = {"symbol": s, "base": s[:-4], "market": m, "futures_listed": m == "futures"}
            if info is None:
                coins[s] = {**base, "pending": True, "note": "Not enough candle history yet (needs about 10 hours of 5m candles)."}
                continue

            prev = store.watch_state_get(s)
            bars = (info["close_time"] - prev["since"]) / watch.FIVE_MIN_MS if prev else 999
            side, flipped = watch.decide_side(info["score"], prev["side"] if prev else None, bars)
            if flipped:
                store.watch_state_set(s, side, info["close_time"], info["price"], info["score"])
                since, since_price = info["close_time"], info["price"]
                if prev and RANK.get(info["strength"], 0) >= RANK.get(C.WATCH_ALERT_MIN_STRENGTH or "", 9):
                    fut, spot = watch.LABELS[side]
                    alerts.append(f"🔁 <b>{s}</b> now <b>{fut if m == 'futures' else spot}</b> "
                                  f"({info['strength']}, score {info['score']:+.0f}) at {watch.fmt(info['price'])}")
            else:
                store.watch_state_score(s, info["score"])
                since, since_price = prev["since"], prev["since_price"]

            sig = active[s]
            if sig:                          # advance the open signal, and its mirror, on 1m candles
                st, mir = store.watch_signal_state(sig), sig["mirror"]
                k1 = self.adv.frames.get((m, s, "1m"))
                flip_px = info["price"] if flipped and sig["side"] != side else None
                watch.advance(st, k1, now_ms, flip_px)
                watch.advance(mir, k1, now_ms, flip_px)
                store.watch_signal_save(sig["id"], st, mir)
                sig = None if st["status"] == "closed" else {**sig, **st}
            if sig is None:
                last_closed = store.watch_last_closed_at(s)
                if flipped or last_closed is None or now_ms - last_closed >= C.WATCH_REOPEN_COOLDOWN_MIN * 60_000:
                    st = watch.new_signal(side, info["price"], info["atr"], m, info["close_time"])
                    mir = watch.mirror_of(st)
                    store.watch_signal_open(s, m, st, mir, info)
                    sig = {**st, "id": None, "strength": info["strength"], "score": info["score"]}

            recent = store.watch_recent_results(s, C.WATCH_COIN_WINDOW)
            low_rel = len(recent) >= C.WATCH_COIN_MIN and sum(recent) / len(recent) < 0
            fut, spot = watch.LABELS[side]
            coins[s] = {
                **base, **{k: info[k] for k in ("price", "pct24", "score", "state", "adx", "contrib", "rsi", "vol_ratio",
                                                "pos_in_range", "htf", "range_1h", "range_4h", "range_24h",
                                                "range_ratio", "swings_24h", "atr")},
                "side": side, "futures_label": fut if m == "futures" else None, "spot_label": spot,
                "since": since, "since_price": since_price, "updated": info["close_time"],
                "strength": "Weak" if low_rel else info["strength"], "raw_strength": info["strength"],
                "low_reliability": low_rel,
                "signal": self._signal_view(sig, info["price"]) if sig else None,
            }

        recent_all = store.watch_recent_results(None, C.WATCH_QUALITY_WINDOW)
        banner = None
        if len(recent_all) >= C.WATCH_QUALITY_MIN and sum(recent_all) / len(recent_all) < 0:
            banner = (f"Low reliability: the last {len(recent_all)} signals average "
                      f"{sum(recent_all) / len(recent_all):+.2f}R after costs. Treat these sides as weak until the rules improve.")
        self.state.update(coins=clean(coins), banner=banner)
        if alerts and telegram.configured():
            await telegram.send("\n\n".join(alerts[:10]))

    @staticmethod
    def _signal_view(sig: dict, price: float) -> dict:
        st = {k: sig.get(k) for k in ("side", "entry", "stop", "t1", "t2", "t1_hit", "cost_r")}
        now_r = watch._gross_mtm(st, price) - (sig.get("cost_r") or 0)
        return {k: sig.get(k) for k in ("id", "side", "entry", "stop", "t1", "t2", "t1_hit", "created_at", "status",
                                       "expired_at", "result_1h_r", "mfe_r", "mae_r", "strength", "score")} | {"now_r": round(now_r, 2)}
