"""Equal highs / equal lows liquidity zone detectors.

Equal levels are groups of two or more swing points whose prices fall
within a configurable tolerance of each other, marking a pool of resting
liquidity at (roughly) the same price.
"""

from liquidity_hunter.core.domain import Candle, LiquiditySide, LiquidityZone, LiquidityZoneType
from liquidity_hunter.liquidity.detectors.base import LiquidityZoneDetector
from liquidity_hunter.liquidity.detectors.swing_points import SwingHighDetector, SwingLowDetector


class _EqualLevelDetector(LiquidityZoneDetector):
    """Base class that groups nearby swing points into equal-level zones."""

    _zone_type: LiquidityZoneType
    _side: LiquiditySide

    def __init__(
        self,
        tolerance_pct: float = 0.0005,
        min_touches: int = 2,
        swing_lookback: int = 2,
    ) -> None:
        if tolerance_pct < 0:
            raise ValueError("tolerance_pct must be >= 0")
        if min_touches < 2:
            raise ValueError("min_touches must be >= 2")
        self._tolerance_pct = tolerance_pct
        self._min_touches = min_touches
        self._swing_detector = self._make_swing_detector(swing_lookback)

    def _make_swing_detector(self, swing_lookback: int) -> LiquidityZoneDetector:
        raise NotImplementedError

    def detect(self, candles: list[Candle]) -> list[LiquidityZone]:
        swings = self._swing_detector.detect(candles)
        if len(swings) < self._min_touches:
            return []

        zones: list[LiquidityZone] = []
        for group in self._group_by_tolerance(swings):
            if len(group) < self._min_touches:
                continue

            prices = [swing.price_high for swing in group]
            latest = max(group, key=lambda swing: swing.formed_at)
            # Saturates to 1.0 once the group has more than `min_touches` touches.
            strength = min(1.0, len(group) / (self._min_touches + 1))

            zones.append(
                LiquidityZone(
                    symbol=latest.symbol,
                    timeframe=latest.timeframe,
                    zone_type=self._zone_type,
                    side=self._side,
                    price_high=max(prices),
                    price_low=min(prices),
                    formed_at=latest.formed_at,
                    strength=strength,
                )
            )
        return zones

    def _group_by_tolerance(self, swings: list[LiquidityZone]) -> list[list[LiquidityZone]]:
        ordered = sorted(swings, key=lambda swing: swing.price_high)
        groups: list[list[LiquidityZone]] = []
        for swing in ordered:
            if groups:
                anchor = groups[-1][0].price_high
                if abs(swing.price_high - anchor) <= anchor * self._tolerance_pct:
                    groups[-1].append(swing)
                    continue
            groups.append([swing])
        return groups


class EqualHighDetector(_EqualLevelDetector):
    """Groups swing highs within `tolerance_pct` of each other into equal-high zones.

    Equal highs mark buy-side liquidity pools above price.
    """

    _zone_type = LiquidityZoneType.EQUAL_HIGHS
    _side = LiquiditySide.BUY_SIDE

    def _make_swing_detector(self, swing_lookback: int) -> LiquidityZoneDetector:
        return SwingHighDetector(lookback=swing_lookback)


class EqualLowDetector(_EqualLevelDetector):
    """Groups swing lows within `tolerance_pct` of each other into equal-low zones.

    Equal lows mark sell-side liquidity pools below price.
    """

    _zone_type = LiquidityZoneType.EQUAL_LOWS
    _side = LiquiditySide.SELL_SIDE

    def _make_swing_detector(self, swing_lookback: int) -> LiquidityZoneDetector:
        return SwingLowDetector(lookback=swing_lookback)
