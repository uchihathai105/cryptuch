"""Web server + 15-minute scheduler. Start with ./run.sh, then open http://localhost:8000"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config as C
from . import auth, engine, hourly, journal as journal_stats, review, spot, store, telegram, watch
from .service import Advisor, clean, next_refresh_at
from .spotter import Spotter
from .watcher import Watcher, normalise

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # no line per Binance request
log = logging.getLogger("main")
WEB = Path(__file__).resolve().parent.parent / "web"

advisor = Advisor()
watcher = Watcher(advisor)
spotter = Spotter(advisor)


async def scheduler():
    """Refresh on start, then ~5 seconds after every 15-minute candle close (URS F04)."""
    await advisor.refresh()
    while True:
        wait = max(1.0, next_refresh_at() - time.time())
        advisor.state["next_refresh"] = time.time() + wait
        await asyncio.sleep(wait)
        await advisor.refresh()


async def watch_scheduler():
    """Watch tab: analyse on start, then ~5 seconds after every 5-minute candle close."""
    await watcher.refresh()
    while True:
        wait = max(1.0, next_refresh_at(minutes=C.WATCH_REFRESH_MINUTES) - time.time())
        watcher.state["next_refresh"] = time.time() + wait
        await asyncio.sleep(wait)
        await watcher.refresh()


async def spot_scheduler():
    """Spot tab: analyse on start, then ~5 seconds after every 5-minute candle close."""
    await spotter.refresh()
    while True:
        wait = max(1.0, next_refresh_at(minutes=C.SPOT_REFRESH_MINUTES) - time.time())
        spotter.state["next_refresh"] = time.time() + wait
        await asyncio.sleep(wait)
        await spotter.refresh()


@contextlib.asynccontextmanager
async def lifespan(_app):
    tasks = [asyncio.create_task(scheduler()), asyncio.create_task(watch_scheduler()),
             asyncio.create_task(spot_scheduler())]
    yield
    for t in tasks:
        t.cancel()
    await advisor.api.close()


app = FastAPI(title="Crypto Trading Advisor", lifespan=lifespan)
app.middleware("http")(auth.guard)   # no-op unless a password is set (see app/auth.py)
app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/login")
def login_page():
    return auth.login_page()


@app.post("/login")
async def login(request: Request):
    return await auth.login(request)


@app.post("/logout")
def logout():
    return auth.logout()


@app.get("/")
def index():
    """Serve the page with the files' modification time added to the script and style links, so a browser never
    keeps an old app.js after an update (an old script made new tabs show but not work)."""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "style.css"):
        html = html.replace(f"/static/{name}", f"/static/{name}?v={int((WEB / name).stat().st_mtime)}")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


def _with_sizing(coins: dict, settings: dict) -> dict:
    for coin in coins.values():
        for market in ("spot", "futures"):
            res = coin.get(market)
            if res and res.get("entry") and res.get("stop") and res.get("call") in ("Long", "Short", "Buy"):
                res["sizing"] = clean(engine.position_size(res["entry"], res["stop"], res["side"], market, settings))
    return coins


@app.get("/api/state")
def state():
    s = advisor.state
    settings = store.get_settings()
    import copy
    coins = _with_sizing(copy.deepcopy(s["coins"]), settings)
    return JSONResponse(clean({
        "now": time.time(), "last_refresh": s["last_refresh"], "last_error": s["last_error"],
        "refreshing": s["refreshing"], "next_refresh": s["next_refresh"], "regime": s["regime"],
        "lists": s["lists"], "coins": coins, "active_calls": store.active_calls(),
        "track_record": store.track_record(), "settings": settings,
        "telegram": telegram.configured(), "rules_version": C.RULES_VERSION,
        "used_weight": s.get("used_weight", {}),
        "screener": s["screener"], "auth": auth.enabled(),
    }))


@app.get("/api/candles")
async def candles(symbol: str, market: str = "spot", interval: str = "1h"):
    if interval not in C.CANDLE_LIMITS or market not in ("spot", "futures"):
        raise HTTPException(400, "Unknown market or interval")
    try:
        return await advisor.chart(symbol.upper(), market, interval)
    except Exception as e:
        raise HTTPException(502, f"Could not load candles: {e}")


@app.post("/api/settings")
async def save_settings(body: dict):
    clean_body = {}
    if "account_size" in body:
        clean_body["account_size"] = max(0.0, float(body["account_size"]))
    if "risk_pct" in body:
        clean_body["risk_pct"] = min(10.0, max(0.05, float(body["risk_pct"])))
    if "max_leverage" in body:
        clean_body["max_leverage"] = int(min(20, max(1, int(body["max_leverage"]))))
    if "max_position_pct" in body:
        clean_body["max_position_pct"] = min(100.0, max(1.0, float(body["max_position_pct"])))
    if "watchlist" in body:
        wl = []
        for sym in body["watchlist"]:
            sym = str(sym).upper().strip().replace("/", "")
            if sym and not sym.endswith("USDT"):
                sym += "USDT"
            if sym and sym not in wl:
                wl.append(sym)
        clean_body["watchlist"] = wl[:30]
    settings = store.save_settings(clean_body)
    if "watchlist" in clean_body:
        asyncio.create_task(advisor.refresh())
    return settings


@app.post("/api/refresh")
async def refresh_now():
    asyncio.create_task(advisor.refresh())
    return {"ok": True}


@app.get("/api/calls/history")
def call_history():
    rows = store.closed_calls()
    for c in rows:
        c["why"] = review.explain(c)
        c["net_r"] = c["result_r"] - review.cost_r(c) if c.get("result_r") is not None and c.get("entry") else None
    return clean(rows)


@app.get("/api/hourly")
def hourly_list():
    rows = [hourly.summary(r["symbol"], r["profile"], r["computed_at"]) for r in store.hourly_all()
            if not r["profile"].get("too_short")]
    rows.sort(key=lambda r: (r["symbol"] != "BTCUSDT", r["symbol"]))
    return clean({"coins": rows, "job": advisor.state["hourly"]})


@app.get("/api/hourly/{symbol}")
def hourly_detail(symbol: str):
    r = store.hourly_get(symbol.upper())
    if not r or r["profile"].get("too_short"):
        raise HTTPException(404, "No hour-of-day profile for this coin yet")
    p = r["profile"]
    return clean({"symbol": r["symbol"], "computed_at": r["computed_at"], "profile": p, "reading": hourly.explain(p)})


@app.post("/api/hourly/{symbol}")
async def hourly_request(symbol: str):
    """Build the profile of a coin that is not in the automatic list (any Binance USDT pair)."""
    sym = symbol.upper().strip()
    if not sym.endswith("USDT") or not sym[:-4].isalnum() or len(sym) > 16:
        raise HTTPException(400, "Use a Binance USDT pair such as LINKUSDT")
    if advisor.state["hourly"]["running"]:
        raise HTTPException(409, "Profiles are being built right now; try again in a few minutes")
    asyncio.create_task(advisor.run_hourly([sym]))
    return {"ok": True}


@app.get("/api/review")
def call_review():
    """Why closed calls won or lost (after fees and slippage)."""
    return clean(review.analyze(store.closed_calls(10_000)))


# ---------------------------------------------------------------- Watch tab
def _watch_payload() -> dict:
    w = watcher.state
    symbols = store.watch_coins()
    coins = [w["coins"].get(s) or {"symbol": s, "base": s[:-4], "pending": True,
                                   "note": "Waiting for the first analysis…"} for s in symbols]
    rows = store.watch_all_signals()
    for r in rows:
        r["mirror"] = r.get("mirror")
    return clean({
        "now": time.time(), "coins": coins, "last_refresh": w["last_refresh"], "last_error": w["last_error"],
        "refreshing": w["refreshing"], "next_refresh": w["next_refresh"], "banner": w["banner"],
        "max_coins": C.WATCH_MAX_COINS, "performance": watch.performance(rows),
        "settings": {"stop_atr": C.WATCH_STOP_ATR, "t1_r": C.WATCH_T1_R, "t2_r": C.WATCH_T2_R,
                     "expire_min": C.WATCH_EXPIRE_MIN, "max_track_min": C.WATCH_MAX_TRACK_MIN,
                     "flip_threshold": C.WATCH_FLIP_THRESHOLD},
    })


@app.get("/api/watch")
def watch_state():
    return JSONResponse(_watch_payload())


@app.post("/api/watch")
async def watch_add(body: dict):
    try:
        sym = normalise(body.get("symbol", ""))
        coins = store.watch_coins()
        if sym in coins:
            raise ValueError(f"{sym} is already being watched")
        if len(coins) >= C.WATCH_MAX_COINS:
            raise ValueError(f"At most {C.WATCH_MAX_COINS} coins can be watched (keeps Binance request weight low)")
        await watcher.validate(sym)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Could not check {body.get('symbol', '')} on Binance: {e}")
    store.watch_add(sym)
    asyncio.create_task(watcher.refresh())
    return JSONResponse(_watch_payload())


@app.delete("/api/watch/{symbol}")
def watch_remove(symbol: str):
    store.watch_remove(symbol.upper(), int(time.time() * 1000))
    watcher.state["coins"].pop(symbol.upper(), None)
    return JSONResponse(_watch_payload())


@app.get("/api/watch/history")
def watch_history(limit: int = 100):
    return clean(store.watch_history(min(max(limit, 1), 500)))


# ---------------------------------------------------------------- Spot tab
def _spot_payload() -> dict:
    w = spotter.state
    symbols = store.spot_coins()
    coins = [w["coins"].get(s) or {"symbol": s, "base": s[:-4], "pending": True,
                                   "note": "Waiting for the first analysis…"} for s in symbols]
    return clean({"now": time.time(), "coins": coins, "last_refresh": w["last_refresh"], "last_error": w["last_error"],
                  "refreshing": w["refreshing"], "next_refresh": w["next_refresh"], "max_coins": C.SPOT_MAX_COINS,
                  "strategy": spot.STRATEGY, "settings": store.get_settings(),
                  "rules": {"stop_atr": C.SPOT_STOP_ATR, "t1_r": C.SPOT_T1_R, "t2_r": C.SPOT_T2_R,
                            "days": C.SPOT_DAYS, "min_score": C.SPOT_BUY_MIN_SCORE}})


@app.get("/api/spot")
def spot_state():
    return JSONResponse(_spot_payload())


@app.post("/api/spot")
async def spot_add(body: dict):
    try:
        sym = normalise(body.get("symbol", ""))
        coins = store.spot_coins()
        if sym in coins:
            raise ValueError(f"{sym} is already in the list")
        if len(coins) >= C.SPOT_MAX_COINS:
            raise ValueError(f"At most {C.SPOT_MAX_COINS} spot coins (keeps Binance request weight low)")
        await spotter.validate(sym)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Could not check {body.get('symbol', '')} on Binance: {e}")
    store.spot_add(sym)
    asyncio.create_task(spotter.refresh())
    return JSONResponse(_spot_payload())


@app.delete("/api/spot/{symbol}")
def spot_remove(symbol: str):
    store.spot_remove(symbol.upper())
    spotter.state["coins"].pop(symbol.upper(), None)
    return JSONResponse(_spot_payload())


def _journal_payload() -> dict:
    rows = store.journal_list()
    for j in rows:
        j["pnl"], j["r"] = journal_stats.pnl(j), journal_stats.result_r(j)
    return clean({"rows": rows, "stats": journal_stats.stats(rows),
                  "tags": {"mistakes": list(C.JOURNAL_MISTAKE_TAGS), "good": list(C.JOURNAL_GOOD_TAGS)}})


@app.get("/api/journal")
def journal():
    return _journal_payload()


@app.post("/api/journal")
def journal_add(body: dict):
    for k in ("entry", "exit", "qty", "stop", "fees"):
        body[k] = float(body[k]) if body.get(k) not in (None, "") else None
    body["call_id"] = int(body["call_id"]) if body.get("call_id") not in (None, "") else None
    if not isinstance(body.get("tags"), list):
        body["tags"] = []
    store.journal_add(body)
    return _journal_payload()


@app.delete("/api/journal/{entry_id}")
def journal_delete(entry_id: int):
    store.journal_delete(entry_id)
    return _journal_payload()


@app.post("/api/telegram/test")
async def telegram_test():
    ok, msg = await telegram.send("✅ Crypto Trading Advisor is connected. Alerts will arrive here.")
    return {"ok": ok, "message": msg}


def run():
    import uvicorn
    log.info("Open http://%s:%d in your browser", "localhost" if C.HOST == "127.0.0.1" else C.HOST, C.PORT)
    uvicorn.run(app, host=C.HOST, port=C.PORT, log_level="warning")


if __name__ == "__main__":
    run()
