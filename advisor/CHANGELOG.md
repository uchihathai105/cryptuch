# Rule changes

Record every change to `app/config.py` or `app/engine.py` here and bump `RULES_VERSION`,
so the track record can be compared fairly before and after (URS N07).

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
