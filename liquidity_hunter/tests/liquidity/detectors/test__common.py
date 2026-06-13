"""Tests for `liquidity_hunter.liquidity.detectors._common`."""

from liquidity_hunter.core.domain import Candle
from liquidity_hunter.liquidity.detectors._common import is_sustained_break
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle


def _candles(*closes: float) -> list[Candle]:
    return [make_candle(i, 105.0, 95.0, close=close) for i, close in enumerate(closes)]


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
