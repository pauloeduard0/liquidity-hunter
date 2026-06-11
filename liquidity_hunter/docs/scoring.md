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

A proxy for "number of touches" / structural significance, taken directly
from `zone.strength` (already in `[0, 1]`, set by the detectors):

```
touch_score = zone.strength * 100
```

- For `EqualHighDetector` / `EqualLowDetector`, `strength` increases with
  the number of swing points grouped into the zone (more touches → higher
  strength → higher `touch_score`).
- For `SwingHighDetector` / `SwingLowDetector`, `strength` reflects the
  prominence of the swing relative to the candle range.

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

The equal-low zone, being closer to price and more frequently touched,
ranks first.
