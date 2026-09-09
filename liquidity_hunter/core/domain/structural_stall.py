"""Structural stall domain entity."""

from datetime import datetime

from pydantic import Field

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import MarketDirection


class StructuralStall(DomainModel):
    """The standing structural leg, observed as no longer advancing.

    A `BREAK_OF_STRUCTURE` leaves its leg as the structure in force until the
    next advance replaces it. When the leg stops advancing the detector is
    correctly silent, so the chart keeps presenting that last BOS as current
    while price drifts back through it -- measured at 78 candles and 10.9 ATR
    of give-back at the median after an expansion, against 40 and 5.6 for an
    ordinary break (`research/expansion_stall.py`).

    **It is not a reversal, and carries no forecast.** It says only that this
    leg is no longer active: after a stall the leg reverses 44% of the time,
    stays undecided 38% and resumes 19% (`research/structural_stall_validation.py`),
    which is precisely why the state must not be read as a direction. See
    `liquidity.structural_stall.detect_structural_stall` for the rule and the
    parameter measurement.
    """

    #: First candle where both conditions held -- when the leg went quiet, not
    #: when it was observed.
    stale_since: datetime
    #: The standing leg's direction. NOT a forecast: a stalled bullish leg is
    #: not a bearish call.
    direction: MarketDirection
    last_advance_timestamp: datetime
    last_advance_price: float = Field(gt=0)
    bars_since_advance: int = Field(ge=0)
    #: Give-back from `leg_extreme_price`, in units of `frozen_atr_pct`.
    retracement_atr: float = Field(ge=0)
    #: Mean true range as a fraction of price, frozen at the advance.
    frozen_atr_pct: float = Field(gt=0)
    #: The furthest close the leg reached, up to `stale_since` (never later --
    #: that would be lookahead).
    leg_extreme_price: float = Field(gt=0)
    leg_extreme_timestamp: datetime
