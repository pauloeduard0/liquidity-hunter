"""Swing (major) market structure detector: BOS/CHoCH and HH/HL/LH/LL.

This module adapts the core idea of the LuxAlgo "Smart Money Concepts"
indicator (BOS/CHoCH from alternating swing highs/lows) with two key
changes to how the *active* reference levels are maintained and broken:

A new swing pivot does **not** immediately become the reference whose break
would flip the trend. It is only promoted to that role once the *opposite*
active reference is actually broken. Until then it is held as a "pending"
candidate. This avoids flagging a CHoCH against a minor retracement pivot
that was never structurally significant.

A pivot whose price exceeds the active reference on its side is only
confirmed as a BOS/CHoCH if the candle that formed it also *closes* beyond
that reference AND has a `volume_delta` (see `indicators.volume_delta`) of
at least `min_volume_delta_ratio` (relative to its `volume`) in the
breakout direction. If either condition fails, the active reference is
considered swept rather than broken: it stays unchanged and a
`StructureEvent.LIQUIDITY_SWEEP` event is emitted instead.

Every pivot that does *not* break the active reference on its side is also
labeled HH/HL/LH/LL by comparing it to the previous pivot of the same type
-- a purely descriptive observation, independent of the active/pending
state machine.
"""

from dataclasses import dataclass
from datetime import datetime

from liquidity_hunter.core.domain import Candle, MarketDirection, MarketStructure, StructureEvent
from liquidity_hunter.indicators import volume_delta
from liquidity_hunter.liquidity.detectors._common import validate_candles
from liquidity_hunter.liquidity.detectors.base import MarketStructureDetector
from liquidity_hunter.liquidity.detectors.swing_points import SwingHighDetector, SwingLowDetector

# Label for a pivot relative to the previous pivot of the same type, keyed
# by whether the new pivot is higher (True) or lower (False).
_HIGH_PIVOT_LABELS: dict[bool, tuple[StructureEvent, MarketDirection]] = {
    True: (StructureEvent.HIGHER_HIGH, MarketDirection.BULLISH),
    False: (StructureEvent.LOWER_HIGH, MarketDirection.BEARISH),
}
_LOW_PIVOT_LABELS: dict[bool, tuple[StructureEvent, MarketDirection]] = {
    True: (StructureEvent.HIGHER_LOW, MarketDirection.BULLISH),
    False: (StructureEvent.LOWER_LOW, MarketDirection.BEARISH),
}


@dataclass(frozen=True)
class _Pivot:
    price: float
    timestamp: datetime


class SwingStructureDetector(MarketStructureDetector):
    """Detects BOS/CHoCH and HH/HL/LH/LL from major (swing) pivots.

    Swing highs/lows are sourced from `SwingHighDetector`/`SwingLowDetector`
    using `swing_lookback`, then walked in chronological order maintaining
    two pairs of reference levels:

    - `active_high`/`active_low`: the confirmed references whose break
      produces a BOS (trend continuation) or CHoCH (trend reversal) event.
    - `pending_high`/`pending_low`: the most recent swing pivot on each
      side that has *not yet* broken its active counterpart. A pending
      pivot is only promoted to active once the opposite active level is
      broken.

    A pivot whose price exceeds the active reference on its side is
    confirmed as a break only if the candle that formed it closes beyond
    that reference and its `volume_delta` ratio
    (`abs(volume_delta) / volume`) is at least `min_volume_delta_ratio` in
    the breakout direction. Otherwise the active reference is left
    unchanged and a `StructureEvent.LIQUIDITY_SWEEP` is reported -- the
    swept pivot becomes the new `pending_high`/`pending_low`, so it can
    still be promoted to active later if the opposite side breaks.

    A pivot that *confirms* a break is, by construction, always higher (for
    highs) or lower (for lows) than the previous pivot of the same type --
    so it is reported only as BOS/CHoCH, an HH/LL label would be redundant.
    A swept pivot is reported only as `LIQUIDITY_SWEEP`, for the same
    reason. Pivots that do *not* exceed the active reference are reported
    as HH/LH (highs) or HL/LL (lows) instead.
    """

    def __init__(self, swing_lookback: int = 50, min_volume_delta_ratio: float = 0.2) -> None:
        if not 0.0 <= min_volume_delta_ratio <= 1.0:
            raise ValueError("min_volume_delta_ratio must be between 0 and 1")
        self._high_detector = SwingHighDetector(lookback=swing_lookback)
        self._low_detector = SwingLowDetector(lookback=swing_lookback)
        self._min_volume_delta_ratio = min_volume_delta_ratio

    def detect(self, candles: list[Candle]) -> list[MarketStructure]:
        validate_candles(candles)

        highs = self._high_detector.detect(candles)
        lows = self._low_detector.detect(candles)
        pivots = sorted(
            [(zone.formed_at, "high", zone.price_high) for zone in highs]
            + [(zone.formed_at, "low", zone.price_low) for zone in lows],
            key=lambda pivot: pivot[0],
        )

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        candles_by_timestamp = {candle.timestamp: candle for candle in candles}

        events: list[MarketStructure] = []
        active_high: _Pivot | None = None
        active_low: _Pivot | None = None
        pending_high: _Pivot | None = None
        pending_low: _Pivot | None = None
        last_high: _Pivot | None = None
        last_low: _Pivot | None = None
        trend = MarketDirection.NEUTRAL

        for timestamp, kind, price in pivots:
            pivot = _Pivot(price=price, timestamp=timestamp)

            if kind == "high":
                if active_high is None:
                    active_high = pivot
                elif price > active_high.price:
                    if self._is_confirmed(
                        candles_by_timestamp[timestamp], active_high.price, bullish=True
                    ):
                        events.append(
                            MarketStructure(
                                symbol=symbol,
                                timeframe=timeframe,
                                timestamp=timestamp,
                                event=(
                                    StructureEvent.CHANGE_OF_CHARACTER
                                    if trend is MarketDirection.BEARISH
                                    else StructureEvent.BREAK_OF_STRUCTURE
                                ),
                                direction=MarketDirection.BULLISH,
                                price_level=price,
                                reference_price_level=active_high.price,
                            )
                        )
                        if pending_low is not None:
                            active_low = pending_low
                            pending_low = None
                        active_high = pivot
                        pending_high = None
                        trend = MarketDirection.BULLISH
                    else:
                        events.append(
                            MarketStructure(
                                symbol=symbol,
                                timeframe=timeframe,
                                timestamp=timestamp,
                                event=StructureEvent.LIQUIDITY_SWEEP,
                                direction=MarketDirection.BULLISH,
                                price_level=price,
                                reference_price_level=active_high.price,
                            )
                        )
                        pending_high = pivot
                else:
                    label = self._label(price, last_high, _HIGH_PIVOT_LABELS)
                    if label is not None:
                        event_type, direction, reference_price = label
                        events.append(
                            MarketStructure(
                                symbol=symbol,
                                timeframe=timeframe,
                                timestamp=timestamp,
                                event=event_type,
                                direction=direction,
                                price_level=price,
                                reference_price_level=reference_price,
                            )
                        )
                    pending_high = pivot
                last_high = pivot
            else:
                if active_low is None:
                    active_low = pivot
                elif price < active_low.price:
                    if self._is_confirmed(
                        candles_by_timestamp[timestamp], active_low.price, bullish=False
                    ):
                        events.append(
                            MarketStructure(
                                symbol=symbol,
                                timeframe=timeframe,
                                timestamp=timestamp,
                                event=(
                                    StructureEvent.CHANGE_OF_CHARACTER
                                    if trend is MarketDirection.BULLISH
                                    else StructureEvent.BREAK_OF_STRUCTURE
                                ),
                                direction=MarketDirection.BEARISH,
                                price_level=price,
                                reference_price_level=active_low.price,
                            )
                        )
                        if pending_high is not None:
                            active_high = pending_high
                            pending_high = None
                        active_low = pivot
                        pending_low = None
                        trend = MarketDirection.BEARISH
                    else:
                        events.append(
                            MarketStructure(
                                symbol=symbol,
                                timeframe=timeframe,
                                timestamp=timestamp,
                                event=StructureEvent.LIQUIDITY_SWEEP,
                                direction=MarketDirection.BEARISH,
                                price_level=price,
                                reference_price_level=active_low.price,
                            )
                        )
                        pending_low = pivot
                else:
                    label = self._label(price, last_low, _LOW_PIVOT_LABELS)
                    if label is not None:
                        event_type, direction, reference_price = label
                        events.append(
                            MarketStructure(
                                symbol=symbol,
                                timeframe=timeframe,
                                timestamp=timestamp,
                                event=event_type,
                                direction=direction,
                                price_level=price,
                                reference_price_level=reference_price,
                            )
                        )
                    pending_low = pivot
                last_low = pivot

        return events

    def _is_confirmed(self, candle: Candle, active_price: float, *, bullish: bool) -> bool:
        """Whether `candle` confirms a break of `active_price`.

        Requires `candle.close` to be beyond `active_price` (not just a
        wick) and `volume_delta(candle)` to be at least
        `min_volume_delta_ratio` of `candle.volume`, in the breakout
        direction (`bullish`).
        """
        close_beyond = candle.close > active_price if bullish else candle.close < active_price
        if not close_beyond or candle.volume == 0:
            return False

        delta = volume_delta(candle)
        delta_in_direction = delta > 0 if bullish else delta < 0
        delta_ratio = abs(delta) / candle.volume
        return delta_in_direction and delta_ratio >= self._min_volume_delta_ratio

    @staticmethod
    def _label(
        price: float,
        last_pivot: _Pivot | None,
        labels: dict[bool, tuple[StructureEvent, MarketDirection]],
    ) -> tuple[StructureEvent, MarketDirection, float] | None:
        """HH/LH (or HL/LL) label for `price` vs. the previous same-type pivot.

        Returns `None` for the first pivot of its type (`last_pivot is None`)
        or when `price` exactly equals `last_pivot.price` (no higher/lower
        label applies).
        """
        if last_pivot is None or price == last_pivot.price:
            return None
        event_type, direction = labels[price > last_pivot.price]
        return event_type, direction, last_pivot.price
