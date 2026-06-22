"""Leverage-liquidation domain entities.

A descriptive "gravitational map" of where leveraged retail positions would
be force-liquidated, inferred from which side of the perpetual-futures book is
over-leveraged (open interest, funding, crowd ratio) and where the crowd most
likely entered (liquidity zones). This is an *observation* of stored potential
energy, not a trade recommendation.
"""

from datetime import datetime
from typing import Self

from pydantic import Field, model_validator

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import LiquiditySide, RetailPositioning, TimeFrame


class LiquidationBand(DomainModel):
    """A price band where positions at one leverage tier would be liquidated.

    Anchored to a likely entry (``source_entry_price``) projected to its
    liquidation price for ``leverage``: crowded longs liquidate *below* their
    entry (``SELL_SIDE``), crowded shorts *above* (``BUY_SIDE``). ``intensity``
    (0-100, normalized to the hottest band in the map) scales with how
    over-leveraged the side is, the anchor's strength, and how populated that
    leverage tier typically is.

    The band is bounded in time: ``start_time`` is when the entry cluster
    formed (the liquidation pool came into existence), and ``end_time`` is when
    price first reached the liquidation level (the pool was consumed). A
    ``None`` ``end_time`` means the level has not been hit yet — the pool is
    still live, so the band extends to the right edge of the chart.
    """

    price_low: float = Field(gt=0)
    price_high: float = Field(gt=0)
    leverage: int = Field(gt=0)
    side: LiquiditySide
    source_entry_price: float = Field(gt=0)
    intensity: float = Field(ge=0.0, le=100.0)
    start_time: datetime
    end_time: datetime | None = None

    @model_validator(mode="after")
    def _check_price_range(self) -> Self:
        if self.price_high <= self.price_low:
            raise ValueError("price_high must be > price_low")
        return self

    @model_validator(mode="after")
    def _check_time_range(self) -> Self:
        if self.end_time is not None and self.end_time < self.start_time:
            raise ValueError("end_time must be >= start_time")
        return self


class LeverageLiquidationMap(DomainModel):
    """Estimated leveraged-liquidation bands for a symbol/timeframe snapshot.

    ``dominant_leveraged_side`` is the over-leveraged side inferred from the
    futures market state; ``positioning_intensity`` (0-1) is how strongly the
    book leans that way. The ``funding_rate`` / ``open_interest_change_pct`` /
    ``long_short_ratio`` fields record the evidence behind that inference for
    transparency. ``bands`` are the projected liquidation price bands (empty
    when positioning is neutral or inputs are insufficient).
    """

    symbol: str
    timeframe: TimeFrame
    current_price: float = Field(gt=0)
    dominant_leveraged_side: RetailPositioning
    positioning_intensity: float = Field(ge=0.0, le=1.0)
    funding_rate: float
    open_interest_change_pct: float
    long_short_ratio: float = Field(ge=0.0)
    bands: list[LiquidationBand]
