"""Tests for `liquidity_hunter.indicators.supertrend`."""

import pytest

from liquidity_hunter.core.domain import Candle, MarketDirection
from liquidity_hunter.indicators import supertrend, true_range_series
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle


def _rising(count: int, start: float = 100.0, step: float = 1.0) -> list[Candle]:
    return [
        make_candle(
            i,
            high=start + step * i + 0.5,
            low=start + step * i - 0.5,
            close=start + step * i + 0.4,
        )
        for i in range(count)
    ]


def _falling(
    count: int, start: float, step: float = 1.0, offset: int = 0
) -> list[Candle]:
    return [
        make_candle(
            offset + i,
            high=start - step * i + 0.5,
            low=start - step * i - 0.5,
            close=start - step * i - 0.4,
        )
        for i in range(count)
    ]


def test_true_range_first_candle_is_its_own_spread() -> None:
    candles = _rising(3)

    assert true_range_series(candles)[0] == pytest.approx(1.0)


def test_true_range_uses_previous_close() -> None:
    candles = _rising(3)

    # High/low span 1.0, but the gap from the previous close widens the range.
    expected = max(
        candles[1].high - candles[1].low,
        abs(candles[1].high - candles[0].close),
        abs(candles[1].low - candles[0].close),
    )
    assert true_range_series(candles)[1] == pytest.approx(expected)


def test_series_shorter_than_atr_period_yields_no_points() -> None:
    assert supertrend(_rising(5), periods=10) == []


def test_first_point_lands_on_the_first_defined_atr() -> None:
    candles = _rising(20)

    points = supertrend(candles, periods=10)

    assert len(points) == len(candles) - 9
    assert points[0].timestamp == candles[9].timestamp


def test_uptrend_follows_the_lower_band_below_price() -> None:
    candles = _rising(40)

    points = supertrend(candles, periods=10)

    last = points[-1]
    assert last.direction is MarketDirection.BULLISH
    assert last.value == last.lower_band
    assert last.value < candles[-1].close


def test_lower_band_only_ratchets_upward_while_the_trend_holds() -> None:
    candles = _rising(40)

    values = [p.value for p in supertrend(candles, periods=10)]

    assert values == sorted(values)


def test_trend_flips_and_marks_the_flip_candle_once() -> None:
    candles = _rising(30) + _falling(30, start=129.0, offset=30)

    points = supertrend(candles, periods=10)

    flips = [p for p in points if p.flip]
    assert len(flips) == 1
    assert flips[0].direction is MarketDirection.BEARISH
    # After the flip the reading follows the ceiling, above price.
    after = points[points.index(flips[0])]
    assert after.value == after.upper_band
    assert after.value > candles[points.index(flips[0]) + 9].close


def test_first_point_is_never_a_flip() -> None:
    candles = _falling(40, start=140.0)

    assert supertrend(candles, periods=10)[0].flip is False


def test_wider_multiplier_keeps_the_band_further_from_price() -> None:
    candles = _rising(40)

    tight = supertrend(candles, periods=10, multiplier=1.0)[-1]
    wide = supertrend(candles, periods=10, multiplier=5.0)[-1]

    assert wide.value < tight.value


def test_sma_atr_method_differs_from_wilder() -> None:
    candles = _rising(20) + _falling(20, start=119.0, offset=20)

    wilder = [p.value for p in supertrend(candles, periods=10, change_atr=True)]
    simple = [p.value for p in supertrend(candles, periods=10, change_atr=False)]

    assert len(wilder) == len(simple)
    assert wilder != simple


def test_non_positive_period_is_rejected() -> None:
    with pytest.raises(ValueError):
        supertrend(_rising(20), periods=0)
