"""Tests for `EqualHighDetector` and `EqualLowDetector`."""

import pytest

from liquidity_hunter.core.domain import LiquiditySide, LiquidityZoneType
from liquidity_hunter.liquidity.detectors.equal_levels import EqualHighDetector, EqualLowDetector
from liquidity_hunter.tests.liquidity.detectors._factories import make_series

# Two swing highs at exactly the same price (110).
HIGHS_EQUAL = [100, 101, 102, 110, 103, 102, 101, 100, 101, 102, 110, 103, 102, 101, 100]
LOWS_FOR_HIGHS_EQUAL = [h - 5 for h in HIGHS_EQUAL]

# Two swing highs that are close but not identical (110.0 vs 110.05).
HIGHS_NEAR_EQUAL = [100, 101, 102, 110.0, 103, 102, 101, 100, 101, 102, 110.05, 103, 102, 101, 100]
LOWS_FOR_HIGHS_NEAR_EQUAL = [h - 5 for h in HIGHS_NEAR_EQUAL]

# Two swing lows at exactly the same price (90).
LOWS_EQUAL = [100, 99, 98, 90, 97, 98, 99, 100, 99, 98, 90, 97, 98, 99, 100]
HIGHS_FOR_LOWS_EQUAL = [low + 5 for low in LOWS_EQUAL]


def test_equal_high_detector_groups_identical_swings() -> None:
    candles = make_series(HIGHS_EQUAL, LOWS_FOR_HIGHS_EQUAL)

    zones = EqualHighDetector().detect(candles)

    assert len(zones) == 1
    zone = zones[0]
    assert zone.zone_type is LiquidityZoneType.EQUAL_HIGHS
    assert zone.side is LiquiditySide.BUY_SIDE
    assert zone.price_high == zone.price_low == 110
    assert zone.formed_at == candles[10].timestamp
    assert zone.strength == pytest.approx(2 / 3)


def test_equal_high_detector_respects_tolerance() -> None:
    candles = make_series(HIGHS_NEAR_EQUAL, LOWS_FOR_HIGHS_NEAR_EQUAL)

    loose = EqualHighDetector(tolerance_pct=0.001).detect(candles)
    assert len(loose) == 1
    assert loose[0].price_low == 110.0
    assert loose[0].price_high == pytest.approx(110.05)

    tight = EqualHighDetector(tolerance_pct=0.0001).detect(candles)
    assert tight == []


def test_equal_high_detector_respects_min_touches() -> None:
    candles = make_series(HIGHS_EQUAL, LOWS_FOR_HIGHS_EQUAL)

    assert EqualHighDetector(min_touches=3).detect(candles) == []


def test_equal_low_detector_groups_identical_swings() -> None:
    candles = make_series(HIGHS_FOR_LOWS_EQUAL, LOWS_EQUAL)

    zones = EqualLowDetector().detect(candles)

    assert len(zones) == 1
    zone = zones[0]
    assert zone.zone_type is LiquidityZoneType.EQUAL_LOWS
    assert zone.side is LiquiditySide.SELL_SIDE
    assert zone.price_high == zone.price_low == 90
    assert zone.formed_at == candles[10].timestamp


@pytest.mark.parametrize("ctor_kwargs", [{"tolerance_pct": -0.1}, {"min_touches": 1}])
def test_equal_level_detector_rejects_invalid_config(ctor_kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        EqualHighDetector(**ctor_kwargs)
