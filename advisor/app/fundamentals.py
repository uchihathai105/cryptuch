"""Project checklist data for the Altcoin screener: market cap, fully diluted value and supply.

Source: CoinGecko public API (no key, one request a day for the listed coins). It cannot see unlock dates,
so the numbers only show how much of the supply is still to come: a coin with a low circulating share or
a high FDV / market cap ratio can be diluted a lot by future unlocks. Treat "unknown" as "check yourself".
"""
from __future__ import annotations

import logging
from urllib.parse import quote_plus

import httpx

from . import config as C

log = logging.getLogger("fundamentals")


async def fetch(bases: list[str]) -> dict[str, dict]:
    """Return {BASE: assessment} for the given coin symbols. Empty dict if CoinGecko cannot be reached."""
    if not C.FUND_ENABLED or not bases:
        return {}
    params = {"vs_currency": "usd", "symbols": ",".join(b.lower() for b in bases), "order": "market_cap_desc",
              "per_page": 250, "page": 1}
    try:
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "crypto-advisor/1.0"}) as client:
            r = await client.get(C.COINGECKO_BASE + "/coins/markets", params=params)
        if r.status_code != 200:
            log.warning("CoinGecko replied HTTP %s", r.status_code)
            return {}
        rows = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("CoinGecko request failed: %s", e)
        return {}
    return pick_best(rows)


def pick_best(rows: list[dict]) -> dict[str, dict]:
    """Several coins can share a ticker; keep the one with the largest market cap."""
    best: dict[str, dict] = {}
    for row in rows or []:
        sym = str(row.get("symbol", "")).upper()
        if not sym:
            continue
        if sym not in best or (row.get("market_cap") or 0) > (best[sym].get("market_cap") or 0):
            best[sym] = row
    return {sym: assess(row) for sym, row in best.items()}


def assess(row: dict) -> dict:
    mc, fdv = row.get("market_cap"), row.get("fully_diluted_valuation")
    circ = row.get("circulating_supply")
    cap = row.get("max_supply") or row.get("total_supply")
    fdv_mc = (fdv / mc) if (mc and fdv) else None
    circ_pct = (circ / cap * 100) if (circ and cap) else None

    if fdv_mc is None and circ_pct is None:
        flag, text = "unknown", "Supply data not available: check the token unlock schedule yourself."
    else:
        bad = (fdv_mc is not None and fdv_mc >= C.FUND_FDV_MC_BAD) or (circ_pct is not None and circ_pct < C.FUND_CIRC_PCT_BAD)
        warn = (fdv_mc is not None and fdv_mc >= C.FUND_FDV_MC_WARN) or (circ_pct is not None and circ_pct < C.FUND_CIRC_PCT_WARN)
        flag = "bad" if bad else "warn" if warn else "ok"
        parts = []
        if circ_pct is not None:
            parts.append(f"{circ_pct:.0f}% of the supply is circulating")
        if fdv_mc is not None:
            parts.append(f"FDV is {fdv_mc:.1f}× the market cap")
        tail = {"ok": "Low dilution risk from supply alone.",
                "warn": "Future unlocks could dilute holders noticeably.",
                "bad": "Heavy dilution risk: a lot of supply is still to be released."}[flag]
        text = "; ".join(parts) + ". " + tail

    cid, name = row.get("id"), row.get("name") or row.get("symbol", "")
    return {
        "flag": flag, "text": text, "market_cap": mc, "fdv": fdv, "fdv_mc": fdv_mc, "circ_pct": circ_pct,
        "name": name,
        "links": {
            "coingecko": f"https://www.coingecko.com/en/coins/{cid}" if cid else None,
            "news": "https://www.google.com/search?q=" + quote_plus(f"{name} token unlock schedule"),
        },
    }


def unknown() -> dict:
    return {"flag": "unknown", "text": "Supply data not available: check the token unlock schedule yourself.",
            "market_cap": None, "fdv": None, "fdv_mc": None, "circ_pct": None, "name": None,
            "links": {"coingecko": None, "news": None}}
