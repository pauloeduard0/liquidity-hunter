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

Structure (price action) and confirmation are kept separate for both event
types:

- A pivot that breaks the active reference on its side *in the direction of
  the current `trend`* (a `BREAK_OF_STRUCTURE` -- including the first break
  while `trend` is still `NEUTRAL`) always advances state (trend, active
  references). An event is only emitted when the first candle in the leg
  whose *close* crosses the reference also passes the optional LuxAlgo-style
  shadow-balance confluence filter (see `_common.bos_confluence`); if no
  such candle exists, or if `confluence_filter` is enabled and the closing
  candle fails it, the state change is silent. The BOS timestamp is that
  candle's timestamp rather than the pivot's.
- A pivot that breaks the active reference *against* the current `trend` is
  confirmed as a `CHANGE_OF_CHARACTER` when `persistence_candles` closes
  beyond the reference form a sustained window anywhere within the leg
  leading up to the pivot (see `_common.is_sustained_break`). If no such
  window exists, the break is reported as a `StructureEvent.LIQUIDITY_SWEEP`
  instead and the active reference remains unchanged. The CHoCH timestamp is
  the first candle at which a sustained window begins; the SWEEP timestamp
  is the first candle whose wick crosses the reference.

Every pivot that does *not* break the active reference on its side is also
labeled HH/HL/LH/LL by comparing it to the previous pivot of the same type
-- a purely descriptive observation, independent of the active/pending
state machine.
"""

from datetime import datetime

from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    StructureEvent,
)
from liquidity_hunter.liquidity.detectors._common import (
    Pivot,
    bos_confluence,
    collect_pivots,
    find_close_break_index,
    find_sustained_break_index,
    find_wick_break_index,
    is_sustained_break,
    validate_candles,
)
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

    BOS (in-trend break): state always advances on any wick break of the
    active reference; a BOS event is only emitted when the first candle in
    the leg whose close crosses the reference also passes the optional
    `confluence_filter` (shadow-balance check). If no confirming close
    exists, or if confluence fails, the state change is silent. This matches
    `InternalStructureDetector`'s BOS confirmation logic.

    CHoCH (counter-trend break): confirmed when `persistence_candles` closes
    beyond the active reference hold in a sustained window anywhere within
    the leg leading up to the breaking pivot (see `is_sustained_break`). A
    break that does not hold is a `LIQUIDITY_SWEEP` instead -- the active
    reference stays unchanged and the swept pivot folds into `pending`.

    In either case (BOS or CHoCH), the new active reference on that side is
    `_extreme(pending_<side>, breaking_pivot)` -- the more extreme of the
    breaking pivot and that side's own pending accumulation, which folds in
    any earlier same-side sweep that reached further than the pivot
    confirming the break.

    For BOS/CHoCH/SWEEP, the emitted timestamp is the actual breaking
    candle (first close beyond reference for BOS, first sustained-break
    window for CHoCH, first wick beyond reference for SWEEP), not the
    forming pivot -- the pivot's timestamp marks the extreme of the new leg,
    not where the prior level was actually crossed.
    """

    def __init__(
        self,
        swing_lookback: int = 15,
        persistence_candles: int = 10,
        confluence_filter: bool = True,
    ) -> None:
        if persistence_candles < 1:
            raise ValueError("persistence_candles must be at least 1")
        self._high_detector = SwingHighDetector(lookback=swing_lookback)
        self._low_detector = SwingLowDetector(lookback=swing_lookback)
        self._persistence_candles = persistence_candles
        self._confluence_filter = confluence_filter

    def detect(self, candles: list[Candle]) -> list[MarketStructure]:
        validate_candles(candles)

        pivots = collect_pivots(candles, self._high_detector, self._low_detector)

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        index_by_timestamp = {candle.timestamp: i for i, candle in enumerate(candles)}

        def confirms_break(
            start_index: int, end_index: int, level_price: float, *, bullish: bool
        ) -> bool:
            return any(
                is_sustained_break(
                    candles,
                    index,
                    level_price,
                    bullish=bullish,
                    persistence_candles=self._persistence_candles,
                )
                for index in range(start_index, end_index + 1)
            )

        events: list[MarketStructure] = []
        active_high: Pivot | None = None
        active_low: Pivot | None = None
        pending_high: Pivot | None = None
        pending_low: Pivot | None = None
        last_high: Pivot | None = None
        last_low: Pivot | None = None
        trend = MarketDirection.NEUTRAL
        prev_high_pivot_index = -1
        prev_low_pivot_index = -1

        def emit(
            timestamp: datetime,
            event: StructureEvent,
            direction: MarketDirection,
            price_level: float,
            reference_price_level: float,
        ) -> None:
            events.append(
                MarketStructure(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=timestamp,
                    event=event,
                    direction=direction,
                    price_level=price_level,
                    reference_price_level=reference_price_level,
                )
            )

        for timestamp, kind, price in pivots:
            pivot = Pivot(price=price, timestamp=timestamp)
            current_index = index_by_timestamp[timestamp]

            if kind == "high":
                if active_high is None and last_high is None:
                    active_high = pivot
                    if last_low is not None:
                        pending_high = pivot
                elif active_high is not None and price > active_high.price:
                    ref_price = active_high.price
                    is_reversal = trend is MarketDirection.BEARISH
                    start = prev_high_pivot_index + 1

                    if is_reversal:
                        if confirms_break(start, current_index, ref_price, bullish=True):
                            if pending_low is not None:
                                active_low = pending_low
                                pending_low = None
                            else:
                                active_low = None
                            active_high = self._extreme(pending_high, pivot, higher=True)
                            pending_high = None
                            trend = MarketDirection.BULLISH
                            break_idx = find_sustained_break_index(
                                candles,
                                start,
                                current_index,
                                ref_price,
                                bullish=True,
                                persistence_candles=self._persistence_candles,
                            )
                            emit(
                                candles[break_idx].timestamp,
                                StructureEvent.CHANGE_OF_CHARACTER,
                                MarketDirection.BULLISH,
                                price,
                                ref_price,
                            )
                        else:
                            sweep_idx = find_wick_break_index(
                                candles, start, current_index, ref_price, bullish=True
                            )
                            emit(
                                candles[sweep_idx].timestamp,
                                StructureEvent.LIQUIDITY_SWEEP,
                                MarketDirection.BULLISH,
                                price,
                                ref_price,
                            )
                            pending_high = self._extreme(pending_high, pivot, higher=True)
                    else:
                        if pending_low is not None:
                            active_low = pending_low
                            pending_low = None
                        else:
                            active_low = None
                        active_high = self._extreme(pending_high, pivot, higher=True)
                        pending_high = None
                        trend = MarketDirection.BULLISH
                        close_idx = find_close_break_index(
                            candles, start, current_index, ref_price, bullish=True
                        )
                        if close_idx is not None and (
                            not self._confluence_filter
                            or bos_confluence(candles[close_idx], bullish=True)
                        ):
                            emit(
                                candles[close_idx].timestamp,
                                StructureEvent.BREAK_OF_STRUCTURE,
                                MarketDirection.BULLISH,
                                price,
                                ref_price,
                            )
                else:
                    label = self._label(price, last_high, _HIGH_PIVOT_LABELS)
                    if label is not None:
                        event_type, direction, reference_price = label
                        emit(timestamp, event_type, direction, price, reference_price)
                    pending_high = self._extreme(pending_high, pivot, higher=True)

                last_high = pivot
                prev_high_pivot_index = current_index

            else:
                if active_low is None and last_low is None:
                    active_low = pivot
                    if last_high is not None:
                        pending_low = pivot
                elif active_low is not None and price < active_low.price:
                    ref_price = active_low.price
                    is_reversal = trend is MarketDirection.BULLISH
                    start = prev_low_pivot_index + 1

                    if is_reversal:
                        if confirms_break(start, current_index, ref_price, bullish=False):
                            if pending_high is not None:
                                active_high = pending_high
                                pending_high = None
                            else:
                                active_high = None
                            active_low = self._extreme(pending_low, pivot, higher=False)
                            pending_low = None
                            trend = MarketDirection.BEARISH
                            break_idx = find_sustained_break_index(
                                candles,
                                start,
                                current_index,
                                ref_price,
                                bullish=False,
                                persistence_candles=self._persistence_candles,
                            )
                            emit(
                                candles[break_idx].timestamp,
                                StructureEvent.CHANGE_OF_CHARACTER,
                                MarketDirection.BEARISH,
                                price,
                                ref_price,
                            )
                        else:
                            sweep_idx = find_wick_break_index(
                                candles, start, current_index, ref_price, bullish=False
                            )
                            emit(
                                candles[sweep_idx].timestamp,
                                StructureEvent.LIQUIDITY_SWEEP,
                                MarketDirection.BEARISH,
                                price,
                                ref_price,
                            )
                            pending_low = self._extreme(pending_low, pivot, higher=False)
                    else:
                        if pending_high is not None:
                            active_high = pending_high
                            pending_high = None
                        else:
                            active_high = None
                        active_low = self._extreme(pending_low, pivot, higher=False)
                        pending_low = None
                        trend = MarketDirection.BEARISH
                        close_idx = find_close_break_index(
                            candles, start, current_index, ref_price, bullish=False
                        )
                        if close_idx is not None and (
                            not self._confluence_filter
                            or bos_confluence(candles[close_idx], bullish=False)
                        ):
                            emit(
                                candles[close_idx].timestamp,
                                StructureEvent.BREAK_OF_STRUCTURE,
                                MarketDirection.BEARISH,
                                price,
                                ref_price,
                            )
                else:
                    label = self._label(price, last_low, _LOW_PIVOT_LABELS)
                    if label is not None:
                        event_type, direction, reference_price = label
                        emit(timestamp, event_type, direction, price, reference_price)
                    pending_low = self._extreme(pending_low, pivot, higher=False)

                last_low = pivot
                prev_low_pivot_index = current_index

        return events

    @staticmethod
    def _extreme(current: "Pivot | None", candidate: "Pivot", *, higher: bool) -> "Pivot":
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
        last_pivot: "Pivot | None",
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
