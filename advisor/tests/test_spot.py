"""Checks for the Spot tab (v1.3) with synthetic candles and a fake Binance.
Run: .venv/bin/python -m tests.test_spot"""
import asyncio
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd

from app import config as C

C.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"      # never touch data/advisor.db
from app import spot, store                            # noqa: E402
from app.indicators import add_indicators               # noqa: E402


def make(days=100, seed=3, low_hour=4, high_hour=16, end_ms=None):
    """1h candles where the day's low is at `low_hour` and the high at `high_hour` (Vietnam time) on every day."""
    rng = np.random.default_rng(seed)
    end = end_ms or (int(time.time() * 1000) // 3_600_000) * 3_600_000
    start = (end - days * 86_400_000 + 7 * 3_600_000) // 86_400_000 * 86_400_000 - 7 * 3_600_000
    rows, px = [], 100.0
    t = start
    while t < end:
        h = int(((t // 3_600_000) + 7) % 24)
        px *= 1 + rng.normal(0.0003, 0.002)
        o, c = px, px * (1 + rng.normal(0, 0.002))
        hi, lo = max(o, c) * 1.001, min(o, c) * 0.999
        if h == high_hour:
            hi *= 1.03
        if h == low_hour:
            lo *= 0.97
        rows.append([t, o, hi, lo, c, 1000 * rng.uniform(0.8, 1.2), t + 3_599_999])
        t += 3_600_000
    return pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume", "close_time"])


def test_extremes_find_the_hours():
    p = spot.extremes(make())
    assert p["low_top"][0] == 4 and p["high_top"][0] == 16
    assert p["low"][4] > 95 and p["high"][16] > 95
    assert abs(sum(p["low"]) - 100) < 1e-6 and abs(sum(p["high"]) - 100) < 1e-6
    assert p["cum_low"][3] < 5 and p["cum_low"][4] > 95
    assert spot.windows(p)["strong"]


def test_too_short_gives_none():
    assert spot.extremes(make(days=10)) is None


def test_random_days_are_not_called_strong():
    rng = np.random.default_rng(9)
    df = make(low_hour=-1, high_hour=-1)
    df["noise"] = rng.normal(0, 0.01, len(df))
    df["high"] *= 1 + df["noise"].clip(lower=0)
    df["low"] *= 1 - (-df["noise"]).clip(lower=0)
    assert not spot.windows(spot.extremes(df.drop(columns="noise")))["strong"]


def test_advice_shapes():
    df = add_indicators(make())
    now = int(df["close_time"].iloc[-1]) + 1
    st = spot.extremes(df)
    a = spot.advise(df, float(df["close"].iloc[-1]), now, st, dict(C.DEFAULT_SETTINGS))
    assert a["verdict"] in ("Buy now", "Buy on dip", "Wait", "Avoid")
    assert 0 <= a["score"] <= 100 and 0 <= a["pos_in_range"] <= 1
    if a["levels"]:
        l = a["levels"]
        assert l["stop"] < l["entry"] < l["t1"] < l["t2"]
        assert a["sizing"] and a["sizing"]["qty"] > 0
    assert spot.advise(df.head(20), 100.0, now, st) is None


def test_downtrend_is_avoided():
    df = make(days=20)
    df["close"] = df["open"] = np.linspace(100, 60, len(df))
    df["high"], df["low"] = df["close"] * 1.002, df["close"] * 0.998
    df = add_indicators(df)
    a = spot.advise(df, float(df["close"].iloc[-1]), int(df["close_time"].iloc[-1]) + 1, None)
    assert a["verdict"] == "Avoid" and a["levels"] is None


def test_defaults_then_add_and_remove():
    assert store.spot_coins() == ["NEARUSDT", "ZECUSDT"]
    store.spot_add("SOLUSDT")
    assert store.spot_coins() == ["NEARUSDT", "ZECUSDT", "SOLUSDT"]
    store.spot_remove("NEARUSDT")
    store.spot_remove("ZECUSDT")
    assert store.spot_coins() == ["SOLUSDT"]          # removed defaults do not come back
    store.spot_remove("SOLUSDT")
    assert store.spot_coins() == []


class FakeApi:
    def __init__(self, df):
        self.df = df

    async def klines(self, market, symbol, interval, limit):
        if symbol == "BADUSDT":
            from app.binance import BinanceError
            raise BinanceError("HTTP 400 for /api/v3/klines: Invalid symbol")
        d = self.df.tail(limit).copy()
        return d

    async def history_1h(self, symbol, days):
        return self.df

    async def spot_price(self, symbol):
        return float(self.df["close"].iloc[-1])

    async def close(self):
        pass


def test_service_and_api():
    from fastapi.testclient import TestClient
    from app import main
    df = make()
    main.advisor.api = FakeApi(df)

    async def fake_candles(market, symbol, interval):
        return add_indicators(df)
    main.advisor.candles = fake_candles
    store.spot_add("NEARUSDT")
    asyncio.run(main.spotter.refresh())
    c = main.spotter.state["coins"]["NEARUSDT"]
    assert c["advice"]["verdict"] and c["stats"]["days"] >= 90 and c["windows"]["notes"]
    cl = TestClient(main.app)
    r = cl.get("/api/spot").json()
    assert [x["symbol"] for x in r["coins"]] == ["NEARUSDT"] and r["strategy"]
    assert cl.post("/api/spot", json={"symbol": "bad"}).status_code == 400
    assert cl.post("/api/spot", json={"symbol": "near"}).status_code == 400      # duplicate
    assert cl.post("/api/spot", json={"symbol": "!!"}).status_code == 400
    assert cl.post("/api/spot", json={"symbol": "zec"}).status_code == 200
    assert [x["symbol"] for x in cl.get("/api/spot").json()["coins"]] == ["NEARUSDT", "ZECUSDT"]
    assert [x["symbol"] for x in cl.delete("/api/spot/NEARUSDT").json()["coins"]] == ["ZECUSDT"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
