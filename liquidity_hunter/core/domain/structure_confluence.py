"""Confluence reading for a single structural break (BOS/CHoCH)."""

from datetime import datetime

from pydantic import Field

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import (
    ConfluenceFactor,
    MarketDirection,
    StructureEvent,
    TimeFrame,
)


class StructureConfluence(DomainModel):
    """How much orthogonal evidence supports a structural break.

    For a given BOS/CHoCH, tallies the independent observations that agree with
    its direction near the break — a VSA volume signal, an order block the move
    launched from, new money entering (OI), aligned taker aggression, a
    preceding stop-hunt sweep. It is a *descriptive* confidence reading (how
    many layers confluence on the structure), not a trade signal.

    Keyed to its event by ``event_timestamp`` + ``event_type`` (the same key
    the frontend already uses for OI participation suffixes), so a badge can be
    appended to the structure label.
    """

    symbol: str
    timeframe: TimeFrame
    event_timestamp: datetime
    event_type: StructureEvent
    direction: MarketDirection
    price_level: float
    factors: list[ConfluenceFactor] = Field(default_factory=list)
    score: float = Field(ge=0.0, le=100.0)
    description: str
