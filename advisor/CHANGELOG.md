# Rule changes

Record every change to `app/config.py` or `app/engine.py` here and bump `RULES_VERSION`,
so the track record can be compared fairly before and after (URS N07).

## 1.3.0 — 20 Nov 2026
- New **Spot** tab for coins you buy on the spot market (no leverage, no shorting). NEAR and ZEC are put in the first time; you add
  coins by typing them (max `SPOT_MAX_COINS` = 20) and remove them with ×. Removed default coins do not come back.
- **Low / high hours:** from `SPOT_DAYS` (90) days of 1h candles, the share of Vietnam days (00:00–24:00) on which the day's lowest and
  highest 1h candle was in each hour, with the three most common hours and how often the low came before the high. Stored in
  `spot_stats`, rebuilt every `SPOT_STATS_REFRESH_HOURS` (12). Each hour has only a few hits in 90 days, so the page calls it a tendency; it is
  only called "strong" when the top three hours hold at least 35% of both lows and highs (random would give 12.5%).
- **Buy-today verdict** (every 5 minutes): score from 6 checks (1h EMA 20/50 trend 20, price above EMA 50 15, RSI 40–65 15, price in the
  cheaper part of today's range 20, not stretched from EMA 20 15, volume 15). Avoid = price under a falling short-term trend; Buy now = score
  >= `SPOT_BUY_MIN_SCORE` (60), not in the top 30% of the range, RSI < 70; Buy on dip = limit order in a zone (RSI hot, price high in the range, or a
  stop that would be too wide); otherwise Wait. Stop = lowest of the last 12 1h lows, today's low and 1.5 ATR below the entry, minus 0.1 ATR; targets
  1.5R and 3R; a stop more than 8% away turns the call into Wait. Size comes from the Settings risk %. **Not backtested.**
- Spot plan (6 points) is shown at the bottom of the tab. Calls from this tab are not recorded in Active calls.
- `history_1h` now also returns the high and low of each candle. API: `GET/POST /api/spot`, `DELETE /api/spot/{symbol}`.
- Tests: `python -m tests.test_spot`.

## 1.2.4 — 16 Nov 2026
- New **Hours** tab: for each coin, which Vietnam-time hours carry the most trading volume, how aggressive the buyers are
  (taker-buy share) and how the price moved, from one year of 1h Binance candles (`HOURLY_DAYS`). Signal rules are unchanged.
- The server builds the profiles in the background (BTC, watchlist, top-volume, Watch and screener coins, at most
  `HOURLY_MAX_COINS`; any other USDT pair can be requested on the tab), one coin at a time, and stores only the 24-hour summary in
  the `hourly_profile` table. Profiles older than `HOURLY_REFRESH_DAYS` (7) are rebuilt. Opening the tab reads the database only.
- Each hour is labelled with what is scheduled then (Asia / Europe / US sessions, funding at 07:00, 15:00 and 23:00, US data and
  stock open, FOMC, options expiry). These are labels of coincidence, not proof of cause; US events move by one hour between
  summer and winter time.
- Quality guards: volume patterns are shown with a stability score (first vs second half of the period). An hour's average price
  move is called notable only when |t| >= `HOURLY_NOTABLE_T` (3.0), because 24 hours are tested and most differences are noise.
- Fixes found while checking the screens in a browser: Journal tag boxes were stacked in a narrow column; the screener's
  project checklist printed "true" instead of the check name.
- Tests: `python -m tests.test_hourly`.

## 1.2.3 — 16 Nov 2026
- Active calls track record: new **"Why calls win or lose"** review under the track record. Signal rules are unchanged; only
  what is recorded and reported changes.
- Each new call now stores a snapshot when it opens (which checks were met, BTC regime, setup kind) and, while it is open, the best
  and worst the price went against the entry in R (`mfe_r`, `mae_r`, from 15m candles). Calls opened before this have no snapshot.
- Results are also shown **net of fees and slippage** (`CALL_COST_PCT`: spot 0.30%, futures 0.20% of entry, same as Watch,
  divided by the stop distance). The stored `result_r` is unchanged and still ignores costs.
- Review: win rate, average and total R, profit factor, worst drop, longest losing run; results by call, market, confidence,
  BTC regime, setup, how it ended, time held, time of day and rules version; results with each check met vs not met; share of
  losers that reversed after reaching 1R, never worked, or were stopped within an hour; plain-language findings. Groups under
  `REVIEW_MIN_GROUP` (10) calls are marked "few calls"; under `REVIEW_MIN_ACT` (30) closed calls the findings say it is too early to
  change rules. Each closed call gets a one-line reason ("Wrong from the start", "Reversal", ...). Nothing changes a rule by itself.
- New endpoint `/api/review`; `/api/calls/history` now includes `why` and `net_r`.
- Still simulated: a call is assumed filled at its entry price at once. Tests: `python -m tests.test_review`.

## 1.2.2 — 16 Nov 2026
- Altcoin screener: new **Dilution** column and a **Project checklist** card. The server asks CoinGecko once a day for
  market cap, fully diluted value (FDV) and supply of the listed coins. Flag: High when FDV / market cap ≥ 4 or under 30% of
  the supply circulates; Medium when ≥ 2 or under 60%; otherwise Low (`FUND_*` in `config.py`). **A High flag turns a Buy into
  Wait.** Supply unknown (CoinGecko unreachable, or coin not found) leaves the verdict but adds a "check unlocks yourself" note.
  Unlock dates, vesting and news cannot be read automatically: they are listed as things to check yourself, with links.
- New Telegram alert: a coin with verdict Buy or Wait for pullback whose price falls into its buy-in zone (and is still above
  the level that invalidates the idea). At most once per coin per `ZONE_ALERT_COOLDOWN_DAYS` (3). Checked every 15 minutes.
- Journal: optional planned stop-loss (gives each result in R), fees (results are net of fees), link to an app call, and tags for
  mistakes (`JOURNAL_MISTAKE_TAGS`) or good habits. New summary: win rate, expectancy, profit factor, average win / loss, results
  with and without tagged mistakes, per-tag totals, and your average R against the app's calls you linked. Existing databases
  are upgraded automatically (new columns). `/api/journal` now returns `{rows, stats, tags}`.
- Tests: `python -m tests.test_extras`.

## 1.2.1 — 16 Nov 2026
- Altcoin screener: new **Buy?** column with a verdict per coin. **Buy** = stage Recovering, score ≥ `SCREEN_BUY_MIN_SCORE` (70)
  and price at most `SCREEN_BUY_MAX_ABOVE_ZONE` (8%) above the top of the buy-in zone. **Wait for pullback** = Recovering
  with a good score but price has run above the zone. **Wait** = Basing, or Recovering with a lower score.
  Stage, score and ranking are unchanged. Price and volume only: check unlocks and news before buying.

## 1.2 — 30 Sep 2026
- New **Watch** tab for volatile coins you type in (up to 20). Day-trade rules (`engine.py`) are unchanged; the
  version is bumped because there is a new rule set with its own track record.
- Every watched coin always shows a side: Long / Short on futures, Buy / Sell on spot (same direction; Sell means
  sell if held, or stay out). Analysis runs 5 s after every 5-minute candle close.
- Score −100..+100 on 5m candles: EMA 9/21, MACD, RSI 14, position in the 4h/24h range, volume pressure, 1h trend.
  ADX decides the mode (≤ 20 ranging: contrarian to the range edge; ≥ 25 trending: follow the trend; blended between).
  Weights in `WATCH_WEIGHTS_TREND` / `WATCH_WEIGHTS_RANGE`. Strength: Strong ≥ 50, Medium ≥ 25, else Weak.
- The side flips only when the score passes ±10 on the other side and the side has been held 3 candles (15 min).
- Every side change opens a graded signal: stop 1 ATR (at least 0.3%), T1 = 1R (take half, stop to entry), T2 = 2R.
  Stop and targets are never moved. Official result at 1 hour; a signal still open is followed until 2 hours
  (result recorded separately, "late win" flagged). Checked on 1m candles; a candle touching both stop and target
  counts as the stop. Results are in R after fees (futures 0.10%, spot 0.20% round trip) and 0.10% slippage.
- Each signal also records the exact opposite trade as a baseline. Statistics by side, strength, market type, coin,
  time of day and rules version; groups under 30 signals are marked "not enough data".
- A coin averaging below 0R over its last 20 signals is shown as Weak; a banner appears when the last 50 average below 0R.
- Optional Telegram alert when a side flips at Strong (`WATCH_ALERT_MIN_STRENGTH`, `None` turns it off).
- Not included: funding payments in futures results (a 2 h signal rarely crosses one).
- Tests: `python -m tests.test_watch` and `python -m tests.test_watcher` (fake candles, temporary database).

## 1.1.1 — 29 Sep 2026
- New backtest (`bash backtest.sh`): replays the day-trade rules over the last 90 days and compares four
  versions, net of fees.
- New optional rules, all **off** so live calls are unchanged until the backtest supports them:
  stop at least 0.5 ATR beyond the setup level (`STOP_LEVEL_BUFFER_ATR`), no entry once price is more than
  1 ATR past the setup level (`CHASE_MAX_ATR`), only trade with the BTC trend (`REGIME_REQUIRED`).
- Terminal no longer prints a line for every Binance request.

## 1.1 — 29 Sep 2026
- New **Altcoin screener** tab, updated once a day after the daily close. It scans every Binance USDT altcoin
  with at least US$10M 24h volume and a year of history, keeps those 50% or more below their 1-year high,
  and grades them: Recovering (above a rising 50-day average with a higher low), Basing (higher low, no
  uptrend yet). Coins still making new lows are left out. Score from 7 checks; buy-in zone and the level
  that proves the idea wrong. Telegram alert when a coin newly turns Recovering.
- Daily candles kept: 400 (was 365) so a full year is always available.

## 1.0.1 — 29 Sep 2026
- Fix: on Python 3.9 (the Mac's built-in Python) every refresh failed with "attached to a different loop".
  The request limiter and refresh lock are now created inside the running event loop. Signal rules unchanged.

## 1.0 — 29 Sep 2026
- First version, following URS section 4.
- Day trade: 4h direction, 1h setup, 15m entry timing. Refresh 5 s after every 15-minute close.
- Checks: trend (EMA 20/50), setup (pullback to EMA 20/VWAP or swing breakout), RSI 14, MACD 12/26/9,
  volume ≥ 1.5× average, 15m timing, BTC regime, futures funding and open interest.
- Stop beyond the last 1h swing, capped at 2 × ATR 14. T1 = 1.5R (take half, stop to entry), T2 = 3R or the next level.
- Calls need reward-to-risk ≥ 1.5 and confidence ≥ 50.
- Top gainers: no long when up > 30% with 1h RSI > 80; shorts only after a lower high on falling volume.
- Long term: Accumulate / Hold / Reduce from the 200-day average, 50/200 cross, 90-day strength vs BTC, weekly trend.
