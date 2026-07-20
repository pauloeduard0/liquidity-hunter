"""Tests for `liquidity_hunter.app.narrative.NarrativeEngine`."""

from datetime import UTC, datetime, timedelta

from liquidity_hunter.app.dashboard_data import DashboardData
from liquidity_hunter.app.narrative import NarrativeEngine
from liquidity_hunter.core.domain import (
    AnomalySeverity,
    BehaviorDivergence,
    Candle,
    DivergenceType,
    LiquiditySide,
    LiquidityZone,
    LiquidityZoneType,
    ManipulationCycle,
    ManipulationCycleStatus,
    ManipulationPhase,
    MarketDirection,
    MarketStructure,
    NarrativeEventType,
    StructureEvent,
    StructureScope,
    TimeFrame,
)
from liquidity_hunter.psychology import RetailBiasEstimate

T0 = datetime(2024, 1, 1, tzinfo=UTC)
H1 = timedelta(hours=1)


def _candle(i: int, price: float = 100.0) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * i,
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=10.0,
        taker_buy_volume=5.0,
    )


def _bias() -> RetailBiasEstimate:
    return RetailBiasEstimate(
        symbol="BTCUSDT",
        generated_at=T0,
        dominant_side="neutral",
        confidence=50.0,
        explanation="Neutral.",
    )


def _minimal_data(**overrides: object) -> DashboardData:
    defaults: dict[str, object] = {
        "symbol": "BTCUSDT",
        "timeframe": TimeFrame.H1,
        "candles": [_candle(0)],
        "current_price": 100.0,
        "higher_timeframe_direction": MarketDirection.NEUTRAL,
        "liquidity_zones": [],
        "ranked_zones": [],
        "market_structure_events": [],
        "internal_structure_events": [],
        "retail_bias": _bias(),
        "poi_zones": [],
        "manipulation_cycles": [],
        "behavior_divergences": [],
        "volume_spread_signals": [],
    }
    defaults.update(overrides)
    return DashboardData(**defaults)  # type: ignore[arg-type]


# ── Empty data ──────────────────────────────────────────────────────

def test_empty_data_produces_narrative() -> None:
    data = _minimal_data()
    narrative = NarrativeEngine().build(data)

    assert narrative.symbol == "BTCUSDT"
    assert narrative.timeframe == TimeFrame.H1
    assert narrative.phase is None
    assert narrative.timeline == []
    assert narrative.anomalies == []
    assert narrative.confluence_total >= 0


# ── Timeline from structure events ──────────────────────────────────

def test_structure_events_appear_in_timeline() -> None:
    bos = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 5,
        event=StructureEvent.BREAK_OF_STRUCTURE,
        direction=MarketDirection.BULLISH,
        price_level=105.0,
        reference_price_level=103.0,
        scope=StructureScope.MAJOR,
    )
    choch = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 10,
        event=StructureEvent.CHANGE_OF_CHARACTER,
        direction=MarketDirection.BEARISH,
        price_level=101.0,
        reference_price_level=104.0,
        reference_timestamp=T0 + H1 * 7,
        scope=StructureScope.MAJOR,
    )
    sweep = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 12,
        event=StructureEvent.LIQUIDITY_SWEEP,
        direction=MarketDirection.BEARISH,
        price_level=99.0,
        reference_price_level=100.0,
        scope=StructureScope.MAJOR,
    )
    data = _minimal_data(market_structure_events=[bos, choch, sweep])
    narrative = NarrativeEngine().build(data)

    assert len(narrative.timeline) == 3
    assert narrative.timeline[0].event_type == NarrativeEventType.STRUCTURE_BREAK
    assert narrative.timeline[1].event_type == NarrativeEventType.STRUCTURE_BREAK
    assert narrative.timeline[2].event_type == NarrativeEventType.SWEEP
    assert narrative.timeline[0].timestamp < narrative.timeline[1].timestamp


# ── Timeline from manipulation cycles ───────────────────────────────

def test_manipulation_cycle_produces_timeline_events() -> None:
    mc = ManipulationCycle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        phase=ManipulationPhase.EXPANSION,
        status=ManipulationCycleStatus.CONFIRMED,
        target_zone_price_low=99.0,
        target_zone_price_high=100.0,
        target_zone_type=LiquidityZoneType.EQUAL_LOWS,
        target_zone_side=LiquiditySide.SELL_SIDE,
        accumulation_start=T0,
        accumulation_end=T0 + H1 * 10,
        consolidation_candles=10,
        sweep_timestamp=T0 + H1 * 11,
        sweep_extreme=98.5,
        sweep_volume_delta=-2.0,
        expansion_timestamp=T0 + H1 * 13,
        expansion_price=105.0,
        expansion_volume_delta=3.0,
    )
    data = _minimal_data(manipulation_cycles=[mc])
    narrative = NarrativeEngine().build(data)

    types = [e.event_type for e in narrative.timeline]
    assert NarrativeEventType.CONSOLIDATION in types
    assert NarrativeEventType.SWEEP in types
    assert NarrativeEventType.EXPANSION in types


# ── Timeline from behavior divergences ──────────────────────────────

def test_behavior_divergence_mapped_to_timeline() -> None:
    bd = BehaviorDivergence(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 5,
        divergence_type=DivergenceType.DISTRIBUTION,
        direction=MarketDirection.BULLISH,
        price_level=105.0,
        volume_delta_avg=-1.5,
        price_change_pct=0.02,
        confidence=75.0,
        description="Price rising with negative VD near buy-side zone.",
    )
    data = _minimal_data(behavior_divergences=[bd])
    narrative = NarrativeEngine().build(data)

    assert len(narrative.timeline) == 1
    assert narrative.timeline[0].event_type == NarrativeEventType.DISTRIBUTION
    assert narrative.timeline[0].source_layer == "behavior_divergence"


# ── Timeline ordering ───────────────────────────────────────────────

def test_timeline_sorted_chronologically() -> None:
    bos = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 10,
        event=StructureEvent.BREAK_OF_STRUCTURE,
        direction=MarketDirection.BULLISH,
        price_level=105.0,
        reference_price_level=103.0,
        scope=StructureScope.MAJOR,
    )
    bd = BehaviorDivergence(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 5,
        divergence_type=DivergenceType.ACCUMULATION,
        direction=MarketDirection.BEARISH,
        price_level=98.0,
        volume_delta_avg=1.5,
        price_change_pct=-0.02,
        confidence=70.0,
        description="Positive VD despite falling price.",
    )
    data = _minimal_data(market_structure_events=[bos], behavior_divergences=[bd])
    narrative = NarrativeEngine().build(data)

    assert len(narrative.timeline) == 2
    assert narrative.timeline[0].timestamp < narrative.timeline[1].timestamp
    assert narrative.timeline[0].source_layer == "behavior_divergence"


# ── Phase detection ─────────────────────────────────────────────────

def test_phase_from_active_manipulation_cycle() -> None:
    mc = ManipulationCycle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        phase=ManipulationPhase.ACCUMULATION,
        status=ManipulationCycleStatus.IN_PROGRESS,
        target_zone_price_low=99.0,
        target_zone_price_high=100.0,
        target_zone_type=LiquidityZoneType.EQUAL_LOWS,
        target_zone_side=LiquiditySide.SELL_SIDE,
        accumulation_start=T0,
        accumulation_end=T0 + H1 * 10,
        consolidation_candles=10,
    )
    data = _minimal_data(manipulation_cycles=[mc])
    narrative = NarrativeEngine().build(data)

    assert narrative.phase == ManipulationPhase.ACCUMULATION


def test_phase_none_when_no_active_cycle() -> None:
    mc = ManipulationCycle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        phase=ManipulationPhase.EXPANSION,
        status=ManipulationCycleStatus.CONFIRMED,
        target_zone_price_low=99.0,
        target_zone_price_high=100.0,
        target_zone_type=LiquidityZoneType.EQUAL_LOWS,
        target_zone_side=LiquiditySide.SELL_SIDE,
        accumulation_start=T0,
        accumulation_end=T0 + H1 * 10,
        consolidation_candles=10,
        sweep_timestamp=T0 + H1 * 11,
        sweep_extreme=98.5,
        expansion_timestamp=T0 + H1 * 13,
        expansion_price=105.0,
    )
    data = _minimal_data(manipulation_cycles=[mc])
    narrative = NarrativeEngine().build(data)

    assert narrative.phase is None


# ── Anomaly detection ───────────────────────────────────────────────

def test_expansion_exhaustion_anomaly() -> None:
    mc = ManipulationCycle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        phase=ManipulationPhase.EXPANSION,
        status=ManipulationCycleStatus.IN_PROGRESS,
        target_zone_price_low=99.0,
        target_zone_price_high=100.0,
        target_zone_type=LiquidityZoneType.EQUAL_LOWS,
        target_zone_side=LiquiditySide.SELL_SIDE,
        accumulation_start=T0,
        accumulation_end=T0 + H1 * 10,
        consolidation_candles=10,
        sweep_timestamp=T0 + H1 * 11,
        sweep_extreme=98.5,
        expansion_timestamp=T0 + H1 * 13,
        expansion_price=105.0,
    )
    bd = BehaviorDivergence(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 14,
        divergence_type=DivergenceType.EXHAUSTION,
        direction=MarketDirection.BULLISH,
        price_level=106.0,
        volume_delta_avg=0.2,
        price_change_pct=0.01,
        confidence=65.0,
        description="VD declining after BOS.",
    )
    data = _minimal_data(manipulation_cycles=[mc], behavior_divergences=[bd])
    narrative = NarrativeEngine().build(data)

    assert len(narrative.anomalies) == 1
    assert narrative.anomalies[0].severity == AnomalySeverity.HIGH
    assert "momentum" in narrative.anomalies[0].description.lower()


def test_accumulation_distribution_anomaly() -> None:
    mc = ManipulationCycle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        phase=ManipulationPhase.ACCUMULATION,
        status=ManipulationCycleStatus.IN_PROGRESS,
        target_zone_price_low=99.0,
        target_zone_price_high=100.0,
        target_zone_type=LiquidityZoneType.EQUAL_LOWS,
        target_zone_side=LiquiditySide.SELL_SIDE,
        accumulation_start=T0,
        accumulation_end=T0 + H1 * 10,
        consolidation_candles=10,
    )
    bd = BehaviorDivergence(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 5,
        divergence_type=DivergenceType.DISTRIBUTION,
        direction=MarketDirection.BULLISH,
        price_level=101.0,
        volume_delta_avg=-1.0,
        price_change_pct=0.01,
        confidence=60.0,
        description="Negative VD despite rising price.",
    )
    data = _minimal_data(manipulation_cycles=[mc], behavior_divergences=[bd])
    narrative = NarrativeEngine().build(data)

    assert len(narrative.anomalies) == 1
    assert narrative.anomalies[0].severity == AnomalySeverity.MEDIUM


def test_no_anomaly_when_no_conflict() -> None:
    data = _minimal_data()
    narrative = NarrativeEngine().build(data)
    assert narrative.anomalies == []


# ── Confluence ──────────────────────────────────────────────────────

def test_confluence_counts_agreeing_layers() -> None:
    bos = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 5,
        event=StructureEvent.BREAK_OF_STRUCTURE,
        direction=MarketDirection.BULLISH,
        price_level=105.0,
        reference_price_level=103.0,
        scope=StructureScope.MAJOR,
    )
    data = _minimal_data(
        market_structure_events=[bos],
        higher_timeframe_direction=MarketDirection.BULLISH,
    )
    narrative = NarrativeEngine().build(data)

    assert narrative.confluence_count >= 2
    assert narrative.confluence_total >= 2


# ── Summary generation ──────────────────────────────────────────────

def test_neutral_summary_mentions_structure() -> None:
    bos = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 5,
        event=StructureEvent.BREAK_OF_STRUCTURE,
        direction=MarketDirection.BULLISH,
        price_level=105.0,
        reference_price_level=103.0,
        scope=StructureScope.MAJOR,
    )
    data = _minimal_data(market_structure_events=[bos])
    narrative = NarrativeEngine().build(data)

    assert "structure" in narrative.summary.lower() or "bullish" in narrative.summary.lower()


def test_accumulation_summary_smart_money_tone() -> None:
    mc = ManipulationCycle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        phase=ManipulationPhase.ACCUMULATION,
        status=ManipulationCycleStatus.IN_PROGRESS,
        target_zone_price_low=99.0,
        target_zone_price_high=100.0,
        target_zone_type=LiquidityZoneType.EQUAL_LOWS,
        target_zone_side=LiquiditySide.SELL_SIDE,
        accumulation_start=T0,
        accumulation_end=T0 + H1 * 10,
        consolidation_candles=10,
        accumulation_avg_volume_delta=0.5,
    )
    data = _minimal_data(manipulation_cycles=[mc])
    narrative = NarrativeEngine().build(data)

    s = narrative.summary.lower()
    assert "smart money" in s
    assert "absorbing" in s
    assert "vd positive" in s
    assert "consolidation" in s


def test_accumulation_summary_negative_vd() -> None:
    mc = ManipulationCycle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        phase=ManipulationPhase.ACCUMULATION,
        status=ManipulationCycleStatus.IN_PROGRESS,
        target_zone_price_low=99.0,
        target_zone_price_high=100.0,
        target_zone_type=LiquidityZoneType.EQUAL_LOWS,
        target_zone_side=LiquiditySide.SELL_SIDE,
        accumulation_start=T0,
        accumulation_end=T0 + H1 * 10,
        consolidation_candles=10,
        accumulation_avg_volume_delta=-0.3,
    )
    data = _minimal_data(manipulation_cycles=[mc])
    narrative = NarrativeEngine().build(data)

    assert "supply being absorbed" in narrative.summary.lower()


def test_manipulation_summary_institutional_tone() -> None:
    mc = ManipulationCycle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        phase=ManipulationPhase.MANIPULATION,
        status=ManipulationCycleStatus.IN_PROGRESS,
        target_zone_price_low=99.0,
        target_zone_price_high=100.0,
        target_zone_type=LiquidityZoneType.EQUAL_LOWS,
        target_zone_side=LiquiditySide.SELL_SIDE,
        accumulation_start=T0,
        accumulation_end=T0 + H1 * 10,
        consolidation_candles=10,
        sweep_timestamp=T0 + H1 * 11,
        sweep_extreme=98.5,
        sweep_volume_delta=-3.0,
    )
    data = _minimal_data(manipulation_cycles=[mc])
    narrative = NarrativeEngine().build(data)

    s = narrative.summary.lower()
    assert "stops swept" in s
    assert "cascading liquidation" in s
    assert "expansion" in s


def test_manipulation_summary_retail_trap() -> None:
    mc = ManipulationCycle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        phase=ManipulationPhase.MANIPULATION,
        status=ManipulationCycleStatus.IN_PROGRESS,
        target_zone_price_low=99.0,
        target_zone_price_high=100.0,
        target_zone_type=LiquidityZoneType.EQUAL_LOWS,
        target_zone_side=LiquiditySide.SELL_SIDE,
        accumulation_start=T0,
        accumulation_end=T0 + H1 * 10,
        consolidation_candles=10,
        sweep_timestamp=T0 + H1 * 11,
        sweep_extreme=98.5,
    )
    bias = RetailBiasEstimate(
        symbol="BTCUSDT",
        generated_at=T0,
        dominant_side="short",
        confidence=75.0,
        explanation="Retail shorting the dip.",
    )
    data = _minimal_data(manipulation_cycles=[mc], retail_bias=bias)
    narrative = NarrativeEngine().build(data)

    assert "retail trapped short" in narrative.summary.lower()


def test_expansion_summary_with_vd_and_resolution() -> None:
    mc = ManipulationCycle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        phase=ManipulationPhase.EXPANSION,
        status=ManipulationCycleStatus.IN_PROGRESS,
        target_zone_price_low=99.0,
        target_zone_price_high=100.0,
        target_zone_type=LiquidityZoneType.EQUAL_LOWS,
        target_zone_side=LiquiditySide.SELL_SIDE,
        accumulation_start=T0,
        accumulation_end=T0 + H1 * 10,
        consolidation_candles=10,
        sweep_timestamp=T0 + H1 * 11,
        sweep_extreme=98.5,
        expansion_timestamp=T0 + H1 * 13,
        expansion_price=105.0,
        expansion_volume_delta=3.0,
    )
    data = _minimal_data(manipulation_cycles=[mc])
    narrative = NarrativeEngine().build(data)

    s = narrative.summary.lower()
    assert "impulsive move" in s
    assert "sustained vd" in s
    assert "institutional direction" in s
    assert "sweep resolved" in s


def test_failed_cycle_summary() -> None:
    candles = [_candle(i) for i in range(40)]
    mc = ManipulationCycle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        phase=ManipulationPhase.EXPANSION,
        status=ManipulationCycleStatus.FAILED,
        target_zone_price_low=99.0,
        target_zone_price_high=100.0,
        target_zone_type=LiquidityZoneType.EQUAL_LOWS,
        target_zone_side=LiquiditySide.SELL_SIDE,
        accumulation_start=T0 + H1 * 28,
        accumulation_end=T0 + H1 * 35,
        consolidation_candles=7,
        sweep_timestamp=T0 + H1 * 36,
        sweep_extreme=98.5,
    )
    data = _minimal_data(candles=candles, manipulation_cycles=[mc])
    narrative = NarrativeEngine().build(data)

    s = narrative.summary.lower()
    assert "failed" in s
    assert "invalidated" in s
    assert "eql" in s


def test_stale_failed_cycle_ignored_in_summary() -> None:
    """A failed cycle from the beginning of the window should not drive
    the summary — the narrative should fall through to neutral."""
    candles = [_candle(i) for i in range(100)]
    mc = ManipulationCycle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        phase=ManipulationPhase.EXPANSION,
        status=ManipulationCycleStatus.FAILED,
        target_zone_price_low=99.0,
        target_zone_price_high=100.0,
        target_zone_type=LiquidityZoneType.EQUAL_LOWS,
        target_zone_side=LiquiditySide.SELL_SIDE,
        accumulation_start=T0,
        accumulation_end=T0 + H1 * 10,
        consolidation_candles=10,
        sweep_timestamp=T0 + H1 * 11,
        sweep_extreme=98.5,
    )
    data = _minimal_data(candles=candles, manipulation_cycles=[mc])
    narrative = NarrativeEngine().build(data)

    s = narrative.summary.lower()
    assert "failed" not in s
    assert "invalidated" not in s


def test_htf_aligned_mentioned_in_summary() -> None:
    mc = ManipulationCycle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        phase=ManipulationPhase.ACCUMULATION,
        status=ManipulationCycleStatus.IN_PROGRESS,
        target_zone_price_low=99.0,
        target_zone_price_high=100.0,
        target_zone_type=LiquidityZoneType.EQUAL_LOWS,
        target_zone_side=LiquiditySide.SELL_SIDE,
        accumulation_start=T0,
        accumulation_end=T0 + H1 * 10,
        consolidation_candles=10,
    )
    data = _minimal_data(
        manipulation_cycles=[mc],
        higher_timeframe_direction=MarketDirection.BULLISH,
    )
    narrative = NarrativeEngine().build(data)
    assert "htf trend aligned" in narrative.summary.lower()


def test_htf_divergent_mentioned_in_summary() -> None:
    mc = ManipulationCycle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        phase=ManipulationPhase.ACCUMULATION,
        status=ManipulationCycleStatus.IN_PROGRESS,
        target_zone_price_low=99.0,
        target_zone_price_high=100.0,
        target_zone_type=LiquidityZoneType.EQUAL_LOWS,
        target_zone_side=LiquiditySide.SELL_SIDE,
        accumulation_start=T0,
        accumulation_end=T0 + H1 * 10,
        consolidation_candles=10,
    )
    data = _minimal_data(
        manipulation_cycles=[mc],
        higher_timeframe_direction=MarketDirection.BEARISH,
    )
    narrative = NarrativeEngine().build(data)
    assert "htf trend diverges" in narrative.summary.lower()


def test_retail_context_in_neutral_summary() -> None:
    bias = RetailBiasEstimate(
        symbol="BTCUSDT",
        generated_at=T0,
        dominant_side="long",
        confidence=80.0,
        explanation="Retail buying the breakout.",
    )
    bos = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 5,
        event=StructureEvent.BREAK_OF_STRUCTURE,
        direction=MarketDirection.BULLISH,
        price_level=105.0,
        reference_price_level=103.0,
        scope=StructureScope.MAJOR,
    )
    data = _minimal_data(retail_bias=bias, market_structure_events=[bos])
    narrative = NarrativeEngine().build(data)

    assert "retail crowded long" in narrative.summary.lower()


def test_empty_data_summary() -> None:
    data = _minimal_data()
    narrative = NarrativeEngine().build(data)
    assert len(narrative.summary) > 0


# ── Deduplication ───────────────────────────────────────────────────

def test_duplicate_sweep_at_same_timestamp_keeps_richer_source() -> None:
    """A sweep from market_structure and manipulation_cycle at the same
    timestamp should produce a single event from the higher-priority source."""
    ts = T0 + H1 * 11
    ms_sweep = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=ts,
        event=StructureEvent.LIQUIDITY_SWEEP,
        direction=MarketDirection.BEARISH,
        price_level=98.0,
        reference_price_level=99.0,
        scope=StructureScope.MAJOR,
    )
    mc = ManipulationCycle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        phase=ManipulationPhase.MANIPULATION,
        status=ManipulationCycleStatus.IN_PROGRESS,
        target_zone_price_low=99.0,
        target_zone_price_high=100.0,
        target_zone_type=LiquidityZoneType.EQUAL_LOWS,
        target_zone_side=LiquiditySide.SELL_SIDE,
        accumulation_start=T0,
        accumulation_end=T0 + H1 * 10,
        consolidation_candles=10,
        sweep_timestamp=ts,
        sweep_extreme=98.5,
        sweep_volume_delta=-2.0,
    )
    data = _minimal_data(
        market_structure_events=[ms_sweep],
        manipulation_cycles=[mc],
    )
    narrative = NarrativeEngine().build(data)

    sweep_events = [
        e for e in narrative.timeline
        if e.event_type == NarrativeEventType.SWEEP and e.timestamp == ts
    ]
    assert len(sweep_events) == 1
    assert sweep_events[0].source_layer == "manipulation_cycle"


def test_different_event_types_at_same_timestamp_not_deduped() -> None:
    ts = T0 + H1 * 5
    bos = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=ts,
        event=StructureEvent.BREAK_OF_STRUCTURE,
        direction=MarketDirection.BULLISH,
        price_level=105.0,
        reference_price_level=103.0,
        scope=StructureScope.MAJOR,
    )
    bd = BehaviorDivergence(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=ts,
        divergence_type=DivergenceType.EXHAUSTION,
        direction=MarketDirection.BULLISH,
        price_level=105.0,
        volume_delta_avg=0.2,
        price_change_pct=0.01,
        confidence=60.0,
        description="VD declining after BOS.",
    )
    data = _minimal_data(
        market_structure_events=[bos],
        behavior_divergences=[bd],
    )
    narrative = NarrativeEngine().build(data)

    assert len(narrative.timeline) == 2


# ── Internal structure events ───────────────────────────────────────

def test_internal_structure_events_in_timeline() -> None:
    internal_bos = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 3,
        event=StructureEvent.BREAK_OF_STRUCTURE,
        direction=MarketDirection.BULLISH,
        price_level=102.0,
        reference_price_level=101.0,
        scope=StructureScope.INTERNAL,
    )
    data = _minimal_data(internal_structure_events=[internal_bos])
    narrative = NarrativeEngine().build(data)

    assert len(narrative.timeline) == 1
    assert narrative.timeline[0].source_layer == "internal_structure"
    assert "(internal)" in narrative.timeline[0].description


def test_internal_and_major_structure_both_appear() -> None:
    major_bos = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 5,
        event=StructureEvent.BREAK_OF_STRUCTURE,
        direction=MarketDirection.BULLISH,
        price_level=105.0,
        reference_price_level=103.0,
        scope=StructureScope.MAJOR,
    )
    internal_choch = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 8,
        event=StructureEvent.CHANGE_OF_CHARACTER,
        direction=MarketDirection.BEARISH,
        price_level=101.0,
        reference_price_level=104.0,
        reference_timestamp=T0 + H1 * 6,
        scope=StructureScope.INTERNAL,
    )
    data = _minimal_data(
        market_structure_events=[major_bos],
        internal_structure_events=[internal_choch],
    )
    narrative = NarrativeEngine().build(data)

    assert len(narrative.timeline) == 2
    sources = {e.source_layer for e in narrative.timeline}
    assert sources == {"market_structure", "internal_structure"}


# ── Rich descriptions ───────────────────────────────────────────────

def test_bos_description_contains_trend_continuation() -> None:
    bos = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 5,
        event=StructureEvent.BREAK_OF_STRUCTURE,
        direction=MarketDirection.BULLISH,
        price_level=105.0,
        reference_price_level=103.0,
        scope=StructureScope.MAJOR,
    )
    data = _minimal_data(market_structure_events=[bos])
    narrative = NarrativeEngine().build(data)

    desc = narrative.timeline[0].description
    assert "103.00" in desc
    assert "trend continuation" in desc.lower()


def test_choch_description_mentions_reversal() -> None:
    choch = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 10,
        event=StructureEvent.CHANGE_OF_CHARACTER,
        direction=MarketDirection.BEARISH,
        price_level=101.0,
        reference_price_level=104.0,
        reference_timestamp=T0 + H1 * 7,
        scope=StructureScope.MAJOR,
    )
    data = _minimal_data(market_structure_events=[choch])
    narrative = NarrativeEngine().build(data)

    desc = narrative.timeline[0].description
    assert "reversing" in desc.lower()
    assert "104.00" in desc


def test_consolidation_description_includes_zone_type_and_vd() -> None:
    mc = ManipulationCycle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        phase=ManipulationPhase.ACCUMULATION,
        status=ManipulationCycleStatus.IN_PROGRESS,
        target_zone_price_low=99.0,
        target_zone_price_high=100.0,
        target_zone_type=LiquidityZoneType.EQUAL_LOWS,
        target_zone_side=LiquiditySide.SELL_SIDE,
        accumulation_start=T0,
        accumulation_end=T0 + H1 * 10,
        consolidation_candles=10,
        accumulation_avg_volume_delta=0.5,
    )
    data = _minimal_data(manipulation_cycles=[mc])
    narrative = NarrativeEngine().build(data)

    consol = [e for e in narrative.timeline if e.event_type == NarrativeEventType.CONSOLIDATION]
    assert len(consol) == 1
    desc = consol[0].description
    assert "EQL" in desc
    assert "10 candles" in desc
    assert "VD" in desc


def test_sweep_description_includes_vd_context() -> None:
    mc = ManipulationCycle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        phase=ManipulationPhase.EXPANSION,
        status=ManipulationCycleStatus.CONFIRMED,
        target_zone_price_low=99.0,
        target_zone_price_high=100.0,
        target_zone_type=LiquidityZoneType.EQUAL_LOWS,
        target_zone_side=LiquiditySide.SELL_SIDE,
        accumulation_start=T0,
        accumulation_end=T0 + H1 * 10,
        consolidation_candles=10,
        sweep_timestamp=T0 + H1 * 11,
        sweep_extreme=98.5,
        sweep_volume_delta=-2.0,
        expansion_timestamp=T0 + H1 * 13,
        expansion_price=105.0,
        expansion_volume_delta=3.0,
    )
    data = _minimal_data(manipulation_cycles=[mc])
    narrative = NarrativeEngine().build(data)

    sweep = [e for e in narrative.timeline if e.event_type == NarrativeEventType.SWEEP]
    assert len(sweep) == 1
    assert "VD:" in sweep[0].description
    assert "-2.0" in sweep[0].description


def test_expansion_description_includes_vd_and_resolution() -> None:
    mc = ManipulationCycle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        phase=ManipulationPhase.EXPANSION,
        status=ManipulationCycleStatus.CONFIRMED,
        target_zone_price_low=99.0,
        target_zone_price_high=100.0,
        target_zone_type=LiquidityZoneType.EQUAL_LOWS,
        target_zone_side=LiquiditySide.SELL_SIDE,
        accumulation_start=T0,
        accumulation_end=T0 + H1 * 10,
        consolidation_candles=10,
        sweep_timestamp=T0 + H1 * 11,
        sweep_extreme=98.5,
        expansion_timestamp=T0 + H1 * 13,
        expansion_price=105.0,
        expansion_volume_delta=3.0,
    )
    data = _minimal_data(manipulation_cycles=[mc])
    narrative = NarrativeEngine().build(data)

    exp = [e for e in narrative.timeline if e.event_type == NarrativeEventType.EXPANSION]
    assert len(exp) == 1
    desc = exp[0].description
    assert "VD:" in desc
    assert "sweep resolved" in desc.lower()


# ── Concentrated liquidity anomaly ──────────────────────────────────

def _zone(
    side: LiquiditySide,
    price_low: float,
    price_high: float,
    index: int = 0,
) -> LiquidityZone:
    return LiquidityZone(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        zone_type=LiquidityZoneType.EQUAL_HIGHS
        if side == LiquiditySide.BUY_SIDE
        else LiquidityZoneType.EQUAL_LOWS,
        side=side,
        price_low=price_low,
        price_high=price_high,
        formed_at=T0 + H1 * index,
        strength=0.5,
    )


def test_concentrated_liquidity_anomaly() -> None:
    zones = [
        _zone(LiquiditySide.SELL_SIDE, 99.0, 99.5, 0),
        _zone(LiquiditySide.SELL_SIDE, 99.2, 99.7, 1),
        _zone(LiquiditySide.SELL_SIDE, 99.4, 99.9, 2),
    ]
    data = _minimal_data(liquidity_zones=zones)
    narrative = NarrativeEngine().build(data)

    concentrated = [
        a
        for a in narrative.anomalies
        if "concentrated" in a.description.lower()
    ]
    assert len(concentrated) == 1
    assert concentrated[0].severity == AnomalySeverity.HIGH
    assert "sweep probability" in concentrated[0].description.lower()


def test_concentrated_liquidity_medium_severity_for_two_zones() -> None:
    zones = [
        _zone(LiquiditySide.BUY_SIDE, 100.0, 100.5, 0),
        _zone(LiquiditySide.BUY_SIDE, 100.3, 100.8, 1),
    ]
    data = _minimal_data(liquidity_zones=zones)
    narrative = NarrativeEngine().build(data)

    concentrated = [
        a
        for a in narrative.anomalies
        if "concentrated" in a.description.lower()
    ]
    assert len(concentrated) == 1
    assert concentrated[0].severity == AnomalySeverity.MEDIUM


def test_no_concentrated_anomaly_for_distant_zones() -> None:
    zones = [
        _zone(LiquiditySide.SELL_SIDE, 90.0, 90.5, 0),
        _zone(LiquiditySide.SELL_SIDE, 110.0, 110.5, 1),
    ]
    data = _minimal_data(liquidity_zones=zones)
    narrative = NarrativeEngine().build(data)

    concentrated = [
        a
        for a in narrative.anomalies
        if "concentrated" in a.description.lower()
    ]
    assert len(concentrated) == 0


def test_no_concentrated_anomaly_for_different_sides() -> None:
    zones = [
        _zone(LiquiditySide.BUY_SIDE, 100.0, 100.5, 0),
        _zone(LiquiditySide.SELL_SIDE, 100.2, 100.7, 1),
    ]
    data = _minimal_data(liquidity_zones=zones)
    narrative = NarrativeEngine().build(data)

    concentrated = [
        a
        for a in narrative.anomalies
        if "concentrated" in a.description.lower()
    ]
    assert len(concentrated) == 0


# ── Unconfirmed CHoCH anomaly ───────────────────────────────────────

def test_unconfirmed_choch_anomaly() -> None:
    choch = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 10,
        event=StructureEvent.CHANGE_OF_CHARACTER,
        direction=MarketDirection.BEARISH,
        price_level=101.0,
        reference_price_level=104.0,
        reference_timestamp=T0 + H1 * 7,
        scope=StructureScope.MAJOR,
    )
    data = _minimal_data(market_structure_events=[choch])
    narrative = NarrativeEngine().build(data)

    unconfirmed = [
        a
        for a in narrative.anomalies
        if "unconfirmed" in a.description.lower()
    ]
    assert len(unconfirmed) == 1
    assert unconfirmed[0].severity == AnomalySeverity.MEDIUM
    assert "bearish" in unconfirmed[0].description.lower()


def test_choch_confirmed_by_subsequent_bos_no_anomaly() -> None:
    choch = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 10,
        event=StructureEvent.CHANGE_OF_CHARACTER,
        direction=MarketDirection.BEARISH,
        price_level=101.0,
        reference_price_level=104.0,
        reference_timestamp=T0 + H1 * 7,
        scope=StructureScope.MAJOR,
    )
    bos = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 15,
        event=StructureEvent.BREAK_OF_STRUCTURE,
        direction=MarketDirection.BEARISH,
        price_level=99.0,
        reference_price_level=100.0,
        scope=StructureScope.MAJOR,
    )
    data = _minimal_data(market_structure_events=[choch, bos])
    narrative = NarrativeEngine().build(data)

    unconfirmed = [
        a
        for a in narrative.anomalies
        if "unconfirmed" in a.description.lower()
    ]
    assert len(unconfirmed) == 0


def test_choch_not_confirmed_by_opposite_bos() -> None:
    choch = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 10,
        event=StructureEvent.CHANGE_OF_CHARACTER,
        direction=MarketDirection.BEARISH,
        price_level=101.0,
        reference_price_level=104.0,
        reference_timestamp=T0 + H1 * 7,
        scope=StructureScope.MAJOR,
    )
    bos_opposite = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 15,
        event=StructureEvent.BREAK_OF_STRUCTURE,
        direction=MarketDirection.BULLISH,
        price_level=106.0,
        reference_price_level=105.0,
        scope=StructureScope.MAJOR,
    )
    data = _minimal_data(market_structure_events=[choch, bos_opposite])
    narrative = NarrativeEngine().build(data)

    unconfirmed = [
        a
        for a in narrative.anomalies
        if "unconfirmed" in a.description.lower()
    ]
    assert len(unconfirmed) == 1


# ── BOS without VD anomaly ──────────────────────────────────────────

def test_bos_without_vd_anomaly() -> None:
    bos = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 5,
        event=StructureEvent.BREAK_OF_STRUCTURE,
        direction=MarketDirection.BULLISH,
        price_level=105.0,
        reference_price_level=103.0,
        scope=StructureScope.MAJOR,
    )
    bd = BehaviorDivergence(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 7,
        divergence_type=DivergenceType.EXHAUSTION,
        direction=MarketDirection.BULLISH,
        price_level=106.0,
        volume_delta_avg=0.1,
        price_change_pct=0.01,
        confidence=60.0,
        description="VD declining after BOS.",
    )
    data = _minimal_data(
        market_structure_events=[bos],
        behavior_divergences=[bd],
    )
    narrative = NarrativeEngine().build(data)

    vd_anomalies = [
        a
        for a in narrative.anomalies
        if "institutional conviction" in a.description.lower()
    ]
    assert len(vd_anomalies) == 1
    assert vd_anomalies[0].severity == AnomalySeverity.MEDIUM


def test_bos_without_vd_not_fired_during_expansion() -> None:
    """When there's an active expansion cycle, the expansion_exhaustion
    anomaly fires instead — avoid double-flagging."""
    bos = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 5,
        event=StructureEvent.BREAK_OF_STRUCTURE,
        direction=MarketDirection.BULLISH,
        price_level=105.0,
        reference_price_level=103.0,
        scope=StructureScope.MAJOR,
    )
    mc = ManipulationCycle(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        direction=MarketDirection.BULLISH,
        phase=ManipulationPhase.EXPANSION,
        status=ManipulationCycleStatus.IN_PROGRESS,
        target_zone_price_low=99.0,
        target_zone_price_high=100.0,
        target_zone_type=LiquidityZoneType.EQUAL_LOWS,
        target_zone_side=LiquiditySide.SELL_SIDE,
        accumulation_start=T0,
        accumulation_end=T0 + H1 * 3,
        consolidation_candles=3,
        sweep_timestamp=T0 + H1 * 4,
        sweep_extreme=98.5,
        expansion_timestamp=T0 + H1 * 5,
        expansion_price=105.0,
    )
    bd = BehaviorDivergence(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 7,
        divergence_type=DivergenceType.EXHAUSTION,
        direction=MarketDirection.BULLISH,
        price_level=106.0,
        volume_delta_avg=0.1,
        price_change_pct=0.01,
        confidence=60.0,
        description="VD declining after BOS.",
    )
    data = _minimal_data(
        market_structure_events=[bos],
        manipulation_cycles=[mc],
        behavior_divergences=[bd],
    )
    narrative = NarrativeEngine().build(data)

    vd_anomalies = [
        a
        for a in narrative.anomalies
        if "institutional conviction" in a.description.lower()
    ]
    assert len(vd_anomalies) == 0

    expansion_anomalies = [
        a
        for a in narrative.anomalies
        if "momentum" in a.description.lower()
    ]
    assert len(expansion_anomalies) == 1


def test_no_bos_vd_anomaly_without_exhaustion() -> None:
    bos = MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=T0 + H1 * 5,
        event=StructureEvent.BREAK_OF_STRUCTURE,
        direction=MarketDirection.BULLISH,
        price_level=105.0,
        reference_price_level=103.0,
        scope=StructureScope.MAJOR,
    )
    data = _minimal_data(market_structure_events=[bos])
    narrative = NarrativeEngine().build(data)

    vd_anomalies = [
        a
        for a in narrative.anomalies
        if "institutional conviction" in a.description.lower()
    ]
    assert len(vd_anomalies) == 0
