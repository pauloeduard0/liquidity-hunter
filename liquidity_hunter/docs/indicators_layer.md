# Indicators layer

Extracted from `CLAUDE.md` (2026-08-29) to keep that file under its size limit.

### Indicators layer (`liquidity_hunter/indicators`)

- **`indicators/volume_delta.py`** — `volume_delta(candle) -> float`
  computes `2 * taker_buy_volume - volume` (net taker buy/sell aggression
  for that candle, ranging from `-volume` to `+volume`);
  `volume_delta_series(candles) -> list[float]` applies it across a series,
  1:1 aligned with `candles`. `cumulative_volume_delta(candles) -> list[float]`
  is the running sum (the **CVD** series): rising = buyers have been the
  aggressors over the run, falling = sellers. All three are re-exported from
  `liquidity_hunter.indicators`.
- **`indicators/supertrend.py`** — `supertrend(candles, *, periods=10,
  multiplier=3.0, change_atr=True) -> list[SupertrendPoint]`, a faithful port
  of the classic TradingView "Supertrend" Pine study: bands at
  `hl2 ± multiplier × ATR`, each ratcheting only in the trend's direction
  (the floor rises while the previous close held above it, the ceiling falls
  while it held below), and a trend flip when a close crosses the *previous*
  candle's opposing band. `change_atr` picks Wilder's ATR (Pine's `atr()`,
  the default) over the simple mean of true range (`sma(tr, n)`) — the
  script's "Change ATR Calculation Method?" input. The result starts at the
  first candle with a defined ATR (index `periods - 1`), so it is shorter than
  `candles`; each `SupertrendPoint` (`core/domain/supertrend.py`) carries
  `timestamp`, `value` (the *active* band — floor while `BULLISH`, ceiling
  while `BEARISH`), `direction`, `flip` (the turn candle), and both raw bands.
  `true_range_series` is exposed alongside it. Descriptive only: a
  volatility-scaled trend envelope, never a buy/sell instruction — the Pine
  script's Buy/Sell labels are not rendered at all.
  Re-exported from `liquidity_hunter.indicators`. Each flip is separately
  *qualified* by `SupertrendBreakAnalyzer` (psychology layer, below).
- **`indicators/volume_profile.py`** — `volume_profile(candles, *, symbol,
  timeframe, bucket_count=100, value_area_pct=0.70, tick_size=None, …) ->
  VolumeProfile | None`: volume-at-price over a window. Each candle's volume
  is spread across the buckets its high-low range overlaps, in proportion to
  the fraction of the range in each; the taker-buy share rides the same
  distribution to give a per-band buy/sell split. POC = heaviest band, value
  area = standard Market Profile expansion outward from it, bands classified
  HVN/LVN against the mean traded band. Bucket width is **floored at the
  instrument's tick** (`infer_tick_size`, from the finest decimal precision in
  the window's OHLC): a sub-tick bucket cannot be reached by any printable
  price, which turns the true profile into a comb of spikes (measured on
  NEARUSDT: 30% overlap without the floor, 87% with). Returns `None` for an
  empty or flat window rather than raising. **Measured against a trade-level
  (aggTrades) profile, the candles already in `DashboardData` reproduce it at
  95-97% histogram overlap with the POC exact or within ~0.6 timeframe-ATR —
  no extra fetch needed.** Delta-at-price is the weak half (72-90% per-bucket
  sign agreement) and is flagged `delta_estimated`; a true footprint needs
  trade data. Full methodology and both measurements in
  `liquidity_hunter/docs/volume_profile.md`. Re-exported from
  `liquidity_hunter.indicators`.
- **`indicators/vwap.py`** — `vwap(candles, *, symbol, timeframe,
  anchor=VWAPAnchor.SESSION, anchor_timestamp=None, rolling_window=None,
  band_multipliers=(1.0, 2.0), label="") -> VWAPSeries | None`: the running
  `Σ(volume × hlc3) / Σ(volume)` from an anchor, with volume-weighted
  standard-deviation bands (accumulated first/second moments, so no re-scan).
  `anchor` selects what restarts the accumulation: `SESSION`/`WEEK`/`MONTH`
  (calendar periods on 00:00 UTC boundaries — the series then holds several
  segments, delimited by each point's `anchor_timestamp`), `EVENT` (one
  accumulation from `anchor_timestamp`), or `ROLLING` (a trailing
  `rolling_window`, defined once full). `anchored_vwap(candles, timestamp, …)`
  is the `EVENT` convenience wrapper — the reading this project cares about,
  since anchoring at a sweep or a CHoCH makes the line the break-even of the
  crowd that event drew in. Returns `None` when no reading is defined (empty
  window, anchor past the last candle, no traded volume) rather than raising.
  `typical_price(candle)` (`hlc3`) is exposed alongside it. Re-exported from
  `liquidity_hunter.indicators`.

