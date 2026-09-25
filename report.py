#!/usr/bin/env python3
"""Crypto market report for BTC, BNB and ETH.

Fetches market data from Binance's public market-data API, computes trend
(MA), volatility (Bollinger Bands) and momentum (MACD, RSI) indicators, and
prints a Markdown report with a rule-based BUY / SELL / HOLD signal and a
written analysis explaining it.

Uses only the Python standard library.
"""

import json
import os
import statistics
import sys
import urllib.request
from datetime import datetime, timezone

SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "BNB": "BNBUSDT"}
KLINE_LIMIT = 300  # hourly candles: enough for MA99, MACD warm-up and 8 days of volume

# api.binance.com refuses requests from US IPs (where GitHub runners live);
# data-api.binance.vision serves the same public market data without that block.
BASE_URLS = [
    "https://data-api.binance.vision/api/v3",
    "https://api.binance.com/api/v3",
]

BUY_THRESHOLD = 3
SELL_THRESHOLD = -3


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


# --- indicators --------------------------------------------------------------


def sma(values, n):
    return sum(values[-n:]) / n


def ema_series(values, n):
    """EMA for every point, seeded with the SMA of the first n values (None before that)."""
    out = [None] * len(values)
    if len(values) < n:
        return out
    k = 2 / (n + 1)
    prev = sum(values[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def macd(closes, fast=12, slow=26, signal=9):
    """Returns (macd_line, signal_line, histogram) series, aligned to the valid part."""
    ema_fast = ema_series(closes, fast)
    ema_slow = ema_series(closes, slow)
    line = [f - s for f, s in zip(ema_fast, ema_slow) if f is not None and s is not None]
    sig = ema_series(line, signal)
    line, sig = line[signal - 1:], sig[signal - 1:]
    hist = [m - s for m, s in zip(line, sig)]
    return line, sig, hist


def bollinger(closes, n=20, k=2):
    window = closes[-n:]
    mid = sum(window) / n
    sd = statistics.pstdev(window)
    return mid - k * sd, mid, mid + k * sd


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


# --- analysis ----------------------------------------------------------------


def analyze(ticker, klines):
    """Compute indicators, a score and a written analysis from a 24h ticker and 1h klines (oldest first)."""
    closes = [float(k[4]) for k in klines]
    quote_vols = [float(k[7]) for k in klines]
    price = float(ticker["lastPrice"])
    change_24h = float(ticker["priceChangePercent"])
    change_7d = (price / closes[-168] - 1) * 100 if len(closes) >= 168 else None

    # Volume: last 24h vs the average 24h over the 7 days before it.
    vol_24h = float(ticker["quoteVolume"])
    hist_vols = quote_vols[-192:-24] if len(quote_vols) >= 192 else quote_vols[:-24]
    avg_vol_24h = sum(hist_vols) / len(hist_vols) * 24 if hist_vols else vol_24h
    vol_ratio = vol_24h / avg_vol_24h if avg_vol_24h else 1.0

    ma7, ma25, ma99 = sma(closes, 7), sma(closes, 25), sma(closes, 99)
    boll_low, boll_mid, boll_up = bollinger(closes)
    pct_b = (price - boll_low) / (boll_up - boll_low) if boll_up > boll_low else 0.5
    bandwidth = (boll_up - boll_low) / boll_mid * 100
    # Squeeze: current bandwidth in the narrowest 20% of the last 100 hours.
    past_bw = []
    for i in range(len(closes) - 100, len(closes)):
        lo, mid, up = bollinger(closes[: i + 1])
        past_bw.append((up - lo) / mid * 100)
    squeeze = sum(bw <= bandwidth for bw in past_bw) <= len(past_bw) * 0.2

    macd_line, macd_sig, macd_hist = macd(closes)
    m, s, h = macd_line[-1], macd_sig[-1], macd_hist[-1]
    # Crossover within the last 3 candles.
    cross = None
    for prev, cur in zip(macd_hist[-4:-1], macd_hist[-3:]):
        if prev <= 0 < cur:
            cross = "bullish"
        elif prev >= 0 > cur:
            cross = "bearish"
    hist_rising = macd_hist[-1] > macd_hist[-2]

    rsi14 = rsi(closes)

    score = 0
    analysis = []  # (factor name, points, sentence)

    # 1. Moving averages: short-term alignment.
    if price > ma7 > ma25:
        pts, text = 1, (f"Price {fmt_price(price)} is above MA7 {fmt_price(ma7)}, which is above MA25 "
                        f"{fmt_price(ma25)}: short-term trend is up.")
    elif price < ma7 < ma25:
        pts, text = -1, (f"Price {fmt_price(price)} is below MA7 {fmt_price(ma7)}, which is below MA25 "
                         f"{fmt_price(ma25)}: short-term trend is down.")
    else:
        pts, text = 0, (f"Price {fmt_price(price)}, MA7 {fmt_price(ma7)} and MA25 {fmt_price(ma25)} are "
                        "not lined up in either direction: no clear short-term trend.")
    analysis.append(("MA (short-term)", pts, text))

    # 2. Moving averages: bigger picture vs MA99 (~4 days).
    if price > ma99 and ma25 > ma99:
        pts, text = 1, f"Price and MA25 are above MA99 {fmt_price(ma99)}: the multi-day trend is up."
    elif price < ma99 and ma25 < ma99:
        pts, text = -1, f"Price and MA25 are below MA99 {fmt_price(ma99)}: the multi-day trend is down."
    else:
        pts, text = 0, f"Price and MA25 sit on opposite sides of MA99 {fmt_price(ma99)}: the multi-day trend is turning or undecided."
    analysis.append(("MA (multi-day)", pts, text))

    # 3. MACD momentum.
    if cross == "bullish":
        pts, text = 1, "MACD just crossed above its signal line: momentum has turned up."
    elif cross == "bearish":
        pts, text = -1, "MACD just crossed below its signal line: momentum has turned down."
    elif h > 0 and hist_rising:
        pts, text = 1, "MACD is above its signal line and the histogram is growing: upward momentum is strengthening."
    elif h < 0 and not hist_rising:
        pts, text = -1, "MACD is below its signal line and the histogram is deepening: downward momentum is strengthening."
    elif h > 0:
        pts, text = 0, "MACD is above its signal line but the histogram is shrinking: upward momentum is fading."
    else:
        pts, text = 0, "MACD is below its signal line but the histogram is shrinking: selling pressure is easing."
    text += f" (MACD {m:+,.2f}, signal {s:+,.2f}, histogram {h:+,.2f})"
    analysis.append(("MACD", pts, text))

    # 4. Bollinger Bands (mean reversion at the extremes).
    band_text = (f"Bands {fmt_price(boll_low)} – {fmt_price(boll_mid)} – {fmt_price(boll_up)}, "
                 f"%B {pct_b:.2f}, width {bandwidth:.2f}%.")
    if pct_b < 0:
        pts, text = 1, "Price has broken below the lower Bollinger Band: stretched to the downside, a bounce is more likely than usual."
    elif pct_b > 1:
        pts, text = -1, "Price has broken above the upper Bollinger Band: stretched to the upside, a pullback is more likely than usual."
    elif pct_b >= 0.5:
        pts, text = 0, "Price is in the upper half of the Bollinger Bands, not at an extreme."
    else:
        pts, text = 0, "Price is in the lower half of the Bollinger Bands, not at an extreme."
    if squeeze:
        text += " The bands are unusually narrow (squeeze): a big move is building, direction not yet known."
    analysis.append(("BOLL", pts, f"{text} {band_text}"))

    # 5. RSI.
    if rsi14 < 30:
        pts, text = 1, f"RSI {rsi14:.0f} is oversold (below 30)."
    elif rsi14 > 70:
        pts, text = -1, f"RSI {rsi14:.0f} is overbought (above 70)."
    else:
        pts, text = 0, f"RSI {rsi14:.0f} is neutral (between 30 and 70)."
    analysis.append(("RSI", pts, text))

    # 6. Volume confirmation of the 24h move.
    if vol_ratio > 1.3 and abs(change_24h) >= 2:
        pts = 1 if change_24h > 0 else -1
        text = (f"The {change_24h:+.2f}% 24h move came on {vol_ratio:.1f}x normal volume: "
                "strong participation backs the move.")
    elif vol_ratio < 0.8:
        pts, text = 0, f"Volume is light ({vol_ratio:.2f}x the 7-day average): moves carry less conviction."
    else:
        pts, text = 0, f"Volume is about normal ({vol_ratio:.2f}x the 7-day average) with no strong 24h move to confirm."
    analysis.append(("Volume", pts, text))

    score = sum(p for _, p, _ in analysis)
    signal = "BUY" if score >= BUY_THRESHOLD else "SELL" if score <= SELL_THRESHOLD else "HOLD"
    bulls = [name for name, p, _ in analysis if p > 0]
    bears = [name for name, p, _ in analysis if p < 0]

    if signal == "BUY":
        verdict = f"{len(bulls)} bullish factors ({', '.join(bulls)}) outweigh the bearish ones, giving a score of {score:+d}."
    elif signal == "SELL":
        verdict = f"{len(bears)} bearish factors ({', '.join(bears)}) outweigh the bullish ones, giving a score of {score:+d}."
    else:
        verdict = f"The score of {score:+d} is between {SELL_THRESHOLD:+d} and {BUY_THRESHOLD:+d}, so the evidence is not strong enough to act on."
        if bulls and bears:
            verdict += f" Bullish ({', '.join(bulls)}) and bearish ({', '.join(bears)}) signals conflict."
        elif bulls:
            verdict += f" It leans bullish ({', '.join(bulls)}); watch for confirmation from the other indicators."
        elif bears:
            verdict += f" It leans bearish ({', '.join(bears)}); watch for confirmation from the other indicators."
        else:
            verdict += " Every indicator is neutral: the market is waiting for a direction."
        if squeeze:
            verdict += " The Bollinger squeeze suggests a breakout may come soon; the direction of the break is the next signal to watch."

    return {
        "price": price,
        "change_24h": change_24h,
        "change_7d": change_7d,
        "high_24h": float(ticker["highPrice"]),
        "low_24h": float(ticker["lowPrice"]),
        "vol_24h": vol_24h,
        "vol_ratio": vol_ratio,
        "ma7": ma7,
        "ma25": ma25,
        "ma99": ma99,
        "boll": (boll_low, boll_mid, boll_up),
        "pct_b": pct_b,
        "squeeze": squeeze,
        "macd": (m, s, h),
        "macd_cross": cross,
        "rsi": rsi14,
        "score": score,
        "signal": signal,
        "analysis": analysis,
        "verdict": verdict,
    }


# --- rendering ---------------------------------------------------------------


def fmt_price(p):
    return f"${p:,.2f}"


def fmt_pct(p):
    return "n/a" if p is None else f"{p:+.2f}%"


def fmt_vol(v):
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if v >= size:
            return f"${v / size:,.2f}{unit}"
    return f"${v:,.0f}"


def trend_label(r):
    if r["price"] > r["ma25"] > r["ma99"]:
        return "Uptrend"
    if r["price"] < r["ma25"] < r["ma99"]:
        return "Downtrend"
    return "Sideways"


def render(results, now):
    icon = {"BUY": "🟢 BUY", "SELL": "🔴 SELL", "HOLD": "🟡 HOLD"}
    pts_icon = {1: "🟢 +1", 0: "⚪ 0", -1: "🔴 −1"}
    lines = [
        "# Crypto Market Report",
        "",
        f"_Updated {now:%Y-%m-%d %H:%M} UTC · data: Binance spot (USDT pairs), 1h candles_",
        "",
        "| Coin | Price | 24h | 7d | 24h Volume | Vol vs 7d avg | Trend | Score | Signal |",
        "|---|---:|---:|---:|---:|---:|---|---:|---|",
    ]
    for coin, r in results.items():
        if "error" in r:
            lines.append(f"| {coin} | error: {r['error']} | | | | | | | |")
            continue
        lines.append(
            f"| **{coin}** | {fmt_price(r['price'])} | {fmt_pct(r['change_24h'])} | {fmt_pct(r['change_7d'])} "
            f"| {fmt_vol(r['vol_24h'])} | {r['vol_ratio']:.2f}x | {trend_label(r)} | {r['score']:+d} | {icon[r['signal']]} |"
        )

    lines += [
        "",
        "## Indicators (1h candles)",
        "",
        "| Coin | MA7 | MA25 | MA99 | BOLL lower / mid / upper | %B | MACD / signal / hist | RSI(14) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for coin, r in results.items():
        if "error" in r:
            continue
        lo, mid, up = r["boll"]
        m, s, h = r["macd"]
        squeeze = " 🔸squeeze" if r["squeeze"] else ""
        cross = f" ({r['macd_cross']} cross)" if r["macd_cross"] else ""
        lines.append(
            f"| **{coin}** | {fmt_price(r['ma7'])} | {fmt_price(r['ma25'])} | {fmt_price(r['ma99'])} "
            f"| {fmt_price(lo)} / {fmt_price(mid)} / {fmt_price(up)}{squeeze} | {r['pct_b']:.2f} "
            f"| {m:+,.2f} / {s:+,.2f} / {h:+,.2f}{cross} | {r['rsi']:.0f} |"
        )

    lines += ["", "## Analysis", ""]
    for coin, r in results.items():
        if "error" in r:
            continue
        lines += [
            f"### {coin}: {icon[r['signal']]} (score {r['score']:+d})",
            f"24h range {fmt_price(r['low_24h'])} – {fmt_price(r['high_24h'])}.",
            "",
            "| Factor | Points | Reading |",
            "|---|---|---|",
            *[f"| {name} | {pts_icon[p]} | {text} |" for name, p, text in r["analysis"]],
            "",
            f"**Why {r['signal']}:** {r['verdict']}",
            "",
        ]

    lines += [
        "## How the signal works",
        "Six factors each score +1 (bullish), 0 (neutral) or −1 (bearish):",
        "- **MA (short-term):** price > MA7 > MA25 is +1; price < MA7 < MA25 is −1.",
        "- **MA (multi-day):** price and MA25 both above MA99 is +1; both below is −1.",
        "- **MACD (12, 26, 9):** a fresh crossover, or a histogram growing on the same side of zero, is ±1.",
        "- **BOLL (20, 2):** closing outside the bands is a stretched move expected to revert (below lower is +1, above upper is −1). A squeeze is reported but not scored.",
        "- **RSI (14):** below 30 is +1; above 70 is −1.",
        "- **Volume:** a 24h move of 2% or more on volume above 1.3x the 7-day average scores in the direction of the move.",
        "",
        f"Score {BUY_THRESHOLD:+d} or more → BUY, {SELL_THRESHOLD:+d} or less → SELL, otherwise HOLD.",
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
            klines = fetch_json(f"/klines?symbol={symbol}&interval=1h&limit={KLINE_LIMIT}")
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
