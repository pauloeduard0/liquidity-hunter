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
  `LIQUIDITY_SWEEP`/HH/HL/LH/LL) with a `MarketDirection` and `StructureScope`.
  Fields: `timestamp` (actual breaking candle, not the triggering pivot),
  `price_level` (triggering pivot's extreme), `reference_price_level` (the
  level that was broken — `active_<side>` for BOS/SWEEP, `validated_choch_<side>`
  for CHoCH), and `reference_timestamp` (for CHoCH events: the timestamp of the
  LH/HL pivot that was promoted to `validated_choch_<side>`, used to anchor
  the CHoCH line's start in the frontend).
- **`POIZone`** — an institutional order block zone, defined in
  `core/domain/poi_zone.py`. Anchored to the leg between a validated CHoCH and
  the first BOS in the same direction. Fields: `direction`, `price_low`,
  `price_high` (frozen at creation), `origin_choch_timestamp`,
  `origin_bos_timestamp`, `extreme_candle_timestamp`, `status`
  (`POIZoneStatus`: `ACTIVE`/`MITIGATED`/`INVALIDATED`), `invalidated_at`,
  `mitigated_at`. For a bullish (demand) zone: `price_low = extreme_candle.low`
  (invalidation boundary) and `price_high = (low + high) / 2` (50% midpoint).
  Bearish (supply) mirrors this.
- **`RTOSweepEvent`** — a Return-to-Origin liquidity capture event, defined in
  `core/domain/poi_zone.py`. Fires when price sweeps beyond a POI zone's
  invalidation boundary and a subsequent candle closes back inside. Fields:
  `timestamp`, `direction`, `zone_price_low`, `zone_price_high`, `sweep_extreme`
  (the most adverse price reached during the sweep).
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

Shared enums (`TimeFrame`, `MarketDirection`, `LiquiditySide`,
`LiquidityZoneType`, `StructureEvent`, `BiasSource`, `RetailPositioning`,
`POIZoneStatus`, `ManipulationPhase`, `ManipulationCycleStatus`,
`DivergenceType`, `NarrativeEventType`, `AnomalySeverity`) live in
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
  as `BinanceDataProvider`.

`BinanceDataProvider`, `OHLCVProvider`, `BinanceFuturesDataProvider`, and
`FuturesDataProvider` are re-exported from `liquidity_hunter.data`.

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
  advances unconditionally on any wick break of the active reference in the
  direction of trend. A `BREAK_OF_STRUCTURE` event is only *emitted*, however,
  when a candle within the leg also *closes* beyond the reference
  (`find_close_break_index`), and that close candle optionally passes the
  LuxAlgo-style confluence filter (`bos_confluence`, see `_common.py`).
  `confluence_filter` (constructor parameter, default `True`) enables this
  shadow-balance check: the breaking close candle must have a larger upper
  shadow than lower shadow (bullish) or vice versa (bearish). The emitted BOS
  `timestamp` is that closing candle's timestamp; `price_level` is the
  triggering pivot's extreme; `reference_price_level` is `active_<side>`.

  **CHoCH**: A counter-trend break is confirmed via **persistence** (same as
  `InternalStructureDetector`): `is_sustained_break` must hold for
  `persistence_candles` consecutive candles beyond the break. The CHoCH
  reference is `validated_choch_<side>`, promoted from `candidate_choch_<side>`
  via the same two-step baseline gate described under `InternalStructureDetector`
  below. `reference_price_level` is `validated_choch_<side>.price`;
  `reference_timestamp` is `validated_choch_<side>.timestamp`.

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
  `internal_swing_lookback` to surface minor pivots within that series). Like
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

  **BOS confirmation**: The state machine advances on any wick break, but a
  `BREAK_OF_STRUCTURE` event is only *emitted* when a candle in the leg also
  *closes* beyond the reference (`find_close_break_index`), and that close
  candle optionally passes the LuxAlgo-style confluence filter
  (`bos_confluence`): upper shadow > lower shadow for bullish, reverse for
  bearish. `confluence_filter` (constructor parameter, default `True`) enables
  this check; `load_dashboard_data` exposes it so tests can disable it. The
  BOS `timestamp` is the close-break candle's timestamp.

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

  The reversal (`CHANGE_OF_CHARACTER`) reference is tracked explicitly per
  side as `validated_choch_high`/`validated_choch_low`, distinct from the
  trailing `active_<side>` and from `pending_<side>`. Promotion to
  `validated_choch_<side>` is a two-step process via an intermediate
  `candidate_choch_<side>`: `candidate_choch_high` is the *highest*
  `LOWER_HIGH`-labeled pivot since the last promotion (or a re-bootstrap pivot
  that is functionally one — see below), not yet promoted. It is kept at the
  window **extreme**, not overwritten with each more-recent LH: within a
  promotion window LHs descend monotonically, so the highest is the pullback
  top that confirmed the BOS, and a weaker later LH must not ratchet the anchor
  down to a mid-leg level no BOS reached (the early-CHoCH bug). The matching
  `candidate = pivot` assignment therefore lives *inside* the "is None or more
  extreme" guard, like the SWEEP branch already did. SMC requires `LL1 -> LH1
  -> LL2 (confirms LH1) -> break LH1` for a bullish CHoCH, so an LH *alone* is
  not a CHoCH
  reference — `candidate_choch_high` is only a placeholder until structure
  confirms it. Alongside `candidate_choch_high`, `candidate_choch_high_baseline`
  snapshots `active_low` as it stood at the moment the candidate was set — the
  trailing low reference in effect immediately before that LH formed.
  `validated_choch_high` (the level a *bullish* CHoCH must break) is updated
  **only when a bearish BOS occurs after `candidate_choch_high` was set, and
  that BOS's pivot price is below `candidate_choch_high_baseline`** — i.e. the
  bearish leg makes a new low *relative to the low that preceded the LH*
  (a genuine `LL2 < LL1` for this candidate), not merely any continuation of
  the leg. At that moment, `candidate_choch_high` is **promoted**
  (`validated_choch_high = candidate_choch_high`, then both
  `candidate_choch_high` and `candidate_choch_high_baseline` are cleared to
  `None`); if no candidate has formed since the last promotion/reset,
  `validated_choch_high` is left unchanged. While no qualifying bearish BOS
  confirms it, `candidate_choch_high`, `candidate_choch_high_baseline`, and
  `validated_choch_high` are all **frozen**.

  This two-part gate — "a BOS after the candidate formed" *and* "beyond that
  candidate's own baseline" — replaced two earlier, each individually flawed
  designs: gating on a new *absolute* low/high of the *entire* leg (tracked as
  `last_ll`/`last_hh`) deadlocks, because the first impulsive BOS right after
  a CHoCH is often the leg's eventual extreme, after which no later pivot can
  ever exceed it — `trend` then gets stuck for hundreds of candles through an
  obvious reversal. Gating on *any* BOS after the candidate, with no baseline,
  over-promotes — `validated_choch_high` keeps ratcheting toward weaker, more
  recent LH pivots even after the leg's true reversal point has already been
  confirmed and should stay frozen. The per-candidate baseline ("beat the low
  that immediately preceded *this* LH", not "beat the whole leg's low") is
  both achievable, since each new candidate gets its own more recent baseline,
  and selective, since a later weaker LH that cannot beat its own baseline
  leaves the earlier validated reference frozen.

  A bullish CHoCH fires when, with `trend` BEARISH, a high pivot breaks
  (sustained, per the persistence rule above) *above* `validated_choch_high`;
  its `reference_price_level` is `validated_choch_high` — never the trailing
  `active_high`, never `candidate_choch_high`, never the breaking pivot. A
  high pivot that breaks the trailing `active_high` but not
  `validated_choch_high` — including while `validated_choch_high` is still
  `None` — or whose break does not hold, is a `LIQUIDITY_SWEEP` (trend
  unchanged) — an internal bounce in the still-intact bearish leg. The moment
  a CHoCH fires, the *opposite* side's `validated_choch_<side>`,
  `candidate_choch_<side>`, and `candidate_choch_<side>_baseline` are all
  reset to `None`. A **one-shot origin** (`choch_origin_<side>`) mechanism
  prevents the "blind spot" after a CHoCH: if the CHoCH was triggered via a
  *validated* reference, `choch_origin_<opposite>` is set to the
  just-promoted `active_<side>` (the extreme of the leg that just reversed),
  frozen at that value. The CHoCH check uses
  `validated_choch_<side> or choch_origin_<side>`, so the origin serves as
  fallback when validated has not been rebuilt yet. An origin-triggered CHoCH
  does **not** set `choch_origin` on the opposite side (one-shot), breaking
  any ping-pong chain: validated CHoCH → origin CHoCH → (must rebuild
  validated normally). When a candidate is normally promoted to
  `validated_choch_<side>`, `choch_origin_<side>` is cleared (redundant).

  Re-bootstrap and `candidate_choch_<side>`: a BOS/CHoCH on one side retires
  the *opposite* side's `active_<side>` (promoted from `pending_<side>`, or to
  `None` if nothing has accumulated there yet). If `active_<side>` was retired
  to `None`, the next pivot on that side silently re-bootstraps it with no
  HH/HL/LH/LL label — but if that pivot is *worse* than the just-retired
  `active_<side>` (lower for a high pivot, higher for a low pivot — judged
  against `last_high_pivot`/`last_low_pivot`, which still hold that retired
  value), it is functionally an LH/HL and still becomes
  `candidate_choch_<opposite-side>` (with `candidate_choch_<opposite-
  side>_baseline` set from the other side's `active_<side>`, same as a
  labeled LH/HL would), even though no label is emitted. Without this, a real
  LH/HL landing on a re-bootstrap pivot would never become a CHoCH candidate,
  permanently freezing `validated_choch_<opposite>` at `None`.

  **Phantom candidate invalidation**: if a `candidate_choch_low` (or high) is
  swept by price before a qualifying BOS can promote it to `validated_choch_low`,
  the old candidate — now a violated level — is replaced by the sweep pivot and
  its baseline is reset to the current trailing reference. The subsequent BOS
  then promotes the actual structural extreme (the sweep pivot) rather than the
  phantom level that had already been breached.

  The low side mirrors this exactly: `candidate_choch_low` is the *lowest*
  `HIGHER_LOW`-labeled pivot since the last promotion (or re-bootstrap
  equivalent) — the pullback floor that confirmed the BOS, kept rather than
  ratcheted up toward a higher, more recent HL — with
  `candidate_choch_low_baseline` snapshotting `active_high` at the moment it
  was set; it is promoted to `validated_choch_low` when a bullish BOS occurs
  after that HL formed *and* its pivot price is above
  `candidate_choch_low_baseline` (a genuine `HH2 > HH1` for this candidate),
  and a bearish CHoCH fires on a sustained break below `validated_choch_low`.
  `last_high_pivot`/`last_low_pivot` track the most recent swing high/low
  pivot regardless of the `active`/`pending` promotion machinery — they do not
  drive `validated_choch_<side>` directly (that role belongs to
  `candidate_choch_<side>`/`candidate_choch_<side>_baseline`), but feed the
  re-bootstrap check above and remain otherwise unused. A
  `BREAK_OF_STRUCTURE`'s `reference_price_level` is always the trailing
  `active_<side>` it broke.

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
- **`liquidity/detectors/poi.py`** — `POIDetector`: detects institutional order
  block (Point of Interest) zones from `InternalStructureDetector` output.
  `detect(candles, structure_events) -> POIResult` (`POIResult` is a frozen
  dataclass with `zones: list[POIZone]` and `sweep_events: list[RTOSweepEvent]`).

  A zone is created for each CHoCH → first-BOS-in-same-direction window: the
  extreme candle in that window (highest close for bullish, lowest close for
  bearish) defines the zone boundaries — demand zone: `price_low = candle.low`,
  `price_high = (low + high) / 2`; supply zone: `price_high = candle.high`,
  `price_low = (low + high) / 2`. Bounds are **frozen at creation**.

  Zone lifecycle:
  - `ACTIVE → MITIGATED`: price sweeps beyond the invalidation boundary (wick
    touch) and a subsequent candle closes back inside/beyond the zone. One
    `RTOSweepEvent` is emitted and the zone is retired.
  - `ACTIVE → INVALIDATED`: `invalidation_persistence_candles` (default `4`)
    consecutive closes beyond the boundary without recovery. Zone is retired
    silently with no signal.

  A pending CHoCH context is cancelled by an opposing BOS (trend resumed in the
  original direction before a new leg formed); any in-progress zone anchor for
  that side is discarded. The internal `_ZoneState` mutable tracker is an
  internal implementation detail and is converted to the immutable `POIZone`
  domain entity on output.

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
  zones, `LIQUIDITY_SWEEP` events, `RTOSweepEvent`s, BOS events, volume
  delta) into three-phase Wyckoff/SMC manipulation cycles. Works in two
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
  `_POI_ENTRY_WEIGHT`=1.0, mitigated downweighted, invalidated dropped — order
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

All six are re-exported from `liquidity_hunter.psychology`.

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
  `poi_zones` (`list[POIZone]`), `poi_sweep_events` (`list[RTOSweepEvent]`),
  `manipulation_cycles` (`list[ManipulationCycle]`),
  `behavior_divergences` (`list[BehaviorDivergence]`),
  `liquidity_heatmap` (`LiquidityHeatmap | None`),
  `liquidation_map` (`LeverageLiquidationMap | None`), and
  `narrative` (`MarketNarrative | None`) for one symbol/timeframe.
- **`load_dashboard_data(provider=..., symbol=..., timeframe=..., limit=..., swing_lookback=..., internal_swing_lookback=..., confluence_filter=True)`**
  — fetches candles, runs all liquidity detectors, scores the zones via
  `LiquidityScoringEngine`, runs `SwingStructureDetector(swing_lookback=...,
  confluence_filter=...)` on `candles` to populate `market_structure_events`,
  fetches a buffered candle series (`internal_candles`), runs
  `InternalStructureDetector(swing_lookback=internal_swing_lookback,
  confluence_filter=...)` (default `internal_swing_lookback =
  DEFAULT_INTERNAL_SWING_LOOKBACK = 2`) **on `internal_candles`**, and reuses
  the result (`all_internal_events`) for both `internal_structure_events`
  (filtered to the visible window) and `POIDetector().detect(internal_candles,
  all_internal_events)` — so CHoCH anchors from the buffer can produce POI
  zones visible in the display window. `confluence_filter` is exposed for tests
  that exercise state-machine logic without needing emission-quality filters.
  `higher_timeframe_direction` is the `direction` of the most recent
  `MarketStructure` event in `market_structure_events`
  (`_latest_structure_direction`), or `NEUTRAL` if none detected yet.

  `internal_candles` is fetched with an extra
  `_INTERNAL_STRUCTURE_BOOTSTRAP_BUFFER = 300` candles of history prepended
  beyond `limit` (`buffered_limit = min(limit + _INTERNAL_STRUCTURE_BOOTSTRAP_BUFFER,
  _MAX_FETCH_LIMIT)`). Running detectors on this larger buffered series lets
  the `trend`/`active_<side>`/`validated_choch_<side>` bootstrap stabilize
  before the visible window, avoiding per-refresh flip-flopping. Both
  `internal_structure_events` and `poi_zones`/`poi_sweep_events` are filtered
  to the calendar range `[candles[0].timestamp, candles[-1].timestamp]` after
  detection. `candles` (main series, its `limit`) is unaffected.

  After all detectors run, `ManipulationCycleDetector().detect(candles,
  all_structure, liquidity_zones, poi_sweep_events, volume_delta_series(candles))`
  populates `manipulation_cycles`.

  `BehaviorDivergenceAnalyzer().analyze(candles, vd, liquidity_zones,
  all_structure)` populates `behavior_divergences`.

  `LiquidityHeatmapEngine().build(...)` populates `liquidity_heatmap`. A
  separate `futures_provider` arg (`FuturesDataProvider | None`, defaults to
  `BinanceFuturesDataProvider()`) fetches open interest / funding /
  long-short ratio, fed to `LeverageLiquidationEstimator().estimate(...)` to
  populate `liquidation_map`. The futures fetch is wrapped in try/except
  `DataProviderError` (`_build_liquidation_map`): a symbol with no perpetual
  contract, or an unreachable venue, degrades to `liquidation_map=None`
  rather than failing the whole snapshot. Tests must inject a fake
  `futures_provider` to avoid network.

  Finally, `NarrativeEngine().build(data)` synthesizes all outputs into a
  `MarketNarrative` (timeline, anomalies, phase-dependent summary,
  confluence count). The engine runs last via `dataclasses.replace` since
  it depends on the fully assembled `DashboardData`.

- **`app/narrative.py`** — `NarrativeEngine`: composition-level synthesizer
  that builds a `MarketNarrative` from a completed `DashboardData`. Lives in
  `app/` (not `psychology/`) because it depends on outputs from every layer.
  `build(data) -> MarketNarrative` produces:
  - **Timeline**: chronological `list[NarrativeEvent]` mapped from structure
    events (major + internal BOS/CHoCH/SWEEP), manipulation cycle phases
    (consolidation/sweep/expansion), behavior divergences, and POI sweep
    events. Deduplicated by `(timestamp, event_type)`, keeping the
    higher-priority source (`manipulation_cycle` > `poi` >
    `behavior_divergence` > `market_structure`).
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

`DashboardData`, `NarrativeEngine`, and `ScoredLiquidityZone` are re-exported
from `liquidity_hunter.app` for use by `dashboard`.

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
  `symbol`, `timeframe`, `limit`, `swing_lookback`, `internal_swing_lookback`,
  defaults matching `load_dashboard_data`) calls `load_dashboard_data`
  directly (no duplicated logic) and returns a `DashboardDataResponse`.
  Results are
  cached per parameter combination via `api/cache.TTLCache`, with a 10s TTL
  (shorter than `cache.DEFAULT_TTL_SECONDS = 300`, since the frontend polls
  this endpoint to keep the dashboard near-live) to avoid redundant Binance
  requests.
- **`api/cache.py`** — `TTLCache`, a minimal generic in-memory
  time-based cache (`get_or_set(key, factory)`).
- **`api/schemas.py`** — `DashboardDataResponse`, a Pydantic `BaseModel`
  (`from_attributes=True`) mirroring the `DashboardData` dataclass fields,
  used to serialize it to JSON; nested domain types (`Candle`,
  `LiquidityZone`, `MarketStructure`, `ScoredLiquidityZone`,
  `RetailBiasEstimate`, `POIZone`, `RTOSweepEvent`, `ManipulationCycle`) are
  already `DomainModel`s and serialize as-is. `poi_zones`,
  `poi_sweep_events`, `manipulation_cycles`, `behavior_divergences`,
  `liquidity_heatmap`, `liquidation_map`, and `narrative` fields are included.

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
  BOS/CHoCH/SWEEP horizontal lines and labels, renders POI order block boxes
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

  **BOS/CHoCH line rendering**: each event draws a horizontal line from its
  `timestamp` to the next event that terminates it. BOS lines end at the next
  opposite-direction CHoCH (so a reversal clears stale BOS references rather
  than letting them run to the chart edge). CHoCH lines start at
  `reference_timestamp` (the timestamp of the `validated_choch_<side>` pivot —
  the origin LH/HL that was promoted) and extend until the next
  opposite-direction CHoCH. CHoCH lines are drawn at `reference_price_level`
  (the validated swing level), not `price_level` (the breaking pivot's
  extreme), since the extreme can be far beyond the confirmed reference level.
  SWEEP lines are drawn at `reference_price_level` like BOS.

- **`frontend/src/components/ManipulationCyclesPanel.tsx`** —
  `ManipulationCyclesPanel` sidebar component: renders manipulation cycle
  cards sorted by relevance (in-progress first, then confirmed, then failed),
  limited to `MAX_DISPLAY = 5`. Each card shows direction arrow, phase badge
  (`ACC`/`MANIP`/`EXP`), status indicator (`LIVE` with pulse animation,
  `CONFIRMED`, `FAILED`), target zone, consolidation candle count, sweep
  info, expansion BOS info, and volume delta. Includes a `CHART ON`/`OFF`
  toggle button that controls the `showManipulationBoxes` prop on `MainChart`.

- **`frontend/src/charting/POIBoxesPrimitive.ts`** — `POIBoxesPrimitive`
  implements `ISeriesPrimitive` and draws filled canvas rectangles for each
  POI zone. Colors: light blue (`#64b5f6` / `#2979ff`) for bullish demand
  zones, red (`#ef5350`) for supply zones. Box border: 1.5px. Active fill
  opacity: ~18% (`#2979ff2e`). The right edge of each box extends to the
  timestamp of the first internal BOS in the same direction after zone
  creation; if no BOS has fired yet, a far-future sentinel timestamp is used
  so `timeToCoordinate` returns `null` and the right edge is clamped to
  `mediaSize.width` (full pane width). Mitigated zones keep their directional
  color at lower opacity (border: 67%, fill: 9%) so direction remains readable.
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
  schema; includes `POIZone`, `RTOSweepEvent`, `MarketStructure` (with
  `reference_timestamp`), `ManipulationCycle`, `ManipulationPhase`,
  `ManipulationCycleStatus`, `BehaviorDivergence`, `DivergenceType`,
  `LiquidityHeatmap`, `HeatmapBucket`, `LeverageLiquidationMap`,
  `LiquidationBand`, `MarketNarrative`, `NarrativeEvent`, `NarrativeAnomaly`,
  `NarrativeEventType`, `AnomalySeverity`.
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

**BOS confirmation** (both detectors): the state machine advances on any wick
break; a `BREAK_OF_STRUCTURE` event is only *emitted* when a candle in the
leg also *closes* beyond the reference (`find_close_break_index`), and
optionally passes the `bos_confluence` shadow-balance filter. SWEEP and CHoCH
detection is unaffected by the close requirement.

**CHoCH confirmation** (both detectors): persistence-based. A candidate LH/HL
pivot is promoted to `validated_choch_<side>` only when a subsequent BOS also
beats `candidate_choch_<side>_baseline` (the opposite trailing reference at
the moment the candidate formed). A bullish CHoCH fires on a sustained break
above `validated_choch_high`; any break that doesn't clear the validated
reference, or doesn't hold, is a `LIQUIDITY_SWEEP`. The moment a CHoCH fires,
the opposite side's validated/candidate/baseline state is reset to `None`. A
`choch_origin_<side>` mechanism prevents the blind spot: the CHoCH check uses
`validated_choch_<side> or choch_origin_<side>`, so the origin serves as
fallback when validated has not been rebuilt yet. `InternalStructureDetector`
uses **one-shot** origin (only a *validated* CHoCH sets the opposite origin;
an origin-triggered CHoCH does not, breaking ping-pong chains — acceptable
because the short blind spot closes quickly with frequent pivots).
`SwingStructureDetector` **always sets** origin (every CHoCH, validated or
origin-triggered, sets `choch_origin_<opposite> = active_<side>`): with
`persistence_candles=10` the ping-pong risk is negligible, while the higher
lookback makes the blind-spot window long enough that one-shot would
re-introduce the stuck-trend bug.

**Phantom candidate invalidation**: if a `candidate_choch_<side>` is swept by
price before promotion, it is replaced by the sweep pivot (with a fresh
baseline) so the subsequent BOS promotes the actual structural extreme, not a
violated phantom level.

**`MarketStructure.reference_timestamp`**: CHoCH events carry the timestamp of
the `validated_choch_<side>` pivot (the promoted LH/HL), allowing the frontend
to anchor CHoCH lines at their true origin rather than at the break candle.

**POI (Order Block) module**: `POIDetector` is implemented and wired into
`load_dashboard_data`. Zones are anchored to the CHoCH → first-same-direction-BOS
window, built from the extreme candle in that window, with frozen boundaries.
The lifecycle (ACTIVE → MITIGATED via RTO, ACTIVE → INVALIDATED via persistence
closes) is fully implemented. The React frontend renders POI zones via
`POIBoxesPrimitive` canvas primitives.

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

**Not yet implemented**:
- Wiring `LIQUIDITY_SWEEP` events to `LiquidityZone.is_mitigated` /
  `invalidated_at` for the swept zone.
- React frontend narrative sidebar panel (`NarrativePanel`).
- React frontend behavior divergence sidebar panel and chart overlay.
- React frontend liquidity targets, retail trap, and market structure
  sidebar panels.
