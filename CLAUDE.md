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

  Confirmation of a counter-trend break is **persistence**-based, not
  volume-based: a single high-volume candle that pokes through a level and
  immediately reverts is a "false break", whereas a break that price *holds*
  beyond for a few candles is "real" — see `_common.is_sustained_break`. The
  constructor's `persistence_candles` (default `3`) is the number of candles
  immediately following a breaking candle that must *also* close beyond the
  reference, in addition to that candle itself, for the break to be confirmed
  as a `CHANGE_OF_CHARACTER`. This check is **not** anchored to the triggering
  pivot's own index: a sustained break is considered confirmed if *any*
  candle from just after the previous pivot of the same kind (exclusive)
  through the triggering pivot (inclusive) — i.e. anywhere in the leg leading
  up to the pivot — starts a window that holds for `persistence_candles`
  beyond it, even if that window extends past the pivot's own index. If no
  such window exists (or there aren't yet enough trailing candles to evaluate
  one), the pivot is reported as a `LIQUIDITY_SWEEP` instead. This replaces
  the previous `volume_delta`/volume-spike confirmation for
  `InternalStructureDetector` entirely — `SwingStructureDetector`'s
  `volume_delta`-ratio confirmation (`min_volume_delta_ratio`) is unaffected
  and unchanged.

  The reversal (`CHANGE_OF_CHARACTER`) reference is tracked explicitly per
  side as `validated_choch_high`/`validated_choch_low`, distinct from the
  trailing `active_<side>` and from `pending_<side>`. Promotion to
  `validated_choch_<side>` is a two-step process via an intermediate
  `candidate_choch_<side>`: `candidate_choch_high` is the most recent
  `LOWER_HIGH`-labeled pivot (or a re-bootstrap pivot that is functionally one
  — see below), not yet promoted. SMC requires `LL1 -> LH1 -> LL2 (confirms
  LH1) -> break LH1` for a bullish CHoCH, so an LH *alone* is not a CHoCH
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
  reset to `None`: the new leg's reversal reference must be rebuilt from a
  fresh LH/HL -> LL/HH confirmation of its own, not seeded from the leg that
  just ended.

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

  The low side mirrors this exactly: `candidate_choch_low` is the most recent
  `HIGHER_LOW`-labeled pivot (or re-bootstrap equivalent), with
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
- **`liquidity/detectors/_common.py`** — shared `validate_candles`,
  `price_range`, `Pivot`, `collect_pivots`, `is_sustained_break`,
  `find_wick_break_index`, and `find_sustained_break_index` helpers (the
  latter three used by `InternalStructureDetector` for persistence-based
  confirmation and break-candle attribution).

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
  on `candles` to populate `market_structure_events`, fetches a second,
  larger candle series of the *same* `timeframe` (`internal_candles`), runs
  `InternalStructureDetector(swing_lookback=internal_swing_lookback)` (default
  `internal_swing_lookback = DEFAULT_INTERNAL_SWING_LOOKBACK = 2`) **on
  `internal_candles`**, and filters the result to populate
  `internal_structure_events` and runs `RetailTrapAnalyzer` to produce a
  `DashboardData`. `higher_timeframe_direction` is the `direction` of the most
  recent `MarketStructure` event in `market_structure_events`
  (`_latest_structure_direction`), or `NEUTRAL` if none have been detected yet
  (e.g. too few candles for `swing_lookback`) — `internal_structure_events`
  does not affect `higher_timeframe_direction`.

  `internal_candles` is fetched with an extra
  `_INTERNAL_STRUCTURE_BOOTSTRAP_BUFFER = 300` candles of history prepended
  beyond `limit` (`buffered_limit = min(limit + _INTERNAL_STRUCTURE_BOOTSTRAP_BUFFER,
  _MAX_FETCH_LIMIT)`). `InternalStructureDetector` is
  stateless, but its `trend`/`active_<side>`/`validated_choch_<side>`
  bootstrap depends on the *first* pivots in whatever series it's given — on
  a fixed-size sliding window re-fetched every refresh, that bootstrap shifts
  by one candle each time, causing the same pivot to flip between
  `BREAK_OF_STRUCTURE`/`CHANGE_OF_CHARACTER`/`LIQUIDITY_SWEEP` across
  refreshes. Running the detector on this larger buffered series instead lets
  the bootstrap stabilize well before the visible window, then
  `internal_structure_events` is filtered down to only events whose
  `timestamp` falls within `[candles[0].timestamp, candles[-1].timestamp]` —
  i.e. the calendar range actually shown on the dashboard. `candles` itself
  (the main-timeframe series and its `limit`) is unaffected by this buffer.

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
(`internal_structure_events`, on the same `candles` series — a larger,
buffered fetch of the same `timeframe`, see `_INTERNAL_STRUCTURE_BOOTSTRAP_BUFFER`
— with `internal_swing_lookback`, default `DEFAULT_INTERNAL_SWING_LOOKBACK = 2`)
surfacing minor/internal structure on that series at a finer pivot
granularity than `swing_lookback`.
`InternalStructureDetector`'s `CHANGE_OF_CHARACTER` confirmation is
persistence-based (`_common.is_sustained_break`, `persistence_candles`,
default `3`): a sustained break is validated if the `persistence_candles`
window holds beyond the reference starting at *any* candle within the leg
leading up to the pivot (since the previous pivot of the same kind), not only
the pivot's own candle — so the window may extend past the pivot's index.
Otherwise the pivot is reported as a `LIQUIDITY_SWEEP` ("false break"). This
replaces the previous
`volume_delta`/finer-timeframe-volume-spike confirmation for
`InternalStructureDetector` entirely (`TimeFrame.to_timedelta()` and
`_common.is_confirmed_break`/`has_volume_spike` have been removed);
`SwingStructureDetector`'s `volume_delta`-ratio confirmation is unaffected.
The `CHANGE_OF_CHARACTER` reference is `validated_choch_high`/
`validated_choch_low`, promoted via an intermediate `candidate_choch_high`/
`candidate_choch_low` — the most recent `LOWER_HIGH`/`HIGHER_LOW` pivot (or a
"worse than the just-retired `active_<side>`" re-bootstrap pivot, judged
against `last_high_pivot`/`last_low_pivot`, which is functionally one even
though no label is emitted). Alongside each candidate,
`candidate_choch_<side>_baseline` snapshots the opposite side's trailing
`active_<opposite>` as it stood when the candidate was set. A candidate is
promoted to `validated_choch_<side>` when a BOS in the candidate's direction
occurs *after* the candidate was set *and* its pivot price surpasses
`candidate_choch_<side>_baseline` — a genuine `LL2 < LL1`/`HH2 > HH1`
*relative to the leg containing the candidate*, not a new absolute extreme of
the whole leg (which deadlocks: the first impulsive BOS after a CHoCH is often
the leg's eventual extreme, permanently starving promotion) and not merely
*any* continuation BOS (which over-ratchets `validated_choch_<side>` toward
weaker, more recent candidates even after the leg's true reversal point is
already confirmed). Both the candidate (and its baseline) and the validated
reference are frozen while no such BOS occurs. A bullish CHoCH fires on a
sustained break *above* `validated_choch_high` (mirror for bearish); a break
of the trailing `active_<side>` that does not also clear the validated
reference (including while it is still `None`), or does not hold, is a
`LIQUIDITY_SWEEP` with `trend` unchanged. The moment a CHoCH fires, the
*opposite* side's `validated_choch_<side>`, `candidate_choch_<side>`, and
`candidate_choch_<side>_baseline` are all reset to `None`. A confirmed
`CHANGE_OF_CHARACTER`'s `reference_price_level` is `validated_choch_<side>`; a
`BREAK_OF_STRUCTURE`'s is the trailing `active_<side>` it broke. This
baseline-gated promotion rule replaced an earlier "any BOS after the
candidate" rule (which over-ratcheted), which in turn replaced a "new absolute
LL/HH of the leg" rule (`last_ll`/`last_hh`, which deadlocked), which in turn
replaced the original `last_high_pivot`/`last_low_pivot`-direct-assignment
rule (itself a replacement for the original `choch_candidate_<side>` + ratchet
+ `hl_since_last_ll`/`lh_since_last_hh` machinery).
For `BREAK_OF_STRUCTURE`, `LIQUIDITY_SWEEP`, and `CHANGE_OF_CHARACTER`, the
emitted `timestamp` is not the triggering pivot's but the actual breaking
candle's — found via `_common.find_wick_break_index` (wick vs.
`active_<side>`, price-only) or `_common.find_sustained_break_index`
(`is_sustained_break` vs. `validated_choch_<side>`), searched within the leg
since the previous pivot of the same kind. This avoids plotting BOS/CHoCH at
the extreme of the new leg (where the confirming pivot forms) instead of the
candle that actually broke the prior level; `price_level` remains the
triggering pivot's own `price` (the true extreme of the move).
Wiring `LIQUIDITY_SWEEP` events to `LiquidityZone.is_mitigated` /
`invalidated_at` for the swept zone is not yet implemented.
