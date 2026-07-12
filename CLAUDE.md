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
  dimmed `BOS?`/`CHoCH?`), and a hunt summary (`hunt_phase`, `hunted_side`,
  `hunt_targets_captured`/`_total`). `MarketOverview` is `symbol` + `entries`
  ordered fine → coarse. Descriptive state per timeframe, not signals.

Shared enums (`TimeFrame`, `MarketDirection`, `LiquiditySide`,
`LiquidityZoneType`, `StructureEvent`, `BiasSource`, `RetailPositioning`,
`POIZoneStatus`, `POIZoneKind`, `ManipulationPhase`, `ManipulationCycleStatus`,
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
  (`OIAnalysis | None`), `liquidity_hunt` (`LiquidityHuntState | None`), and
  `higher_timeframe` (`TimeFrame | None` — the `_HIGHER_TIMEFRAME_MAP` anchor
  pair `higher_timeframe_direction` was measured on, `None` for the top
  timeframe; lets the frontend label readings "vs 4H" instead of a generic
  "HTF") for one symbol/timeframe.
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
  `_INTERNAL_STRUCTURE_PARAMS` (M5=`(6, 4)`, M15=`(6, 2)`, M30=`(5, 2)`,
  H1=`(4, 2)`, H4=`(5, 8)`, D1=`(5, 8)`, W1=`(5, 12)`; `_DEFAULT_INTERNAL_PARAMS
  = (5, 12)`) — so the constructor defaults (`2`/`5`) apply only to a
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
  CHoCH-seeded level, formed before the flip); BOS without a resolved
  `reference_timestamp` are kept. Same-timestamp BOS are judged
  earlier-formed-reference first (the earlier structural break).
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
  `liquidity_hunt`, and `higher_timeframe` fields are included.

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
  provisional marks (`BOS? ▼`), and a hunt-phase chip (`⚠` counter-trend /
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
  Also reused for manipulation cycle accumulation boxes (second instance).

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
  `LiquidityHuntTarget`, `LiquidityHuntPhase`, `LiquidityHuntTargetKind`;
  `DashboardData.higher_timeframe` (`TimeFrame | null`).

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
  RSI, the liquidity heatmap gradient, leverage-liquidation bands
  (`LIQUIDATION_LEVERAGE_COLORS`, warm gradient by tier), and other chart
  elements.

The KPI row, main chart (with volume delta and RSI sub-panes), and
manipulation cycles sidebar panel are implemented. The liquidity targets,
retail trap, and market structure sidebar panels are not yet implemented
in the React frontend.

## Project status

Core domain, data, indicators, liquidity detectors, scoring, psychology,
FastAPI API, and React frontend (main chart + sidebar) are all implemented. Below are the key design decisions and confirmed
behaviors as of 2026-06-20:

**Both structure detectors use the same unified architecture** (as of today):
trailing `active_high`/`active_low` references, `candidate_choch_<side>` /
`candidate_choch_<side>_baseline` / `validated_choch_<side>` two-step
promotion gate, persistence-based CHoCH confirmation (`is_sustained_break`),
and the LuxAlgo-style `bos_confluence` filter for BOS emission. Neither
detector uses `volume_delta` or `min_volume_delta_ratio` for any confirmation.
`SwingStructureDetector` defaults: `swing_lookback=10`, `persistence_candles=10`.
`InternalStructureDetector` defaults: `swing_lookback=2`, `persistence_candles=5`.
(The state machines remain unified; they diverge only in what an emitted BOS
*reports* as its reference — see "Internal BOS reference + close-break
re-anchor" below — and in the composition-level re-anchor applied to the
internal detector alone.)

**BOS confirmation** (both detectors): the state machine advances **only when
a candle in the leg *closes* beyond the active reference**
(`find_close_break_index`) — a wick-only overshoot does not advance state and
*freezes* the reference (it is not trailed to the new pivot) until a close
confirms. A continuation BOS must also satisfy the **BOS staircase**: it must
extend the leg beyond the previous BOS level (`last_bear_bos_low` /
`last_bull_bos_high`), so bearish BOS lows keep making lower lows (bullish BOS
highs higher highs) while the trend is unchanged; a break of a higher trailing
low (lower trailing high) during a retrace, which does not beat the previous
BOS extreme, is not a BOS. The staircase is **seeded at each CHoCH with the
CHoCH level itself** (the broken reference), so the first BOS of the new leg
must break *beyond the CHoCH level* — a BOS cannot form on the wrong side of
the CHoCH (e.g. a bullish BOS below a bullish CHoCH after price fell back
through it). Only the first BOS out of the `NEUTRAL` bootstrap is
unconstrained. The `BREAK_OF_STRUCTURE` event is *emitted* once confirmed and
optionally passes the `bos_confluence` shadow-balance filter. SWEEP and CHoCH
detection are unaffected by the close/staircase requirements (sweeps are wick
events).

**BOS reference + close-break re-anchor** (**both detectors**, as of
2026-06-26): a BOS reports `reference_price_level` = the **formed low/high it
broke** (the staircase floor captured at the state-advance — `_PendingBOS.floor`
in the internal detector, `floor_at_advance` in the major) rather than the
trailing pivot, so it plots at the prior swing extreme and the BOS levels form a
clean staircase. A composition-level pass, `_reanchor_bos_close_break` in
`load_dashboard_data`, then re-times each BOS to the first candle that *closes*
beyond that formed level (within the window the BOS stays active) and **drops**
any BOS whose leg only *wicked* past it — a conservative close-confirmation that
can leave long event-free stretches on the macro (intended). It also sets
`reference_timestamp` to the candle that *formed* the level, so the frontend
starts the BOS line at the level's origin and runs it to the break. The pass
runs on both `all_internal_events` and `all_major_events`. Both detectors' state
machines, trailing references, and CHoCH promotion are untouched — only the
reported BOS reference/timestamp change.

**Pre-break-reference BOS drop** (**both detectors**, composition level, as of
2026-07-02): `_drop_pre_break_reference_bos` runs right after
`_reanchor_bos_close_break` on both streams. A wick that pokes beyond the
still-unbroken prior BOS level (a failed break attempt) still ratchets the
detector's staircase extreme (`prev_*_bos_extreme`), so the *next* continuation
BOS would report that pre-break wick as the formed level it broke (the M15
motivating case: a 09:45 candle wicked 61447 above the unbroken 61322 level and
closed 61159; after the 11:45 close finally broke 61322, a 12:45 BOS printed
against 61447). The rule: a continuation BOS whose `reference_timestamp`
strictly predates the confirming close (`timestamp`) of the previous
same-direction BOS in the same leg is **dropped** — a reference may only come
from price action after the prior break confirms. A CHoCH resets the constraint
for its direction (the first BOS of a leg legitimately references the
CHoCH-seeded pre-flip level); unresolved `reference_timestamp` → kept.
Same-timestamp BOS are judged earlier-formed-reference first, so a staged mark
re-timed onto the same confirming candle as the real BOS is judged against it
deterministically. The detectors' state machines and CHoCH promotion are
untouched (composition-level, per the additive-over-state-machine lesson).
Measured (`limit=1200`, BTCUSDT): removes exactly the M15 12:45/61447 BOS
(internal + major), plus the same wick-attempt pathology on M5 (a staged
backwards-staircase mark), M30 (64362 wick vs 64250 level), H1 internal (61870
wick vs 62232), H1 major, and one D1 major (a 2023 31500 wick); every dropped
event was verified as `high > prior level && close < prior level` (or the
bearish mirror) formed before the prior BOS's close. Zero events added, H4
untouched.

**Staleness re-anchor** (**both detectors**, as of 2026-06-29):
`stale_reanchor_candles` (constructor default `None` = off; wired in
`load_dashboard_data` for **both** detectors per timeframe via the shared
`_STALE_REANCHOR_CANDLES`, e.g. H4=60, D1=40) retires a stale cycle. On a coarse
timeframe a trend can lock for a long time: a bearish leg pins the bullish
reversal reference at the leg origin, so the eventual CHoCH only fires once price
climbs all the way back there (the "long-stuck-BOS" pathology). When the trend
runs `stale_reanchor_candles` candles past its last BOS / trend flip (tracked by
`last_advance_index`, set in `emit` for BOS/CHoCH/`CHOCH_FAILED`) without a fresh
one, the reversal reference is pulled to the most recent local swing extreme via
the existing `reanchor_opposite` helper — so a CHoCH can confirm **locally** and
a new cycle begins, **without flipping `trend`** (the CHoCH itself still has to
confirm). It only ever tightens (and only to a level on the correct side of
price), so it tracks the recent extreme as the range unfolds; a confirming
CHoCH/BOS resets the counter. Independent of `reanchor_mode`. The two detectors
differ only in how they pick the local extreme: the major uses `last_high_pivot`
/ `last_low_pivot`; the internal (no such held pivot — references trail) uses the
extreme high/low over a trailing window of `stale_reanchor_candles` candles.
**The internal detector is what the chart renders for all timeframes**
(`MainChart.tsx`: `scopeEvents = data.internal_structure_events`), so the
internal staleness re-anchor is what fixes the visible lock; the major one feeds
`market_structure_events` (in the API, not currently drawn).

**Impulse BOS staging** (`InternalStructureDetector` only, as of 2026-06-29):
`impulse_bos_displacement_pct` (constructor default `None` = off; wired in
`load_dashboard_data` via `_IMPULSE_BOS_DISPLACEMENT_PCT = 0.015`). A clean
impulsive leg (consecutive lower lows / higher highs with **no intervening
opposite pivot**) advances the state machine at each step but, with no pullback
pivot to confirm them, emits at most one *deferred* pending BOS — so a sharp
multi-leg move (e.g. a −20% drop over ~60 H4 candles) prints one long event-free
stretch instead of a staircase. When set, each state-advance whose displacement
beyond the prior BOS level (`prev_bear_bos_extreme`/`prev_bull_bos_extreme`, the
`floor_at_advance`) clears the threshold is recorded as a staged BOS in a
**separate list**; at the end of `detect` the staged BOS are **deduped against
the real emitted BOS** (same direction, `price_level` within
`_STAGED_BOS_DEDUP_PCT = 0.2%` — both report the advance pivot's extreme) and
merged. So it only ever *adds* marks in the impulsive gaps the state machine left
empty; the state machine, references, and CHoCH promotion are **untouched** (with
the flag off the output is byte-for-byte identical). Staged BOS leave
`reference_timestamp` unset so `_reanchor_bos_close_break` anchors their line
origin like any other BOS. The reported `reference_price_level` is the prior
BOS's extreme, so staged steps continue the same descending/ascending staircase.
This was chosen over a composition-level post-pass: re-deriving "current trend +
live staircase floor" outside the detector leaks across segments (a stale floor
from a prior leg bleeds in), whereas in-detector `trend`/`prev_*_bos_extreme` are
already correct. Not mirrored into `SwingStructureDetector` (coarse lookback has
far fewer impulsive gaps and is not drawn).

**Re-anchor min-price-gap guard** (`InternalStructureDetector` only, as of
2026-06-30): `reanchor_min_price_gap_pct` (constructor default `None` = off;
wired in `load_dashboard_data` via `_REANCHOR_MIN_PRICE_GAP_PCT = 0.003`) guards
the *output* of `reanchor_opposite` (every trigger — chain, stale, displacement):
it refuses to set the reversal reference to a local extreme closer than this
fraction to current price. A `"chain"` or staleness re-anchor in a tight lateral
range can land `validated_choch_<side>` on a local high/low sitting almost on top
of price; that reference is hair-trigger, so a trivial bounce confirms a
mid-range CHoCH that immediately fails (the "CHoCH in chop" clutter — e.g. an M5
bullish CHoCH the chain re-anchor wrote ~0.1% above price, which then failed).
Requiring a minimum gap makes breaking the re-anchored level a genuine reversal.
Measured purely additive-or-cleaner: on M5 it removes exactly the spurious CHoCH
(its `CHOCH_FAILED` count drops) while staying **neutral on the coarser
timeframes** (whose re-anchors already land far from price — D1's `CHOCH_FAILED`
are legitimate failed macro reversals, not chop, and are unaffected). A
leg-displacement gate (`reanchor_chain_min_displacement_pct`) was prototyped first
but **rejected by measurement** (inert on M30/D1, destabilizing on M5).

**Chain re-anchor establish-only** (`InternalStructureDetector` only, as of
2026-06-30): `reanchor_chain_establish_only` (constructor default `False`; wired
**`True`** in `load_dashboard_data`) restricts the `"chain"` trigger to
*establishing* a reversal reference that has gone blind (the opposite-side
`validated_choch_<side>` is `None`, as in a clean impulse that nulled it) — it
never *tightens* a reference that already exists. The chain trigger exists for the
blind-impulse case; when a fresh `validated_choch_<side>` was just promoted from a
real pullback, tightening it down to a shallower in-leg high degrades the CHoCH
reference to a weak pullback, so a small reclaim fires a premature CHoCH (e.g. an
M5 bullish CHoCH the chain wrote at a shallow 58,861 local high after a good 59,316
pullback had already been promoted — distinct from the gap-guard case, since that
level was a legitimate distance from price). With this set, a present reference is
left for the staleness re-anchor to tighten only once genuinely stale; the
blind-impulse establish case the chain was added for (e.g. the H4 June drop) is
unaffected. Measured: removes the degraded M5 CHoCH, net-neutral on M15/M30/H4,
trims a couple chain-tightening CHoCH on H1/D1.

**BOS pullback wick filter** (`InternalStructureDetector` only, as of
2026-06-30): `bos_pullback_max_wick_pct` (constructor default `None` = off; wired
**`0.4`** in `load_dashboard_data` via `_BOS_PULLBACK_MAX_WICK_PCT`) filters the
pullback pivot that *confirms* a BOS. A BOS confirms when a pivot forms in the
opposite direction (a high pivot for a bearish BOS, a low for a bullish BOS); with
a small swing lookback that pivot can be a single-candle **wick** — the candle
spikes to the extreme intrabar but its body closes far away (e.g. an M5 bearish
BOS confirmed by a candle that wicked up to 58,732 then closed near its low at
58,350 and made a new low) — so the BOS prints off a "pullback" that never
retraced. When set, the confirming pivot candle's pivot-side wick
(`_pullback_quality_ok`: upper wick for a high pivot, lower for a low pivot) must
be at most this fraction of its range; a wick-only spike does not confirm and the
pending BOS is **kept alive** so a later, real-bodied pullback confirms it instead
(or it never confirms if none forms). Because the confirming pullback also seeds
`candidate_choch_<side>`, the filter propagates correctly into CHoCH detection
(the reversal reference anchors to a genuine pullback too) rather than being a
cosmetic mark drop. Adjusting the swing lookback was measured and rejected as a
fix (it coarsens all structure globally and doesn't target the wick). With the
flag off the output is byte-for-byte identical. Not mirrored into
`SwingStructureDetector` (the major detector is not drawn).

**Wick-rejected BOS staging** (`InternalStructureDetector` only, as of
2026-07-01): `stage_wick_rejected_bos` (constructor default `False`; wired
**`True`** in `load_dashboard_data`) is the *additive* complement to the wick
filter above. When a continuation advance's only pullback is a wick (rejected by
`bos_pullback_max_wick_pct`) and no *real* pullback ever confirms it before the
trend flips, the state machine emits **no BOS** even though the leg genuinely
closed beyond the staircase floor — a visibly-missing mark. This flag stages an
**additive** `BREAK_OF_STRUCTURE` for that break (once per pending BOS, at the
break's close and referencing the floor it broke), merged and deduped against the
real BOS at the end exactly like the impulse staging (`impulse_bos_displacement_pct`).
Crucially it **never touches the state machine or CHoCH promotion** — it does not
seed `candidate_choch_<side>` — so, unlike relaxing the wick filter itself (which
cascades: more confirmed BOS shift the trend state and can turn a correct reversal
CHoCH into a premature one), it cannot change the CHoCH sequence. Purely a mark.
A wick-rejected pending BOS that *later* gets a real bodied pullback still emits
its normal BOS (which dedups the staged mark away), so staging only ever fills the
genuinely-missing gaps. With the flag off the output is byte-for-byte identical.
Not mirrored into `SwingStructureDetector` (not drawn). (Relaxing the wick filter
to a neighborhood-corroboration test was prototyped first and **rejected by
measurement**: it recovered the missing marks but cascaded downstream and
destroyed a known-correct reversal CHoCH — the state-machine-vs-additive lesson.)

**Leg-origin CHoCH reference** (`InternalStructureDetector` only, as of
2026-07-02): `bos_leg_origin_choch_ref` (constructor default `False`; wired
**`True`** in `load_dashboard_data`, with `bos_leg_origin_release_gap_pct =
_BOS_LEG_ORIGIN_RELEASE_GAP_PCT = 0.04`). Every *emitted* BOS promotes its **leg
origin** — the extreme the breaking leg launched from (`_PendingBOS.pullback_ref`:
the fundo a bullish leg rose from / the topo a bearish leg dropped from) — directly
to the opposite `validated_choch_<side>` at emission, marked *structural*
(`validated_choch_<side>_structural`), replacing the current reference
unconditionally (even to a looser level — structure wins). The close-break plus the
confirming pullback is itself the continuation evidence, so the CHoCH reference no
longer waits for the continuation gate (which still runs on top and can tighten to
the newer post-BOS pullback). Two companion rules make it hold:
(1) **re-anchors (stale/chain) refuse to slide a structural reference while it is
reachable** — within the release gap of current price (originally
`bos_leg_origin_release_gap_pct` = 4%; since 2026-07-03 volatility-normalized,
see "Volatility-normalized release gap" below); beyond
that gap the leg has run away and the staleness re-anchor regains authority
(otherwise an impulsive leg that emits no BOS for months pins the reference and
coarse timeframes lose whole reversal sequences — the H4 Feb→Mar regime collapse,
measured); (2) under the flag a re-anchor writes its synthetic level **only into
`validated_choch_<side>`**, never into `active_<side>`/`candidate_choch_<side>`,
whose genuine swing pivots feed the leg-origin snapshot — otherwise the re-anchor
level gets laundered into a "structural" leg origin at the next emission (measured:
an M30 leg-origin of 63650, a stale-window artifact, instead of the genuine 65469
fundo). Motivating cases (measured, `limit=1200`): the M30 bearish CHoCH fires
17/06 08:00 against the 15/06 leg origin 65469 (instead of 18/06 against a
stale-re-anchor 64525), and the H4 May bearish CHoCH fires 17/05 against the
78128 mínima of the 04/05 BOS (instead of a sliding stale-window low 78713).
Neutral on H1/D1 counts; M15 loses one whipsaw pair; M5 gains 2 `CHOCH_FAILED`
(accepted, 4-day window). Threshold measured: 5% degrades M15, 6% loses the H4
April CHoCH. Two stricter variants were **rejected by measurement**: hybrid
promote-only-over-re-anchored refs (first structural ref pins the whole leg) and
an unconditional re-anchor bar (same pinning). With the flag off the output is
byte-for-byte identical. Not mirrored into `SwingStructureDetector` (not drawn).

**Leg-origin promotion on origin-reclaim kill** (`InternalStructureDetector`,
as of 2026-07-03): a *pending* BOS (close-break advanced, awaiting its
confirming pullback) that is discarded because the next opposite pivot is
already **beyond its leg origin** (`price > pullback_ref` bearish / `<`
bullish) now promotes that origin to `validated_choch_<side>` (structural)
before being dropped, so the CHoCH check on that same pivot evaluates against
it. Rationale: the state machine already treated the advance as real
(staircase/leg extremes ratcheted), and the reclaim of the leg origin *is* the
conservative reversal — but emission-only promotion missed it whenever the
pullback was wick-rejected (`bos_pullback_max_wick_pct`) and price then
reclaimed the origin directly. Motivating case (measured, ETHUSDT H1,
`limit=1200`): the 06/06 04:00 bearish BOS's leg origin 1618.85 was never
promoted (its 1592.80 pullback was wick-rejected; the staged mark drew the BOS
but stages never promote), the reference stayed at the stale 1793.66, and the
whole 06/07 rally to 1721.57 printed as sweeps with no bullish CHoCH. With the
fix the CHoCH fires 06-07 08:00 against 1618.85 and the bullish leg develops
(BOS 1721.57). Measured elsewhere: same-pattern improvements only — ETH D1's
Dec→Feb decline gets its bearish CHoCH on 12-19 instead of 02-02 (which
correctly becomes a continuation BOS), BTC 4h finds an earlier 01-02 bullish
CHoCH, ETH 4h gains one honest CHoCH+`CHOCH_FAILED` whipsaw pair, M30/H1 refs
tighten slightly. Gated behind `bos_leg_origin_choch_ref` (off = byte-for-byte
identical).

**Pending-BOS leg origin in the CHoCH reference chain**
(`InternalStructureDetector`, as of 2026-07-03, same-day follow-up to the
above): while a pending BOS is **alive** (close-break advanced, every pullback
attempt wick-rejected so it neither emitted nor got killed), its
`pullback_ref` now participates in the CHoCH reference chain: `validated or
pending.pullback_ref or choch_origin or active_<side>` (`via_validated`
treats a pending-origin trigger like a validated one). Motivating case
(ETHUSDT H1 2026-06-25, verified by state instrumentation): the 06-23 bearish
CHoCH was fallback-triggered (armed no blind-spot origin) and reset the
validated refs; the 06-24 BOS (staged mark at 1633) never emitted — both its
pullbacks (1629.15, 1660.56) were wick-rejected — so nothing ever promoted;
the bullish-CHoCH check fell back to the trailing `active_high` = 1629.15 and
the 1660.56 pivot fired a premature mid-range CHoCH, while the pending BOS
carried the genuine 1692 leg origin the whole time. With the fix: 06-25
becomes a sweep, the 06-25 13:00 close-break of 1551 correctly emits as a
continuation BOS (the wrong CHoCH had flipped the trend and eaten it), the
06-26 `CHOCH_FAILED` disappears with its premature CHoCH, and the bullish
CHoCH fires 06-27 10:00 against 1583 (the leg origin of the newest activated
BOS — the reference correctly ratchets when a newer BOS activates). Measured:
**zero diffs** on ETH 30m/4h/1d and BTC 1h/4h — surgical. `validated` still
outranks the pending origin so the staleness re-anchor retains authority over
a long-lived pending. Gated behind `bos_leg_origin_choch_ref`.

**Cold-start fallback suppressed in the unconfirmed-CHoCH window**
(`InternalStructureDetector`, as of 2026-07-03, third same-day follow-up):
the `active_<side>` cold-start fallback in the CHoCH reference chain is
**suppressed while an unconfirmed CHoCH's origin is armed**
(`bear_choch_origin`/`bull_choch_origin`) — inside that provisional window
the designed reversal exit is `CHOCH_FAILED` at the origin, and the fallback
was undercutting it at a far weaker level. Motivating case (SOLUSDT H1
2026-06-23, verified by instrumentation): the 06-20 `CHOCH_FAILED` reset all
refs and armed nothing (one-shot); the 06-22 bearish CHoCH then fired via the
`active_low` fallback (so `via_validated=False` armed no blind-spot origin);
no bearish BOS had activated yet (the 68.07 fundo was the flip pivot itself),
so the bullish side was fully blind — validated/pending/origin all `None` —
and the check fell to the trailing 69.63 LH, firing a premature bullish CHoCH
(failed next day) while `bear_choch_origin` sat at 74.97. With the
suppression: the 70.36 rally is a sweep, the trend stays bearish, the drop
prints the missing bearish continuation BOS (64.66 breaking the 68.07 fundo,
then 64.00), and the genuine bullish CHoCH fires 06-26 against 69.64 (the
newest BOS's leg origin), confirmed by the 70.97→73.91 bullish BOS staircase.
Measured: ETH 1h / BTC 1h / BTC 4h zero diffs; ETH 30m drops 3 whipsaw CHoCH
in the 06-30–07-02 chop (5 flips → 2 CHoCH + 1 honest `CHOCH_FAILED`, plus
the missing 07-03 bullish BOS); ETH 4h/1d reshape only Dec-2024 regions where
the same pattern held (D1: the 3744 reversal attempt now closes with
`CHOCH_FAILED` at its 3302 origin and the Feb-2025 crash BOS references it —
instead of a double bearish CHoCH). Real-data regression fixture:
`tests/liquidity/detectors/data/solusdt_1h_2026_06_13_27.json` (5-column
rows: ts/open/high/low/close — open matters for the wick filter). Gated
behind `bos_leg_origin_choch_ref`.

**Volatility-normalized release gap** (`InternalStructureDetector`, as of
2026-07-03): `bos_leg_origin_release_gap_atr` (constructor default `None` = off;
wired **`3.0`** in `load_dashboard_data` via `_BOS_LEG_ORIGIN_RELEASE_GAP_ATR`,
taking precedence over the fixed `bos_leg_origin_release_gap_pct = 0.04`, which
stays as fallback for series too short to measure a range). The structural-ref
release gap becomes `N × mean true-range%` of the detected series instead of a
fixed fraction of price, so "reachable" means the same number of typical candles
on every asset/timeframe. Motivation (measured): the fixed 4% was worth **8.5
ATR on BTC 30m** (the guard held almost always, pinning the reversal reference
through the 06-23..26 June drop so every bounce fired a bullish CHoCH that then
failed — three whipsaw CHoCH/`CHOCH_FAILED` pairs across a 63k→58k decline)
but **0.6 ATR on SOL D1** (one average candle released it — no guard at all).
Measured (BTC/ETH/SOL × 30m/1h/4h/1d, `limit=1200`): **N ∈ [2, 3] is a stable
plateau** (byte-identical outputs); 8/12 combos unchanged vs the fixed 4% (all
SOL, all H4, BTC D1 — the leg-origin motivating cases M30 17/06 and H4 May
78128 intact); BTC 30m resolves the June drop into one bearish CHoCH at the
63833 leg origin + a 59060→58030 bearish BOS staircase and drops the 06-27..30
chop flips (3 trend flips in ~30h); BTC 1h moves the 06-22 CHoCH 1h earlier
with a tighter ref; ETH 30m gains one whipsaw pair 06-20/21 (accepted); ETH 1d
reshapes an Aug–Sep-2023 region (tighter CHoCH ref 1684 vs 1808, the missing
09-11 continuation BOS appears). N=4 reverts to fixed-pct behavior on the fine
timeframes. Real-data regression fixture:
`tests/liquidity/detectors/data/btcusdt_30m_2026_06_05_07_02.json` (whipsaw
resolution + an ATR≡equivalent-pct equivalence/precedence test). Off = the
fixed-pct behavior, byte-for-byte.

**New-cycle CHoCH barrier** (`InternalStructureDetector`, as of 2026-07-03):
`choch_weak_ref_persistence_candles` (constructor default `None` = off; wired
**`4`** in `load_dashboard_data` for M5/M15/M30/H1 via
`_CHOCH_WEAK_REF_PERSISTENCE` — coarse timeframes with base persistence 8+ are
left alone, and M1 is deliberately absent since its default base of 12 would be
*weakened* by 4). A CHoCH about to fire against a **weak** reference — a
synthetic re-anchor level (`validated_choch_<side>` present but not
`_structural`; only `reanchor_opposite` writes those) or the trailing
`active_<side>` cold-start fallback — must sustain for this many candles
instead of the base `persistence_candles`. **Structural** references (leg
origin, continuation-promoted candidate, live pending-BOS origin, blind-spot
`choch_origin_<side>`) keep the base persistence: the conservative CHoCH is
never delayed. The `CHOCH_FAILED` check also keeps the base persistence (the
escape valve that undoes a wrong cycle must not be delayed). Rationale: with
intraday base persistence at 2, a brief poke through a weak local level was
enough to flip the trend and start a dirty cycle ("às vezes é só um sweep e já
cria CHoCH de novo ciclo"); a genuine reversal holds past the barrier anyway,
so the cost is bounded at a few candles of confirmation delay. Measured
(BTC/ETH/SOL × 5m/15m/30m/1h, barrier 3/4/5, `limit=1200`): every removal is a
whipsaw CHoCH/`CHOCH_FAILED` pair — BTC 5m double-flip chop (two CHoCH 15 min
apart), BTC 15m two pairs (restoring the 06-30 bearish continuation
staircase), BTC 30m 06-12 pair (needs 4+), SOL 30m one pair; ETH 5m / SOL
5m/15m/1h / BTC 1h / ETH 1h untouched. Costs: one genuine weak-ref CHoCH
delayed 9h (ETH 30m, same level), one delayed 1 candle (ETH 15m). **4
chosen**: strictly better than 3 (adds the BTC 30m pair at no observed cost),
while 5 starts delaying a genuine BTC 30m reversal CHoCH by 6h. Tests: weak-ref
delay on the BTC 30m fixture (06-07 23:00 → 06-08 01:30, same ref), structural
exemption on the SOL H1 fixture (barrier 10 ≡ off, the 06-26 CHoCH vs 69.64
intact). Off = byte-for-byte identical. Not mirrored into
`SwingStructureDetector` (not drawn).

**Shallow-pullback leg-origin promotion** (`InternalStructureDetector`, as of
2026-07-03): `bos_leg_origin_min_pullback_atr` (constructor default `None` =
off; requires `bos_leg_origin_choch_ref`; wired **`1.5`** in
`load_dashboard_data` for **M15/M30/H1** via `_BOS_LEG_ORIGIN_MIN_PULLBACK_ATR`).
The leg origin a BOS promotes to the opposite CHoCH reference is normally the
trailing pivot at the state-advance (`active_high` for a bearish BOS /
`active_low` for a bullish one) — the *immediate* pullback high/low. When that
immediate pullback is **shallow** — its height (`active_high − active_low`) is
less than N × the series' mean true-range% of price — it is a minor secondary
high/low well inside the correction, so the CHoCH line lands at a small pivot
rather than the correction's visible top/bottom. In that case the origin is
promoted instead to the correction's **extreme** pivot (`pending_high`/
`pending_low`, already the most extreme high/low accumulated for the leg), but
only when that extreme is genuinely beyond the immediate pullback. The reference
then sits at the visible leg top/bottom; and because it is higher/lower, a
premature poke through the shallow level is reclassified as a `LIQUIDITY_SWEEP`
and the reversal CHoCH fires once price reclaims the true extreme. Motivating
case (measured, AAVEUSDT H1 `limit=1200`): the bullish CHoCH ref goes 86.59 →
**87.82** (the correction top, the 07-01 02:00 swing high), firing 07-03 on the
reclaim instead of 07-02 on the 88.49 poke that fell straight back to 84.28.
Only the promoted origin changes — the state machine, trailing references, and
continuation gate are untouched. Measured (BTC/ETH/SOL/AAVE × 5m..1d): **N=1.5**
is the minimum that catches the AAVE target (immediate depth 1.42 × ATR); every
intraday change is a whipsaw CHoCH/`CHOCH_FAILED` pair reclassified to a sweep
(AAVE 30m/1h, BTC 30m, SOL 1h), M15 near-neutral. **M5 is excluded** (noisy,
net-adds marks) and **4h/1d excluded** (they reshape already-tuned coarse
regions, e.g. BTC 4h May 78128 → 78713 — needs visual review), mirroring the
weak-ref barrier's intraday scope. Real-data regression fixture:
`tests/liquidity/detectors/data/aaveusdt_1h_2026_06_20_07_04.json` (337-candle
self-contained window; off → CHoCH ref 86.59 @ 07-02, on → 87.82 @ 07-03). Off
= byte-for-byte identical. Not mirrored into `SwingStructureDetector` (not
drawn).

**Close-confirmed structural leg origin** (`InternalStructureDetector`, as of
2026-07-04): `bos_leg_origin_require_close_break` (constructor default `False`;
requires `bos_leg_origin_choch_ref`; wired **`True`** in `load_dashboard_data`).
A BOS's state machine advances on a close beyond the trailing `active_<side>`,
but the leg origin it promotes to the opposite CHoCH reference is marked
`validated_choch_<side>_structural = True` **only if a candle actually *closed*
beyond the staircase floor** it reported (`_PendingBOS.floor`, checked with the
same `find_close_break_index`). When the continuation merely *wicked* past the
prior BOS level — the exact break `_reanchor_bos_close_break` drops from the
visible marks anyway — the origin is still promoted to the CHoCH reference but
as a **weak** reference (`_structural = False`), so the new-cycle barrier
(`choch_weak_ref_persistence_candles`) governs the resulting CHoCH and
re-anchors may still slide it, instead of it firing at base persistence off an
unconfirmed break. This closes the gap between the wick-based staircase gate
(which accepts a pivot whose wick beats the prior BOS high) and the close-based
composition drop (which hides that BOS): the promotion now agrees with the
mark. Motivating case (measured, AAVEUSDT H1 `limit=1200`): a bullish leg from
the 72.61 fundo (06-16 14:00) makes its only new high over the prior 77.70 BOS
top as a single-candle wick to 77.94 (06-17 02:00, close 76.94, no close above
77.70). Off, 72.61 is structural → a 06-18 poke fires a premature bearish CHoCH
that fails 06-20 (whipsaw); on, 72.61 is weak → the barrier defers the genuine
bearish CHoCH to 06-23 at the same level. Measured (BTC/ETH/SOL/AAVE ×
5m/15m/30m/1h/4h/1d): **23 of 24 combinations byte-identical**, only AAVE H1
changes (−3/+1: the whipsaw CHoCH/`CHOCH_FAILED` pair plus a duplicate collapse
into one clean 06-23 CHoCH). Real-data regression fixture:
`tests/liquidity/detectors/data/aaveusdt_1h_2026_06_05_24.json` (457-candle
self-contained window). Off = byte-for-byte identical. Not mirrored into
`SwingStructureDetector` (not drawn); gates the *emitted*-BOS leg-origin
promotion and (since the same day, see below) the candidate-continuation
promotion; the reclaim-kill structural path is untouched.

*Candidate-continuation extension (2026-07-04, same flag)*: the
continuation-gated candidate promotion (`candidate_choch_<side>` →
`validated_choch_<side>` on a new leg extreme) now also marks the promoted
reference weak when the advance's staircase-floor break was wick-only
(`floor_did_close` false — the same physical test). The emission gate alone was
incomplete: on the production window the wick-leg BOS never *emits* (so the
emission gate never runs), yet the wick advance itself — a new leg extreme by
wick only — promoted the candidate as structural, and a CHoCH fired against it
at base persistence with zero closes beyond the floor (user-reported: AAVE H1
bearish CHoCH 06-23 against a leg whose only break of the 77.70 top was the
06-17 wick; in the full window the premature CHoCH pair was 06-18/72.25 →
failed 06-20, then 06-23/74.45 → failed 06-24). A blanket "skip promotion
without a floor close" variant was **rejected by measurement**: it does not
remove those CHoCHs (the reference arrives via the candidate path) and it
destroys genuine wick-top reversals — AAVE H1 06-28 (99.29 wick top over the
98.18 floor, then −15%: base catches the bearish CHoCH + staircase, the skip
variant loses all of it) and BTC 1d's early Oct-2023 reversal — because a
wick-only top is often exactly the SMC liquidity grab a reversal should be
measured from. Weak-promote keeps those reversals (barrier persistence) while
demoting the premature ones. Measured (BTC/ETH/SOL/AAVE × 5m..1d, all flags
wired): **23/24 byte-identical**, only AAVE H1 changes — the two whipsaw pairs
collapse to one honest CHoCH (06-23 15:00 vs 72.25, sustained hold, failed
honestly 06-24 10:00) and the breakout BOS confirms 06-24 22:00 on the first
close above 77.70 (~30h earlier), staircase 77.70 → 85.12 → 87.99. Real-data
regression fixture: `tests/liquidity/detectors/data/aaveusdt_1h_2026_05_10_07_04.json`
(1315 candles — the **full** production internal-detector slice from the
structural anchor; the release-gap/min-pullback ATR guards use the series-wide
mean true range, so a truncated window does not reproduce production state).

**Close-confirmed reported staircase floor** (`InternalStructureDetector`, as
of 2026-07-04, companion to the above): `bos_floor_require_close_break`
(constructor default `False`; wired **`True`** in `load_dashboard_data`). The
*reported* floor tracker (`prev_<side>_bos_extreme`, the level the next
continuation BOS plots against) does **not** ratchet on an advance that only
*wick-swept* it — pivot extreme beyond the current floor with no candle closing
beyond it. Such a wick never established a new formed level (its own mark is
dropped by `_reanchor_bos_close_break`), so the next continuation keeps
referencing the last close-confirmed extreme instead of the wick. Narrow by
design: an advance whose pivot never even *reached* the floor (a trailing-level
break far short of it, e.g. range breakdowns inside a post-crash range months
above the crash fundo) still ratchets — freezing there leaves later BOS
reporting a level their leg never broke, and the composition re-anchor then
drops the whole staircase (measured: AAVE D1 lost its 176→142→91 post-crash
staircase under the broad freeze variant, which was rejected). Companion stash:
the failed-CHoCH staircase restore previously set the reported tracker from the
*gate* (`last_<side>_bos_<extreme>`), which legitimately ratchets on wick-only
breaks — laundering the wick back into the reported floor across a provisional
CHoCH. Under the flag, the reported tracker is stashed at each CHoCH alongside
the gate (`pre_choch_<side>_bos_extreme`) and restored from its *own* stash
(max/min'd with the CHoCH origin, whose break the failure itself
close-confirmed). The state machine — gate, trend, CHoCH promotion — is
untouched: only what an emitted BOS *reports* (and thus where the re-anchor
confirms it) changes. Motivating case (measured, AAVEUSDT H1 `limit=1200`,
same wick as the leg-origin case above): the 06-26 breakout BOS (px 87.99)
reported the swept 77.94 wick instead of the close-confirmed 77.70 first top —
the 06-17 wick advance ratcheted the tracker, and the 06-24 `CHOCH_FAILED`
restore reinjected it from the gate. On, it reports 77.70. Measured
(BTC/ETH/SOL/AAVE × 5m/15m/30m/1h/4h/1d): **zero marks lost**; 10 of 24 combos
change and every change is a reference correction to the earlier
close-confirmed level (BTC 30m 06-13: 64362.60 wick → 64250.00, the documented
pre-break-drop wick; BTC 15m/30m 07-03: 62386.70 → 62180.00; ETH 1h/4h 06-02
converge on 1965.48; ETH 1d 02-02: 3302.20 → 3100.00) or a mark *gain* (AAVE
1d: the May-2026 crash BOS appears, ref the 05-08 91.85 retest low — off, it
referenced the pre-break 85.67 and `_drop_pre_break_reference_bos` killed it).
Real-data regression fixture:
`tests/liquidity/detectors/data/aaveusdt_1h_2026_06_05_27.json` (552-candle
self-contained window; off → 06-26 BOS ref 77.94, on → 77.70). Off =
byte-for-byte identical. Not mirrored into `SwingStructureDetector` (not
drawn).

**Provisional live-edge BOS** (`InternalStructureDetector`, as of 2026-07-05):
`emit_provisional_bos` (constructor default `False`; wired **`True`** in
`load_dashboard_data`). At the right edge a continuation can *close* beyond the
staircase floor (`last_bear_bos_low`/`last_bull_bos_high`) while its confirming
swing pivots have not formed yet (the swing-lookback lag), so the state machine
emits no BOS even though structure broke by close — the "why is there no BOS at
the June low" case (BTC D1: price closed below the 59800 floor on 06-25 → new
lows to 57758, but 57758 is not a confirmed pivot yet). When set, at the end of
`detect` a single `MarketStructure` with `provisional=True` is emitted at the
first candle that closed beyond the floor (`reference_price_level` = the floor,
`reference_timestamp` = the floor's origin, `price_level` = the live leg
extreme), computed from authoritative final state (trend + floor), **never
re-derived outside the detector**. Purely additive (appended, excluded from the
staged-BOS dedup; with the flag off the output is byte-for-byte identical) and
**skipped by** `_reanchor_bos_close_break` / `_drop_pre_break_reference_bos`. The
frontend renders it dimmed + `SparseDotted` with a `?` suffix (`BOS? ▼`), like a
weak CHoCH; it is superseded by the confirmed BOS once pivots form, or vanishes
if the trend flips first (an intentional live-edge repaint, honestly signaled by
the dimmed style — the payoff is *only* the live edge, so a static backtest shows
nothing; it is measured by **walk-forward replay**).
*Continuation gate*: `bull_floor_from_bos` / `bear_floor_from_bos` track whether
the current floor is a genuine BOS extreme (set `True` on a BOS advance, or a
`CHOCH_FAILED` restore of the resumed trend's real floor) or merely *seeded* at a
fresh CHoCH with that CHoCH's own level (`False`). The provisional emits only when
the flag is `True`: a fresh CHoCH's seed level has necessarily already been
closed beyond (that is what confirmed the CHoCH), so a provisional there just
doubles the CHoCH line (the NEAR M15 clutter — a provisional bullish BOS on the
1.965 bullish-CHoCH level). The `CHOCH_FAILED`-restore = `True` keeps the BTC D1
59800 case (no new BOS since the flip, but the floor is the genuine 31/01 BOS
low). Measured (walk-forward, BTC/ETH/SOL × 1h/4h/1d): **85% of resolved
provisional marks confirm** (up from 67% without the gate — it removed the
CHoCH-seed lead≈0 redundants and several repaints, keeping every genuine
continuation), ~11-candle median lead. Not mirrored into `SwingStructureDetector`
(not drawn).

**Provisional live-edge CHoCH** (`InternalStructureDetector`, as of 2026-07-06):
`emit_provisional_choch` (constructor default `False`; wired **`True`** in
`load_dashboard_data`). The mirror of the provisional BOS for the *reversal*: at
the right edge a counter-trend move can *close*-break the standing **structural**
CHoCH reference (`validated_choch_<side>` with `_structural=True`, e.g. a BOS
leg-origin) for `persistence_candles` consecutive closes — the same sustained
break the confirmed CHoCH demands — while its confirming swing-low/high pivot has
not formed yet (the swing-lookback lag). The state machine emits no CHoCH, so a
genuine forming reversal is invisible until the pivot confirms ~`swing_lookback`
candles later — the SOL M15 case (price sustained a close-break below the 80.72
leg-origin reference but the fundo was too fresh to be a confirmed pivot, so the
bearish CHoCH did not render, only a `LIQUIDITY_SWEEP`). When set, at the end of
`detect` a single `MarketStructure` with `provisional=True`,
`event=CHANGE_OF_CHARACTER`, `reference_structural=True` is emitted at the first
candle that started the sustained close-break (`reference_price_level` = the
structural reference, `reference_timestamp` = its pivot, `price_level` = the live
leg extreme), computed from authoritative final state — never re-derived outside
the detector. Gates: **only a structural reference** qualifies (mirror of the BOS
`floor_from_bos` gate — a weak re-anchor/fallback level would repaint as chop;
since 2026-07-11 `emit_provisional_choch_weak` relaxes this, at the weak-ref
barrier persistence — see "Provisional CHoCH against weak references" below),
and a poke that closes beyond for *fewer* than `persistence_candles` and reclaims
is (correctly) just a sweep and emits nothing. A live-edge reversal **supersedes**
a same-tail provisional BOS (`prov_event = None` — the two references are on
opposite sides of price, so a double would draw a contradictory `BOS?`/`CHoCH?`
pair). Purely additive (appended, off = byte-for-byte identical) and **skipped
by** `_reanchor_bos_close_break` / `_drop_pre_break_reference_bos` (non-BOS) and
by the frontend line-termination logic (a provisional mark never truncates a
confirmed line — `!other.provisional` in `structureLineEndTime`). The frontend
renders it dimmed + `SparseDotted` with a `?` suffix (`CHoCH? ▼`), like a
provisional BOS; it is superseded by the confirmed CHoCH once the pivot forms, or
vanishes if price reclaims the level (an intentional live-edge repaint). Measured
(walk-forward, BTC/ETH/SOL/AAVE × 15m/30m/1h/4h, 350 steps): **~50% of resolved
provisional marks confirm** (a lower bound — some "repaints" are ref re-anchors
the exact-match missed, or confirmations just past the replay window), ~8-candle
median lead. Reversals at the live edge are inherently more sweep-prone than
continuations (hence lower than the BOS's 85%); the dimmed style is what
communicates that. Real-data regression fixture:
`tests/liquidity/detectors/data/solusdt_15m_2026_06_30_07_06.json` (500-candle
self-contained window; off → no provisional, on → one bearish `CHoCH?` @ 80.72).
Not mirrored into `SwingStructureDetector` (not drawn).

**Fast-fizzle CHoCH invalidation marker** (`InternalStructureDetector`, as of
2026-07-07): `choch_fizzle_reclaim_candles` (constructor default `None` = off;
wired **`30`** in `load_dashboard_data` via `_CHOCH_FIZZLE_RECLAIM_CANDLES`). The
normal CHOCH_FAILED only invalidates a CHoCH once price *closes* back through the
far **leg origin** (the swing the reversal launched from). A CHoCH whose reversal
fizzled — price reclaims the very level the CHoCH *broke* and then ranges above
it — can therefore hang unfailed for a long time while its line runs to the chart
edge, because the closes never clear the distant origin (the SOL M15 case: a
bearish CHoCH at 80.72 whose confirming continuation BOS was wick-only, so
dropped from the chart, leaving the line looking unbroken; price reclaimed 80.72
in 14 candles yet the closes never cleared the 82.3 origin, so it stood for over
a day). When set, at the end of `detect` the **standing** CHoCH (the most recent
trend-defining CHANGE_OF_CHARACTER whose line is still open, tracked as
`standing_choch_ref`/`_index`/`_dir`, set at every CHoCH emission and cleared at
every CHOCH_FAILED) gets an additive CHOCH_FAILED **marker** at the first candle
that starts a sustained (`persistence_candles`) close-reclaim of its own broken
level, **if that reclaim starts within `choch_fizzle_reclaim_candles` of the
CHoCH**. A reclaim *after* the window is genuine follow-through (the reversal
held) and left alone — so the number separating a fizzle from a held reversal is
a **wide plateau** (the NEAR M5 genuine reversal held its level 133 candles
before reclaiming; the SOL M15 fizzle 14 — any K in `[~20, ~100]` splits them).
The marker is **purely additive** and does **not** flip the state-machine trend:
the frontend's `failedChochTime` pairs it (same direction, no intervening
same-direction CHoCH) to terminate the stale line at the reclaim, and it renders
as a normal solid failure mark. It is flagged `provisional=True` **only** so the
replay consumers (`LiquidityHuntEngine`, `NarrativeEngine`) skip it — the
detector trend never flipped, so the hunt/narrative reading must not either (the
hunt stays inócuo). Two rejected alternatives, both ruled out by measurement: a
*closer origin decided at emission* (from leg geometry) cannot separate the cases
— NEAR's leg is 2.23 ATR tall, SOL's cut point 2.20 ATR, they collide — and a
*real trend-flip CHOCH_FAILED* cascades the whole downstream CHoCH sequence
(+206/-220 events across BTC/ETH/SOL/AAVE/NEAR × 5m..1d), the additive-over-
state-machine failure mode. The additive marker is surgical: **+8/-0 across the
same 30 combos** (one marker per chart whose standing CHoCH fizzled, zero
removals, zero CHoCH-count changes). Real-data regression fixture:
`tests/liquidity/detectors/data/solusdt_15m_2026_06_24_07_07.json` (1243-candle
self-contained window; off → no CHOCH_FAILED, on → one bearish marker @ 80.72,
07-06 15:15; the reclaim lands 9 candles after the CHoCH, so a window of 8 leaves
it unmarked). Off = byte-for-byte identical. Not mirrored into
`SwingStructureDetector` (not drawn).

*Resumed-fizzle cancel (composition level, as of 2026-07-11)*:
`_drop_resumed_fizzle_markers` in `dashboard_data` (after the two BOS passes)
drops a fizzle marker followed by a **chart-surviving** same-direction BOS: the
reclaim was a deep pullback the reversal recovered from, not a fizzle, and the
marker would falsely invalidate a standing CHoCH (the ETHUSDT H1 case: the
06-29 bullish CHoCH's 1583 reference was reclaimed for a day — the marker fired
— then the reversal printed a bullish BOS staircase to 1833, and on the chart
the false ✕ let the 06-19 bearish CHoCH line run to the edge via the
failed-CHoCH transparency rule). The cancel lives at composition, not in the
detector, because only composition knows which BOS survive
`_reanchor_bos_close_break` — the SOL M15 motivating fizzle also has a
same-direction BOS after its reclaim at detector level, but it is wick-only and
dropped from the chart, so the line there is genuinely stale and its marker
stands. Measured (BTC/ETH/SOL/AAVE/NEAR × 15m..1d): drops exactly 3 markers,
all the false pattern (ETH 15m/30m/1h — the same June-bottom reversal); the
genuine ones (BTC 1d, SOL 15m) are untouched. Real-data regression fixture:
`tests/liquidity/detectors/data/ethusdt_1h_2026_05_10_07_11.json` (1500-candle
production H1 slice; the raw detector emits the fizzle, `_run_internal_structure`
drops it). The frontend companion: a fizzle (provisional `choch_failed`) is
excluded from `isFailedChoch` (the line-termination *transparency* rule) — the
state-machine trend never flipped back, so a fizzle-marked CHoCH still cuts the
prior opposite CHoCH/BOS lines; only its *own* line stops at the reclaim
(`failedChochTime` keeps counting fizzles for that, via `includeFizzle`).

**Failed-CHoCH whipsaw fixes** (`InternalStructureDetector`, as of 2026-07-11,
two companion flags): the BTC H1 18–25/06 crash printed a single bearish BOS
(62232) and then only sweeps because two weak bullish CHoCHs flipped the trend
mid-crash — with the trend flipped, every new low was counter-trend (sweep,
never BOS), the `CHOCH_FAILED` prints late (at the next confirmed pivot) and
never retro-reclassifies, and the second flip (06-25 04:00, a cold-start
fallback CHoCH at the 61256 trailing LH firing on a 4-candle bounce one day
after the previous failure) happened because a failed-CHoCH flip arms no origin
(one-shot), so the unconfirmed-window fallback suppression lapses at the
failure. (1) `choch_failed_fallback_suppress_candles` (constructor default
`None` = off; wired **`20`** flat via
`_CHOCH_FAILED_FALLBACK_SUPPRESS_CANDLES`): the cold-start `active_<side>`
fallback stays suppressed for this many candles after a *same-direction*
`CHOCH_FAILED`; structural/validated references are untouched, so a genuine
reversal (which promotes a leg origin via BOS) still fires — the motivating
whipsaw fired 15 candles after the failure. (2) `stage_choch_failed_window_bos`
(constructor default `False`; wired **`True`** via
`_STAGE_CHOCH_FAILED_WINDOW_BOS`): while a CHoCH is provisional, counter-trend
staircase breaks (new extremes beyond the previous recorded one, seeded from
the pre-CHoCH reported-floor stash) are recorded (`_EatenBreak`); at the
`CHOCH_FAILED` each is staged as an additive BOS of the resumed trend (merged/
deduped like the impulse staging, close-break re-anchored at composition, so
wick-only ones drop) and the eaten extremes fold into the restored staircase
floors (gate: most extreme pivot; reported floor: close-confirmed only) so the
next real continuation references the true prior formed extreme. Recorded
breaks are discarded if the CHoCH confirms; a fresh CHoCH clears the window.
Measured (BTC/ETH/SOL/AAVE/NEAR × 5m..1d, `limit=1200`): 17/30 combos
identical; the rest are staircase splits (one gap-jumping BOS becomes two
steps, e.g. NEAR 1h `3.08 ref=2.58` → `2.76 ref=2.58` + `3.08 ref=2.76`); zero
new `CHOCH_FAILED` anywhere. BTC 1h (motivating): the crash gains the
62232 → 61870 → 59060 → 58030 staircase, the 06-25 whipsaw CHoCH becomes a
sweep, the phantom 06-30 `CHOCH_FAILED` disappears; cost: the recovery CHoCH
fires 07-02 09:00 (ref 60758) instead of 07-01 13:00 (ref 59444 fallback),
~20h later against a better reference. Real-data regression fixture:
`tests/liquidity/detectors/data/btcusdt_1h_2026_05_18_07_04.json` (1136-candle
production H1 slice). Off = byte-for-byte identical. Not mirrored into
`SwingStructureDetector` (not drawn).

**Displacement release for spent cycles** (`InternalStructureDetector`, as of
2026-07-11): `stale_reanchor_displacement_atr` /
`stale_reanchor_displacement_candles` (constructor defaults `None` = off, must
be set together; wired **16.0 / 15** in `load_dashboard_data` via
`_STALE_REANCHOR_DISPLACEMENT_ATR`/`_CANDLES`). The staleness re-anchor's
candle timer is blind to how far a leg *stretched*: after a violent move the
reversal reference stays pinned at the pre-move leg origin for the full
`stale_reanchor_candles` window (H4 = 60 candles = 10 days), so the strongest
bounce of the whole cycle is consumed as a `LIQUIDITY_SWEEP` against a level
many ATRs overhead — the ETHUSDT H4 case: the 06-05 crash BOS (1503, breaking
1712) promoted its pre-crash leg origin **2046** (structural) as the bullish
CHoCH reference; the +23% bounce to 1848 nine days later printed as a sweep,
and the chart sat on the mid-crash BOS for **35+ days**. When the gap between
the effective reversal reference (`validated or origin or active`) and the
leg's running extreme (`bear_leg_low`/`bull_leg_high`) reaches N × the series'
mean true-range% (same volatility normalization as the release gap — per-TF
adaptivity for free), the cycle is *spent*: the staleness threshold shrinks to
the displacement candle count and the re-anchor **window starts at the last
advance** (the post-move range) instead of a fixed trailing window, so the
reference lands on the new range's first pullback extreme (ETH: 1722, the
06-07 top). Everything else is the existing staleness machinery
(`reanchor_opposite` with all its guards). Measured (BTC/ETH/SOL/AAVE/NEAR ×
15m/30m/1h/4h/1d, `limit=1200`): **N=16 changes exactly 6/25 combos, all the
motivating pattern** — ETH 4h resolves into CHoCH↑ 06-15 (ref 1722, weak) →
CHoCH↓ 06-23 → BOS↓ 06-24 (1510) → CHoCH↑ 07-02 (ref 1692, structural), trend
ends bullish; BTC 4h gains the honest 06-14 CHoCH↑ + 06-27 `CHOCH_FAILED` pair
around the June crash; AAVE 4h gains the entirely-missing Feb→Mar −30% bearish
cycle (CHoCH↓ 03-07 + BOS staircase 104→92); NEAR 1h / SOL 4h flip the June
bottom ~6–17 days earlier with a confirming BOS staircase. N=8 fires on
routine legs (25/25 combos — a leg's ref-to-extreme gap *is* its height); N=14
starts reshaping BTC 30m/1d; N=18 loses AAVE 4h / NEAR 1h; N=20 loses the ETH
4h target itself (~19 ATR). M is a wide plateau (10/15/20/25 byte-identical on
the whole matrix). A companion **weak-ref sweep ratchet** (a sweep beyond a
weak validated ref moves it to the swept extreme) was prototyped alongside and
**rejected by measurement**: on a grinding decline each lower sweep ratcheted
the reference down just ahead of the confirming closes, so the reversal CHoCH
could never fire (it erased the genuine ETH H4 March 2385→1936 bearish cycle);
the candidate pipeline's sweep re-anchor requires trend *resumption* before a
swept level goes live, and skipping that gate is what broke. Real-data
regression fixture:
`tests/liquidity/detectors/data/ethusdt_4h_2025_11_21_2026_07_11.json`
(1395-candle production H4 slice). Off = byte-for-byte identical. Not mirrored
into `SwingStructureDetector` (not drawn).

**Provisional CHoCH against weak references** (`InternalStructureDetector`, as
of 2026-07-11): `emit_provisional_choch_weak` (constructor default `False`,
requires `emit_provisional_choch`; wired **`True`** in `load_dashboard_data`).
The provisional live-edge CHoCH originally required a *structural* reference —
but after any re-anchor the standing reference is weak, so exactly in the
released/reset cycles the displacement release creates, the forming reversal
was invisible (the ETH H4 case: price closed above the weak reference with
nothing on screen). When set, a weak reference also qualifies, sustaining the
weak-ref barrier persistence (`choch_weak_ref_persistence_candles`) where
wired instead of the base; the emitted mark carries
`reference_structural=False`, so the frontend renders it dimmed with a `?*`
suffix (forming *and* weak — `?` leads, since a full repaint is the stronger
caveat). On coarse timeframes it is near-inert (the pivot lag ~5 is shorter
than the base persistence 12, so the confirmed CHoCH arrives with the
provisional); the lead materializes intraday where the weak barrier (4) is
shorter than the pivot lag. Measured (walk-forward over the last 400 candles,
BTC/ETH/SOL/AAVE/NEAR × 15m/30m/1h): 10 weak provisional marks, **10/10
followed by a confirmed same-direction CHoCH** (among them the BTC 1h 07-02
recovery CHoCH from the whipsaw-fix cost, now visible at the live edge).
Real-data regression fixture:
`tests/liquidity/detectors/data/solusdt_15m_2026_06_26_07_11.json`
(1474-candle window ending at the live edge; off → no provisional CHoCH, on →
one bullish `CHoCH?*` @ 78.34). Off = byte-for-byte identical.

**Weak-ref CHoCH failure at the broken level** (`InternalStructureDetector`, as
of 2026-07-12): `choch_weak_ref_fail_at_broken_level` (constructor default
`False`; wired **`True`** in `load_dashboard_data` via
`_CHOCH_WEAK_REF_FAIL_AT_BROKEN_LEVEL`). A CHoCH fired against a **weak**
reference (a synthetic re-anchor level or the cold-start fallback) arms its own
*broken level* as an additional invalidation reference alongside the far leg
origin: the synthetic level's break was the reversal's only evidence, so a
sustained close (base persistence) back through it emits a real `CHOCH_FAILED`
(trend flips back) at the *tighter* of the two levels — structural CHoCHs keep
the origin-only failure. Motivating case (BTCUSDT D1): the 2026-04-30 bullish
CHoCH against the weak 75998.9 re-anchor collapsed within days, but the 59800
origin was never sustained-broken — the trend sat bullish through the entire
82.8k→57.7k crash (−30%), every new low printed as a counter-trend sweep, and
the chart showed no bearish BOS at the bottom, unlike ETH D1 (whose rally never
fired a CHoCH and whose June break of 1736 printed the continuation BOS at the
fundão). On: `CHOCH_FAILED` 05-26 at 75998.9 + BOS↓ 06-25 ref 59800 + trend
bearish — the ETH analogue. **Weak-level failures re-seed the resumed
staircase at the failure level** (gate + reported floor, like a CHoCH seeds its
cycle; `*_floor_from_bos = False` so a provisional BOS never doubles the
failure line) instead of restoring the pre-CHoCH stash: the weak reference
existed precisely because the old cycle was spent, and the plain restore was
measured to pin the resumed trend's whole next leg on an ancient floor (AAVE 4h
lost its entire Feb→Mar −30% staircase, NEAR 1h its June one). The reported
floor folds the deepest eaten-window extreme when `stage_choch_failed_window_bos`
recorded breaks (else the next continuation re-reports the failure level and,
with no matching opposite pivot, its line origin never resolves — a stretched
duplicate of the ✕ line). Origin-triggered failures restore exactly as before.
Measured (BTC/ETH/SOL/AAVE/NEAR × 15m..1d, `limit=1200`): 12/25 identical;
every change is the weak-CHoCH lifecycle — whipsaw CHoCH pairs become honest
`CHoCH + CHOCH_FAILED` sequences with the resumed trend's staircase intact
(BTC 4h gains the June-crash BOS staircase and a richer March; NEAR 1h keeps
its June staircase with an honest 06-14/06-19 failure pair; SOL M15's live-edge
fizzle becomes a real failure; ETH 4h June reads `✕ @ 1721.57` + BOS↓ 1510 with
the 07-02 recovery CHoCH untouched). Real-data regression fixture:
`tests/liquidity/detectors/data/btcusdt_1d_2022_06_03_2026_07_11.json`
(1500-candle production D1 slice; off → fizzle marker only + trend bullish +
no bearish BOS after January, on → real failure + fundão BOS + trend bearish).
Off = byte-for-byte identical. Not mirrored into `SwingStructureDetector` (not
drawn).

**CHoCH origin = deepest leg extreme** (`InternalStructureDetector`, as of
2026-07-05): `choch_origin_leg_extreme` (constructor default `False`; wired
**`True`** in `load_dashboard_data`). A CHoCH's *origin* — the level whose
sustained break back through it (before a confirming BOS) invalidates the
unconfirmed reversal as a `CHOCH_FAILED` — is now the **deepest extreme of the
reversed leg** (`_extreme(active_<side>, pending_<side>)`), not the trailing
`active_<side>` alone. The trailing reference ratchets toward the new high/low
through the reversal leg's intermediate pivots, so by the time the CHoCH
confirms it can sit right next to the new extreme, arming an *instant* failure
on the first minor pullback and ping-ponging the trend into weak
CHoCH/`CHOCH_FAILED` pairs — and because a failed CHoCH never emits an opposite
CHoCH, the genuine reversal line never terminates and stretches across the
chart. Motivating case (measured, NEARUSDT M5 `limit=1200`): a bullish CHoCH
07-04 14:10 (a genuine +4% reversal to 2.039) whose true fundo was 1.967 but
whose `active_low` had ratcheted up to a 2.004 higher-low near the top — off,
it failed immediately at 2.004 and spawned a weak `CHoCH* ▲` and a second
failure (the line ran to the chart edge); on, the origin is 1.967, the CHoCH
holds through the shallow pullbacks and fails once honestly when price breaks
the true fundo (03:40, ref 1.967), so its line pairs with that failure and
terminates. Neither `active_<side>` nor `pending_<side>` alone is the fundo —
`active_low` ratchets up through higher-lows, `pending_low` can retain a
shallower early-leg low — so the *deeper* of the two is taken (mirror: the
higher of `active_high`/`pending_high` for a bearish origin). Measured
(BTC/ETH/SOL/AAVE/NEAR × 5m..1d, `limit=1200`): **`CHOCH_FAILED` drops ~33%**
(63 → 42), converting whipsaw CHoCH/fail pairs into sweeps or holding CHoCHs
(BOS count neutral, +17 sweeps); the few timeframes that gain CHoCHs (BTC 15m)
are genuine chop where the added CHoCHs are `struct=True`. Real-data regression
fixture: `tests/liquidity/detectors/data/nearusdt_5m_2026_07_04_05.json`
(373-candle self-contained window; off → whipsaw with a `CHOCH_FAILED` @ 2.004,
on → one `CHOCH_FAILED` @ 1.967). Off = byte-for-byte identical. Not mirrored
into `SwingStructureDetector` (not drawn).

**CHoCH confirmation** (`InternalStructureDetector`): the CHoCH reference is
the **pullback (origin) of the most recent continuation-confirmed BOS**. A
BOS's pullback (the confirming LH for bearish, HL for bullish) starts as a
provisional `candidate_choch_<side>`; it is promoted to
`validated_choch_<side>` only when a subsequent move makes a **new leg
extreme** (below `bear_leg_low` for bearish, above `bull_leg_high` for
bullish) — a genuine continuation. A pullback-BOS formed during a retrace
that does not extend the leg cannot ratchet the reference down. Each
continuation pullback must also stay on the correct side of the previous
pullback (LH staircase / HL staircase) via a dedup gate. A sweep that pokes
beyond the current `candidate_choch_<side>` re-anchors that candidate to the
swept extreme (more-extreme only — the "sweep then expand" origin), but a
sweep never moves the *validated* reference directly; a sweep with no
continuation never promotes. A bullish CHoCH fires on a sustained break above
`validated_choch_high or choch_origin_high or active_high`; any break that
doesn't clear the reference, or doesn't hold, is a `LIQUIDITY_SWEEP`. The
`active_<side>` cold-start fallback ensures the detector can flip trend
during the bootstrap phase (before any validated/origin reference has been
built), preventing the trend from getting stuck if the initial direction was
wrong. The `choch_origin` one-shot blind-spot fallback prevents the trend
from getting stuck after a CHoCH whose reversal fails before a fresh
validated reference can be rebuilt.

**Failed CHoCH (`CHOCH_FAILED`, both detectors)**: a CHoCH is provisional
until a same-direction BOS confirms it. While unconfirmed it carries an origin
(`bull_choch_origin`/`bear_choch_origin` — the active low at a bullish CHoCH /
active high at a bearish CHoCH, the swing the CHoCH move launched from). A
sustained break back through that origin before a confirming BOS emits a
`CHOCH_FAILED` event (direction = the failed CHoCH's direction,
`reference_price_level` = the broken origin) and flips the trend back; it
supersedes the `choch_origin` recovery for the unconfirmed window at a tighter
level. The origin is retired on the confirming BOS or at the next trend flip,
and a failed-CHoCH flip arms no opposite origin (one-shot, no ping-pong).
Because a failed CHoCH means the original trend never ended, the resumed
trend's BOS staircase continues from its *genuine* last BOS extreme, not the
(often higher-low / lower-high) CHoCH origin — otherwise a non-extending BOS
could print past the previous same-direction BOS. The reversing trend's
staircase floor is stashed (`pre_choch_bear_bos_low`/`pre_choch_bull_bos_high`)
when the CHoCH fires and restored on failure (more extreme of it and the
origin); a confirming BOS discards the stash.

**CHoCH lines across a failed CHoCH (frontend)**: in
`MainChart.structureLineEndTime`, a provisional CHoCH that is later invalidated
(a same-direction `choch_failed` fires before another same-direction CHoCH
intervenes — paired via `isFailedChoch`) is transparent to line termination:
the prior BOS/CHoCH line it appeared to cut keeps running through it until a
*genuine* opposite-direction CHoCH (or the chart edge), matching the resumed
structure. A **fizzle marker** (a *provisional* `choch_failed`) does **not**
grant this transparency (as of 2026-07-11, `isFailedChoch` passes
`includeFizzle: false` to `failedChochTime`): the state-machine trend never
flipped back, so the fizzle-marked CHoCH still genuinely reversed structure and
keeps cutting the prior opposite lines — only its *own* line stops at the
reclaim (own-line termination keeps counting fizzles). Before this, a fizzle
let the prior opposite CHoCH line run to the chart edge (the ETH H1 stretched
bearish CHoCH).

**CHoCH confirmation** (`SwingStructureDetector`): uses the older
candidate/baseline model. A candidate LH/HL is promoted to validated when a
subsequent BOS beats `candidate_choch_<side>_baseline`. `SwingStructureDetector`
**always sets** origin (every CHoCH sets
`choch_origin_<opposite> = active_<side>`): with `persistence_candles=10`
ping-pong risk is negligible, while the higher lookback makes the blind-spot
window long enough that one-shot would re-introduce the stuck-trend bug.

**`MarketStructure.reference_timestamp`**: CHoCH events carry the timestamp of
the `validated_choch_<side>` pivot (the promoted LH/HL), allowing the frontend
to anchor CHoCH lines at their true origin rather than at the break candle.

**POI (Order Block) module** (rewritten 2026-07-10, made Pine-faithful
2026-07-11): `POIDetector` implements the MSB-OB logic (EmreKb's "Market
Structure Break & Order Block" TradingView indicator, minus the zigzag
drawing) as a **faithful batch port**: `barssince`-window pivots (local
extremes since the previous opposite *signal*, not leg extremes),
value-compared same-pivot guard, and running anchor scans with the
indicator's `[pivot_len]`-lagged window bound. Fidelity was verified against
the indicator on TradingView with real BTCUSDT 15m data — a user-reported
divergence (a missing 07-09 Bu-OB) traced to an earlier leg-extreme pivot
variant that flipped less often; the port reproduces the on-chart boxes
exactly (regression fixture
`tests/liquidity/detectors/data/btcusdt_15m_2026_06_25_07_11.json`, 44
zones). Flips require a fib-extension break (`fib_factor=0.33`); each MSB
marks the last opposite-direction candle of the impulse-origin leg as the
order block and the last same-direction candle of the broken-pivot leg as
the `BREAKER_BLOCK` (prior extreme swept) / `MITIGATION_BLOCK` (`kind`
field), full candle range, frozen. Lifecycle: ACTIVE → INVALIDATED on a
single close beyond the far boundary; touches never retire a zone. The
MITIGATED state, `RTOSweepEvent`, and `poi_sweep_events` were removed
everywhere (domain, `DashboardData`, API schema, `ManipulationCycleDetector`
input, `NarrativeEngine` timeline, frontend RTO markers). The React frontend
renders active zones via `POIBoxesPrimitive`, starting each box at the
anchor candle, labeled `OB`/`BB`/`MB` + direction arrow.

**Manipulation cycle detection**: `ManipulationCycleDetector` connects
existing observations into three-phase Wyckoff/SMC cycles (accumulation →
sweep → expansion). Works retrospectively (matching sweeps to prior
accumulation and subsequent expansion BOS) and prospectively (identifying
active accumulation zones where stops are building). Minimum accumulation
candles are timeframe-adaptive (`_TIMEFRAME_MIN_ACCUMULATION`: M5=15,
M15=10, M30=7, H1=7, H4=3). Prospective accumulations are clustered per
side within `proximity_pct` to avoid duplicate cycles for overlapping zones,
and zones already targeted by sweep-based cycles are excluded via proximity
matching. The React frontend renders cycles in a sidebar panel
(`ManipulationCyclesPanel`, max 5 cards) and as chart overlay boxes (max 3,
togglable via CHART ON/OFF button).

**Behavior divergence detection**: `BehaviorDivergenceAnalyzer` cross-references
volume delta with zone proximity and structure events to detect when institutional
flow opposes visible price direction. Four types: distribution (price rising +
negative VD near buy-side zone), accumulation (price falling + positive VD near
sell-side zone), exhaustion (VD declining after BOS), absorption (high volume +
small price movement near zone). Window size is timeframe-adaptive. Wired into
`DashboardData` and the API schema; frontend TypeScript types are defined but
no sidebar panel or chart overlay yet.

**React frontend panes**: the main chart has three synced panes — candlestick
(main), volume delta histogram, and RSI(14) with regular divergence detection
(bullish LL+HL, bearish HH+LH). All three share synchronized time scales and
crosshairs.

**Narrative engine**: `NarrativeEngine` (in `app/narrative.py`) synthesizes all
detection layer outputs into a `MarketNarrative` — a chronological timeline of
institutional events, pattern anomalies, phase-dependent summary, and
confluence count. Wired into `DashboardData`, API schema, and frontend
TypeScript types. Five anomaly detectors: expansion+exhaustion, accumulation+
distribution, concentrated liquidity, unconfirmed CHoCH, BOS without VD.
Summary tone adapts per manipulation phase (neutral, accumulation, manipulation,
expansion, failed) with retail bias and HTF alignment context. Frontend
`NarrativePanel` sidebar component is not yet implemented.

**Leverage liquidation estimator** (psychology-evolution roadmap idea 3,
completed): the data layer gained a second provider port,
`FuturesDataProvider` (`BinanceFuturesDataProvider` via ccxt `binanceusdm`),
sourcing open interest / funding / long-short ratio.
`LeverageLiquidationEstimator` infers the over-leveraged side and projects
`LeverageLiquidationMap` liquidation bands at tiers 10x/25x/50x/100x around
liquidity-zone entries, each time-bounded from entry formation to the
liquidation-hit candle (or still live). Wired into
`DashboardData.liquidation_map` (degrades to `None` for spot-only symbols) +
API schema. Frontend renders the bands via `LiquidationBandsPrimitive`
(time-bounded boxes, warm color per leverage tier: 10x amber → 100x crimson)
behind a `⊟ Liq` toolbar toggle, decluttered to a near-price subset (live pools
+ recent hits, ±8%, ≤12) while the full set stays in the API for backtesting.

**OI regime analysis** (as of 2026-07-02): `OIRegimeAnalyzer` (psychology)
joins open interest with price to read *who is behind the move*: the
price × OI matrix as a rolling current regime (long/short buildup = new money,
short covering / long liquidation = unwinding) plus per-event qualification of
the internal BOS/CHoCH/SWEEP stream (`NEW_MONEY`/`COVERING`/`FLUSH`/`FLAT`),
measured through one candle *after* the event so a sweep's liquidation flush
(which lands on the next OI sample) is caught.
`BinanceFuturesDataProvider.get_open_interest_history` paginates past the
500-row cap (clamped to Binance's ~30-day retention +1h margin — a `startTime`
at exactly −30d gets error -1130), so OI covers the visible window on intraday
timeframes; on D1 coverage is ~30 points and most structure events fall
outside it (regime still shown, events unqualified — intended degradation).
Wired into `DashboardData.oi_analysis` + API. Frontend: "OI Regime" KPI card
(with HTF-trend confluence badge) and `⊕`/`⊖`/`⚡` suffixes on structure
labels. Historical continuation-frequency stats were deliberately deferred.

**Liquidity hunt state** (as of 2026-07-06): `LiquidityHuntEngine` (app-level
synthesizer) answers "who is the resting liquidity of the current move, and
has it been captured yet" — the counter-trend trap question (an LTF CHoCH
against the HTF trend makes its entrants the fuel; e.g. SOL H1 bearish CHoCH
inside an H4 uptrend → shorts get swept before the correction can proceed).
Combines the internal-structure trend vs HTF direction, nearby equal-level
zones and liquidation bands (intact vs captured-since-the-flip), OI flush
events, and the OI regime into a `LiquidityHuntState` with a strict `CAPTURED`
gate (all mapped pools consumed **and** OI no longer unwinding). Wired into
`DashboardData.liquidity_hunt` + API schema. Frontend: the KPI row's Price
card was replaced by the **Liquidity Hunt** conclusion card (rightmost).
Purely observational language throughout (who is the liquidity / when it was
captured), per the research-platform constraint.

**Multi-timeframe overview / Structure Ladder** (as of 2026-07-11):
`app/overview.py` + `GET /api/overview` + the `MultiTimeframePanel` sidebar
panel — a per-timeframe state ladder (M5→W1: internal trend, last structural
event, forming provisional marks, hunt phase), sharing the exact production
detection pipeline via `dashboard_data._run_internal_structure` so the ladder
always matches what the chart renders per timeframe. Per-timeframe API
caching with proportional TTLs. This is **phase 1** of the user's multi-TF
score plan (see memory: the composite confluence score over OB/Sweep/EQL/
VOL/RSI-div/Hunt comes next; decision/execution logic stays out of this
repo). Alongside it, **narrative/anomaly synthesis was turned off by
default** at the API (`narrative=false` query param; `compute_narrative`
flag in `load_dashboard_data`) — the `NarrativePanel` exists and returns
untouched whenever the param is re-enabled.

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
