"""Tests for `EqualHighDetector` and `EqualLowDetector`."""

import pytest

from liquidity_hunter.core.domain import Candle, LiquiditySide, LiquidityZoneType
from liquidity_hunter.liquidity.detectors.equal_levels import EqualHighDetector, EqualLowDetector
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle, make_series

# These fixtures place their pivots three candles apart from the series
# edges, so they exercise grouping under an explicit lookback rather than
# the production default (10, see the detector's module docstring).
LOOKBACK = 3

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

    zones = EqualHighDetector(swing_lookback=LOOKBACK).detect(candles)

    assert len(zones) == 1
    zone = zones[0]
    assert zone.zone_type is LiquidityZoneType.EQUAL_HIGHS
    assert zone.side is LiquiditySide.BUY_SIDE
    assert zone.price_high == zone.price_low == 110
    assert zone.formed_at == candles[10].timestamp
    # Only the two touching candles reach the band, each carrying the
    # series' mean volume: 2 / _VOLUME_SATURATION.
    assert zone.strength == pytest.approx(2 / 40)


def test_equal_high_detector_respects_tolerance() -> None:
    candles = make_series(HIGHS_NEAR_EQUAL, LOWS_FOR_HIGHS_NEAR_EQUAL)

    loose = EqualHighDetector(tolerance_pct=0.001, swing_lookback=LOOKBACK).detect(candles)
    assert len(loose) == 1
    assert loose[0].price_low == 110.0
    assert loose[0].price_high == pytest.approx(110.05)

    tight = EqualHighDetector(tolerance_pct=0.0001, swing_lookback=LOOKBACK).detect(candles)
    assert tight == []


def test_equal_high_detector_respects_min_touches() -> None:
    candles = make_series(HIGHS_EQUAL, LOWS_FOR_HIGHS_EQUAL)

    assert EqualHighDetector(min_touches=3, swing_lookback=LOOKBACK).detect(candles) == []


def test_equal_low_detector_groups_identical_swings() -> None:
    candles = make_series(HIGHS_FOR_LOWS_EQUAL, LOWS_EQUAL)

    zones = EqualLowDetector(swing_lookback=LOOKBACK).detect(candles)

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


class TestAreaVolumeStrength:
    """`strength` reports volume traded at the level, not the touch count."""

    @staticmethod
    def _series(volumes: dict[int, float]) -> list[Candle]:
        """`HIGHS_EQUAL` with per-index volume overrides (default 1.0)."""
        return [
            make_candle(i, high, high - 5, volume=volumes.get(i, 1.0))
            for i, high in enumerate(HIGHS_EQUAL)
        ]

    def test_volume_at_the_level_raises_strength(self) -> None:
        quiet = EqualHighDetector(swing_lookback=LOOKBACK).detect(self._series({}))
        busy = EqualHighDetector(swing_lookback=LOOKBACK).detect(
            self._series({3: 10.0, 10: 10.0})
        )

        assert busy[0].strength > quiet[0].strength

    def test_volume_away_from_the_level_does_not(self) -> None:
        """A candle that never traded in the band says nothing about it."""
        quiet = EqualHighDetector(swing_lookback=LOOKBACK).detect(self._series({}))
        elsewhere = EqualHighDetector(swing_lookback=LOOKBACK).detect(
            self._series({6: 10.0})
        )

        # Candle 6 tops out at 101, far below the 110 pool, so it only moves
        # the series' mean volume — which can lower the reading, never raise it.
        assert elsewhere[0].strength <= quiet[0].strength

    def test_volume_outside_the_construction_window_does_not(self) -> None:
        """The window is bounded by the pool's own touches (index 3 -> 10)."""
        quiet = EqualHighDetector(swing_lookback=LOOKBACK).detect(self._series({}))
        after = EqualHighDetector(swing_lookback=LOOKBACK).detect(
            self._series({13: 10.0})
        )

        assert after[0].strength <= quiet[0].strength

    def test_reading_is_relative_to_the_series(self) -> None:
        """Scaling every candle's volume leaves the reading unchanged.

        The area volume is normalized by the series' *own* mean, so
        `strength` ranks pools within one chart and is not comparable
        across symbols — the same trade-off the Tide ribbon's saturation
        channel makes. It also means the 1.0 ceiling is reachable only
        when a level holds a large share of the whole window's volume,
        never from one loud candle.
        """
        quiet = EqualHighDetector(swing_lookback=LOOKBACK).detect(self._series({}))
        scaled = EqualHighDetector(swing_lookback=LOOKBACK).detect(
            [
                make_candle(i, high, high - 5, volume=1000.0)
                for i, high in enumerate(HIGHS_EQUAL)
            ]
        )

        assert scaled[0].strength == pytest.approx(quiet[0].strength)
        assert 0.0 <= scaled[0].strength <= 1.0
