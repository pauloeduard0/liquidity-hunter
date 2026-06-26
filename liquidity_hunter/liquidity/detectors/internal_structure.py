"""Internal (minor) market structure detector: trailing-reference BOS/HL/LH
with a *continuation-confirmed* CHoCH reference.

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
  State (trend, promotions) advances **only when a candle in the leg
  *closes* beyond the reference** -- a wick-only overshoot does not count.
  On a wick-only break the state does not advance and the broken reference
  is *frozen* (not trailed to this pivot), so a later candle that closes
  beyond that same level activates the BOS then. Once the close confirms the
  break, the BOS event is still only *emitted* when a pullback pivot forms
  in the opposite direction (HL for bullish, LH for bearish) that is above/
  below the pullback reference snapshot (confirming direction). If the next
  opposite-direction pivot is not a valid pullback, the pending BOS is
  silently discarded (state already advanced). A
  continuation dedup gate ensures each pullback stays on the correct side
  of the previous pullback (LH staircase for bearish, HL staircase for
  bullish), preventing re-emission of the same structural break.

  **BOS staircase**: a continuation BOS must also *extend* the leg beyond
  the previous BOS level (`last_bear_bos_low`/`last_bull_bos_high`). While
  the trend is unchanged, a break of a higher trailing low (or lower trailing
  high) formed during a retrace -- which does not beat the previous BOS
  extreme -- is not a structural BOS; it merely trails the active reference.
  So bearish BOS lows keep making lower lows (and bullish BOS highs higher
  highs) until a CHoCH flips the trend. The staircase is *seeded at each
  CHoCH with the CHoCH level itself* (the reference the CHoCH broke): the
  first BOS of the new leg must already break beyond that level, so a BOS
  cannot form on the wrong side of the CHoCH (e.g. a bullish BOS below a
  bullish CHoCH after price fell back through it). Only the very first BOS
  out of the `NEUTRAL` bootstrap (no CHoCH yet) is unconstrained.
- `LOWER_HIGH`/`HIGHER_LOW`: a pivot that does not break the trailing
  reference.
- `LIQUIDITY_SWEEP`: a counter-trend pivot that breaks the trailing
  reference but is not a confirmed reversal (see below). Sweeps are noise
  and do NOT affect the CHoCH reference.

`pending_high`/`pending_low` accumulate the most extreme high/low pivot for
their side, promoted to `active_<side>` when the opposite side breaks (the
leg that just ended is retired in favor of the extreme accumulated during
it). `_extreme` keeps the more extreme of the two.

The CHoCH reference (`CHANGE_OF_CHARACTER`)
==========================================

The CHoCH reference is the **pullback (origin) of the most recent
continuation-confirmed BOS**. A BOS's pullback starts as a *provisional*
candidate; it is promoted to the *validated* CHoCH reference only when a
subsequent move makes a new leg extreme (a genuine continuation), confirming
that the BOS was structural, not noise. If price reverses before that
continuation, the BOS is never confirmed and its pullback never anchors a
CHoCH.

The reference is tracked per side as `validated_choch_high` (the level a
bullish CHoCH must break) and `validated_choch_low` (bearish CHoCH). The
promotion pipeline for `validated_choch_high` (bearish leg, mirrored on the
bullish side):

1. **BOS emission**: when a bearish BOS is confirmed (pending BOS + LH
   pullback), the confirming LH pivot becomes `candidate_choch_high` --
   *provisional*, not yet the CHoCH reference.

2. **Continuation-gated promotion**: the next bearish state-advance (a lower-
   low pivot) promotes `candidate_choch_high` to `validated_choch_high`
   **only if** the new low is below `bear_leg_low` (the running extreme of
   the current bearish leg). This ensures the leg actually extended -- a
   pullback-BOS formed during a retrace that does not make a new leg low
   leaves the candidate provisional and cannot ratchet the reference down
   to a less significant level.

3. **Validated reference is frozen**: once promoted, `validated_choch_high`
   stays at that level until it is consumed by a CHoCH firing (reset to
   `None`) or replaced by the next genuine promotion. Weaker, more recent
   BOS pullbacks that cannot produce a new leg low do not overwrite it.

`bear_leg_low` / `bull_leg_high` track the running extreme of each leg,
seeded at each trend flip (CHoCH) and updated on every in-trend state-
advance.

**CHoCH check**: with `trend` BEARISH, a high pivot that breaks (sustained,
see persistence below) above `validated_choch_high or choch_origin_high or
active_high` is a `CHANGE_OF_CHARACTER`; its `reference_price_level` is the
reference it broke. The `active_high` fallback ensures the detector can flip
trend during the cold-start phase (before any validated/origin reference has
been built), preventing the trend from getting stuck if the bootstrap picks
the wrong initial direction. A high pivot whose break does not hold for
`persistence_candles` is a `LIQUIDITY_SWEEP` (trend unchanged).

**One-shot origin (blind-spot fallback)**: the moment a CHoCH fires, all
validated/candidate state is reset. Rebuilding the *reverse* reference needs
a fresh BOS + continuation, during which a failed reversal would otherwise
leave the trend stuck. `choch_origin_<side>` is the extreme of the leg the
CHoCH just reversed (set only by a *validated*-triggered CHoCH, one-shot).
The CHoCH check uses `validated or origin`, so the origin serves as fallback
until a validated reference is rebuilt. An origin-triggered CHoCH does NOT
set origin on the opposite side (one-shot), breaking ping-pong chains.

Confirmation is *persistence*-based (see `_common.is_sustained_break`): the
breaking candle AND the `persistence_candles` candles immediately following
it must all close beyond the reference. A single candle that pokes through
the reference and reverts (a "false break") is a `LIQUIDITY_SWEEP`; a break
that holds is a `CHANGE_OF_CHARACTER`.

**Failed CHoCH (`CHOCH_FAILED`)**: a CHoCH is only *provisional* until a
same-direction BOS confirms the new trend (that first BOS is guaranteed to be
beyond the CHoCH level by the staircase floor above). While unconfirmed, the
CHoCH carries an *origin* -- the swing it launched from
(`bull_choch_origin`/`bear_choch_origin`, the active low at a bullish CHoCH /
active high at a bearish CHoCH). If price breaks back through that origin
(sustained, same persistence rule) *before* a confirming BOS, the reversal
failed: a `CHOCH_FAILED` event fires (its `direction` is the failed CHoCH's
direction, `reference_price_level` the broken origin) and the trend flips
back. This supersedes the older `choch_origin` blind-spot recovery for the
unconfirmed window, at a tighter level (the impulse base, not the prior leg's
extreme). The origin is retired once the confirming BOS fires (the CHoCH can
no longer fail) or when the trend flips again. A failed-CHoCH flip does NOT
arm the opposite origin (one-shot), so failures cannot ping-pong.

When a CHoCH fires it nulls the reversing trend's BOS staircase
(`last_bear_bos_low`/`last_bull_bos_high`) to seed the new leg, but a failed
CHoCH means that trend never actually ended -- it must resume from its
*genuine* last BOS extreme, not from the (often higher-low / lower-high) CHoCH
origin, or a non-extending BOS could print past the previous same-direction
BOS. So the reversing trend's staircase floor is *stashed*
(`pre_choch_bear_bos_low`/`pre_choch_bull_bos_high`) when the CHoCH fires and
*restored* on failure (taking the more extreme of it and the origin); a
confirming BOS discards the stash. Lifecycle is tied 1:1 to the matching
`*_choch_origin`.

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
        swing_lookback: int = 5,
        persistence_candles: int = 12,
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
        # The price level of the previous confirmed BOS in the current trend
        # (the low established by the last bearish BOS / high by the last
        # bullish BOS). A new continuation BOS must *extend* the staircase --
        # break beyond this level -- so a break of a higher trailing low (lower
        # trailing high) formed during a retrace, which never beats the previous
        # BOS, is not a structural BOS. Reset to None at each trend flip (CHoCH);
        # the first BOS of a leg (None) is unconstrained.
        last_bear_bos_low: float | None = None
        last_bull_bos_high: float | None = None
        # The *origin* of an unconfirmed CHoCH: the swing the CHoCH move launched
        # from (the active low at a bullish CHoCH / active high at a bearish
        # CHoCH). While set, the CHoCH is provisional -- a break back through
        # this level (sustained) before a confirming BOS is a *failed* CHoCH
        # (CHOCH_FAILED): the reversal is invalidated and structure flips back.
        # Cleared once the first same-direction BOS confirms the CHoCH (it can no
        # longer fail), or when the trend flips again. Set only by a *normal*
        # CHoCH, never by a failed-CHoCH flip -- one-shot, so failures cannot
        # ping-pong.
        bull_choch_origin: Pivot | None = None
        bear_choch_origin: Pivot | None = None
        # The pre-CHoCH staircase floor of the trend that resumes if the current
        # provisional CHoCH *fails*. A CHoCH nulls the reversing trend's BOS
        # staircase (`last_bear_bos_low`/`last_bull_bos_high`) to seed the new
        # leg, but a failed CHoCH means that trend never actually ended -- it
        # must resume from its genuine last BOS extreme, not from the (often
        # higher-low / lower-high) CHoCH origin, or a non-extending BOS could
        # print above the previous same-direction BOS. Stashed when the CHoCH
        # fires, restored on failure, discarded once a confirming BOS makes the
        # reversal real. Lifecycle tied 1:1 to the matching `*_choch_origin`.
        pre_choch_bear_bos_low: float | None = None
        pre_choch_bull_bos_high: float | None = None
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
                # A wick-only in-trend break (no candle closed beyond the
                # active reference) stays *pending*: the state must not advance
                # and the broken reference must stay frozen at its level (not
                # trail up to this pivot) so a later candle that *closes* beyond
                # it activates the BOS then.
                wick_only_break = False
                # --- Pending BEARISH BOS confirmation ---
                if pending_bos is not None and pending_bos.direction is MarketDirection.BEARISH:
                    pb = pending_bos.pullback_ref
                    if (
                        pb is not None
                        and price < pb.price
                        and (last_bearish_bos_origin is None or price < last_bearish_bos_origin)
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
                        # The bearish CHoCH is now confirmed by an *emitted* BOS
                        # (a state-advance alone leaves a still-pending BOS that
                        # may never emit, so the CHoCH could still fail): retire
                        # its origin and drop the stashed bullish ceiling here.
                        bear_choch_origin = None
                        pre_choch_bull_bos_high = None
                    pending_bos = None

                # Validated reference takes priority; choch_origin_high is the
                # blind-spot fallback after a prior CHoCH (see declarations).
                via_validated = validated_choch_high is not None
                choch_high_ref = validated_choch_high or choch_origin_high or active_high
                if (
                    trend is MarketDirection.BEARISH
                    and bear_choch_origin is not None
                    and price > bear_choch_origin.price
                    and confirms_break(
                        prev_high_pivot_index + 1,
                        current_index,
                        bear_choch_origin.price,
                        bullish=True,
                    )
                ):
                    # Failed bearish CHoCH: price broke back above the origin the
                    # CHoCH drop launched from, before any confirming BOS. The
                    # reversal is invalidated; structure flips back to bullish.
                    break_candle = candles[
                        find_sustained_break_index(
                            candles,
                            prev_high_pivot_index + 1,
                            current_index,
                            bear_choch_origin.price,
                            bullish=True,
                            persistence_candles=self._persistence_candles,
                        )
                    ]
                    emit(
                        break_candle.timestamp,
                        StructureEvent.CHOCH_FAILED,
                        MarketDirection.BEARISH,
                        price,
                        bear_choch_origin.price,
                        reference_timestamp=bear_choch_origin.timestamp,
                    )
                    trend = MarketDirection.BULLISH
                    active_low = pending_low
                    pending_low = None
                    validated_choch_high = None
                    validated_choch_low = None
                    candidate_choch_high = None
                    candidate_choch_low = None
                    # Bullish trend resumes: cap the staircase at its genuine
                    # last BOS high (preserved across the provisional CHoCH), not
                    # the lower CHoCH origin -- a non-extending BOS must not
                    # print below the previous bullish BOS.
                    bull_leg_high = price
                    last_bull_bos_high = (
                        bear_choch_origin.price
                        if pre_choch_bull_bos_high is None
                        else max(pre_choch_bull_bos_high, bear_choch_origin.price)
                    )
                    last_bear_bos_low = None
                    pre_choch_bear_bos_low = None
                    pre_choch_bull_bos_high = None
                    # One-shot: a failed-CHoCH flip does NOT arm the opposite
                    # origin / blind-spot fallback, so failures cannot ping-pong.
                    choch_origin_high = None
                    choch_origin_low = None
                    bear_choch_origin = None
                    bull_choch_origin = None
                    pending_bos = None
                    last_bullish_bos_price = None
                    last_bullish_bos_origin = None
                    last_bearish_bos_price = None
                    last_bearish_bos_origin = None
                elif (
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
                    # The active low this rally launched from is the bullish
                    # CHoCH's origin: a sustained break back below it (before a
                    # confirming BOS) invalidates the CHoCH (CHOCH_FAILED).
                    bull_choch_origin = active_low
                    bear_choch_origin = None
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
                    # New regime: the bullish BOS staircase is *floored at the
                    # CHoCH level* -- a continuation BOS must break ABOVE the
                    # level the CHoCH broke, never re-break a lower high formed
                    # after price fell back below the CHoCH (the active reference
                    # trails down during that decline). The bearish staircase is
                    # irrelevant in the new bullish leg.
                    # Stash the bearish floor in case this CHoCH later fails and
                    # the bearish trend has to resume from its genuine last BOS.
                    pre_choch_bear_bos_low = last_bear_bos_low
                    pre_choch_bull_bos_high = None
                    last_bull_bos_high = choch_high_ref.price
                    last_bear_bos_low = None
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
                    elif last_bull_bos_high is not None and price <= last_bull_bos_high:
                        # BOS bullish staircase: a continuation BOS must *extend*
                        # the leg beyond the previous BOS high. A break of a lower
                        # trailing high formed during a retrace (price not above
                        # the last BOS high) is not a structural BOS -- it just
                        # trails active_high. The first BOS of the leg
                        # (last_bull_bos_high is None) is unconstrained.
                        pass
                    else:
                        # BOS bullish: the state advances ONLY when a candle in
                        # the leg *closes* beyond the reference. A wick-only
                        # overshoot stays pending (the reference is frozen below)
                        # so the BOS activates later, once a close confirms it.
                        ref_price = active_high.price
                        close_idx = find_close_break_index(
                            candles,
                            prev_high_pivot_index + 1,
                            current_index,
                            ref_price,
                            bullish=True,
                        )
                        if close_idx is None:
                            wick_only_break = True
                        else:
                            # Promote the previous bullish BOS's pullback to the
                            # validated bearish-CHoCH reference *only* if this
                            # break makes a NEW LEG HIGH (above bull_leg_high,
                            # the bullish leg's running extreme) -- a genuine
                            # continuation. A higher-high that does not exceed
                            # the leg extreme (e.g. a pullback-BOS within a
                            # retrace) leaves the candidate provisional: that BOS
                            # never extended the leg, so its pullback must not
                            # ratchet the CHoCH reference down.
                            if (
                                candidate_choch_low is not None
                                and bull_leg_high is not None
                                and price > bull_leg_high
                            ):
                                validated_choch_low = candidate_choch_low
                                choch_origin_low = None
                            if bull_leg_high is None or price > bull_leg_high:
                                bull_leg_high = price
                            # Extend the BOS staircase: the next bullish
                            # continuation must break above this new high.
                            last_bull_bos_high = price
                            pullback_ref_snapshot = active_low
                            trend = MarketDirection.BULLISH
                            active_low = pending_low
                            pending_low = None
                            if (
                                last_bullish_bos_origin is not None
                                and last_bullish_bos_price is not None
                                and pullback_ref_snapshot is not None
                                and pullback_ref_snapshot.price < last_bullish_bos_origin
                                and price < last_bullish_bos_price
                            ):
                                last_bullish_bos_price = None
                                last_bullish_bos_origin = None
                            if not self._confluence_filter or bos_confluence(
                                candles[close_idx], bullish=True
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
                # Freeze the reference on a wick-only break (see above): the
                # pivot must not become the new trailing active_high, so the
                # broken level persists until a candle closes beyond it.
                if not wick_only_break:
                    active_high = pivot
                    prev_high_pivot_index = current_index
            else:
                wick_only_break = False
                # --- Pending BULLISH BOS confirmation ---
                if pending_bos is not None and pending_bos.direction is MarketDirection.BULLISH:
                    pb = pending_bos.pullback_ref
                    if (
                        pb is not None
                        and price > pb.price
                        and (last_bullish_bos_origin is None or price > last_bullish_bos_origin)
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
                        # The bullish CHoCH is now confirmed by an *emitted* BOS
                        # (a state-advance alone leaves a still-pending BOS that
                        # may never emit, so the CHoCH could still fail): retire
                        # its origin and drop the stashed bearish floor here.
                        bull_choch_origin = None
                        pre_choch_bear_bos_low = None
                    pending_bos = None

                # Validated reference takes priority; choch_origin_low is the
                # blind-spot fallback after a prior CHoCH (see declarations).
                via_validated = validated_choch_low is not None
                choch_low_ref = validated_choch_low or choch_origin_low or active_low
                if (
                    trend is MarketDirection.BULLISH
                    and bull_choch_origin is not None
                    and price < bull_choch_origin.price
                    and confirms_break(
                        prev_low_pivot_index + 1,
                        current_index,
                        bull_choch_origin.price,
                        bullish=False,
                    )
                ):
                    # Failed bullish CHoCH: price broke back below the origin the
                    # CHoCH rally launched from, before any confirming BOS. The
                    # reversal is invalidated; structure flips back to bearish.
                    break_candle = candles[
                        find_sustained_break_index(
                            candles,
                            prev_low_pivot_index + 1,
                            current_index,
                            bull_choch_origin.price,
                            bullish=False,
                            persistence_candles=self._persistence_candles,
                        )
                    ]
                    emit(
                        break_candle.timestamp,
                        StructureEvent.CHOCH_FAILED,
                        MarketDirection.BULLISH,
                        price,
                        bull_choch_origin.price,
                        reference_timestamp=bull_choch_origin.timestamp,
                    )
                    trend = MarketDirection.BEARISH
                    active_high = pending_high
                    pending_high = None
                    validated_choch_low = None
                    validated_choch_high = None
                    candidate_choch_high = None
                    candidate_choch_low = None
                    # Bearish trend resumes: floor the staircase at its genuine
                    # last BOS low (preserved across the provisional CHoCH), not
                    # the higher CHoCH origin -- a non-extending BOS must not
                    # print above the previous bearish BOS.
                    bear_leg_low = price
                    last_bear_bos_low = (
                        bull_choch_origin.price
                        if pre_choch_bear_bos_low is None
                        else min(pre_choch_bear_bos_low, bull_choch_origin.price)
                    )
                    last_bull_bos_high = None
                    pre_choch_bear_bos_low = None
                    pre_choch_bull_bos_high = None
                    # One-shot: a failed-CHoCH flip does NOT arm the opposite
                    # origin / blind-spot fallback, so failures cannot ping-pong.
                    choch_origin_low = None
                    choch_origin_high = None
                    bull_choch_origin = None
                    bear_choch_origin = None
                    pending_bos = None
                    last_bullish_bos_price = None
                    last_bullish_bos_origin = None
                    last_bearish_bos_price = None
                    last_bearish_bos_origin = None
                elif (
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
                    # The active high this drop launched from is the bearish
                    # CHoCH's origin (mirror of the bullish case).
                    bear_choch_origin = active_high
                    bull_choch_origin = None
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
                    # New regime: the bearish BOS staircase is *floored at the
                    # CHoCH level* -- a continuation BOS must break BELOW the
                    # level the CHoCH broke, never re-break a higher low formed
                    # after price rose back above the CHoCH (the active reference
                    # trails up during that rise). The bullish staircase is
                    # irrelevant in the new bearish leg.
                    # Stash the bullish ceiling in case this CHoCH later fails
                    # and the bullish trend resumes from its genuine last BOS.
                    pre_choch_bull_bos_high = last_bull_bos_high
                    pre_choch_bear_bos_low = None
                    last_bear_bos_low = choch_low_ref.price
                    last_bull_bos_high = None
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
                    elif last_bear_bos_low is not None and price >= last_bear_bos_low:
                        # BOS bearish staircase: a continuation BOS must *extend*
                        # the leg beyond the previous BOS low. A break of a higher
                        # trailing low formed during a retrace (price not below
                        # the last BOS low) is not a structural BOS -- it just
                        # trails active_low. The first BOS of the leg
                        # (last_bear_bos_low is None) is unconstrained.
                        pass
                    else:
                        # BOS bearish: the state advances ONLY when a candle in
                        # the leg *closes* beyond the reference. A wick-only
                        # overshoot stays pending (the reference is frozen above)
                        # so the BOS activates later, once a close confirms it.
                        ref_price = active_low.price
                        close_idx = find_close_break_index(
                            candles,
                            prev_low_pivot_index + 1,
                            current_index,
                            ref_price,
                            bullish=False,
                        )
                        if close_idx is None:
                            wick_only_break = True
                        else:
                            # Promote the previous bearish BOS's pullback to the
                            # validated bullish-CHoCH reference *only* if this
                            # break makes a NEW LEG LOW (below bear_leg_low, the
                            # bearish leg's running extreme) -- a genuine
                            # continuation. A lower-low that does not break the
                            # leg extreme (e.g. a pullback-BOS within a retrace)
                            # leaves the candidate provisional: that BOS never
                            # extended the leg, so its pullback must not ratchet
                            # the CHoCH reference down.
                            if (
                                candidate_choch_high is not None
                                and bear_leg_low is not None
                                and price < bear_leg_low
                            ):
                                validated_choch_high = candidate_choch_high
                                choch_origin_high = None
                            if bear_leg_low is None or price < bear_leg_low:
                                bear_leg_low = price
                            # Extend the BOS staircase: the next bearish
                            # continuation must break below this new low.
                            last_bear_bos_low = price
                            pullback_ref_snapshot = active_high
                            trend = MarketDirection.BEARISH
                            active_high = pending_high
                            pending_high = None
                            if (
                                last_bearish_bos_origin is not None
                                and last_bearish_bos_price is not None
                                and pullback_ref_snapshot is not None
                                and pullback_ref_snapshot.price > last_bearish_bos_origin
                                and price > last_bearish_bos_price
                            ):
                                last_bearish_bos_price = None
                                last_bearish_bos_origin = None
                            if not self._confluence_filter or bos_confluence(
                                candles[close_idx], bullish=False
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
                # Freeze the reference on a wick-only break (see above): the
                # pivot must not become the new trailing active_low, so the
                # broken level persists until a candle closes beyond it.
                if not wick_only_break:
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
