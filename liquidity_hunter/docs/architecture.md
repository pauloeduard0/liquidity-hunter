# Architecture

`liquidity_hunter` is a research platform for market liquidity detection and
market psychology analysis. It is **not** a trading system: it produces no
buy/sell signals and contains no order execution or strategy logic. Domain
entities and modules describe *observations* about a market (price action,
liquidity zones, structure, retail sentiment), never actions.

## Layering

Dependencies flow inward only — outer layers may depend on inner layers,
never the reverse. Each top-level package under `liquidity_hunter/` states
its responsibility and allowed dependencies in its `__init__.py` docstring.

```
        app ◄── api
         │
 ┌───────┼────────────┐
 │       │            │
liquidity  psychology │
 │       │            │
 indicators           │
 │       │            │
 └───►  data ◄────────┘
         │
        core (domain)
```

| Layer        | Responsibility                                                                 | Depends on                          |
|--------------|---------------------------------------------------------------------------------|--------------------------------------|
| `core`       | Framework-agnostic domain entities and shared enums                            | nothing                               |
| `data`       | Market data acquisition: Binance spot + USDT-M perpetual futures via CCXT (`OHLCVProvider` / `FuturesDataProvider` ports, fallback chaining, retries, OI pagination) | `core` |
| `indicators` | Stateless derived series computed from `Candle` data (volume delta)            | `core`, `data`                        |
| `liquidity`  | Detection/modeling of `LiquidityZone`, `MarketStructure`, and `POIZone`        | `core`, `data`, `indicators`          |
| `psychology` | Retail bias, manipulation cycles, behavior divergences, leverage liquidation map, OI regime | `core`, `data`               |
| `scoring`    | Composite, descriptive scoring of liquidity zones                               | `core`, `liquidity`, `psychology`     |
| `app`        | Composition root (`load_dashboard_data`), cross-layer synthesis (`NarrativeEngine`, `LiquidityHuntEngine`), multi-timeframe overview (`app/overview.py`) | all of the above |
| `api`        | Presentation of `app` output as JSON over HTTP (FastAPI)                        | `app`, `core`                         |
| `config`     | Application settings (environment-driven)                                      | nothing                               |

The React + TypeScript frontend (`frontend/`) lives outside the Python
package entirely and consumes the API — an alternative presentation layer
with no inward dependency.

## Domain entities

All domain entities live in `liquidity_hunter.core.domain`, subclass
`DomainModel` (an immutable Pydantic model: `frozen=True`,
`extra="forbid"`, `validate_assignment=True`), and describe *observations*
rather than decisions:

- **`Candle`** — a single OHLCV price bar, including `taker_buy_volume`
  (the basis for volume delta), with high/low consistency validators.
- **`LiquidityZone`** — a price region holding resting liquidity
  (equal highs/lows, swing points, ...).
- **`MarketStructure`** — a discrete structural observation
  (BOS / CHoCH / failed CHoCH / liquidity sweep / HH-HL-LH-LL) with
  direction, scope (major vs. internal), the broken reference level, and
  provisional/weak-reference metadata for live-edge rendering.
- **`POIZone`** — an MSB-anchored order block / breaker block / mitigation
  block zone with an ACTIVE → INVALIDATED lifecycle.
- **`ManipulationCycle`** — an accumulation → sweep → expansion
  Wyckoff/SMC cycle.
- **`BehaviorDivergence`** — a price vs. volume-delta divergence
  (distribution / accumulation / exhaustion / absorption).
- **`RetailBias`** — a measured retail sentiment/positioning observation.
- **`OpenInterestPoint`** / **`FundingRate`** / **`LongShortRatio`** —
  perpetual-futures market-state samples.
- **`LiquidationBand`** / **`LeverageLiquidationMap`** — projected
  force-liquidation levels per leverage tier around real entry areas.
- **`OIRegimeReading`** / **`OIQualifiedEvent`** / **`OIAnalysis`** —
  joint price × open-interest observations.
- **`LiquidityHuntState`** / **`LiquidityHuntTarget`** — who is the
  resting liquidity of the current move and whether it was captured.
- **`MarketNarrative`** — a synthesized event timeline with anomalies.
- **`TimeframeOverview`** / **`MarketOverview`** — the multi-timeframe
  structural ladder.

Shared enums live in `core/domain/enums.py`; behavior is extended by
adding enum members rather than branching logic elsewhere (Open/Closed).

## Composition

`app.dashboard_data.load_dashboard_data` is the single composition root:
it fetches one buffered candle series, runs every detector/analyzer with
per-timeframe production parameters, applies conservative composition-level
passes over the raw detector output (close-break re-anchoring of BOS,
pre-break-reference drops, resumed-fizzle cancellation), and assembles the
immutable `DashboardData` snapshot the API serves. The multi-timeframe
overview (`app/overview.py`) reuses the exact same pipeline per timeframe,
so the ladder always matches what the chart renders.

A design rule that recurs throughout the detectors: **missing marks are
fixed additively** (staged events merged after the fact) rather than by
relaxing confirmation rules inside the state machine — relaxation cascades
into the trend state and corrupts downstream CHoCH sequencing. Every
behavioral flag defaults to off, is byte-for-byte inert when disabled, and
is pinned by a real-data regression fixture. `CLAUDE.md` documents each
flag's motivating case and measurement.

## SOLID notes

- **Single Responsibility**: each domain entity and each layer package has
  one reason to change.
- **Open/Closed**: new zone types, structure events, and bias sources are
  added via the enums in `core.domain.enums` without modifying model logic.
- **Liskov Substitution**: all detectors/providers/estimators implement
  abstract ports (`LiquidityZoneDetector`, `MarketStructureDetector`,
  `OHLCVProvider`, `FuturesDataProvider`, `RetailBiasEstimator`) and are
  drop-in interchangeable — e.g. the futures OHLCV provider replaced the
  spot one behind the same port, and a future ML bias estimator can
  replace the rule-based one.
- **Interface Segregation**: layers expose only what downstream layers
  need via their package `__init__`.
- **Dependency Inversion**: higher layers depend on `core` abstractions,
  not on concrete implementations in `data`.
