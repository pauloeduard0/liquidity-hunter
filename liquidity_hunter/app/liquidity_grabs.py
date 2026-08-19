"""Build the unified stream of liquidity grabs from the mapped pools.

Composition-level, like `NarrativeEngine` and `LiquidityHuntEngine`: it
reads outputs of two different layers (`liquidity` for equal-level pools,
`liquidity` again for order blocks) and speaks about them in one vocabulary.

It emits *every* grab in the series. Deciding which few belong on a chart --
the side the current trend consumes, the events since the last structural
advance -- is presentation, and lives in the frontend with the rest of the
decluttering.
"""

from collections import defaultdict
from datetime import datetime

from liquidity_hunter.core.domain import (
    LiquidityGrab,
    LiquidityGrabOutcome,
    LiquidityPoolKind,
    LiquiditySide,
    LiquidityZone,
    MarketDirection,
    POIZone,
    TimeFrame,
)

_KIND_ORDER = {kind: i for i, kind in enumerate(LiquidityPoolKind)}


class _Contribution:
    """One pool consumed at a grab moment."""

    def __init__(
        self,
        kind: LiquidityPoolKind,
        price_level: float,
        outcome: LiquidityGrabOutcome,
    ) -> None:
        self.kind = kind
        self.price_level = price_level
        self.outcome = outcome


def build_liquidity_grabs(
    *,
    symbol: str,
    timeframe: TimeFrame,
    liquidity_zones: list[LiquidityZone],
    poi_zones: list[POIZone],
) -> list[LiquidityGrab]:
    """Collapse every consumed pool into one grab per candle and side."""
    by_moment: dict[tuple[datetime, LiquiditySide], list[_Contribution]] = defaultdict(list)

    for zone in liquidity_zones:
        if zone.invalidated_at is None:
            continue
        # The pool's own edge is the level that was resting: the top of a
        # buy-side pool, the bottom of a sell-side one.
        level = zone.price_high if zone.side is LiquiditySide.BUY_SIDE else zone.price_low
        outcome = (
            LiquidityGrabOutcome.REJECTED
            if zone.sweep_rejected
            else LiquidityGrabOutcome.SPENT
        )
        by_moment[(zone.invalidated_at, zone.side)].append(
            _Contribution(LiquidityPoolKind.EQUAL_LEVEL, level, outcome)
        )

    for poi in poi_zones:
        if poi.invalidated_at is None:
            continue
        # An order block retires on a *close* beyond its far boundary, so the
        # zone's own record only ever describes a spent level -- there is no
        # rejected order block to report, because a wick back into the box
        # never retires it. The far boundary is what was taken: the bottom of
        # a bullish (demand) block, the top of a bearish one.
        bullish = poi.direction is MarketDirection.BULLISH
        side = LiquiditySide.SELL_SIDE if bullish else LiquiditySide.BUY_SIDE
        level = poi.price_low if bullish else poi.price_high
        by_moment[(poi.invalidated_at, side)].append(
            _Contribution(LiquidityPoolKind.ORDER_BLOCK, level, LiquidityGrabOutcome.SPENT)
        )

    grabs: list[LiquidityGrab] = []
    for (timestamp, side), contributions in by_moment.items():
        kinds = sorted({c.kind for c in contributions}, key=lambda k: _KIND_ORDER[k])
        # The reported level is the furthest one reached: a candle that took
        # four stacked pools went as far as the deepest of them, and the
        # shallower ones are on the way there.
        levels = [c.price_level for c in contributions]
        price_level = max(levels) if side is LiquiditySide.BUY_SIDE else min(levels)
        # One pool handed back while another was spent is a spent moment:
        # price closed beyond a level that was resting there.
        outcome = (
            LiquidityGrabOutcome.REJECTED
            if all(c.outcome is LiquidityGrabOutcome.REJECTED for c in contributions)
            else LiquidityGrabOutcome.SPENT
        )
        grabs.append(
            LiquidityGrab(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=timestamp,
                price_level=price_level,
                side=side,
                kinds=kinds,
                pool_count=len(contributions),
                outcome=outcome,
            )
        )

    grabs.sort(key=lambda g: (g.timestamp, g.side.value))
    return grabs
