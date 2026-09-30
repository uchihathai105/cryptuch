"""End-to-end check of the Watch cycle with a fake Binance and a throw-away database.
Run: .venv/bin/python -m tests.test_watcher"""
import asyncio
import math
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd

from app import config as C

C.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"      # never touch data/advisor.db
from app import store, watch                           # noqa: E402  (must come after the DB path is set)
from app.binance import BinanceError                   # noqa: E402
from app.service import Advisor                        # noqa: E402
from app.watcher import Watcher, normalise             # noqa: E402

MIN = 60_000


class FakeApi:
    """Serves candles for any symbol from a price path defined per minute; 'NOPEUSDT' does not exist."""
    used_weight: dict = {}

    def __init__(self):
        self.t0 = (int(time.time() * 1000) - 30 * 3600_000) // (5 * MIN) * (5 * MIN)
        self.end_min = 1500                # minutes of data available
        self.path = lambda m: 100 + 30 * m / 1500 + 0.2 * math.sin(m / 7)
        self.calls = 0

    async def futures_symbols(self):
        return {"PEPEUSDT"}

    async def close(self):
        pass

    async def klines(self, market, symbol, interval, limit):
        self.calls += 1
        if symbol.startswith("NOPE"):
            raise BinanceError("HTTP 400 for /api/v3/klines: Invalid symbol")
        k = {"1m": 1, "5m": 5, "1h": 60, "4h": 240}[interval]
        n_candles = self.end_min // k
        rows = []
        for i in range(max(0, n_candles - limit), n_candles):
            ps = [self.path(m) for m in range(i * k, (i + 1) * k)]
            rows.append([self.t0 + i * k * MIN, ps[0], max(ps) * 1.0004, min(ps) * 0.9996, ps[-1], 100.0,
                         self.t0 + (i + 1) * k * MIN - 1])
        return pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume", "close_time"])


async def main():
    api = FakeApi()
    adv = Advisor(api)
    w = Watcher(adv)

    # input handling
    assert normalise("pepe") == "PEPEUSDT" and normalise("btc/usdt") == "BTCUSDT"
    try:
        normalise("bad symbol!")
        raise SystemExit("expected ValueError")
    except ValueError:
        pass
    try:
        await w.validate("NOPEUSDT")
        raise SystemExit("expected ValueError for unknown coin")
    except ValueError as e:
        assert "no NOPEUSDT" in str(e)

    # 1) add a coin: it gets a side and one signal straight away
    await w.validate("PEPEUSDT")
    store.watch_add("PEPEUSDT")
    await w.refresh()
    coin = w.state["coins"]["PEPEUSDT"]
    assert coin["side"] == "long" and coin["score"] > 10 and coin["futures_label"] == "Long" and coin["spot_label"] == "Buy", coin
    sig = store.watch_active_signal("PEPEUSDT")
    assert sig and sig["side"] == "long" and sig["status"] == "open" and sig["stop"] < sig["entry"] < sig["t1"] < sig["t2"], sig
    assert sig["mirror"]["side"] == "short" and sig["components"]
    print("ok   first analysis:", coin["side"], coin["score"], coin["strength"], coin["state"])

    # 2) same candle again: no duplicate signal
    await w.refresh()
    assert len(store.watch_open_signals()) == 1

    # 3) price crashes: the side flips, the old signal closes as "flip", a new short signal opens
    crash_from = api.end_min
    base = api.path
    api.path = lambda m: base(m) if m < crash_from else base(crash_from) * (1 - 0.0025 * (m - crash_from))
    api.end_min += 40
    adv.raw.clear(); adv.frames.clear()
    await w.refresh()
    coin = w.state["coins"]["PEPEUSDT"]
    old = [r for r in store.watch_history() if r["id"] == sig["id"]]
    assert old and old[0]["final_status"] in ("flip", "stop") and old[0]["result_1h_r"] is not None, old
    assert coin["side"] == "short" and coin["futures_label"] == "Short" and coin["spot_label"] == "Sell", coin
    new = store.watch_active_signal("PEPEUSDT")
    assert new and new["side"] == "short" and new["id"] != sig["id"], new
    print("ok   flip: old signal", old[0]["final_status"], round(old[0]["result_r"], 2), "R; new", new["side"], coin["strength"])

    # 4) the new signal runs past 1 h: 1 h result recorded and the signal is followed on
    for _ in range(3):
        base2 = api.path
        end = api.end_min
        api.path = lambda m, b=base2, e=end: b(m) if m < e else b(e) + 0.05 * (m - e)     # drifts back up slowly
        api.end_min += 30
        adv.raw.clear(); adv.frames.clear()
        await w.refresh()
    rows = {r["id"]: r for r in store.watch_all_signals()}
    assert rows[new["id"]]["result_1h_r"] is not None, rows[new["id"]]
    print("ok   1h result recorded:", rows[new["id"]]["status"], rows[new["id"]]["result_1h_r"])

    # 5) statistics and removal
    p = watch.performance(store.watch_all_signals())
    assert p["official_1h"]["n"] >= 2 and p["mirror_1h"]["n"] >= 2 and not p["official_1h"]["enough"]
    store.watch_remove("PEPEUSDT", int(time.time() * 1000))
    assert store.watch_coins() == [] and store.watch_active_signal("PEPEUSDT") is None
    p2 = watch.performance(store.watch_all_signals())
    assert p2["official_1h"]["n"] <= p["official_1h"]["n"]
    print("ok   statistics and removal; requests made:", api.calls)


if __name__ == "__main__":
    asyncio.run(main())
