"""Test helpers for building `RetailTrapAnalyzer` inputs."""

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

SYMBOL = "BTCUSDT"
FORMED_AT = datetime(2024, 1, 1, tzinfo=UTC)


def make_structure_event(
    event: StructureEvent,
    direction: MarketDirection,
    *,
    timeframe: TimeFrame = TimeFrame.M15,
    timestamp: datetime = FORMED_AT,
    price_level: float = 100.0,
) -> MarketStructure:
    """Build a `MarketStructure` event."""
    return MarketStructure(
        symbol=SYMBOL,
        timeframe=timeframe,
        timestamp=timestamp,
        event=event,
        direction=direction,
        price_level=price_level,
    )


def make_zone(
    price: float,
    *,
    side: LiquiditySide,
    zone_type: LiquidityZoneType = LiquidityZoneType.EQUAL_LOWS,
    timeframe: TimeFrame = TimeFrame.H1,
    strength: float = 0.5,
    price_low: float | None = None,
) -> LiquidityZone:
    """Build a `LiquidityZone` at `price` (or `[price_low, price]` if given)."""
    return LiquidityZone(
        symbol=SYMBOL,
        timeframe=timeframe,
        zone_type=zone_type,
        side=side,
        price_high=price,
        price_low=price_low if price_low is not None else price,
        formed_at=FORMED_AT,
        strength=strength,
    )
