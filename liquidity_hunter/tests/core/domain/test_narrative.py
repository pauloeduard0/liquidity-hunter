"""Construction and validation tests for narrative domain entities."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from liquidity_hunter.core.domain import (
    AnomalySeverity,
    ManipulationPhase,
    MarketDirection,
    MarketNarrative,
    NarrativeAnomaly,
    NarrativeEvent,
    NarrativeEventType,
    TimeFrame,
)

NOW = datetime(2024, 1, 1, tzinfo=UTC)


def test_narrative_event_valid_construction() -> None:
    event = NarrativeEvent(
        timestamp=NOW,
        event_type=NarrativeEventType.STRUCTURE_BREAK,
        direction=MarketDirection.BULLISH,
        description="BOS bullish @ 104,200.00",
        source_layer="market_structure",
    )
    assert event.event_type == NarrativeEventType.STRUCTURE_BREAK
    assert event.direction == MarketDirection.BULLISH


def test_narrative_event_is_frozen() -> None:
    event = NarrativeEvent(
        timestamp=NOW,
        event_type=NarrativeEventType.SWEEP,
        direction=MarketDirection.BEARISH,
        description="Sweep bearish",
        source_layer="market_structure",
    )
    with pytest.raises(ValidationError):
        event.direction = MarketDirection.BULLISH  # type: ignore[misc]


def test_narrative_event_forbids_extra() -> None:
    with pytest.raises(ValidationError):
        NarrativeEvent(
            timestamp=NOW,
            event_type=NarrativeEventType.SWEEP,
            direction=MarketDirection.BEARISH,
            description="test",
            source_layer="test",
            extra_field="oops",  # type: ignore[call-arg]
        )


def test_narrative_anomaly_valid_construction() -> None:
    anomaly = NarrativeAnomaly(
        timestamp=NOW,
        expected="Sustained VD during expansion",
        observed="VD declining",
        description="Expansion losing momentum",
        severity=AnomalySeverity.HIGH,
    )
    assert anomaly.severity == AnomalySeverity.HIGH


def test_narrative_anomaly_is_frozen() -> None:
    anomaly = NarrativeAnomaly(
        timestamp=NOW,
        expected="x",
        observed="y",
        description="z",
        severity=AnomalySeverity.LOW,
    )
    with pytest.raises(ValidationError):
        anomaly.severity = AnomalySeverity.HIGH  # type: ignore[misc]


def test_market_narrative_valid_construction() -> None:
    narrative = MarketNarrative(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=NOW,
        phase=ManipulationPhase.ACCUMULATION,
        timeline=[],
        anomalies=[],
        summary="Accumulation in progress.",
        confluence_count=3,
        confluence_total=4,
    )
    assert narrative.phase == ManipulationPhase.ACCUMULATION
    assert narrative.confluence_count == 3
    assert narrative.confluence_total == 4


def test_market_narrative_no_phase() -> None:
    narrative = MarketNarrative(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=NOW,
        phase=None,
        summary="No active cycle.",
        confluence_count=0,
        confluence_total=0,
    )
    assert narrative.phase is None
    assert narrative.timeline == []
    assert narrative.anomalies == []


def test_market_narrative_rejects_negative_confluence() -> None:
    with pytest.raises(ValueError):
        MarketNarrative(
            symbol="BTCUSDT",
            timeframe=TimeFrame.H1,
            timestamp=NOW,
            summary="test",
            confluence_count=-1,
            confluence_total=0,
        )


def test_market_narrative_is_frozen() -> None:
    narrative = MarketNarrative(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=NOW,
        summary="test",
        confluence_count=0,
        confluence_total=0,
    )
    with pytest.raises(ValidationError):
        narrative.summary = "changed"  # type: ignore[misc]
