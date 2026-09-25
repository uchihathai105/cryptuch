#!/usr/bin/env python3
"""Daily events report for BTC, ETH and BNB.

- Yesterday: each coin's daily move (Binance daily candle, UTC day) plus crypto
  news headlines from that day, grouped by coin and market-wide topics.
- Next 7 days: scheduled high-impact US economic events (Forex Factory's public
  calendar feed) and recent headlines that mention upcoming crypto events.

Rule-based and free: no AI, no API keys. Uses only the Python standard library.
"""

import email.utils
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from report import SYMBOLS, fetch_json, fmt_pct, fmt_price, fmt_vol

VN = timezone(timedelta(hours=7), "ICT")
USER_AGENT = "Mozilla/5.0 (compatible; cryptuch-events/1.0; +https://github.com/uchihathai105/cryptuch)"

NEWS_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
    "The Block": "https://www.theblock.co/rss.xml",
}
CALENDAR_FEEDS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
]

# Headline groups, matched case-insensitively on whole words.
TOPICS = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "ether", "eth"],
    "BNB": ["bnb", "binance", "bnb chain"],
    "Market-wide": [
        "fed", "fomc", "powell", "interest rate", "rate cut", "rate hike", "inflation", "cpi",
        "jobs report", "payrolls", "sec", "etf", "etfs", "regulation", "regulator", "stablecoin",
        "tariff", "tariffs", "treasury", "crypto market", "liquidation", "liquidations",
    ],
}
# Headlines that point at something scheduled.
UPCOMING_WORDS = [
    "upgrade", "hard fork", "fork", "unlock", "unlocks", "deadline", "vote", "mainnet",
    "launch", "launches", "listing", "decision", "hearing", "next week", "this week",
    "tomorrow", "scheduled", "expiry", "expiration", "approval",
]
MAX_PER_GROUP = 8
MAX_UPCOMING = 8


def fetch_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def matches(text, words):
    return any(re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", text, re.I) for w in words)


# --- data ----------------------------------------------------------------------


def daily_moves():
    """Yesterday's completed UTC daily candle per coin, with volume vs the 7 days before."""
    moves = {}
    for coin, symbol in SYMBOLS.items():
        try:
            # Last candle is today's (still open); the one before is yesterday's.
            k = fetch_json(f"/klines?symbol={symbol}&interval=1d&limit=9")
            day = k[-2]
            open_, high, low, close = (float(x) for x in day[1:5])
            vol = float(day[7])
            prior = [float(c[7]) for c in k[:-2]]
            moves[coin] = {
                "open": open_, "high": high, "low": low, "close": close,
                "change": (close / open_ - 1) * 100,
                "range": (high / low - 1) * 100,
                "vol": vol,
                "vol_ratio": vol / (sum(prior) / len(prior)) if prior else 1.0,
            }
        except Exception as e:
            moves[coin] = {"error": str(e)}
    return moves


def parse_feed(source, raw):
    """RSS 2.0 items as dicts with source, title, link and time (UTC)."""
    items = []
    root = ET.fromstring(raw)
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        date = item.findtext("pubDate")
        if not (title and date):
            continue
        try:
            when = email.utils.parsedate_to_datetime(date).astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        items.append({"source": source, "title": title, "link": link, "time": when})
    return items


def news():
    """All headlines from the feeds, newest first, and the names of feeds that failed."""
    items, failed = [], []
    for source, url in NEWS_FEEDS.items():
        try:
            items += parse_feed(source, fetch_text(url))
        except Exception as e:
            print(f"::warning::{source} feed failed: {e}")
            failed.append(source)
    seen, unique = set(), []
    for it in sorted(items, key=lambda i: i["time"], reverse=True):
        key = re.sub(r"\W+", " ", it["title"].lower()).strip()
        if key not in seen:
            seen.add(key)
            unique.append(it)
    return unique, failed


def group_headlines(items, start, end):
    groups = {name: [] for name in TOPICS}
    for it in items:
        if not (start <= it["time"] < end):
            continue
        for name, words in TOPICS.items():
            if matches(it["title"], words) and len(groups[name]) < MAX_PER_GROUP:
                groups[name].append(it)
    return groups


def upcoming_headlines(items, since):
    coin_words = [w for name in ("BTC", "ETH", "BNB") for w in TOPICS[name]]
    picked = []
    for it in items:
        if it["time"] >= since and matches(it["title"], UPCOMING_WORDS) and matches(it["title"], coin_words):
            picked.append(it)
            if len(picked) >= MAX_UPCOMING:
                break
    return picked


def calendar(now, days=7):
    """High-impact US events from now until now + days, plus how far the feeds reach."""
    events, reach, failed = [], None, []
    for url in CALENDAR_FEEDS:
        try:
            data = json.loads(fetch_text(url))
        except Exception as e:
            print(f"::warning::calendar feed {url} unavailable: {e}")
            failed.append(url)
            continue
        for e in data:
            try:
                when = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
            except (KeyError, ValueError):
                continue
            reach = max(reach, when) if reach else when
            if e.get("country") == "USD" and e.get("impact") == "High" and now <= when < now + timedelta(days=days):
                events.append({"time": when, "title": e.get("title", ""),
                               "forecast": e.get("forecast") or "", "previous": e.get("previous") or ""})
    unique = {(ev["time"], ev["title"]): ev for ev in events}
    return sorted(unique.values(), key=lambda ev: ev["time"]), reach, failed


# --- rendering -----------------------------------------------------------------


def vn(t, fmt="%a %d %b %H:%M"):
    return t.astimezone(VN).strftime(fmt)


def headline_line(it):
    title = it["title"].replace("|", "–").replace("[", "(").replace("]", ")")
    return f"- [{title}]({it['link']}) · {it['source']}, {vn(it['time'], '%H:%M')}"


def render(now, day_start, moves, groups, events, reach, upcoming, failed):
    day_end = day_start + timedelta(days=1)
    lines = [
        "# Crypto Events Report",
        "",
        f"_Updated {vn(now, '%Y-%m-%d %H:%M')} Vietnam time · free sources, no AI: "
        "Binance, crypto news RSS feeds, Forex Factory calendar_",
        "",
        f"## Yesterday · {vn(day_start, '%a %d %b %H:%M')} → {vn(day_end, '%a %d %b %H:%M')} (Vietnam time)",
        "",
        "| Coin | Open | Close | Change | High | Low | Range | Volume | Vol vs 7d avg |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for coin, m in moves.items():
        if "error" in m:
            lines.append(f"| {coin} | error: {m['error']} | | | | | | | |")
            continue
        lines.append(
            f"| **{coin}** | {fmt_price(m['open'])} | {fmt_price(m['close'])} | {fmt_pct(m['change'])} "
            f"| {fmt_price(m['high'])} | {fmt_price(m['low'])} | {m['range']:.2f}% "
            f"| {fmt_vol(m['vol'])} | {m['vol_ratio']:.2f}x |"
        )
    lines += ["", "### Headlines from that day", ""]
    for name, items in groups.items():
        lines.append(f"**{name}**")
        lines += [headline_line(it) for it in items] or ["- _No matching headlines._"]
        lines.append("")

    lines += ["## Next 7 days", "", "### Scheduled high-impact US economic events", ""]
    if events:
        lines += ["| When (Vietnam time) | Event | Forecast | Previous |", "|---|---|---:|---:|"]
        lines += [f"| {vn(ev['time'])} | {ev['title']} | {ev['forecast'] or '–'} | {ev['previous'] or '–'} |"
                  for ev in events]
    else:
        lines.append("_No high-impact US events found in the calendar for this period._")
    if reach and reach < now + timedelta(days=7):
        lines.append(f"\n_The calendar feed only reaches {vn(reach, '%a %d %b')}; later events are not published yet._")
    lines += [
        "",
        "These releases (Fed decisions and speeches, inflation, jobs, GDP) often move BTC, ETH and BNB "
        "because they change expectations for interest rates and risk appetite.",
        "",
        "### Crypto items mentioned in recent news (last 3 days)",
        "",
    ]
    lines += [headline_line(it) for it in upcoming] or ["_No headlines mentioning upcoming BTC, ETH or BNB events._"]

    if failed:
        lines += ["", f"_Unavailable this run: {', '.join(failed)}._"]
    lines += [
        "",
        "> ℹ️ Headlines are matched by keywords, so some may be only loosely related, and news feeds only "
        "keep their most recent items. This is a news digest, not financial advice.",
        "",
    ]
    return "\n".join(lines)


def push_text(moves, events, groups):
    parts = [f"{c} {fmt_pct(m['change'])}" for c, m in moves.items() if "error" not in m]
    lines = ["Yesterday: " + ", ".join(parts) if parts else "Yesterday: price data unavailable"]
    n_news = len({it["link"] for items in groups.values() for it in items})
    lines.append(f"{n_news} related headlines")
    if events:
        nxt = events[0]
        lines.append(f"Next 7 days: {len(events)} high-impact US events. Next: {nxt['title']} {vn(nxt['time'])}")
    else:
        lines.append("Next 7 days: no high-impact US events found")
    return "\n".join(lines)


def main():
    now = datetime.now(timezone.utc)
    # Yesterday's completed UTC day (07:00 → 07:00 Vietnam time), matching Binance daily candles.
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)

    moves = daily_moves()
    items, failed = news()
    groups = group_headlines(items, day_start, day_start + timedelta(days=1))
    upcoming = upcoming_headlines(items, now - timedelta(days=3))
    events, reach, cal_failed = calendar(now)
    if len(cal_failed) == len(CALENDAR_FEEDS):
        failed.append("economic calendar")

    report = render(now, day_start, moves, groups, events, reach, upcoming, failed)
    with open(os.environ.get("REPORT_PATH", "EVENTS.md"), "w") as f:
        f.write(report)
    notify_path = os.environ.get("NOTIFY_PATH")
    if notify_path:
        with open(notify_path, "w") as f:
            f.write(push_text(moves, events, groups))
    print(report)
    return 1 if all("error" in m for m in moves.values()) and not items and not events else 0


if __name__ == "__main__":
    sys.exit(main())
