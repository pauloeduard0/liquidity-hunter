"""Consolidation (lateral range) domain entity."""

from datetime import datetime
from typing import Self

from pydantic import Field, model_validator

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import (
    ConsolidationStatus,
    MarketDirection,
    TimeFrame,
)


class ConsolidationRange(DomainModel):
    """An observed lateral consolidation: a stretch of candles with no
    structure advance where price oscillated inside a volatility-bounded box.

    A range is *confirmed* only after a minimum number of candles held inside
    a box no taller than a multiple of the series' mean true range, touching
    both boundary zones alternately -- so a slow drift or an ordinary pullback
    does not qualify. While confirmed and unresolved the structure detector is
    expected to be silent (a range has no BOS/CHoCH by definition); the range
    itself is the standing structural observation for that stretch.

    status lifecycle:
    - ACTIVE: price is still inside the box at the end of the series
      (`end_timestamp` is `None`).
    - RESOLVED: sustained closes beyond a boundary broke the range
      (`resolved_direction` is the breakout direction, `end_timestamp` the
      first breakout candle), or a structure advance (BOS/CHoCH) ended it.
    """

    symbol: str
    timeframe: TimeFrame
    start_timestamp: datetime
    end_timestamp: datetime | None = None
    price_low: float = Field(gt=0)
    price_high: float = Field(gt=0)
    status: ConsolidationStatus = ConsolidationStatus.ACTIVE
    resolved_direction: MarketDirection | None = None
    candle_count: int = Field(ge=1)

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.price_high <= self.price_low:
            raise ValueError("price_high must be > price_low")
        if self.status is ConsolidationStatus.RESOLVED:
            if self.end_timestamp is None or self.resolved_direction is None:
                raise ValueError(
                    "a RESOLVED range requires end_timestamp and resolved_direction"
                )
            if self.end_timestamp < self.start_timestamp:
                raise ValueError("end_timestamp must not precede start_timestamp")
        elif self.end_timestamp is not None or self.resolved_direction is not None:
            raise ValueError(
                "an ACTIVE range must not carry end_timestamp or resolved_direction"
            )
        return self
