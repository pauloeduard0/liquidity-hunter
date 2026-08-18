"""Post-detection consumption check for liquidity zones.

Scans candles after each zone's `formed_at` for the two distinguishable
ways a pool is consumed (see `LiquidityZone`):

- a **sweep**: a candle's wick reaches through the zone. The resting
  orders were paid; the zone gets `is_mitigated=True` and
  `invalidated_at` set to that candle, plus `sweep_rejected` recording
  whether that same candle *closed back inside* — the grab handed back.
- a **breach**: a candle *closes* beyond the zone. The level stopped
  being a pool; `breached_at` is set to that candle.

Buy-side zones (EQH, Swing High) are reached from below — swept when a
high exceeds `price_high`, breached when a close does. Sell-side zones
(EQL, Swing Low) mirror it against `price_low`.

Both are scanned in one pass, and both are kept: a breach does not erase
the sweep that preceded it. The scan stops once both are resolved.
"""

from liquidity_hunter.core.domain import Candle, LiquiditySide, LiquidityZone


def mark_swept_zones(
    zones: list[LiquidityZone],
    candles: list[Candle],
) -> list[LiquidityZone]:
    """Return a new list with swept and breached zones marked."""
    sorted_candles = sorted(candles, key=lambda c: c.timestamp)
    return [_check_zone(zone, sorted_candles) for zone in zones]


def _check_zone(zone: LiquidityZone, sorted_candles: list[Candle]) -> LiquidityZone:
    # A caller-supplied mitigation is authoritative for the sweep half; only
    # the breach is still open to discovery.
    need_sweep = not zone.is_mitigated
    if not need_sweep and zone.breached_at is not None:
        return zone

    buy_side = zone.side == LiquiditySide.BUY_SIDE
    level = zone.price_high if buy_side else zone.price_low

    update: dict[str, object] = {}
    for candle in sorted_candles:
        if candle.timestamp <= zone.formed_at:
            continue

        if need_sweep:
            wick = candle.high if buy_side else candle.low
            if (wick > level) if buy_side else (wick < level):
                update["is_mitigated"] = True
                update["invalidated_at"] = candle.timestamp
                # Read on this candle alone: the wick took the resting orders
                # and the close came back inside. Whether the level survives
                # the rest of the series is `breached_at`, a different (and
                # far rarer) question.
                update["sweep_rejected"] = (
                    (candle.close <= level) if buy_side else (candle.close >= level)
                )
                need_sweep = False

        if zone.breached_at is None and "breached_at" not in update:
            if (candle.close > level) if buy_side else (candle.close < level):
                update["breached_at"] = candle.timestamp
                # A close beyond the level implies its wick went beyond too,
                # so a zone breached before any sweep was found is swept by
                # this very candle.
                if need_sweep:
                    update["is_mitigated"] = True
                    update["invalidated_at"] = candle.timestamp
                    need_sweep = False
                break

    return zone.model_copy(update=update) if update else zone
