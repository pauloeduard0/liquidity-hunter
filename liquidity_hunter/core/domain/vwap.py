"""VWAP domain entities.

The volume-weighted average price of everything traded since an anchor: not
"where price is" but *what the participants who entered since that point paid*.
Where a `VolumeProfile` answers "where did the market agree" as a static
picture of one window, a VWAP answers "what does the population that entered
here hold, on average" as a line that walks forward with the tape.

That difference is what makes the ``EVENT`` anchor the interesting one for this
project: anchored at a liquidity sweep or at the CHoCH that turned the leg, the
line is the break-even of the crowd that was drawn in by that event — the price
level at which that population sits flat, which is where it tends to be
defended or given up.

A descriptive *observation*, like every other reading here: an average paid,
never a level to act on.

Fidelity
--------
Binance klines report one volume figure per candle, not per trade, so each
candle contributes its *typical price* ``(high + low + close) / 3`` weighted by
its whole volume, rather than every individual print at its own price. The
error this introduces is second-order — a volume-weighted mean averages the
intra-candle dispersion away, and the dispersion is bounded by the candle's own
range — so a kline VWAP tracks a trade-level VWAP far more closely than
kline-sourced *delta*-at-price tracks a true footprint (see
`core.domain.volume_profile`). ``estimated`` records the approximation rather
than hiding it.
"""

from datetime import datetime
from typing import Self

from pydantic import Field, model_validator

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import TimeFrame, VWAPAnchor


class VWAPPoint(DomainModel):
    """One candle's VWAP reading within its accumulation.

    Fields
    ------
    timestamp:
        The candle this reading belongs to.
    anchor_timestamp:
        The candle the accumulation behind this reading started at. Constant
        across a run of points and changing at each new session (or window),
        so a consumer can break the line into segments on it rather than
        drawing one continuous line across unrelated accumulations.
    value:
        The volume-weighted average price from the anchor through this candle.
    upper_1 / lower_1 / upper_2 / lower_2:
        Volume-weighted standard-deviation bands around ``value``, at the
        series' ``band_multipliers``. ``None`` when the accumulation has no
        dispersion yet (its first candle) or fewer multipliers were requested.
    """

    timestamp: datetime
    anchor_timestamp: datetime
    value: float = Field(gt=0)
    upper_1: float | None = None
    lower_1: float | None = None
    upper_2: float | None = None
    lower_2: float | None = None


class VWAPSeries(DomainModel):
    """A VWAP reading over one symbol/timeframe window.

    ``points`` are ordered by time and 1:1 with the candles that carried
    volume from the anchor onward (a candle with no accumulated volume behind
    it yet has no defined average, so it contributes no point).

    ``anchor_timestamp`` is the start of the *most recent* accumulation in
    ``points`` — the one still running at the live edge, and the only one whose
    value is a live reading. Earlier segments are delimited by each point's own
    ``anchor_timestamp``.

    ``label`` is a short human-readable name for what the series is anchored to
    (e.g. ``"Session"``, ``"CHoCH ▼"``, ``"Sweep"``), carried so a consumer can
    tag the line without re-deriving the anchor's meaning.
    """

    symbol: str
    timeframe: TimeFrame
    anchor: VWAPAnchor
    anchor_timestamp: datetime
    label: str = ""
    band_multipliers: list[float] = Field(default_factory=list)
    points: list[VWAPPoint]
    #: The average is built from per-candle typical prices, not per-trade
    #: prices. See the module docstring on what that approximation costs.
    estimated: bool = True

    @model_validator(mode="after")
    def _check_points(self) -> Self:
        if not self.points:
            raise ValueError("a VWAP series must have at least one point")
        return self

    @property
    def value(self) -> float:
        """The latest reading — the average paid by the running accumulation."""
        return self.points[-1].value
