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
structure (BOS) and change of character (CHoCH) events on the major
(swing) structure. A pivot that breaks the active level is confirmed as
BOS/CHoCH only if its candle's close is also beyond that level and its
volume delta ratio is at least `min_volume_delta_ratio` in the breakout
direction; otherwise it's reported as a `LIQUIDITY_SWEEP`:

```python
from liquidity_hunter.liquidity import SwingStructureDetector

events = SwingStructureDetector(swing_lookback=50, min_volume_delta_ratio=0.2).detect(candles)
for event in events:
    print(event.timestamp, event.event, event.direction, event.price_level)
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

A Streamlit dashboard renders live BTCUSDT research data with Plotly
charts, split into five modular sections: market structure (trend,
candlestick chart, and BOS/CHoCH events), retail bias, detected liquidity
zones, liquidity ranking, and retail trap score:

```bash
poetry run streamlit run liquidity_hunter/dashboard/app.py
```

## Development

```bash
# Run all tests
poetry run pytest

# Lint
poetry run ruff check .

# Type-check (strict mode)
poetry run mypy liquidity_hunter
```
