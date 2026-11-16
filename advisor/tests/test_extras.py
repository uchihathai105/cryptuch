"""Checks for the screener project checklist, buy-zone alerts and journal statistics (v1.2.2).
Run: .venv/bin/python -m tests.test_extras"""
import tempfile
import time
from pathlib import Path

from app import config as C

C.DB_PATH = Path(tempfile.mkdtemp()) / "test.db"      # never touch data/advisor.db
from app import fundamentals, journal, screener, store  # noqa: E402
from app.service import zone_alert_text                # noqa: E402


def row(**kw):
    base = {"id": "x", "symbol": "abc", "name": "Abc", "market_cap": 100e6, "fully_diluted_valuation": 120e6,
            "circulating_supply": 800, "total_supply": 1000, "max_supply": 1000}
    base.update(kw)
    return base


def test_fundamentals():
    ok = fundamentals.assess(row())
    assert ok["flag"] == "ok", ok
    warn = fundamentals.assess(row(fully_diluted_valuation=250e6))
    assert warn["flag"] == "warn", warn
    bad = fundamentals.assess(row(fully_diluted_valuation=500e6, circulating_supply=200))
    assert bad["flag"] == "bad", bad
    assert fundamentals.assess({"symbol": "zzz"})["flag"] == "unknown"
    picked = fundamentals.pick_best([row(market_cap=5), row(id="big", market_cap=900e6)])
    assert picked["ABC"]["links"]["coingecko"].endswith("/big")


def test_verdict_downgrade():
    r = {"verdict": "Buy", "verdict_reason": "Recovering."}
    screener.apply_fundamentals(r, fundamentals.assess(row(fully_diluted_valuation=500e6, circulating_supply=200)))
    assert r["verdict"] == "Wait" and "dilution" in r["verdict_reason"].lower()
    r = {"verdict": "Buy", "verdict_reason": "Recovering."}
    screener.apply_fundamentals(r, None)
    assert r["verdict"] == "Buy" and "unlock" in r["verdict_reason"]
    r = {"verdict": "Wait", "verdict_reason": "x"}
    screener.apply_fundamentals(r, fundamentals.assess(row()))
    assert r["verdict"] == "Wait" and r["fundamentals"]["flag"] == "ok"


def test_zone_alert():
    r = {"base": "ABC", "zone": [1.0, 1.2], "invalidation": 0.8, "verdict": "Buy", "score": 80}
    assert zone_alert_text(r, 1.1) and "ABC" in zone_alert_text(r, 1.1)
    assert zone_alert_text(r, 1.3) is None          # still above the zone
    assert zone_alert_text(r, 0.7) is None          # below the level that invalidates the idea
    now = int(time.time() * 1000)
    assert not store.zone_alert_recent("ABCUSDT", now)
    store.zone_alert_mark("ABCUSDT", now)
    assert store.zone_alert_recent("ABCUSDT", now + 3600_000)
    assert not store.zone_alert_recent("ABCUSDT", now + (C.ZONE_ALERT_COOLDOWN_DAYS + 1) * 86_400_000)


def test_journal():
    store.journal_add({"trade_date": "2026-11-01", "symbol": "ethusdt", "market": "spot", "side": "long", "entry": 100,
                       "exit": 110, "qty": 2, "stop": 95, "fees": 1, "followed": True, "tags": ["Followed my plan"]})
    store.journal_add({"trade_date": "2026-11-02", "symbol": "SOLUSDT", "market": "spot", "side": "long", "entry": 50,
                       "exit": 45, "qty": 4, "stop": 48, "fees": 0.5, "followed": False,
                       "tags": ["Chased the price", "No stop-loss", "not-a-real-tag"]})
    store.journal_add({"trade_date": "2026-11-03", "symbol": "LINKUSDT", "market": "futures", "side": "short",
                       "entry": 20, "exit": None, "qty": 1})
    rows = store.journal_list()
    st = journal.stats(rows)
    assert st["closed"] == 2 and st["open"] == 1
    assert abs(st["total"] - ((20 - 1) + (-20 - 0.5))) < 1e-9          # +19 and -20.5
    by = {j["symbol"]: j for j in rows}
    assert abs(journal.result_r(by["ETHUSDT"]) - 19 / 10) < 1e-9       # risk = 5 * 2
    assert "not-a-real-tag" not in by["SOLUSDT"]["tags"]
    assert st["with_mistake"]["n"] == 1 and st["with_mistake"]["total"] < 0
    assert st["by_tag"]["Chased the price"]["n"] == 1
    assert st["followed"]["n"] == 1 and st["not_followed"]["n"] == 1
    short = {"side": "short", "entry": 100, "exit": 90, "qty": 1}
    assert journal.pnl(short) == 10


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
