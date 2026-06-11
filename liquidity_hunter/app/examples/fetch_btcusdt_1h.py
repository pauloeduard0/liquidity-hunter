"""Example: fetch 500 BTCUSDT 1h candles from Binance and print the first five.

Run with:

    poetry run python -m liquidity_hunter.app.examples.fetch_btcusdt_1h
"""

import logging

from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.data import BinanceDataProvider, OHLCVProvider

logger = logging.getLogger(__name__)

SYMBOL = "BTCUSDT"
TIMEFRAME = TimeFrame.H1
LIMIT = 500


def main(provider: OHLCVProvider | None = None) -> list[Candle]:
    """Fetch candles and print the first five. Returns the full list."""
    provider = provider if provider is not None else BinanceDataProvider()

    candles = provider.get_ohlcv(SYMBOL, TIMEFRAME, LIMIT)
    logger.info("Fetched %d candle(s) for %s %s", len(candles), SYMBOL, TIMEFRAME.value)

    for candle in candles[:5]:
        print(candle)

    return candles


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
