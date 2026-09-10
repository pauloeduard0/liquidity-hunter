"""RSI: warm-up honesty, Wilder's smoothing, and the undefined-ratio edges."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from liquidity_hunter.core.domain.candle import Candle
from liquidity_hunter.core.domain.enums import TimeFrame
from liquidity_hunter.indicators.rsi import rsi, rsi_ma, rsi_ma_series, rsi_series


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


def test_warm_up_is_none_not_backfilled() -> None:
    values = rsi([float(i) for i in range(20)], period=14)
    assert values[:14] == [None] * 14
    assert values[14] is not None


def test_series_shorter_than_period_is_all_none() -> None:
    assert rsi([1.0, 2.0, 3.0], period=14) == [None] * 3


def test_seed_and_wilder_smoothing_by_hand() -> None:
    # period=2: gains [1, 0], losses [0, 1] -> both averages 0.5 -> RSI 50.
    # Next close adds gain 1: avg_gain 0.75, avg_loss 0.25 -> 100 - 100/4 = 75.
    assert rsi([1.0, 2.0, 1.0, 2.0], period=2) == pytest.approx(
        [None, None, 50.0, 75.0]
    )


def test_monotone_series_saturate() -> None:
    assert rsi([1.0, 2.0, 3.0, 4.0], period=2)[-1] == 100.0
    assert rsi([4.0, 3.0, 2.0, 1.0], period=2)[-1] == 0.0


def test_flat_series_is_neutral() -> None:
    # No move at all: the ratio is undefined in both directions, and the
    # honest answer is the midpoint, not a saturated extreme.
    assert rsi([5.0] * 6, period=2)[-1] == 50.0


def test_rejects_non_positive_period() -> None:
    with pytest.raises(ValueError):
        rsi([1.0, 2.0, 3.0], period=0)


def test_series_reads_closes_and_aligns_1_to_1() -> None:
    closes = [11.0, 12.0, 11.0, 12.0]
    candles = _candles(closes)
    assert rsi_series(candles, period=2) == rsi(closes, period=2)
    assert len(rsi_series(candles, period=2)) == len(candles)


def test_rsi_ma_waits_for_a_full_window_of_rsi_values() -> None:
    """The average never rests on a partial window.

    RSI(14) first prints at index 14, so its 14-period mean cannot print
    before index 27. Seeding it earlier with fewer observations would make the
    first values a different statistic wearing the same name.
    """
    values = [10.0 + i * 0.5 for i in range(40)]
    averaged = rsi_ma(values)
    assert averaged[:27] == [None] * 27
    assert averaged[27] is not None


def test_rsi_ma_is_the_simple_mean_of_the_rsi_window() -> None:
    values = [10.0 + (i % 5) - (i % 3) for i in range(60)]
    base = rsi(values)
    averaged = rsi_ma(values)
    defined = [v for v in base if v is not None]
    assert averaged[-1] == pytest.approx(sum(defined[-14:]) / 14)


def test_rsi_ma_rejects_a_non_positive_window() -> None:
    with pytest.raises(ValueError):
        rsi_ma([1.0, 2.0, 3.0], ma_period=0)


def test_rsi_ma_series_reads_closes() -> None:
    candles = _candles([11.0 + (i % 7) for i in range(60)])
    assert rsi_ma_series(candles) == rsi_ma([c.close for c in candles])
