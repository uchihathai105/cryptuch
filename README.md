# cryptuch

Automated market report for **BTC, ETH and BNB**, refreshed every 10 minutes by GitHub Actions.

Each run:
1. Pulls the 24h ticker and 300 hourly candles for each coin from Binance's public market-data API (no API key needed).
2. Computes price, 24h / 7d change, 24h volume vs. its 7-day average, MA7 / MA25 / MA99, Bollinger Bands (20, 2), MACD (12, 26, 9) and RSI(14).
3. Scores six factors per coin, gives a **BUY / SELL / HOLD** signal, and explains the reasoning factor by factor (see "How the signal works" in the report).
4. Writes the report to the open issue titled **"Crypto Market Report"** (created on the first run) and to the run summary in the Actions tab.

## Run it

- Automatically: `.github/workflows/crypto-report.yml` runs on a `*/10 * * * *` schedule once it is on the default branch.
- Manually: Actions → Crypto Report → Run workflow.
- Locally: `python3 report.py` (Python 3, standard library only). The output goes to `REPORT.md`.

> ⚠️ The signals are simple technical indicators, not financial advice.
