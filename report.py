#!/usr/bin/env python3
"""Crypto market report for BTC, BNB and ETH.

Fetches market data from Binance's public market-data API, computes simple
trend and momentum indicators, and prints a Markdown report with a
rule-based BUY / SELL / HOLD signal per coin.

Uses only the Python standard library.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "BNB": "BNBUSDT"}

# api.binance.com refuses requests from US IPs (where GitHub runners live);
# data-api.binance.vision serves the same public market data without that block.
BASE_URLS = [
    "https://data-api.binance.vision/api/v3",
    "https://api.binance.com/api/v3",
]


def fetch_json(path):
    last_error = None
    for base in BASE_URLS:
        try:
            req = urllib.request.Request(base + path, headers={"User-Agent": "cryptuch-report"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.load(resp)
        except Exception as e:  # try the next mirror
            last_error = e
    raise RuntimeError(f"all endpoints failed for {path}: {last_error}")


def sma(values, n):
    return sum(values[-n:]) / n


def rsi(closes, n=14):
    """Wilder's RSI."""
    gains, losses = [], []
    for prev, cur in zip(closes, closes[1:]):
        d = cur - prev
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:n]) / n
    avg_loss = sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        avg_gain = (avg_gain * (n - 1) + g) / n
        avg_loss = (avg_loss * (n - 1) + l) / n
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def analyze(ticker, klines):
    """Compute indicators and a signal from a 24h ticker and 1h klines (oldest first)."""
    closes = [float(k[4]) for k in klines]
    quote_vols = [float(k[7]) for k in klines]
    price = float(ticker["lastPrice"])

    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    rsi14 = rsi(closes)
    change_24h = float(ticker["priceChangePercent"])
    change_7d = (price / closes[-168] - 1) * 100 if len(closes) >= 168 else None
    vol_24h = float(ticker["quoteVolume"])
    # Average 24h volume over the previous 7 days of hourly candles.
    hist = quote_vols[-192:-24] if len(quote_vols) >= 192 else quote_vols[:-24]
    avg_vol_24h = sum(hist) / len(hist) * 24 if hist else vol_24h
    vol_ratio = vol_24h / avg_vol_24h if avg_vol_24h else 1.0

    score, reasons = 0, []
    if price > sma50 and sma20 > sma50:
        trend = "Uptrend"
        score += 1
        reasons.append("price and SMA20 above SMA50 (uptrend)")
    elif price < sma50 and sma20 < sma50:
        trend = "Downtrend"
        score -= 1
        reasons.append("price and SMA20 below SMA50 (downtrend)")
    else:
        trend = "Sideways"
        reasons.append("moving averages mixed (no clear trend)")

    if rsi14 < 30:
        score += 1
        reasons.append(f"RSI {rsi14:.0f} is oversold")
    elif rsi14 > 70:
        score -= 1
        reasons.append(f"RSI {rsi14:.0f} is overbought")

    if vol_ratio > 1.3 and abs(change_24h) >= 2:
        score += 1 if change_24h > 0 else -1
        direction = "rally" if change_24h > 0 else "sell-off"
        reasons.append(f"{direction} on {vol_ratio:.1f}x normal volume")

    signal = "BUY" if score >= 2 else "SELL" if score <= -2 else "HOLD"
    return {
        "price": price,
        "change_24h": change_24h,
        "change_7d": change_7d,
        "high_24h": float(ticker["highPrice"]),
        "low_24h": float(ticker["lowPrice"]),
        "vol_24h": vol_24h,
        "vol_ratio": vol_ratio,
        "sma20": sma20,
        "sma50": sma50,
        "rsi": rsi14,
        "trend": trend,
        "score": score,
        "signal": signal,
        "reasons": reasons,
    }


def fmt_price(p):
    return f"${p:,.2f}"


def fmt_pct(p):
    return "n/a" if p is None else f"{p:+.2f}%"


def fmt_vol(v):
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if v >= size:
            return f"${v / size:,.2f}{unit}"
    return f"${v:,.0f}"


def render(results, now):
    icon = {"BUY": "🟢 BUY", "SELL": "🔴 SELL", "HOLD": "🟡 HOLD"}
    lines = [
        "# Crypto Market Report",
        "",
        f"_Updated {now:%Y-%m-%d %H:%M} UTC · data: Binance spot (USDT pairs), 1h candles_",
        "",
        "| Coin | Price | 24h | 7d | 24h Volume | Vol vs 7d avg | RSI(14) | Trend | Signal |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for coin, r in results.items():
        if "error" in r:
            lines.append(f"| {coin} | error: {r['error']} | | | | | | | |")
            continue
        lines.append(
            f"| **{coin}** | {fmt_price(r['price'])} | {fmt_pct(r['change_24h'])} | {fmt_pct(r['change_7d'])} "
            f"| {fmt_vol(r['vol_24h'])} | {r['vol_ratio']:.2f}x | {r['rsi']:.0f} | {r['trend']} | {icon[r['signal']]} |"
        )
    lines += ["", "## Details", ""]
    for coin, r in results.items():
        if "error" in r:
            continue
        lines += [
            f"### {coin} — {icon[r['signal']]} (score {r['score']:+d})",
            f"- 24h range: {fmt_price(r['low_24h'])} – {fmt_price(r['high_24h'])}",
            f"- SMA20 {fmt_price(r['sma20'])} · SMA50 {fmt_price(r['sma50'])} (hourly)",
            *[f"- {reason}" for reason in r["reasons"]],
            "",
        ]
    lines += [
        "## How the signal works",
        "Each coin scores +1/−1 for trend (price and SMA20 vs SMA50), RSI oversold (<30) / "
        "overbought (>70), and a strong 24h move (≥2%) on above-normal volume (>1.3x). "
        "Score ≥ +2 → BUY, ≤ −2 → SELL, otherwise HOLD.",
        "",
        "> ⚠️ This is an automated technical-indicator summary, not financial advice. "
        "Crypto is highly volatile; indicators lag and are often wrong. "
        "Only risk money you can afford to lose.",
        "",
    ]
    return "\n".join(lines)


def main():
    results = {}
    for coin, symbol in SYMBOLS.items():
        try:
            ticker = fetch_json(f"/ticker/24hr?symbol={symbol}")
            klines = fetch_json(f"/klines?symbol={symbol}&interval=1h&limit=200")
            results[coin] = analyze(ticker, klines)
        except Exception as e:
            results[coin] = {"error": str(e)}

    report = render(results, datetime.now(timezone.utc))
    out = os.environ.get("REPORT_PATH", "REPORT.md")
    with open(out, "w") as f:
        f.write(report)
    print(report)
    return 1 if all("error" in r for r in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
