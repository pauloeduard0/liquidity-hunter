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
```

| Layer        | Responsibility                                                              | May depend on                     |
|--------------|------------------------------------------------------------------------------|------------------------------------|
| `core`       | Framework-agnostic domain entities (`Candle`, `LiquidityZone`, `MarketStructure`, `RetailBias`) and shared enums | nothing |
| `data`       | Market data acquisition, repositories, persistence adapters                 | `core`                              |
| `indicators` | Stateless derived series computed from `Candle` data                        | `core`, `data`                      |
| `liquidity`  | Detection/modeling of `LiquidityZone` and `MarketStructure`                  | `core`, `data`, `indicators`        |
| `psychology` | Modeling of `RetailBias` from sentiment/positioning data                     | `core`, `data`                      |
| `scoring`    | Composite, descriptive scoring combining `liquidity` and `psychology` output | `core`, `liquidity`, `psychology`   |
| `app`        | Composition root and orchestration                                           | all of the above                    |
| `dashboard`  | Presentation/visualization of `app` output                                   | `app`, `core`                       |
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
- **`MarketStructure`** — a discrete structural observation (break of
  structure, change of character, HH/HL/LH/LL) with a `MarketDirection`.
- **`RetailBias`** — a measurement of retail sentiment/positioning from a
  given `BiasSource`, with a bounded `sentiment_score` and `confidence`.

Shared enums (`TimeFrame`, `MarketDirection`, `LiquiditySide`,
`LiquidityZoneType`, `StructureEvent`, `BiasSource`, `RetailPositioning`)
live in `core/domain/enums.py`. Extend behavior by adding enum members
rather than branching logic elsewhere (Open/Closed principle).

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

`BinanceDataProvider` and `OHLCVProvider` are re-exported from
`liquidity_hunter.data` for convenience.

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
  detects BOS/CHoCH and HH/HL/LH/LL on the major (swing) structure. Sources
  swing pivots from `SwingHighDetector`/`SwingLowDetector` (`swing_lookback`)
  and walks them chronologically maintaining `active_high`/`active_low`
  references and `pending_high`/`pending_low` candidates. A pending pivot is
  only promoted to active once the *opposite* active level breaks — this
  avoids flagging a CHoCH against a minor retracement pivot. A pivot whose
  price exceeds the active level on its side is confirmed as a BOS/CHoCH
  only if its candle's `close` is also beyond that level AND its
  `volume_delta` (see `indicators.volume_delta`) ratio
  (`abs(volume_delta) / volume`) is at least the constructor's
  `min_volume_delta_ratio` (default `0.2`) in the breakout direction. If
  either condition fails, the active level is left unchanged and a
  `StructureEvent.LIQUIDITY_SWEEP` is reported instead (`price_level` the
  sweeping pivot, `reference_price_level` the swept active level); the swept
  pivot becomes the new `pending_high`/`pending_low`, so it can still be
  promoted to active later. Pivots that don't break the active level are
  labeled HH/LH (highs) or HL/LL (lows) by comparison with the previous
  pivot of the same type — a confirmed or swept pivot is reported only as
  BOS/CHoCH/`LIQUIDITY_SWEEP` (no redundant label). Internal/minor structure
  detection is not yet implemented.
- **`liquidity/detectors/_common.py`** — shared `validate_candles` and
  `price_range` helpers.

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
documented in `liquidity_hunter/docs/psychology.md`. All three are
re-exported from `liquidity_hunter.psychology`.

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
  `market_structure_events`, and `retail_bias` for one symbol/timeframe.
- **`load_dashboard_data(provider=..., symbol=..., timeframe=..., limit=..., swing_lookback=...)`**
  — fetches candles, runs all liquidity detectors, scores the zones via
  `LiquidityScoringEngine`, runs `SwingStructureDetector(swing_lookback=...)`
  to populate `market_structure_events`, and runs `RetailTrapAnalyzer` to
  produce a `DashboardData`. `higher_timeframe_direction` is the `direction`
  of the most recent `MarketStructure` event (`_latest_structure_direction`),
  or `NEUTRAL` if none have been detected yet (e.g. too few candles for
  `swing_lookback`).

`DashboardData` and `ScoredLiquidityZone` are re-exported from
`liquidity_hunter.app` for use by `dashboard`.

### Dashboard layer (`liquidity_hunter/dashboard`)

A modular Streamlit app, depending only on `app` and `core`:

- **`dashboard/app.py`** — entrypoint; loads a cached `DashboardData` (via
  `liquidity_hunter.app.load_dashboard_data`) and renders each section in
  order. Run with:

  ```bash
  poetry run streamlit run liquidity_hunter/dashboard/app.py
  ```

- **`dashboard/charts.py`** — pure Plotly figure builders (no Streamlit
  dependency): `candlestick_chart`, `liquidity_zones_chart`,
  `ranking_chart`, `confidence_gauge`.
- **`dashboard/sections/`** — one module per section, each exposing
  `render(data: DashboardData) -> None`:
  1. `market_structure` — higher timeframe trend, candlestick chart, and a
     table of detected BOS/CHoCH events.
  2. `retail_bias` — `dominant_side`, `confidence`, and `explanation` from
     `RetailBiasEstimate`.
  3. `liquidity_zones` — candlestick chart with detected zones overlaid,
     plus a table.
  4. `liquidity_ranking` — bar chart and table of `ScoredLiquidityZone`s.
  5. `retail_trap_score` — gauge chart of `retail_bias.confidence`.

Tested with `streamlit.testing.v1.AppTest` in
`liquidity_hunter/tests/dashboard/test_app.py`.

## Project status

This is an early-stage scaffold. `core.domain` models, the `data.providers`
(Binance/CCXT) module, `indicators.volume_delta`, the `liquidity.detectors`
(swing/equal-level, swing market structure) module, `scoring.engine`
(`LiquidityScoringEngine`), `psychology.analyzers` (`RetailTrapAnalyzer`),
and the `dashboard` Streamlit app are implemented. Internal/minor
`MarketStructure` detection within `liquidity` is not yet implemented.
`SwingStructureDetector`'s BOS/CHoCH confirmation rule now uses
`indicators.volume_delta` ("close beyond level AND volume delta ratio
`>= min_volume_delta_ratio` in the breakout direction"), with a failed
confirmation reported as `StructureEvent.LIQUIDITY_SWEEP` rather than a
break. Wiring `LIQUIDITY_SWEEP` events to `LiquidityZone.is_mitigated` /
`invalidated_at` for the swept zone is not yet implemented.
