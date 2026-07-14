# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project purpose and constraints

`liquidity_hunter` is a **research platform** for market liquidity detection
and market psychology analysis. It is explicitly **not** a trading system:

- Do not add trading strategies, order execution, or position management.
- Do not add buy/sell signals or any decisioning/recommendation logic.
- Domain entities and modules describe *observations* about a market
  (price action, liquidity zones, structure, retail sentiment), not actions.

## Commands

This project uses Poetry with Python 3.12.

```bash
# Install dependencies (or: pip install -r requirements-dev.txt)
poetry install

# Run all tests
poetry run pytest

# Run a single test file / test
poetry run pytest liquidity_hunter/tests/core/domain/test_models.py
poetry run pytest liquidity_hunter/tests/core/domain/test_models.py::test_candle_valid_construction

# Lint
poetry run ruff check .

# Type-check (strict mode)
poetry run mypy liquidity_hunter
```

Test discovery is configured to `liquidity_hunter/tests` (see
`[tool.pytest.ini_options]` in `pyproject.toml`). Tests mirror the package
layout 1:1 (e.g. `liquidity_hunter/core/domain/candle.py` →
`liquidity_hunter/tests/core/domain/test_models.py`).

### Frontend (`frontend/`)

A separate React + TypeScript + Vite project (Tailwind CSS, Lightweight
Charts), outside the `liquidity_hunter` Python package, that consumes
`GET /api/dashboard`. Run `poetry run uvicorn liquidity_hunter.api.main:app
--reload` first, then:

```bash
cd frontend
npm install
npm run dev      # dev server, proxies /api -> http://127.0.0.1:8000
npx tsc -b       # type-check
npm run lint     # eslint
npm run build    # production build
```

## Architecture

The codebase follows clean architecture: **dependencies flow inward only**,
toward `core`. Each top-level package under `liquidity_hunter/` is a layer
with a documented responsibility and allowed dependencies, stated in its
`__init__.py` docstring — read that first when working in a new layer.

```
        app
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

api ── depends on app, core (presentation layer)
```

| Layer        | Responsibility                                                              | May depend on                     |
|--------------|------------------------------------------------------------------------------|------------------------------------|
| `core`       | Framework-agnostic domain entities (`Candle`, `LiquidityZone`, `MarketStructure`, `ManipulationCycle`, `RetailBias`) and shared enums | nothing |
| `data`       | Market data acquisition, repositories, persistence adapters                 | `core`                              |
| `indicators` | Stateless derived series computed from `Candle` data                        | `core`, `data`                      |
| `liquidity`  | Detection/modeling of `LiquidityZone` and `MarketStructure`                  | `core`, `data`, `indicators`        |
| `psychology` | Modeling of `RetailBias` from sentiment/positioning data                     | `core`, `data`                      |
| `scoring`    | Composite, descriptive scoring combining `liquidity` and `psychology` output | `core`, `liquidity`, `psychology`   |
| `app`        | Composition root and orchestration                                           | all of the above                    |
| `api`        | Presentation of `app` output as JSON over HTTP (FastAPI)                    | `app`, `core`                       |
| `config`     | Application settings (environment-driven, via `pydantic-settings`)          | nothing                             |

### Domain entities (`liquidity_hunter/core/domain`)

All domain entities subclass `DomainModel` (`core/domain/base.py`), a Pydantic
`BaseModel` configured as **immutable** (`frozen=True`), with `extra="forbid"`
and `validate_assignment=True`. New entities should follow this pattern.

- **`Candle`** — a single OHLCV price bar, including `taker_buy_volume`
  (taker buy base asset volume, the basis for `indicators.volume_delta`);
  validates high/low consistency against open/close and
  `taker_buy_volume <= volume` in `model_validator`s.
- **`LiquidityZone`** — a price region holding resting liquidity (equal
  highs/lows, order blocks, fair value gaps, etc.); validates
  `price_high >= price_low`.
- **`MarketStructure`** — a discrete structural observation (BOS/CHoCH/
  `CHOCH_FAILED`/`LIQUIDITY_SWEEP`/HH/HL/LH/LL) with a `MarketDirection` and
  `StructureScope`.
  Fields: `timestamp` (actual breaking candle, not the triggering pivot),
  `price_level` (triggering pivot's extreme), `reference_price_level` (the
  level that was broken — for `SWEEP`, `active_<side>`; for BOS (**both
  detectors**), the **formed low/high it broke** (the staircase floor), so it
  plots at the prior swing extreme; `validated_choch_<side>` for CHoCH, the
  broken CHoCH *origin* for `CHOCH_FAILED`), and `reference_timestamp` (for
  CHoCH: the timestamp of the LH/HL pivot promoted to `validated_choch_<side>`;
  for BOS: the candle that *formed* the broken level, so the line starts at the
  level's origin — both used to anchor the line's start in the frontend), and
  `reference_structural` (`bool | None`; `InternalStructureDetector` CHoCH only:
  whether the broken reference was a *structural* level — close-confirmed leg
  origin / continuation-promoted pullback / pending-BOS origin / blind-spot
  origin — or a *weak* one (re-anchor, wick-only-break promotion, cold-start
  fallback), the same classification the new-cycle persistence barrier uses;
  `None` for other events and the major detector — the frontend renders weak
  CHoCH dimmed/dotted with a `*` suffix), and `provisional` (`bool`, default
  `False`; `InternalStructureDetector` only): a *provisional* mark is a live-edge
  event whose confirming swing pivots have not formed yet (the swing-lookback lag
  at the right edge). A provisional **BOS** (`BREAK_OF_STRUCTURE`, under
  `emit_provisional_bos`) is a continuation whose staircase floor already
  *closed*-broke; a provisional **CHoCH** (`CHANGE_OF_CHARACTER`, under
  `emit_provisional_choch`) is a reversal whose *structural* CHoCH reference has
  been sustained-*closed*-broken (under `emit_provisional_choch_weak`, a *weak*
  reference also qualifies, at the weak-ref barrier persistence — rendered
  `CHoCH?* ▲`). Either appears only in the last few candles of a
  leg — superseded by the confirmed event once the pivots form, or it vanishes if
  the move fails first (an intentional live-edge repaint) — and the frontend
  renders it dimmed/dotted with a `?` suffix (`BOS? ▼` / `CHoCH? ▼`), like a weak
  CHoCH. A
  `CHOCH_FAILED` event marks a CHoCH
  that was invalidated before a confirming BOS (its `direction` is the failed
  CHoCH's direction); see the `InternalStructureDetector` notes. A `CHOCH_FAILED`
  may also be `provisional=True`: the additive **fast-fizzle marker**
  (`choch_fizzle_reclaim_candles`) that disregards a *standing* CHoCH whose
  reversal fizzled without flipping the state-machine trend — `provisional` here
  keeps it out of the `LiquidityHuntEngine`/`NarrativeEngine` replay while the
  frontend still terminates the stale line; see the `InternalStructureDetector`
  notes.
- **`POIZone`** — an institutional order/breaker/mitigation block zone,
  defined in `core/domain/poi_zone.py`. Anchored to a **market structure
  break (MSB)**. An `ORDER_BLOCK` is the *last opposite-direction candle
  before the impulse* that broke structure (for a bullish MSB, the last
  bearish candle of the down leg into the swing low the impulse launched
  from; bearish mirrors it); a `BREAKER_BLOCK`/`MITIGATION_BLOCK` is the last
  *same*-direction candle of the leg that formed the broken pivot (breaker
  when the impulse-origin extreme swept the prior one — bullish `l0 < l1`,
  bearish `h0 > h1` — mitigation otherwise). Both span the anchor candle's
  **full high-low range**, frozen at creation. Fields: `direction`, `kind`
  (`POIZoneKind`, default `ORDER_BLOCK`), `price_low`, `price_high`,
  `created_at` (the MSB confirmation candle), `ob_candle_timestamp` (the
  anchor candle — the box's left edge), `status` (`POIZoneStatus`:
  `ACTIVE`/`INVALIDATED`), `invalidated_at`. A single candle *close* beyond
  the far boundary (below `price_low` for bullish, above `price_high` for
  bearish) invalidates the zone; price touching back inside does not retire
  it. Identical lifecycle for all kinds.
- **`ConsolidationRange`** — an observed lateral consolidation, defined in
  `core/domain/consolidation.py`: a stretch of candles with **no structure
  advance** where price oscillated inside a volatility-bounded box (at least
  N candles within K×mean-TR% height, touching both boundary zones
  alternately — see `liquidity/detectors/consolidation.py`). Where the
  structure detector is *correctly* silent (a range has no BOS/CHoCH), made
  explicit. Fields: `symbol`, `timeframe`, `start_timestamp`,
  `end_timestamp` (`None` while open), `price_low`/`price_high` (the box),
  `status` (`ConsolidationStatus`: `ACTIVE`/`RESOLVED`), `resolved_direction`
  (the breakout/advance direction when `RESOLVED`), `candle_count`. Resolution
  = sustained closes beyond a boundary, or a structure advance ending the
  segment; a wick/unsustained poke beyond the box is a boundary sweep and
  stays outside it.
- **`ManipulationCycle`** — an observed institutional manipulation cycle
  (accumulation → sweep → expansion), defined in
  `core/domain/manipulation_cycle.py`. Describes the three-phase Wyckoff/SMC
  pattern where price consolidates near a liquidity zone (accumulation),
  sweeps the zone to capture stops (manipulation), then moves impulsively in
  the opposite direction (expansion). `direction` is the expansion direction:
  a bullish cycle sweeps sell-side liquidity (lows) then expands upward.
  Fields: `direction`, `phase` (`ManipulationPhase`: `ACCUMULATION`/
  `MANIPULATION`/`EXPANSION`), `status` (`ManipulationCycleStatus`:
  `IN_PROGRESS`/`CONFIRMED`/`FAILED`), target zone info
  (`target_zone_price_low/high`, `target_zone_type`, `target_zone_side`),
  accumulation context (`accumulation_start/end`, `consolidation_candles`,
  `accumulation_avg_volume_delta`), sweep context (`sweep_timestamp`,
  `sweep_extreme`, `sweep_volume_delta`), and expansion context
  (`expansion_timestamp`, `expansion_price`, `expansion_volume_delta`).
- **`BehaviorDivergence`** — an observed divergence between price movement
  and volume delta, defined in `core/domain/behavior_divergence.py`. Detects
  when institutional flow opposes visible price direction. Fields: `timestamp`,
  `divergence_type` (`DivergenceType`: `DISTRIBUTION`/`ACCUMULATION`/
  `EXHAUSTION`/`ABSORPTION`), `direction` (apparent price direction),
  `price_level`, `volume_delta_avg`, `price_change_pct`, optional zone
  context (`nearest_zone_side`, `nearest_zone_price_low/high`),
  `confidence` (0-100), `description`.
- **`RetailBias`** — a measurement of retail sentiment/positioning from a
  given `BiasSource`, with a bounded `sentiment_score` and `confidence`.
- **`OpenInterestPoint`** / **`FundingRate`** / **`LongShortRatio`** —
  perpetual-futures market-state samples, defined in `core/domain/futures.py`.
  The raw inputs the `LeverageLiquidationEstimator` uses to infer the
  over-leveraged side (open interest, funding rate, crowd long/short account
  ratio).
- **`LiquidationBand`** / **`LeverageLiquidationMap`** — a "gravitational map"
  of where leveraged retail positions would be force-liquidated, defined in
  `core/domain/liquidation.py`. `LeverageLiquidationMap` fields: `symbol`,
  `timeframe`, `current_price`, `dominant_leveraged_side` (`RetailPositioning`),
  `positioning_intensity` (0-1), `funding_rate`, `open_interest_change_pct`,
  `long_short_ratio`, `bands`. Each `LiquidationBand` has `price_low`,
  `price_high`, `leverage`, `side` (`LiquiditySide`), `source_entry_price`,
  `intensity` (0-100), and a time span: `start_time` (when the entry cluster
  formed) and `end_time` (when price first reached the liquidation level — the
  pool was consumed — or `None` if still live).
- **`OIRegimeReading`** / **`OIQualifiedEvent`** / **`OIAnalysis`** — joint
  price × open-interest observations, defined in `core/domain/oi_analysis.py`.
  `OIRegimeReading` classifies the most recent window into the classic futures
  matrix (`OIRegime`: `LONG_BUILDUP` price↑+OI↑, `SHORT_COVERING` price↑+OI↓,
  `SHORT_BUILDUP` price↓+OI↑, `LONG_LIQUIDATION` price↓+OI↓, `FLAT` below the
  significance floors), with `price_change_pct`, `oi_change_pct`,
  `window_candles`, `intensity` (0-100). `OIQualifiedEvent` attaches OI context
  to a structure event (`participation`, `OIParticipation`: `NEW_MONEY` OI
  rising into the break / `COVERING` OI falling / `FLUSH` sharp OI drop on a
  sweep / `FLAT`). `OIAnalysis` aggregates both plus the OI series' coverage
  span (`coverage_start`/`coverage_end`).
- **`LiquidityHuntState`** / **`LiquidityHuntTarget`** — a descriptive reading
  of *who is the resting liquidity* of the current move, defined in
  `core/domain/liquidity_hunt.py`. When the current timeframe's structure runs
  counter to the higher-timeframe trend, the counter-trend entrants become the
  fuel: `hunted_side` (`RetailPositioning`: SHORT during a bearish correction
  inside a bullish HTF, LONG mirrored), `phase` (`LiquidityHuntPhase`:
  `NONE`/`COUNTER_TREND`/`HUNT_IN_PROGRESS`/`CAPTURED`), `targets` (the nearby
  opposing pools — `LiquidityHuntTarget` with `kind` (`LiquidityHuntTargetKind`:
  `EQUAL_LEVEL`/`LIQUIDATION_BAND`), `label`, `price_level`, `captured`,
  `captured_at`; list capped at 8, counts in `targets_captured`/`targets_total`
  cover the full set), `correction_direction`, `counter_structure_timestamp`
  (the trend-flip event), `oi_unwinding`, `last_flush_timestamp`, `captured_at`,
  `description`. `CAPTURED` requires **all** mapped pools consumed *and* OI no
  longer unwinding against the hunted side — conservative by design (and never
  reached with zero mapped pools: absence of pools is not evidence of capture).
- **`MarketNarrative`** — synthesized institutional narrative for a
  symbol/timeframe snapshot, defined in `core/domain/narrative.py`. Fields:
  `symbol`, `timeframe`, `timestamp`, `phase` (`ManipulationPhase | None`),
  `timeline` (`list[NarrativeEvent]`), `anomalies` (`list[NarrativeAnomaly]`),
  `summary`, `confluence_count`, `confluence_total`.
- **`NarrativeEvent`** — a single event in the narrative timeline. Fields:
  `timestamp`, `event_type` (`NarrativeEventType`), `direction`, `description`,
  `source_layer`.
- **`NarrativeAnomaly`** — a pattern contradiction. Fields: `timestamp`,
  `expected`, `observed`, `description`, `severity` (`AnomalySeverity`).
- **`TimeframeOverview`** / **`MarketOverview`** — the multi-timeframe
  structural ladder, defined in `core/domain/overview.py` (built by
  `app.overview`, see below). `TimeframeOverview` is one timeframe's standing
  state: `timeframe`, `trend` (the internal detector's state-machine trend —
  exactly what the chart renders for that timeframe), `current_price`,
  `candle_timestamp`, the `_HIGHER_TIMEFRAME_MAP` anchor pair
  (`higher_timeframe`/`higher_timeframe_direction`, `None` at the top), the
  last non-provisional trend-relevant event (`last_event`,
  `last_event_direction`, `last_event_timestamp`, `last_event_candles_ago`),
  any provisional live-edge mark (`forming_event`/`forming_direction` — the
  dimmed `BOS?`/`CHoCH?`), the consolidation state (`in_consolidation` — price
  is inside a confirmed ACTIVE `ConsolidationRange`, so `trend` reads as the
  pre-range cycle — and `consolidation_candles`), and a hunt summary
  (`hunt_phase`, `hunted_side`,
  `hunt_targets_captured`/`_total`). `MarketOverview` is `symbol` + `entries`
  ordered fine → coarse. Descriptive state per timeframe, not signals.

Shared enums (`TimeFrame`, `MarketDirection`, `LiquiditySide`,
`LiquidityZoneType`, `StructureEvent`, `BiasSource`, `RetailPositioning`,
`POIZoneStatus`, `POIZoneKind`, `ConsolidationStatus`, `ManipulationPhase`,
`ManipulationCycleStatus`,
`DivergenceType`, `LiquidityHuntPhase`, `LiquidityHuntTargetKind`,
`NarrativeEventType`, `AnomalySeverity`) live in
`core/domain/enums.py`. Extend behavior by adding enum members rather than
branching logic elsewhere (Open/Closed principle).

Full architecture rationale, including SOLID notes, is documented in
`liquidity_hunter/docs/architecture.md`.

### Data layer (`liquidity_hunter/data`)

- **`data/providers/base.py`** — `OHLCVProvider`, the abstract port all
  market data sources implement (`get_ohlcv(symbol, timeframe, limit) -> list[Candle]`).
- **`data/providers/binance.py`** — `BinanceDataProvider`, a CCXT-backed
  implementation for Binance. `to_ccxt_symbol()` converts concatenated
  symbols (e.g. `"BTCUSDT"`) to CCXT's unified `"BASE/QUOTE"` form. Candles
  are fetched via ccxt's implicit `publicGetKlines` (raw Binance
  `/api/v3/klines`, 12 columns) rather than `fetch_ohlcv`, since only the
  raw response includes taker buy base asset volume (column index 9),
  needed to populate `Candle.taker_buy_volume`.
- **`data/retry.py`** — `retry_with_backoff` decorator (exponential backoff,
  logged) used to retry transient `ccxt.NetworkError`s.
- **`data/exceptions.py`** — `DataProviderConnectionError` (retries
  exhausted) and `DataProviderRequestError` (non-retryable, e.g. invalid
  symbol), both subclasses of `DataProviderError`.

- **`data/providers/base.py`** — also defines `FuturesDataProvider`, the
  abstract port for perpetual-futures market state
  (`get_open_interest_history`, `get_funding_rate_history`,
  `get_long_short_ratio`), a sibling to `OHLCVProvider`.
- **`data/providers/binance_futures.py`** — `BinanceFuturesDataProvider`, a
  ccxt `binanceusdm`-backed implementation. Open interest and funding use
  ccxt's unified `fetch_open_interest_history`/`fetch_funding_rate_history`
  (against the swap symbol, e.g. `"BTC/USDT:USDT"`); the crowd long/short
  account ratio uses the implicit `fapiDataGetGlobalLongShortAccountRatio`
  (raw `BTCUSDT` symbol). `TimeFrame` maps to Binance's fixed futures-data
  periods (`_FUTURES_PERIOD`). Same `retry_with_backoff` + error translation
  as `BinanceDataProvider`. `get_open_interest_history` **paginates** past
  Binance's 500-row per-request cap when `limit > 500` (paging forward with
  `since`, de-duplicated by timestamp), clamped to Binance's ~30-day OI
  retention with a 1-hour safety margin inside the boundary (a `startTime`
  at exactly −30d is rejected with error -1130).

- **`data/providers/binance_futures_ohlcv.py`** — `BinanceFuturesOHLCVProvider`,
  an `OHLCVProvider` (sibling to `BinanceDataProvider`) that fetches **candles**
  from Binance USDT-M perpetual futures via ccxt `binanceusdm`'s implicit
  `fapiPublicGetKlines` (raw `/fapi/v1/klines`, same 12-column layout as spot,
  so `Candle.taker_buy_volume` at column 9 is still populated). Preferred over
  spot because the candles align with the futures-derived analysis already
  overlaid on the chart (OI/funding/long-short/liquidation map) and reflect
  leveraged flow. Its `max_fetch_limit` is **1500** (vs spot's 1000), so one
  request covers a larger window. Symbols with no perpetual contract raise
  `DataProviderRequestError`.
- **`data/providers/fallback.py`** — `FallbackOHLCVProvider(primary, secondary)`:
  an `OHLCVProvider` that tries `primary` and falls back to `secondary` on
  `DataProviderRequestError` (e.g. a symbol with no perpetual), clamping the
  fallback request to the secondary's `max_fetch_limit`; connection errors
  propagate. `max_fetch_limit` follows the primary. `load_dashboard_data`'s
  default provider is `FallbackOHLCVProvider(BinanceFuturesOHLCVProvider(),
  BinanceDataProvider())` (futures candles, spot fallback for spot-only pairs).

The `OHLCVProvider` port carries a `max_fetch_limit` class attribute (default
1000, the per-request candle cap) that callers read instead of assuming a fixed
limit; `klines_row_to_candle` (in `binance.py`) is the shared 12-column row →
`Candle` parser used by both the spot and futures providers.

`BinanceDataProvider`, `BinanceFuturesOHLCVProvider`, `FallbackOHLCVProvider`,
`OHLCVProvider`, `BinanceFuturesDataProvider`, and `FuturesDataProvider` are
re-exported from `liquidity_hunter.data`.

### Indicators layer (`liquidity_hunter/indicators`)

- **`indicators/volume_delta.py`** — `volume_delta(candle) -> float`
  computes `2 * taker_buy_volume - volume` (net taker buy/sell aggression
  for that candle, ranging from `-volume` to `+volume`);
  `volume_delta_series(candles) -> list[float]` applies it across a series,
  1:1 aligned with `candles`. Both are re-exported from
  `liquidity_hunter.indicators`.

### Liquidity layer (`liquidity_hunter/liquidity`)

- **`liquidity/detectors/base.py`** — `LiquidityZoneDetector`, the abstract
  port all detectors implement (`detect(candles) -> list[LiquidityZone]`).
- **`liquidity/detectors/swing_points.py`** — `SwingHighDetector` /
  `SwingLowDetector`: fractal-style local extrema (configurable `lookback`),
  returning point zones (`price_high == price_low`) with `strength` derived
  from prominence relative to the candle range.
- **`liquidity/detectors/equal_levels.py`** — `EqualHighDetector` /
  `EqualLowDetector`: group swing points within a configurable
  `tolerance_pct` (relative tolerance) into equal-level zones, requiring
  `min_touches` (default 2); `strength` scales with touch count.
- **`liquidity/detectors/base.py`** — also defines `MarketStructureDetector`,
  the abstract port for structure detectors
  (`detect(candles) -> list[MarketStructure]`).
- **`liquidity/detectors/market_structure.py`** — `SwingStructureDetector`:
  detects BOS/CHoCH and HH/HL/LH/LL on the major (swing) structure. As of
  2026-06-16 this detector **mirrors `InternalStructureDetector`'s
  architecture exactly**, differing only in defaults (`swing_lookback=10`,
  `persistence_candles=10`). It uses trailing `active_high`/`active_low`
  references and the same `candidate_choch_<side>` / `candidate_choch_<side>_baseline`
  / `validated_choch_<side>` two-step promotion gate as `InternalStructureDetector`.
  Volume-delta confirmation (`min_volume_delta_ratio`) has been removed entirely.

  **BOS**: The state machine (`active_<side>`, `pending_<side>`, `trend`)
  advances **only when a candle in the leg *closes* beyond the active
  reference** (`find_close_break_index`) — a wick-only overshoot does not
  advance state. On a wick-only break the reference is *frozen* (not trailed
  to the new pivot), so a later candle that closes beyond that same level
  activates the BOS then. A continuation BOS must also satisfy the **BOS
  staircase**: it must extend the leg beyond the previous BOS level
  (`last_bear_bos_low`/`last_bull_bos_high`) — breaking a higher trailing low
  (or lower trailing high) formed during a retrace, which does not beat the
  previous BOS extreme, is not a structural BOS. The staircase is **seeded at
  each CHoCH with the CHoCH level** (the broken reference), so the first BOS of
  the new leg must break beyond the CHoCH level — a BOS cannot form on the
  wrong side of the CHoCH. The
  `BREAK_OF_STRUCTURE` event is *emitted* once confirmed, and that close
  candle optionally passes the LuxAlgo-style confluence filter
  (`bos_confluence`, see `_common.py`). `confluence_filter` (constructor
  parameter, default `True`) enables this shadow-balance check: the breaking
  close candle must have a larger upper shadow than lower shadow (bullish) or
  vice versa (bearish). The emitted BOS `timestamp` is that closing candle's
  timestamp; `price_level` is the triggering pivot's extreme;
  `reference_price_level` is the **formed low/high it broke** (the staircase
  floor captured before it ratchets), mirroring `InternalStructureDetector`
  (`floor or active_<side>`), and re-anchored to the formed level's close-break
  in `load_dashboard_data`.

  **CHoCH**: A counter-trend break is confirmed via **persistence** (same as
  `InternalStructureDetector`): `is_sustained_break` must hold for
  `persistence_candles` consecutive candles beyond the break. The CHoCH
  reference is `validated_choch_<side> or choch_origin_<side> or
  active_<side>`, promoted from `candidate_choch_<side>` via the same
  two-step baseline gate described under `InternalStructureDetector` below.
  The `active_<side>` cold-start fallback ensures the detector can flip
  trend during the bootstrap phase (before any validated/origin reference has
  been built). `reference_price_level` is the reference that was broken;
  `reference_timestamp` is `validated_choch_<side>.timestamp` (when the
  validated reference was used).

  **SWEEP**: A counter-trend wick break that does not hold (`is_sustained_break`
  fails) is a `LIQUIDITY_SWEEP`; timestamp via `find_wick_break_index`.

  `pending_high`/`pending_low` accumulate the most extreme pivot seen since
  the opposite active level was last set, so a BOS/CHoCH reflects the true
  extreme of the prior leg. Every emitted `MarketStructure` has
  `scope = StructureScope.MAJOR`.
- **`liquidity/detectors/internal_structure.py`** — `InternalStructureDetector`:
  detects BOS/CHoCH/`LIQUIDITY_SWEEP`/HL/LH on finer-grained, internal/minor
  structure, with `scope = StructureScope.INTERNAL` stamped on every emitted
  `MarketStructure` (see `app.dashboard_data.load_dashboard_data`, which runs
  it on the same candle series as `market_structure_events`, with a smaller
  per-timeframe `swing_lookback`/`persistence_candles` to surface minor pivots
  within that series — see `_INTERNAL_STRUCTURE_PARAMS` under `load_dashboard_data`).
  A Portuguese walkthrough of the whole BOS/CHoCH pipeline lives in
  `liquidity_hunter/docs/estrutura_bos_choch.md`. Like
  `SwingStructureDetector`, it sources swing pivots from
  `SwingHighDetector`/`SwingLowDetector` (`swing_lookback`) via the shared
  `_common.collect_pivots`, and maintains `pending_high`/`pending_low`
  (the most extreme high/low pivot accumulated for a future promotion). But
  unlike `SwingStructureDetector`, `active_high`/`active_low` are *trailing*
  references — normally the most recently formed swing high/low pivot,
  updated after *every* pivot of that kind (adapted from LuxAlgo's "Smart
  Money Concepts" indicator) — rather than references held until the
  opposite side breaks. A pivot above `active_high` (below `active_low`), in
  the direction of `trend` (or the first such break while `trend` is
  `NEUTRAL`), is a `BREAK_OF_STRUCTURE` on price alone; against `trend` it is
  a `CHANGE_OF_CHARACTER` if confirmed (see below), else a `LIQUIDITY_SWEEP`.
  A pivot below `active_high` (above `active_low`) is a descriptive
  `LOWER_HIGH`/`HIGHER_LOW` label. A purely trailing reference has its own
  failure mode, though: comparing a CHoCH against the last pivot — possibly a
  minor retracement rather than the true extreme of the leg that just ended —
  can spuriously flag a continuation BOS right after the reversal. To avoid
  that, a confirmed BOS/CHoCH promotes the *opposite* side's `pending_<side>`
  to `active_<side>` (or to `None`, if nothing has accumulated there yet —
  the next pivot on that side then silently re-bootstraps with no label, the
  accepted cost of carrying forward "extreme of the prior leg" semantics). A
  `LIQUIDITY_SWEEP`, or a pivot that doesn't break the active reference (a
  HL/LH label), instead folds the *opposite* side's current `active_<side>`
  into its `pending_<side>` via `_extreme`, so that value isn't lost when
  `active_<side>` is later overwritten by its own next pivot. Bootstrapping a
  side (its `active_<side>` was `None`) also seeds `pending_<side>` with the
  same pivot, if the opposite side is already active. `SwingStructureDetector`'s
  freeze — an active reference that happens to equal the extreme of the
  entire remaining candle window can permanently freeze the *opposite* side,
  since it is only promoted once the opposite side breaks — is acceptable for
  `StructureScope.MAJOR`'s "significant level" semantics, but would leave
  `StructureScope.INTERNAL` unable to surface large moves as BOS/CHoCH for
  long stretches. `InternalStructureDetector` avoids this: both references
  keep tracking recent pivots (rather than freezing on either an old extreme
  or a stale promoted value).

  **BOS confirmation**: The state machine advances **only when a candle in the
  leg *closes* beyond the reference** (`find_close_break_index`) — a wick-only
  overshoot does not advance state; the reference is *frozen* (not trailed to
  the new pivot) until a close confirms. A continuation BOS must also satisfy
  the **BOS staircase**: it must extend the leg beyond the previous BOS level
  (`last_bear_bos_low`/`last_bull_bos_high`) — a break of a higher trailing low
  (or lower trailing high) formed during a retrace, which does not beat the
  previous BOS extreme, is not a structural BOS (it just trails the active
  reference). The staircase is **seeded at each CHoCH with the CHoCH level**
  (the broken reference), so the first BOS of the new leg must break beyond the
  CHoCH level — a BOS cannot form on the wrong side of the CHoCH. The
  `BREAK_OF_STRUCTURE` event is
  *emitted* once confirmed, and that close candle optionally passes the
  LuxAlgo-style confluence filter (`bos_confluence`): upper shadow > lower
  shadow for bullish, reverse for bearish. `confluence_filter` (constructor
  parameter, default `True`) enables this check; `load_dashboard_data` exposes
  it so tests can disable it. The BOS `timestamp` is the close-break candle's
  timestamp. A BOS is only *emitted* once a confirming opposite-direction
  pullback pivot forms beyond the pullback reference snapshot
  (`active_<opposite>` at the state-advance). In an **impulsive leg** of
  consecutive same-side pivots with no intervening opposite pivot, the first
  advance nulls `active_<opposite>` (promoting an empty `pending_<opposite>`),
  so a later advance would snapshot a `None` pullback ref and the BOS could
  never confirm — leaving a whole impulsive move with zero BOS. The leg keeps
  extending from the *same* opposite pivot, so a `None` snapshot inherits the
  prior pending BOS's pullback ref, and the continuation BOS still confirms at
  the next opposite pivot.

  **CHoCH confirmation** is **persistence**-based: a single candle that pokes
  through a level and immediately reverts is a "false break"; a break that
  *holds* beyond for a few candles is "real" — see `_common.is_sustained_break`.
  The constructor's `persistence_candles` (default `5`) is the count of
  consecutive candles (including the breaking one) that must close beyond the
  reference. This check is **not** anchored to the triggering pivot's own index:
  a sustained break is considered confirmed if *any* candle from just after the
  previous pivot of the same kind (exclusive) through the triggering pivot
  (inclusive) starts a window that holds for `persistence_candles` beyond it,
  even if that window extends past the pivot's own index. If no such window
  exists (or there aren't yet enough trailing candles), the pivot is reported
  as a `LIQUIDITY_SWEEP` instead. This replaces the previous
  `volume_delta`/volume-spike confirmation entirely.

  The reversal (`CHANGE_OF_CHARACTER`) reference is the **pullback (origin) of
  the most recent continuation-confirmed BOS**, tracked per side as
  `validated_choch_high` (the level a bullish CHoCH must break) and
  `validated_choch_low` (bearish CHoCH). The promotion pipeline for
  `validated_choch_high` (bearish leg, mirrored on the bullish side):

  1. **BOS emission**: when a bearish BOS is confirmed (pending BOS + LH
     pullback), the confirming LH pivot becomes `candidate_choch_high` —
     *provisional*, not yet the CHoCH reference. A continuation-dedup gate
     ensures each pullback stays below the previous pullback (LH staircase),
     preventing re-emission of the same structural break.

  2. **Continuation-gated promotion**: the next bearish state-advance (a new
     lower-low pivot) promotes `candidate_choch_high` to
     `validated_choch_high` **only if** the new low is below `bear_leg_low`
     (the running extreme of the current bearish leg). A pullback-BOS formed
     during a retrace that does not make a new leg low leaves the candidate
     provisional: that BOS never extended the leg, so its pullback must not
     ratchet the CHoCH reference down. `bear_leg_low` / `bull_leg_high` are
     seeded at each trend flip (CHoCH) and updated on every in-trend
     state-advance.

  2b. **Sweep re-anchor of the candidate**: while the leg unfolds, a
     counter-trend sweep that pokes beyond the current `candidate_choch_<side>`
     re-anchors that candidate to the swept extreme (the high a bearish leg's
     sweep grabbed / the low a bullish leg's sweep grabbed), but only to a
     *more extreme* level (higher for `candidate_choch_high`, lower for
     `candidate_choch_low`). Once price sweeps the prior pullback and then
     resumes the trend to a new leg extreme, the swept level — not the
     pre-sweep pullback — is where the eventual reversal launched from, so the
     CHoCH should break it (the SMC "sweep then expand" pattern). This only
     feeds step 2's continuation-gated promotion; a sweep with no follow-through
     never promotes, so the *validated* reference is untouched.

  3. **Validated reference is frozen**: once promoted, `validated_choch_high`
     stays until consumed by a CHoCH (reset to `None`) or replaced by the
     next genuine continuation promotion. Non-extending BOS do not overwrite
     it, and a sweep can only move the *candidate* (step 2b), never the
     validated level directly.

  A bullish CHoCH fires when, with `trend` BEARISH, a high pivot breaks
  (sustained, per the persistence rule above) above
  `validated_choch_high or choch_origin_high or active_high`; its
  `reference_price_level` is the reference it broke. The `active_high`
  cold-start fallback ensures the detector can flip trend during the
  bootstrap phase (before any validated/origin reference has been built),
  preventing the trend from getting stuck if the initial direction was wrong.
  A high pivot whose break does not hold is a `LIQUIDITY_SWEEP` (trend
  unchanged). A sweep never overwrites the *validated* CHoCH reference, but it
  re-anchors the pullback *candidate* to the swept extreme (step 2b above) so a
  later continuation can promote it.

  **Failed CHoCH (`CHOCH_FAILED`)**: a CHoCH is *provisional* until a
  same-direction BOS confirms it (that first BOS is beyond the CHoCH level by
  the staircase floor). While unconfirmed it carries an *origin*
  (`bull_choch_origin`/`bear_choch_origin` — the active low at a bullish CHoCH
  / active high at a bearish CHoCH, the swing the CHoCH move launched from). A
  sustained break back through that origin *before* a confirming BOS emits a
  `CHOCH_FAILED` (direction = the failed CHoCH's direction,
  `reference_price_level` = the broken origin) and flips the trend back. This
  supersedes the older `choch_origin` blind-spot recovery for the unconfirmed
  window at a tighter level. The origin is retired on the confirming BOS (the
  CHoCH can no longer fail) or at the next trend flip; a failed-CHoCH flip does
  not arm the opposite origin (one-shot, no ping-pong).

  **One-shot origin (blind-spot fallback)**: the moment a CHoCH fires, all
  validated/candidate state is reset. `choch_origin_<opposite>` is the
  extreme of the leg the CHoCH just reversed (set only by a *validated*-
  triggered CHoCH, one-shot). The CHoCH check uses `validated or origin`, so
  the origin serves as fallback until a validated reference is rebuilt. An
  origin-triggered CHoCH does NOT set origin on the opposite side (one-shot),
  breaking ping-pong chains.

  A `BREAK_OF_STRUCTURE`'s `reference_price_level` is the **formed low/high it
  broke** — the staircase floor (`last_bear_bos_low`/`last_bull_bos_high`) in
  effect at the state-advance, captured into `_PendingBOS.floor` before it
  ratchets to the breaking pivot — rather than the trailing `active_<side>` the
  state machine advanced on. So a continuation BOS reports (and plots at) the
  prior swing extreme it actually broke, forming a clean descending/ascending
  staircase of levels. The **first BOS of a leg** is seeded at the trend flip:
  `prev_bear_bos_extreme`/`prev_bull_bos_extreme` (the reported-floor tracker) is
  set at each CHoCH to the CHoCH's *confirming* extreme (`price` at the flip — the
  fundo/topo the reversal formed), so that first BOS references the level the leg
  actually launched from and, via the close-break re-anchor, confirms only on a
  close beyond it — rather than the trailing `active_<side>` that ratchets to a
  shallow retrace pivot during the pullback (the "reference climbs with trailing"
  bug: e.g. an M30 first bearish BOS reporting a 62,402 higher-low instead of the
  61,870 CHoCH fundo). This is distinct from the staircase *gate*
  (`last_bear_bos_low`/`last_bull_bos_high`), seeded at the CHoCH with the *broken*
  reference. The state machine, trailing references,
  and CHoCH promotion are unaffected — only the reported reference changes. A
  composition-level pass then re-times each BOS to the first *close* beyond that
  level and drops wick-only continuations (see `load_dashboard_data` below).

  The pivot loop above decides *which* event fires and *against which*
  reference level, but does not itself supply that event's `timestamp` for
  `BREAK_OF_STRUCTURE`, `LIQUIDITY_SWEEP`, and `CHANGE_OF_CHARACTER` — using
  the triggering pivot's own timestamp there would plot the marker at the
  extreme of the *new* leg (where the pivot forms) rather than the candle
  that actually broke the prior level, visually "lagging" the break. Instead,
  once a break is decided, a backward scan over the candles between the
  previous pivot of the same kind (exclusive) and the triggering pivot
  (inclusive) locates the actual breaking candle: `_common.find_wick_break_index`
  for `BREAK_OF_STRUCTURE`/`LIQUIDITY_SWEEP` (the first candle whose high/low
  wick crosses `active_<side>`, price-only), and `_common.find_sustained_break_index`
  for `CHANGE_OF_CHARACTER` (the first candle at which `is_sustained_break`
  against `validated_choch_<side>` holds). The emitted event's `timestamp` is
  that candle's timestamp; `price_level` remains the triggering pivot's own
  `price` — the true extreme of the move — and `reference_price_level` is
  unchanged either way (`active_<side>.price` or
  `validated_choch_<side>.price`). `LOWER_HIGH`/`HIGHER_LOW` labels are
  unaffected — they describe the pivot itself, not a break, so they keep the
  pivot's own timestamp/price.
- **`liquidity/detectors/_common.py`** — shared helpers used by both structure
  detectors:
  - `validate_candles`, `price_range`, `Pivot`, `collect_pivots` — unchanged
  - `is_sustained_break` — whether a break of `active_price` holds for
    `persistence_candles` consecutive closes
  - `find_wick_break_index` — first candle whose wick crosses a level (BOS/SWEEP
    timestamp attribution)
  - `find_close_break_index` — first candle whose **close** crosses a level;
    returns `None` if only a wick breach occurred (no confirming close)
  - `find_sustained_break_index` — first index at which `is_sustained_break`
    holds (CHoCH timestamp attribution)
  - `bos_confluence(candle, *, bullish)` — LuxAlgo-style shadow-balance check:
    `upper_shadow = high - max(close, open)`, `lower_shadow = min(close, open) - low`;
    bullish requires `upper_shadow > lower_shadow`, bearish the reverse. Mirrors
    LuxAlgo's "Confluence Filter" (`bullishBar`/`bearishBar` in Pine source).
- **`liquidity/detectors/poi.py`** — `POIDetector`: detects MSB-anchored order
  block zones, a **faithful batch port** of the "Market Structure Break &
  Order Block" TradingView indicator (EmreKb, MPL 2.0) — verified to
  reproduce the indicator's on-chart boxes exactly on real BTCUSDT 15m data
  (2026-07-11). **Self-contained**: `detect(candles) -> list[POIZone]` — it
  derives its own swing pivots rather than consuming structure events
  (deliberately a separate, simpler structure read than
  `InternalStructureDetector`). Constructor: `pivot_len` (default `9`, the
  indicator's "ZigZag Length") and `fib_factor` (default `0.33`).

  **Pivots (Pine `barssince` semantics)**: a rolling `pivot_len` window
  tracks the swing state — a candle whose high is the window max turns the
  swing up (`to_up`), one whose low is the window min turns it down. Each
  swing flip records the completed leg's extreme measured over a **local**
  window — the bars since the previous opposite *signal*
  (`ta.barssince(to_up[1])`, min 1 bar), **not** since the last opposite
  pivot. In choppy stretches these local windows are shorter than the full
  leg, renewing pivots faster and flipping the market state machine more
  often — porting this exactly is what makes the output match TradingView (a
  prior "leg extreme since the opposite pivot" variant produced fewer, later
  MSBs and missed real flips). The pivot index is the most recent bar whose
  own low/high equaled its running window extreme.

  **MSB**: with the market bullish, a new low pivot `l0 < l1 − fib_factor ×
  |h0 − l1|` confirms a bearish MSB (the bullish mirror breaks `h1` by
  `fib_factor × |h1 − l0|`). The market starts bullish. After a flip, both
  the high and low pivot **values** must change before another flip can fire
  (the indicator's `ta.valuewhen` guard, compared by value). The MSB
  confirms on the swing-flip candle that records the breaking pivot.

  **Zones**: each MSB emits up to two same-direction zones, anchored by
  **running scans** re-evaluated every bar exactly like the indicator,
  including its `[pivot_len]`-lagged window bound (the scan uses `l0i`/`h0i`
  as known `pivot_len` bars ago): `ORDER_BLOCK` = last opposite-direction
  candle in `h1i → l0i[pivot_len]` (bullish; bearish mirror in
  `l1i → h0i[pivot_len]`); `BREAKER_BLOCK`/`MITIGATION_BLOCK` = last
  *same*-direction candle in `l1i − pivot_len → h1i` (bullish; bearish
  mirror) — breaker when the impulse-origin extreme swept the prior one
  (bullish `l0 < l1`, bearish `h0 > h1`), else mitigation. Because the scans
  are running state, an anchor persists from earlier windows when the
  current window has no matching candle (faithful to the indicator). All
  zones span the anchor candle's full high-low range. Lifecycle (all
  kinds): `ACTIVE → INVALIDATED` on a **single close** beyond the far
  boundary, checked from the creation candle onward; touches inside the
  zone never retire it. There is no MITIGATED state and no RTO sweep events
  (removed with the old CHoCH→BOS detector).

- **`liquidity/detectors/consolidation.py`** — `detect_consolidation_ranges`
  and `stage_breakout_events`,
  **pure post-passes** (not a `MarketStructureDetector`): the first scans the
  quiet segments between structure advances for confirmed
  `ConsolidationRange`s.
  Inputs: the candle series plus `(candle index, established-trend direction)`
  advance boundaries; a range may never span an advance. Confirmation =
  `min_candles` candles inside a box no taller than `max_height_pct` (the
  caller resolves it as N × the series' mean true-range%) with alternating
  edge-zone touches (compressed top/bottom sequence ≥ 3 over the outer 25%
  zones, so a one-way drift inside the cap does not qualify). A confirmed box
  absorbs candles while total height stays within the cap; an unabsorbable
  poke either resolves the range (`is_sustained_break` beyond the boundary,
  `resolve_persistence` closes) or is a boundary sweep left outside the frozen
  box. Run at the composition level over the *surviving* internal event
  stream (see `load_dashboard_data`), **not** inside the detector — an
  in-detector variant was measured and reverted (a detector advance later
  dropped as wick-only split BTC H1's July 2026 box at an invisible point).
  `stage_breakout_events` (phase 2) stages one additive `MarketStructure`
  per range resolved by a sustained boundary break, at the breakout candle:
  a real BOS when the break continues the segment's standing trend (the
  direction of the advance that opened the segment), a `provisional=True`
  CHoCH when it reverses it (the additive contract — replay consumers skip
  it, the chart shows the dimmed `CHoCH?`), both referencing the broken
  boundary with `reference_timestamp` at its first forming candle. Nothing
  is staged for advance-resolved ranges, bootstrap segments, or when a real
  same-direction BOS/CHoCH sits within the dedup window of the breakout.
  Calibration + the motivating BTC/ETH H1 locks + the phase-2 measurement
  (+7/−0 on the live matrix, trend unchanged) are documented in
  `liquidity_hunter/docs/structure_decisions.md`.

All detectors are re-exported from `liquidity_hunter.liquidity`.

### Psychology layer (`liquidity_hunter/psychology`)

- **`psychology/analyzers/base.py`** — `RetailBiasEstimator`, the abstract
  port all retail bias estimators implement
  (`analyze(symbol, higher_timeframe_direction, market_structure_events,
  liquidity_zones, current_price) -> RetailBiasEstimate`). The plain-domain-type
  inputs double as a feature set, so a future ML-based estimator can
  implement the same interface as a drop-in replacement.
- **`psychology/analyzers/retail_trap.py`** — `RetailTrapAnalyzer`, a
  rule-based `RetailBiasEstimator`. Combines the higher timeframe trend,
  the most recent `MarketStructure` event, and nearby `LiquidityZone`s to
  estimate retail crowd psychology (e.g. "buying a perceived bottom against
  the higher timeframe trend").
- **`psychology/models.py`** — `RetailBiasEstimate`: `dominant_side`
  (`RetailPositioning`: LONG/SHORT/NEUTRAL), `confidence` (0-100), and a
  human-readable `explanation`. Distinct from `core.domain.RetailBias`,
  which represents a *measured* sentiment observation rather than an
  *inferred* one.

The full estimation logic (confidence formula and worked example) is
documented in `liquidity_hunter/docs/psychology.md`.
- **`psychology/analyzers/manipulation_cycle.py`** —
  `ManipulationCycleDetector`: connects existing observations (liquidity
  zones, `LIQUIDITY_SWEEP` events, BOS events, volume delta) into
  three-phase Wyckoff/SMC manipulation cycles. Works in two
  modes: **retrospective** (for each sweep event, looks backward for
  accumulation near a liquidity zone and forward for an expansion BOS) and
  **prospective** (scans active zones where price is currently consolidating,
  reporting `IN_PROGRESS` `ACCUMULATION` cycles). Constructor parameters:
  `proximity_pct` (default `0.015` = 1.5%), `min_accumulation_candles`
  (default `None` → resolved per timeframe from
  `_TIMEFRAME_MIN_ACCUMULATION`: M1=20, M5=15, M15=10, M30=7, H1=7, H4=3,
  D1=2, W1=2), `max_expansion_candles` (default `30`). Zone deduplication:
  nearby prospective zones are clustered per side within `proximity_pct`
  (keeping the strongest), and zones already targeted by a sweep-based cycle
  are excluded from prospective results via proximity matching.

- **`psychology/analyzers/behavior_divergence.py`** —
  `BehaviorDivergenceAnalyzer`: cross-references `volume_delta_series` with
  `LiquidityZone` proximity and `MarketStructure` events to detect when
  institutional flow opposes visible price direction. Produces
  `list[BehaviorDivergence]` with four divergence types:
  - **Distribution**: price rising + negative VD near a buy-side zone →
    institutional selling into retail buying.
  - **Accumulation**: price falling + positive VD near a sell-side zone →
    institutional buying into retail panic.
  - **Exhaustion**: VD magnitude declining after a BOS while price continues
    trending → move losing momentum.
  - **Absorption**: high volume + small price movement near a zone → large
    orders being absorbed at a key level.
  Constructor parameters: `window_size` (default `None` → resolved per
  timeframe from `_TIMEFRAME_WINDOW`: M1=20, M5=15, M15=10, M30=7, H1=7,
  H4=5, D1=5, W1=3), `proximity_pct` (default `0.02` = 2%),
  `min_price_change_pct` (default `0.005` = 0.5%), `min_vd_ratio` (default
  `0.1` = 10% of average volume). Deduplication keeps only the
  highest-confidence event per type within a window-sized range.

- **`psychology/analyzers/leverage_liquidation.py`** —
  `LeverageLiquidationEstimator`: builds a `LeverageLiquidationMap` from
  perpetual-futures market state. `estimate(symbol, timeframe, current_price,
  liquidity_zones, open_interest, funding, long_short) -> LeverageLiquidationMap`.
  Infers the over-leveraged side from a signed positioning score (funding sign
  + long/short account ratio, each normalized to [-1, 1] and averaged; OI
  growth amplifies `positioning_intensity`): score > `_NEUTRAL_THRESHOLD`
  (0.1) → LONG (crowded), < -threshold → SHORT, else NEUTRAL. Then projects
  `LiquidationBand`s around unmitigated liquidity-zone entries (midpoint =
  entry) at leverage tiers `_LEVERAGE_DISTANCE_PCT` (10x=9.5%, 25x=3.6%,
  50x=1.6%, 100x=0.6%, from Binance tier-1 maintenance margin). **Both sides**
  are emitted (long-liquidation pool below entries, `SELL_SIDE`; short-liquidation
  pool above, `BUY_SIDE`); the non-dominant side's intensity is dampened by
  `_NON_DOMINANT_FACTOR` (0.45) so the over-leveraged side stays prominent. Band
  intensity (0-100, peak-normalized across both sides) = `side_scale ×
  entry.weight × _LEVERAGE_POPULATION_PRIOR[lev]` (10x most common → hottest).
  Entry anchors come from `_entry_anchors`: liquidity zones with `strength > 0`
  **including mitigated ones** (real past entry areas, downweighted by
  `_MITIGATED_ENTRY_FACTOR`=0.7) **and order blocks** (`poi_zones`, weight
  `_POI_ENTRY_WEIGHT`=1.0, invalidated dropped — order
  blocks concentrate real institutional volume), merged within
  `_ENTRY_CLUSTER_PCT` (0.4%,
  keep strongest), then at most `_MAX_ENTRY_CLUSTERS` (16) kept **spread evenly
  across price** via `_bucket_select` (strongest per equal-width price bucket) —
  so coverage isn't monopolized by the densest cluster and bands appear across
  the whole range, not just one region. NEUTRAL positioning or empty inputs →
  no bands.
  Each kept band is time-bounded via `candles`: `start_time = zone.formed_at`,
  `end_time = _liquidation_hit_time(...)` (first candle at/after start whose
  wick reaches the liquidation level, `None` if never — still live). The
  hit-scan runs only for the top-`_MAX_BANDS` bands.

- **`psychology/analyzers/oi_regime.py`** — `OIRegimeAnalyzer`: produces an
  `OIAnalysis` from candles + `OpenInterestPoint` history + structure events.
  `analyze(candles, open_interest, structure_events) -> OIAnalysis`. Two
  outputs: (1) **current regime** — the price × OI matrix over a rolling
  window (timeframe-adaptive `_TIMEFRAME_WINDOW`, same values as
  `BehaviorDivergenceAnalyzer`), `FLAT` unless both `min_price_change_pct`
  (default 0.2%) and `min_oi_change_pct` (default 0.3%) floors are met,
  intensity saturating at 4× each floor; (2) **event qualification** — for
  each BOS/CHoCH/`LIQUIDITY_SWEEP` (pivot labels and `CHOCH_FAILED` are
  skipped), the OI delta measured from `window` candles before the event
  through **one candle after** it (OI samples mark period ends, so the
  breaking candle's own OI change lands at the next sample — required to see
  a sweep's liquidation flush). A sweep with OI dropping ≥ `flush_oi_drop_pct`
  (default 0.5%) is a `FLUSH`; otherwise ±`min_oi_change_pct` splits
  `NEW_MONEY`/`COVERING`/`FLAT`. Events outside OI coverage are skipped, not
  guessed. OI alignment is by bisect (latest sample at/before each timestamp).

All seven are re-exported from `liquidity_hunter.psychology`.

### Scoring layer (`liquidity_hunter/scoring`)

- **`scoring/engine.py`** — `LiquidityScoringEngine.score(zones, current_price)`
  ranks `LiquidityZone` objects as liquidity targets, returning
  `list[ScoredLiquidityZone]` sorted by descending score (0-100).
- **`scoring/models.py`** — `ScoredLiquidityZone`: a zone plus its
  composite `score` and the three component scores (`distance_score`,
  `touch_score`, `timeframe_score`).
- **`scoring/weights.py`** — `DEFAULT_TIMEFRAME_WEIGHTS`, the per-timeframe
  weighting used by the `timeframe_score` factor.

The full scoring methodology (formulas and worked examples) is documented
in `liquidity_hunter/docs/scoring.md`. All three are re-exported from
`liquidity_hunter.scoring`.

### Examples (`liquidity_hunter/app/examples`)

Runnable scripts demonstrating module usage. Each exposes a `main(provider=...)`
function so it can be tested with a fake provider (no network) — see
`liquidity_hunter/tests/app/examples`. Run with:

```bash
poetry run python -m liquidity_hunter.app.examples.fetch_btcusdt_1h
poetry run python -m liquidity_hunter.app.examples.detect_btcusdt_liquidity
poetry run python -m liquidity_hunter.app.examples.score_btcusdt_liquidity
poetry run python -m liquidity_hunter.app.examples.estimate_btcusdt_retail_bias
```

### Composition root (`liquidity_hunter/app/dashboard_data.py`)

- **`DashboardData`** — a frozen dataclass snapshot combining `candles`,
  `higher_timeframe_direction`, `liquidity_zones`, `ranked_zones`,
  `market_structure_events`, `internal_structure_events`, `retail_bias`,
  `poi_zones` (`list[POIZone]`),
  `manipulation_cycles` (`list[ManipulationCycle]`),
  `behavior_divergences` (`list[BehaviorDivergence]`),
  `liquidity_heatmap` (`LiquidityHeatmap | None`),
  `liquidation_map` (`LeverageLiquidationMap | None`),
  `narrative` (`MarketNarrative | None`), `oi_analysis`
  (`OIAnalysis | None`), `liquidity_hunt` (`LiquidityHuntState | None`),
  `higher_timeframe` (`TimeFrame | None` — the `_HIGHER_TIMEFRAME_MAP` anchor
  pair `higher_timeframe_direction` was measured on, `None` for the top
  timeframe; lets the frontend label readings "vs 4H" instead of a generic
  "HTF"), and `consolidation_ranges` (`list[ConsolidationRange]` — confirmed
  lateral ranges overlapping the visible window, from the
  `_detect_consolidations` post-pass inside `_run_internal_structure`:
  `detect_consolidation_ranges` over the *surviving* non-provisional
  BOS/CHoCH/`CHOCH_FAILED` boundaries, height cap
  `_CONSOLIDATION_MAX_HEIGHT_ATR` = 8 × mean TR%, `_CONSOLIDATION_MIN_CANDLES`
  = 60, resolve persistence 4 — calibrated 2026-07-14, see
  `docs/structure_decisions.md`) for one symbol/timeframe. Under
  `_CONSOLIDATION_STAGE_BREAKOUT_EVENTS` (default `True`), range breakouts
  also stage additive events into `internal_structure_events` via
  `stage_breakout_events` (deduped within
  `_CONSOLIDATION_STAGE_DEDUP_CANDLES` = 12 of a real same-direction
  BOS/CHoCH; merged timestamp-sorted).
- **`load_dashboard_data(provider=..., symbol=..., timeframe=..., limit=1200, swing_lookback=..., confluence_filter=False, futures_provider=...)`**
  — fetches a single buffered candle series (`buffered_candles`) and derives the
  visible `candles` from its tail (`buffered_candles[-limit:]`; no separate fetch
  for the visible window — its second fetch would be redundant and could race a
  freshly-printed candle). It then runs all liquidity detectors and scores the
  zones via `LiquidityScoringEngine`, runs `SwingStructureDetector` on the
  buffered series and
  `InternalStructureDetector` on a **structurally anchored** slice of it to
  populate `market_structure_events` and `internal_structure_events`
  respectively, both filtered to the visible window. The internal detector's
  base `swing_lookback`/`persistence_candles` are resolved **per timeframe** from
  `_INTERNAL_STRUCTURE_PARAMS` (currently a uniform `(5, 12)` for every timeframe
  M5→W1, matching `_DEFAULT_INTERNAL_PARAMS = (5, 12)`; the per-TF dict is kept so
  timeframes can diverge again without touching the wiring) — so the constructor
  defaults (`swing_lookback=2`/`persistence_candles=5`) apply only to a
  directly-built detector, not the production wiring. The internal detector's
  output is passed through **`_reanchor_bos_close_break`** before the visible
  filter: each `BREAK_OF_STRUCTURE` is
  re-timed to the first candle that *closes* beyond the formed level it broke
  (`reference_price_level`), within the window the BOS stays active (up to the
  next same-direction BOS or opposite-direction CHoCH); any BOS whose leg only
  *wicked* past that level (never closed) is **dropped** — a conservative
  close-break confirmation matching the macro SMC cycle. The pass also sets each
  BOS's `reference_timestamp` to the candle that *formed* the broken level (the
  prior swing extreme, found by scanning back for a matching low/high), so the
  frontend can start the line at the level's origin. The same pass runs on the
  major detector's events too (`all_major_events`), keeping the two consistent.
  A second pass, **`_drop_pre_break_reference_bos`** (both streams, after the
  re-anchor), drops any continuation BOS whose `reference_timestamp` predates
  the confirming close of the previous same-direction BOS in the same leg: a
  wick that poked beyond the still-unbroken prior BOS level ratchets the
  detector's staircase extreme, so the next continuation would report that
  pre-break wick as the formed level it broke — but a reference may only come
  from price action *after* the prior break confirms. A CHoCH resets the
  constraint for its direction (the first BOS of a leg references the
  CHoCH-seeded level, formed before the flip); a non-provisional `CHOCH_FAILED`
  likewise resets the *opposite* direction (the leg it flips into); BOS without
  a resolved `reference_timestamp` are kept. Same-timestamp BOS are judged
  earlier-formed-reference first (the earlier structural break). The
  `reference_timestamp` itself (the line's start anchor, purely cosmetic) is
  resolved by `_common.resolve_break_origin_timestamp` — own-side exact →
  opposite-side exact (a first-BOS floor is the reversal's opposite-polarity
  extreme) → range-straddle — used both here (to fill a `None` the detector's
  own-side scan left) and by the provisional-BOS path in the detector.
  A third pass, **`_drop_resumed_fizzle_markers`** (internal stream only, after
  the BOS passes), drops a fast-fizzle `CHOCH_FAILED` marker followed by a
  chart-surviving same-direction BOS — the reclaim was a deep pullback the
  reversal recovered from, not a fizzle (see the fizzle-marker status block).
  `confluence_filter` is exposed for tests that exercise state-machine logic
  without needing emission-quality filters. `higher_timeframe_direction` (as of
  2026-07-06) is the **state-machine trend** (`final_trend`) of the **internal**
  detector run on the **higher** timeframe (mapped via `_HIGHER_TIMEFRAME_MAP`)
  with that timeframe's own production wiring — built by
  **`_build_internal_detector(timeframe, confluence_filter=...)`**, the single
  construction point shared with the current-TF internal run (per-TF
  params + all flags), fed the HTF series fetched at the same `buffered_limit`
  and sliced from its own `_structural_anchor_index` — i.e. **exactly the run
  the HTF view renders**, so the reported HTF direction always matches the
  structure the user sees when opening that timeframe, and the liquidity hunt's
  "counter-trend?" comparison uses the same trend semantics on both sides of
  the pair. (The previous source — `SwingStructureDetector` on a 100-candle
  window, `_HIGHER_TIMEFRAME_CANDLE_LIMIT`, now removed — used a different
  methodology on a window too short for its lookback: measured 2026-07-06
  across BTC/ETH/SOL/AAVE × 5m..1d, 11/24 combos changed — AAVE intraday and
  BTC 1h/4h read a bootstrap `NEUTRAL` (hunt card invisible) and BTC intraday
  read H1 `bullish` against an H1 chart showing a bearish CHoCH; SOL, the live
  hunt scenario, was unchanged.) For the top timeframe (no higher TF) it falls
  back to the current run's `internal_detector.final_trend`, so downstream
  comparisons read "aligned". Using the detector's `final_trend` rather than
  the last event's `direction` avoids spurious flips from descriptive
  HH/HL/LH/LL pivots or `LIQUIDITY_SWEEP` events (whose `direction` is the
  pivot/wick side, not the standing trend); `InternalStructureDetector` now
  exposes `final_trend` mirroring the major's (provisional marks never mutate
  it, `CHOCH_FAILED` reverts it).

  `buffered_candles` is fetched with an extra
  `_INTERNAL_STRUCTURE_BOOTSTRAP_BUFFER = 300` candles of history prepended
  beyond `limit` (`buffered_limit = min(limit + _INTERNAL_STRUCTURE_BOOTSTRAP_BUFFER,
  provider.max_fetch_limit)` — the cap comes from the provider: 1000 for spot,
  1500 for the futures default). The **major** detector runs on the full
  `buffered_candles`; the **internal** detector (and `POIDetector`, which runs
  on the same slice) instead start at a **structural anchor** —
  `_structural_anchor_index(buffered_candles, visible_start)`, the index of the
  most recent *major extreme* (lowest low / highest high, whichever is more
  recent) within `_STRUCTURAL_ANCHOR_REGION = 300` candles before the visible
  window. A fixed candle offset would land the `NEUTRAL`→first-break bootstrap on
  whatever pivot sits there, inheriting a stale far-back regime (e.g. a
  months-old downtrend carried into a window that has since clearly reversed) and
  producing a late, wrong-direction first CHoCH; anchoring at the move's
  structural origin seeds the trend from the price action actually entering the
  window, while staying stable across refreshes (a major extreme is a fixed price
  point, not a sliding offset). The anchor falls back to `0` when the provider
  returns no pre-visible buffer. Both `market_structure_events`,
  `internal_structure_events`, and `poi_zones` are filtered
  to the calendar range `[candles[0].timestamp, candles[-1].timestamp]` after
  detection (`poi_zones` by `created_at`). `candles` (the visible window, the
  trailing `limit` of `buffered_candles`) is unaffected.

  After all detectors run, `ManipulationCycleDetector().detect(candles,
  all_structure, liquidity_zones, volume_delta_series(candles))`
  populates `manipulation_cycles`.

  `BehaviorDivergenceAnalyzer().analyze(candles, vd, liquidity_zones,
  all_structure)` populates `behavior_divergences`.

  `LiquidityHeatmapEngine().build(...)` populates `liquidity_heatmap`. A
  separate `futures_provider` arg (`FuturesDataProvider | None`, defaults to
  `BinanceFuturesDataProvider()`) fetches open interest / funding /
  long-short ratio **once** (`_fetch_futures_state`, OI requested with
  `limit=limit` so the paginated history spans the visible window, capped by
  Binance's ~30-day OI retention). The state feeds both
  `LeverageLiquidationEstimator().estimate(...)` (→ `liquidation_map`; it
  receives only the tail `_LIQUIDATION_OI_POINTS = 500` OI points so its
  `open_interest_change_pct` horizon is unchanged) and
  `OIRegimeAnalyzer().analyze(candles, open_interest,
  internal_structure_events)` (→ `oi_analysis` — the internal events are the
  ones the chart renders, so the qualified events match the drawn labels).
  The fetch is wrapped in try/except `DataProviderError`: a symbol with no
  perpetual contract, or an unreachable venue, degrades to
  `liquidation_map=None` **and** `oi_analysis=None` rather than failing the
  whole snapshot. Tests must inject a fake `futures_provider` to avoid
  network.

  Finally, `NarrativeEngine().build(data)` synthesizes all outputs into a
  `MarketNarrative` (timeline, anomalies, phase-dependent summary,
  confluence count), and `LiquidityHuntEngine().build(data)` synthesizes the
  `LiquidityHuntState`. Both run last via `dataclasses.replace` since they
  depend on the fully assembled `DashboardData`.

- **`app/narrative.py`** — `NarrativeEngine`: composition-level synthesizer
  that builds a `MarketNarrative` from a completed `DashboardData`. Lives in
  `app/` (not `psychology/`) because it depends on outputs from every layer.
  `build(data) -> MarketNarrative` produces:
  - **Timeline**: chronological `list[NarrativeEvent]` mapped from structure
    events (major + internal BOS/CHoCH/SWEEP), manipulation cycle phases
    (consolidation/sweep/expansion), and behavior divergences. Deduplicated
    by `(timestamp, event_type)`, keeping the higher-priority source
    (`manipulation_cycle` > `behavior_divergence` > `market_structure`).
  - **Anomalies**: `list[NarrativeAnomaly]` detecting pattern contradictions:
    expansion + exhaustion (HIGH), accumulation + distribution (MEDIUM),
    concentrated liquidity on one side (MEDIUM/HIGH), unconfirmed CHoCH
    (MEDIUM), BOS without sustained VD (MEDIUM).
  - **Phase**: the `ManipulationPhase` of the latest active cycle, or `None`.
  - **Summary**: phase-dependent institutional tone incorporating retail bias,
    HTF alignment, and VD context. Phases: neutral, accumulation
    ("smart money absorbing supply"), manipulation ("stops swept, cascading
    liquidation, retail trapped"), expansion ("impulsive move, sustained VD"),
    failed ("expansion failed to materialize, cycle invalidated").
  - **Confluence**: `(count, total)` — how many detection layers agree on
    direction (structure, manipulation cycle, behavior divergence, HTF).

- **`app/liquidity_hunt.py`** — `LiquidityHuntEngine`: composition-level
  synthesizer that builds a `LiquidityHuntState` from a completed
  `DashboardData` (like `NarrativeEngine`, it lives in `app/` because it
  cross-references structure, liquidity, and psychology outputs).
  `build(data) -> LiquidityHuntState`:
  - **Current-TF trend**: replays `internal_structure_events`
    (non-provisional BOS/CHoCH set the trend, `CHOCH_FAILED` reverts it;
    pivot labels/sweeps ignored) into a trend + flip timestamp (the event
    that started the current corrective leg). Counter-trend = that trend
    opposes `higher_timeframe_direction` (the existing
    `_HIGHER_TIMEFRAME_MAP` pair) → `hunted_side` SHORT under a bullish HTF,
    LONG under a bearish one; aligned/neutral → phase `NONE`.
  - **Targets**: equal-highs zones (hunted shorts) / equal-lows (hunted
    longs) — intact if unmitigated and beyond price, captured if
    `invalidated_at >= flip` (older sweeps are excluded, they belong to prior
    legs) — plus `LiquidationBand`s on the hunted side (`BUY_SIDE` above for
    shorts), live (`end_time=None`, beyond price) = intact, `end_time >= flip`
    = captured; bands clustered within 0.4% are one pool (strongest member
    represents it, intact while any member is live). The "nearby" bound is
    **volatility-normalized** (as of 2026-07-06): `proximity_atr` (wired
    **2.0** via `_HUNT_PROXIMITY_ATR`) × the visible series' mean true-range%
    of price, falling back to `proximity_pct` (default `0.02`) when unset or
    the series is under 2 candles. The fixed 2% was ~6 ATR on a calm BTC 15m
    (mapping too many pools for the strict all-captured gate to ever clear)
    but under 0.5 ATR on a volatile daily (mapping none — AAVE 4h sat at
    "hunting 0/0" forever; with N=2 it reads an honest captured 3/3, and ETH
    1d gets a map at all). N=3 measured worse (pulled a ~3-ATR pool into SOL
    4h and regressed its conclusion).
  - **Evidence**: `last_flush_timestamp` = latest `OIQualifiedEvent` with
    `participation=FLUSH` in the capture direction since the flip;
    capture-side `LIQUIDITY_SWEEP` since the flip; `oi_unwinding` =
    `current_regime` is `SHORT_COVERING` (hunted shorts) /
    `LONG_LIQUIDATION` (hunted longs).
  - **Phase**: `CAPTURED` only when all mapped pools are captured **and**
    not `oi_unwinding` (`captured_at` = last capture); any capture / flush /
    sweep / unwinding → `HUNT_IN_PROGRESS`; else `COUNTER_TREND`. With zero
    mapped pools the state never reaches `CAPTURED` (conservative).

- **`app/overview.py`** — multi-timeframe structural overview (the sidebar
  "Structure Ladder", as of 2026-07-11). Split in two stages so the API can
  cache each timeframe independently:
  - **`load_timeframe_structure(provider, symbol, timeframe, limit,
    confluence_filter)`** — the cacheable I/O unit for one timeframe. Runs the
    exact production internal-structure pipeline `load_dashboard_data` uses
    (the shared **`dashboard_data._run_internal_structure`** helper: buffered
    fetch, structural anchor, per-TF detector wiring via
    `_build_internal_detector`, both composition passes) plus equal-level zone
    detection + `mark_swept_zones`, returning a `TimeframeStructureSnapshot`
    (candles, visible events, `final_trend`, EQL zones).
  - **`build_overview(symbol, snapshots)`** — pure assembly into a
    `core.domain.MarketOverview` of `TimeframeOverview` entries: per timeframe
    the detector's `final_trend` (**exactly the trend the chart renders** —
    same pipeline), the last non-provisional BOS/CHoCH/`CHOCH_FAILED` (+
    direction, timestamp, candles-ago), any provisional live-edge
    `BOS?`/`CHoCH?` as `forming_event` (the fizzle `CHOCH_FAILED` marker is
    excluded — provisional but not "forming"), and a `LiquidityHuntEngine`
    summary (phase, hunted side, captured/total) computed against the
    `_HIGHER_TIMEFRAME_MAP` anchor's trend **from the same snapshot batch**
    (no duplicate HTF fetches; W1 or a missing anchor degrades to the entry's
    own trend = "aligned", the `load_dashboard_data` fallback). The hunt runs
    on a slim `DashboardData` (EQL zones + events only; `liquidation_map` and
    `oi_analysis` deliberately `None` — the documented graceful degradation,
    so ladder hunt phases are structure+EQL-based; the full OI-qualified hunt
    stays on `/api/dashboard`).
  - **`load_overview(provider, symbol, timeframes, limit, confluence_filter)`**
    composes both over the default ladder `OVERVIEW_TIMEFRAMES` (M5→W1).
  Purely descriptive throughout: a state reading per timeframe, not a signal.

`load_dashboard_data` also accepts **`compute_narrative`** (default `True`;
`False` skips the `NarrativeEngine` synthesis entirely, `narrative=None`) and
its buffered-fetch + internal-detection front half now lives in
`_run_internal_structure` (returning an `InternalStructureRun`), shared with
the overview and the HTF-trend run so all three stay byte-identical;
`default_ohlcv_provider()` builds the production fallback provider chain.

`DashboardData`, `LiquidityHuntEngine`, `NarrativeEngine`,
`ScoredLiquidityZone`, `TimeframeStructureSnapshot`, `OVERVIEW_TIMEFRAMES`,
`build_overview`, `load_overview`, and `load_timeframe_structure` are
re-exported from `liquidity_hunter.app`.

### API layer (`liquidity_hunter/api`)

A FastAPI app exposing `app.load_dashboard_data` output as JSON, depending
only on `app` and `core` (an alternative presentation layer to
`dashboard`):

- **`api/main.py`** — `app = FastAPI(...)`, with CORS enabled (open, for a
  future separate frontend) and the routers below registered. Run with:

  ```bash
  poetry run uvicorn liquidity_hunter.api.main:app --reload
  ```

- **`api/routes/health.py`** — `GET /api/health` returns `{"status": "ok"}`.
- **`api/routes/dashboard.py`** — `GET /api/dashboard` (query params
  `symbol`, `timeframe`, `limit`, `swing_lookback`,
  defaults matching `load_dashboard_data`) calls `load_dashboard_data`
  directly (no duplicated logic) and returns a `DashboardDataResponse`.
  Results are
  cached per parameter combination via `api/cache.TTLCache`, with a 10s TTL
  (shorter than `cache.DEFAULT_TTL_SECONDS = 300`, since the frontend polls
  this endpoint to keep the dashboard near-live) to avoid redundant Binance
  requests. The `narrative` query param (default **`false`**, as of
  2026-07-11) gates the narrative/anomaly synthesis: off by default while the
  multi-TF overview occupies the sidebar slot (`narrative=null` in the
  response, so the frontend `NarrativePanel` auto-hides); `narrative=true`
  re-enables it. The library-level `compute_narrative` default stays `True`.
- **`api/routes/overview.py`** — `GET /api/overview` (query param `symbol`)
  returns a `core.domain.MarketOverview` (the domain model is the response
  model directly — no mirror schema needed). Each timeframe's
  `TimeframeStructureSnapshot` is cached per `(symbol, timeframe)` with a
  **timeframe-proportional TTL** (`_SNAPSHOT_TTL_SECONDS`: M5=30s, M15=60s,
  M30=90s, H1=120s, H4=300s, D1=600s, W1=1200s — a reading changes at most
  once per candle), while the cross-timeframe assembly (`build_overview`)
  is recomputed per request. A cold overview costs one buffered-klines fetch
  per ladder timeframe (~2.5s); warm requests only refresh expired intraday
  snapshots.
- **`api/cache.py`** — `TTLCache`, a minimal generic in-memory
  time-based cache (`get_or_set(key, factory, ttl_seconds=None)`; the
  optional per-call `ttl_seconds` overrides the cache-wide TTL for entries
  that age at different rates, e.g. the per-timeframe overview snapshots).
- **`api/schemas.py`** — `DashboardDataResponse`, a Pydantic `BaseModel`
  (`from_attributes=True`) mirroring the `DashboardData` dataclass fields,
  used to serialize it to JSON; nested domain types (`Candle`,
  `LiquidityZone`, `MarketStructure`, `ScoredLiquidityZone`,
  `RetailBiasEstimate`, `POIZone`, `ManipulationCycle`) are
  already `DomainModel`s and serialize as-is. `poi_zones`,
  `manipulation_cycles`, `behavior_divergences`,
  `liquidity_heatmap`, `liquidation_map`, `narrative`, `oi_analysis`,
  `liquidity_hunt`, `higher_timeframe`, and `consolidation_ranges` fields are
  included.

Tested with FastAPI's `TestClient` in `liquidity_hunter/tests/api/test_main.py`.

### React frontend (`frontend/`)

A React + TypeScript + Vite project (Tailwind CSS, Lightweight Charts v4),
separate from the Python package, that polls `GET /api/dashboard` and renders
the dashboard data.

The React frontend has a professional TradingView-style dark UI with a
`Logo` component, `StatusBar` (live connection indicator, candle/event
counts, clock), `LoadingSkeleton`, and header with symbol badge + timeframe
selector.

- **`frontend/src/components/MainChart.tsx`** — `MainChart` component:
  renders three synced Lightweight Charts panes (main candlestick, volume
  delta histogram, RSI indicator) with synchronized time scales and
  crosshairs. The main pane overlays top-ranked liquidity zone lines, draws
  BOS/CHoCH/SWEEP horizontal lines and labels (plus a grey `CHoCH ✕` line at
  the broken origin for `choch_failed` events), renders POI order block boxes
  via `POIBoxesPrimitive`, and renders manipulation cycle accumulation boxes
  via a second `POIBoxesPrimitive` instance (toggled via
  `showManipulationBoxes` prop). Accumulation boxes are color-coded by
  status: amber (`in_progress`), green (`confirmed`), gray (`failed`).
  Limited to `MAX_MANIP_BOXES = 3` most relevant (in-progress first).

  **Consolidation range boxes**: a third `POIBoxesPrimitive` instance draws
  each `data.consolidation_ranges` entry as a neutral slate `▭ RANGE` box
  (`CONSOLIDATION_BOX_STYLES`, live ranges slightly stronger than resolved
  ones, resolved boxes labeled with the breakout direction arrow); a live
  range extends to the right edge via the far-future-sentinel clamp. Toggled
  by the `▭ Range` toolbar button in `App.tsx` (`showConsolidationRanges`
  prop, default **on**). Range boxes do **not** terminate BOS/CHoCH lines —
  a truncate-at-range-start variant was built and reverted on visual review
  (2026-07-14): the reference lines must keep running through the box, and
  the stale-line problem is solved by the staged breakout event that ends
  them at the range's resolution instead.

  **Volume delta pane**: histogram bars colored by candle direction
  (`CANDLE_UP_COLOR`/`CANDLE_DOWN_COLOR`), computed as
  `2 * taker_buy_volume - volume` per candle.

  **RSI pane**: RSI(14) line with 70/30 reference lines and regular
  divergence detection (bullish: price LL + RSI HL below 50; bearish:
  price HH + RSI LH above 50). Divergence lines drawn as colored
  `LineSeries` overlays.

  **BOS/CHoCH line rendering**: each event draws a horizontal line to the next
  event that terminates it (`structureLineEndTime`): BOS lines end at the next
  same-direction BOS or opposite-direction (non-failed) CHoCH; CHoCH lines at
  the next opposite-direction CHoCH (so a reversal clears stale references
  rather than letting them run to the chart edge). **Both BOS and CHoCH lines
  are drawn at `reference_price_level`** (the level that was broken — the formed
  swing extreme for BOS, the validated swing for CHoCH), not `price_level`,
  since the breaking pivot's extreme can be far beyond the confirmed level. Both
  also **start at `reference_timestamp`** (the candle that *formed* the broken
  level — the prior swing extreme for BOS, the promoted LH/HL for CHoCH), so the
  line runs from the level's origin to where it was broken rather than starting
  at the break. SWEEP lines are drawn at `price_level` (the sweep wick's
  extreme), starting at the event `timestamp`. A CHoCH with
  `reference_structural === false` (a weak reference — re-anchor/fallback level
  or wick-only-break promotion, barrier-governed) renders **dotted and dimmed**
  (`SparseDotted`, color + `99` alpha) with a `*` label suffix (`CHoCH* ▼`),
  so a conservative-sequence CHoCH (structural leg origin, solid dashed
  `CHoCH ▼`) is distinguishable at a glance. A BOS **or CHoCH** with
  `provisional === true` (a live-edge continuation whose floor closed-broke, or a
  live-edge reversal whose structural reference was sustained-closed-broken, but
  whose confirming pivots have not formed yet) gets the same dimmed/`SparseDotted`
  treatment with a `?` suffix (`BOS? ▼` / `CHoCH? ▼`), so it reads as "forming"
  until the confirmed event supersedes it (or it vanishes if the move fails).
  Provisional marks are also excluded from line *termination*
  (`!other.provisional` in `structureLineEndTime`): a forming mark never truncates
  a confirmed BOS/CHoCH line — it only draws its own dimmed line to the edge.

- **`frontend/src/components/ManipulationCyclesPanel.tsx`** —
  `ManipulationCyclesPanel` sidebar component: renders manipulation cycle
  cards sorted by relevance (in-progress first, then confirmed, then failed),
  limited to `MAX_DISPLAY = 5`. Each card shows direction arrow, phase badge
  (`ACC`/`MANIP`/`EXP`), status indicator (`LIVE` with pulse animation,
  `CONFIRMED`, `FAILED`), target zone, consolidation candle count, sweep
  info, expansion BOS info, and volume delta. Includes a `CHART ON`/`OFF`
  toggle button that controls the `showManipulationBoxes` prop on `MainChart`.

- **`frontend/src/components/MultiTimeframePanel.tsx`** — the **Structure
  Ladder** sidebar panel (as of 2026-07-11, first panel in the sidebar): one
  compact row per `TimeframeOverview` entry (M5 → W1) showing the timeframe
  chip, trend (`▲ BULL` / `▼ BEAR` / `◆ FLAT`, directional colors), the last
  structural event with candles-ago (`BOS ▲ ·12c`), a dimmed forming chip for
  provisional marks (`BOS? ▼`), a slate `▭ RANGE ·Nc` chip when the
  timeframe is inside a confirmed consolidation (`in_consolidation` /
  `consolidation_candles`), and a hunt-phase chip (`⚠` counter-trend /
  `⚡ x/y` hunting / `✓` captured). The `CollapsibleSection` header shows an
  alignment summary (`6▲ 1▼`); the full reading is each row's hover title.
  **Clicking a row switches the chart timeframe** (`switchChartTimeframe`,
  the chart-only divergence — global panels stay on the selected timeframe).
  `App.tsx` polls `GET /api/overview` every `OVERVIEW_REFRESH_INTERVAL_MS =
  30s` per symbol (transient failures keep the last ladder rather than
  tearing the dashboard down). The `NarrativePanel` (which exists and renders
  whenever `data.narrative` is non-null) auto-hides now that `/api/dashboard`
  defaults `narrative=false` — re-enabling the query param brings it back
  with zero frontend changes.

- **`frontend/src/charting/POIBoxesPrimitive.ts`** — `POIBoxesPrimitive`
  implements `ISeriesPrimitive` and draws filled canvas rectangles for each
  POI zone. Colors: light blue (`#64b5f6` / `#2979ff`) for bullish demand
  zones, red (`#ef5350`) for supply zones. Box border: 1.5px. Active fill
  opacity: ~18% (`#2979ff2e`). Each box starts at the order block candle
  (`ob_candle_timestamp`) and its right edge extends to `invalidated_at` (the
  candle whose close broke the zone); while the zone is ACTIVE, a far-future
  sentinel timestamp is used so `timeToCoordinate` returns `null` and the
  right edge is clamped to `mediaSize.width` (full pane width). Only ACTIVE
  zones are drawn (`selectVisiblePoiZones` drops invalidated ones and keeps
  the most recent few per direction near price).
  Also reused for manipulation cycle accumulation boxes (second instance) and
  consolidation range boxes (third instance).

- **`frontend/src/charting/LiquidationBandsPrimitive.ts`** —
  `LiquidationBandsPrimitive` implements `ISeriesPrimitive` and draws
  leverage-liquidation bands as **time-bounded** horizontal boxes on the main
  pane (modeled on `POIBoxesPrimitive`): each spans `x0` (entry-cluster
  formation) to `x1` (liquidation-hit time, or a far-future sentinel →
  clamped to the right edge if still live). Color encodes the **leverage tier**
  (`LIQUIDATION_LEVERAGE_COLORS`, warmer = higher leverage: 10x amber → 100x
  crimson — the estimator emits only one side per snapshot, so color is free
  to encode leverage rather than side), opacity scales with `intensity`
  between `LIQUIDATION_MIN_ALPHA` and `LIQUIDATION_MAX_ALPHA`, with a center
  line and a leverage tag (`10x`/`25x`/…) at the left edge. Toggled by the
  `showLiquidationBands` prop (the `⊟ Liq` toolbar button in `App.tsx`).
  `MainChart.selectVisibleLiquidationBands` declutters the render to a relevant
  subset near current price — still-live (untriggered) pools plus a few most
  recent hits — within `LIQ_PRICE_WINDOW` (±8%), capped at `LIQ_MAX_BANDS` (12),
  **balanced across both sides of price** (`balancedTake`, so above/below stay
  visible) and ranked by a **proximity-weighted relevance** (0.6 proximity +
  0.4 intensity) so the nearest live pools surface instead of far-but-strong
  ones. The **full** band
  set stays in `liquidation_map.bands` (API) for backtesting; only the chart is
  filtered. Live pools render with a solid center line; already-hit (consumed)
  levels render fainter with a dashed line (`HIT_ALPHA_FACTOR`); the leverage
  tag is drawn only above `TAG_MIN_INTENSITY`. The `⊟ Liq` toolbar button
  toggles visibility on plain click; Alt/Shift-click toggles a "live pools only"
  mode (`liquidationLiveOnly`, shown as `⊟ Liq •`).

- **`frontend/src/types/dashboard.ts`** — TypeScript types mirroring the API
  schema; includes `POIZone`, `MarketStructure` (with
  `reference_timestamp`, `reference_structural`, `provisional`),
  `ManipulationCycle`, `ManipulationPhase`,
  `ManipulationCycleStatus`, `BehaviorDivergence`, `DivergenceType`,
  `LiquidityHeatmap`, `HeatmapBucket`, `LeverageLiquidationMap`,
  `LiquidationBand`, `MarketNarrative`, `NarrativeEvent`, `NarrativeAnomaly`,
  `NarrativeEventType`, `AnomalySeverity`, `OIAnalysis`, `OIRegimeReading`,
  `OIQualifiedEvent`, `OIRegime`, `OIParticipation`, `LiquidityHuntState`,
  `LiquidityHuntTarget`, `LiquidityHuntPhase`, `LiquidityHuntTargetKind`,
  `ConsolidationRange`, `ConsolidationStatus`;
  `DashboardData.higher_timeframe` (`TimeFrame | null`) and
  `DashboardData.consolidation_ranges`; `TimeframeOverview.in_consolidation`
  / `consolidation_candles`.

- **Liquidity Hunt KPI card** (frontend, as of 2026-07-06): the KPI row reads
  left-to-right as a story ending in the hunt "conclusion" card — the
  **Price card was removed** to keep the grid at `md:grid-cols-5` (price
  remains visible in the chart toolbar OHLC): Retail Bias, Dominant
  Liquidity, HTF Trend, OI Regime, **Liquidity Hunt**. `huntCardProps` in
  `KpiRow.tsx` maps `data.liquidity_hunt.phase` to presentation:
  `none` → `◆ —` / "structure aligned with HTF"; `counter_trend` →
  `Shorts = liquidity` (red, badge `⚠ INTACT`); `hunt_in_progress` →
  `Hunting shorts` (amber, badge `⚡ ACTIVE`); `captured` →
  `Shorts captured` (green, badge `✓ CLEARED`, capture time in the
  sub-line). Sub-line shows `captured/total pools swept` plus
  `· OI unwinding` while the regime still burns the hunted side; the full
  engine `description` is the card's hover title. **Anchor chips** (as of
  2026-07-06, from `data.higher_timeframe`): the HTF Trend card label reads
  `HTF Trend · 4H` with sub `4H internal structure` (`top timeframe — own
  trend` when null), and every hunt sub-line ends in `· vs 4H` (the `none`
  phase reads `structure aligned with 4H`) — so an M15 card saying
  `Hunting longs · vs 1H` reads as the pair's fractal handoff (the bounce's
  buyers are the H1 correction's fuel), not a contradiction of the 4H story.

- **Hunt window chart shading** (frontend, as of 2026-07-06):
  `frontend/src/charting/HuntWindowPrimitive.ts` shades the liquidity-hunt
  window as a **full-pane-height vertical band** on the main pane (modeled on
  `POIBoxesPrimitive`, but a time span rather than a price box, and rendered
  at `zOrder 'bottom'` so it paints *behind* the candles and every overlay).
  `MainChart` fills it from `data.liquidity_hunt`: the band runs from
  `counter_structure_timestamp` (the counter-trend flip candle, dashed
  vertical edge) to `captured_at` when `phase === 'captured'`, or to the
  right edge via the far-future-sentinel clamp while the hunt is still
  running. Amber (`#ff9800`, ~5% fill) with a `⚡ hunting shorts|longs` label
  at the top while active; green (`#26a69a`) with `✓ shorts|longs captured`
  once concluded; nothing when `phase === 'none'`. Only the *current* hunt is
  drawn (the state is a live snapshot, not a history of past windows).
  Toggled by the `⚡ Hunt` toolbar button in `App.tsx` (`huntWindowVisible` →
  the `showHuntWindow` prop on `MainChart`), **off by default**. Independently
  of the toggle, the structure label of the **flip event itself** — the
  non-provisional BOS/CHoCH/`CHOCH_FAILED` whose timestamp equals
  `counter_structure_timestamp` while the hunt phase is not `none` — gets a
  `⚠` suffix (`CHoCH ▼ ⚠`): the entrants of that break are the resting
  liquidity being hunted. Only the *standing* flip is marked; historical
  events would need the HTF trend as of their own time, which a snapshot
  does not carry.

- **OI regime surfaces** (frontend): `KpiRow` renders an **"OI Regime"**
  card (grid is `md:grid-cols-5`; the `LoadingSkeleton` in `App.tsx` matches)
  from `data.oi_analysis.current_regime` — regime label + price-direction
  icon, directional colors for the buildup regimes and amber for the
  unwinding ones, sub-line `OI ±x% · Px ±y%`, and a badge: `✓ CONFLUENT` /
  `⚠ DIVERGENT` compares a buildup regime's conviction direction against the
  HTF trend, `⚠ UNWIND` flags covering/liquidation regimes; `—` when
  `oi_analysis` is null (spot-only symbol). `MainChart` appends an OI
  participation suffix to structure event labels via
  `OI_PARTICIPATION_SUFFIX` (`⊕` new money, `⊖` covering, `⚡` flush; FLAT
  adds nothing), keyed by `event_timestamp|event_type` from
  `oi_analysis.qualified_events`.
- **`frontend/src/theme.ts`** — color constants for POI zones, structure
  events, manipulation cycle boxes (`MANIPULATION_BOX_STYLES`), volume delta,
  RSI, consolidation range boxes (`CONSOLIDATION_BOX_STYLES`, neutral slate),
  the liquidity heatmap gradient, leverage-liquidation bands
  (`LIQUIDATION_LEVERAGE_COLORS`, warm gradient by tier), and other chart
  elements.

The KPI row, main chart (with volume delta and RSI sub-panes), and
manipulation cycles sidebar panel are implemented. The liquidity targets,
retail trap, and market structure sidebar panels are not yet implemented
in the React frontend.

## Project status

Core domain, data, indicators, liquidity detectors, scoring, psychology,
FastAPI API, and React frontend (main chart + sidebar) are all implemented.

**The full changelog of structure-detector design decisions and confirmed
behaviors lives in `liquidity_hunter/docs/structure_decisions.md`** (extracted
from this file to stay under its size limit). Read it before touching the
`InternalStructureDetector` / `SwingStructureDetector` pipeline — it documents
every production flag wired in `load_dashboard_data`, the measurements behind
each, the rejected alternatives, and the real-data regression fixtures. Current
state in brief:

- **Both structure detectors share one unified architecture**: trailing
  `active_high`/`active_low` references, the
  `candidate_choch_<side>` / `_baseline` / `validated_choch_<side>` two-step
  promotion gate, persistence-based CHoCH confirmation (`is_sustained_break`),
  and the LuxAlgo `bos_confluence` filter. No `volume_delta` in any
  confirmation. Defaults: major `swing_lookback=10`/`persistence_candles=10`,
  internal `swing_lookback=2`/`persistence_candles=5`. They diverge only in
  what an emitted BOS *reports* as its reference and in composition-level
  passes applied to the internal stream.
- **BOS**: state advances only on a *close* beyond the reference
  (`find_close_break_index`); a continuation must extend the BOS staircase
  (`last_bear_bos_low`/`last_bull_bos_high`), seeded at each CHoCH with the
  CHoCH level. Composition passes re-time each BOS to its first close beyond
  the formed level and drop wick-only continuations
  (`_reanchor_bos_close_break`, `_drop_pre_break_reference_bos`).
- **CHoCH**: persistence-confirmed against
  `validated_choch_<side> or choch_origin_<side> or active_<side>`; the
  validated reference is the leg origin of the most recent continuation BOS.
  `CHOCH_FAILED` reverts the trend when the origin is reclaimed before a
  confirming BOS.
- **Production flags** (all wired in `load_dashboard_data`, gated off by
  default in the detector; see the doc for each): staleness/chain re-anchor,
  impulse + wick-rejected BOS staging, leg-origin CHoCH reference family,
  volatility-normalized release gap, new-cycle weak-ref barrier,
  shallow-pullback promotion, close-confirmed structural floor, provisional
  live-edge BOS/CHoCH marks, fast-fizzle marker, failed-CHoCH whipsaw fixes,
  displacement release, weak-ref failure at the broken level, staircase
  rollback on a discarded phantom advance, displacement-success
  CHoCH-origin retirement (an impulsive reversal that emitted no BOS is not
  marked a false `CHOCH_FAILED` on its pullback), and the scoped
  consolidation cycle reset (`_CONSOLIDATION_RANGE_RESET_CYCLE`, a second
  `detect(range_resets=…)` pass re-seeding references onto the ACTIVE range's
  boundaries — active-only, measured 0/20 change). A `CHOCH_FAILED`'s reclaim
  scan is also bounded to *after* the CHoCH formed (`*_choch_arm_index`), so a
  failure can never be timestamped before the CHoCH it invalidates.
- **Consolidation (lateral range) observation + breakout staging** (phases
  1–2, 2026-07-14): a composition-level post-pass over the surviving event
  stream turns the detector's correct silence inside a range into explicit
  `ConsolidationRange`s (chart box + ladder chip + line truncation; trend
  untouched), and each sustained boundary breakout stages one additive event
  at the broken boundary — a real BOS with the segment trend, a
  `provisional=True` CHoCH against it (replay-skipped). Measured +7/−0 on
  the live matrix, `final_trend` unchanged. **Phase 3, scoped cycle reset**
  (flag `_CONSOLIDATION_RANGE_RESET_CYCLE`, default OFF): re-seeds the state
  machine's references onto the **ACTIVE** range's boundaries (a second
  `detect(range_resets=…)` pass fed the scanner's `RangeReset` directives),
  so while price sits in the box the references track the box instead of
  pre-range levels. Scoped to the one live range only — the blanket re-seed
  of all history was measured and rejected (20/20 churn, rewrote settled
  structure, flipped ETH 4H's July conclusion); active-only measures 0/20
  structural changes, 0 trend flips (just BTC 4H's spurious mid-box `BOS?`
  dropped). Conservative: suppresses mid-box provisional clutter + anchors
  the forming breakout mark at the boundary, but does not itself flip the
  trend at range exit (a range un-scopes on resolution). Full cycle-reset
  (re-seed persisting through resolution + `CHOCH_FAILED` preserved) is
  deferred. See `docs/structure_decisions.md`.

**Not yet implemented**:
- Wiring `LIQUIDITY_SWEEP` events to `LiquidityZone.is_mitigated` /
  `invalidated_at` for the swept zone.
- Composite multi-timeframe confluence score (phase 2 of the score plan):
  per-TF signed sub-scores from OB/Sweep/EQL/VOL/RSI-div/Hunt with exposed
  components; requires porting RSI(14) + divergence detection (today
  frontend-only, `MainChart.tsx`) into `indicators/`.
- React frontend behavior divergence sidebar panel and chart overlay.
- React frontend liquidity targets, retail trap, and market structure
  sidebar panels.
