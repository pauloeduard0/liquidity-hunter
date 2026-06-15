"""Tests for `liquidity_hunter.liquidity.detectors._common`."""

from liquidity_hunter.core.domain import Candle
from liquidity_hunter.liquidity.detectors._common import (
    find_sustained_break_index,
    find_wick_break_index,
    is_sustained_break,
)
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle


def _candles(*closes: float) -> list[Candle]:
    return [make_candle(i, 105.0, 95.0, close=close) for i, close in enumerate(closes)]


def _candles_hl(*highs_lows: tuple[float, float]) -> list[Candle]:
    return [make_candle(i, high, low) for i, (high, low) in enumerate(highs_lows)]


def test_is_sustained_break_true_when_pivot_and_window_close_beyond() -> None:
    candles = _candles(101.0, 102.0, 103.0)

    assert is_sustained_break(candles, 0, 100.0, bullish=True, persistence_candles=2)


def test_is_sustained_break_false_when_pivot_close_not_beyond() -> None:
    # pivot candle itself doesn't clear 100, regardless of the rest.
    candles = _candles(99.0, 102.0, 103.0)

    assert not is_sustained_break(candles, 0, 100.0, bullish=True, persistence_candles=2)


def test_is_sustained_break_false_when_break_reverts_within_window() -> None:
    # close beyond 100 at the pivot, but candle 1 closes back below it.
    candles = _candles(101.0, 99.0, 103.0)

    assert not is_sustained_break(candles, 0, 100.0, bullish=True, persistence_candles=2)


def test_is_sustained_break_false_when_insufficient_trailing_candles() -> None:
    # only 1 candle after the pivot, but persistence_candles=2 needs 2.
    candles = _candles(101.0, 102.0)

    assert not is_sustained_break(candles, 0, 100.0, bullish=True, persistence_candles=2)


def test_is_sustained_break_bearish_direction() -> None:
    candles = _candles(99.0, 98.0, 97.0)

    assert is_sustained_break(candles, 0, 100.0, bullish=False, persistence_candles=2)
    assert not is_sustained_break(
        _candles(99.0, 101.0, 97.0), 0, 100.0, bullish=False, persistence_candles=2
    )


def test_find_wick_break_index_returns_first_break_in_range() -> None:
    # highs at indices 1 and 2 both clear 100; the first one wins.
    candles = _candles_hl((99.0, 90.0), (101.0, 90.0), (105.0, 90.0))

    assert find_wick_break_index(candles, 0, 2, 100.0, bullish=True) == 1


def test_find_wick_break_index_falls_back_to_end_index() -> None:
    # only candles[2]'s wick clears 100.
    candles = _candles_hl((99.0, 90.0), (99.0, 90.0), (105.0, 90.0))

    assert find_wick_break_index(candles, 0, 2, 100.0, bullish=True) == 2


def test_find_wick_break_index_bearish_direction() -> None:
    candles = _candles_hl((105.0, 96.0), (105.0, 94.0), (105.0, 90.0))

    assert find_wick_break_index(candles, 0, 2, 95.0, bullish=False) == 1


def test_find_sustained_break_index_returns_first_in_range() -> None:
    candles = _candles(101.0, 102.0, 103.0, 104.0, 105.0)

    assert (
        find_sustained_break_index(candles, 0, 4, 100.0, bullish=True, persistence_candles=1) == 0
    )


def test_find_sustained_break_index_falls_back_to_end_index() -> None:
    candles = _candles(99.0, 99.0, 101.0, 102.0, 103.0)

    assert (
        find_sustained_break_index(candles, 0, 2, 100.0, bullish=True, persistence_candles=2) == 2
    )


def test_find_sustained_break_index_persistence_window_may_extend_past_end_index() -> None:
    # end_index=1 is where `confirms_break` first holds, but its own
    # persistence window (candles[1:4]) extends past end_index -- this must
    # not cause the match to be missed.
    candles = _candles(99.0, 101.0, 102.0, 103.0)

    assert (
        find_sustained_break_index(candles, 0, 1, 100.0, bullish=True, persistence_candles=2) == 1
    )
