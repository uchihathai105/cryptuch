# Rule changes

Record every change to `app/config.py` or `app/engine.py` here and bump `RULES_VERSION`,
so the track record can be compared fairly before and after (URS N07).

## 1.0 — 29 Sep 2026
- First version, following URS section 4.
- Day trade: 4h direction, 1h setup, 15m entry timing. Refresh 5 s after every 15-minute close.
- Checks: trend (EMA 20/50), setup (pullback to EMA 20/VWAP or swing breakout), RSI 14, MACD 12/26/9,
  volume ≥ 1.5× average, 15m timing, BTC regime, futures funding and open interest.
- Stop beyond the last 1h swing, capped at 2 × ATR 14. T1 = 1.5R (take half, stop to entry), T2 = 3R or the next level.
- Calls need reward-to-risk ≥ 1.5 and confidence ≥ 50.
- Top gainers: no long when up > 30% with 1h RSI > 80; shorts only after a lower high on falling volume.
- Long term: Accumulate / Hold / Reduce from the 200-day average, 50/200 cross, 90-day strength vs BTC, weekly trend.
