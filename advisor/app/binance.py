"""Binance public market data (no API key). Handles rate limits (URS N02)."""
from __future__ import annotations

import asyncio
import logging
import time

import httpx
import pandas as pd

from . import config

log = logging.getLogger("binance")


class BinanceError(Exception):
    pass


class Binance:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(timeout=20, headers={"User-Agent": "crypto-advisor/1.0"})
        self.sem = asyncio.Semaphore(config.MAX_CONCURRENT_REQUESTS)
        self.blocked_until = 0.0
        self.spot_base = config.SPOT_BASE
        self.used_weight: dict[str, str] = {}

    async def close(self) -> None:
        await self.client.aclose()

    async def _get(self, base: str, path: str, params: dict | None = None, market: str = "spot"):
        if time.time() < self.blocked_until:
            raise BinanceError(f"Paused by Binance rate limit for {int(self.blocked_until - time.time())}s")
        async with self.sem:
            for attempt in range(3):
                try:
                    r = await self.client.get(base + path, params=params)
                except httpx.HTTPError as e:
                    if market == "spot" and base == config.SPOT_BASE and attempt == 0:
                        log.warning("Spot API unreachable (%s); trying the public data mirror", e)
                        base = self.spot_base = config.SPOT_FALLBACK
                        continue
                    if attempt == 2:
                        raise BinanceError(f"Network error: {e}") from e
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                w = r.headers.get("x-mbx-used-weight-1m")
                if w:
                    self.used_weight[market] = w
                if r.status_code in (429, 418):
                    wait = int(r.headers.get("Retry-After", "60"))
                    self.blocked_until = time.time() + wait
                    raise BinanceError(
                        f"Binance rate limit hit (HTTP {r.status_code}); pausing requests for {wait}s"
                    )
                if r.status_code == 451:
                    raise BinanceError("Binance refuses requests from this location (HTTP 451)")
                if r.status_code >= 500 and attempt < 2:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                if r.status_code != 200:
                    raise BinanceError(f"HTTP {r.status_code} for {path}: {r.text[:200]}")
                return r.json()
        raise BinanceError(f"Failed after retries: {path}")

    # ------------------------------------------------------------ endpoints
    async def spot_tickers(self) -> list[dict]:
        return await self._get(self.spot_base, "/api/v3/ticker/24hr")

    async def futures_tickers(self) -> list[dict]:
        return await self._get(config.FUTURES_BASE, "/fapi/v1/ticker/24hr", market="futures")

    async def futures_symbols(self) -> set[str]:
        info = await self._get(config.FUTURES_BASE, "/fapi/v1/exchangeInfo", market="futures")
        return {
            s["symbol"] for s in info.get("symbols", [])
            if s.get("contractType") == "PERPETUAL" and s.get("quoteAsset") == "USDT"
            and s.get("status") == "TRADING"
        }

    async def premium_index(self) -> dict[str, float]:
        rows = await self._get(config.FUTURES_BASE, "/fapi/v1/premiumIndex", market="futures")
        return {r["symbol"]: float(r.get("lastFundingRate") or 0) for r in rows}

    async def open_interest_change(self, symbol: str) -> float | None:
        """Open-interest change over the last 24h, in percent."""
        rows = await self._get(
            config.FUTURES_BASE, "/futures/data/openInterestHist",
            {"symbol": symbol, "period": "1h", "limit": 25}, market="futures",
        )
        if not rows or len(rows) < 2:
            return None
        first = float(rows[0]["sumOpenInterest"])
        last = float(rows[-1]["sumOpenInterest"])
        return (last / first - 1) * 100 if first else None

    async def klines(self, market: str, symbol: str, interval: str, limit: int) -> pd.DataFrame:
        if market == "spot":
            data = await self._get(self.spot_base, "/api/v3/klines",
                                   {"symbol": symbol, "interval": interval, "limit": limit})
        else:
            data = await self._get(config.FUTURES_BASE, "/fapi/v1/klines",
                                   {"symbol": symbol, "interval": interval, "limit": limit},
                                   market="futures")
        return klines_to_frame(data)


def klines_to_frame(data: list) -> pd.DataFrame:
    df = pd.DataFrame(
        [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), int(k[6])]
         for k in data],
        columns=["open_time", "open", "high", "low", "close", "volume", "close_time"],
    )
    # Signals use closed candles only (URS F04): drop the candle still forming.
    now_ms = int(time.time() * 1000)
    return df[df["close_time"] < now_ms].reset_index(drop=True)
