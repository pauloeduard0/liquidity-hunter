"""Internal (minor) market structure detector: trailing-reference BOS/CHoCH/HL/LH.

`SwingStructureDetector` deliberately holds an active reference until the
*opposite* side breaks, so the reference reflects the true extreme of the
prior leg rather than whichever pivot formed last -- the right behavior for
`StructureScope.MAJOR`, where the goal is not to flag a CHoCH against a minor
retracement.

For `StructureScope.INTERNAL`, that same design has a failure mode: if an
active reference happens to equal the extreme (max/min) of the entire
remaining candle window, it can never be broken again, which permanently
freezes the *opposite* side's reference too (it is only promoted when the
opposite side breaks). A large subsequent move then goes undetected as
BOS/CHoCH for the rest of the window -- only descriptive HH/HL/LH/LL labels
are emitted.

`InternalStructureDetector` keeps `active_high`/`active_low` as *trailing*
references -- normally the most recently formed swing high/low pivot,
updated after every pivot of that kind -- so both stay close to current
price. But a purely trailing reference has its own failure mode: comparing a
CHoCH against the last pivot, which may be a minor retracement rather than
the true extreme of the leg that just ended, can spuriously flag a
continuation BOS right after the reversal. To avoid that, `pending_high`/
`pending_low` accumulate the most extreme high/low pivot seen for their side
since it was last set as active, mirroring `SwingStructureDetector`'s pending
mechanism:

- A pivot that breaks the active reference on its side *and* is confirmed as
  a BOS/CHoCH promotes the *opposite* side's `pending_<side>` to
  `active_<side>` (or `None`, if nothing has accumulated there yet) -- the
  leg that just ended is over, so its trailing reference is retired in favor
  of the extreme accumulated during that leg. If `active_<side>` becomes
  `None`, the next pivot on that side silently re-bootstraps (no label) --
  the accepted cost of carrying forward "extreme of the prior leg" semantics
  instead of "last pivot".
- A pivot that breaks the active reference but is *not* confirmed (a
  `LIQUIDITY_SWEEP`), or that does not break it at all (a HL/LH label),
  instead folds the *opposite* side's current `active_<side>` into
  `pending_<side>` (via `_extreme`), so that value is not lost when
  `active_<side>` is later overwritten by its own next pivot.
- Bootstrapping a side (its `active_<side>` was `None`) also seeds
  `pending_<side>` with the same pivot, if the opposite side is already
  active -- the bootstrap pivot is simultaneously the new trailing reference
  and a valid promotion candidate for the window that is just beginning.

A pivot that exceeds the active reference on its side, in the direction of
`trend` (or the first such break while `trend` is still `NEUTRAL`), is a
`BREAK_OF_STRUCTURE` on price alone. A pivot that exceeds the active
reference *against* `trend` is a `CHANGE_OF_CHARACTER` if confirmed, or a
`LIQUIDITY_SWEEP` otherwise. A pivot that does not exceed the active
reference is labeled `LOWER_HIGH`/`HIGHER_LOW`.

Confirmation is *persistence*-based rather than volume-based (see
`_common.is_sustained_break`): the breaking candle must close beyond the
reference AND the `persistence_candles` candles immediately following it
must also close beyond it. A single high-volume candle that pokes through a
level and immediately reverts (a "false break") fails this check and is
reported as a `LIQUIDITY_SWEEP`; a break that *holds* for `persistence_candles`
candles is reported as a `CHANGE_OF_CHARACTER`. If there are not yet enough
candles after the pivot to evaluate the window, the break is treated as
unconfirmed (`LIQUIDITY_SWEEP`). This applies only to
`InternalStructureDetector`; `SwingStructureDetector`'s `volume_delta`-ratio
confirmation is unaffected.
"""

from datetime import datetime

from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    StructureScope,
)
from liquidity_hunter.liquidity.detectors._common import (
    Pivot,
    collect_pivots,
    is_sustained_break,
    validate_candles,
)
from liquidity_hunter.liquidity.detectors.base import MarketStructureDetector
from liquidity_hunter.liquidity.detectors.swing_points import SwingHighDetector, SwingLowDetector


class InternalStructureDetector(MarketStructureDetector):
    """Detects internal BOS/CHoCH/HL/LH from a trailing swing pivot reference.

    Swing highs/lows are sourced from `SwingHighDetector`/`SwingLowDetector`
    using `swing_lookback`, then walked in chronological order maintaining
    `active_high`/`active_low` (trailing references, normally the most
    recently formed pivot of that kind) and `pending_high`/`pending_low`
    (the most extreme pivot of that kind accumulated for a future promotion).

    For each new pivot, it is compared against the *current* `active_high`/
    `active_low`:

    - If `active_<side>` is `None`, this pivot bootstraps it: `active_<side>
      = pivot`, with no event. If the opposite side is already active,
      `pending_<side>` is also seeded with this pivot.
    - A high pivot above `active_high` (a low pivot below `active_low`) in
      the direction of `trend` (or the first such break while `trend` is
      `NEUTRAL`) is a `BREAK_OF_STRUCTURE`; against `trend`, it is a
      `CHANGE_OF_CHARACTER` if the break is sustained -- the candle that
      formed it AND the `persistence_candles` candles immediately following
      it all close beyond the reference (see `_common.is_sustained_break`)
      -- otherwise a `LIQUIDITY_SWEEP`.
      - On a confirmed BOS/CHoCH, `trend` is updated and the *opposite*
        side's `pending_<side>` is promoted to `active_<side>` (or `None`
        if `pending_<side>` is empty), then cleared.
      - On a `LIQUIDITY_SWEEP`, the opposite side's current `active_<side>`
        is folded into its `pending_<side>` via `_extreme` instead.
    - A high pivot below `active_high` (a low pivot above `active_low`) is a
      descriptive `LOWER_HIGH`/`HIGHER_LOW` label, and also folds the
      opposite side's `active_<side>` into its `pending_<side>`.
    - A pivot exactly equal to `active_<side>` produces no event and does
      not touch either `pending_<side>`.

    In every case, `active_<side>` is then set to this pivot (the trailing
    update). Every `MarketStructure` emitted has `scope =
    StructureScope.INTERNAL`.

    `persistence_candles` is the number of candles immediately following a
    counter-trend pivot that must also close beyond the reference for the
    break to be reported as a `CHANGE_OF_CHARACTER` rather than a
    `LIQUIDITY_SWEEP`.
    """

    def __init__(self, swing_lookback: int = 10, persistence_candles: int = 3) -> None:
        if persistence_candles < 1:
            raise ValueError("persistence_candles must be at least 1")
        self._high_detector = SwingHighDetector(lookback=swing_lookback)
        self._low_detector = SwingLowDetector(lookback=swing_lookback)
        self._persistence_candles = persistence_candles

    def detect(self, candles: list[Candle]) -> list[MarketStructure]:
        validate_candles(candles)

        pivots = collect_pivots(candles, self._high_detector, self._low_detector)

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        index_by_timestamp = {candle.timestamp: index for index, candle in enumerate(candles)}

        def confirms_break(timestamp: datetime, active_price: float, *, bullish: bool) -> bool:
            return is_sustained_break(
                candles,
                index_by_timestamp[timestamp],
                active_price,
                bullish=bullish,
                persistence_candles=self._persistence_candles,
            )

        events: list[MarketStructure] = []
        active_high: Pivot | None = None
        active_low: Pivot | None = None
        pending_high: Pivot | None = None
        pending_low: Pivot | None = None
        trend = MarketDirection.NEUTRAL

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
                    scope=StructureScope.INTERNAL,
                )
            )

        for timestamp, kind, price in pivots:
            pivot = Pivot(price=price, timestamp=timestamp)

            if kind == "high":
                if active_high is None:
                    if active_low is not None:
                        pending_high = pivot
                elif price > active_high.price:
                    is_reversal = trend is MarketDirection.BEARISH
                    if not is_reversal or confirms_break(
                        timestamp, active_high.price, bullish=True
                    ):
                        emit(
                            timestamp,
                            StructureEvent.CHANGE_OF_CHARACTER
                            if is_reversal
                            else StructureEvent.BREAK_OF_STRUCTURE,
                            MarketDirection.BULLISH,
                            price,
                            active_high.price,
                        )
                        trend = MarketDirection.BULLISH
                        active_low = pending_low
                        pending_low = None
                    else:
                        emit(
                            timestamp,
                            StructureEvent.LIQUIDITY_SWEEP,
                            MarketDirection.BULLISH,
                            price,
                            active_high.price,
                        )
                        pending_low = self._extreme(pending_low, active_low, higher=False)
                elif price < active_high.price:
                    emit(
                        timestamp,
                        StructureEvent.LOWER_HIGH,
                        MarketDirection.BEARISH,
                        price,
                        active_high.price,
                    )
                    pending_low = self._extreme(pending_low, active_low, higher=False)
                active_high = pivot
            else:
                if active_low is None:
                    if active_high is not None:
                        pending_low = pivot
                elif price < active_low.price:
                    is_reversal = trend is MarketDirection.BULLISH
                    if not is_reversal or confirms_break(
                        timestamp, active_low.price, bullish=False
                    ):
                        emit(
                            timestamp,
                            StructureEvent.CHANGE_OF_CHARACTER
                            if is_reversal
                            else StructureEvent.BREAK_OF_STRUCTURE,
                            MarketDirection.BEARISH,
                            price,
                            active_low.price,
                        )
                        trend = MarketDirection.BEARISH
                        active_high = pending_high
                        pending_high = None
                    else:
                        emit(
                            timestamp,
                            StructureEvent.LIQUIDITY_SWEEP,
                            MarketDirection.BEARISH,
                            price,
                            active_low.price,
                        )
                        pending_high = self._extreme(pending_high, active_high, higher=True)
                elif price > active_low.price:
                    emit(
                        timestamp,
                        StructureEvent.HIGHER_LOW,
                        MarketDirection.BULLISH,
                        price,
                        active_low.price,
                    )
                    pending_high = self._extreme(pending_high, active_high, higher=True)
                active_low = pivot

        return events

    @staticmethod
    def _extreme(current: Pivot | None, candidate: Pivot | None, *, higher: bool) -> Pivot | None:
        """The more extreme of `current` and `candidate`, by price.

        Either may be `None`; returns whichever of the two is non-`None`, or
        `None` if both are. `higher=True` keeps the higher-priced pivot (for
        `pending_high`); `higher=False` keeps the lower-priced one (for
        `pending_low`).
        """
        if candidate is None:
            return current
        if current is None:
            return candidate
        if higher:
            return candidate if candidate.price > current.price else current
        return candidate if candidate.price < current.price else current
