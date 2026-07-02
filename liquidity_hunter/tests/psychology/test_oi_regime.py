"""Tests for `liquidity_hunter.psychology.analyzers.oi_regime`."""

import pytest

from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    OIParticipation,
    OIRegime,
    OpenInterestPoint,
    StructureEvent,
)
from liquidity_hunter.psychology import OIRegimeAnalyzer
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle
from liquidity_hunter.tests.psychology._factories import make_structure_event

WINDOW = 5


def _candles(closes: list[float]) -> list[Candle]:
    return [
        make_candle(i, close + 1.0, close - 1.0, close=close) for i, close in enumerate(closes)
    ]


def _oi(candles: list[Candle], values: list[float]) -> list[OpenInterestPoint]:
    return [
        OpenInterestPoint(symbol="BTCUSDT", timestamp=candle.timestamp, open_interest=value)
        for candle, value in zip(candles, values, strict=True)
    ]


def _analyzer() -> OIRegimeAnalyzer:
    return OIRegimeAnalyzer(window_size=WINDOW)


# ------------------------------------------------------------------
# Current regime (the price x OI matrix)
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("closes", "oi_values", "expected"),
    [
        # price up + OI up: new longs entering
        ([100, 100.5, 101, 101.5, 102], [1000, 1005, 1010, 1015, 1020], OIRegime.LONG_BUILDUP),
        # price up + OI down: shorts covering
        ([100, 100.5, 101, 101.5, 102], [1020, 1015, 1010, 1005, 1000], OIRegime.SHORT_COVERING),
        # price down + OI up: new shorts entering
        ([102, 101.5, 101, 100.5, 100], [1000, 1005, 1010, 1015, 1020], OIRegime.SHORT_BUILDUP),
        # price down + OI down: longs closing
        ([102, 101.5, 101, 100.5, 100], [1020, 1015, 1010, 1005, 1000], OIRegime.LONG_LIQUIDATION),
    ],
)
def test_regime_matrix(
    closes: list[float], oi_values: list[float], expected: OIRegime
) -> None:
    candles = _candles(closes)
    analysis = _analyzer().analyze(candles, _oi(candles, oi_values), [])

    regime = analysis.current_regime
    assert regime is not None
    assert regime.regime is expected
    assert regime.intensity > 0
    assert regime.window_candles == WINDOW
    assert regime.timestamp == candles[-1].timestamp


def test_regime_flat_when_price_barely_moves() -> None:
    closes = [100.0, 100.05, 100.0, 100.05, 100.1]  # 0.1% < 0.2% floor
    candles = _candles(closes)
    analysis = _analyzer().analyze(candles, _oi(candles, [1000, 1010, 1020, 1030, 1040]), [])

    regime = analysis.current_regime
    assert regime is not None
    assert regime.regime is OIRegime.FLAT
    assert regime.intensity == 0.0


def test_regime_flat_when_oi_barely_moves() -> None:
    closes = [100.0, 100.5, 101.0, 101.5, 102.0]
    candles = _candles(closes)
    analysis = _analyzer().analyze(candles, _oi(candles, [1000, 1000.5, 1001, 1001.5, 1002]), [])

    regime = analysis.current_regime
    assert regime is not None
    assert regime.regime is OIRegime.FLAT


def test_no_regime_without_oi_coverage() -> None:
    candles = _candles([100, 101, 102, 103, 104])
    analysis = _analyzer().analyze(candles, [], [])

    assert analysis.current_regime is None
    assert analysis.qualified_events == []
    assert analysis.coverage_start is None
    assert analysis.coverage_end is None


def test_coverage_span_reported() -> None:
    candles = _candles([100, 101, 102, 103, 104])
    oi = _oi(candles, [1000, 1005, 1010, 1015, 1020])
    analysis = _analyzer().analyze(candles, oi, [])

    assert analysis.coverage_start == candles[0].timestamp
    assert analysis.coverage_end == candles[-1].timestamp


# ------------------------------------------------------------------
# Structure event qualification
# ------------------------------------------------------------------


def test_bos_with_rising_oi_is_new_money() -> None:
    candles = _candles([100, 100.5, 101, 101.5, 102, 102.5, 103, 103.5, 104, 104.5])
    oi = _oi(candles, [1000, 1002, 1004, 1006, 1008, 1010, 1012, 1014, 1016, 1018])
    bos = make_structure_event(
        StructureEvent.BREAK_OF_STRUCTURE,
        MarketDirection.BULLISH,
        timestamp=candles[7].timestamp,
    )

    analysis = _analyzer().analyze(candles, oi, [bos])

    assert len(analysis.qualified_events) == 1
    qualified = analysis.qualified_events[0]
    assert qualified.event_type is StructureEvent.BREAK_OF_STRUCTURE
    assert qualified.participation is OIParticipation.NEW_MONEY
    assert qualified.oi_delta_pct > 0
    assert qualified.event_timestamp == bos.timestamp


def test_bos_with_falling_oi_is_covering() -> None:
    candles = _candles([100, 100.5, 101, 101.5, 102, 102.5, 103, 103.5, 104, 104.5])
    oi = _oi(candles, [1018, 1016, 1014, 1012, 1010, 1008, 1006, 1004, 1002, 1000])
    bos = make_structure_event(
        StructureEvent.BREAK_OF_STRUCTURE,
        MarketDirection.BULLISH,
        timestamp=candles[7].timestamp,
    )

    analysis = _analyzer().analyze(candles, oi, [bos])

    assert analysis.qualified_events[0].participation is OIParticipation.COVERING


def test_sweep_with_sharp_oi_drop_is_flush() -> None:
    candles = _candles([100.0] * 10)
    # OI collapses ~2% into the sweep candle -- a liquidation flush.
    oi = _oi(candles, [1000, 1000, 1000, 1000, 1000, 1000, 1000, 995, 985, 980])
    sweep = make_structure_event(
        StructureEvent.LIQUIDITY_SWEEP,
        MarketDirection.BEARISH,
        timestamp=candles[8].timestamp,
    )

    analysis = _analyzer().analyze(candles, oi, [sweep])

    qualified = analysis.qualified_events[0]
    assert qualified.participation is OIParticipation.FLUSH
    assert qualified.oi_delta_pct < 0


def test_sweep_includes_next_oi_sample() -> None:
    """The flush lands on the sample *after* the sweep candle and must be seen."""
    candles = _candles([100.0] * 10)
    # OI is flat through the sweep candle (index 8); the drop only appears at
    # the next sample (index 9), where the sweep candle's liquidations settle.
    oi = _oi(candles, [1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 980])
    sweep = make_structure_event(
        StructureEvent.LIQUIDITY_SWEEP,
        MarketDirection.BEARISH,
        timestamp=candles[8].timestamp,
    )

    analysis = _analyzer().analyze(candles, oi, [sweep])

    assert analysis.qualified_events[0].participation is OIParticipation.FLUSH


def test_small_oi_change_is_flat_participation() -> None:
    candles = _candles([100.0] * 10)
    oi = _oi(candles, [1000.0] * 9 + [1001.0])
    bos = make_structure_event(
        StructureEvent.BREAK_OF_STRUCTURE,
        MarketDirection.BULLISH,
        timestamp=candles[7].timestamp,
    )

    analysis = _analyzer().analyze(candles, oi, [bos])

    assert analysis.qualified_events[0].participation is OIParticipation.FLAT


def test_pivot_labels_are_not_qualified() -> None:
    candles = _candles([100.0] * 10)
    oi = _oi(candles, [1000 + i * 10 for i in range(10)])
    pivot = make_structure_event(
        StructureEvent.HIGHER_LOW,
        MarketDirection.BULLISH,
        timestamp=candles[7].timestamp,
    )

    analysis = _analyzer().analyze(candles, oi, [pivot])

    assert analysis.qualified_events == []


def test_event_outside_oi_coverage_is_skipped() -> None:
    candles = _candles([100.0] * 10)
    # OI coverage only starts at candle 6; an event at candle 2 has no
    # OI sample at its window start.
    oi = _oi(candles[6:], [1000, 1010, 1020, 1030])
    bos = make_structure_event(
        StructureEvent.BREAK_OF_STRUCTURE,
        MarketDirection.BULLISH,
        timestamp=candles[2].timestamp,
    )

    analysis = _analyzer().analyze(candles, oi, [bos])

    assert analysis.qualified_events == []


def test_event_not_on_a_candle_is_skipped() -> None:
    candles = _candles([100.0] * 10)
    oi = _oi(candles, [1000 + i * 10 for i in range(10)])
    bos = make_structure_event(
        StructureEvent.BREAK_OF_STRUCTURE,
        MarketDirection.BULLISH,
        timestamp=candles[0].timestamp.replace(minute=30),
    )

    analysis = _analyzer().analyze(candles, oi, [bos])

    assert analysis.qualified_events == []
