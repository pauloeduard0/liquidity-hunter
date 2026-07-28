"""Tests for `liquidity_hunter.indicators.vwap`."""

from datetime import timedelta
from typing import Any

import pytest

from liquidity_hunter.core.domain import Candle, TimeFrame, VWAPAnchor, VWAPSeries
from liquidity_hunter.indicators import anchored_vwap, typical_price, vwap
from liquidity_hunter.tests.liquidity.detectors._factories import BASE_TIME, make_candle


def _series(
    prices: list[float],
    volumes: list[float] | None = None,
    interval: timedelta = timedelta(hours=1),
) -> list[Candle]:
    """A candle series whose typical price equals each entry of `prices`."""
    volumes = volumes if volumes is not None else [1.0] * len(prices)
    return [
        make_candle(
            index,
            high=price,
            low=price,
            close=price,
            volume=volume,
            taker_buy_volume=volume / 2,
            interval=interval,
        )
        for index, (price, volume) in enumerate(zip(prices, volumes, strict=True))
    ]


def _vwap(candles: list[Candle], **kwargs: Any) -> VWAPSeries | None:
    return vwap(candles, symbol="BTCUSDT", timeframe=TimeFrame.H1, **kwargs)


def test_typical_price_is_hlc3() -> None:
    candle = make_candle(0, high=110.0, low=90.0, close=100.0)

    assert typical_price(candle) == pytest.approx(100.0)


def test_value_is_volume_weighted_not_arithmetic() -> None:
    # 100 traded once, 200 traded nine times: the average paid is near 200,
    # while the unweighted mean of the two prices would be 150.
    series = _vwap(_series([100.0, 200.0], volumes=[1.0, 9.0]))

    assert series is not None
    assert series.points[-1].value == pytest.approx(190.0)


def test_flat_accumulation_has_no_bands() -> None:
    series = _vwap(_series([100.0, 100.0, 100.0]))

    assert series is not None
    assert all(point.upper_1 is None for point in series.points)


def test_bands_are_volume_weighted_standard_deviations() -> None:
    # Two equally weighted prices 10 apart: the weighted deviation is 5.
    series = _vwap(_series([95.0, 105.0]))

    assert series is not None
    last = series.points[-1]
    assert last.value == pytest.approx(100.0)
    assert last.upper_1 == pytest.approx(105.0)
    assert last.lower_1 == pytest.approx(95.0)
    assert last.upper_2 == pytest.approx(110.0)
    assert last.lower_2 == pytest.approx(90.0)


def test_session_anchor_restarts_at_the_utc_day() -> None:
    # 48 hourly candles = two UTC days; the second day must not inherit the
    # first day's accumulation.
    prices = [100.0] * 24 + [200.0] * 24
    series = _vwap(_series(prices), anchor=VWAPAnchor.SESSION)

    assert series is not None
    anchors = {point.anchor_timestamp for point in series.points}
    assert anchors == {BASE_TIME, BASE_TIME + timedelta(days=1)}
    # First candle of the second session: only its own price is accumulated.
    assert series.points[24].value == pytest.approx(200.0)
    assert series.points[-1].value == pytest.approx(200.0)
    assert series.anchor_timestamp == BASE_TIME + timedelta(days=1)


def test_event_anchor_starts_at_the_given_timestamp() -> None:
    candles = _series([100.0, 100.0, 300.0, 300.0])
    anchor = candles[2].timestamp

    series = anchored_vwap(
        candles, anchor, symbol="BTCUSDT", timeframe=TimeFrame.H1, label="Sweep"
    )

    assert series is not None
    assert len(series.points) == 2
    assert series.points[0].anchor_timestamp == anchor
    assert series.value == pytest.approx(300.0)
    assert series.label == "Sweep"
    assert series.anchor is VWAPAnchor.EVENT


def test_event_anchor_past_the_last_candle_returns_none() -> None:
    candles = _series([100.0, 100.0])

    assert (
        anchored_vwap(
            candles,
            candles[-1].timestamp + timedelta(hours=5),
            symbol="BTCUSDT",
            timeframe=TimeFrame.H1,
        )
        is None
    )


def test_event_anchor_requires_a_timestamp() -> None:
    with pytest.raises(ValueError, match="anchor_timestamp"):
        _vwap(_series([100.0]), anchor=VWAPAnchor.EVENT)


def test_rolling_anchor_drops_candles_leaving_the_window() -> None:
    candles = _series([100.0, 100.0, 400.0])

    series = _vwap(candles, anchor=VWAPAnchor.ROLLING, rolling_window=2)

    assert series is not None
    # Defined only once the window is full: 2 points for 3 candles.
    assert len(series.points) == 2
    assert series.points[0].value == pytest.approx(100.0)
    assert series.points[-1].value == pytest.approx(250.0)
    assert series.points[-1].anchor_timestamp == candles[1].timestamp


def test_rolling_anchor_requires_a_window() -> None:
    with pytest.raises(ValueError, match="rolling_window"):
        _vwap(_series([100.0, 100.0]), anchor=VWAPAnchor.ROLLING)


def test_zero_volume_candles_contribute_no_reading() -> None:
    candles = _series([100.0, 200.0], volumes=[0.0, 0.0])

    assert _vwap(candles) is None


def test_empty_series_returns_none() -> None:
    assert _vwap([]) is None


def test_negative_band_multiplier_is_rejected() -> None:
    with pytest.raises(ValueError, match="band multipliers"):
        _vwap(_series([100.0]), band_multipliers=(-1.0,))
