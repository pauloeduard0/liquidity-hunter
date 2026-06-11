"""Candle domain entity."""

from datetime import datetime

from pydantic import Field, model_validator
from typing_extensions import Self

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import TimeFrame


class Candle(DomainModel):
    """A single OHLCV price bar for a symbol and timeframe."""

    symbol: str
    timeframe: TimeFrame
    timestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)
    taker_buy_volume: float = Field(ge=0)

    @model_validator(mode="after")
    def _check_price_consistency(self) -> Self:
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high must be >= open, close, and low")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low must be <= open, close, and high")
        return self

    @model_validator(mode="after")
    def _check_taker_buy_volume(self) -> Self:
        if self.taker_buy_volume > self.volume:
            raise ValueError("taker_buy_volume must be <= volume")
        return self
