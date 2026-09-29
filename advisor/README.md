# Crypto Trading Advisor

A personal dashboard that reads Binance market data every 15 minutes and gives rule-based calls:

- **Day trade** (held 1–24 hours): Long / Short / Wait on USDT-M futures and Buy / Exit / Wait on spot,
  with entry zone, stop-loss, two targets, reward-to-risk, confidence and position size.
- **Long term** (months): Accumulate / Hold / Reduce with a buy-in zone.
- Coin lists: top 10 by 24h volume, top 10 gainers, and your watchlist.
- **Altcoin screener** (daily): liquid altcoins still 50%+ below their 1-year high, graded Recovering or
  Basing, with a buy-in zone and the price that would prove the idea wrong. It sees only price and volume,
  so check each project (token unlocks, news) before buying.
- Active calls are followed until a target, the stop or an exit rule ends them; results build a track record.
- Optional Telegram alerts for new calls, targets, exits and long-term rating changes.

It only reads public data. It never connects to your Binance account and never places orders.
Signals are rules, not financial advice.

## Start it

1. Open **Terminal** (Applications → Utilities → Terminal).
2. The first time, download the code:
   ```
   cd ~/Documents
   git clone https://github.com/uchihathai105/cryptuch.git
   ```
3. Start the app:
   ```
   cd ~/Documents/cryptuch/advisor
   bash run.sh
   ```
4. Your browser opens **http://localhost:8000**. The first load takes about 20–30 seconds while it downloads candles.

Keep the Terminal window open. Press **Ctrl+C** in it to stop the app. Next time, just run step 3 again.
To get updates later: `cd ~/Documents/cryptuch && git pull`.

Unlike the reports in the rest of this repo, this app runs on your own computer, not on GitHub Actions:
it needs Binance futures data, which Binance blocks from GitHub's US servers.

The first run sets up a private Python environment (1–2 minutes). If it says Python is missing, install
Python from https://www.python.org/downloads/macos/ and run it again.

## Keep it running all day

`run.sh` uses macOS `caffeinate`, so the Mac won't sleep while the app runs. The screen can still turn off.
If you close the lid of a laptop it will still sleep; your Mac mini is fine.

## Open it on your phone (same Wi-Fi)

Start it with:
```
ADVISOR_HOST=0.0.0.0 bash run.sh
```
Then on your phone open `http://<your Mac's IP>:8000` (find the IP in System Settings → Wi-Fi → Details).
Anyone on the same Wi-Fi could open it too, so only do this on your home network.

## Backtest the rules

Replays the day-trade rules on the last 90 days of Binance data for the top 20 coins, one 15-minute
close at a time, with the same exits as Active calls and trading fees included. It compares the current
rules with three stricter versions (wider stop, no chasing, only with the BTC trend):
```
bash backtest.sh            # 20 coins, 90 days, about 3–6 minutes
bash backtest.sh 30 120     # 30 coins, 120 days
```
The app can keep running meanwhile. Results are saved to `data/backtest_latest.txt`.
Funding and open interest are not replayed, and past results do not guarantee future ones.

## Telegram alerts (optional, free)

1. In Telegram, message **@BotFather**, send `/newbot`, and follow the steps. It gives you a **token**.
2. Send any message to your new bot (e.g. "hi").
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in your browser (put your token in place of `<TOKEN>`).
   Find `"chat":{"id": 123456789` — that number is your **chat id**.
4. Copy `config.local.example.json` to `config.local.json` and fill in both values:
   ```
   cp config.local.example.json config.local.json
   open -e config.local.json
   ```
5. Restart the app, go to **Settings → Send test message**.

`config.local.json` stays on your Mac. Keep the bot token private.

## Password login (optional)

Off by default, which is fine when the app only runs on your own computer. Turn it on before you put the app
on a server or open it to other devices:

- Environment variable: `ADVISOR_PASSWORD='your long password' bash run.sh`
- or add `"password": "your long password"` to `config.local.json`.

With a password set, every page and API call needs a login; a successful login lasts 30 days on that browser
(**Log out** is in the page footer). Five wrong passwords in 10 minutes lock that address out for 10 minutes.
Changing the password logs every device out. It is a single shared password, so use a long one and serve the app
over HTTPS when it is reachable from the internet.

## Settings and rules

- Account size, risk per trade (default 1%), max leverage (default 5x), max position size and watchlist:
  the **Settings** tab.
- Every rule threshold (EMA lengths, RSI ranges, stop and target sizes, minimum reward-to-risk, list filters):
  `app/config.py`. After changing a rule, add a line to `CHANGELOG.md`.
- Your data (settings, call history, journal) is in `data/advisor.db`. Delete it to start fresh.

## How often it calls Binance

About 5 seconds after every 15-minute candle close, plus one daily altcoin scan after 07:00 your time. Only new candles are downloaded after the first load:
15m every cycle, 1h hourly, 4h every 4 hours, daily and weekly once a day. A cycle uses roughly 100–450
request weight, far below Binance's limits (6,000 per minute for spot, 2,400 for futures). If Binance
ever returns a rate-limit error, the app pauses for the time Binance asks and keeps showing the last data
with a warning.

## Troubleshooting

- **"Last refresh failed … HTTP 451"**: Binance refuses the location, usually because a VPN routes you
  through the US. Turn the VPN off or pick another country.
- **Page shows "Can't reach the app server"**: the Terminal window was closed; run `bash run.sh` again from the `advisor` folder.
- **Port 8000 is busy**: `ADVISOR_PORT=8010 bash run.sh`.

## Files

| Path | What it is |
| --- | --- |
| `run.sh` | Starts the app |
| `app/config.py` | All rule settings |
| `app/engine.py` | Buy / sell / hold rules and position sizing |
| `app/service.py` | 15-minute refresh, coin lists, call tracking, alerts |
| `app/binance.py` | Binance public data and rate-limit handling |
| `web/` | The dashboard |

Charts use TradingView Lightweight Charts (Apache 2.0), included in `web/vendor/`.
