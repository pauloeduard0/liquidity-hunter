"""Swing (major) market structure detector: BOS/CHoCH and HH/HL/LH/LL.

This module adapts the core idea of the LuxAlgo "Smart Money Concepts"
indicator (BOS/CHoCH from alternating swing highs/lows) with two key
changes to how the *active* reference levels are maintained and broken:

A new swing pivot does **not** immediately become the reference whose break
would flip the trend. It is only promoted to that role once the *opposite*
active reference is actually broken. Until then it is held as a "pending"
candidate -- specifically, the *most extreme* pivot of its kind (highest
high / lowest low) seen since the opposite active reference was last set,
not merely the most recent one. This avoids flagging a CHoCH against a minor
retracement pivot that was never structurally significant, and ensures the
promoted reference reflects the true extreme of the prior leg rather than
whichever pivot happened to form last.

When the active reference *on a given side* is itself broken (a
BREAK_OF_STRUCTURE or CHANGE_OF_CHARACTER on that side), the new active
reference for that side is not simply the breaking pivot -- it is the more
extreme of the breaking pivot and whatever had been accumulating as that
side's own pending candidate since the reference was last set. This matters
when an earlier same-side LIQUIDITY_SWEEP reached further than the pivot
that goes on to confirm the break: the active reference becomes that
earlier, more extreme sweep level, not the (less extreme) confirming pivot,
so it continues to represent the true extreme of the leg that just ended.

A BREAK_OF_STRUCTURE/CHANGE_OF_CHARACTER on one side also retires the
*opposite* side's active reference, which belonged to the leg that just
ended: it is replaced by that side's pending candidate (the extreme pivot
accumulated during the leg), promoted to active. If no such pivot has formed
yet (pending is empty), the opposite active reference is discarded to `None`
rather than left at its now-stale prior value. While an active reference is
`None`, pivots on that side cannot trigger a BOS/CHoCH -- they are purely
descriptive HH/HL/LH/LL labels that accumulate into pending, until the next
opposite-side BOS/CHoCH promotes that accumulation to active. The very first
pivot of each kind (the bootstrap) is also seeded into the opposite side's
pending candidate if that opposite side has already been bootstrapped -- it
chronologically falls within that side's active-creation window, and is
therefore a legitimate promotion candidate for it later.

Structure (price action) and confirmation (volume) are kept separate:

- A pivot that breaks the active reference on its side *in the direction of
  the current `trend`* (a `BREAK_OF_STRUCTURE` -- including the first break
  while `trend` is still `NEUTRAL`) is reported on price alone: a wick
  beyond the active reference is sufficient, regardless of where the candle
  closes or its `volume_delta` (see `indicators.volume_delta`).
- A pivot that breaks the active reference *against* the current `trend` is
  only confirmed as a `CHANGE_OF_CHARACTER` if the candle that formed it
  also *closes* beyond that reference AND has a `volume_delta` of at least
  `min_volume_delta_ratio` (relative to its `volume`) in the breakout
  direction. If either condition fails, the active reference is considered
  swept rather than broken: it stays unchanged and a
  `StructureEvent.LIQUIDITY_SWEEP` event is emitted instead.

Every pivot that does *not* break the active reference on its side is also
labeled HH/HL/LH/LL by comparing it to the previous pivot of the same type
-- a purely descriptive observation, independent of the active/pending
state machine.
"""

from dataclasses import dataclass
from datetime import datetime

from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    StructureEvent,
)
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
    - `pending_high`/`pending_low`: the highest high / lowest low pivot on
      each side, among those that have *not* broken their active
      counterpart, seen since the *opposite* active level was last set. A
      pending pivot is only promoted to active once the opposite active
      level is broken -- at which point it represents the most extreme
      point reached during the leg that just ended, i.e. the natural
      reference for the next reversal in the other direction. If nothing
      has accumulated in pending at that point, the opposite active
      reference is discarded to `None` instead -- it belonged to the leg
      that just ended and would otherwise be left stale. While an active
      reference is `None`, pivots on that side are purely descriptive
      HH/HL/LH/LL labels (they accumulate into pending but cannot trigger a
      BOS/CHoCH) until the next opposite-side BOS/CHoCH promotes that
      accumulation to active.

    A pivot whose price exceeds the active reference on its side, *in the
    direction of `trend`* (or the first such break while `trend` is still
    `NEUTRAL`), is reported as a `BREAK_OF_STRUCTURE` on price alone -- a
    wick beyond the active reference is enough, regardless of the candle's
    close or `volume_delta`. A pivot that exceeds the active reference
    *against* `trend` is only confirmed as a `CHANGE_OF_CHARACTER` if the
    candle that formed it also closes beyond that reference and its
    `volume_delta` ratio (`abs(volume_delta) / volume`) is at least
    `min_volume_delta_ratio` in the breakout direction. Otherwise the active
    reference is left unchanged and a `StructureEvent.LIQUIDITY_SWEEP` is
    reported -- the swept pivot becomes the new `pending_high`/`pending_low`,
    so it can still be promoted to active later if the opposite side breaks.

    In either case (BOS or CHoCH), the new active reference on that side is
    `_extreme(pending_<side>, breaking_pivot)` -- the more extreme of the
    breaking pivot and that side's own pending accumulation, which folds in
    any earlier same-side sweep that reached further than the pivot
    confirming the break.

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
                if active_high is None and last_high is None:
                    active_high = pivot
                    if last_low is not None:
                        pending_high = pivot
                elif active_high is not None and price > active_high.price:
                    is_reversal = trend is MarketDirection.BEARISH
                    if not is_reversal or self._is_confirmed(
                        candles_by_timestamp[timestamp], active_high.price, bullish=True
                    ):
                        events.append(
                            MarketStructure(
                                symbol=symbol,
                                timeframe=timeframe,
                                timestamp=timestamp,
                                event=(
                                    StructureEvent.CHANGE_OF_CHARACTER
                                    if is_reversal
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
                        else:
                            active_low = None
                        active_high = self._extreme(pending_high, pivot, higher=True)
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
                        pending_high = self._extreme(pending_high, pivot, higher=True)
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
                    pending_high = self._extreme(pending_high, pivot, higher=True)
                last_high = pivot
            else:
                if active_low is None and last_low is None:
                    active_low = pivot
                    if last_high is not None:
                        pending_low = pivot
                elif active_low is not None and price < active_low.price:
                    is_reversal = trend is MarketDirection.BULLISH
                    if not is_reversal or self._is_confirmed(
                        candles_by_timestamp[timestamp], active_low.price, bullish=False
                    ):
                        events.append(
                            MarketStructure(
                                symbol=symbol,
                                timeframe=timeframe,
                                timestamp=timestamp,
                                event=(
                                    StructureEvent.CHANGE_OF_CHARACTER
                                    if is_reversal
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
                        else:
                            active_high = None
                        active_low = self._extreme(pending_low, pivot, higher=False)
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
                        pending_low = self._extreme(pending_low, pivot, higher=False)
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
                    pending_low = self._extreme(pending_low, pivot, higher=False)
                last_low = pivot

        return events

    def _is_confirmed(self, candle: Candle, active_price: float, *, bullish: bool) -> bool:
        """Whether `candle` confirms a counter-trend break of `active_price` as a CHoCH.

        Requires `candle.close` to be beyond `active_price` (not just a
        wick) and `volume_delta(candle)` to be at least
        `min_volume_delta_ratio` of `candle.volume`, in the breakout
        direction (`bullish`). Only called for breaks against the current
        `trend`; breaks in the direction of `trend` (BOS) are confirmed by
        price alone.
        """
        close_beyond = candle.close > active_price if bullish else candle.close < active_price
        if not close_beyond or candle.volume == 0:
            return False

        delta = volume_delta(candle)
        delta_in_direction = delta > 0 if bullish else delta < 0
        delta_ratio = abs(delta) / candle.volume
        return delta_in_direction and delta_ratio >= self._min_volume_delta_ratio

    @staticmethod
    def _extreme(current: "_Pivot | None", candidate: "_Pivot", *, higher: bool) -> "_Pivot":
        """The more extreme of `current` and `candidate`.

        Used to accumulate `pending_high`/`pending_low` as the highest high
        / lowest low pivot seen since the opposite active level was last
        set, rather than simply the most recently formed pivot.
        `higher=True` keeps the higher-priced pivot (`pending_high`);
        `higher=False` keeps the lower-priced one (`pending_low`).
        """
        if current is None:
            return candidate
        if higher:
            return candidate if candidate.price > current.price else current
        return candidate if candidate.price < current.price else current

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
