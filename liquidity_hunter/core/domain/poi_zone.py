"""POI (Point of Interest / Order Block) domain entities."""

from datetime import datetime
from typing import Self

from pydantic import Field, model_validator

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import MarketDirection, POIZoneStatus, TimeFrame


class POIZone(DomainModel):
    """An order block zone anchored to a market structure break (MSB).

    The zone is the *last opposite-direction candle before the impulse* that
    broke market structure: for a bullish MSB (a swing high confirmed beyond
    the prior swing high by the fib-factor extension), the last bearish candle
    of the down leg into the swing low the impulse launched from; a bearish
    MSB mirrors it. The box spans the OB candle's full range (high to low),
    frozen at creation.

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
