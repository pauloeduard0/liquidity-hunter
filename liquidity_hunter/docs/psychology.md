# Psychology layer

The `psychology` layer models crowd behavior and institutional flow from
price structure and derivatives market state. Five analyzers, all producing
descriptive observations (never signals):

| Analyzer | Output | Reads |
|----------|--------|-------|
| `RetailTrapAnalyzer` | `RetailBiasEstimate` — what retail is likely thinking/doing | HTF trend, structure events, liquidity zones (detailed below) |
| `ManipulationCycleDetector` | `ManipulationCycle` — accumulation → sweep → expansion Wyckoff/SMC cycles, retrospective and prospective | zones, sweeps, BOS, volume delta |
| `BehaviorDivergenceAnalyzer` | `BehaviorDivergence` — institutional flow opposing visible price direction (distribution / accumulation / exhaustion / absorption) | volume delta, zone proximity, structure events |
| `LeverageLiquidationEstimator` | `LeverageLiquidationMap` — projected force-liquidation bands per leverage tier (10x/25x/50x/100x) around real entry areas | open interest, funding, long/short ratio, zones, POI zones |
| `OIRegimeAnalyzer` | `OIAnalysis` — the price × OI matrix (long/short buildup, covering, liquidation) plus per-event OI qualification (new money / covering / flush) | candles, OI history, structure events |

Parameters that depend on candle cadence (accumulation windows, divergence
windows) are timeframe-adaptive. The rest of this document details the
retail bias estimation; the other analyzers are documented in their module
docstrings and in `CLAUDE.md`.

## Retail crowd psychology estimation

`RetailTrapAnalyzer` (`liquidity_hunter/psychology/analyzers/retail_trap.py`)
estimates what retail traders are likely **thinking and doing** given the
current market context. It produces a `RetailBiasEstimate` — a
description of likely crowd behavior, not a price prediction or a trading
signal.

## Inputs

`RetailTrapAnalyzer.analyze(...)` takes:

- `symbol: str` — the instrument being analyzed.
- `higher_timeframe_direction: MarketDirection` — the prevailing
  higher-timeframe trend (`BULLISH`, `BEARISH`, or `NEUTRAL`).
- `market_structure_events: list[MarketStructure]` — lower-timeframe
  structural observations (HH/HL/LH/LL, break of structure, change of
  character). The most recent event (by `timestamp`) is used.
- `liquidity_zones: list[LiquidityZone]` — detected liquidity zones (e.g.
  from `EqualHighDetector`, `SwingLowDetector`, ...).
- `current_price: float` — the reference price. Must be `> 0`.

## Output

A `RetailBiasEstimate`:

```python
class RetailBiasEstimate:
    symbol: str
    generated_at: datetime
    dominant_side: RetailPositioning  # LONG, SHORT, or NEUTRAL
    confidence: float                 # 0-100
    explanation: str
```

`dominant_side` describes the position side retail traders are estimated
to be holding or entering — it is not a recommendation to take that side.

`RetailBiasEstimate` is distinct from `core.domain.RetailBias`:
`RetailBias` represents a *measured* observation from an external
sentiment/positioning source (a COT report, survey, etc.), while
`RetailBiasEstimate` is *inferred* from price structure context by a
`RetailBiasEstimator`.

## Estimation logic

### 1. Dominant side

Retail traders tend to anchor on the most recent visible structural shift
on the lower timeframe. The `direction` of the most recent
`MarketStructure` event becomes the "local direction":

- Local direction `BULLISH` → `dominant_side = LONG` (retail is likely
  buying — a breakout or a perceived bottom).
- Local direction `BEARISH` → `dominant_side = SHORT` (retail is likely
  selling — a breakdown or a perceived top).
- No structure events, or the latest event has direction `NEUTRAL` → fall
  back to `higher_timeframe_direction` (retail trend-following in the
  absence of a fresh local signal).
- If neither gives a direction, `dominant_side = NEUTRAL`.

### 2. Confidence (0-100)

Confidence reflects how strongly the situation is likely to draw retail
attention and conviction:

```
confidence = event_base_confidence
            + (20 if counter-trend else 0)
            + supporting_zone.strength * 20   (if a supporting zone exists)
```

clamped to `[0, 100]`.

**Event base confidence** — how attention-grabbing the most recent
structure event is:

| Event                  | Base confidence |
|------------------------|------------------|
| Change of character     | 60 |
| Break of structure      | 50 |
| HH / HL / LH / LL        | 35 |
| No structure events     | 20 |

**Counter-trend bonus (+20)** — applied when the local direction runs
counter to `higher_timeframe_direction`. This is the classic "retail trap"
setup: the crowd fades the dominant trend at the first sign of a reversal.

**Supporting liquidity bonus (up to +20)** — a nearby liquidity zone that
reinforces the narrative adds `strength * 20`:

- For `LONG` bias, a nearby `SELL_SIDE` zone (e.g. equal lows) below price
  reinforces a "perceived bottom" / support narrative.
- For `SHORT` bias, a nearby `BUY_SIDE` zone (e.g. equal highs) above price
  reinforces a "perceived top" / resistance narrative.

The zone with the smallest distance from `current_price` (using the zone's
midpoint) on the relevant side is used.

### 3. Explanation

A human-readable sentence describing the inferred behavior, the structure
event that triggered it, whether it runs with or against the higher
timeframe trend, and any reinforcing liquidity zone.

## Worked example

Higher timeframe bearish, lower timeframe change of character (bullish),
with a nearby equal-lows zone (`strength = 0.1`) acting as support:

```
event_base_confidence = 60   (change of character)
counter-trend bonus   = 20   (local bullish vs. higher-TF bearish)
liquidity bonus       = 0.1 * 20 = 2

confidence = 60 + 20 + 2 = 82
dominant_side = LONG

explanation = "Retail traders are likely attempting to buy a perceived
bottom against the higher timeframe trend, following a change of
character on the lower timeframe, reinforced by a nearby equal lows zone
acting as perceived support."
```

## Designing for future ML models

`RetailTrapAnalyzer` implements the `RetailBiasEstimator` abstract base
class (`psychology/analyzers/base.py`), whose `analyze(...)` signature
takes only plain domain types (`MarketDirection`, `list[MarketStructure]`,
`list[LiquidityZone]`, `float`) and returns a `RetailBiasEstimate`.

These inputs already form a structured feature set. A future
machine-learning-based estimator can implement the same interface — e.g.
`MLRetailBiasEstimator(RetailBiasEstimator)` — consuming the same inputs
(or features derived from them) and returning the same
`RetailBiasEstimate` shape, without any change to callers (Dependency
Inversion / Liskov substitution).
