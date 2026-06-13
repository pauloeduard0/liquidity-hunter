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

dashboard, api ── both depend on app, core (alternative presentation layers)
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
| `dashboard`  | Presentation/visualization of `app` output (Streamlit)                      | `app`, `core`                       |
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
- **`MarketStructure`** — a discrete structural observation (break of
  structure, change of character, HH/HL/LH/LL) with a `MarketDirection`.
- **`RetailBias`** — a measurement of retail sentiment/positioning from a
  given `BiasSource`, with a bounded `sentiment_score` and `confidence`.

Shared enums (`TimeFrame`, `MarketDirection`, `LiquiditySide`,
`LiquidityZoneType`, `StructureEvent`, `BiasSource`, `RetailPositioning`)
live in `core/domain/enums.py`. Extend behavior by adding enum members
rather than branching logic elsewhere (Open/Closed principle). `TimeFrame`
also has `finer()` (the next shorter-duration `TimeFrame`, or `None` for
`M1`), used by `app.dashboard_data.load_dashboard_data` to fetch the candle
series that `InternalStructureDetector` runs on (see the `liquidity` layer
below).

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
  references, `pending_high`/`pending_low` candidates, and the current
  `trend` (`MarketDirection`, starting `NEUTRAL`). `pending_high`/
  `pending_low` accumulate the *most extreme* pivot of their kind (highest
  high / lowest low) seen since the *opposite* active level was last set,
  not merely the most recent one — so when a pending pivot is promoted to
  active (once the *opposite* active level breaks), it represents the true
  extreme of the leg that just ended, the natural reference for the next
  reversal in the other direction. This avoids flagging a CHoCH against a
  minor retracement pivot. Structure (price action) and confirmation
  (volume) are kept separate: a pivot that breaks the active level on its
  side *in the direction of `trend`* (including the first break while
  `trend` is still `NEUTRAL`) is reported as a `BREAK_OF_STRUCTURE` on price
  alone — a wick beyond the active level is enough, regardless of the
  candle's `close` or `volume_delta` (see `indicators.volume_delta`). A
  pivot that breaks the active level *against* `trend` is only confirmed as
  a `CHANGE_OF_CHARACTER` if the candle's `close` is also beyond that level
  AND its `volume_delta` ratio (`abs(volume_delta) / volume`) is at least
  the constructor's `min_volume_delta_ratio` (default `0.2`) in the breakout
  direction. Otherwise the active level is left unchanged and a
  `StructureEvent.LIQUIDITY_SWEEP` is reported instead (`price_level` the
  sweeping pivot, `reference_price_level` the swept active level); the swept
  pivot is folded into `pending_high`/`pending_low` (per the accumulation
  rule above), so it can still be promoted to active later. When a BOS or
  CHoCH *does* update the active level on its side, the new value is
  `_extreme(pending_high/low, breaking_pivot)` — the more extreme of the
  breaking pivot and that side's own pending accumulation — so an earlier
  same-side `LIQUIDITY_SWEEP` that reached further than the pivot confirming
  the break still ends up as the active level, preserving the true extreme
  of the leg that just ended. A BOS/CHoCH on one side also retires the
  *opposite* side's active level (it belonged to the leg that just ended):
  it is replaced by that side's `pending_high`/`pending_low` (the extreme
  pivot accumulated during the leg), promoted to active — or, if nothing has
  accumulated yet, discarded to `None` rather than left stale. While an
  active level is `None`, pivots on that side cannot trigger a BOS/CHoCH;
  they are purely descriptive HH/HL/LH/LL labels that accumulate into
  pending, until the next opposite-side BOS/CHoCH promotes that accumulation
  to active. The very first pivot of each kind (the bootstrap) is also seeded
  into the opposite side's pending candidate if that side has already been
  bootstrapped, since it chronologically falls within that side's
  active-creation window. Pivots that
  don't break the active level are labeled HH/LH (highs) or HL/LL (lows) by
  comparison with the previous pivot of the same type — a confirmed or swept
  pivot is reported only as BOS/CHoCH/`LIQUIDITY_SWEEP` (no redundant
  label). Every emitted `MarketStructure` has `scope = StructureScope.MAJOR`
  (the field's default).
- **`liquidity/detectors/internal_structure.py`** — `InternalStructureDetector`:
  detects BOS/CHoCH/`LIQUIDITY_SWEEP`/HL/LH on finer-grained, internal/minor
  structure, with `scope = StructureScope.INTERNAL` stamped on every emitted
  `MarketStructure` (see `app.dashboard_data.load_dashboard_data`, which runs
  it on the candle series one `TimeFrame` finer than `market_structure_events`'
  series, e.g. M30 pivots alongside H1 major structure). Like
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

  Confirmation of a counter-trend break is **persistence**-based, not
  volume-based: a single high-volume candle that pokes through a level and
  immediately reverts is a "false break", whereas a break that price *holds*
  beyond for a few candles is "real" — see `_common.is_sustained_break`. The
  constructor's `persistence_candles` (default `3`) is the number of candles
  immediately following the breaking pivot's candle that must *also* close
  beyond the reference, in addition to the pivot's own candle, for the break
  to be confirmed as a `CHANGE_OF_CHARACTER`; if the window reverts (or there
  aren't yet enough trailing candles to evaluate it), the pivot is reported
  as a `LIQUIDITY_SWEEP` instead. This replaces the previous
  `volume_delta`/volume-spike confirmation for `InternalStructureDetector`
  entirely — `SwingStructureDetector`'s `volume_delta`-ratio confirmation
  (`min_volume_delta_ratio`) is unaffected and unchanged.

  A confirmed `CHANGE_OF_CHARACTER` additionally requires the pivot to clear
  `choch_candidate_high`/`choch_candidate_low` (mirrored per side) — state
  distinct from `active_<side>`/`pending_<side>` that tracks the swing
  high/low which defined the leg leading into the most recent confirmed
  BOS/CHoCH on the *opposite* side, i.e. the level a CHoCH must actually
  break to represent a real reversal. A persistence-confirmed break of
  `active_<side>` alone is not sufficient: `active_<side>` is a *trailing*
  reference, so after a reversal it can be silently re-bootstrapped (no
  event) by a pivot formed *during the pullback that follows* — part of the
  new leg, not the leg being reversed. Treating a break of that
  pullback-formed `active_<side>` as the CHoCH reference would flag an
  internal bounce as a structural reversal. `choch_candidate_<side>`
  survives `active_<side>` resets to `None` and silent re-bootstraps, so it
  remains the correct reversal target. It is set only when a confirmed
  BOS/CHoCH on the *opposite* side performs its `active_<side> =
  pending_<side>; pending_<side> = None` reset: the pre-reset
  `active_<side>` (if not `None`) becomes `choch_candidate_<side>`, since it
  was the extreme of the leg that BOS/CHoCH just ended. A counter-trend
  pivot that passes the persistence check but does not also clear
  `choch_candidate_<side>` (when one has been recorded) is reported as a
  `LIQUIDITY_SWEEP` with `trend` unchanged — an internal bounce within the
  leg `choch_candidate_<side>` still defines, folding the opposite side's
  `active_<side>` into `pending_<side>` as usual. A confirmed
  `CHANGE_OF_CHARACTER`'s `reference_price_level` is `choch_candidate_<side>`
  if one has been recorded, else the more extreme of `active_<side>` and
  `pending_<side>` by price (the pre-`choch_candidate_<side>` fallback); a
  `BREAK_OF_STRUCTURE`'s `reference_price_level` is always `active_<side>`.

  `choch_candidate_<side>` is not frozen at the value set by the BOS/CHoCH
  that created it: a `LOWER_HIGH`/`HIGHER_LOW` pivot *ratchets*
  `choch_candidate_high`/`choch_candidate_low` to itself if `trend` is
  `BEARISH`/`BULLISH` (respectively) AND a `HIGHER_LOW`/`LOWER_HIGH` has been
  confirmed on the *opposite* side since `choch_candidate_high`/
  `choch_candidate_low` was last set. The opposite-side confirmation
  requirement distinguishes a re-bootstrap pullback top/bottom (formed right
  after a reversal, before the opposite side has confirmed anything — must
  NOT become `choch_candidate_<side>`) from a later LH/HL that is itself
  part of the current leg's now-confirmed structure — a closer, more
  relevant CHoCH level than the (possibly much older) one recorded when the
  leg began. Each `HIGHER_LOW`/`LOWER_HIGH` label both runs its own side's
  ratchet check and arms the *opposite* side's ratchet for next time;
  setting `choch_candidate_<side>` at a confirmed BOS/CHoCH disarms it
  again.
- **`liquidity/detectors/_common.py`** — shared `validate_candles`,
  `price_range`, `Pivot`, `collect_pivots`, and `is_sustained_break` helpers
  (the latter used by `InternalStructureDetector` for persistence-based
  confirmation).

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
  `market_structure_events`, `internal_structure_events`, and `retail_bias`
  for one symbol/timeframe.
- **`load_dashboard_data(provider=..., symbol=..., timeframe=..., limit=..., swing_lookback=..., internal_swing_lookback=...)`**
  — fetches candles, runs all liquidity detectors, scores the zones via
  `LiquidityScoringEngine`, runs `SwingStructureDetector(swing_lookback=...)`
  on `candles` to populate `market_structure_events`, fetches a second candle
  series one `TimeFrame` finer (`finer_candles`, via `timeframe.finer()`,
  `None`/skipped if `timeframe` is already `M1`) and runs
  `InternalStructureDetector(swing_lookback=internal_swing_lookback)` (default
  `internal_swing_lookback = DEFAULT_INTERNAL_SWING_LOOKBACK = 10`) **on
  `finer_candles`** (e.g. M30 pivots when `timeframe` is H1) to populate
  `internal_structure_events` — `[]` if `timeframe` is already `M1`, and runs
  `RetailTrapAnalyzer` to produce a `DashboardData`. `higher_timeframe_direction`
  is the `direction` of the most recent `MarketStructure` event in
  `market_structure_events` (`_latest_structure_direction`), or `NEUTRAL` if
  none have been detected yet (e.g. too few candles for `swing_lookback`) —
  `internal_structure_events` does not affect `higher_timeframe_direction`.

`DashboardData` and `ScoredLiquidityZone` are re-exported from
`liquidity_hunter.app` for use by `dashboard`.

### Dashboard layer (`liquidity_hunter/dashboard`)

A modular Streamlit app, depending only on `app` and `core`, styled as a
dark, multi-column "trading intelligence" layout (institutional look and
feel inspired by TradingView/Bloomberg-style terminals):

- **`dashboard/app.py`** — entrypoint; loads a cached `DashboardData` (via
  `liquidity_hunter.app.load_dashboard_data`), injects the custom theme
  (`dashboard.styles`), and assembles the layout: a top KPI row, a main
  area (chart + right sidebar panels), and a bottom tab group. Run with:

  ```bash
  poetry run streamlit run liquidity_hunter/dashboard/app.py
  ```

- **`dashboard/styles.py`** — `inject()` injects custom CSS (card styling,
  spacing, section titles) on top of the dark theme defined in
  `.streamlit/config.toml`.
- **`dashboard/charts.py`** — pure Plotly figure builders (no Streamlit
  dependency), all sharing an institutional dark theme
  (`_apply_dark_theme`): `candlestick_chart`, `liquidity_zones_chart`
  (zone overlays, optionally annotated with `ScoredLiquidityZone` scores
  via `ranked_zones`), `main_chart` (zones + BOS/CHoCH/`LIQUIDITY_SWEEP`
  markers via `_add_structure_events`), `ranking_chart`, `confidence_gauge`.
  `_add_structure_events` renders `StructureScope.MAJOR` events as labeled
  triangle markers and overlays any `StructureScope.INTERNAL` events of the
  same `StructureEvent` type as smaller, textless, semi-transparent markers
  (trace name suffixed `" (Internal)"`).
- **`dashboard/sections/`** — one module per section, each exposing
  `render(data: DashboardData) -> None`:
  - `kpi_row` — top row: price, retail bias, dominant liquidity level, and
    higher timeframe trend.
  - `main_chart` — the primary chart (see `charts.main_chart`), passing the
    concatenation of `market_structure_events` and
    `internal_structure_events`.
  - `liquidity_targets` — right sidebar: top-ranked `ScoredLiquidityZone`s
    (price, type, score, distance %).
  - `retail_trap_panel` — right sidebar: `RetailBiasEstimate` dominant
    side, a descriptive Low/Medium/High "trap risk" label derived from
    `confidence`, and `explanation`.
  - `market_structure_panel` — right sidebar: trend for the dashboard's
    loaded timeframe, the latest `market_structure_events` entry, and the
    latest `internal_structure_events` entry. Currently single-timeframe;
    a future phase may add a per-timeframe (D1/H4/H1/M15) view.
  - `liquidity_zones_table`, `recent_events`, `statistics` — bottom tabs:
    detected zones table, structure events table (major and internal,
    sorted by timestamp with a "Scope" column), and descriptive summary
    counts.

Tested with `streamlit.testing.v1.AppTest` in
`liquidity_hunter/tests/dashboard/test_app.py`.

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
  `RetailBiasEstimate`) are already `DomainModel`s and serialize as-is.

Tested with FastAPI's `TestClient` in `liquidity_hunter/tests/api/test_main.py`.

## Project status

This is an early-stage scaffold. `core.domain` models, the `data.providers`
(Binance/CCXT) module, `indicators.volume_delta`, the `liquidity.detectors`
(swing/equal-level, swing market structure) module, `scoring.engine`
(`LiquidityScoringEngine`), `psychology.analyzers` (`RetailTrapAnalyzer`),
the `dashboard` Streamlit app, and the `api` FastAPI app are implemented.
A React frontend (`frontend/`) is in progress: the KPI row and main chart
(candlesticks, top-ranked liquidity zones, structure event markers) are
implemented; the sidebar panels and bottom tabs remain Streamlit-only.
`SwingStructureDetector` reports a `BREAK_OF_STRUCTURE` on price alone (a
wick beyond the active level is enough) for any break *in the direction of
the current `trend`* (including the first break while `trend` is still
`NEUTRAL`); `indicators.volume_delta` confirmation ("close beyond level AND
volume delta ratio `>= min_volume_delta_ratio` in the breakout direction")
gates only counter-trend breaks, reported as `CHANGE_OF_CHARACTER` if
confirmed, with a failed confirmation reported as
`StructureEvent.LIQUIDITY_SWEEP` instead. `MarketStructure.scope`
(`StructureScope`: `MAJOR`/`INTERNAL`) distinguishes the swing-structure pass
(`market_structure_events`, on `candles`, `swing_lookback`, also the sole
input to `higher_timeframe_direction`) from a second, finer-grained pass
(`internal_structure_events`, on `finer_candles` — one `TimeFrame` finer than
`candles`, `[]` if `timeframe` is already `M1` — `internal_swing_lookback`,
default `DEFAULT_INTERNAL_SWING_LOOKBACK = 10`) surfacing minor/internal
structure on that finer series (e.g. M30 pivots when `timeframe` is H1).
`InternalStructureDetector`'s `CHANGE_OF_CHARACTER` confirmation is
persistence-based (`_common.is_sustained_break`, `persistence_candles`,
default `3`): the breaking candle and the `persistence_candles` candles
following it must all close beyond the reference, else the pivot is reported
as a `LIQUIDITY_SWEEP` ("false break"). This replaces the previous
`volume_delta`/finer-timeframe-volume-spike confirmation for
`InternalStructureDetector` entirely (`TimeFrame.to_timedelta()` and
`_common.is_confirmed_break`/`has_volume_spike` have been removed);
`SwingStructureDetector`'s `volume_delta`-ratio confirmation is unaffected.
A persistence-confirmed counter-trend break is reported as a confirmed
`CHANGE_OF_CHARACTER` only if it *also* clears `choch_candidate_high`/
`choch_candidate_low` (when one has been recorded) — persistent state, set
when a confirmed BOS/CHoCH on the opposite side retires `active_<side>`,
that survives `active_<side>`'s subsequent reset to `None` and silent
re-bootstrap by a post-reversal pullback pivot, so a CHoCH cannot be flagged
against a pullback-formed level that never defined the leg being reversed.
Otherwise the break is a `LIQUIDITY_SWEEP` with `trend` unchanged. A
confirmed `CHANGE_OF_CHARACTER`'s `reference_price_level` is
`choch_candidate_<side>` if recorded, else `max`/`min` of
`active_<side>`/`pending_<side>` by price.
`choch_candidate_high`/`choch_candidate_low` ratchet toward a later
`LOWER_HIGH`/`HIGHER_LOW` of the same leg once the *opposite* side has
confirmed at least one `HIGHER_LOW`/`LOWER_HIGH` since `choch_candidate_high`/
`choch_candidate_low` was last set — so a CHoCH can fire against the most
recent confirmed LH/HL of the current leg rather than only the (possibly much
older) level recorded when the leg began.
Wiring `LIQUIDITY_SWEEP` events to `LiquidityZone.is_mitigated` /
`invalidated_at` for the swept zone is not yet implemented.
