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

A `BREAK_OF_STRUCTURE`'s `reference_price_level` is simply `active_<side>`
(the level it broke). A `CHANGE_OF_CHARACTER`'s reference is more subtle:
`active_<side>` is a *trailing* reference and may be a minor pivot formed
*after* the most recent opposite-side extreme that defined the leg now being
reversed -- using it (or `pending_<side>`) as the CHoCH level would flag a
merely-internal bounce as a structural reversal. `choch_candidate_high`/
`choch_candidate_low` track the *actual* level a CHoCH must clear: the
`active_<side>` that was active -- i.e. the extreme of the leg leading into
the opposite side's most recent confirmed *reversal* (CHoCH) -- at the moment
that opposite-side CHoCH "spent" it (see below). A counter-trend break is
only a `CHANGE_OF_CHARACTER` if it is a sustained break of `active_<side>`
*and* (when `choch_candidate_<side>` has been recorded) the pivot also clears
`choch_candidate_<side>`; otherwise it is a `LIQUIDITY_SWEEP` (trend
unchanged) -- an internal bounce within the leg that `choch_candidate_<side>`
still defines. The confirmed CHoCH's `reference_price_level` is
`choch_candidate_<side>` if recorded, else the same max/min-of-active/pending
fallback used before `choch_candidate_<side>` existed.

Every confirmed BOS/CHoCH performs the `active_<side> = pending_<side>;
pending_<side> = None` reset on the *opposite* side (the leg on that side's
trailing reference is retired in favor of its pending accumulation,
regardless of whether this pivot is a reversal or a same-direction
continuation). But `choch_candidate_<side>` is only snapshotted from the
pre-reset `active_<side>` when this pivot is itself a confirmed *reversal*
(`is_reversal`): only a reversal means a leg on the opposite side has
genuinely ended, making its pre-reset `active_<side>` "the extreme of the leg
that just ended" -- the correct future CHoCH reference. A same-direction
continuation BOS does not end any leg on the opposite side; its pre-reset
`active_<side>` is typically just a post-reversal pullback pivot formed
*during* the current leg, and snapshotting it would overwrite a correct
`choch_candidate_<side>` with a spurious, too-close level (causing a CHoCH to
fire against an internal pullback rather than the true start of the leg).
The snapshot, once taken on a reversal, survives subsequent continuation
BOS/CHoCH events on the same side -- and survives `active_<side>` becoming
`None` and silently re-bootstrapping on a post-reversal pullback pivot, which
must NOT become the next CHoCH's reference.

`choch_candidate_<side>` is not frozen at that initial value for the rest of
the leg, though: a re-bootstrapped pivot with no opposite-side confirmation
yet (like the post-reversal pullback pivot above) must not become
`choch_candidate_<side>`, but a *later* LH/HL that forms *after* the
opposite side has confirmed at least one HL/LH of its own is itself part of
the current leg's structure -- a closer, more relevant level for a CHoCH to
clear than the (possibly much older) level recorded at the leg's start. So
`choch_candidate_high` *ratchets* to a `LOWER_HIGH` pivot if `trend` is
`BEARISH` and a `HIGHER_LOW` has been confirmed since `choch_candidate_high`
was last set (mirrored: `choch_candidate_low` ratchets to a `HIGHER_LOW`
pivot if `trend` is `BULLISH` and a `LOWER_HIGH` has been confirmed since
`choch_candidate_low` was last set). Each `HIGHER_LOW`/`LOWER_HIGH` label
both performs its own side's ratchet check and arms the *opposite* side's
ratchet for next time; setting `choch_candidate_<side>` (at a confirmed
BOS/CHoCH) disarms it again. Otherwise, `choch_candidate_<side>` is left
untouched by re-bootstrapping and `LIQUIDITY_SWEEP`s.

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
    recently formed pivot of that kind), `pending_high`/`pending_low` (the
    most extreme pivot of that kind accumulated for a future promotion), and
    `choch_candidate_high`/`choch_candidate_low` (the level a CHoCH on that
    side must clear -- see module docstring).

    For each new pivot, it is compared against the *current* `active_high`/
    `active_low`:

    - If `active_<side>` is `None`, this pivot bootstraps it: `active_<side>
      = pivot`, with no event. If the opposite side is already active,
      `pending_<side>` is also seeded with this pivot.
      `choch_candidate_<side>` is left untouched.
    - A high pivot above `active_high` (a low pivot below `active_low`) in
      the direction of `trend` (or the first such break while `trend` is
      `NEUTRAL`) is a `BREAK_OF_STRUCTURE`; against `trend`, it is a
      `CHANGE_OF_CHARACTER` if the break is sustained -- the candle that
      formed it AND the `persistence_candles` candles immediately following
      it all close beyond the reference (see `_common.is_sustained_break`)
      -- AND, if `choch_candidate_<side>` has been recorded, the pivot also
      clears `choch_candidate_<side>`. Otherwise it is a `LIQUIDITY_SWEEP`.
      - On a confirmed BOS/CHoCH, `trend` is updated and the *opposite*
        side's `pending_<side>` is promoted to `active_<side>` (or `None` if
        `pending_<side>` is empty) and cleared. Additionally, if this pivot
        is itself a confirmed reversal (`is_reversal`) and the opposite
        side's pre-reset `active_<side>` is not `None`, it is saved as that
        side's `choch_candidate_<side>` (it was the extreme of the leg this
        reversal just ended) -- a same-direction continuation BOS does not
        touch `choch_candidate_<side>`. A confirmed `CHANGE_OF_CHARACTER`'s
        `reference_price_level` is `choch_candidate_<side>` (the same side as
        the breaking pivot) if recorded, else the max/min-of-active/pending
        fallback; a `BREAK_OF_STRUCTURE`'s is always `active_<side>`.
      - On a `LIQUIDITY_SWEEP`, the opposite side's current `active_<side>`
        is folded into its `pending_<side>` via `_extreme` instead, and
        `choch_candidate_<side>` (either side) is untouched.
    - A high pivot below `active_high` (a low pivot above `active_low`) is a
      descriptive `LOWER_HIGH`/`HIGHER_LOW` label, and also folds the
      opposite side's `active_<side>` into its `pending_<side>`. If `trend`
      is `BEARISH` (`BULLISH`) and a `HIGHER_LOW` (`LOWER_HIGH`) has been
      confirmed since `choch_candidate_high` (`choch_candidate_low`) was
      last set, this pivot *ratchets* `choch_candidate_high`
      (`choch_candidate_low`) to itself -- see module docstring. Either way,
      this pivot arms the *opposite* side's ratchet for its next LH/HL.
    - A pivot exactly equal to `active_<side>` produces no event and does
      not touch either `pending_<side>` or `choch_candidate_<side>`.

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
        # The swing high/low that defined the leg leading to the most recent
        # confirmed *reversal* (CHoCH) on the *opposite* side -- the level a
        # CHoCH must clear to represent a real change of character, as
        # opposed to an internal bounce within the still-active leg. Persists
        # across active_<side> resets to `None`; only updated when a
        # confirmed bearish/bullish CHoCH on the opposite side "spends" the
        # current active_<side>. A same-direction continuation BOS on the
        # opposite side leaves it untouched.
        choch_candidate_high: Pivot | None = None
        choch_candidate_low: Pivot | None = None
        # Whether a confirmed HIGHER_LOW/LOWER_HIGH has occurred since
        # choch_candidate_high/choch_candidate_low was last set (by a
        # confirmed bearish/bullish CHoCH "spending" active_<side>).
        # Gates the choch_candidate_<side> ratchet below: a LH/HL pivot only
        # refines choch_candidate_<side> toward itself once at least one
        # opposite-side pivot has confirmed the current leg's structure --
        # otherwise it may be a re-bootstrap pullback top/bottom that doesn't
        # represent the leg yet (e.g. the first high after a fresh LL, before
        # any higher low has formed).
        higher_low_since_choch_candidate_high = False
        lower_high_since_choch_candidate_low = False
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
                    if is_reversal:
                        # A CHoCH is only structurally valid if it clears
                        # choch_candidate_high -- the swing high that defined
                        # the bearish leg leading to the current lows (the LH
                        # the bearish structure last "spent"). Clearing the
                        # trailing active_high with a sustained break is not
                        # enough on its own: a pivot that does so but doesn't
                        # also clear choch_candidate_high is just an internal
                        # bounce within the still-active bearish leg, reported
                        # as a LIQUIDITY_SWEEP with trend left unchanged.
                        confirmed = confirms_break(
                            timestamp, active_high.price, bullish=True
                        ) and (
                            choch_candidate_high is None or price > choch_candidate_high.price
                        )
                    else:
                        confirmed = True
                    if confirmed:
                        if is_reversal:
                            # The CHoCH's reference is the level that defined
                            # the leg it just broke: choch_candidate_high if
                            # one has been recorded, else fall back to the
                            # more extreme of active_high/pending_high (no
                            # bearish BOS/CHoCH has "spent" a high yet).
                            if choch_candidate_high is not None:
                                reference = choch_candidate_high
                            else:
                                reference = active_high
                                if (
                                    pending_high is not None
                                    and pending_high.price > active_high.price
                                ):
                                    reference = pending_high
                        else:
                            reference = active_high
                        emit(
                            timestamp,
                            StructureEvent.CHANGE_OF_CHARACTER
                            if is_reversal
                            else StructureEvent.BREAK_OF_STRUCTURE,
                            MarketDirection.BULLISH,
                            price,
                            reference.price,
                        )
                        trend = MarketDirection.BULLISH
                        if is_reversal:
                            if active_low is not None:
                                choch_candidate_low = active_low
                            lower_high_since_choch_candidate_low = False
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
                    # Ratchet choch_candidate_high toward this LH: once a
                    # confirmed HIGHER_LOW has occurred in the current
                    # bearish leg, a subsequent LH is itself part of that
                    # leg's structure (not a pre-HL re-bootstrap pullback
                    # top), so it becomes the new level a bullish CHoCH must
                    # clear.
                    if trend is MarketDirection.BEARISH and higher_low_since_choch_candidate_high:
                        choch_candidate_high = pivot
                    lower_high_since_choch_candidate_low = True
                    pending_low = self._extreme(pending_low, active_low, higher=False)
                active_high = pivot
            else:
                if active_low is None:
                    if active_high is not None:
                        pending_low = pivot
                elif price < active_low.price:
                    is_reversal = trend is MarketDirection.BULLISH
                    if is_reversal:
                        # Mirror of the bullish case above: a bearish CHoCH
                        # must also clear choch_candidate_low -- the swing low
                        # that defined the bullish leg leading to the current
                        # highs -- not just the trailing active_low, else it
                        # is an internal bounce (LIQUIDITY_SWEEP, trend
                        # unchanged).
                        confirmed = confirms_break(
                            timestamp, active_low.price, bullish=False
                        ) and (choch_candidate_low is None or price < choch_candidate_low.price)
                    else:
                        confirmed = True
                    if confirmed:
                        if is_reversal:
                            # Mirror of the bullish case: the reference is
                            # choch_candidate_low if recorded, else the more
                            # extreme of active_low/pending_low.
                            if choch_candidate_low is not None:
                                reference = choch_candidate_low
                            else:
                                reference = active_low
                                if (
                                    pending_low is not None
                                    and pending_low.price < active_low.price
                                ):
                                    reference = pending_low
                        else:
                            reference = active_low
                        emit(
                            timestamp,
                            StructureEvent.CHANGE_OF_CHARACTER
                            if is_reversal
                            else StructureEvent.BREAK_OF_STRUCTURE,
                            MarketDirection.BEARISH,
                            price,
                            reference.price,
                        )
                        trend = MarketDirection.BEARISH
                        if is_reversal:
                            if active_high is not None:
                                choch_candidate_high = active_high
                            higher_low_since_choch_candidate_high = False
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
                    # Mirror of the LOWER_HIGH ratchet above: once a
                    # confirmed LOWER_HIGH has occurred in the current
                    # bullish leg, a subsequent HL is part of that leg's
                    # structure, so it becomes the new level a bearish CHoCH
                    # must clear.
                    if trend is MarketDirection.BULLISH and lower_high_since_choch_candidate_low:
                        choch_candidate_low = pivot
                    higher_low_since_choch_candidate_high = True
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
