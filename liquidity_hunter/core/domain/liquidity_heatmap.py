"""LiquidityHeatmap domain entities.

A heatmap of estimated resting-liquidity concentration ("stop magnets")
across price, aggregated from liquidity zones, POI (order block) zones, and
in-progress manipulation cycles. This is a descriptive *observation* of where
stops are likely clustered, not a trade recommendation.
"""

from typing import Self

from pydantic import Field, model_validator

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import LiquiditySide, TimeFrame


class HeatmapBucket(DomainModel):
    """A single price band of the liquidity heatmap.

    ``heat`` is the normalized concentration of estimated resting liquidity
    in this band (0-100, relative to the hottest band in the map). The
    ``heat_*`` fields break that contribution down by source *before*
    normalization, for transparency. ``side`` records whether the band sits
    above (``BUY_SIDE``) or below (``SELL_SIDE``) the current price.
    """

    price_low: float = Field(gt=0)
    price_high: float = Field(gt=0)
    heat: float = Field(ge=0.0, le=100.0)
    side: LiquiditySide
    heat_zones: float = Field(default=0.0, ge=0.0)
    heat_poi: float = Field(default=0.0, ge=0.0)
    heat_manipulation: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def _check_price_range(self) -> Self:
        if self.price_high <= self.price_low:
            raise ValueError("price_high must be > price_low")
        return self


class LiquidityHeatmap(DomainModel):
    """A heatmap of estimated liquidity concentration across price.

    ``buckets`` partition the visible candle range into equal-width price
    bands (``bucket_pct`` of ``current_price`` each), ordered from lowest to
    highest price.
    """

    symbol: str
    timeframe: TimeFrame
    current_price: float = Field(gt=0)
    bucket_pct: float = Field(gt=0)
    buckets: list[HeatmapBucket]
