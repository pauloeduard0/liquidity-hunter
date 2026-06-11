"""RetailBias domain entity."""

from datetime import datetime

from pydantic import Field

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import BiasSource, MarketDirection


class RetailBias(DomainModel):
    """A measurement of retail market participant sentiment or positioning."""

    symbol: str
    timestamp: datetime
    source: BiasSource
    direction: MarketDirection
    sentiment_score: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
