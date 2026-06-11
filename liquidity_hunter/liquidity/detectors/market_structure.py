"""Swing (major) market structure detector: BOS / CHoCH from swing pivots.

This module adapts the core idea of the LuxAlgo "Smart Money Concepts"
indicator (BOS/CHoCH from alternating swing highs/lows) with one key
change to how the *active* reference levels are maintained:

A new swing pivot does **not** immediately become the reference whose break
would flip the trend. It is only promoted to that role once the *opposite*
active reference is actually broken. Until then it is held as a "pending"
candidate. This avoids flagging a CHoCH against a minor retracement pivot
that was never structurally significant.
"""

from dataclasses import dataclass
from datetime import datetime

from liquidity_hunter.core.domain import Candle, MarketDirection, MarketStructure, StructureEvent
from liquidity_hunter.liquidity.detectors._common import validate_candles
from liquidity_hunter.liquidity.detectors.base import MarketStructureDetector
from liquidity_hunter.liquidity.detectors.swing_points import SwingHighDetector, SwingLowDetector


@dataclass(frozen=True)
class _Pivot:
    price: float
    timestamp: datetime


class SwingStructureDetector(MarketStructureDetector):
    """Detects BOS/CHoCH events from major (swing) pivots.

    Swing highs/lows are sourced from `SwingHighDetector`/`SwingLowDetector`
    using `swing_lookback`, then walked in chronological order maintaining
    two pairs of reference levels:

    - `active_high`/`active_low`: the confirmed references whose break
      produces a BOS (trend continuation) or CHoCH (trend reversal) event.
    - `pending_high`/`pending_low`: the most recent swing pivot on each
      side that has *not yet* broken its active counterpart. A pending
      pivot is only promoted to active once the opposite active level is
      broken.

    A break is confirmed as soon as a new swing pivot's price exceeds the
    active reference on its side. This is a provisional confirmation rule
    (no false-breakout/liquidity-sweep filtering yet) and is expected to be
    refined once volume-delta data is available.
    """

    def __init__(self, swing_lookback: int = 50) -> None:
        self._high_detector = SwingHighDetector(lookback=swing_lookback)
        self._low_detector = SwingLowDetector(lookback=swing_lookback)

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

        events: list[MarketStructure] = []
        active_high: _Pivot | None = None
        active_low: _Pivot | None = None
        pending_high: _Pivot | None = None
        pending_low: _Pivot | None = None
        trend = MarketDirection.NEUTRAL

        for timestamp, kind, price in pivots:
            pivot = _Pivot(price=price, timestamp=timestamp)

            if kind == "high":
                if active_high is None:
                    active_high = pivot
                elif price > active_high.price:
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
                    pending_high = pivot
            else:
                if active_low is None:
                    active_low = pivot
                elif price < active_low.price:
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
                    pending_low = pivot

        return events
