"""Behavioral divergence between price action and institutional flow."""

from datetime import datetime

from pydantic import Field

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import (
    DivergenceType,
    LiquiditySide,
    MarketDirection,
    TimeFrame,
)


class BehaviorDivergence(DomainModel):
    """An observed divergence between price movement and volume delta.

    Describes a window where institutional flow (volume delta) opposes the
    visible price direction — e.g. price rising while net taker flow is
    selling (distribution near a buy-side zone). This is an *observation*,
    not a trade recommendation.
    """

    symbol: str
    timeframe: TimeFrame
    timestamp: datetime
    divergence_type: DivergenceType
    direction: MarketDirection
    price_level: float
    volume_delta_avg: float
    price_change_pct: float
    nearest_zone_side: LiquiditySide | None = None
    nearest_zone_price_low: float | None = None
    nearest_zone_price_high: float | None = None
    confidence: float = Field(ge=0.0, le=100.0)
    description: str
