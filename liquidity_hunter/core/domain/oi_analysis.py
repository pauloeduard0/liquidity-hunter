"""Open-interest regime observations.

These entities describe how open interest moved *together with* price — the
joint reading that distinguishes a conviction move (new positions entering)
from an unwinding one (positions closing). They are observations about
market participation, not signals or recommendations.
"""

from datetime import datetime

from pydantic import Field

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import (
    MarketDirection,
    OIParticipation,
    OIRegime,
    StructureEvent,
    TimeFrame,
)


class OIRegimeReading(DomainModel):
    """The joint price/open-interest regime over the most recent window.

    ``price_change_pct`` and ``oi_change_pct`` are fractional changes over the
    ``window_candles`` window ending at ``timestamp``. ``intensity`` (0-100)
    scales with how far both displacements exceed their significance floors.
    """

    symbol: str
    timeframe: TimeFrame
    timestamp: datetime
    regime: OIRegime
    price_change_pct: float
    oi_change_pct: float
    window_candles: int = Field(gt=0)
    intensity: float = Field(ge=0.0, le=100.0)
    description: str


class OIQualifiedEvent(DomainModel):
    """Open-interest context measured around a market structure event.

    Qualifies a BOS/CHoCH/SWEEP with how open interest behaved into the
    break: ``NEW_MONEY`` (fresh positioning behind the move), ``COVERING``
    (the move is position unwinding), ``FLUSH`` (a sweep that force-closed
    leveraged positions), or ``FLAT``. ``oi_delta_pct`` is the fractional OI
    change over the event's measurement window.
    """

    symbol: str
    timeframe: TimeFrame
    event_timestamp: datetime
    event_type: StructureEvent
    direction: MarketDirection
    price_level: float
    oi_delta_pct: float
    participation: OIParticipation
    description: str


class OIAnalysis(DomainModel):
    """Aggregate open-interest analysis for one symbol/timeframe snapshot.

    ``current_regime`` is ``None`` when the OI series does not cover the
    recent window (e.g. a venue returning sparse history);
    ``qualified_events`` only contains events that fall inside OI coverage
    (``coverage_start``/``coverage_end``, the OI series' span).
    """

    symbol: str
    timeframe: TimeFrame
    current_regime: OIRegimeReading | None = None
    qualified_events: list[OIQualifiedEvent] = Field(default_factory=list)
    coverage_start: datetime | None = None
    coverage_end: datetime | None = None
