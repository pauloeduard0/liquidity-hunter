"""Swing high / swing low liquidity zone detectors.

A swing point is a fractal-style local extreme: a candle whose high (or
low) is more extreme than every candle within `lookback` positions on
either side.
"""

from collections.abc import Sequence

from liquidity_hunter.core.domain import Candle, LiquiditySide, LiquidityZone, LiquidityZoneType
from liquidity_hunter.liquidity.detectors._common import price_range, validate_candles
from liquidity_hunter.liquidity.detectors.base import LiquidityZoneDetector


class _SwingPointDetector(LiquidityZoneDetector):
    """Base class for fractal-style swing point detection."""

    _zone_type: LiquidityZoneType
    _side: LiquiditySide

    def __init__(self, lookback: int = 2) -> None:
        if lookback < 1:
            raise ValueError("lookback must be >= 1")
        self._lookback = lookback

    def detect(self, candles: list[Candle]) -> list[LiquidityZone]:
        validate_candles(candles)
        if len(candles) < 2 * self._lookback + 1:
            return []

        span = price_range(candles)
        zones: list[LiquidityZone] = []
        for i in range(self._lookback, len(candles) - self._lookback):
            pivot = self._extreme(candles[i])
            neighbors = [
                self._extreme(candles[j])
                for j in range(i - self._lookback, i + self._lookback + 1)
                if j != i
            ]
            if not self._is_swing(pivot, neighbors):
                continue

            prominence = self._prominence(pivot, neighbors)
            strength = min(1.0, max(0.0, prominence / span)) if span > 0 else 0.0

            zones.append(
                LiquidityZone(
                    symbol=candles[i].symbol,
                    timeframe=candles[i].timeframe,
                    zone_type=self._zone_type,
                    side=self._side,
                    price_high=pivot,
                    price_low=pivot,
                    formed_at=candles[i].timestamp,
                    strength=strength,
                )
            )
        return zones

    def _extreme(self, candle: Candle) -> float:
        raise NotImplementedError

    def _is_swing(self, pivot: float, neighbors: Sequence[float]) -> bool:
        raise NotImplementedError

    def _prominence(self, pivot: float, neighbors: Sequence[float]) -> float:
        raise NotImplementedError


class SwingHighDetector(_SwingPointDetector):
    """Detects swing highs: local maxima of `Candle.high`.

    Swing highs mark resting buy-side liquidity (stop losses of short
    positions and breakout buy orders) above price.
    """

    _zone_type = LiquidityZoneType.SWING_HIGH
    _side = LiquiditySide.BUY_SIDE

    def _extreme(self, candle: Candle) -> float:
        return candle.high

    def _is_swing(self, pivot: float, neighbors: Sequence[float]) -> bool:
        return all(pivot > neighbor for neighbor in neighbors)

    def _prominence(self, pivot: float, neighbors: Sequence[float]) -> float:
        return pivot - max(neighbors)


class SwingLowDetector(_SwingPointDetector):
    """Detects swing lows: local minima of `Candle.low`.

    Swing lows mark resting sell-side liquidity (stop losses of long
    positions) below price.
    """

    _zone_type = LiquidityZoneType.SWING_LOW
    _side = LiquiditySide.SELL_SIDE

    def _extreme(self, candle: Candle) -> float:
        return candle.low

    def _is_swing(self, pivot: float, neighbors: Sequence[float]) -> bool:
        return all(pivot < neighbor for neighbor in neighbors)

    def _prominence(self, pivot: float, neighbors: Sequence[float]) -> float:
        return min(neighbors) - pivot
