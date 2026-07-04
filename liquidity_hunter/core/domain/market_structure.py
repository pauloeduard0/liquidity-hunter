"""MarketStructure domain entity."""

from datetime import datetime

from pydantic import Field

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import (
    MarketDirection,
    StructureEvent,
    StructureScope,
    TimeFrame,
)


class MarketStructure(DomainModel):
    """A descriptive snapshot of market structure at a point in time."""

    symbol: str
    timeframe: TimeFrame
    timestamp: datetime
    event: StructureEvent
    direction: MarketDirection
    price_level: float = Field(gt=0)
    reference_price_level: float | None = Field(default=None, gt=0)
    reference_timestamp: datetime | None = None
    origin_price_level: float | None = Field(default=None, gt=0)
    scope: StructureScope = StructureScope.MAJOR
    # For CHANGE_OF_CHARACTER only: whether the broken reference was a
    # *structural* level (a close-confirmed BOS leg origin, a
    # continuation-promoted pullback, a live pending-BOS origin, or the
    # blind-spot CHoCH origin) rather than a *weak* one (a synthetic re-anchor
    # level, a wick-only-break promotion, or the trailing cold-start fallback
    # -- the ones the new-cycle persistence barrier governs). `None` for other
    # event types and for detectors that do not classify their references.
    reference_structural: bool | None = None
