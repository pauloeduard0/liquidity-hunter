"""What a liquidity sweep actually swept."""

from datetime import datetime

from pydantic import Field

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import MarketDirection, TimeFrame


class SweepContext(DomainModel):
    """The pool context of one ``LIQUIDITY_SWEEP``, measured after the fact.

    A sweep is emitted as a *residual* category: a counter-trend pivot that
    broke the trailing ``active_<side>`` and failed the CHoCH persistence
    check. That makes it a statement about the state machine, not about
    liquidity -- the event knows a level was poked and reverted, but not
    whether anything was resting there. Measured against a direction-matched
    random control, "this sweep coincides with some mapped pool" is very
    nearly vacuous (94% of sweeps, 89% of random pivots).

    What *is* not vacuous is a sweep whose extreme lands inside an order
    block that (a) faces the sweep -- demand below for a sweep taking lows --
    and (b) already existed when the sweep happened. Over 12 symbols x
    15m/1h/4h the swept extreme then survived uncrossed for the next 5
    candles 76% of the time against the control's 60% (59% vs 45% at 10),
    with a third less adverse excursion. The effect is **short-horizon**: by
    20-40 candles it is gone. So this is a reading about the candles right
    after the sweep, not about a trend starting -- and, like everything else
    here, descriptive.

    Keyed to the event by ``event_timestamp``, the way ``OIQualifiedEvent``
    is: the annotation rides alongside the structure stream rather than
    inside ``MarketStructure``, which has no business carrying fields only
    one of its event types can use.
    """

    symbol: str
    timeframe: TimeFrame
    #: The sweeping candle -- the ``MarketStructure.timestamp`` of the event
    #: this annotates.
    event_timestamp: datetime
    #: The sweep's own direction: the side the wick reached (``BULLISH`` took
    #: highs, ``BEARISH`` took lows), matching the annotated event.
    direction: MarketDirection
    #: The extreme the sweeping candle reached -- the high of a bullish
    #: sweep, the low of a bearish one.
    swept_extreme: float = Field(gt=0)
    #: Whether that extreme landed inside a facing, pre-existing order block:
    #: the measured half of this reading.
    in_order_block: bool = False
    #: The boundaries of that block, so a consumer can point at the box.
    #: ``None`` when ``in_order_block`` is False.
    block_low: float | None = Field(default=None, gt=0)
    block_high: float | None = Field(default=None, gt=0)
    #: How far past the broken reference the sweeping candle reached, in
    #: mean-true-range units of its own series -- the same depth measure
    #: `LiquidityGrab` carries, read on the sweeping candle alone. Separates
    #: a wick that grazed the level from one that ran a long way past it;
    #: 39% of sweeps clear it by less than a quarter ATR. ``None`` when the
    #: event carried no reference level or the series has no volatility.
    excursion_atr: float | None = Field(default=None, ge=0.0)
