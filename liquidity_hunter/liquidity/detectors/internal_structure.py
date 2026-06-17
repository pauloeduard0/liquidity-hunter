"""Internal (minor) market structure detector: trailing-reference BOS/HL/LH
with a *validated* CHoCH reference.

`SwingStructureDetector` deliberately holds an active reference until the
*opposite* side breaks, so the reference reflects the true extreme of the
prior leg rather than whichever pivot formed last -- the right behavior for
`StructureScope.MAJOR`. For `StructureScope.INTERNAL` that same design can
freeze a side for long stretches, so `InternalStructureDetector` keeps
`active_high`/`active_low` as *trailing* references (normally the most
recently formed swing high/low pivot, updated after every pivot of that
kind). These drive:

- `BREAK_OF_STRUCTURE`: a pivot beyond the trailing reference *in the
  direction of* `trend` (or the first break while `trend` is `NEUTRAL`) --
  price alone, no confirmation needed.
- `LOWER_HIGH`/`HIGHER_LOW`: a pivot that does not break the trailing
  reference.
- `LIQUIDITY_SWEEP`: a counter-trend pivot that breaks the trailing
  reference but is not a confirmed reversal (see below).

`pending_high`/`pending_low` accumulate the most extreme high/low pivot for
their side, promoted to `active_<side>` when the opposite side breaks (the
leg that just ended is retired in favor of the extreme accumulated during
it). `_extreme` keeps the more extreme of the two.

The CHoCH reference (`CHANGE_OF_CHARACTER`)
==========================================

A change of character is a *reversal*, and the level it must break to count
as one is tracked explicitly per side as `validated_choch_high` /
`validated_choch_low` -- distinct from the trailing `active_<side>` and from
`pending_<side>`. Promotion to `validated_choch_<side>` is a two-step process
via an intermediate `candidate_choch_<side>` (mirrored for the low side):

- `candidate_choch_high` is the most recent `LOWER_HIGH`-labeled pivot (or a
  re-bootstrap pivot that is functionally one -- see below), not yet
  promoted. An LH *alone* is not a CHoCH reference: SMC requires `LL1 -> LH1
  -> LL2 (confirms LH1) -> break LH1` for a bullish CHoCH, so
  `candidate_choch_high` is only a placeholder until structure confirms it.
  Alongside it, `candidate_choch_high_baseline` snapshots `active_low` as it
  stood at the moment `candidate_choch_high` was set -- the trailing low
  reference in effect immediately before that LH formed.
- `validated_choch_high` is the swing high that a *bullish* CHoCH must break.
  It is **only updated when a bearish BOS occurs after `candidate_choch_high`
  was set, and that BOS's pivot price is below `candidate_choch_high_baseline`**
  -- i.e. the bearish leg makes a *new* low relative to the low that preceded
  the LH (a genuine `LL2 < LL1` for *this* candidate), not merely any
  continuation of the leg. At that moment, `candidate_choch_high` is
  **promoted**: `validated_choch_high = candidate_choch_high`, and both
  `candidate_choch_high` and `candidate_choch_high_baseline` are cleared to
  `None`. If no candidate has formed since the last promotion/reset,
  `validated_choch_high` is left unchanged.

  This two-part gate -- "a BOS after the candidate formed" *and* "beyond that
  candidate's own baseline" -- balances two failure modes seen in earlier
  iterations:
    - Gating on a new *absolute* low/high of the *entire* leg (tracked as
      `last_ll`/`last_hh`) deadlocks: the *first* impulsive BOS right after a
      CHoCH is often the leg's eventual extreme, after which no later pivot
      can ever exceed it, permanently starving promotion -- `trend` can get
      stuck for hundreds of candles through an obvious reversal.
    - Gating on *any* BOS after the candidate, with no baseline at all,
      over-promotes: `validated_choch_high` keeps ratcheting toward weaker,
      more recent LH pivots even after the leg's true reversal point has
      already been confirmed and should stay frozen.
  The per-candidate baseline -- "beat the low that immediately preceded
  *this* LH", not "beat the whole leg's low" -- is both achievable (fixing
  the deadlock, since each new candidate gets its own, more recent baseline)
  and selective (preserving the freeze once the leg's true reversal has
  already been confirmed and a later, weaker LH cannot beat its own
  baseline).
- While no qualifying bearish BOS confirms it, `candidate_choch_high`,
  `candidate_choch_high_baseline`, and `validated_choch_high` are **frozen**.
- A *bullish CHoCH* fires when, with `trend` BEARISH, a high pivot breaks
  (sustained, see persistence below) **above `validated_choch_high`**; its
  `reference_price_level` is `validated_choch_high` (never the trailing
  `active_high`, never `candidate_choch_high`, never the breaking pivot). A
  high pivot that breaks the trailing `active_high` but not
  `validated_choch_high` -- including while `validated_choch_high` is still
  `None` -- or whose break does not hold, is a `LIQUIDITY_SWEEP` (trend
  unchanged) -- an internal bounce within the still-intact bearish leg.
- The moment a CHoCH fires, the *opposite* side's `validated_choch_<side>`,
  `candidate_choch_<side>`, and `candidate_choch_<side>_baseline` are all
  reset to `None`: the leg that just ended is over, and the new leg's
  reversal reference must be rebuilt from a fresh LH/HL -> LL/HH confirmation
  of its own, not seeded from the leg that just ended.

Re-bootstrap and `candidate_choch_<side>`: a BOS/CHoCH on one side retires the
*opposite* side's `active_<side>` (promoted from `pending_<side>`, or to
`None` if nothing has accumulated there yet). If `active_<side>` was retired
to `None`, the next pivot on that side silently re-bootstraps it with no
HH/HL/LH/LL label (the "accepted cost" described above) -- but if that pivot
is *worse* than the just-retired `active_<side>` (lower for a high pivot,
higher for a low pivot -- judged against `last_high_pivot`/`last_low_pivot`,
which still hold that retired value), it is functionally an LH/HL and still
becomes `candidate_choch_<opposite-side>` (with `candidate_choch_<opposite-
side>_baseline` set from `active_<side>` on the *other* side, same as a
labeled LH/HL would), even though no label is emitted. Without this, a real
LH/HL that happens to land on a re-bootstrap pivot would never become a CHoCH
candidate, permanently freezing `validated_choch_<opposite>` at `None`.

`last_high_pivot`/`last_low_pivot` track the most recent swing high/low pivot
*regardless* of the `active_<side>`/`pending_<side>` promotion machinery.
They no longer drive `validated_choch_<side>` directly -- that role now
belongs to `candidate_choch_<side>` -- but feed the re-bootstrap check above
and remain otherwise unused.

The symmetric machinery on the bullish side: `candidate_choch_low` is the
most recent `HIGHER_LOW`-labeled pivot (or re-bootstrap equivalent), with
`candidate_choch_low_baseline` snapshotting `active_high` at the moment it
was set. `validated_choch_low` is promoted from it when a bullish BOS occurs
after that HL formed *and* its pivot price is above
`candidate_choch_low_baseline` (a genuine `HH2 > HH1` for this candidate); a
bearish CHoCH fires on a sustained break below `validated_choch_low`.

Confirmation is *persistence*-based (see `_common.is_sustained_break`): the
breaking candle AND the `persistence_candles` candles immediately following
it must all close beyond the reference. A single candle that pokes through
`validated_choch_<side>` and reverts (a "false break") fails this and is a
`LIQUIDITY_SWEEP`; a break that holds is a `CHANGE_OF_CHARACTER`. If there
are not yet enough trailing candles to evaluate the window, the break is
treated as unconfirmed. This applies only to `InternalStructureDetector`;
`SwingStructureDetector`'s `volume_delta`-ratio confirmation is unaffected.

Every emitted `MarketStructure` has `scope = StructureScope.INTERNAL`.
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


class InternalStructureDetector(MarketStructureDetector):
    """Detects internal BOS/CHoCH/HL/LH from trailing swing pivot references.

    Swing highs/lows are sourced from `SwingHighDetector`/`SwingLowDetector`
    using `swing_lookback`, then walked in chronological order. See the module
    docstring for the full model; in brief:

    - `active_high`/`active_low` are *trailing* references (the most recent
      pivot of each kind); `pending_high`/`pending_low` accumulate each side's
      extreme for promotion when the opposite side breaks.
    - A pivot beyond the trailing reference in the direction of `trend` is a
      `BREAK_OF_STRUCTURE`; one that does not break it is a `LOWER_HIGH`/
      `HIGHER_LOW` label.
    - The reversal (`CHANGE_OF_CHARACTER`) reference is `validated_choch_high`/
      `validated_choch_low`, promoted from `candidate_choch_high`/
      `candidate_choch_low` (the most recent LH/HL) on the next BOS in that
      leg's direction whose pivot price also surpasses
      `candidate_choch_high_baseline`/`candidate_choch_low_baseline` (a
      snapshot of the opposite side's trailing reference taken when the
      candidate was set) -- a genuine `LL2 < LL1`/`HH2 > HH1` relative to the
      leg containing the candidate, not necessarily a new absolute extreme of
      the whole leg. A counter-trend break of the validated reference is a
      CHoCH if sustained for `persistence_candles`, else a `LIQUIDITY_SWEEP`.

    `persistence_candles` is the number of candles immediately following a
    counter-trend pivot that must also close beyond the reference for the
    break to be a `CHANGE_OF_CHARACTER` rather than a `LIQUIDITY_SWEEP`.

    `confluence_filter` (default `True`) applies LuxAlgo's internal-structure
    confluence filter to in-trend BOS candles: the breaking candle (the first
    one whose close crosses the level) must also have a larger upper shadow
    than lower shadow for a bullish BOS (or larger lower shadow for a bearish
    BOS), confirming directional price expansion beyond the level. When
    `False`, the filter is skipped and only the close requirement is checked.
    """

    def __init__(
        self,
        swing_lookback: int = 2,
        persistence_candles: int = 5,
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
        index_by_timestamp = {candle.timestamp: index for index, candle in enumerate(candles)}

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
        # Trailing references (most recent pivot of each kind); drive BOS
        # detection and HL/LH labels.
        active_high: Pivot | None = None
        active_low: Pivot | None = None
        # Most extreme pivot of each side, promoted to active_<side> when the
        # opposite side breaks.
        pending_high: Pivot | None = None
        pending_low: Pivot | None = None
        # The most recent high/low pivot, period -- never retired to `None` by
        # a promotion. Sources the CHoCH reference (the last high/low before a
        # newly confirmed LL/HH).
        last_high_pivot: Pivot | None = None
        last_low_pivot: Pivot | None = None
        # The CHoCH reference levels. validated_choch_high is the swing high a
        # bullish CHoCH must break; promoted from candidate_choch_high on the
        # next bearish BOS (structural continuation confirming that LH), and
        # frozen otherwise. Mirror for validated_choch_low / candidate_choch_low.
        validated_choch_high: Pivot | None = None
        validated_choch_low: Pivot | None = None
        # The most recent LH/HL pivot (LOWER_HIGH/HIGHER_LOW label) not yet
        # promoted to validated_choch_<side>. A LH/HL alone does not become a
        # CHoCH reference -- only once a BOS in that leg's direction confirms
        # it does the pending candidate get promoted (and cleared).
        candidate_choch_high: Pivot | None = None
        candidate_choch_low: Pivot | None = None
        # The active_<opposite-side> snapshot captured when candidate_choch_<side>
        # was set -- the swing extreme that immediately preceded that LH/HL.
        # Promotion to validated_choch_<side> requires a BOS in that leg's
        # direction to surpass this snapshot (a genuine "HH2 > HH1" / "LL2 <
        # LL1" relative to the leg containing the candidate), not merely any
        # continuation BOS -- otherwise validated_choch_<side> would keep
        # ratcheting toward weaker, more recent LH/HL pivots even after the
        # leg's true reversal point has already been confirmed and frozen.
        candidate_choch_high_baseline: Pivot | None = None
        candidate_choch_low_baseline: Pivot | None = None
        trend = MarketDirection.NEUTRAL
        # Candle index of the previous pivot of each kind, used to bound the
        # break-candle search below to the leg between consecutive pivots of
        # that kind. -1 (no previous pivot) is never read: every branch below
        # that performs a search is only reachable once active_<side>/
        # validated_choch_<side> is set, which happens no earlier than the
        # first pivot of that kind, i.e. once these are no longer -1.
        prev_high_pivot_index = -1
        prev_low_pivot_index = -1

        def emit(
            timestamp: datetime,
            event: StructureEvent,
            direction: MarketDirection,
            price_level: float,
            reference_price_level: float,
            reference_timestamp: datetime | None = None,
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
                    reference_timestamp=reference_timestamp,
                    scope=StructureScope.INTERNAL,
                )
            )

        for timestamp, kind, price in pivots:
            pivot = Pivot(price=price, timestamp=timestamp)
            current_index = index_by_timestamp[timestamp]

            if kind == "high":
                if (
                    trend is MarketDirection.BEARISH
                    and validated_choch_high is not None
                    and price > validated_choch_high.price
                    and confirms_break(
                        prev_high_pivot_index + 1,
                        current_index,
                        validated_choch_high.price,
                        bullish=True,
                    )
                ):
                    # Bullish CHoCH: a sustained break above validated_choch_high
                    # (the last high before the bearish leg's lowest low). The
                    # reference is validated_choch_high -- never the trailing
                    # active_high, never the breaking pivot. Checked before the
                    # active_high bootstrap so a CHoCH still fires even if
                    # active_high was retired to `None`. Timestamped on the
                    # candle that first sustains the break, not the pivot that
                    # eventually confirmed it -- price_level remains the
                    # pivot's price (the extreme of the move).
                    break_candle = candles[
                        find_sustained_break_index(
                            candles,
                            prev_high_pivot_index + 1,
                            current_index,
                            validated_choch_high.price,
                            bullish=True,
                            persistence_candles=self._persistence_candles,
                        )
                    ]
                    emit(
                        break_candle.timestamp,
                        StructureEvent.CHANGE_OF_CHARACTER,
                        MarketDirection.BULLISH,
                        price,
                        validated_choch_high.price,
                        reference_timestamp=validated_choch_high.timestamp,
                    )
                    trend = MarketDirection.BULLISH
                    active_low = pending_low
                    pending_low = None
                    # The bearish CHoCH reference must be rebuilt from a fresh
                    # HL -> BOS confirmation in this new leg, not seeded from
                    # the leg that just ended.
                    validated_choch_low = None
                    candidate_choch_low = None
                    candidate_choch_low_baseline = None
                elif active_high is None:
                    if active_low is not None:
                        pending_high = pivot
                    if last_high_pivot is not None and price < last_high_pivot.price:
                        # A re-bootstrap pivot lower than the just-retired
                        # active_high is functionally a LOWER_HIGH (no label
                        # emitted, per the "silent re-bootstrap" rule), but it
                        # still becomes the candidate reference a future
                        # bullish CHoCH must break, once a bearish BOS beyond
                        # active_low confirms it.
                        candidate_choch_high = pivot
                        candidate_choch_high_baseline = active_low
                elif price > active_high.price:
                    if trend is MarketDirection.BEARISH:
                        # Counter-trend: swept the trailing high but not the
                        # validated CHoCH level (or it didn't hold) -- an
                        # internal bounce within the bearish leg. Sweeps are
                        # wick-based by design; only BOS requires a closing
                        # confirmation.
                        sweep_candle = candles[
                            find_wick_break_index(
                                candles,
                                prev_high_pivot_index + 1,
                                current_index,
                                active_high.price,
                                bullish=True,
                            )
                        ]
                        emit(
                            sweep_candle.timestamp,
                            StructureEvent.LIQUIDITY_SWEEP,
                            MarketDirection.BULLISH,
                            price,
                            active_high.price,
                        )
                        pending_low = self._extreme(pending_low, active_low, higher=False)
                        if candidate_choch_high is not None and price > candidate_choch_high.price:
                            candidate_choch_high = pivot
                            candidate_choch_high_baseline = active_low
                    else:
                        # BOS bullish (first break from NEUTRAL, or
                        # continuation): state always advances (trend,
                        # active_low, validated_choch_low) so that the CHoCH
                        # reference system is unaffected by the BOS emission
                        # filter. The emitted BOS event is separately gated on
                        # a candle in the leg closing beyond active_high, plus
                        # the optional LuxAlgo confluence shadow-balance check.
                        # A wick-only break with no confirming close (or one
                        # that fails confluence) still advances the trend and
                        # promotes validated_choch_low -- it just doesn't
                        # appear as a BOS marker on the chart.
                        ref_price = active_high.price
                        trend = MarketDirection.BULLISH
                        active_low = pending_low
                        pending_low = None
                        if candidate_choch_low is not None and (
                            candidate_choch_low_baseline is None
                            or price > candidate_choch_low_baseline.price
                        ):
                            validated_choch_low = candidate_choch_low
                            candidate_choch_low = None
                            candidate_choch_low_baseline = None
                        close_idx = find_close_break_index(
                            candles,
                            prev_high_pivot_index + 1,
                            current_index,
                            ref_price,
                            bullish=True,
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
                elif price < active_high.price:
                    emit(
                        timestamp,
                        StructureEvent.LOWER_HIGH,
                        MarketDirection.BEARISH,
                        price,
                        active_high.price,
                    )
                    pending_low = self._extreme(pending_low, active_low, higher=False)
                    # This LH is the candidate reference a future bullish CHoCH
                    # must break, once a bearish BOS beyond active_low confirms
                    # it.
                    candidate_choch_high = pivot
                    candidate_choch_high_baseline = active_low
                active_high = pivot
                # Tracked for now (unused by the candidate_choch_<side> logic
                # above) to ease before/after comparison with the prior
                # last_high_pivot-based promotion rule.
                last_high_pivot = pivot  # noqa: F841
                prev_high_pivot_index = current_index
            else:
                if (
                    trend is MarketDirection.BULLISH
                    and validated_choch_low is not None
                    and price < validated_choch_low.price
                    and confirms_break(
                        prev_low_pivot_index + 1,
                        current_index,
                        validated_choch_low.price,
                        bullish=False,
                    )
                ):
                    # Bearish CHoCH (mirror of the bullish case).
                    break_candle = candles[
                        find_sustained_break_index(
                            candles,
                            prev_low_pivot_index + 1,
                            current_index,
                            validated_choch_low.price,
                            bullish=False,
                            persistence_candles=self._persistence_candles,
                        )
                    ]
                    emit(
                        break_candle.timestamp,
                        StructureEvent.CHANGE_OF_CHARACTER,
                        MarketDirection.BEARISH,
                        price,
                        validated_choch_low.price,
                        reference_timestamp=validated_choch_low.timestamp,
                    )
                    trend = MarketDirection.BEARISH
                    active_high = pending_high
                    pending_high = None
                    # The bullish CHoCH reference must be rebuilt from a fresh
                    # LH -> BOS confirmation in this new leg, not seeded from
                    # the leg that just ended.
                    validated_choch_high = None
                    candidate_choch_high = None
                    candidate_choch_high_baseline = None
                elif active_low is None:
                    if active_high is not None:
                        pending_low = pivot
                    if last_low_pivot is not None and price > last_low_pivot.price:
                        # A re-bootstrap pivot higher than the just-retired
                        # active_low is functionally a HIGHER_LOW (no label
                        # emitted, per the "silent re-bootstrap" rule), but it
                        # still becomes the candidate reference a future
                        # bearish CHoCH must break, once a bullish BOS beyond
                        # active_high confirms it.
                        candidate_choch_low = pivot
                        candidate_choch_low_baseline = active_high
                elif price < active_low.price:
                    if trend is MarketDirection.BULLISH:
                        # Counter-trend sweep (mirror of the bullish case).
                        sweep_candle = candles[
                            find_wick_break_index(
                                candles,
                                prev_low_pivot_index + 1,
                                current_index,
                                active_low.price,
                                bullish=False,
                            )
                        ]
                        emit(
                            sweep_candle.timestamp,
                            StructureEvent.LIQUIDITY_SWEEP,
                            MarketDirection.BEARISH,
                            price,
                            active_low.price,
                        )
                        pending_high = self._extreme(pending_high, active_high, higher=True)
                        if candidate_choch_low is not None and price < candidate_choch_low.price:
                            candidate_choch_low = pivot
                            candidate_choch_low_baseline = active_high
                    else:
                        # BOS bearish: state always advances (mirror of the
                        # bullish case); only the emitted event is gated on
                        # close + optional confluence check.
                        ref_price = active_low.price
                        trend = MarketDirection.BEARISH
                        active_high = pending_high
                        pending_high = None
                        if candidate_choch_high is not None and (
                            candidate_choch_high_baseline is None
                            or price < candidate_choch_high_baseline.price
                        ):
                            validated_choch_high = candidate_choch_high
                            candidate_choch_high = None
                            candidate_choch_high_baseline = None
                        close_idx = find_close_break_index(
                            candles,
                            prev_low_pivot_index + 1,
                            current_index,
                            ref_price,
                            bullish=False,
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
                elif price > active_low.price:
                    emit(
                        timestamp,
                        StructureEvent.HIGHER_LOW,
                        MarketDirection.BULLISH,
                        price,
                        active_low.price,
                    )
                    pending_high = self._extreme(pending_high, active_high, higher=True)
                    # This HL is the candidate reference a future bearish CHoCH
                    # must break, once a bullish BOS beyond active_high
                    # confirms it.
                    candidate_choch_low = pivot
                    candidate_choch_low_baseline = active_high
                active_low = pivot
                # Tracked for now (unused by the candidate_choch_<side> logic
                # above) to ease before/after comparison with the prior
                # last_low_pivot-based promotion rule.
                last_low_pivot = pivot  # noqa: F841
                prev_low_pivot_index = current_index

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
