"""LiquidityZone domain entity."""

from datetime import datetime

from pydantic import Field, model_validator
from typing_extensions import Self

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import LiquiditySide, LiquidityZoneType, TimeFrame


class LiquidityZone(DomainModel):
    """A price region identified as holding resting liquidity."""

    symbol: str
    timeframe: TimeFrame
    zone_type: LiquidityZoneType
    side: LiquiditySide
    price_high: float = Field(gt=0)
    price_low: float = Field(gt=0)
    formed_at: datetime
    invalidated_at: datetime | None = None
    strength: float = Field(default=0.0, ge=0.0, le=1.0)
    is_mitigated: bool = False

    @model_validator(mode="after")
    def _check_price_range(self) -> Self:
        if self.price_high < self.price_low:
            raise ValueError("price_high must be >= price_low")
        return self
