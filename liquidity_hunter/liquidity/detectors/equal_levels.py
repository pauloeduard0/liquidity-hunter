"""Equal highs / equal lows liquidity zone detectors.

Equal levels are groups of two or more swing points whose prices fall
within a configurable tolerance of each other, marking a pool of resting
liquidity at (roughly) the same price.

**What makes a pool worth drawing** was measured on 2026-08-18 across 21
symbol/timeframe combos, against a matched random control (a random candle,
level = its own high/low, same side): for each pool that was later grabbed,
the rejection excursion over the following 10 candles, in mean-TR units.
The control's median is the bar any reading has to clear.

- The **pivot lookback is the discriminator.** At the previous default of 2
  — a pivot with two candles either side, i.e. micro-noise rather than a
  swing — pools reacted *worse than random* (lift 0.82, 77 pools per chart).
  At 10 the reading is neutral-to-positive (1.01) on 11 pools per chart.
  A detector whose output measures below its own control is not a
  conservative detector, it is an anti-informative one.
- **Volume traded inside the pool's price band, over its construction
  window** (first touch → last touch) separates the halves 1.05 / 0.81, and
  unlike the post-hoc window (formation → grab, 1.05 / 0.89) it is knowable
  at detection time. It became `strength`.
- **Touch count did not survive.** It saturated at 1.0 for any group of 3+
  touches — a channel that is constant carries nothing — and its close
  relative, the revisit count inside the band, splits the *wrong* way (more
  revisits, weaker defense: a pool already drained).
- A **maximum time span between touches was measured and rejected**: it
  discriminates backwards. Tight groups are worse (≤20 candles → 0.60,
  ≤160 → 0.96), so two highs far apart in time are the better pool, not a
  coincidence to filter out.
"""

import statistics
from datetime import datetime

from liquidity_hunter.core.domain import Candle, LiquiditySide, LiquidityZone, LiquidityZoneType
from liquidity_hunter.indicators.supertrend import true_range_series
from liquidity_hunter.liquidity.detectors.base import LiquidityZoneDetector
from liquidity_hunter.liquidity.detectors.swing_points import SwingHighDetector, SwingLowDetector

#: Area volume, in units of the series' mean candle volume, at which
#: `strength` saturates to 1.0. The measured median construction-window
#: volume is ~21 mean-candles, so this puts a typical pool near 0.5 and
#: leaves room above it — the point of the channel is to *vary*.
_VOLUME_SATURATION = 40.0


class _EqualLevelDetector(LiquidityZoneDetector):
    """Base class that groups nearby swing points into equal-level zones."""

    _zone_type: LiquidityZoneType
    _side: LiquiditySide

    def __init__(
        self,
        tolerance_pct: float = 0.0005,
        min_touches: int = 2,
        swing_lookback: int = 10,
        tolerance_atr: float | None = None,
    ) -> None:
        """Group swing points into equal-level pools.

        ``tolerance_atr``, when given, replaces ``tolerance_pct`` with N times
        the series' own mean true range as a fraction of price. "Equal" is a
        statement about what the market treats as one level, and that scale is
        volatility, not a constant: 0.05% is several candles' range on a calm
        BTC hour and a rounding error on a volatile alt, so a fixed percent
        asks a different question of every chart. It is the same fix the hunt's
        proximity and the consolidation height cap already carry.
        """
        if tolerance_pct < 0:
            raise ValueError("tolerance_pct must be >= 0")
        if tolerance_atr is not None and tolerance_atr < 0:
            raise ValueError("tolerance_atr must be >= 0")
        if min_touches < 2:
            raise ValueError("min_touches must be >= 2")
        self._tolerance_pct = tolerance_pct
        self._tolerance_atr = tolerance_atr
        self._min_touches = min_touches
        self._swing_detector = self._make_swing_detector(swing_lookback)

    def _resolve_tolerance(self, candles: list[Candle]) -> float:
        """The relative tolerance this series gets."""
        if self._tolerance_atr is None or not candles:
            return self._tolerance_pct
        ranges = true_range_series(candles)
        mean_tr_pct = statistics.mean(
            true_range / candle.close
            for true_range, candle in zip(ranges, candles, strict=True)
            if candle.close > 0
        )
        return mean_tr_pct * self._tolerance_atr if mean_tr_pct > 0 else self._tolerance_pct

    def _make_swing_detector(self, swing_lookback: int) -> LiquidityZoneDetector:
        raise NotImplementedError

    def detect(self, candles: list[Candle]) -> list[LiquidityZone]:
        swings = self._swing_detector.detect(candles)
        if len(swings) < self._min_touches:
            return []

        mean_volume = statistics.mean(candle.volume for candle in candles)
        tolerance = self._resolve_tolerance(candles)

        zones: list[LiquidityZone] = []
        for group in self._group_by_tolerance(swings, tolerance):
            if len(group) < self._min_touches:
                continue

            prices = [swing.price_high for swing in group]
            price_low, price_high = min(prices), max(prices)
            latest = max(group, key=lambda swing: swing.formed_at)
            earliest = min(group, key=lambda swing: swing.formed_at)
            strength = self._area_strength(
                candles, earliest.formed_at, latest.formed_at, price_low, price_high, mean_volume
            )

            zones.append(
                LiquidityZone(
                    symbol=latest.symbol,
                    timeframe=latest.timeframe,
                    zone_type=self._zone_type,
                    side=self._side,
                    price_high=price_high,
                    price_low=price_low,
                    formed_at=latest.formed_at,
                    strength=strength,
                )
            )
        return zones

    @staticmethod
    def _area_strength(
        candles: list[Candle],
        start: datetime,
        end: datetime,
        price_low: float,
        price_high: float,
        mean_volume: float,
    ) -> float:
        """How much volume changed hands *at this level* while the pool formed.

        The pool's construction window is bounded by its own touches, and
        only candles whose range overlaps the band count: a candle that
        traded elsewhere says nothing about the orders resting here. See the
        module docstring for the measurement this replaced touch counting
        with.
        """
        if mean_volume <= 0:
            return 0.0

        area_volume = sum(
            candle.volume
            for candle in candles
            if start <= candle.timestamp <= end
            and candle.high >= price_low
            and candle.low <= price_high
        )
        return min(1.0, area_volume / (mean_volume * _VOLUME_SATURATION))

    @staticmethod
    def _group_by_tolerance(
        swings: list[LiquidityZone], tolerance: float
    ) -> list[list[LiquidityZone]]:
        ordered = sorted(swings, key=lambda swing: swing.price_high)
        groups: list[list[LiquidityZone]] = []
        for swing in ordered:
            if groups:
                anchor = groups[-1][0].price_high
                if abs(swing.price_high - anchor) <= anchor * tolerance:
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
