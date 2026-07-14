"""Tests for the consolidation detection and breakout staging post-passes."""

from datetime import UTC, datetime, timedelta

from liquidity_hunter.core.domain import (
    Candle,
    ConsolidationRange,
    ConsolidationStatus,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    StructureScope,
    TimeFrame,
)
from liquidity_hunter.liquidity.detectors.consolidation import (
    detect_consolidation_ranges,
    stage_breakout_events,
)

_START = datetime(2026, 1, 1, tzinfo=UTC)


def _candle(index: int, high: float, low: float, close: float) -> Candle:
    return Candle(
        symbol="TESTUSDT",
        timeframe=TimeFrame.H1,
        timestamp=_START + timedelta(hours=index),
        open=close,
        high=max(high, close),
        low=min(low, close),
        close=close,
        volume=1.0,
        taker_buy_volume=0.5,
    )


def _oscillating(count: int, start_index: int = 0) -> list[Candle]:
    """Candles alternating between the top (101) and bottom (99) of a box.

    Box height 2 around 100 (~2%); even candles touch the top edge zone,
    odd candles the bottom one.
    """
    candles = []
    for i in range(count):
        if (start_index + i) % 2 == 0:
            candles.append(_candle(start_index + i, 101.0, 99.8, 100.5))
        else:
            candles.append(_candle(start_index + i, 100.2, 99.0, 99.7))
    return candles


def test_oscillating_stretch_confirms_and_resolves_bullish() -> None:
    candles = _oscillating(20)
    breakout_start = len(candles)
    # Sustained breakout: closes above the 101 box high for the breaking
    # candle plus the persistence window.
    for i in range(4):
        candles.append(_candle(breakout_start + i, 103.0, 101.5, 102.5))

    ranges = detect_consolidation_ranges(
        candles, [], min_candles=10, max_height_pct=0.03, resolve_persistence=2
    )

    assert len(ranges) == 1
    r = ranges[0]
    assert r.status is ConsolidationStatus.RESOLVED
    assert r.resolved_direction is MarketDirection.BULLISH
    assert r.start_timestamp == candles[0].timestamp
    assert r.end_timestamp == candles[breakout_start].timestamp
    assert r.price_high == 101.0
    assert r.price_low == 99.0
    assert r.candle_count == breakout_start


def test_open_range_at_series_end_is_active() -> None:
    candles = _oscillating(20)

    ranges = detect_consolidation_ranges(
        candles, [], min_candles=10, max_height_pct=0.03, resolve_persistence=2
    )

    assert len(ranges) == 1
    r = ranges[0]
    assert r.status is ConsolidationStatus.ACTIVE
    assert r.end_timestamp is None
    assert r.resolved_direction is None
    assert r.candle_count == 20


def test_one_way_drift_within_cap_does_not_confirm() -> None:
    # A slow grind from 99 to ~101 stays inside a 3% box but never revisits
    # the bottom edge zone after leaving it: no oscillation, no range.
    candles = [
        _candle(i, 99.0 + i * 0.1 + 0.15, 99.0 + i * 0.1 - 0.15, 99.0 + i * 0.1)
        for i in range(20)
    ]

    ranges = detect_consolidation_ranges(
        candles, [], min_candles=10, max_height_pct=0.03, resolve_persistence=2
    )

    assert ranges == []


def test_unsustained_boundary_poke_is_a_sweep_not_a_resolution() -> None:
    candles = _oscillating(20)
    poke_index = len(candles)
    # One candle closes above the box high, then price falls straight back
    # inside: the break never sustains, the range stays open.
    candles.append(_candle(poke_index, 103.0, 101.2, 102.5))
    candles.extend(_oscillating(6, start_index=poke_index + 1))

    ranges = detect_consolidation_ranges(
        candles, [], min_candles=10, max_height_pct=0.03, resolve_persistence=2
    )

    assert len(ranges) == 1
    r = ranges[0]
    assert r.status is ConsolidationStatus.ACTIVE
    # The unabsorbable poke stays outside the frozen box.
    assert r.price_high == 101.0


def test_range_never_spans_a_structure_advance() -> None:
    candles = _oscillating(30)
    advances = [(15, MarketDirection.BULLISH)]

    # 30 oscillating candles would confirm at min_candles=20, but the advance
    # at index 15 splits them into two sub-threshold segments.
    assert (
        detect_consolidation_ranges(
            candles, advances, min_candles=20, max_height_pct=0.03, resolve_persistence=2
        )
        == []
    )
    assert (
        len(
            detect_consolidation_ranges(
                candles, [], min_candles=20, max_height_pct=0.03, resolve_persistence=2
            )
        )
        == 1
    )


def test_advance_resolves_an_open_range_in_its_direction() -> None:
    candles = _oscillating(25)
    # The advance candle itself (a breakdown the detector stamped as a
    # bearish event) ends the segment.
    candles.append(_candle(25, 99.5, 97.0, 97.5))
    advances = [(25, MarketDirection.BEARISH)]

    ranges = detect_consolidation_ranges(
        candles, advances, min_candles=10, max_height_pct=0.03, resolve_persistence=2
    )

    assert len(ranges) == 1
    r = ranges[0]
    assert r.status is ConsolidationStatus.RESOLVED
    assert r.resolved_direction is MarketDirection.BEARISH
    assert r.end_timestamp == candles[25].timestamp


def _breakout_series(count: int = 24, breakout_close: float = 102.5) -> list[Candle]:
    """An oscillating range followed by a sustained upward breakout."""
    candles = _oscillating(count)
    for i in range(4):
        candles.append(
            _candle(count + i, breakout_close + 0.5, breakout_close - 1.0, breakout_close)
        )
    return candles


def _staging_inputs(
    candles: list[Candle], advances: list[tuple[int, MarketDirection]]
) -> tuple[list[ConsolidationRange], list[tuple[int, MarketDirection]]]:
    ranges = detect_consolidation_ranges(
        candles, advances, min_candles=10, max_height_pct=0.03, resolve_persistence=2
    )
    return ranges, advances


def test_breakout_with_trend_stages_a_bos_at_the_boundary() -> None:
    candles = _breakout_series()
    ranges, advances = _staging_inputs(candles, [(0, MarketDirection.BULLISH)])

    staged = stage_breakout_events(candles, ranges, advances, [], dedup_candles=12)

    assert len(staged) == 1
    event = staged[0]
    assert event.event is StructureEvent.BREAK_OF_STRUCTURE
    assert event.direction is MarketDirection.BULLISH
    assert event.provisional is False
    assert event.scope is StructureScope.INTERNAL
    assert event.timestamp == ranges[-1].end_timestamp
    assert event.reference_price_level == 101.0
    # The line anchors at the first candle that formed the broken boundary.
    assert event.reference_timestamp == candles[2].timestamp


def test_breakout_against_trend_stages_a_provisional_choch() -> None:
    candles = _breakout_series()
    ranges, advances = _staging_inputs(candles, [(0, MarketDirection.BEARISH)])

    staged = stage_breakout_events(candles, ranges, advances, [], dedup_candles=12)

    assert len(staged) == 1
    event = staged[0]
    assert event.event is StructureEvent.CHANGE_OF_CHARACTER
    assert event.direction is MarketDirection.BULLISH
    # The additive contract: the state-machine trend never flipped, so the
    # mark must be provisional (replay consumers skip it).
    assert event.provisional is True
    assert event.reference_price_level == 101.0


def test_staging_dedupes_against_a_nearby_real_event() -> None:
    candles = _breakout_series()
    ranges, advances = _staging_inputs(candles, [(0, MarketDirection.BULLISH)])
    real = MarketStructure(
        symbol="TESTUSDT",
        timeframe=TimeFrame.H1,
        timestamp=candles[26].timestamp,
        event=StructureEvent.BREAK_OF_STRUCTURE,
        direction=MarketDirection.BULLISH,
        price_level=103.0,
        scope=StructureScope.INTERNAL,
    )

    assert stage_breakout_events(candles, ranges, advances, [real], dedup_candles=12) == []
    # An opposite-direction real event does not dedup the staged mark.
    bearish = real.model_copy(update={"direction": MarketDirection.BEARISH})
    assert (
        len(stage_breakout_events(candles, ranges, advances, [bearish], dedup_candles=12))
        == 1
    )


def test_bootstrap_segment_stages_nothing() -> None:
    candles = _breakout_series()
    ranges, advances = _staging_inputs(candles, [])

    # The range resolved, but with no opening advance there is no trend
    # context to classify the breakout against.
    assert ranges[-1].status is ConsolidationStatus.RESOLVED
    assert stage_breakout_events(candles, ranges, advances, [], dedup_candles=12) == []


def test_advance_resolved_range_stages_nothing() -> None:
    candles = _oscillating(25)
    candles.append(_candle(25, 99.5, 97.0, 97.5))
    advances = [(0, MarketDirection.BULLISH), (25, MarketDirection.BEARISH)]
    ranges = detect_consolidation_ranges(
        candles, advances, min_candles=10, max_height_pct=0.03, resolve_persistence=2
    )

    # The real event already marks the resolution candle.
    assert ranges[-1].resolved_direction is MarketDirection.BEARISH
    assert stage_breakout_events(candles, ranges, advances, [], dedup_candles=12) == []


def test_empty_and_short_inputs_return_no_ranges() -> None:
    assert (
        detect_consolidation_ranges(
            [], [], min_candles=10, max_height_pct=0.03, resolve_persistence=2
        )
        == []
    )
    assert (
        detect_consolidation_ranges(
            _oscillating(5), [], min_candles=10, max_height_pct=0.03, resolve_persistence=2
        )
        == []
    )
