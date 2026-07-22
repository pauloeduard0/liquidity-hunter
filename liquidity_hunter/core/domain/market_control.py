"""Market-control reading: who is in control, from CVD aggression × OI.

Cross-references taker aggression (Cumulative Volume Delta over a recent
window) with open interest to answer a single question — *who is initiating
the tape with conviction right now?* — and how risky it is to trade against
them. It is an observation about participation, not a signal.
"""

from datetime import datetime

from pydantic import Field

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import (
    MarketControlSide,
    OIRegime,
    TimeFrame,
)


class MarketControlPoint(DomainModel):
    """One candle's control reading, for the chart oscillator series.

    ``control_score`` is the signed conviction (``[-100, 100]``, positive =
    buyers), ``controller`` the credited side at that candle (only the
    OI-rising quadrants credit a side). Kept lightweight: the full context
    lives on the snapshot :class:`MarketControlState`.
    """

    timestamp: datetime
    control_score: float = Field(ge=-100.0, le=100.0)
    controller: MarketControlSide


class MarketControlState(DomainModel):
    """Who controls the tape, combining CVD aggression with open interest.

    The classic futures matrix, but read on the *aggression* axis (CVD slope)
    rather than price — so a move that ticks up on no real buying is not
    mistaken for buyer control:

    - buy aggression + OI rising  → ``LONG_BUILDUP``  → ``BUYERS`` in control
    - sell aggression + OI rising → ``SHORT_BUILDUP`` → ``SELLERS`` in control
    - buy aggression + OI falling → ``SHORT_COVERING``  → shorts closing (weak)
    - sell aggression + OI falling → ``LONG_LIQUIDATION`` → longs closing (weak)

    ``controller`` credits a side only in the OI-rising quadrants (fresh money
    behind the aggression). ``control_score`` is the signed *conviction
    oscillator* in ``[-100, 100]``: its sign is the aggressor side (positive =
    buyers), its magnitude is the conviction — amplified when OI confirms the
    aggression, attenuated when OI diverges (unwinding). ``fade_warning`` is
    ``True`` exactly when new money backs the aggression (``controller`` is not
    ``BALANCED``): trading against a conviction-backed side is the high-risk
    entry this reading exists to flag.
    """

    symbol: str
    timeframe: TimeFrame
    timestamp: datetime
    controller: MarketControlSide
    regime: OIRegime
    cvd_change: float  # net taker delta over the window (signed aggression, raw units)
    cvd_change_ratio: float = Field(ge=-1.0, le=1.0)  # cvd_change / window volume
    oi_change_pct: float  # fractional OI change over the window
    conviction: float = Field(ge=0.0, le=100.0)  # |control_score|, the oscillator height
    control_score: float = Field(ge=-100.0, le=100.0)  # signed: sign = side, |·| = conviction
    fade_warning: bool  # new money backs the aggression — entering against is high-risk
    window_candles: int = Field(gt=0)
    description: str
    # Per-candle control_score over the visible window, for the chart
    # oscillator. Only candles with OI coverage are present, 1:1 with those
    # candle timestamps; the last point corresponds to this snapshot.
    series: list[MarketControlPoint] = Field(default_factory=list)
