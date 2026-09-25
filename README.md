# cryptuch

Automated market report for **BTC, ETH and BNB**, refreshed every 10 minutes by GitHub Actions.

Each run:
1. Pulls the 24h ticker and 300 hourly candles for each coin from Binance's public market-data API (no API key needed).
2. Computes price, 24h / 7d change, 24h volume vs. its 7-day average, MA7 / MA25 / MA99, Bollinger Bands (20, 2), MACD (12, 26, 9) and RSI(14).
3. Scores six factors per coin, gives a **BUY / SELL / HOLD** signal, and explains the reasoning factor by factor (see "How the signal works" in the report).
4. Writes the report to the open issue titled **"Crypto Market Report"** (created on the first run) and to the run summary in the Actions tab.
5. Sends a push notification to your phone through [ntfy](https://ntfy.sh) when any coin's signal changes (for example BTC HOLD → BUY).

## Phone notifications

1. Install the free **ntfy** app ([iOS](https://apps.apple.com/app/ntfy/id1625396347), [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)). No account is needed.
2. Pick a hard-to-guess topic name (anyone who knows it can read your notifications) and subscribe to it in the app.
3. Add it as a repository secret: Settings → Secrets and variables → Actions → New repository secret, name `NTFY_TOPIC`.
4. Test it: Actions → Crypto Report → Run workflow, tick **"Send a push notification with the current signals"**.

## Run it

- Automatically: `.github/workflows/crypto-report.yml` runs on a `*/10 * * * *` schedule once it is on the default branch.
- Manually: Actions → Crypto Report → Run workflow.
- Locally: `python3 report.py` (Python 3, standard library only). The output goes to `REPORT.md`.

> ⚠️ The signals are simple technical indicators, not financial advice.
