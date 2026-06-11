"""Test helpers for building `LiquidityZone` instances."""

from datetime import UTC, datetime

from liquidity_hunter.core.domain import (
    LiquiditySide,
    LiquidityZone,
    LiquidityZoneType,
    TimeFrame,
)

FORMED_AT = datetime(2024, 1, 1, tzinfo=UTC)


def make_zone(
    price: float,
    *,
    timeframe: TimeFrame = TimeFrame.H1,
    strength: float = 0.5,
    zone_type: LiquidityZoneType = LiquidityZoneType.SWING_HIGH,
    side: LiquiditySide = LiquiditySide.BUY_SIDE,
    price_low: float | None = None,
) -> LiquidityZone:
    """Build a `LiquidityZone` at `price` (or `[price_low, price]` if given)."""
    return LiquidityZone(
        symbol="BTCUSDT",
        timeframe=timeframe,
        zone_type=zone_type,
        side=side,
        price_high=price,
        price_low=price_low if price_low is not None else price,
        formed_at=FORMED_AT,
        strength=strength,
    )
