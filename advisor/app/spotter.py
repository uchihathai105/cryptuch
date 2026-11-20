"""Spot tab (v1.3): every 5 minutes, refresh the price and the buy-today advice of the spot coins you typed in.

The low / high hour statistics (90 days of 1h candles) are built when a coin is added and rebuilt when older than
SPOT_STATS_REFRESH_HOURS. Uses the Advisor's candle cache and Binance client, so rate-limit handling is shared.
"""
from __future__ import annotations

import asyncio
import logging
import time

from . import config as C
from . import spot, store
from .binance import BinanceError
from .service import Advisor, clean, next_refresh_at

log = logging.getLogger("spotter")


class Spotter:
    def __init__(self, advisor: Advisor) -> None:
        self.adv = advisor
        self._lock: asyncio.Lock | None = None
        self._again = False
        self.state: dict = {"coins": {}, "last_refresh": None, "last_error": None, "refreshing": False,
                            "next_refresh": next_refresh_at(minutes=C.SPOT_REFRESH_MINUTES)}

    @property
    def lock(self) -> asyncio.Lock:
        if self._lock is None:               # created inside the running loop (Python 3.9)
            self._lock = asyncio.Lock()
        return self._lock

    async def validate(self, symbol: str) -> None:
        """Raise ValueError if Binance has no such spot USDT pair."""
        try:
            await self.adv.api.klines("spot", symbol, "1h", 5)
        except BinanceError as e:
            if "HTTP 400" in str(e):
                raise ValueError(f"Binance has no spot pair {symbol}") from e
            raise

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
                    log.exception("Spot refresh failed")
                    self.state["last_error"] = str(e)
                finally:
                    self.state["refreshing"] = False
                    self.state["next_refresh"] = next_refresh_at(minutes=C.SPOT_REFRESH_MINUTES)
                if not self._again:
                    break

    async def _stats(self, symbol: str, now_ms: int) -> dict | None:
        row = store.spot_stats_get(symbol)
        if row and now_ms - row["computed_at"] < C.SPOT_STATS_REFRESH_HOURS * 3_600_000:
            return row
        try:
            df = await self.adv.api.history_1h(symbol, C.SPOT_DAYS)
            st = spot.extremes(df)
        except BinanceError as e:
            log.warning("Spot statistics %s: %s", symbol, e)
            return row                       # keep the old statistics if any
        if st is None:
            return row
        store.spot_stats_save(symbol, st, now_ms)
        return {"computed_at": now_ms, "stats": st}

    async def _refresh(self) -> None:
        symbols = store.spot_coins()
        if not symbols:
            self.state["coins"] = {}
            return
        now_ms = int(time.time() * 1000)
        settings = store.get_settings()
        coins = {}
        for s in symbols:
            base = {"symbol": s, "base": s[:-4]}
            try:
                df = await self.adv.candles("spot", s, "1h")
                price = await self.adv.api.spot_price(s)
            except BinanceError as e:
                old = self.state["coins"].get(s)
                coins[s] = old or {**base, "pending": True, "note": f"Could not load data: {e}"}
                if old:
                    old["note"] = f"Showing the last data; refresh failed: {e}"
                continue
            row = await self._stats(s, now_ms)
            stats = row["stats"] if row else None
            advice = spot.advise(df, price, now_ms, stats, settings)
            if advice is None:
                coins[s] = {**base, "pending": True, "note": "Not enough 1h candle history yet."}
                continue
            coins[s] = {**base, "price": price, "advice": advice, "updated": now_ms,
                        "stats": stats, "stats_at": row["computed_at"] if row else None,
                        "windows": spot.windows(stats) if stats else None,
                        "note": None if stats else "Low / high hour statistics are not ready yet (needs 30 complete days of history)."}
        self.state["coins"] = clean(coins)
