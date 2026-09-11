# Liquidity Scoring

`LiquidityScoringEngine` (`liquidity_hunter/scoring/engine.py`) ranks
`LiquidityZone` objects by how relevant they are as **liquidity targets**
relative to the current price. It produces a descriptive `score` in
`[0, 100]` per zone — this is a research metric, not a trading signal.

## Inputs

- `zones: list[LiquidityZone]` — output of any `LiquidityZoneDetector`
  (`SwingHighDetector`, `SwingLowDetector`, `EqualHighDetector`,
  `EqualLowDetector`, ...).
- `current_price: float` — the reference price zones are scored against
  (e.g. the latest candle's close). Must be `> 0`.

## Output

A `list[ScoredLiquidityZone]`, sorted by descending `score`. Each entry
contains the original `zone` plus the score and its three components, so
the result can be inspected or re-weighted without recomputation:

```python
class ScoredLiquidityZone:
    zone: LiquidityZone
    score: float            # 0-100, weighted composite
    distance_score: float   # 0-100
    touch_score: float      # 0-100
    timeframe_score: float  # 0-100
```

## Scoring factors

The composite `score` is a weighted sum of three factors, each normalized
to `[0, 100]`:

```
score = distance_score * distance_weight
      + touch_score    * touch_weight
      + timeframe_score * timeframe_weight
```

`distance_weight + touch_weight + timeframe_weight` must equal `1.0`
(defaults: `0.4`, `0.4`, `0.2`), so `score` is always in `[0, 100]`.

### 1. Distance score

How close the zone is to `current_price`. Zones nearer to price are more
likely to be reached (and thus more relevant) than zones far away.

The zone's reference price is the midpoint of its range:
`(price_high + price_low) / 2`. The relative distance is:

```
distance_pct = |reference_price - current_price| / current_price
```

`distance_score` decays **linearly from 100 to 0** as `distance_pct` goes
from `0` to `max_distance_pct` (default `0.05`, i.e. 5%), and is clamped to
`0` beyond that:

```
distance_score = clamp(100 * (1 - distance_pct / max_distance_pct), 0, 100)
```

### 2. Touch score

`zone.strength` (already in `[0, 1]`, set by the detectors), rescaled:

```
touch_score = zone.strength * 100
```

The field name is historical: **`strength` stopped being a touch count when
the equal-level detector was re-measured on 2026-08-18**. Touch counting was
dropped because it saturated at 1.0 for any group of three or more touches.
Each detector now sets `strength` from a quantity of its own:

- `EqualHighDetector` / `EqualLowDetector` (`_EqualLevelDetector._area_strength`
  in `liquidity/detectors/equal_levels.py`): the **volume traded inside the
  pool's own price band** over its construction window (its first touch to its
  last touch), counting only candles whose range overlaps the band, in units of
  the series' mean candle volume:

  ```
  strength = min(1, area_volume / (mean_volume * _VOLUME_SATURATION))
  ```

  with `_VOLUME_SATURATION = 118.0`, the measured p75 of live pools.

- `SwingHighDetector` / `SwingLowDetector` (`_SwingPointDetector.detect` in
  `liquidity/detectors/swing_points.py`): the pivot's **prominence** — its
  distance to the most extreme of its `lookback` neighbours on either side —
  as a fraction of the range spanned by the whole loaded series
  (`price_range` in `liquidity/detectors/_common.py`):

  ```
  strength = clamp(prominence / price_range(candles), 0, 1)
  ```

  Note that the denominator is a property of the loaded window, not of the
  pivot: the same swing scores differently depending on how many candles were
  requested.

The two are **different quantities that share a `[0, 1]` range**, not two
readings of one unit: a volume ratio and a price-fraction of a window. The
engine adds both into the same `touch_score` channel, so an equal-level zone
and a swing point are compared on a scale they do not share.

### 3. Timeframe score

Reflects that liquidity resting on higher timeframes is generally more
significant and slower to be absorbed:

```
timeframe_score = timeframe_weights[zone.timeframe] * 100
```

Default weights (`DEFAULT_TIMEFRAME_WEIGHTS` in `scoring/weights.py`),
increasing from `1m` to `1w`:

| Timeframe | Weight |
|-----------|--------|
| `1m`      | 0.10   |
| `5m`      | 0.20   |
| `15m`     | 0.35   |
| `30m`     | 0.50   |
| `1h`      | 0.65   |
| `4h`      | 0.80   |
| `1d`      | 0.90   |
| `1w`      | 1.00   |

`zone.timeframe` is the timeframe of the candles the detector ran on, so this
component only varies when a single `score()` call mixes zones from several
timeframes. **In the dashboard it never does**: `load_dashboard_data` builds
every zone from one series (`app/dashboard_data.py`), so within a chart
`timeframe_score` is the same constant for every candidate and contributes
nothing to their ordering — it shifts all of their scores by the same amount.
It still affects the absolute value of `score`, which the API exposes.

Both the per-component weights and `timeframe_weights` are configurable via
the `LiquidityScoringEngine` constructor.

## Worked example

A 1h `EqualLow` zone with `strength = 0.67`, sitting `0.3%` below a current
price of `$77,000` (well within the default 5% `max_distance_pct`):

```
distance_score  = 100 * (1 - 0.003 / 0.05)  = 94.0
touch_score     = 0.67 * 100                = 67.0
timeframe_score = 0.65 * 100  (1h)          = 65.0

score = 94.0 * 0.4 + 67.0 * 0.4 + 65.0 * 0.2 = 77.8
```

A 4h `SwingHigh` zone with `strength = 0.10`, sitting `4%` above the same
current price:

```
distance_score  = 100 * (1 - 0.04 / 0.05)   = 20.0
touch_score     = 0.10 * 100                = 10.0
timeframe_score = 0.80 * 100  (4h)          = 80.0

score = 20.0 * 0.4 + 10.0 * 0.4 + 80.0 * 0.2 = 28.0
```

The equal-low zone ranks first: it is closer to price, and the volume that
changed hands inside its band gives it the higher `touch_score`. Both zones
in this example are scored by a single `score()` call spanning two timeframes,
so `timeframe_score` differs between them; inside one chart it would not.
