"""Example: score and rank BTCUSDT liquidity zones by relevance.

Run with:

    poetry run python -m liquidity_hunter.app.examples.score_btcusdt_liquidity
"""

import logging

from liquidity_hunter.core.domain import TimeFrame
from liquidity_hunter.data import BinanceDataProvider, OHLCVProvider
from liquidity_hunter.liquidity import (
    EqualHighDetector,
    EqualLowDetector,
    SwingHighDetector,
    SwingLowDetector,
)
from liquidity_hunter.scoring import LiquidityScoringEngine, ScoredLiquidityZone

logger = logging.getLogger(__name__)

SYMBOL = "BTCUSDT"
TIMEFRAME = TimeFrame.H1
LIMIT = 500


def main(provider: OHLCVProvider | None = None) -> list[ScoredLiquidityZone]:
    """Fetch BTCUSDT 1h candles, score detected liquidity zones, and print them ranked."""
    provider = provider if provider is not None else BinanceDataProvider()

    candles = provider.get_ohlcv(SYMBOL, TIMEFRAME, LIMIT)
    logger.info("Fetched %d candle(s) for %s %s", len(candles), SYMBOL, TIMEFRAME.value)

    zones = [
        *SwingHighDetector().detect(candles),
        *SwingLowDetector().detect(candles),
        *EqualHighDetector().detect(candles),
        *EqualLowDetector().detect(candles),
    ]

    current_price = candles[-1].close
    ranked = LiquidityScoringEngine().score(zones, current_price)

    print(f"Current price: {current_price:.2f}")
    for scored in ranked[:10]:
        zone = scored.zone
        label = zone.zone_type.value.replace("_", " ").title()
        print(f"{label}\n  Score: {scored.score:.0f}")

    return ranked


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
