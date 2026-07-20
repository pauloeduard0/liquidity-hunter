"""Volume-Spread-Analysis (VSA) signal read from a single candle's anatomy."""

from datetime import datetime

from pydantic import Field

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import (
    MarketDirection,
    TimeFrame,
    VSAPattern,
)


class VolumeSpreadSignal(DomainModel):
    """A Volume-Spread-Analysis reading of one candle.

    Classic VSA reads the *effort vs result* relationship between a candle's
    spread (high-low range), the position of its close within that range, its
    wick rejection, and its **raw** volume relative to recent candles — while
    ``volume_delta`` (net taker aggression) confirms *who* won.  Each signal is
    an *observation* about market participation (climax, absence of a side,
    rejection), not a trade recommendation.

    Fields
    ------
    pattern:
        The classified :class:`VSAPattern`.
    direction:
        The directional implication of the pattern (e.g. a selling climax is
        ``BULLISH``: capitulation preceding a likely bounce).
    price_level:
        The candle's close, where the signal is anchored on the chart.
    spread_ratio:
        Candle spread (``high - low``) over the trailing mean spread — the
        "result" magnitude (wide vs narrow).
    close_position:
        ``(close - low) / (high - low)`` in ``[0, 1]``: 0 = closed on the low,
        1 = closed on the high.
    volume_ratio:
        Raw ``volume`` over the trailing mean volume — the "effort" magnitude.
    volume_delta:
        Net taker aggression (``2 * taker_buy_volume - volume``) for the
        candle, confirming direction.
    """

    symbol: str
    timeframe: TimeFrame
    timestamp: datetime
    pattern: VSAPattern
    direction: MarketDirection
    price_level: float
    spread_ratio: float = Field(ge=0.0)
    close_position: float = Field(ge=0.0, le=1.0)
    volume_ratio: float = Field(ge=0.0)
    volume_delta: float
    confidence: float = Field(ge=0.0, le=100.0)
    description: str
