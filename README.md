# cryptuch

Automated market reports for **BTC, ETH, BNB, XRP, SOL, SUI, NEAR and ZEC**, run by GitHub Actions and started on schedule by cron-job.org.

Each run:
1. Pulls the 24h ticker and 300 hourly candles for each coin from Binance's public market-data API (no API key needed).
2. Computes price, 24h / 7d change, 24h volume vs. its 7-day average, MA7 / MA25 / MA99, Bollinger Bands (20, 2), MACD (12, 26, 9) and RSI(14).
3. Scores six factors per coin, gives a **BUY / SELL / HOLD** signal, and explains the reasoning factor by factor (see "How the signal works" in the report).
   A **short-term view on 15-minute candles** (last-hour change, RSI, price vs MA20 ≈ 5 hours, MACD momentum → 🟢 bullish / 🔴 bearish / ⚪ mixed) is shown next to it for timing entries; it is **not** part of the score.
   - **Act now or wait:** a BUY is shown as **🟡 BUY · wait for pullback** when price is stretched (%B > 0.8 or RSI > 60), volume is light or 15m momentum is turning down, with a trade plan: pullback zone (MA7 / Bollinger middle), stop (below MA25 / middle band) and 1.5R / 3R targets. SELL mirrors this (**🟠 wait for bounce** when %B < 0.2 or RSI < 40, so it never says to sell into the low). Labels carry Vietnamese captions (MUA / MUA · chờ điều chỉnh / GIỮ / BÁN / BÁN · chờ hồi) and each plan has a Vietnamese **Gợi ý** line.
   - **⚡ Intraday scalping (long only):** a 15m LONG setup when the 1h trend is up (price above 1h MA25, signal not SELL), 15m is bullish with RSI 40–68, and there is a trigger (fresh 15m MACD cross up or a bounce off the 15m MA20). Stop just under the last 2 hours' low (skipped if wider than 2.5%), targets 1R / 2R capped at the 1h upper band. A new setup sends a high-priority **"Scalp setup"** push.
4. Writes the report to the open issue titled **"Crypto Market Report"** (created on the first run) and to the run summary in the Actions tab.
5. Sends push notifications through [ntfy](https://ntfy.sh) only when there is something to act on: a routine summary of the coins with a BUY / SELL signal or an active scalp setup, at most every `SUMMARY_MINUTES` (default 10, to stay under ntfy's free daily limit; nothing is sent while every coin is HOLD), plus immediate high-priority pushes for a changed signal ("Crypto signal CHANGED", e.g. BTC HOLD → BUY) or a new scalp setup ("Scalp setup").

## Daily events report

Every day at **08:00 Vietnam time**, `.github/workflows/events-report.yml` runs `events.py` and updates the issue **"Crypto Events Report"**, then sends a short ntfy push:

- **Yesterday:** each coin's daily move (Binance daily candle: open, close, change, range, volume vs 7-day average) and crypto news headlines from that day (CoinDesk, Cointelegraph, Decrypt, The Block RSS), grouped by coin (BTC, ETH, BNB, XRP, SOL, SUI, NEAR, ZEC) and market-wide topics.
- **Next 7 days:** scheduled high- and medium-impact US economic events (Forex Factory's public calendar: Fed, CPI, jobs, GDP…) shown in Vietnam time, plus recent headlines that mention upcoming events for these coins (upgrades, unlocks, decisions, launches).

It is free and rule-based (keyword matching, no AI), so headlines can be loosely related and it cannot judge whether an event is bullish or bearish.

## Flows report

`.github/workflows/flows-report.yml` runs `flows.py` every 15 minutes and keeps the issue **"Crypto Flows Report"** up to date:

- **Exchange net flows** (Binance, OKX, Bitfinex, Bybit, Gate, Bitget, Gemini, HTX) from the wallets each exchange publishes, via DefiLlama. The previous UTC day's net flow is computed from changes in coin amounts × current price, so price moves are not counted as flows.
- **Spot ETF flows** for BTC and ETH per fund, from Farside Investors' daily tables (fills in as funds report after each US trading day).
- **Company treasuries** (Strategy, Metaplanet, BitMine…) from CoinGecko's public treasury list, with holdings changes.

Pushes: an instant **"Crypto flow alert"** (once per event) when an exchange's net flow is ≥ $100M/day, BTC ETFs ≥ $200M/day, ETH ETFs ≥ $100M/day, or a company adds or sells ≥ 1,000 BTC / 10,000 ETH; and a **"Daily crypto flows"** summary on the first run after 08:00 Vietnam time. Free: public sources, no API keys, no AI.

## Phone notifications

**Quiet hours:** the price and flows reports send no pushes from **02:00 to 06:00 Vietnam time**. The report issues still update; flow alerts found in that window stay listed under "Recent alerts".

1. Install the free **ntfy** app ([iOS](https://apps.apple.com/app/ntfy/id1625396347), [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)). No account is needed.
2. Pick a hard-to-guess topic name (anyone who knows it can read your notifications) and subscribe to it in the app.
3. Add it as a repository secret: Settings → Secrets and variables → Actions → New repository secret, name `NTFY_TOPIC`.
4. Test it: Actions → Crypto Report → Run workflow, tick **"Send a test push with every coin now"**.

## Scheduling

GitHub's own `schedule:` trigger ran these workflows only about once every 4–5 hours, so the workflows have no `schedule:` and are started by three free [cron-job.org](https://cron-job.org) jobs (time zone Asia/Ho_Chi_Minh) that call the GitHub API:

| Workflow | cron-job.org schedule |
|---|---|
| `crypto-report.yml` (price report) | every 5 minutes (alerts within ~5 min; summary push at most every 10) |
| `flows-report.yml` (flows report) | every 15 minutes |
| `events-report.yml` (events report) | daily at 08:00 |

Each job sends `POST https://api.github.com/repos/uchihathai105/cryptuch/actions/workflows/<file>/dispatches` with body `{"ref":"main"}` and headers `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`, `Content-Type: application/json` and `Authorization: Bearer <token>`. The token is a fine-grained personal access token limited to this repository with **Actions: Read and write**. If the reports stop, check that the token has not been revoked and that the jobs are still enabled on cron-job.org.

## Run it

- Automatically: see Scheduling above.
- Manually: Actions → pick a report → Run workflow.
- Locally: `python3 report.py` (Python 3, standard library only). The output goes to `REPORT.md`.

> ⚠️ The signals are simple technical indicators, not financial advice.
