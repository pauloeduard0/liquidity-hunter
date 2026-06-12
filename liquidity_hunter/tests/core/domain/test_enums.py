"""Tests for `liquidity_hunter.core.domain.enums.TimeFrame`."""

from datetime import timedelta

from liquidity_hunter.core.domain import TimeFrame


def test_to_timedelta() -> None:
    assert TimeFrame.M1.to_timedelta() == timedelta(minutes=1)
    assert TimeFrame.M30.to_timedelta() == timedelta(minutes=30)
    assert TimeFrame.H1.to_timedelta() == timedelta(hours=1)
    assert TimeFrame.D1.to_timedelta() == timedelta(days=1)
    assert TimeFrame.W1.to_timedelta() == timedelta(weeks=1)


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
