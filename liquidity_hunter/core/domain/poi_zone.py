"""POI (Point of Interest / Order Block) domain entities."""

from datetime import datetime
from typing import Self

from pydantic import Field, model_validator

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import MarketDirection, POIZoneStatus, TimeFrame


class POIZone(DomainModel):
    """An order block zone anchored between a validated CHoCH and the first BOS.

    `price_low` / `price_high` are frozen at creation and never updated.
    For a bullish (demand) zone the box spans from the extreme candle's low
    to its midpoint (50% of high-low range); bearish (supply) mirrors it.

    status lifecycle:
    - ACTIVE: zone is live and unmitigated.
    - MITIGATED: price swept the invalidation boundary and closed back
      inside/beyond (RTO fired).
    - INVALIDATED: `invalidation_persistence_candles` consecutive closes
      beyond the boundary confirmed a structural break.
    """

    symbol: str
    timeframe: TimeFrame
    direction: MarketDirection
    price_low: float = Field(gt=0)
    price_high: float = Field(gt=0)
    created_at: datetime
    origin_choch_timestamp: datetime
    origin_bos_timestamp: datetime
    extreme_candle_timestamp: datetime
    status: POIZoneStatus = POIZoneStatus.ACTIVE
    invalidated_at: datetime | None = None
    mitigated_at: datetime | None = None

    @model_validator(mode="after")
    def _check_price_range(self) -> Self:
        if self.price_high <= self.price_low:
            raise ValueError("price_high must be > price_low")
        return self


class RTOSweepEvent(DomainModel):
    """Institutional liquidity capture + return-to-origin on a POI zone.

    Fires when price sweeps beyond the zone's invalidation boundary and a
    subsequent candle closes back inside or beyond the zone in the zone's
    direction -- a liquidity grab followed by recovery.

    `sweep_extreme`: the most adverse price reached during the sweep period
    (lowest low for a bullish zone, highest high for a bearish zone).
    """

    symbol: str
    timeframe: TimeFrame
    direction: MarketDirection
    timestamp: datetime
    zone_price_low: float = Field(gt=0)
    zone_price_high: float = Field(gt=0)
    sweep_extreme: float = Field(gt=0)
