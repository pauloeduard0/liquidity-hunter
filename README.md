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

## Development

```bash
# Run all tests
poetry run pytest

# Lint
poetry run ruff check .

# Type-check (strict mode)
poetry run mypy liquidity_hunter
```
