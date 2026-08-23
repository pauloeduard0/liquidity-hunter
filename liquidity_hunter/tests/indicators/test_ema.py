"""EMA: warm-up honesty and the seed convention."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from liquidity_hunter.core.domain.candle import Candle
from liquidity_hunter.core.domain.enums import TimeFrame
from liquidity_hunter.indicators.ema import ema, ema_series


def _candles(closes: list[float]) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Candle(
            symbol="BTCUSDT",
            timeframe=TimeFrame.H1,
            timestamp=start + timedelta(hours=i),
            open=c, high=c + 1, low=c - 1, close=c,
            volume=100.0, taker_buy_volume=50.0,
        )
        for i, c in enumerate(closes)
    ]


def test_warmup_is_none_not_backfilled() -> None:
    """A consumer must not read a warm-up value as a level."""
    out = ema([1.0] * 5, period=3)
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(1.0)


def test_seed_is_the_simple_mean_of_the_first_period() -> None:
    out = ema([float(i) for i in range(1, 13)], period=9)
    assert out[8] == pytest.approx(5.0)  # mean(1..9)


def test_constant_series_equals_the_constant() -> None:
    out = ema([7.0] * 20, period=9)
    assert all(v == pytest.approx(7.0) for v in out[8:])


def test_linear_ramp_lags_by_a_constant() -> None:
    """On a straight line the EMA trails by a fixed offset, never converging."""
    out = ema([float(i) for i in range(1, 21)], period=9)
    gaps = [i + 1 - v for i, v in enumerate(out) if v is not None]
    assert all(g == pytest.approx(gaps[-1]) for g in gaps[-5:])


def test_shorter_than_period_is_all_none() -> None:
    assert ema([1.0, 2.0], period=9) == [None, None]


def test_series_reads_closes_and_aligns() -> None:
    closes = [float(i) for i in range(10, 24)]
    assert ema_series(_candles(closes), period=9) == ema(closes, period=9)


def test_rejects_non_positive_period() -> None:
    with pytest.raises(ValueError, match="period must be"):
        ema([1.0, 2.0], period=0)
