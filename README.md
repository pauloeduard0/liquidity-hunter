# liquidity-hunter

A research platform for market liquidity detection and market psychology
analysis. This project is **not** a trading system — it produces no
buy/sell signals and contains no order execution or strategy logic.

## Architecture

The codebase follows a clean architecture, with dependencies flowing inward
toward a framework-agnostic domain core:

| Layer        | Responsibility                                                              |
|--------------|------------------------------------------------------------------------------|
| `core`       | Domain entities (`Candle`, `LiquidityZone`, `MarketStructure`, `RetailBias`) and shared enums |
| `data`       | Market data acquisition, repositories, persistence adapters                 |
| `indicators` | Stateless derived series computed from `Candle` data                        |
| `liquidity`  | Detection/modeling of `LiquidityZone` and `MarketStructure`                  |
| `psychology` | Modeling of `RetailBias` from sentiment/positioning data                     |
| `scoring`    | Composite, descriptive scoring combining `liquidity` and `psychology` output |
| `app`        | Composition root, orchestration, and runnable examples                       |
| `dashboard`  | Presentation/visualization of `app` output                                   |
| `config`     | Application settings (environment-driven, via `pydantic-settings`)          |

See `liquidity_hunter/docs/architecture.md` for the full rationale.

## Setup

Requires Python 3.12 and [Poetry](https://python-poetry.org/).

```bash
poetry install
```

## Usage

### Fetching market data

`BinanceDataProvider` fetches OHLCV candles from Binance via
[CCXT](https://github.com/ccxt/ccxt) and returns them as `Candle` domain
objects, with retries and logging built in:

```python
from liquidity_hunter.core.domain import TimeFrame
from liquidity_hunter.data import BinanceDataProvider

provider = BinanceDataProvider()
candles = provider.get_ohlcv("BTCUSDT", TimeFrame.H1, limit=500)
```

Run the example script, which fetches 500 BTCUSDT 1h candles and prints the
first five:

```bash
poetry run python -m liquidity_hunter.app.examples.fetch_btcusdt_1h
```

### Computing volume delta

`volume_delta` derives net taker buy/sell aggression for a candle from
`Candle.taker_buy_volume` (`2 * taker_buy_volume - volume`); `volume_delta_series`
applies it across a series, 1:1 aligned with `candles`:

```python
from liquidity_hunter.indicators import volume_delta_series

deltas = volume_delta_series(candles)
```

### Detecting liquidity zones

Swing-point and equal-level detectors take a list of `Candle` objects and
return `LiquidityZone` objects (type, price range, strength, timeframe):

```python
from liquidity_hunter.liquidity import (
    EqualHighDetector,
    EqualLowDetector,
    SwingHighDetector,
    SwingLowDetector,
)

swing_highs = SwingHighDetector(lookback=2).detect(candles)
equal_highs = EqualHighDetector(tolerance_pct=0.0005, min_touches=2).detect(candles)
```

Run the example script, which fetches 500 BTCUSDT 1h candles and prints the
detected swing highs/lows and equal highs/lows:

```bash
poetry run python -m liquidity_hunter.app.examples.detect_btcusdt_liquidity
```

### Detecting market structure

`SwingStructureDetector` walks swing highs/lows and reports break of
structure (BOS) and change of character (CHoCH) events. A pivot that
breaks the active level is confirmed as BOS/CHoCH only if its candle's
close is also beyond that level and its volume delta ratio is at least
`min_volume_delta_ratio` in the breakout direction; otherwise it's
reported as a `LIQUIDITY_SWEEP`. Every event is stamped with a `scope`
(`StructureScope.MAJOR` by default), so the same detector can be run again
with a smaller `swing_lookback` and `scope=StructureScope.INTERNAL` to
surface finer-grained ("internal") structure on the same candles:

```python
from liquidity_hunter.core.domain import StructureScope
from liquidity_hunter.liquidity import SwingStructureDetector

events = SwingStructureDetector(swing_lookback=50, min_volume_delta_ratio=0.2).detect(candles)
internal_events = SwingStructureDetector(
    swing_lookback=10, scope=StructureScope.INTERNAL
).detect(candles)
for event in events:
    print(event.timestamp, event.event, event.direction, event.price_level, event.scope)
```

### Scoring liquidity zones

`LiquidityScoringEngine` ranks detected zones as liquidity targets, scoring
each on distance from the current price, number of touches, and timeframe
weight (see `liquidity_hunter/docs/scoring.md` for the full methodology):

```python
from liquidity_hunter.scoring import LiquidityScoringEngine

ranked = LiquidityScoringEngine().score(zones, current_price=candles[-1].close)
for scored in ranked[:5]:
    print(scored.zone.zone_type, scored.score)
```

Run the example script, which fetches 500 BTCUSDT 1h candles, detects
liquidity zones, and prints them ranked by score:

```bash
poetry run python -m liquidity_hunter.app.examples.score_btcusdt_liquidity
```

### Estimating retail crowd psychology

`RetailTrapAnalyzer` estimates what retail traders are likely thinking and
doing — not a price prediction or a trading signal — from the higher
timeframe trend, recent market structure, and nearby liquidity zones (see
`liquidity_hunter/docs/psychology.md` for the full methodology):

```python
from liquidity_hunter.psychology import RetailTrapAnalyzer

estimate = RetailTrapAnalyzer().analyze(
    symbol="BTCUSDT",
    higher_timeframe_direction=higher_timeframe_direction,
    market_structure_events=market_structure_events,
    liquidity_zones=liquidity_zones,
    current_price=current_price,
)
print(estimate.dominant_side, estimate.confidence, estimate.explanation)
```

Run the example script, which estimates retail bias for an illustrative
"higher timeframe bearish, lower timeframe bullish change of character"
scenario:

```bash
poetry run python -m liquidity_hunter.app.examples.estimate_btcusdt_retail_bias
```

### Running the dashboard

A Streamlit dashboard renders live BTCUSDT research data as a dark,
multi-column research terminal: a top KPI row (price, retail bias,
dominant liquidity level, trend), a main candlestick chart annotated with
liquidity zones and BOS/CHoCH/liquidity-sweep markers, a right sidebar
(liquidity targets, retail trap analysis, market structure), and bottom
tabs for detected zones, recent structure events, and summary statistics:

```bash
poetry run streamlit run liquidity_hunter/dashboard/app.py
```

### Running the API

A FastAPI application (`liquidity_hunter.api`) exposes the same research
data as JSON, reusing `app.load_dashboard_data` directly:

```bash
poetry run uvicorn liquidity_hunter.api.main:app --reload
```

#### Endpoints

- `GET /api/health` — liveness check, returns `{"status": "ok"}`.
- `GET /api/dashboard` — a `DashboardData` snapshot (candles, liquidity
  zones, ranked zones, major and internal market structure events, retail
  bias estimate) as JSON. Query parameters:

  | Parameter                 | Type     | Default   | Notes                                  |
  |---------------------------|----------|-----------|-----------------------------------------|
  | `symbol`                  | string   | `BTCUSDT` |                                          |
  | `timeframe`               | string   | `1h`      | One of the `TimeFrame` values (e.g. `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1d`, `1w`) |
  | `limit`                   | integer  | `500`     | Number of candles, `1`-`1000`           |
  | `swing_lookback`          | integer  | `50`      | Passed to `SwingStructureDetector` for `market_structure_events`, must be `> 0` |
  | `internal_swing_lookback` | integer  | `10`      | Passed to `SwingStructureDetector` for `internal_structure_events`, must be `> 0` |

  Responses are cached in-memory per parameter combination for 10 seconds
  to avoid redundant Binance requests while still keeping the dashboard
  near-live.

  ```bash
  curl "http://127.0.0.1:8000/api/dashboard?symbol=BTCUSDT&timeframe=1h&limit=500&swing_lookback=50"
  ```

### Running the React frontend

A React + TypeScript frontend (`frontend/`), built with Vite and styled
with Tailwind CSS, consumes `GET /api/dashboard` and renders the same dark,
institutional theme as the Streamlit dashboard. The current scope covers
the top KPI row (price, retail bias, dominant liquidity, trend) and the
main candlestick chart (top-ranked liquidity zones and BOS/CHoCH/
liquidity-sweep markers) using
[Lightweight Charts](https://tradingview.github.io/lightweight-charts/).
Other panels (sidebar, bottom tabs) remain Streamlit-only for now.

With the FastAPI app running (see above), in a separate terminal:

```bash
cd frontend
npm install
npm run dev
```

The dev server proxies `/api/*` requests to `http://127.0.0.1:8000` (see
`frontend/vite.config.ts`), so the FastAPI app must be running for the
dashboard to load data.

## Development

```bash
# Run all tests
poetry run pytest

# Lint
poetry run ruff check .

# Type-check (strict mode)
poetry run mypy liquidity_hunter
```

### Frontend

```bash
cd frontend

# Type-check
npx tsc -b

# Lint
npm run lint

# Build for production
npm run build
```
