#!/usr/bin/env python3
"""Crypto market report for BTC, ETH, BNB, XRP, SOL, SUI, NEAR and ZEC.

Fetches market data from Binance's public market-data API, computes trend
(MA), volatility (Bollinger Bands) and momentum (MACD, RSI) indicators, and
prints a Markdown report with a rule-based BUY / SELL / HOLD signal and a
written analysis explaining it.

Uses only the Python standard library.
"""

import json
import os
import re
import statistics
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

SYMBOLS = {
    "BTC": "BTCUSDT", "ETH": "ETHUSDT", "BNB": "BNBUSDT", "XRP": "XRPUSDT",
    "SOL": "SOLUSDT", "SUI": "SUIUSDT", "NEAR": "NEARUSDT", "ZEC": "ZECUSDT",
}
KLINE_LIMIT = 300  # hourly candles: enough for MA99, MACD warm-up and 8 days of volume
SHORT_INTERVAL, SHORT_LIMIT = "15m", 120  # short-term view (not scored): 30 hours of 15-minute candles

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
    text += f" (MACD {fmt_ind(m)}, signal {fmt_ind(s)}, histogram {fmt_ind(h)})"
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


def short_term(klines):
    """15-minute snapshot for timing: RSI, MACD momentum and price vs MA20 (~5 hours). Not scored."""
    closes = [float(k[4]) for k in klines]
    price = closes[-1]
    rsi14 = rsi(closes)
    ma20 = sma(closes, 20)
    _, _, hist = macd(closes)
    cross = None
    for prev, cur in zip(hist[-4:-1], hist[-3:]):
        if prev <= 0 < cur:
            cross = "up"
        elif prev >= 0 > cur:
            cross = "down"
    if price > ma20 and hist[-1] > 0:
        bias = "bullish"
    elif price < ma20 and hist[-1] < 0:
        bias = "bearish"
    else:
        bias = "mixed"
    notes = []
    if cross:
        notes.append(f"MACD just crossed {cross}")
    elif hist[-1] > 0:
        notes.append("MACD rising" if hist[-1] > hist[-2] else "MACD up but fading")
    else:
        notes.append("MACD falling" if hist[-1] < hist[-2] else "MACD down but easing")
    if rsi14 >= 70:
        notes.append("overbought")
    elif rsi14 <= 30:
        notes.append("oversold")
    change_1h = (price / closes[-5] - 1) * 100 if len(closes) >= 5 else None
    lows = [float(k[3]) for k in klines]
    # Pullback-and-bounce: one of the last two candles dipped to MA20 and price is back above it, rising.
    bounce = min(lows[-2:]) <= ma20 * 1.002 and price > ma20 and price > closes[-2]
    return {"rsi": rsi14, "ma20": ma20, "bias": bias, "cross": cross, "notes": notes, "change_1h": change_1h,
            "above_ma": price > ma20, "price": price, "swing_low": min(lows[-8:]), "bounce": bounce}


def entry_plan(r):
    """Split a 1h BUY/SELL into 'act now' vs 'wait', with a zone, stop and targets.

    BUY is 'wait for a pullback' when price is stretched (near the upper band, RSI > 65),
    volume is light or the 15m momentum is turning down; SELL mirrors that. Levels come from
    MA7 / Bollinger middle (pullback zone), MA25 / Bollinger middle (stop) and 1.5R / 3R targets.
    """
    signal, price = r["signal"], r["price"]
    lo, mid, up = r["boll"]
    st = r.get("short") or {}
    if signal == "HOLD":
        return {"label": "HOLD", "reasons": []}
    reasons = []
    if signal == "BUY":
        if r["pct_b"] > 0.8:
            reasons.append(f"price near the top of the bands (%B {r['pct_b']:.2f}), don't chase")
        if r["rsi"] > 60:
            reasons.append(f"RSI {r['rsi']:.0f} is hot")
        if st.get("bias") == "bearish" or st.get("cross") == "down":
            reasons.append("15m momentum turning down")
        if r["vol_ratio"] < 0.8:
            reasons.append(f"light volume ({r['vol_ratio']:.2f}x)")
        zone = sorted(v for v in (r["ma7"], mid) if v < price) or [price]
        entry = price if not reasons else sum(zone) / len(zone)
        stop = min(r["ma25"], mid, zone[0]) * 0.995
        risk = entry - stop
        targets = (entry + 1.5 * risk, entry + 3 * risk) if risk > 0 else None
        label = "BUY-WAIT" if reasons else "BUY"
    else:
        if r["pct_b"] < 0.2:
            reasons.append(f"price near the bottom of the bands (%B {r['pct_b']:.2f}), don't sell into the low")
        if r["rsi"] < 40:
            reasons.append(f"RSI {r['rsi']:.0f} is low")
        if st.get("bias") == "bullish" or st.get("cross") == "up":
            reasons.append("15m momentum turning up")
        if r["vol_ratio"] < 0.8:
            reasons.append(f"light volume ({r['vol_ratio']:.2f}x)")
        zone = sorted(v for v in (r["ma7"], mid) if v > price) or [price]
        entry = price if not reasons else sum(zone) / len(zone)
        stop = max(r["ma25"], mid, zone[-1]) * 1.005  # a reclaim above this cancels the SELL
        risk = stop - entry
        targets = (entry - 1.5 * risk, entry - 3 * risk) if risk > 0 else None
        label = "SELL-WAIT" if reasons else "SELL"
    return {"label": label, "reasons": reasons, "zone": (zone[0], zone[-1]), "entry": entry,
            "stop": stop, "targets": targets}


PLAN_ICON = {
    "BUY": "🟢 BUY (MUA)", "BUY-WAIT": "🟡 BUY · wait for pullback (MUA · chờ giá điều chỉnh)",
    "HOLD": "⚪ HOLD (GIỮ / đứng ngoài)",
    "SELL": "🔴 SELL (BÁN)", "SELL-WAIT": "🟠 SELL · wait for bounce (BÁN · chờ giá hồi)",
}
# Short Vietnamese tags for push lines.
VN_LABEL = {"BUY": "MUA", "BUY-WAIT": "MUA·chờ điều chỉnh", "HOLD": "GIỮ",
            "SELL": "BÁN", "SELL-WAIT": "BÁN·chờ hồi"}


def plan_vn(p):
    """One-line Vietnamese summary of the trade plan."""
    if p["label"] == "HOLD" or not p.get("targets"):
        return ""
    z0, z1 = p["zone"]
    zone = fmt_price(z0) if abs(z1 - z0) < 1e-9 else f"{fmt_price(z0)}–{fmt_price(z1)}"
    t1, t2 = (fmt_price(t) for t in p["targets"])
    stop = fmt_price(p["stop"])
    if p["label"] == "BUY":
        return f"Có thể mua quanh {fmt_price(p['entry'])}; cắt lỗ {stop}; chốt lời {t1} / {t2}."
    if p["label"] == "BUY-WAIT":
        return f"Xu hướng tăng nhưng giá đang cao, đừng mua đuổi. Chờ giá về {zone} rồi mua; cắt lỗ {stop}; chốt lời {t1} / {t2}."
    if p["label"] == "SELL":
        return f"Xu hướng giảm: nên thoát/không giữ quanh {fmt_price(p['entry'])}. Nếu giá vượt lại {stop} thì tín hiệu bán bị hủy."
    return (f"Xu hướng giảm nhưng giá đã gần đáy, đừng bán tháo. Chờ giá hồi lên {zone} rồi bán; "
            f"nếu giá vượt lại {stop} thì hủy tín hiệu bán. Chưa có hàng thì đừng bắt đáy.")


def plan_text(p):
    if p["label"] == "HOLD" or not p.get("targets"):
        return ""
    t1, t2 = p["targets"]
    z0, z1 = p["zone"]
    zone = fmt_price(z0) if abs(z1 - z0) < 1e-9 else f"{fmt_price(z0)}–{fmt_price(z1)}"
    if p["label"] == "BUY":
        return f"Buy now ~{fmt_price(p['entry'])} · stop {fmt_price(p['stop'])} · targets {fmt_price(t1)} / {fmt_price(t2)}"
    if p["label"] == "BUY-WAIT":
        return (f"Wait for a pullback to {zone} ({'; '.join(p['reasons'])}) · stop {fmt_price(p['stop'])} "
                f"· targets {fmt_price(t1)} / {fmt_price(t2)}")
    if p["label"] == "SELL":
        return f"Sell / exit now ~{fmt_price(p['entry'])} · invalid above {fmt_price(p['stop'])} · downside {fmt_price(t1)} / {fmt_price(t2)}"
    return (f"Exit on a bounce to {zone} ({'; '.join(p['reasons'])}) · invalid above {fmt_price(p['stop'])} "
            f"· downside {fmt_price(t1)} / {fmt_price(t2)}")


SCALP_MAX_RISK_PCT = 2.5


def scalp_setup(r):
    """Intraday long setup on 15m candles, only with the 1h trend (spot: long only).

    LONG when the 1h trend is not down (price above 1h MA25, signal not SELL), 15m is bullish,
    RSI 40-68, and there is a trigger: a fresh 15m MACD cross up or a bounce off the 15m MA20.
    Stop just under the last 2 hours' low; targets 1R and 2R, capped by the 1h upper band.
    """
    st = r.get("short")
    if not st:
        return {"state": "n/a", "why": "no 15m data"}
    trend_ok = r["price"] > r["ma25"] and r["signal"] != "SELL"
    if not trend_ok:
        return {"state": "AVOID", "why": "1h trend not up"}
    if st["bias"] != "bullish":
        return {"state": "WAIT", "why": f"15m {st['bias']}"}
    if not 40 <= st["rsi"] <= 68:
        return {"state": "WAIT", "why": f"15m RSI {st['rsi']:.0f} {'too hot' if st['rsi'] > 68 else 'too weak'}"}
    trigger = "MACD cross up" if st["cross"] == "up" else "bounce off MA20" if st["bounce"] else None
    if not trigger:
        return {"state": "WAIT", "why": "no 15m trigger yet (MACD cross up or MA20 bounce)"}
    entry = st["price"]
    stop = st["swing_low"] * 0.998
    risk = entry - stop
    if risk <= 0 or risk / entry * 100 > SCALP_MAX_RISK_PCT:
        return {"state": "WAIT", "why": f"stop too far (> {SCALP_MAX_RISK_PCT}%)"}
    t1, t2 = entry + risk, entry + 2 * risk
    up = r["boll"][2]
    if up > entry:
        t2 = min(t2, up)
    return {"state": "LONG", "why": trigger, "entry": entry, "stop": stop, "t1": t1, "t2": max(t1, t2),
            "risk_pct": risk / entry * 100}


def scalp_text(coin, s):
    return (f"⚡ {coin} scalp LONG {fmt_price(s['entry'])} · SL {fmt_price(s['stop'])} (−{s['risk_pct']:.1f}%) "
            f"· TP {fmt_price(s['t1'])} / {fmt_price(s['t2'])} · {s['why']}")


def short_label(st):
    icon = {"bullish": "🟢", "bearish": "🔴", "mixed": "⚪"}[st["bias"]]
    return f"{icon} {st['bias']} · RSI {st['rsi']:.0f} · {', '.join(st['notes'])}"


# --- rendering ---------------------------------------------------------------


def fmt_price(p):
    # Cheaper coins (XRP, SUI, ...) need more decimals for MA / Bollinger levels to mean anything.
    return f"${p:,.2f}" if abs(p) >= 100 else f"${p:,.4f}"


def fmt_ind(x):
    """MACD values: large for BTC, tiny for XRP."""
    return f"{x:+,.2f}" if abs(x) >= 1 else f"{x:+.5f}"


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
            f"| {fmt_vol(r['vol_24h'])} | {r['vol_ratio']:.2f}x | {trend_label(r)} | {r['score']:+d} "
            f"| {PLAN_ICON[r['plan']['label']] if r.get('plan') else icon[r['signal']]} |"
        )

    lines += [
        "",
        "**Chú thích:** 🟢 MUA: xu hướng tăng, vào được · 🟡 MUA · chờ điều chỉnh: xu hướng tăng nhưng giá đang cao, "
        "chờ giá giảm về vùng gợi ý · ⚪ GIỮ: chưa có tín hiệu rõ, đứng ngoài · 🔴 BÁN: xu hướng giảm, thoát/không giữ · "
        "🟠 BÁN · chờ hồi: xu hướng giảm nhưng giá đã gần đáy, đừng bán tháo, chờ hồi rồi bán. Chỉ giao dịch spot "
        "(không bán khống).",
    ]

    lines += [
        "",
        "## ⚡ Intraday scalping (15m candles, long only, with the 1h trend)",
        "",
        "| Coin | Setup | Entry | Stop | Target 1 / 2 | Why |",
        "|---|---|---:|---:|---:|---|",
    ]
    for coin, r in results.items():
        s = r.get("scalp")
        if "error" in r or not s:
            continue
        if s["state"] == "LONG":
            lines.append(f"| **{coin}** | ⚡ **LONG (mua lướt)** | {fmt_price(s['entry'])} | {fmt_price(s['stop'])} "
                         f"(−{s['risk_pct']:.1f}%) | {fmt_price(s['t1'])} / {fmt_price(s['t2'])} | {s['why']} |")
        else:
            icon_s = "⛔ avoid (tránh)" if s["state"] == "AVOID" else "⏳ wait (chờ)"
            lines.append(f"| **{coin}** | {icon_s} | | | | {s['why']} |")
    lines += ["", "LONG needs: 1h price above MA25 and 1h signal not SELL; 15m bullish with RSI 40–68; and a "
              "trigger (fresh 15m MACD cross up, or a bounce off the 15m MA20). Stop sits just under the last "
              f"2 hours' low (skipped if wider than {SCALP_MAX_RISK_PCT}%); targets are 1R and 2R, capped at the "
              "1h upper Bollinger band. Scalps are fast: take profit at the targets, always use the stop."]

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
            f"| {fmt_ind(m)} / {fmt_ind(s)} / {fmt_ind(h)}{cross} | {r['rsi']:.0f} |"
        )

    lines += [
        "",
        "## Short-term view (15m candles, for timing only, not part of the signal)",
        "",
        "| Coin | Last 1h | RSI(14) | vs MA20 (~5h) | MACD | Read |",
        "|---|---:|---:|---|---|---|",
    ]
    for coin, r in results.items():
        st = r.get("short")
        if "error" in r or not st:
            continue
        lines.append(
            f"| **{coin}** | {fmt_pct(st['change_1h'])} | {st['rsi']:.0f} "
            f"| {'above' if st['above_ma'] else 'below'} {fmt_price(st['ma20'])} | {st['notes'][0]} "
            f"| {short_label(st).split(' · ')[0]} |"
        )
    lines += ["", "🟢 bullish = price above MA20 and MACD histogram positive; 🔴 bearish = both negative; "
              "⚪ mixed otherwise. Short-term readings flip often; use them to time entries around the 1h signal."]

    lines += ["", "## Analysis", ""]
    for coin, r in results.items():
        if "error" in r:
            continue
        plan = plan_text(r["plan"]) if r.get("plan") else ""
        lines += [
            f"### {coin}: {PLAN_ICON[r['plan']['label']] if r.get('plan') else icon[r['signal']]} (score {r['score']:+d})",
            f"24h range {fmt_price(r['low_24h'])} – {fmt_price(r['high_24h'])}."
            + (f" Short-term (15m): {short_label(r['short'])}." if r.get("short") else ""),
            *([f"", f"**Trade plan:** {plan}", f"", f"**Gợi ý:** {plan_vn(r['plan'])}"] if plan else []),
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
        "**Act now or wait?** A BUY becomes **🟡 BUY · wait for pullback** when price is stretched "
        "(%B > 0.8 or RSI > 60), volume is light (< 0.8x) or 15m momentum is turning down; the plan then "
        "names the pullback zone (MA7 / Bollinger middle). SELL mirrors this (**🟠 wait for bounce** when "
        "%B < 0.2, RSI < 40, volume is light or 15m momentum is turning up, so you don't sell into the low). "
        "Stops sit below MA25 / Bollinger middle (above for SELL); targets are 1.5R and 3R.",
        "",
        "> ⚠️ This is an automated technical-indicator summary, not financial advice. "
        "Crypto is highly volatile; indicators lag and are often wrong. "
        "Only risk money you can afford to lose.",
        "",
        # Machine-readable signals, so the next run can detect changes.
        f"<!-- signals: {json.dumps(signals_of(results))} -->",
        f"<!-- scalps: {json.dumps(scalps_of(results))} -->",
        "",
    ]
    return "\n".join(lines)


def scalps_of(results):
    return sorted(c for c, r in results.items() if r.get("scalp", {}).get("state") == "LONG")


def previous_scalps(report_text):
    marker = re.search(r"<!-- scalps: (\[.*?\]) -->", report_text or "")
    return set(json.loads(marker.group(1))) if marker else set()


def signals_of(results):
    return {coin: r["signal"] for coin, r in results.items() if "error" not in r}


def previous_signals(report_text):
    marker = re.search(r"<!-- signals: (\{.*?\}) -->", report_text or "")
    return json.loads(marker.group(1)) if marker else {}


def notification(results, previous, always=False, previous_scalp=frozenset(), test=False):
    """New scalp setups first (lines start with ⚡), then one line per coin whose signal changed
    (or every coin if always). '' if nothing to send."""
    new_scalps = [scalp_text(c, r["scalp"]) for c, r in results.items()
                  if r.get("scalp", {}).get("state") == "LONG" and c not in previous_scalp]
    lines = []
    for coin, r in results.items():
        if "error" in r:
            continue
        before = previous.get(coin)
        changed = before is not None and before != r["signal"]
        # Routine summaries only list coins with something to act on: a BUY/SELL signal
        # (including the wait-for-pullback/bounce variants) or an active scalp setup.
        actionable = r["signal"] != "HOLD" or r.get("scalp", {}).get("state") == "LONG"
        if (always and actionable) or changed or test:
            shown = r["plan"]["label"] if r.get("plan") else r["signal"]
            label = f"{before} → {shown}" if changed else shown
            label += f" ({VN_LABEL.get(shown, shown)})"
            line = f"{coin} {label} at {fmt_price(r['price'])} (score {r['score']:+d}, 24h {fmt_pct(r['change_24h'])})"
            if r.get("short"):
                st = r["short"]
                line += f" · 15m {st['bias']} RSI {st['rsi']:.0f}"
            if r.get("scalp", {}).get("state") == "LONG":
                line += " · scalp LONG"
            lines.append(line)
    return "\n".join(new_scalps + ([""] if new_scalps and lines else []) + lines)


def main():
    results = {}
    for coin, symbol in SYMBOLS.items():
        try:
            ticker = fetch_json(f"/ticker/24hr?symbol={symbol}")
            klines = fetch_json(f"/klines?symbol={symbol}&interval=1h&limit={KLINE_LIMIT}")
            results[coin] = analyze(ticker, klines)
        except Exception as e:
            results[coin] = {"error": str(e)}
            continue
        try:
            short = fetch_json(f"/klines?symbol={symbol}&interval={SHORT_INTERVAL}&limit={SHORT_LIMIT}")
            results[coin]["short"] = short_term(short)
        except Exception as e:  # the 1h report still works without the short-term view
            print(f"::warning::{coin} 15m data unavailable: {e}")
        results[coin]["plan"] = entry_plan(results[coin])
        results[coin]["scalp"] = scalp_setup(results[coin])

    now = datetime.now(timezone.utc)
    report = render(results, now)
    prev_text = ""
    prev_path = os.environ.get("PREV_REPORT_PATH", "")
    if prev_path and os.path.exists(prev_path):
        with open(prev_path) as f:
            prev_text = f.read()
    # The report may run every couple of minutes; the routine all-coins push keeps its own pace
    # (SUMMARY_MINUTES, default 10) so ntfy's free daily limit isn't hit. Alerts are never delayed.
    last = re.search(r"<!-- last-summary: (\S+) -->", prev_text)
    last_at = datetime.fromisoformat(last.group(1)) if last else None
    every = timedelta(minutes=float(os.environ.get("SUMMARY_MINUTES", "10")))
    summary_due = last_at is None or now - last_at >= every - timedelta(seconds=45)
    report += f"<!-- last-summary: {(now if summary_due else last_at).isoformat(timespec='seconds')} -->\n"
    out = os.environ.get("REPORT_PATH", "REPORT.md")
    with open(out, "w") as f:
        f.write(report)
    print(report)

    # Write a push-notification message when a signal changed since the previous report.
    notify_path = os.environ.get("NOTIFY_PATH")
    if notify_path:
        previous, previous_scalp = previous_signals(prev_text), previous_scalps(prev_text)
        always = os.environ.get("NOTIFY_ALWAYS") == "true" and summary_due
        test = os.environ.get("TEST_PUSH") == "true"  # manual test: every coin, whatever its signal
        message = notification(results, previous, always=always, previous_scalp=previous_scalp, test=test)
        with open(notify_path, "w") as f:
            f.write(message)
        print(f"\nNotification: {message or '(no signal change)'}")
    return 1 if all("error" in r for r in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
