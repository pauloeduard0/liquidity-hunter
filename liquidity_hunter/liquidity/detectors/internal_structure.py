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
  direction of* `trend` (or the first break while `trend` is `NEUTRAL`).
  State (trend, promotions) advances immediately on the break, but the BOS
  event is only *emitted* when a pullback pivot forms in the opposite
  direction (HL for bullish, LH for bearish). If the next opposite-direction
  pivot is not a valid pullback, the pending BOS is silently discarded (state
  already advanced). Wick-only breaks (no candle closing beyond the level)
  advance state but never create a pending BOS.
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

- `candidate_choch_high` is the *highest* `LOWER_HIGH`-labeled pivot since the
  last promotion (or a re-bootstrap pivot that is functionally one -- see
  below), not yet promoted. It is the **highest**, not the most recent: a
  weaker, more recent LH must NOT ratchet the candidate down to a level no BOS
  reached (that is the "ativou cedo demais" bug -- the CHoCH would then anchor
  on a micro lower-high in the middle of the leg rather than on the pullback
  that actually confirmed the BOS). Within one promotion window LOWER_HIGH
  labels descend monotonically, so the highest is the first -- the pullback
  top that confirmed the BOS. An LH *alone* is not a CHoCH reference: SMC
  requires `LL1 -> LH1 -> LL2 (confirms LH1) -> break LH1` for a bullish CHoCH,
  so `candidate_choch_high` is only a placeholder until structure confirms it.
  Alongside it, `candidate_choch_high_baseline` snapshots `active_low` as it
  stood at the moment `candidate_choch_high` was set -- the trailing low
  reference in effect immediately before that LH formed. (The matching
  `candidate = pivot` assignment lives *inside* the "is None or more extreme"
  guard, exactly as in the SWEEP branch -- keeping candidate and baseline in
  sync.)
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
  reset to `None`. A **one-shot origin** mechanism prevents the "blind spot"
  after a CHoCH: if the CHoCH was triggered via a *validated* reference,
  `choch_origin_<opposite>` is set to the just-promoted `active_<side>`
  (the extreme of the leg that just reversed), frozen at that value. The
  CHoCH check uses `validated_choch_<side> or choch_origin_<side>`, so the
  origin serves as fallback when validated has not been rebuilt yet. An
  origin-triggered CHoCH does **not** set `choch_origin` on the opposite
  side (one-shot), breaking any ping-pong chain: validated CHoCH -> origin
  CHoCH -> (no further origin, must rebuild validated). When a candidate is
  normally promoted to `validated_choch_<side>`, the corresponding
  `choch_origin_<side>` is cleared (redundant).

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
*lowest* `HIGHER_LOW`-labeled pivot since the last promotion (or re-bootstrap
equivalent) -- the pullback floor that confirmed the BOS, kept rather than
ratcheted up toward a higher, more recent HL -- with
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

from dataclasses import dataclass
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


@dataclass
class _PendingBOS:
    """A BOS break that awaits pullback confirmation."""

    direction: MarketDirection
    breaking_pivot: Pivot
    ref_price: float
    close_break_timestamp: datetime
    pullback_ref: Pivot | None


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
      `candidate_choch_low` (the strongest LH/HL of its window) on the next BOS
      in that leg's direction whose pivot price also surpasses
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
        confluence_filter: bool = False,
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
        # The CHoCH reference levels. validated_choch_high is the swing high a
        # bullish CHoCH must break: the pullback (origin) of the most recent
        # *continuation-confirmed* bearish BOS. Mirror for validated_choch_low.
        validated_choch_high: Pivot | None = None
        validated_choch_low: Pivot | None = None
        # The pullback (origin) of the most recent BOS in each direction, still
        # *provisional*: promoted to validated_choch_<side> only once a
        # continuation (the next BOS in that direction) confirms its BOS. If
        # price reverses before that continuation, the BOS is never confirmed
        # and its pullback never anchors a CHoCH.
        candidate_choch_high: Pivot | None = None
        candidate_choch_low: Pivot | None = None
        # One-shot blind-spot fallback. When a CHoCH fires it consumes the
        # validated reference (reset to None); rebuilding the *reverse*
        # reference needs a fresh BOS + continuation, during which a failed
        # reversal would otherwise leave the trend stuck. choch_origin_<side>
        # is the extreme of the leg the CHoCH just reversed (set only by a
        # *validated*-triggered CHoCH, one-shot, so the chain cannot ping-pong),
        # used as the CHoCH reference until a validated one is rebuilt.
        choch_origin_high: Pivot | None = None
        choch_origin_low: Pivot | None = None
        # Running extreme of the current leg, used to gate candidate -> validated
        # promotion. A bearish BOS's pullback is promoted only when a later low
        # makes a NEW LEG LOW (below bear_leg_low) -- not merely a lower-low
        # below that BOS's own pivot -- so a pullback-BOS formed during a
        # retrace (which never extends the leg) cannot ratchet the CHoCH
        # reference down to a less significant level. Seeded/reset at each trend
        # flip (CHoCH) and at the NEUTRAL bootstrap; mirror for bull_leg_high.
        bear_leg_low: float | None = None
        bull_leg_high: float | None = None
        pending_bos: _PendingBOS | None = None
        last_bullish_bos_price: float | None = None
        last_bullish_bos_origin: float | None = None
        last_bearish_bos_price: float | None = None
        last_bearish_bos_origin: float | None = None
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
            origin_price_level: float | None = None,
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
                    origin_price_level=origin_price_level,
                    scope=StructureScope.INTERNAL,
                )
            )

        for timestamp, kind, price in pivots:
            pivot = Pivot(price=price, timestamp=timestamp)
            current_index = index_by_timestamp[timestamp]

            if kind == "high":
                # --- Pending BEARISH BOS confirmation ---
                if pending_bos is not None and pending_bos.direction is MarketDirection.BEARISH:
                    pb = pending_bos.pullback_ref
                    if (
                        pb is not None
                        and price < pb.price
                        and (last_bearish_bos_price is None or price < last_bearish_bos_price)
                    ):
                        emit(
                            pending_bos.close_break_timestamp,
                            StructureEvent.BREAK_OF_STRUCTURE,
                            MarketDirection.BEARISH,
                            pending_bos.breaking_pivot.price,
                            pending_bos.ref_price,
                            origin_price_level=price,
                        )
                        last_bearish_bos_price = pending_bos.breaking_pivot.price
                        last_bearish_bos_origin = price
                        # This BOS's pullback (the confirming LH) is the
                        # *provisional* CHoCH reference; it is promoted to
                        # validated_choch_high only once a continuation (the
                        # next bearish BOS) confirms this BOS.
                        candidate_choch_high = pivot
                    pending_bos = None

                # Validated reference takes priority; choch_origin_high is the
                # blind-spot fallback after a prior CHoCH (see declarations).
                via_validated = validated_choch_high is not None
                choch_high_ref = validated_choch_high or choch_origin_high
                if (
                    trend is MarketDirection.BEARISH
                    and choch_high_ref is not None
                    and price > choch_high_ref.price
                    and confirms_break(
                        prev_high_pivot_index + 1,
                        current_index,
                        choch_high_ref.price,
                        bullish=True,
                    )
                ):
                    break_candle = candles[
                        find_sustained_break_index(
                            candles,
                            prev_high_pivot_index + 1,
                            current_index,
                            choch_high_ref.price,
                            bullish=True,
                            persistence_candles=self._persistence_candles,
                        )
                    ]
                    emit(
                        break_candle.timestamp,
                        StructureEvent.CHANGE_OF_CHARACTER,
                        MarketDirection.BULLISH,
                        price,
                        choch_high_ref.price,
                        reference_timestamp=choch_high_ref.timestamp,
                    )
                    trend = MarketDirection.BULLISH
                    active_low = pending_low
                    pending_low = None
                    # CHoCH consumes the references; the next confirmed BOS
                    # chain rebuilds them from scratch (provisional -> validated).
                    validated_choch_high = None
                    validated_choch_low = None
                    candidate_choch_high = None
                    candidate_choch_low = None
                    # Arm the opposite-side origin (the bottom of the bearish leg
                    # just reversed) so a failed bullish reversal can still flip
                    # back to bearish before validated_choch_low is rebuilt --
                    # but only for a *validated* trigger (one-shot, no ping-pong).
                    choch_origin_high = None
                    choch_origin_low = active_low if via_validated else None
                    # New bullish leg begins; seed its running high extreme.
                    bull_leg_high = price
                    pending_bos = None
                    last_bullish_bos_price = None
                    last_bullish_bos_origin = None
                    last_bearish_bos_price = None
                    last_bearish_bos_origin = None
                elif active_high is None:
                    if active_low is not None:
                        pending_high = pivot
                elif price > active_high.price:
                    if trend is MarketDirection.BEARISH:
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
                    else:
                        # BOS bullish: state advances now; the BOS is emitted
                        # once a pullback (HL) confirms it. Promote the previous
                        # bullish BOS's pullback to the validated bearish-CHoCH
                        # reference *only* if this break makes a NEW LEG HIGH
                        # (above bull_leg_high, the bullish leg's running
                        # extreme) -- a genuine continuation. A higher-high that
                        # does not exceed the leg extreme (e.g. a pullback-BOS
                        # within a retrace) leaves the candidate provisional:
                        # that BOS never extended the leg, so its pullback must
                        # not ratchet the CHoCH reference down.
                        if (
                            candidate_choch_low is not None
                            and bull_leg_high is not None
                            and price > bull_leg_high
                        ):
                            validated_choch_low = candidate_choch_low
                            choch_origin_low = None
                        if bull_leg_high is None or price > bull_leg_high:
                            bull_leg_high = price
                        ref_price = active_high.price
                        pullback_ref_snapshot = active_low
                        trend = MarketDirection.BULLISH
                        active_low = pending_low
                        pending_low = None
                        close_idx = find_close_break_index(
                            candles,
                            prev_high_pivot_index + 1,
                            current_index,
                            ref_price,
                            bullish=True,
                        )
                        if (
                            last_bullish_bos_origin is not None
                            and last_bullish_bos_price is not None
                            and pullback_ref_snapshot is not None
                            and pullback_ref_snapshot.price < last_bullish_bos_origin
                            and price < last_bullish_bos_price
                        ):
                            last_bullish_bos_price = None
                            last_bullish_bos_origin = None
                        if close_idx is not None and (
                            not self._confluence_filter
                            or bos_confluence(candles[close_idx], bullish=True)
                        ):
                            pending_bos = _PendingBOS(
                                direction=MarketDirection.BULLISH,
                                breaking_pivot=pivot,
                                ref_price=ref_price,
                                close_break_timestamp=candles[close_idx].timestamp,
                                pullback_ref=pullback_ref_snapshot,
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
                active_high = pivot
                prev_high_pivot_index = current_index
            else:
                # --- Pending BULLISH BOS confirmation ---
                if pending_bos is not None and pending_bos.direction is MarketDirection.BULLISH:
                    pb = pending_bos.pullback_ref
                    if (
                        pb is not None
                        and price > pb.price
                        and (last_bullish_bos_price is None or price > last_bullish_bos_price)
                    ):
                        emit(
                            pending_bos.close_break_timestamp,
                            StructureEvent.BREAK_OF_STRUCTURE,
                            MarketDirection.BULLISH,
                            pending_bos.breaking_pivot.price,
                            pending_bos.ref_price,
                            origin_price_level=price,
                        )
                        last_bullish_bos_price = pending_bos.breaking_pivot.price
                        last_bullish_bos_origin = price
                        # Provisional CHoCH reference (see bearish mirror above):
                        # promoted only once a continuation (the next bullish
                        # BOS) confirms this BOS.
                        candidate_choch_low = pivot
                    pending_bos = None

                # Validated reference takes priority; choch_origin_low is the
                # blind-spot fallback after a prior CHoCH (see declarations).
                via_validated = validated_choch_low is not None
                choch_low_ref = validated_choch_low or choch_origin_low
                if (
                    trend is MarketDirection.BULLISH
                    and choch_low_ref is not None
                    and price < choch_low_ref.price
                    and confirms_break(
                        prev_low_pivot_index + 1,
                        current_index,
                        choch_low_ref.price,
                        bullish=False,
                    )
                ):
                    break_candle = candles[
                        find_sustained_break_index(
                            candles,
                            prev_low_pivot_index + 1,
                            current_index,
                            choch_low_ref.price,
                            bullish=False,
                            persistence_candles=self._persistence_candles,
                        )
                    ]
                    emit(
                        break_candle.timestamp,
                        StructureEvent.CHANGE_OF_CHARACTER,
                        MarketDirection.BEARISH,
                        price,
                        choch_low_ref.price,
                        reference_timestamp=choch_low_ref.timestamp,
                    )
                    trend = MarketDirection.BEARISH
                    active_high = pending_high
                    pending_high = None
                    # CHoCH consumes the references; the next confirmed BOS
                    # chain rebuilds them from scratch (provisional -> validated).
                    validated_choch_low = None
                    validated_choch_high = None
                    candidate_choch_high = None
                    candidate_choch_low = None
                    # Arm the opposite-side origin (the top of the bullish leg
                    # just reversed) so a failed bearish reversal can still flip
                    # back to bullish before validated_choch_high is rebuilt --
                    # but only for a *validated* trigger (one-shot, no ping-pong).
                    choch_origin_low = None
                    choch_origin_high = active_high if via_validated else None
                    # New bearish leg begins; seed its running low extreme.
                    bear_leg_low = price
                    pending_bos = None
                    last_bullish_bos_price = None
                    last_bearish_bos_price = None
                elif active_low is None:
                    if active_high is not None:
                        pending_low = pivot
                elif price < active_low.price:
                    if trend is MarketDirection.BULLISH:
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
                    else:
                        # BOS bearish: state advances now; the BOS is emitted
                        # once a pullback (LH) confirms it. Promote the previous
                        # bearish BOS's pullback to the validated bullish-CHoCH
                        # reference *only* if this break makes a NEW LEG LOW
                        # (below bear_leg_low, the bearish leg's running extreme)
                        # -- a genuine continuation. A lower-low that does not
                        # break the leg extreme (e.g. a pullback-BOS within a
                        # retrace) leaves the candidate provisional: that BOS
                        # never extended the leg, so its pullback must not
                        # ratchet the CHoCH reference down.
                        if (
                            candidate_choch_high is not None
                            and bear_leg_low is not None
                            and price < bear_leg_low
                        ):
                            validated_choch_high = candidate_choch_high
                            choch_origin_high = None
                        if bear_leg_low is None or price < bear_leg_low:
                            bear_leg_low = price
                        ref_price = active_low.price
                        pullback_ref_snapshot = active_high
                        trend = MarketDirection.BEARISH
                        active_high = pending_high
                        pending_high = None
                        close_idx = find_close_break_index(
                            candles,
                            prev_low_pivot_index + 1,
                            current_index,
                            ref_price,
                            bullish=False,
                        )
                        if (
                            last_bearish_bos_origin is not None
                            and last_bearish_bos_price is not None
                            and pullback_ref_snapshot is not None
                            and pullback_ref_snapshot.price > last_bearish_bos_origin
                            and price > last_bearish_bos_price
                        ):
                            last_bearish_bos_price = None
                            last_bearish_bos_origin = None
                        if close_idx is not None and (
                            not self._confluence_filter
                            or bos_confluence(candles[close_idx], bullish=False)
                        ):
                            pending_bos = _PendingBOS(
                                direction=MarketDirection.BEARISH,
                                breaking_pivot=pivot,
                                ref_price=ref_price,
                                close_break_timestamp=candles[close_idx].timestamp,
                                pullback_ref=pullback_ref_snapshot,
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
                active_low = pivot
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
