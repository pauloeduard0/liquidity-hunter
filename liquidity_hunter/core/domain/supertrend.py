"""Supertrend: an ATR-banded trailing trend reading of a candle series."""

from datetime import datetime

from pydantic import Field

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import (
    MarketControlSide,
    MarketDirection,
    SupertrendBreakQuality,
)


class SupertrendPoint(DomainModel):
    """One candle's Supertrend reading.

    The Supertrend trails price with an ATR-scaled band: while the trend is
    up, ``value`` is the *lower* band (a floor that only ratchets upward);
    while it is down, ``value`` is the *upper* band (a ceiling that only
    ratchets downward). A close through the opposing band flips the trend and
    the band the reading follows.

    Fields
    ------
    timestamp:
        The candle this reading belongs to.
    value:
        The active band price — the floor when ``direction`` is ``BULLISH``,
        the ceiling when it is ``BEARISH``.
    direction:
        The standing trend at this candle (``BULLISH``/``BEARISH`` only).
    flip:
        ``True`` on the candle where ``direction`` changed (the classic
        "buy"/"sell" marker of the indicator). Purely descriptive: it marks
        where the band flipped, not an instruction to act.
    upper_band / lower_band:
        Both bands at this candle, kept so the inactive side stays available
        for research even though the chart draws only ``value``.
    """

    timestamp: datetime
    value: float
    direction: MarketDirection
    flip: bool = False
    upper_band: float
    lower_band: float


class SupertrendBreak(DomainModel):
    """A Supertrend flip, qualified by who financed it.

    The band that a flip breaks is where the previous flip's entrants keep
    their stops, so the break is a liquidity event as much as a trend event.
    This entity records the break and what the participation layers said about
    it: whether fresh money took the flip's side (``MarketControlState``),
    whether structure confirmed it with a break of its own shortly after, and
    whether price simply came back inside the band — the "broke out, stopped
    you, returned" pattern.

    Fields
    ------
    timestamp:
        The candle on which the trend flipped.
    direction:
        The flip's direction (the side the band broke toward).
    broken_level:
        The band that gave way — the active band of the preceding candle.
    quality:
        The :class:`SupertrendBreakQuality` verdict.
    reclaim_timestamp / reclaim_candles:
        When (and how many candles later) price *closed* back inside the broken
        band. ``None`` while the break still holds.
    controller:
        The credited side at the flip candle, from the market-control series;
        ``None`` when the series does not cover it (spot / no OI).
    structure_confirmed:
        A confirmed same-direction break of structure followed the flip within
        the confirmation window.
    evidence:
        Names of the components that shaped the verdict (e.g. ``"reclaim:3c"``,
        ``"no-new-money"``, ``"oi-flush"``, ``"vsa-climax"``, ``"bos"``).
    """

    timestamp: datetime
    direction: MarketDirection
    broken_level: float = Field(gt=0)
    quality: SupertrendBreakQuality = SupertrendBreakQuality.UNKNOWN
    reclaim_timestamp: datetime | None = None
    reclaim_candles: int | None = Field(default=None, ge=0)
    controller: MarketControlSide | None = None
    structure_confirmed: bool = False
    evidence: list[str] = Field(default_factory=list)
    description: str = ""
