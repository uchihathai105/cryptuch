"""Checks for the Watch rules using made-up candles. Run: .venv/bin/python -m tests.test_watch"""
import numpy as np
import pandas as pd

from app import config as C
from app import watch
from app.indicators import add_indicators

MIN = 60_000
T0 = 1_800_000_000_000  # arbitrary start, aligned to a 5m boundary


def candles(prices, step=MIN, start=T0, spread=0.0005, vol=100.0):
    rows = []
    for i, p in enumerate(prices):
        o = prices[i - 1] if i else p
        rows.append([start + i * step, o, max(o, p) * (1 + spread), min(o, p) * (1 - spread), p, vol,
                     start + (i + 1) * step - 1])
    return pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume", "close_time"])


def choppy(n, seed=3):
    """Sideways market: mean-reverting noise around 100 (no lasting trend, low ADX)."""
    g = np.random.default_rng(seed)
    x, out = 0.0, []
    for i in range(n):
        x += -0.15 * x + g.normal(0, 0.7)
        out.append(100 + x)
    return np.array(out)


def one_min(rows, start=T0):
    """rows: list of (high, low, close) per minute."""
    out = []
    for i, (h, l, c) in enumerate(rows):
        out.append([start + i * MIN, c, h, l, c, 1.0, start + (i + 1) * MIN - 1])
    return pd.DataFrame(out, columns=["open_time", "open", "high", "low", "close", "volume", "close_time"])


def sig(side="long", entry=100.0, atr=1.0, created=T0 - 1):
    return watch.new_signal(side, entry, atr, "futures", created)


def test_levels_and_cost():
    s = sig("long")
    assert (s["stop"], s["t1"], s["t2"]) == (99.0, 101.0, 102.0), s
    assert abs(s["cost_r"] - 0.2 / 1.0) < 1e-6      # 0.20% of 100 = 0.2 = 0.2R
    s = sig("short")
    assert (s["stop"], s["t1"], s["t2"]) == (101.0, 99.0, 98.0)
    # tiny ATR: the minimum stop distance (0.3%) applies
    s = watch.new_signal("long", 100.0, 0.01, "spot", T0)
    assert abs(s["entry"] - s["stop"] - 0.3) < 1e-9


def test_target_path():
    s = sig("long")
    k = one_min([(100.5, 99.8, 100.4), (101.2, 100.3, 101.1), (102.3, 101.0, 102.2)])
    watch.advance(s, k, T0 + 10 * MIN)
    assert s["status"] == "closed" and s["final_status"] == "t2", s
    assert abs(s["result_r"] - (0.5 * 1 + 0.5 * 2 - s["cost_r"])) < 1e-6
    assert s["result_1h_r"] == s["result_r"]


def test_stop_and_same_candle_conflict():
    s = sig("long")
    watch.advance(s, one_min([(101.5, 98.5, 100)]), T0 + MIN)     # touches T1 and stop in one candle
    assert s["final_status"] == "stop" and abs(s["result_r"] - (-1 - s["cost_r"])) < 1e-6, s


def test_breakeven_after_t1():
    s = sig("short")
    k = one_min([(100.2, 98.9, 99.0), (100.1, 98.8, 99.9)])       # T1 (99), then back to entry
    watch.advance(s, k, T0 + 5 * MIN)
    assert s["final_status"] == "be_stop" and abs(s["result_r"] - (0.5 - s["cost_r"])) < 1e-6, s


def test_expiry_and_tracking():
    s = sig("long")
    flat = [(100.4, 99.6, 100.2)] * 59                              # first hour, nothing hit
    hour2 = [(100.4, 99.6, 100.3)] * 30
    k = one_min(flat + [(100.4, 99.6, 100.2)] + hour2)
    watch.advance(s, k, T0 + 90 * MIN)
    assert s["status"] == "tracking" and s["expired_at"] == T0 - 1 + 60 * MIN, s
    assert abs(s["result_1h_r"] - (0.2 - s["cost_r"])) < 1e-6, s["result_1h_r"]
    # stop and targets never move; T1 in the second hour is a "late win"
    k2 = one_min(flat + [(100.4, 99.6, 100.2)] + hour2 + [(101.3, 100.2, 101.2)])
    watch.advance(s, k2, T0 + 91 * MIN)
    assert s["t1_hit"] == 1 and s["late_win"] == 1 and s["stop"] == 99.0, s
    k3 = one_min(flat + [(100.4, 99.6, 100.2)] + hour2 + [(101.3, 100.2, 101.2)] + [(101.4, 100.9, 101.0)] * 40)
    watch.advance(s, k3, T0 + 140 * MIN)
    assert s["final_status"] == "expired" and s["result_1h_r"] < 0.2, s


def test_flip_closes_and_mirror():
    s = sig("long")
    m = watch.mirror_of(s)
    k = one_min([(100.3, 99.7, 100.1)] * 5)
    for st in (s, m):
        watch.advance(st, k, T0 + 5 * MIN, flip_px=100.4)
    assert s["final_status"] == "flip" and m["final_status"] == "flip"
    assert s["result_r"] > 0 > m["result_r"] - 0.01 or abs(s["result_r"] + m["result_r"]) < 0.5
    assert m["side"] == "short" and m["stop"] == 101.0 and m["t1"] == 99.0


def test_hysteresis():
    assert watch.decide_side(5, None, 99) == ("long", True)
    assert watch.decide_side(-3, None, 99) == ("short", True)
    assert watch.decide_side(-5, "long", 99) == ("long", False)      # inside the dead band
    assert watch.decide_side(-15, "long", 1) == ("long", False)      # too soon after the last flip
    assert watch.decide_side(-15, "long", 5) == ("short", True)
    assert watch.decide_side(15, "short", 5) == ("long", True)


def test_score_direction_and_state():
    n = 300
    up = np.linspace(100, 130, n) * (1 + 0.002 * np.sin(np.arange(n)))
    a = watch.analyze(add_indicators(candles(up, 5 * MIN)), None)
    assert a and a["score"] > 25 and a["state"] == "trend", a
    down = up[::-1]
    b = watch.analyze(add_indicators(candles(down, 5 * MIN)), None)
    assert b and b["score"] < -25, b
    r = watch.analyze(add_indicators(candles(choppy(n), 5 * MIN)), None)
    assert r and r["state"] == "range" and r["swings_24h"] >= 2, r
    assert abs(sum(r["contrib"].values()) - r["score"]) < 0.5
    assert watch.analyze(add_indicators(candles(up[:50], 5 * MIN)), None) is None   # not enough history


def test_range_regime_is_contrarian():
    n = 300
    rng = choppy(n)
    seen = 0
    for cut in range(150, 300):
        a = watch.analyze(add_indicators(candles(rng[:cut], 5 * MIN)), None)
        if not a or a["adx"] > C.WATCH_ADX_RANGE_MAX:
            continue
        if a["pos_in_range"] <= 15:
            assert a["contrib"]["position"] > 0, a            # bottom of a ranging market leans Buy
            seen += 1
        if a["pos_in_range"] >= 85:
            assert a["contrib"]["position"] < 0, a            # top leans Sell
            seen += 1
    assert seen > 0, "test data never reached the edge of a ranging market"


def test_swing_count():
    p = np.array([100, 102, 100.2, 102.5, 100.4, 103, 100.5], dtype=float)
    assert watch.swing_count(p, 1.5) == 5, watch.swing_count(p, 1.5)   # 5 turns after the first leg
    assert watch.swing_count(np.linspace(100, 110, 50), 1.5) == 0


def test_performance():
    rows = []
    for i in range(40):
        win = i % 2 == 0
        r = 1.0 if win else -1.0
        rows.append({"side": "long", "strength": "Strong", "state": "trend", "symbol": "PEPEUSDT", "created_at": T0 + i * MIN,
                     "rules_version": "1.2", "status": "closed", "result_1h_r": r, "result_r": r, "late_win": 0,
                     "expired_at": None, "final_status": "t2", "mirror": {"result_1h_r": -r}})
    p = watch.performance(rows)
    assert p["official_1h"]["n"] == 40 and p["official_1h"]["enough"] and p["official_1h"]["win_rate"] == 50.0
    assert p["mirror_1h"]["avg_r"] == 0 and p["official_1h"]["profit_factor"] == 1.0
    assert watch.metrics([1.0] * 5)["enough"] is False


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok  ", name)
