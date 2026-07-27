# Volume profile: methodology and measured fidelity

`indicators/volume_profile.py` builds a `VolumeProfile` — volume-at-price over
a window — from a plain candle series. This document records how it is
computed, how close it lands to a *true* profile rebuilt from individual
trades, and where it deliberately stops short.

## Method

1. The window's `[min(low), max(high)]` range is divided into
   `bucket_count` (default 100) equal-width bands.
2. Each candle's volume is attributed to the bands its high-low range
   overlaps, **in proportion to the fraction of that range falling in each
   band**. A candle whose whole range fits one band contributes all of its
   volume there.
3. The taker-buy share of each candle (`taker_buy_volume / volume`, the same
   basis as `indicators.volume_delta`) is carried through the same
   distribution, giving each band a `buy_volume` / `sell_volume` split.
4. The heaviest band is the **POC**. The **value area** grows outward from it,
   at each step taking whichever neighbour holds more volume, until
   `value_area_pct` (default 70%) of total volume is enclosed — the standard
   Market Profile expansion.
5. Bands are classified `HIGH_VOLUME` / `LOW_VOLUME` / `NORMAL` against the
   mean of the *traded* bands (`DEFAULT_HVN_FACTOR` 1.5, `DEFAULT_LVN_FACTOR`
   0.35).

### The tick floor

Bucket width is floored at the instrument's tick (`infer_tick_size`, read off
the finest decimal precision the window's OHLC values use, overridable via
`tick_size`).

This is not a cosmetic guard. A bucket narrower than the tick contains prices
that *cannot be printed*, so the true profile degenerates into a comb of
spikes separated by structurally empty bands, and any smooth estimate scores
badly against it no matter how good it is. Measured on NEARUSDT (tick 0.001,
two-hour range ~0.036): at a fixed 120 buckets the estimate scored **30%**
histogram overlap against the trade-level truth; with the tick floor applied
(55 buckets) the *same* estimate scored **87%**, POC exact. The first number
was measuring the bucketing, not the method.

## Fidelity vs. trade-level truth

Two measurements, both on Binance USDT-M perpetual data.

### 1. Against every individual trade (aggTrades)

The reference profile uses each trade's exact price and quantity — the true
volume-at-price. Window: 2 hours (95,094 trades BTCUSDT, 5,787 NEARUSDT).

| sub-candle | symbol | ΔPOC | overlap | VA IoU |
|---|---|---|---|---|
| 1m | BTCUSDT | exact | 86% | 67% |
| 1m | NEARUSDT | exact | 87% | 91% |
| 5m | BTCUSDT | −0.07 ATR | 81% | 67% |
| 15m | BTCUSDT | −0.07 ATR | 75% | 79% |

A 2-hour window is the *hard* case: its buckets are narrower than a single
candle's range, so the distribution assumption is doing real work. The 1h row
of this test is degenerate (two candles, no averaging) and is not reported.

### 2. Over the window the dashboard actually renders

1200 candles, 100 buckets, with 1m klines as the truth proxy (validated
above). This is the case that matters in production.

| dashboard TF | sub-candle | extra requests | ΔPOC | overlap | VA IoU |
|---|---|---|---|---|---|
| BTC 1h (50d) | **native 1h** | **0** | +0.63 ATR | **97.3%** | 96% |
| BTC 1h | 5m | 10 | exact | 98.8% | 100% |
| BTC 15m (12d) | **native 15m** | **0** | −0.42 ATR | **96.0%** | 91% |
| BTC 5m (4d) | **native 5m** | **0** | exact | **96.4%** | 100% |
| NEAR 1h (50d) | **native 1h** | **0** | exact | **96.8%** | 95% |

**The candles already in `DashboardData` are enough.** Over a wide window the
buckets are wider than any single candle's range, so the distribution error
averages out and no additional fetch buys a meaningfully different picture.
The shipped `volume_profile()` was re-validated on this basis and scores
91-95% overlap with POC within 0-2 buckets on shorter (harder) windows.

A "high fidelity" mode fetching 5m sub-candles would cost 1-10 extra requests
for +1-2 points of overlap. Not wired: the price is real and the gain is not.

### Distribution model

Uniform (fractional overlap) and triangular (peaked at the candle midpoint)
were both measured. They tie over the production window. Uniform ships: it is
simpler and does not invent a shape the data does not support.

## Scope: a recent lookback

`load_dashboard_data` builds the profile over the last
`_VOLUME_PROFILE_LOOKBACK` = 200 candles, not the full visible series, into
`_VOLUME_PROFILE_BUCKETS` = 200 bands. The profile answers "where is the market
trading *now*" — a 1200-candle H1 profile spans ~50 days and averages the
current balance away into months of unrelated history. 200 bars matches the
reference TradingView studies.

The scope is deliberately **fixed**, not re-derived from the chart's visible
range. A visible-range profile (the reference study's "Use Visible Range"
option) was built and reverted: it made the POC and value area move whenever the
user zoomed or panned, so the levels stopped being a stable reading of the
current balance. Only the *rendering* follows zoom — see
`VolumeProfilePrimitive` — which is what keeps the profile in proportion with
the chart without changing what it measures.

Cross-checked against the kv4coins "Volume Profile" study (200 bars, VA 68%) on
NEARUSDT.P 4h: it reports POC ~1.918, this implementation 1.9150 — within two
bands of the 0.00197 bucket size.

## Where this is *not* the truth

**Delta-at-price.** Per-bucket delta *sign* agreed with the trade-level truth
72-90% of the time depending on timeframe. The aggressor is known per candle
but not per price, so a candle that was bought at its low and sold at its high
reports the same split at both ends. `VolumeProfile.delta_estimated` is `True`
on every kline-sourced profile to keep this visible in the domain, and the
chart keeps the split behind a modifier-click (`▤ VP` Alt/Shift) rather than
making it the default picture.

True delta-at-price is a **footprint** layer: it needs `aggTrades` (available,
paginated, with `isBuyerMaker`) or a websocket tape, plus persistence — one
hour of BTCUSDT is ~50,000 trades. That is a separate module with a separate
data path, not a parameter of this one.

**Order book / DOM.** Nothing here observes resting orders. `LiquidityHeatmap`
and `LeverageLiquidationMap` *estimate* where resting liquidity sits from
structure; the volume profile reports where volume *executed*. The two are
drawn on opposite edges of the pane so they are never read as the same thing.
