"""Example: estimate retail crowd psychology for an illustrative BTCUSDT scenario.

This example does not fetch live data. `MarketStructure` detection is not
yet implemented (see CLAUDE.md "Project status"), so the inputs below are
constructed directly to illustrate the scenario from the architecture
brief: a higher timeframe bearish trend with a lower timeframe bullish
change of character.

Run with:

    poetry run python -m liquidity_hunter.app.examples.estimate_btcusdt_retail_bias
"""

from datetime import UTC, datetime

from liquidity_hunter.core.domain import (
    LiquiditySide,
    LiquidityZone,
    LiquidityZoneType,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.psychology import RetailBiasEstimate, RetailTrapAnalyzer

SYMBOL = "BTCUSDT"
CURRENT_PRICE = 60_000.0


def main() -> RetailBiasEstimate:
    """Estimate retail bias for a higher-TF-bearish / lower-TF-bullish-CHOCH scenario."""
    higher_timeframe_direction = MarketDirection.BEARISH

    market_structure_events = [
        MarketStructure(
            symbol=SYMBOL,
            timeframe=TimeFrame.M15,
            timestamp=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
            event=StructureEvent.CHANGE_OF_CHARACTER,
            direction=MarketDirection.BULLISH,
            price_level=CURRENT_PRICE,
        )
    ]

    liquidity_zones = [
        LiquidityZone(
            symbol=SYMBOL,
            timeframe=TimeFrame.H1,
            zone_type=LiquidityZoneType.EQUAL_LOWS,
            side=LiquiditySide.SELL_SIDE,
            price_high=CURRENT_PRICE * 0.995,
            price_low=CURRENT_PRICE * 0.995,
            formed_at=datetime(2024, 1, 1, 6, 0, tzinfo=UTC),
            strength=0.1,
        )
    ]

    estimate = RetailTrapAnalyzer().analyze(
        symbol=SYMBOL,
        higher_timeframe_direction=higher_timeframe_direction,
        market_structure_events=market_structure_events,
        liquidity_zones=liquidity_zones,
        current_price=CURRENT_PRICE,
    )

    print(f"Dominant side: {estimate.dominant_side.value}")
    print(f"Confidence: {estimate.confidence:.0f}")
    print(f"Explanation: {estimate.explanation}")

    return estimate


if __name__ == "__main__":
    main()
