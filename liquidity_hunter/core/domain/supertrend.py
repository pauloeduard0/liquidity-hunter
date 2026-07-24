"""Supertrend: an ATR-banded trailing trend reading of a candle series."""

from datetime import datetime

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import MarketDirection


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
