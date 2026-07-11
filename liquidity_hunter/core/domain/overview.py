"""Multi-timeframe structural overview models.

A :class:`MarketOverview` is a compact, per-timeframe reading of the standing
market structure for one symbol — which way each timeframe's internal
structure currently points, what the last structural event was, and how far
the liquidity hunt on that timeframe has progressed. It is a descriptive
"state ladder" (M5 → W1), not a signal: each entry states what is observed on
that timeframe, mirroring the trend/events the chart renders when the user
opens it.
"""

from datetime import datetime

from pydantic import Field

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import (
    LiquidityHuntPhase,
    MarketDirection,
    RetailPositioning,
    StructureEvent,
    TimeFrame,
)


class TimeframeOverview(DomainModel):
    """The standing structural state observed on a single timeframe.

    ``trend`` is the internal structure detector's state-machine trend for
    the production run of this timeframe — exactly the trend the chart shows
    when this timeframe is opened. ``last_event`` is the most recent
    non-provisional trend-relevant mark (BOS / CHoCH / ``CHOCH_FAILED``)
    within the visible window; ``forming_event`` is a provisional live-edge
    BOS/CHoCH (the dimmed ``BOS?``/``CHoCH?`` marks), when one is standing.
    The hunt fields summarize the timeframe's :class:`LiquidityHuntState`
    (computed against ``higher_timeframe_direction``).
    """

    timeframe: TimeFrame
    trend: MarketDirection
    current_price: float = Field(gt=0)
    candle_timestamp: datetime
    higher_timeframe: TimeFrame | None = None
    higher_timeframe_direction: MarketDirection | None = None
    last_event: StructureEvent | None = None
    last_event_direction: MarketDirection | None = None
    last_event_timestamp: datetime | None = None
    last_event_candles_ago: int | None = Field(default=None, ge=0)
    forming_event: StructureEvent | None = None
    forming_direction: MarketDirection | None = None
    hunt_phase: LiquidityHuntPhase = LiquidityHuntPhase.NONE
    hunted_side: RetailPositioning = RetailPositioning.NEUTRAL
    hunt_targets_captured: int = Field(default=0, ge=0)
    hunt_targets_total: int = Field(default=0, ge=0)


class MarketOverview(DomainModel):
    """Per-timeframe structural readings for one symbol, ordered fine → coarse."""

    symbol: str
    entries: list[TimeframeOverview]
