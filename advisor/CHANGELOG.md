# Rule changes

Record every change to `app/config.py` or `app/engine.py` here and bump `RULES_VERSION`,
so the track record can be compared fairly before and after (URS N07).

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
