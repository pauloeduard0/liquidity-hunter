"""Liquidity grab: one moment where price took resting liquidity."""

from datetime import datetime

from pydantic import Field

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import (
    LiquidityGrabOutcome,
    LiquidityPoolKind,
    LiquiditySide,
    TimeFrame,
)


class LiquidityGrab(DomainModel):
    """A candle that consumed resting liquidity, whatever kind of pool held it.

    Each detector knows separately that a level of its own was consumed --
    an equal-level pool by `invalidated_at`, an order block by
    `POIZone.invalidated_at` -- and each speaks about it in its own terms
    (or, in the order block's case, says nothing and simply stops drawing
    the box). A grab is that observation with the detector's identity moved
    from the *event* to its evidence: what happened is that price reached a
    level and took what was resting there; which layer had mapped the level
    is a detail of how we knew.

    Read per **moment**, not per pool. One impulse routinely takes several
    stacked pools at once -- four equal highs a few ticks apart and the
    order block behind them are one event, not five -- so grabs at the same
    candle and side are one `LiquidityGrab` carrying every `kind` that
    contributed. `pool_count` is how many pools it consumed, which is the
    only honest measure of how much was resting there.
    """

    symbol: str
    timeframe: TimeFrame
    #: The candle that took the liquidity.
    timestamp: datetime
    #: The level that was taken (the consumed pool's own edge; the most
    #: extreme one when several were taken together).
    price_level: float = Field(gt=0)
    #: Which side rested there: buy-side above price, sell-side below.
    side: LiquiditySide
    #: Every pool kind consumed at this moment, ordered as listed in the enum.
    kinds: list[LiquidityPoolKind] = Field(min_length=1)
    #: How many individual pools this one candle consumed.
    pool_count: int = Field(ge=1)
    outcome: LiquidityGrabOutcome

    @property
    def was_rejected(self) -> bool:
        """Whether the level was handed back rather than spent."""
        return self.outcome is LiquidityGrabOutcome.REJECTED
