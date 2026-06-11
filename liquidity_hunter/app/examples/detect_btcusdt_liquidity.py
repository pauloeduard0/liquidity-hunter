"""Example: detect liquidity zones in BTCUSDT 1h candles.

Run with:

    poetry run python -m liquidity_hunter.app.examples.detect_btcusdt_liquidity
"""

import logging

from liquidity_hunter.core.domain import Candle, LiquidityZone, TimeFrame
from liquidity_hunter.data import BinanceDataProvider, OHLCVProvider
from liquidity_hunter.liquidity import (
    EqualHighDetector,
    EqualLowDetector,
    SwingHighDetector,
    SwingLowDetector,
)

logger = logging.getLogger(__name__)

SYMBOL = "BTCUSDT"
TIMEFRAME = TimeFrame.H1
LIMIT = 500


def detect_zones(candles: list[Candle]) -> dict[str, list[LiquidityZone]]:
    """Run all liquidity detectors over `candles`."""
    return {
        "Swing Highs": SwingHighDetector().detect(candles),
        "Swing Lows": SwingLowDetector().detect(candles),
        "Equal Highs": EqualHighDetector().detect(candles),
        "Equal Lows": EqualLowDetector().detect(candles),
    }


def main(provider: OHLCVProvider | None = None) -> dict[str, list[LiquidityZone]]:
    """Fetch BTCUSDT 1h candles, detect liquidity zones, and print a summary."""
    provider = provider if provider is not None else BinanceDataProvider()

    candles = provider.get_ohlcv(SYMBOL, TIMEFRAME, LIMIT)
    logger.info("Fetched %d candle(s) for %s %s", len(candles), SYMBOL, TIMEFRAME.value)

    zones_by_type = detect_zones(candles)
    for name, zones in zones_by_type.items():
        print(f"{name}: {len(zones)} zone(s)")
        for zone in zones[-5:]:
            print(
                f"  price={zone.price_low:.2f}-{zone.price_high:.2f} "
                f"strength={zone.strength:.2f} formed_at={zone.formed_at}"
            )

    return zones_by_type


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
