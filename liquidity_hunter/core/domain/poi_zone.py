"""POI (Point of Interest / Order Block) domain entities."""

from datetime import datetime
from typing import Self

from pydantic import Field, model_validator

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import (
    MarketDirection,
    POIZoneKind,
    POIZoneStatus,
    TimeFrame,
)


class POIZone(DomainModel):
    """An order/breaker/mitigation block zone anchored to a market structure break.

    An ORDER_BLOCK is the *last opposite-direction candle before the impulse*
    that broke market structure (MSB): for a bullish MSB (a swing high
    confirmed beyond the prior swing high by the fib-factor extension), the
    last bearish candle of the down leg into the swing low the impulse
    launched from; a bearish MSB mirrors it. A BREAKER_BLOCK /
    MITIGATION_BLOCK is the last *same*-direction candle of the leg that
    formed the broken pivot (breaker when the impulse-origin extreme swept
    the prior one, mitigation otherwise). The box spans the anchor candle's
    full range (high to low), frozen at creation.

    status lifecycle:
    - ACTIVE: zone is live. Price trading back inside the zone does not
      retire it -- the box keeps extending right.
    - INVALIDATED: a single candle *close* beyond the zone's far boundary
      (below `price_low` for a bullish zone, above `price_high` for a
      bearish one) retires it.
    """

    symbol: str
    timeframe: TimeFrame
    direction: MarketDirection
    kind: POIZoneKind = POIZoneKind.ORDER_BLOCK
    price_low: float = Field(gt=0)
    price_high: float = Field(gt=0)
    created_at: datetime
    ob_candle_timestamp: datetime
    status: POIZoneStatus = POIZoneStatus.ACTIVE
    invalidated_at: datetime | None = None

    @model_validator(mode="after")
    def _check_price_range(self) -> Self:
        if self.price_high <= self.price_low:
            raise ValueError("price_high must be > price_low")
        return self
