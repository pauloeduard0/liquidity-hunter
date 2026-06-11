"""Tests for `SwingHighDetector` and `SwingLowDetector`."""

import pytest

from liquidity_hunter.core.domain import LiquiditySide, LiquidityZoneType, TimeFrame
from liquidity_hunter.liquidity.detectors.swing_points import SwingHighDetector, SwingLowDetector
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle, make_series

# Two prominent peaks at indices 3 and 10 (value 110), surrounded by lower highs.
HIGHS_WITH_TWO_PEAKS = [100, 101, 102, 110, 103, 102, 101, 100, 101, 102, 110, 103, 102, 101, 100]
LOWS_FOR_HIGHS = [h - 5 for h in HIGHS_WITH_TWO_PEAKS]

# Two prominent troughs at indices 3 and 10 (value 90), surrounded by higher lows.
LOWS_WITH_TWO_TROUGHS = [100, 99, 98, 90, 97, 98, 99, 100, 99, 98, 90, 97, 98, 99, 100]
HIGHS_FOR_LOWS = [low + 5 for low in LOWS_WITH_TWO_TROUGHS]


def test_swing_high_detector_finds_local_maxima() -> None:
    candles = make_series(HIGHS_WITH_TWO_PEAKS, LOWS_FOR_HIGHS)

    zones = SwingHighDetector(lookback=2).detect(candles)

    assert [z.formed_at for z in zones] == [candles[3].timestamp, candles[10].timestamp]
    for zone in zones:
        assert zone.zone_type is LiquidityZoneType.SWING_HIGH
        assert zone.side is LiquiditySide.BUY_SIDE
        assert zone.price_high == zone.price_low == 110
        assert zone.symbol == "BTCUSDT"
        assert zone.timeframe is TimeFrame.H1
        assert 0.0 < zone.strength <= 1.0


def test_swing_low_detector_finds_local_minima() -> None:
    candles = make_series(HIGHS_FOR_LOWS, LOWS_WITH_TWO_TROUGHS)

    zones = SwingLowDetector(lookback=2).detect(candles)

    assert [z.formed_at for z in zones] == [candles[3].timestamp, candles[10].timestamp]
    for zone in zones:
        assert zone.zone_type is LiquidityZoneType.SWING_LOW
        assert zone.side is LiquiditySide.SELL_SIDE
        assert zone.price_high == zone.price_low == 90
        assert 0.0 < zone.strength <= 1.0


def test_swing_detector_returns_empty_for_short_series() -> None:
    candles = make_series(HIGHS_WITH_TWO_PEAKS[:4], LOWS_FOR_HIGHS[:4])

    assert SwingHighDetector(lookback=2).detect(candles) == []


def test_swing_detector_rejects_invalid_lookback() -> None:
    with pytest.raises(ValueError, match="lookback"):
        SwingHighDetector(lookback=0)


def test_swing_detector_rejects_mixed_symbols() -> None:
    candles = make_series(HIGHS_WITH_TWO_PEAKS, LOWS_FOR_HIGHS)
    candles[0] = make_candle(0, candles[0].high, candles[0].low, symbol="ETHUSDT")

    with pytest.raises(ValueError, match="same symbol and timeframe"):
        SwingHighDetector().detect(candles)


def test_swing_detector_rejects_empty_candles() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        SwingHighDetector().detect([])
