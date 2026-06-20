"""Market narrative domain entities."""

from datetime import datetime

from pydantic import Field

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import (
    AnomalySeverity,
    ManipulationPhase,
    MarketDirection,
    NarrativeEventType,
    TimeFrame,
)


class NarrativeEvent(DomainModel):
    """A single event in the narrative timeline.

    Represents a significant market observation mapped from one of the
    detection layers (structure, POI, manipulation cycles, behavior
    divergence) into a unified chronological sequence.
    """

    timestamp: datetime
    event_type: NarrativeEventType
    direction: MarketDirection
    description: str
    source_layer: str


class NarrativeAnomaly(DomainModel):
    """A contradiction between an expected pattern and what actually happened.

    Surfaces moments where multiple detection layers disagree — e.g. an
    expansion phase with declining volume delta, or accumulation signals
    during visible distribution.
    """

    timestamp: datetime
    expected: str
    observed: str
    description: str
    severity: AnomalySeverity


class MarketNarrative(DomainModel):
    """Synthesized institutional narrative for a symbol/timeframe snapshot.

    Connects the outputs of all detection layers into a coherent story:
    a chronological timeline of significant events, pattern anomalies,
    and a phase-aware summary.  This is a descriptive observation, not
    a trading recommendation.
    """

    symbol: str
    timeframe: TimeFrame
    timestamp: datetime
    phase: ManipulationPhase | None = None
    timeline: list[NarrativeEvent] = Field(default_factory=list)
    anomalies: list[NarrativeAnomaly] = Field(default_factory=list)
    summary: str
    confluence_count: int = Field(ge=0)
    confluence_total: int = Field(ge=0)
