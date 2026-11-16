"""Checks for the track-record review (v1.2.3): snapshot at entry, movement tracking, statistics, findings.
Run: .venv/bin/python -m tests.test_review"""
import json
import tempfile
import time
from pathlib import Path

import pandas as pd

from app import config as C

C.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"      # never touch data/advisor.db
from app import review, store                          # noqa: E402
from app.service import Advisor                        # noqa: E402

NOW = int(time.time() * 1000)


def call(i, result_r, outcome, **kw):
    c = {"id": i, "created_at": NOW - 5 * 3600_000, "closed_at": NOW - 4 * 3600_000 + i * 60_000, "symbol": "ABCUSDT",
         "market": "spot", "side": "long", "call": "Buy", "entry": 100.0, "stop": 98.0, "confidence": 75,
         "result_r": result_r, "outcome": outcome, "regime": "Bull", "setup_kind": "pullback",
         "rules_version": "1.2.3", "checks": json.dumps({"volume": True, "timing": True}), "mfe_r": 0.2, "mae_r": 0.5}
    c.update(kw)
    return c


def test_cost_and_net():
    c = call(1, 1.0, "Target 2 reached")
    # spot cost 0.30% of entry, stop distance 2% -> 0.15R
    assert abs(review.cost_r(c) - 0.15) < 1e-9
    assert abs(review.enrich([c])[0]["net_r"] - 0.85) < 1e-9


def test_explain():
    assert "Wrong from the start" in review.explain(call(1, -1.0, "Stop-loss hit", mfe_r=0.1, checks=json.dumps({"volume": False})))
    assert "volume" in review.explain(call(1, -1.0, "Stop-loss hit", mfe_r=0.1, checks=json.dumps({"volume": False})))
    assert "Reversal" in review.explain(call(1, -1.0, "Stop-loss hit", mfe_r=1.3))
    assert "No excursion data" in review.explain(call(1, -1.0, "Stop-loss hit", mfe_r=None, checks=None))
    assert "Full win" in review.explain(call(1, 2.25, "Target 2 reached"))


def test_analyze_findings():
    rows = []
    for i in range(12):                      # 12 reversal losers
        rows.append(call(i, -1.0, "Stop-loss hit", mfe_r=1.2, confidence=65, regime="Bear"))
    for i in range(12, 24):                  # 12 clean winners
        rows.append(call(i, 2.0, "Target 2 reached", confidence=85, regime="Bull"))
    rv = review.analyze(rows)
    assert rv["n"] == 24 and rv["overall"]["win_rate"] == 50
    assert rv["overall"]["longest_losing_streak"] == 12
    texts = " ".join(f["text"] for f in rv["findings"])
    assert "Only 24 closed calls" in texts                        # below REVIEW_MIN_ACT
    assert "first went at least 1R" in texts                      # reversal share 100%
    assert "BTC regime at entry" in texts                         # Bear vs Bull differ clearly
    conf = {g["key"]: g for g in rv["groups"]["Confidence at entry"]}
    assert conf["80+"]["avg_r"] > conf["60-69"]["avg_r"]
    assert review.analyze([])["n"] == 0


def test_snapshot_and_movement():
    res = {"side": "long", "call": "Buy", "entry": 100.0, "stop": 98.0, "t1": 103.0, "t2": 106.0, "confidence": 70,
           "reason": "x", "setup_kind": "pullback",
           "checks": [{"name": "trend", "passed": True, "applies": True}, {"name": "volume", "passed": False, "applies": True},
                      {"name": "positioning", "passed": False, "applies": False}]}
    cid = store.open_call("TESTUSDT", "spot", res, "Bull")
    row = store.active_calls()[0]
    assert json.loads(row["checks"]) == {"trend": True, "volume": False} and row["regime"] == "Bull"
    assert row["setup_kind"] == "pullback" and row["mfe_r"] == 0 and row["mae_r"] == 0

    t0 = row["created_at"] + 60_000
    def candles(rows):
        return pd.DataFrame([{"open_time": t0 + i * 900_000, "close_time": t0 + (i + 1) * 900_000 - 1, "high": h, "low": l,
                              "close": cl} for i, (h, l, cl) in enumerate(rows)])
    adv = Advisor()
    # candle 1: up 1.5R (to 103? no: 1R = 2, high 102.5 -> 1.25R) and down 0.5R; candle 2: stop (98) hit
    adv.frames[("spot", "TESTUSDT", "15m")] = candles([(102.5, 99.0, 100.5), (100.5, 97.5, 98.0)])
    h = pd.DataFrame([{"open_time": row["created_at"], "close": 101.0, "ema20": 100.0}])
    adv.frames[("spot", "TESTUSDT", "1h")] = h
    out = adv._track_active({})
    done = store.closed_calls()[0]
    assert done["outcome"] == "Stop-loss hit" and done["result_r"] == -1.0
    assert abs(done["mfe_r"] - 1.25) < 1e-9 and abs(done["mae_r"] - 1.25) < 1e-9, (done["mfe_r"], done["mae_r"])
    assert "Reversal" in review.explain(done)
    assert out and "Exit" in out[0]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
