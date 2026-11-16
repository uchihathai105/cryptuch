"""Hour-of-day profile of a coin in Vietnam time (UTC+7), from one year of 1-hour candles.

For each of the 24 hours: how much of the day's trading volume happens then, how aggressive the buyers are,
and how the price moved. Pure functions: no network, no database.

Two things are measured very differently in quality:
* The volume pattern is stable: the same hours are busy every day, so a year of data pins it down well.
* The direction of the price by hour is mostly noise. An average move of a few hundredths of a percent over
  ~365 samples per hour is rarely distinguishable from chance, and with 24 hours some will look special by luck.
  An hour is only called notable when |t| >= HOURLY_NOTABLE_T (3.0), and even then it is a description of the
  past, not a prediction.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C

VN_OFFSET_H = 7


def vn_hour(open_time_ms) -> np.ndarray:
    return ((np.asarray(open_time_ms, dtype="int64") // 3_600_000) + VN_OFFSET_H) % 24


def vn_day(open_time_ms) -> np.ndarray:
    return (np.asarray(open_time_ms, dtype="int64") + VN_OFFSET_H * 3_600_000) // 86_400_000


# ---------------------------------------------------------------- what is going on at each hour
def sessions(hour: int) -> list[str]:
    s = []
    if 7 <= hour < 15:
        s.append("Asia")
    if 14 <= hour < 23:
        s.append("Europe")
    if hour >= 20 or hour < 4:
        s.append("US")
    return s


EVENTS = {
    7: "Daily candle closes and funding is paid (07:00); Tokyo and Seoul stock markets open",
    8: "Singapore, Hong Kong and China stock markets open",
    14: "London opens in summer time",
    15: "Funding is paid (15:00); London and Frankfurt open in winter time; Deribit options expire on Fridays",
    19: "US economic data (CPI, jobs) comes out at 19:30 in US summer time",
    20: "US economic data at 20:30 in US winter time; US stock market opens at 20:30 in summer",
    21: "US stock market opens at 21:30 in US winter time",
    23: "Funding is paid (23:00)",
    1: "Federal Reserve decisions come out at 01:00 in summer (on meeting days)",
    2: "Federal Reserve decisions come out at 02:00 in winter (on meeting days)",
    3: "US stock market closes at 03:00 in summer",
    4: "US stock market closes at 04:00 in winter",
}


def context(hour: int) -> str:
    """Plain-language list of what coincides with this Vietnam hour. A schedule, not proof of cause."""
    ss = sessions(hour)
    parts = []
    if ss:
        parts.append("/".join(ss) + " session" + ("s overlap" if len(ss) > 1 else ""))
    else:
        parts.append("between sessions (US closed, Asia not open yet)")
    if hour in EVENTS:
        parts.append(EVENTS[hour])
    return "; ".join(parts)


# ---------------------------------------------------------------- the profile
def compute(df: pd.DataFrame) -> dict | None:
    """df columns: open_time, open, close, quote_volume, taker_buy_quote (closed 1h candles). None if too short."""
    if df is None or len(df) < 24 * C.HOURLY_MIN_DAYS:
        return None
    d = df.copy()
    d["hour"], d["day"] = vn_hour(d["open_time"]), vn_day(d["open_time"])
    d["ret"] = (d["close"] / d["open"] - 1) * 100
    d = d[d["quote_volume"] > 0]

    # volume share of each hour within its own day (only days with all 24 hours)
    full = d.groupby("day")["hour"].transform("nunique") == 24
    days = d[full]
    if days["day"].nunique() < C.HOURLY_MIN_DAYS:
        return None
    day_total = days.groupby("day")["quote_volume"].transform("sum")
    days = days.assign(share=days["quote_volume"] / day_total * 100)
    share = days.groupby("hour")["share"].mean().reindex(range(24)).fillna(0.0)

    # does the pattern hold in both halves of the period?
    uniq = sorted(days["day"].unique())
    half = uniq[len(uniq) // 2]
    a = days[days["day"] < half].groupby("hour")["share"].mean().reindex(range(24)).fillna(0.0)
    b = days[days["day"] >= half].groupby("hour")["share"].mean().reindex(range(24)).fillna(0.0)
    consistency = float(np.corrcoef(a, b)[0, 1]) if a.std() > 0 and b.std() > 0 else None

    out = []
    for h in range(24):
        x = d[d["hour"] == h]
        n = len(x)
        mean = float(x["ret"].mean()) if n else 0.0
        std = float(x["ret"].std(ddof=1)) if n > 1 else 0.0
        t = mean / (std / np.sqrt(n)) if n > 1 and std > 0 else 0.0
        qv = float(x["quote_volume"].sum())
        out.append({
            "hour": h, "share": float(share[h]), "n": n,
            "buy_share": float(x["taker_buy_quote"].sum() / qv * 100) if qv > 0 else None,
            "avg_ret": mean, "up_rate": float((x["ret"] > 0).mean() * 100) if n else None, "t": float(t),
            "notable": bool(abs(t) >= C.HOURLY_NOTABLE_T),
            "sessions": sessions(h), "context": context(h),
        })
    ranked = sorted(out, key=lambda r: -r["share"])
    return {
        "hours": out, "days": int(days["day"].nunique()), "candles": int(len(d)),
        "top": [r["hour"] for r in ranked[:3]], "quiet": [r["hour"] for r in ranked[-3:][::-1]],
        "top_share": float(sum(r["share"] for r in ranked[:3])), "consistency": consistency,
        "best_hour": max(out, key=lambda r: r["avg_ret"])["hour"], "worst_hour": min(out, key=lambda r: r["avg_ret"])["hour"],
        "notable_up": [r["hour"] for r in out if r["notable"] and r["avg_ret"] > 0],
        "notable_down": [r["hour"] for r in out if r["notable"] and r["avg_ret"] < 0],
        "first_day": int(uniq[0]), "last_day": int(uniq[-1]),
    }


def _hh(h: int) -> str:
    return f"{h:02d}:00–{(h + 1) % 24:02d}:00"


def explain(p: dict) -> list[dict]:
    """Plain-language reading of a profile, with the schedule that coincides with each notable hour."""
    H = {r["hour"]: r for r in p["hours"]}
    flat = 100 / 24
    out = []
    top = p["top"]
    out.append({"tone": "info", "text": "Busiest hours (Vietnam time): " + "; ".join(
        f"{_hh(h)} = {H[h]['share']:.1f}% of the day's volume ({H[h]['share'] / flat:.1f}× an even share). Coincides with: {H[h]['context']}"
        for h in top) + f". Together these three hours carry {p['top_share']:.0f}% of the day."})
    out.append({"tone": "info", "text": "Quietest hours: " + "; ".join(
        f"{_hh(h)} ({H[h]['share']:.1f}%)" for h in p["quiet"]) + ". Thin trading means wider spreads and sudden spikes on little volume."})
    c = p.get("consistency")
    if c is not None:
        word = "very stable" if c >= 0.9 else "fairly stable" if c >= 0.75 else "unstable"
        out.append({"tone": "good" if c >= 0.75 else "warn",
                    "text": f"The volume pattern is {word}: the first and second half of the period correlate at {c:.2f}."})
    buy = [(h, r["buy_share"]) for h, r in H.items() if r["buy_share"] is not None]
    if buy:
        hi, lo = max(buy, key=lambda x: x[1]), min(buy, key=lambda x: x[1])
        if hi[1] - lo[1] >= 2.0:
            out.append({"tone": "info", "text": f"Aggressive buyers (taker buys) were {hi[1]:.1f}% of volume at {_hh(hi[0])} and only "
                                                f"{lo[1]:.1f}% at {_hh(lo[0])}. This shows who hits the order book, not money moving in or out of the exchange."})
    if p["notable_up"] or p["notable_down"]:
        for h in p["notable_up"][:3]:
            r = H[h]
            out.append({"tone": "warn", "text": f"{_hh(h)} rose on average {r['avg_ret']:+.3f}% per hour (up in {r['up_rate']:.0f}% of {r['n']} hours, t = {r['t']:.1f}). "
                                                f"A pattern in the past; it can disappear."})
        for h in p["notable_down"][:3]:
            r = H[h]
            out.append({"tone": "warn", "text": f"{_hh(h)} fell on average {r['avg_ret']:+.3f}% per hour (up in only {r['up_rate']:.0f}% of {r['n']} hours, t = {r['t']:.1f}). "
                                                f"A pattern in the past; it can disappear."})
    else:
        out.append({"tone": "info", "text": "No hour shows a reliable tendency to rise or fall: the differences are within normal noise. "
                                            "Do not trade on the hour of the day alone."})
    out.append({"tone": "info", "text": "US events shift by one hour between US summer and winter time (clocks change in March and November), "
                                        "so the US-related volume is spread over two neighbouring hours."})
    return out


def summary(symbol: str, p: dict, computed_at: int | None = None) -> dict:
    H = {r["hour"]: r for r in p["hours"]}
    return {"symbol": symbol, "base": symbol[:-4], "days": p["days"], "computed_at": computed_at,
            "top": [{"hour": h, "share": H[h]["share"], "sessions": H[h]["sessions"]} for h in p["top"]],
            "quiet": p["quiet"][0], "top_share": p["top_share"], "consistency": p["consistency"],
            "notable_up": p["notable_up"], "notable_down": p["notable_down"],
            "best_hour": {"hour": p["best_hour"], "avg_ret": H[p["best_hour"]]["avg_ret"], "notable": H[p["best_hour"]]["notable"]},
            "worst_hour": {"hour": p["worst_hour"], "avg_ret": H[p["worst_hour"]]["avg_ret"], "notable": H[p["worst_hour"]]["notable"]}}
