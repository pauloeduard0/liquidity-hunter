"""Post-detection sweep check for liquidity zones.

Scans candles after each zone's `formed_at` to determine whether price
has swept (wicked through) the zone.  A swept zone gets
`is_mitigated=True` and `invalidated_at` set to the sweeping candle's
timestamp.

- Buy-side zones (EQH, Swing High): swept when a candle's high
  exceeds `price_high`.
- Sell-side zones (EQL, Swing Low): swept when a candle's low
  falls below `price_low`.
"""

from liquidity_hunter.core.domain import Candle, LiquiditySide, LiquidityZone


def mark_swept_zones(
    zones: list[LiquidityZone],
    candles: list[Candle],
) -> list[LiquidityZone]:
    """Return a new list with swept zones marked as mitigated."""
    sorted_candles = sorted(candles, key=lambda c: c.timestamp)
    return [_check_zone(zone, sorted_candles) for zone in zones]


def _check_zone(zone: LiquidityZone, sorted_candles: list[Candle]) -> LiquidityZone:
    if zone.is_mitigated:
        return zone

    for candle in sorted_candles:
        if candle.timestamp <= zone.formed_at:
            continue

        swept = (
            candle.high > zone.price_high
            if zone.side == LiquiditySide.BUY_SIDE
            else candle.low < zone.price_low
        )
        if swept:
            return zone.model_copy(
                update={"is_mitigated": True, "invalidated_at": candle.timestamp}
            )

    return zone
