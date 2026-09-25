#!/usr/bin/env python3
"""Crypto flows report: exchange net flows, spot ETF flows and company treasuries.

Runs every 15 minutes. Each run:
- Exchanges (DefiLlama, wallets published by each exchange): when a new daily snapshot
  appears, computes the previous UTC day's net flow = change in coin amounts x current
  price, so price moves do not count as flows.
- ETFs (Farside Investors daily tables, US$m): latest trading day's flow per fund.
- Companies (CoinGecko public treasuries): changes in BTC / ETH holdings.
It sends an instant alert (once per event) when a flow crosses a threshold, and a daily
summary at 08:00 Vietnam time. State is kept in a hidden marker in the report issue.

Free: no API keys, no AI. Uses only the Python standard library.
"""

import gzip
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

VN = timezone(timedelta(hours=7), "ICT")
USER_AGENT = "Mozilla/5.0 (compatible; cryptuch-flows/1.0; +https://github.com/uchihathai105/cryptuch)"

EXCHANGES = {  # DefiLlama slug -> display name (largest tracked exchanges)
    "binance-cex": "Binance", "okx": "OKX", "bitfinex": "Bitfinex", "bybit": "Bybit",
    "gate": "Gate", "bitget": "Bitget", "gemini": "Gemini", "htx": "HTX",
}
ETF_PAGES = {"BTC": "https://farside.co.uk/btc/", "ETH": "https://farside.co.uk/eth/"}
TREASURIES = {"BTC": "bitcoin", "ETH": "ethereum"}

EXCHANGE_ALERT_USD = 100e6
ETF_ALERT_USD_M = {"BTC": 200, "ETH": 100}
COMPANY_ALERT_UNITS = {"BTC": 1000, "ETH": 10000}
# A one-day change bigger than this share of an exchange's holdings is almost always
# DefiLlama adding or removing wallets, not a real flow.
SUSPECT_SHARE = 0.10
DAILY_HOUR_VN = 8
MAX_ALERT_LOG = 15


# --- fetching ------------------------------------------------------------------


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return gzip.decompress(raw) if resp.headers.get("content-encoding") == "gzip" else raw


def fetch_json(url):
    return json.loads(fetch(url))


# --- formatting ----------------------------------------------------------------


def usd(x, signed=True):
    sign = ("+" if x >= 0 else "−") if signed else ("−" if x < 0 else "")
    a = abs(x)
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if a >= size:
            return f"{sign}${a / size:,.2f}{unit}"
    return f"{sign}${a:,.0f}"


def usd_m(x):
    """Farside figures are already in US$ millions."""
    return usd(x * 1e6)


def units(x, coin):
    return f"{'+' if x >= 0 else '−'}{abs(x):,.0f} {coin}"


def utc_day(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%d %b")


# --- exchanges -----------------------------------------------------------------


def exchange_flow(protocol):
    """Net flow over the latest complete UTC day from a DefiLlama protocol payload."""
    daily = [p for p in protocol["tokens"] if p["date"] % 86400 == 0]
    usd_points = {p["date"]: p["tokens"] for p in protocol["tokensInUsd"]}
    if len(daily) < 2:
        return None
    prev, last = daily[-2], daily[-1]
    if last["date"] - prev["date"] != 86400 or last["date"] not in usd_points:
        return None
    usd_now = usd_points[last["date"]]
    flows, untracked = {}, 0.0
    for tok, amount in last["tokens"].items():
        if tok not in prev["tokens"]:
            untracked += usd_now.get(tok, 0.0)
            continue
        if not amount:
            continue
        price = usd_now.get(tok, 0.0) / amount
        flows[tok] = (amount - prev["tokens"][tok]) * price
    total_usd = sum(usd_now.values())
    net = sum(flows.values())
    top = sorted(flows.items(), key=lambda kv: -abs(kv[1]))[:4]
    return {
        "date": last["date"], "net": net, "holdings": total_usd,
        "top": [[t, round(v)] for t, v in top if abs(v) >= 1e6],
        "suspect": bool(total_usd) and abs(net) > SUSPECT_SHARE * total_usd,
    }


def update_exchanges(state, today_ts, log):
    tvl_seen = state.setdefault("tvl", {})
    results = state.setdefault("exchanges", {})
    for slug, name in EXCHANGES.items():
        have = results.get(slug, {}).get("date", 0)
        try:
            tvl = float(fetch(f"https://api.llama.fi/tvl/{slug}", timeout=20))
        except Exception as e:
            log.append(f"{name}: holdings check failed ({e})")
            continue
        # Download the big payload only when today's daily snapshot is missing and the
        # holdings figure has moved since the last look (a new snapshot has landed).
        if have >= today_ts or (tvl_seen.get(slug) == tvl and have):
            continue
        tvl_seen[slug] = tvl
        try:
            flow = exchange_flow(fetch_json(f"https://api.llama.fi/protocol/{slug}"))
        except Exception as e:
            log.append(f"{name}: flow download failed ({e})")
            continue
        if flow and flow["date"] > have:
            results[slug] = flow


# --- ETFs ----------------------------------------------------------------------


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.tables[-1].append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def farside_number(cell):
    cell = cell.replace(",", "").strip()
    if cell in ("", "-"):
        return None
    neg = cell.startswith("(") and cell.endswith(")")
    value = float(cell.strip("()"))
    return -value if neg else value


def parse_farside(html):
    """Last 5 trading days: [{'date', 'total', 'funds': {ticker: US$m}, 'pending': [...]}, ...]."""
    parser = _Tables()
    parser.feed(html)
    table = max(parser.tables, key=len)
    tickers = next(r for r in table if len(r) > 3 and r[0] == "" and r[1])[1:-1]
    days = []
    for row in table:
        if not re.fullmatch(r"\d{1,2} \w{3} \d{4}", row[0]):
            continue
        funds, pending = {}, []
        for t, cell in zip(tickers, row[1:-1]):
            v = farside_number(cell)
            if v is None:
                pending.append(t)
            else:
                funds[t] = v
        days.append({"date": row[0], "total": farside_number(row[-1]), "funds": funds, "pending": pending})
    return days[-5:]


def update_etfs(state, log):
    etfs = state.setdefault("etf", {})
    for coin, url in ETF_PAGES.items():
        try:
            etfs[coin] = parse_farside(fetch(url, timeout=30).decode("utf-8", "replace"))
        except Exception as e:
            log.append(f"{coin} ETF flows unavailable ({e})")


# --- companies -----------------------------------------------------------------


def update_treasuries(state, log):
    """Returns {coin: [(company, change, new_holdings), ...]} since the previous run."""
    seen = state.setdefault("treasury", {})
    changes = {}
    for coin, cg in TREASURIES.items():
        try:
            data = fetch_json(f"https://api.coingecko.com/api/v3/companies/public_treasury/{cg}")
        except Exception as e:
            log.append(f"{coin} company holdings unavailable ({e})")
            continue
        now = {c["name"]: round(c["total_holdings"], 2) for c in data["companies"]}
        before = seen.get(coin)
        if before:
            changes[coin] = [(n, h - before.get(n, 0.0), h) for n, h in now.items()
                             if abs(h - before.get(n, 0.0)) >= 1]
        seen[coin] = now
        state.setdefault("treasury_total", {})[coin] = data["total_holdings"]
    return changes


# --- alerts, report ------------------------------------------------------------


def collect_alerts(state, company_changes):
    """New alert lines (each event alerts once); remembers them in state."""
    done = set(state.setdefault("alerted", []))
    alerts = []

    def add(key, text):
        if key not in done:
            done.add(key)
            alerts.append(text)

    for slug, f in state.get("exchanges", {}).items():
        if abs(f["net"]) >= EXCHANGE_ALERT_USD and not f["suspect"]:
            top = ", ".join(f"{t} {usd(v)}" for t, v in f["top"][:2])
            kind = "inflow" if f["net"] > 0 else "outflow"
            add(f"ex:{slug}:{f['date']}",
                f"{EXCHANGES[slug]} net {kind} {usd(f['net'])} on {utc_day(f['date'] - 86400)} (UTC)"
                + (f" · {top}" if top else ""))
    for coin, days in state.get("etf", {}).items():
        if days and days[-1]["total"] is not None and abs(days[-1]["total"]) >= ETF_ALERT_USD_M[coin]:
            d = days[-1]
            kind = "inflow" if d["total"] > 0 else "outflow"
            add(f"etf:{coin}:{d['date']}", f"{coin} ETFs net {kind} {usd_m(d['total'])} on {d['date']}")
    for coin, rows in company_changes.items():
        for name, delta, held in rows:
            if abs(delta) >= COMPANY_ALERT_UNITS[coin]:
                verb = "added" if delta > 0 else "sold"
                add(f"co:{coin}:{name}:{held}", f"{name} {verb} {abs(delta):,.0f} {coin} (now {held:,.0f})")

    state["alerted"] = sorted(done)[-300:]
    stamp = datetime.now(VN).strftime("%d %b %H:%M")
    state["alert_log"] = ([f"{stamp} · {a}" for a in alerts] + state.get("alert_log", []))[:MAX_ALERT_LOG]
    return alerts


def daily_summary(state):
    parts = []
    etf = state.get("etf", {})
    etf_bits = [f"{c} {usd_m(d[-1]['total'])} ({d[-1]['date'][:6]})" for c, d in etf.items()
                if d and d[-1]["total"] is not None]
    if etf_bits:
        parts.append("ETFs: " + ", ".join(etf_bits))
    ex = sorted(state.get("exchanges", {}).items(), key=lambda kv: -abs(kv[1]["net"]))
    ex_bits = [f"{EXCHANGES[s]} {usd(f['net'])}" for s, f in ex if not f["suspect"]][:4]
    if ex_bits:
        parts.append("Exchanges 24h: " + ", ".join(ex_bits))
    base, now = state.get("treasury_at_daily", {}), state.get("treasury", {})
    co_bits = []
    for coin, holdings in now.items():
        for name, held in holdings.items():
            delta = held - base.get(coin, {}).get(name, held)
            if abs(delta) >= 1:
                co_bits.append((abs(delta) / COMPANY_ALERT_UNITS[coin], f"{name} {units(delta, coin)}"))
    if co_bits:
        parts.append("Companies: " + ", ".join(t for _, t in sorted(co_bits, reverse=True)[:3]))
    elif base:
        parts.append("Companies: no holdings changes")
    state["treasury_at_daily"] = now
    return "\n".join(parts) or "No flow data available today."


def render(state, now, log):
    lines = [
        "# Crypto Flows Report",
        "",
        f"_Updated {now.astimezone(VN):%Y-%m-%d %H:%M} Vietnam time · checks every 15 minutes · free sources: "
        "DefiLlama (exchange wallets), Farside Investors (ETF flows), CoinGecko (company treasuries)_",
        "",
        "## 🚨 Recent alerts",
        "",
        f"Thresholds: exchange net flow ≥ {usd(EXCHANGE_ALERT_USD, False)}/day · BTC ETFs ≥ "
        f"${ETF_ALERT_USD_M['BTC']}M/day · ETH ETFs ≥ ${ETF_ALERT_USD_M['ETH']}M/day · company change ≥ "
        f"{COMPANY_ALERT_UNITS['BTC']:,} BTC or {COMPANY_ALERT_UNITS['ETH']:,} ETH",
        "",
    ]
    lines += [f"- {a}" for a in state.get("alert_log", [])] or ["_No alerts yet._"]

    lines += ["", "## 🏦 Exchange net flows (previous UTC day)", "",
              "| Exchange | Day (UTC) | Net flow | Biggest moves | Holdings |", "|---|---|---:|---|---:|"]
    for slug, name in EXCHANGES.items():
        f = state.get("exchanges", {}).get(slug)
        if not f:
            lines.append(f"| {name} | – | _no data yet_ | | |")
            continue
        top = ", ".join(f"{t} {usd(v)}" for t, v in f["top"]) or "–"
        flag = " ⚠️ likely wallet-list change" if f["suspect"] else ""
        lines.append(f"| {name} | {utc_day(f['date'] - 86400)} | **{usd(f['net'])}**{flag} | {top} "
                     f"| {usd(f['holdings'], False)} |")
    lines += ["", "Positive = coins moved **into** the exchange (often read as selling pressure); negative = "
              "moved **out** (often read as holding). Stablecoin inflows can mean buying power arriving. "
              "Computed from coin amounts, so price moves are excluded."]

    for coin, days in state.get("etf", {}).items():
        lines += ["", f"## 📈 {coin} spot ETF flows (US$, last 5 trading days)", ""]
        if not days:
            lines.append("_No data._")
            continue
        tickers = sorted({t for d in days for t in d["funds"]},
                         key=lambda t: -max(abs(d["funds"].get(t, 0)) for d in days))[:6]
        lines += ["| Day | Total | " + " | ".join(tickers) + " | Not reported yet |",
                  "|---|---:|" + "---:|" * len(tickers) + "---|"]
        for d in days:
            total = usd_m(d["total"]) if d["total"] is not None else "–"
            cells = " | ".join(usd_m(d["funds"][t]) if t in d["funds"] else "–" for t in tickers)
            lines.append(f"| {d['date']} | **{total}** | {cells} | {', '.join(d['pending']) or '–'} |")

    lines += ["", "## 🏢 Company treasuries (CoinGecko)", ""]
    for coin, holdings in state.get("treasury", {}).items():
        total = state.get("treasury_total", {}).get(coin, sum(holdings.values()))
        top = sorted(holdings.items(), key=lambda kv: -kv[1])[:8]
        lines.append(f"**{coin}** · public companies hold {total:,.0f} {coin} in total. Top holders: "
                     + ", ".join(f"{n} {h:,.0f}" for n, h in top))
        lines.append("")

    if log:
        lines += ["_Issues this run: " + "; ".join(log) + "_", ""]
    lines += ["> ℹ️ Exchange figures cover only wallets each exchange has published (e.g. Binance proof of "
              "reserves); ETF figures are published by Farside after each US trading day and fill in as "
              "funds report. This is data, not financial advice.", ""]
    return "\n".join(lines)


def load_state(path):
    if path and os.path.exists(path):
        m = re.search(r"<!-- flows-state: (\{.*?\}) -->", open(path).read(), re.S)
        if m:
            return json.loads(m.group(1))
    return {}


def main():
    now = datetime.now(timezone.utc)
    today_ts = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    state = load_state(os.environ.get("PREV_REPORT_PATH"))
    log = []

    update_exchanges(state, today_ts, log)
    update_etfs(state, log)
    company_changes = update_treasuries(state, log)
    alerts = collect_alerts(state, company_changes)

    vn_now = now.astimezone(VN)
    daily = ""
    due = vn_now.hour >= DAILY_HOUR_VN and state.get("daily_sent") != vn_now.strftime("%Y-%m-%d")
    if due or os.environ.get("FORCE_DAILY") == "true":
        daily = daily_summary(state)
        state["daily_sent"] = vn_now.strftime("%Y-%m-%d")

    # "-->" inside a company name would end the HTML comment early; \u003e is the same JSON string.
    blob = json.dumps(state, separators=(",", ":")).replace("-->", "--\\u003e")
    report = render(state, now, log) + f"\n<!-- flows-state: {blob} -->\n"
    with open(os.environ.get("REPORT_PATH", "FLOWS.md"), "w") as f:
        f.write(report)
    with open(os.environ.get("ALERT_PATH", "FLOWS_ALERT.txt"), "w") as f:
        f.write("\n".join(alerts))
    with open(os.environ.get("DAILY_PATH", "FLOWS_DAILY.txt"), "w") as f:
        f.write(daily)
    print(report[: report.index("<!-- flows-state")])
    print(f"Alerts: {alerts or 'none'}\nDaily: {daily or '(not due)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
