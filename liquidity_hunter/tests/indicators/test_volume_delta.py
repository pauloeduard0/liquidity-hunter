"""Tests for `liquidity_hunter.indicators.volume_delta`."""

import pytest

from liquidity_hunter.indicators import (
    cumulative_volume_delta,
    volume_delta,
    volume_delta_series,
)
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle


def test_volume_delta_positive_for_buy_dominant_candle() -> None:
    candle = make_candle(0, high=101.0, low=99.0, taker_buy_volume=0.8)

    assert volume_delta(candle) == 2 * 0.8 - candle.volume


def test_volume_delta_negative_for_sell_dominant_candle() -> None:
    candle = make_candle(0, high=101.0, low=99.0, taker_buy_volume=0.2)

    assert volume_delta(candle) == 2 * 0.2 - candle.volume


def test_volume_delta_zero_for_balanced_candle() -> None:
    candle = make_candle(0, high=101.0, low=99.0, taker_buy_volume=0.5)

    assert volume_delta(candle) == 0.0


def test_volume_delta_series_aligns_with_candles() -> None:
    candles = [
        make_candle(0, high=101.0, low=99.0, taker_buy_volume=0.8),
        make_candle(1, high=102.0, low=100.0, taker_buy_volume=0.2),
    ]

    assert volume_delta_series(candles) == [volume_delta(candles[0]), volume_delta(candles[1])]


def test_cumulative_volume_delta_is_running_sum() -> None:
    candles = [
        make_candle(0, high=101.0, low=99.0, taker_buy_volume=0.8),  # +0.6
        make_candle(1, high=102.0, low=100.0, taker_buy_volume=0.2),  # -0.6
        make_candle(2, high=103.0, low=101.0, taker_buy_volume=0.7),  # +0.4
    ]

    cvd = cumulative_volume_delta(candles)

    assert cvd == pytest.approx([0.6, 0.0, 0.4])
    assert len(cvd) == len(candles)


def test_cumulative_volume_delta_empty() -> None:
    assert cumulative_volume_delta([]) == []
