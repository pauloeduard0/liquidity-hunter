"""LiquidityZone domain entity."""

from datetime import datetime

from pydantic import Field, model_validator
from typing_extensions import Self

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import LiquiditySide, LiquidityZoneType, TimeFrame


class LiquidityZone(DomainModel):
    """A price region identified as holding resting liquidity.

    A pool is consumed in two distinguishable ways, and the distinction is
    the whole point of watching one:

    - a **wick** reaches through and price comes back — the resting orders
      were paid and the level survives as memory. This is the liquidity
      *grab* the platform is built to observe (`invalidated_at`,
      `is_mitigated`).
    - a **close** lands beyond it — the level stopped being a pool and
      became something else (`breached_at`).

    The two are recorded separately because they are ordered in time, not
    exclusive: a pool grabbed on Monday may be closed through on Friday.
    A breach implies an earlier (or simultaneous) sweep, since a close
    beyond a level requires a wick beyond it.
    """

    symbol: str
    timeframe: TimeFrame
    zone_type: LiquidityZoneType
    side: LiquiditySide
    price_high: float = Field(gt=0)
    price_low: float = Field(gt=0)
    formed_at: datetime
    #: First candle whose *wick* reached through the zone — the grab.
    invalidated_at: datetime | None = None
    #: First candle whose *close* landed beyond the zone — the level spent.
    breached_at: datetime | None = None
    #: Whether the sweeping candle *closed back inside* — the grab handed back.
    sweep_rejected: bool = False
    strength: float = Field(default=0.0, ge=0.0, le=1.0)
    is_mitigated: bool = False

    @property
    def is_breached(self) -> bool:
        """Whether a candle has closed beyond this zone."""
        return self.breached_at is not None

    @property
    def was_rejected(self) -> bool:
        """Whether the grab itself was handed back.

        Read on the sweeping candle, not over the rest of the series. The
        difference is not cosmetic: measured across six symbol/timeframe
        combos, "swept and never closed through afterwards" holds for only
        1-8 pools out of 48-80 — over a 1200-candle window almost every
        level is eventually spent, so that reading barely varies and says
        little. Whether the wick was given straight back splits 40-50%,
        which is the question a grab actually poses.
        """
        return self.is_mitigated and self.sweep_rejected

    @model_validator(mode="after")
    def _check_price_range(self) -> Self:
        if self.price_high < self.price_low:
            raise ValueError("price_high must be >= price_low")
        return self

    @model_validator(mode="after")
    def _check_consumption_order(self) -> Self:
        if self.breached_at is not None and self.invalidated_at is not None:
            if self.breached_at < self.invalidated_at:
                raise ValueError("breached_at must be >= invalidated_at")
        return self
