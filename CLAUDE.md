# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project purpose and constraints

`liquidity_hunter` is a **research platform** for market liquidity detection
and market psychology analysis. Decision layers — readings that assess whether a
move is likely to be sustained, and that a person could act on — are **allowed**
(relaxed 2026-08-20, previously forbidden outright), under these conditions:

- **A decision layer ships only with the measurement that earned it.** No
  reading gets an edge claim it has not demonstrated against a control matched
  on symbol, timeframe **and direction**. Without direction matching, any
  period that trended makes everything look predictive.
- **Report negatives as findings, not failures.** The measurement record here
  is mostly negative (`research/raid_reversal.py`, the exhaustion-flush study,
  `research/control_continuation.py`), and each of those saved a layer from
  being built on nothing. A rejected hypothesis is a result — record it.
- Prefer **scale-free** metrics (direction hit rate, MFE/MAE ratio) over mean
  return. Both tails widening is volatility, not edge, and mean return cannot
  tell the two apart.

Still out of scope: order execution and position management. The project
observes and assesses; it does not place or manage orders.

Domain entities remain descriptions of *observations* about a market (price
action, liquidity zones, structure, retail sentiment). An assessment layer is
built **on top of** them, and is named for what it measures rather than for an
action to take.

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

### Layer reference docs

Each layer's full reference — every class, field, wired parameter and the
measurement behind it — lives in `liquidity_hunter/docs/`. **Read the doc for
the layer you are working in before changing it.** This file keeps only the
map; the docs keep the detail.

| Layer / area | Doc | What it covers |
|---|---|---|
| `core/domain` | `docs/domain_entities.md` | Every `DomainModel` entity and its fields (`Candle`, `LiquidityZone`, `MarketStructure` incl. `provisional`/`reference_structural`, `POIZone`, `ConsolidationRange`, `ManipulationCycle`, `BehaviorDivergence`, `VolumeProfile`, `VWAPSeries`, futures/liquidation/OI/hunt/narrative/overview models) and the shared enums |
| `data` | `docs/data_layer.md` | `OHLCVProvider`/`FuturesDataProvider` ports, the Binance spot/futures and GeckoTerminal providers, routing + fallback + `CachingOHLCVProvider`, `SQLiteCandleStore`, `series_key`, rate-limit/cache mechanics |
| `indicators` | `docs/indicators_layer.md` | `volume_delta`/CVD, `supertrend`, `volume_profile`, `vwap` |
| `liquidity` | `docs/liquidity_layer.md` | Swing/equal-level detectors, `SwingStructureDetector`, `InternalStructureDetector` (BOS staircase, CHoCH promotion, `CHOCH_FAILED`), `POIDetector`, consolidation detection, `_common` helpers |
| `psychology` | `docs/psychology_layer.md` | `RetailTrapAnalyzer`, `ManipulationCycleDetector`, `BehaviorDivergenceAnalyzer`, `LeverageLiquidationEstimator`, `OIRegimeAnalyzer`, `SupertrendBreakAnalyzer`, `MarketControlAnalyzer` |
| `app` composition root | `docs/composition_root.md` | `DashboardData`, `load_dashboard_data` (buffered fetch, structural anchor, every composition pass and production flag), `NarrativeEngine`, `LiquidityHuntEngine`, `app/overview.py` |
| `frontend/` | `docs/frontend.md` | `MainChart` panes and overlays, structure line rendering rules, POI/consolidation/hunt primitives, volume profile & VWAP drawing, Tide ribbon, KPI cards, `chartTime`/`format` utilities, dashboard types |
| Structure detector changelog | `docs/structure_decisions.md` | Every detector design decision, the measurement behind it, rejected alternatives, regression fixtures |
| Status & roadmap | `docs/project_status.md` | What is implemented, the brief state of the structure pipeline, what is not yet implemented |

Other references: `docs/architecture.md` (SOLID rationale),
`docs/estrutura_bos_choch.md` (Portuguese BOS/CHoCH walkthrough),
`docs/psychology.md` (bias formula), `docs/scoring.md`,
`docs/volume_profile.md`, `docs/volume_e_confluencia.md`,
`docs/block_reclaim.md`.

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
- **`api/anchors.py`** — `AnchorStore` (module-level `anchor_store`), the
  **only stateful piece of the structure pipeline**, deliberately in the
  presentation layer. It remembers the structural anchor last used per
  `(symbol, timeframe)` (1h TTL, 512-pair cap, thread-safe) and both routes
  feed it back as `anchor_hint`, so `_structural_anchor_index` holds its
  anchor while that candle stays in the region instead of letting a fresh
  extreme steal it. Without this, 36.8% of refreshes rewrote non-provisional
  events more than 100 candles behind the live edge — repainting settled
  history, which the `provisional` marks exist to confine to the live edge;
  with it, 15.4% (measured, `research/atr_window_stability.py`). The state is
  here and never in `app/`: passing no hint reproduces the stateless pipeline
  byte for byte, which is what keeps replays, fixtures and `research/`
  reproducible. Full measurement in `docs/structure_decisions.md`.
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
  `liquidity_hunt`, `higher_timeframe`, `volume_profile`, `vwap`,
  `anchored_vwaps`, and `consolidation_ranges` fields are included.

Tested with FastAPI's `TestClient` in `liquidity_hunter/tests/api/test_main.py`.


## Project status

Core domain, data, indicators, liquidity detectors, scoring, psychology,
FastAPI API, and React frontend (main chart + sidebar) are all implemented.
**The detailed state — the unified structure-detector architecture, every
production flag wired in `load_dashboard_data`, the consolidation phases, and
the "not yet implemented" list — lives in `docs/project_status.md`, and the
full decision changelog in `docs/structure_decisions.md`.** Read both before
touching the `InternalStructureDetector` / `SwingStructureDetector` pipeline.

## Keeping this file small

`CLAUDE.md` is loaded into context on every session and has a size limit.
When a layer's detail grows, put it in that layer's `docs/` file and leave a
row in the table above — do not grow this file.
