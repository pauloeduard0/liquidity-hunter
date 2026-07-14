"""Tests for `liquidity.detectors.consolidation.detect_consolidation_ranges`."""

from datetime import UTC, datetime, timedelta

from liquidity_hunter.core.domain import (
    Candle,
    ConsolidationStatus,
    MarketDirection,
    TimeFrame,
)
from liquidity_hunter.liquidity.detectors.consolidation import detect_consolidation_ranges

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
