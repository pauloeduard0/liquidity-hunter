"""MarketStructure domain entity."""

from datetime import datetime

from pydantic import Field

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import MarketDirection, StructureEvent, TimeFrame


class MarketStructure(DomainModel):
    """A descriptive snapshot of market structure at a point in time."""

    symbol: str
    timeframe: TimeFrame
    timestamp: datetime
    event: StructureEvent
    direction: MarketDirection
    price_level: float = Field(gt=0)
    reference_price_level: float | None = Field(default=None, gt=0)
