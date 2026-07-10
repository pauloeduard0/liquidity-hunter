"""POI (Point of Interest / Order Block) detector.

Detects order block zones from market structure breaks (MSB), adapted from
the "Market Structure Break & Order Block" TradingView indicator (EmreKb,
MPL 2.0). Self-contained: it derives its own swing pivots from the candle
series rather than consuming structure events.

Pivots
------
A rolling window of ``pivot_len`` candles tracks the swing state: a candle
whose high is the window maximum turns the swing up, one whose low is the
window minimum turns it down. Each swing flip records the completed leg's
extreme (the highest high of an up leg / lowest low of a down leg) as a
pivot, yielding the alternating high/low pivot sequence
``h0/h1`` / ``l0/l1`` (0 = most recent).

Market structure break (MSB)
----------------------------
With the market bearish, a new high pivot ``h0`` above the prior high ``h1``
by more than ``fib_factor`` of the preceding leg's height (``|h1 - l0|``)
confirms a *bullish* MSB; the bearish mirror breaks ``l1`` by
``fib_factor * |h0 - l1|``. After a flip, both the high and the low pivot
must renew before another flip can fire (same-pivot guard).

Order block
-----------
A bullish MSB marks the *last bearish candle* (open > close) of the down leg
into the swing low the impulse launched from (the ``h1 -> l0`` window); a
bearish MSB marks the last bullish candle of the ``l1 -> h0`` up leg. The
zone spans that candle's full high-low range, frozen at creation.

Zone lifecycle
--------------
ACTIVE -> INVALIDATED
  A single candle *close* beyond the far boundary (below ``price_low`` for
  a bullish zone, above ``price_high`` for a bearish one) retires the zone.
  Price trading back inside the zone does not retire it.
"""

from dataclasses import dataclass

from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    POIZoneStatus,
)
from liquidity_hunter.core.domain.poi_zone import POIZone

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------


@dataclass
class _Pivot:
    price: float
    index: int


@dataclass
class _ZoneState:
    direction: MarketDirection
    price_low: float
    price_high: float
    created_index: int
    ob_candle_index: int
    invalidated_index: int | None = None


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class POIDetector:
    """Detects MSB-anchored order block zones from a candle series.

    Parameters
    ----------
    pivot_len:
        Rolling window length for swing detection (the indicator's
        "ZigZag Length"). Default 9.
    fib_factor:
        Fraction of the preceding leg's height a new pivot must exceed the
        broken pivot by to confirm an MSB. Default 0.33.
    """

    def __init__(self, pivot_len: int = 9, fib_factor: float = 0.33) -> None:
        if pivot_len < 2:
            raise ValueError("pivot_len must be >= 2")
        if not 0.0 <= fib_factor <= 1.0:
            raise ValueError("fib_factor must be within [0, 1]")
        self._pivot_len = pivot_len
        self._fib_factor = fib_factor

    def detect(self, candles: list[Candle]) -> list[POIZone]:
        if len(candles) < self._pivot_len:
            return []

        highs = [c.high for c in candles]
        lows = [c.low for c in candles]

        swing = 1  # 1 = up leg forming, -1 = down leg forming
        high_pivots: list[_Pivot] = []
        low_pivots: list[_Pivot] = []

        market = MarketDirection.BULLISH
        # Pivot indices at the last MSB: both sides must renew before the
        # next flip (the indicator's same-pivot guard).
        flip_h0i: int | None = None
        flip_l0i: int | None = None

        zones: list[_ZoneState] = []

        for i, candle in enumerate(candles):
            window_start = max(0, i - self._pivot_len + 1)
            to_up = candle.high >= max(highs[window_start : i + 1])
            to_down = candle.low <= min(lows[window_start : i + 1])

            pivot_recorded = False
            if swing == 1 and to_down:
                swing = -1
                high_pivots.append(self._leg_extreme(highs, low_pivots, i, is_high=True))
                pivot_recorded = True
            elif swing == -1 and to_up:
                swing = 1
                low_pivots.append(self._leg_extreme(lows, high_pivots, i, is_high=False))
                pivot_recorded = True

            if pivot_recorded and not self._guard_blocked(
                high_pivots, low_pivots, flip_h0i, flip_l0i
            ):
                flip = self._on_pivot(candles, high_pivots, low_pivots, i, market)
                if flip is not None:
                    market, new_zone = flip
                    flip_h0i = high_pivots[-1].index
                    flip_l0i = low_pivots[-1].index
                    if new_zone is not None:
                        zones.append(new_zone)

            # --- update zones (skip the candle they were created on) ---
            for zone in zones:
                if zone.invalidated_index is not None or zone.created_index >= i:
                    continue
                if zone.direction == MarketDirection.BULLISH:
                    if candle.close < zone.price_low:
                        zone.invalidated_index = i
                elif candle.close > zone.price_high:
                    zone.invalidated_index = i

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        return [
            POIZone(
                symbol=symbol,
                timeframe=timeframe,
                direction=z.direction,
                price_low=z.price_low,
                price_high=z.price_high,
                created_at=candles[z.created_index].timestamp,
                ob_candle_timestamp=candles[z.ob_candle_index].timestamp,
                status=(
                    POIZoneStatus.ACTIVE
                    if z.invalidated_index is None
                    else POIZoneStatus.INVALIDATED
                ),
                invalidated_at=(
                    None
                    if z.invalidated_index is None
                    else candles[z.invalidated_index].timestamp
                ),
            )
            for z in zones
        ]

    # ------------------------------------------------------------------
    # Pivot recording
    # ------------------------------------------------------------------

    @staticmethod
    def _leg_extreme(
        prices: list[float],
        opposite_pivots: list[_Pivot],
        i: int,
        *,
        is_high: bool,
    ) -> _Pivot:
        """Extreme of the leg since the last opposite pivot (through bar ``i``).

        Prefers the most recent bar when the extreme repeats, matching the
        indicator's `barssince`-based index attribution.
        """
        start = opposite_pivots[-1].index if opposite_pivots else 0
        best = start
        for j in range(start, i + 1):
            if (is_high and prices[j] >= prices[best]) or (
                not is_high and prices[j] <= prices[best]
            ):
                best = j
        return _Pivot(price=prices[best], index=best)

    # ------------------------------------------------------------------
    # MSB evaluation (runs on each swing flip, when a pivot is recorded)
    # ------------------------------------------------------------------

    @staticmethod
    def _guard_blocked(
        high_pivots: list[_Pivot],
        low_pivots: list[_Pivot],
        flip_h0i: int | None,
        flip_l0i: int | None,
    ) -> bool:
        if not high_pivots or not low_pivots:
            return True
        return (flip_h0i is not None and high_pivots[-1].index == flip_h0i) or (
            flip_l0i is not None and low_pivots[-1].index == flip_l0i
        )

    def _on_pivot(
        self,
        candles: list[Candle],
        high_pivots: list[_Pivot],
        low_pivots: list[_Pivot],
        i: int,
        market: MarketDirection,
    ) -> tuple[MarketDirection, _ZoneState | None] | None:
        """Evaluate the MSB conditions with the freshly recorded pivot.

        Returns the flipped market direction plus the new order block zone
        (None when the impulse-origin leg holds no opposite candle) when the
        market flips, or None when no MSB confirmed.
        """
        if len(high_pivots) < 2 or len(low_pivots) < 2:
            return None

        h0, h1 = high_pivots[-1], high_pivots[-2]
        l0, l1 = low_pivots[-1], low_pivots[-2]

        if market == MarketDirection.BULLISH:
            # Bearish MSB: the new low pivot breaks the prior low by the
            # fib extension of the preceding up leg (l1 -> h0).
            if l0.price < l1.price - abs(h0.price - l1.price) * self._fib_factor:
                zone = self._build_zone(
                    candles,
                    window_start=l1.index,
                    window_end=h0.index,
                    msb_index=i,
                    bullish=False,
                )
                return MarketDirection.BEARISH, zone
        else:
            # Bullish MSB: the new high pivot breaks the prior high by the
            # fib extension of the preceding down leg (h1 -> l0).
            if h0.price > h1.price + abs(h1.price - l0.price) * self._fib_factor:
                zone = self._build_zone(
                    candles,
                    window_start=h1.index,
                    window_end=l0.index,
                    msb_index=i,
                    bullish=True,
                )
                return MarketDirection.BULLISH, zone
        return None

    # ------------------------------------------------------------------
    # Zone creation
    # ------------------------------------------------------------------

    @staticmethod
    def _build_zone(
        candles: list[Candle],
        *,
        window_start: int,
        window_end: int,
        msb_index: int,
        bullish: bool,
    ) -> _ZoneState | None:
        """Order block = last opposite-direction candle in the impulse-origin leg."""
        ob_index: int | None = None
        for j in range(window_start, window_end + 1):
            candle = candles[j]
            is_opposite = candle.open > candle.close if bullish else candle.open < candle.close
            if is_opposite:
                ob_index = j
        if ob_index is None:
            return None

        ob = candles[ob_index]
        if ob.high <= ob.low:
            return None  # degenerate candle with no range

        return _ZoneState(
            direction=MarketDirection.BULLISH if bullish else MarketDirection.BEARISH,
            price_low=ob.low,
            price_high=ob.high,
            created_index=msb_index,
            ob_candle_index=ob_index,
        )
