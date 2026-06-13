"""Tests for `liquidity_hunter.core.domain.enums.TimeFrame`."""

from liquidity_hunter.core.domain import TimeFrame


def test_finer_returns_next_finer_timeframe() -> None:
    assert TimeFrame.W1.finer() is TimeFrame.D1
    assert TimeFrame.D1.finer() is TimeFrame.H4
    assert TimeFrame.H4.finer() is TimeFrame.H1
    assert TimeFrame.H1.finer() is TimeFrame.M30
    assert TimeFrame.M30.finer() is TimeFrame.M15
    assert TimeFrame.M15.finer() is TimeFrame.M5
    assert TimeFrame.M5.finer() is TimeFrame.M1


def test_finer_returns_none_for_finest_timeframe() -> None:
    assert TimeFrame.M1.finer() is None
