"""Checks for the hour-of-day profile (v1.2.4) with synthetic candles.
Run: .venv/bin/python -m tests.test_hourly"""
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd

from app import config as C

C.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"      # never touch data/advisor.db
from app import hourly, store                          # noqa: E402


def make(days=200, seed=1, drift_hour=None):
    """Volume peaks at 21:00 and 15:00 Vietnam time; optionally one hour with a strong upward drift."""
    rng = np.random.default_rng(seed)
    start = (1_700_000_000_000 // 86_400_000) * 86_400_000 - 7 * 3_600_000      # a VN midnight
    rows = []
    for i in range(days * 24):
        t = start + i * 3_600_000
        h = (i % 24)
        vol = 1000.0 * (4.0 if h == 21 else 2.5 if h == 15 else 1.0) * rng.uniform(0.9, 1.1)
        ret = rng.normal(0, 0.5) + (0.15 if h == drift_hour else 0.0)
        o = 100.0
        c = o * (1 + ret / 100)
        rows.append([t, o, c, vol, vol * (0.56 if h == 21 else 0.5), t + 3_599_999])
    return pd.DataFrame(rows, columns=["open_time", "open", "close", "quote_volume", "taker_buy_quote", "close_time"])


def test_hours_are_vietnam_time():
    # 17:00 UTC is 00:00 the next day in Vietnam
    t = 1_700_000_000_000 // 3_600_000 * 3_600_000
    utc_h = (t // 3_600_000) % 24
    assert int(hourly.vn_hour([t])[0]) == (utc_h + 7) % 24


def test_volume_profile():
    p = hourly.compute(make())
    assert p["top"][0] == 21 and p["top"][1] == 15, p["top"]
    assert p["days"] == 200 and abs(sum(r["share"] for r in p["hours"]) - 100) < 1e-6
    assert p["consistency"] > 0.9
    assert abs(p["hours"][21]["buy_share"] - 56) < 0.5
    assert p["hours"][21]["sessions"] == ["Europe", "US"] and "Asia" in p["hours"][8]["sessions"]


def test_noise_is_not_called_notable_but_a_real_drift_is():
    quiet = hourly.compute(make(seed=2))
    assert quiet["notable_up"] == [] and quiet["notable_down"] == []
    assert any("No hour shows a reliable tendency" in e["text"] for e in hourly.explain(quiet))
    drift = hourly.compute(make(days=300, seed=3, drift_hour=10))
    assert 10 in drift["notable_up"], drift["notable_up"]
    assert any("rose on average" in e["text"] for e in hourly.explain(drift))


def test_too_short_and_incomplete_days():
    assert hourly.compute(make(days=10)) is None
    df = make(days=100)
    df = df[~((df["open_time"] // 3_600_000 % 24) == 3)]               # an hour missing every day -> no complete day
    assert hourly.compute(df) is None


def test_explain_and_summary_and_store():
    p = hourly.compute(make())
    texts = " ".join(e["text"] for e in hourly.explain(p))
    assert "21:00–22:00" in texts and "US stock market opens at 21:30" in texts and "summer and winter" in texts
    s = hourly.summary("ABCUSDT", p, 1)
    assert s["base"] == "ABC" and s["top"][0]["hour"] == 21
    store.hourly_save("ABCUSDT", p, int(time.time() * 1000))
    assert store.hourly_get("ABCUSDT")["profile"]["top"][0] == 21
    assert store.hourly_age_ms("ABCUSDT", int(time.time() * 1000) + 5000) >= 5000
    assert store.hourly_age_ms("NOPEUSDT", 1) is None


def test_background_job_and_api():
    import asyncio
    from fastapi.testclient import TestClient
    from app import main
    from app.binance import BinanceError

    class FakeApi:
        used_weight: dict = {}
        calls = []

        async def history_1h(self, symbol, days):
            self.calls.append(symbol)
            if symbol == "BOOMUSDT":
                raise BinanceError("HTTP 400 for /api/v3/klines: Invalid symbol")
            return make(days=120, seed=len(symbol)) if symbol != "NEWUSDT" else make(days=10)

    adv = main.advisor
    adv.api = FakeApi()
    asyncio.run(adv.run_hourly(["BTCUSDT", "ETHUSDT", "NEWUSDT", "BOOMUSDT"]))
    assert adv.state["hourly"]["running"] is False and "Invalid symbol" in adv.state["hourly"]["error"]
    assert sorted(FakeApi.calls) == ["BOOMUSDT", "BTCUSDT", "ETHUSDT", "NEWUSDT"]
    asyncio.run(adv.run_hourly(["BTCUSDT", "ETHUSDT"]))                # fresh profiles are not downloaded again
    assert FakeApi.calls.count("BTCUSDT") == 1

    c = TestClient(main.app)
    r = c.get("/api/hourly").json()
    symbols = [x["symbol"] for x in r["coins"]]
    assert symbols[0] == "BTCUSDT" and "ETHUSDT" in symbols                      # BTC first
    assert "NEWUSDT" not in symbols and "BOOMUSDT" not in symbols                # too short / failed: left out
    assert r["coins"][0]["top"][0]["hour"] == 21
    d = c.get("/api/hourly/btcusdt").json()
    assert len(d["profile"]["hours"]) == 24 and d["reading"]
    assert c.get("/api/hourly/NEWUSDT").status_code == 404
    assert c.post("/api/hourly/BAD$").status_code == 400


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
