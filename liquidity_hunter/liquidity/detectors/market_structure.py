"""Swing (major) market structure detector: BOS/CHoCH and LH/HL.

Architecture is identical to `InternalStructureDetector` (see
`internal_structure.py` for the full model description). Differences:

- `swing_lookback=15` default (vs. 2 for internal) surfaces only
  structurally significant pivots rather than every minor swing.
- `persistence_candles=10` default (vs. 5 for internal) requires a
  longer sustained window before a counter-trend break is confirmed as a
  CHoCH.
- Emitted events carry `scope = StructureScope.MAJOR` (the domain
  default), distinguishing them from `StructureScope.INTERNAL` events.
- `choch_origin_<side>` is **always set** on CHoCH (not one-shot like
  `InternalStructureDetector`): with `persistence_candles=10` the risk of
  origin-driven ping-pong is negligible, while the higher lookback makes
  the blind-spot window long enough that a one-shot would re-introduce the
  stuck-trend bug on the third event.
- **Cold-start fallback**: when neither `validated_choch_<side>` nor
  `choch_origin_<side>` exists (bootstrap phase), `active_<side>` is used
  as the CHoCH reference. This ensures the trend can flip even when the
  detector starts in the wrong direction. With `persistence_candles=10`
  the fallback's ping-pong risk is negligible.

The CHoCH reference is `validated_choch_high`/`validated_choch_low`,
promoted from a `candidate_choch_*` (the *strongest* LOWER_HIGH /
HIGHER_LOW pivot of its window -- highest LH / lowest HL since the last
promotion, the pullback that confirmed the BOS, NOT the most recent
pivot -- or a functionally equivalent re-bootstrap pivot) via the same
two-step gate: a BOS in the leg's direction must occur *after* the
candidate was set *and* its pivot price must surpass
`candidate_choch_*_baseline` (the opposite-side trailing reference
snapshotted when the candidate was set), confirming a genuine structural
continuation. Keeping the candidate at the window extreme (rather than
overwriting it with each weaker, more recent LH/HL) stops the CHoCH from
anchoring early on a mid-leg pivot no BOS reached. Ghost-candidate fix: a
SWEEP that violates an unvalidated candidate updates the candidate to the
sweep pivot.

Every emitted `MarketStructure` has `scope = StructureScope.MAJOR`
(the field's default).
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
    find_fvg,
    find_sustained_break_index,
    find_wick_break_index,
    is_sustained_break,
    validate_candles,
)
from liquidity_hunter.liquidity.detectors.base import MarketStructureDetector
from liquidity_hunter.liquidity.detectors.swing_points import SwingHighDetector, SwingLowDetector

# Allowed values for `SwingStructureDetector.reanchor_mode`; mirrors
# `InternalStructureDetector`. See the "online re-anchor" notes there.
_REANCHOR_MODES = frozenset({"off", "displacement", "chain"})


class SwingStructureDetector(MarketStructureDetector):
    """Detects BOS/CHoCH and LH/HL from major (swing) pivots.

    Swing highs/lows are sourced from `SwingHighDetector`/`SwingLowDetector`
    using `swing_lookback`, then walked in chronological order. See the module
    docstring for the full model; in brief:

    - `active_high`/`active_low` are *trailing* references (the most recent
      pivot of each kind); `pending_high`/`pending_low` accumulate each
      side's extreme for promotion when the opposite side breaks.
    - A pivot beyond the trailing reference in the direction of `trend` is a
      `BREAK_OF_STRUCTURE`; one that does not break it is a `LOWER_HIGH`/
      `HIGHER_LOW` label. A BOS's emitted `reference_price_level` is the
      **formed low/high it broke** -- the staircase floor (`last_bear_bos_low`/
      `last_bull_bos_high`, `floor_at_advance`) captured before it ratchets to
      this pivot -- not the trailing `active_<side>` the close-break tested,
      mirroring `InternalStructureDetector`. The first BOS of a leg
      (`floor is None`) falls back to the trailing reference. A composition-level
      pass (`app.dashboard_data._reanchor_bos_close_break`) re-times each BOS to
      the first *close* beyond that formed level and drops wick-only ones.
    - The reversal (`CHANGE_OF_CHARACTER`) reference is
      `validated_choch_high`/`validated_choch_low`, promoted from
      `candidate_choch_high`/`candidate_choch_low` (the strongest LH/HL of its
      window) on the next BOS in that leg's direction whose pivot price also
      surpasses `candidate_choch_*_baseline`.

    `persistence_candles` is the number of candles immediately following a
    counter-trend pivot that must also close beyond the reference for the
    break to be a `CHANGE_OF_CHARACTER` rather than a `LIQUIDITY_SWEEP`.

    `confluence_filter` (default `True`) applies a LuxAlgo-style
    shadow-balance check to the BOS close candle.

    `reanchor_mode` (default `"off"`) enables the online re-anchor (flavor B),
    mirroring `InternalStructureDetector`: on an extended impulsive leg it pulls
    the stale opposite-side references to a local level mid-move WITHOUT flipping
    `trend`, so the reversal CHoCH lands locally. `"displacement"` triggers on an
    in-trend fair-value gap; `"chain"` triggers after `reanchor_chain_threshold`
    (default `3`) BOS advances within the leg. `"off"` is byte-for-byte the
    original behavior.

    `stale_reanchor_candles` (default `None` = off) enables a *staleness*
    re-anchor, independent of `reanchor_mode`: when the trend has run this many
    candles without a confirming BOS or a trend flip (CHoCH/`CHOCH_FAILED`), the
    cycle has stopped making structural sense -- price has ranged or reversed
    while the reversal reference is still pinned at the leg's far origin (so the
    eventual CHoCH only fires once price climbs all the way back there). The
    reversal reference is then pulled down (bearish) / up (bullish) to the most
    recent local swing extreme so a CHoCH can confirm locally and a new cycle
    can begin -- WITHOUT flipping `trend` (the CHoCH itself still has to
    confirm). It only ever tightens, so it tracks the recent extreme as the
    range unfolds; a confirming CHoCH/BOS resets the staleness counter.
    """

    def __init__(
        self,
        swing_lookback: int = 10,
        persistence_candles: int = 10,
        confluence_filter: bool = True,
        reanchor_mode: str = "off",
        reanchor_chain_threshold: int = 3,
        stale_reanchor_candles: int | None = None,
    ) -> None:
        if persistence_candles < 1:
            raise ValueError("persistence_candles must be at least 1")
        if reanchor_mode not in _REANCHOR_MODES:
            raise ValueError(f"reanchor_mode must be one of {sorted(_REANCHOR_MODES)}")
        if reanchor_chain_threshold < 1:
            raise ValueError("reanchor_chain_threshold must be at least 1")
        if stale_reanchor_candles is not None and stale_reanchor_candles < 1:
            raise ValueError("stale_reanchor_candles must be at least 1")
        self._high_detector = SwingHighDetector(lookback=swing_lookback)
        self._low_detector = SwingLowDetector(lookback=swing_lookback)
        self._persistence_candles = persistence_candles
        self._confluence_filter = confluence_filter
        self._reanchor_mode = reanchor_mode
        self._reanchor_chain_threshold = reanchor_chain_threshold
        self._stale_reanchor_candles = stale_reanchor_candles
        # The state-machine trend after the most recent `detect()` call. This is
        # the single source of truth for "the standing trend" -- unlike the last
        # emitted event's `direction`, it is unaffected by descriptive HH/HL/LH/LL
        # labels or LIQUIDITY_SWEEPs (whose `direction` is the pivot/wick side,
        # not the trend) and it resolves CHOCH_FAILED correctly (the trend
        # reverts to the opposite of the failed CHoCH's `direction`). NEUTRAL
        # until `detect()` runs.
        self.final_trend: MarketDirection = MarketDirection.NEUTRAL

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
        last_high_pivot: Pivot | None = None
        last_low_pivot: Pivot | None = None
        validated_choch_high: Pivot | None = None
        validated_choch_low: Pivot | None = None
        candidate_choch_high: Pivot | None = None
        candidate_choch_low: Pivot | None = None
        candidate_choch_high_baseline: Pivot | None = None
        candidate_choch_low_baseline: Pivot | None = None
        choch_origin_high: Pivot | None = None
        choch_origin_low: Pivot | None = None
        # The price level of the previous confirmed BOS in the current trend.
        # A continuation BOS must *extend* the staircase beyond this level, so a
        # break of a higher trailing low (lower trailing high) formed during a
        # retrace is not a structural BOS. Reset to None at each trend flip
        # (CHoCH); the first BOS of a leg (None) is unconstrained.
        last_bear_bos_low: float | None = None
        last_bull_bos_high: float | None = None
        # The extreme of the *previous* BOS in the current leg, used as the
        # emitted `reference_price_level`. Unlike the staircase floor it is not
        # seeded at a CHoCH (`None` for the first BOS of a leg), so that first BOS
        # reports the trailing reference it broke instead of plotting on the
        # CHoCH's own line.
        prev_bear_bos_extreme: float | None = None
        prev_bull_bos_extreme: float | None = None
        # The *origin* of an unconfirmed CHoCH: the swing the CHoCH move launched
        # from (active low at a bullish CHoCH / active high at a bearish CHoCH).
        # While set, the CHoCH is provisional -- a sustained break back through
        # this level before a confirming BOS is a *failed* CHoCH (CHOCH_FAILED),
        # flipping structure back. Cleared once the first same-direction BOS
        # confirms the CHoCH, or when the trend flips again. Set only by a
        # *normal* CHoCH, never by a failed-CHoCH flip (one-shot, no ping-pong).
        bull_choch_origin: Pivot | None = None
        bear_choch_origin: Pivot | None = None
        # The pre-CHoCH staircase floor of the trend that resumes if the current
        # provisional CHoCH *fails*. A CHoCH nulls the reversing trend's staircase
        # to seed the new leg, but a failed CHoCH means that trend never ended --
        # it must resume from its genuine last BOS extreme, not the (often
        # higher-low / lower-high) CHoCH origin, or a non-extending BOS could
        # print past the previous same-direction BOS. Stashed when the CHoCH
        # fires, restored on failure, discarded once a confirming BOS makes the
        # reversal real. Lifecycle tied 1:1 to the matching `*_choch_origin`.
        pre_choch_bear_bos_low: float | None = None
        pre_choch_bull_bos_high: float | None = None
        trend = MarketDirection.NEUTRAL
        prev_high_pivot_index = -1
        prev_low_pivot_index = -1
        # --- Online re-anchor state (flavor B; only when reanchor_mode is not
        # "off"). Mirrors InternalStructureDetector: on an extended impulsive leg
        # the opposite-side references (active_high/validated_choch_high for a
        # bearish impulse, mirror for bullish) go stale/blind, so the eventual
        # reversal CHoCH fires late at a stale level. A trigger pulls them to a
        # local level mid-move WITHOUT flipping `trend`.
        prev_any_pivot_index = -1
        # Count of BOS state-advances within the current leg (trigger "chain").
        # `chain_dir` is the leg's advance direction; minor LH/HL pullbacks do
        # not reset it -- only a trend change does, implicitly: the opposite
        # leg's first advance finds `chain_dir` mismatched and restarts the count
        # (within a single-direction leg a counter-trend break is a sweep/CHoCH,
        # never an opposite advance).
        bos_chain = 0
        chain_dir = MarketDirection.NEUTRAL
        # Index of the candle that last *advanced* the cycle (a BOS) or *flipped*
        # it (a CHoCH / CHOCH_FAILED), set in `emit`. Drives the staleness
        # re-anchor (`stale_reanchor_candles`): a cycle that runs too long past
        # this index without a fresh advance/flip is stale.
        last_advance_index = -1

        def reanchor_opposite(level: float, ts: datetime, *, current_price: float) -> bool:
            """Pull the stale *opposite-side* references to a local `level`
            (flavor B), without touching `trend` or the staircase. Mirrors
            `InternalStructureDetector.reanchor_opposite` (also clears the
            candidate baseline this detector keeps). Either tightens an existing
            reversal reference or establishes one when the impulse nulled them;
            only ever moves to the correct side of `current_price`. Returns
            whether it moved anything.
            """
            nonlocal active_high, active_low
            nonlocal validated_choch_high, validated_choch_low
            nonlocal choch_origin_high, choch_origin_low
            nonlocal candidate_choch_high, candidate_choch_low
            nonlocal candidate_choch_high_baseline, candidate_choch_low_baseline
            new = Pivot(price=level, timestamp=ts)
            if trend is MarketDirection.BEARISH:
                if level <= current_price:
                    return False
                refs = [
                    r.price
                    for r in (active_high, validated_choch_high, choch_origin_high)
                    if r is not None
                ]
                if refs and level >= max(refs):
                    return False
                active_high = new
                validated_choch_high = new
                choch_origin_high = None
                candidate_choch_high = None
                candidate_choch_high_baseline = None
                return True
            if trend is MarketDirection.BULLISH:
                if level >= current_price:
                    return False
                refs = [
                    r.price
                    for r in (active_low, validated_choch_low, choch_origin_low)
                    if r is not None
                ]
                if refs and level <= min(refs):
                    return False
                active_low = new
                validated_choch_low = new
                choch_origin_low = None
                candidate_choch_low = None
                candidate_choch_low_baseline = None
                return True
            return False

        def emit(
            timestamp: datetime,
            event: StructureEvent,
            direction: MarketDirection,
            price_level: float,
            reference_price_level: float,
            reference_timestamp: datetime | None = None,
        ) -> None:
            nonlocal last_advance_index
            if event in (
                StructureEvent.BREAK_OF_STRUCTURE,
                StructureEvent.CHANGE_OF_CHARACTER,
                StructureEvent.CHOCH_FAILED,
            ):
                last_advance_index = index_by_timestamp[timestamp]
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
                )
            )

        for timestamp, kind, price in pivots:
            pivot = Pivot(price=price, timestamp=timestamp)
            current_index = index_by_timestamp[timestamp]

            # --- Trigger "displacement": an in-trend fair-value gap formed in the
            # leg since the previous pivot re-anchors the stale opposite-side
            # references to the gap's reclaim edge (see InternalStructureDetector).
            if self._reanchor_mode == "displacement" and trend is not MarketDirection.NEUTRAL:
                fvg = find_fvg(
                    candles,
                    max(0, prev_any_pivot_index),
                    current_index,
                    bullish=trend is MarketDirection.BULLISH,
                )
                if fvg is not None:
                    fvg_c0_index, fvg_level = fvg
                    reanchor_opposite(
                        fvg_level,
                        candles[fvg_c0_index].timestamp,
                        current_price=candles[current_index].close,
                    )

            # --- Staleness re-anchor: the cycle has run `stale_reanchor_candles`
            # candles past its last BOS/CHoCH without a fresh one. Pull the stale
            # reversal reference to the most recent local swing extreme (the high
            # a bearish leg must reclaim / the low a bullish leg must lose) so a
            # CHoCH can confirm locally instead of waiting for price to climb all
            # the way back to the leg's origin. `reanchor_opposite` only tightens,
            # so this tracks the recent extreme as the range unfolds.
            if (
                self._stale_reanchor_candles is not None
                and trend is not MarketDirection.NEUTRAL
                and last_advance_index >= 0
                and current_index - last_advance_index >= self._stale_reanchor_candles
            ):
                local = last_high_pivot if trend is MarketDirection.BEARISH else last_low_pivot
                if local is not None:
                    reanchor_opposite(
                        local.price,
                        local.timestamp,
                        current_price=candles[current_index].close,
                    )

            if kind == "high":
                # A wick-only in-trend break (no candle closed beyond the
                # active reference) stays *pending*: the state must not advance
                # and the broken reference stays frozen at its level (not trailed
                # up to this pivot) so a later candle that *closes* beyond it
                # activates the BOS then.
                wick_only_break = False
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
                    validated_choch_low = None
                    validated_choch_high = None
                    candidate_choch_low = None
                    candidate_choch_low_baseline = None
                    candidate_choch_high = None
                    candidate_choch_high_baseline = None
                    # Bullish trend resumes: cap the staircase at its genuine
                    # last BOS high (preserved across the provisional CHoCH), not
                    # the lower CHoCH origin -- a non-extending BOS must not print
                    # below the previous bullish BOS.
                    last_bull_bos_high = (
                        bear_choch_origin.price
                        if pre_choch_bull_bos_high is None
                        else max(pre_choch_bull_bos_high, bear_choch_origin.price)
                    )
                    last_bear_bos_low = None
                    # Bullish trend resumed -> previous BOS extreme is the
                    # restored staircase floor (a genuine level, not a seed).
                    prev_bull_bos_extreme = last_bull_bos_high
                    prev_bear_bos_extreme = None
                    pre_choch_bear_bos_low = None
                    pre_choch_bull_bos_high = None
                    # One-shot: a failed-CHoCH flip arms no opposite origin /
                    # blind-spot fallback, so failures cannot ping-pong.
                    choch_origin_high = None
                    choch_origin_low = None
                    bear_choch_origin = None
                    bull_choch_origin = None
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
                    # CHoCH's origin (CHOCH_FAILED if broken before a BOS).
                    bull_choch_origin = active_low
                    bear_choch_origin = None
                    active_low = pending_low
                    pending_low = None
                    validated_choch_low = None
                    choch_origin_high = None
                    choch_origin_low = active_low
                    candidate_choch_low = None
                    candidate_choch_low_baseline = None
                    # New regime: the bullish BOS staircase is *floored at the
                    # CHoCH level* -- a continuation BOS must break ABOVE the
                    # level the CHoCH broke, never re-break a lower high formed
                    # after price fell back below the CHoCH (the active reference
                    # trails down during that decline). The bearish staircase is
                    # irrelevant in the new bullish leg.
                    # Stash the bearish floor in case this CHoCH later fails and
                    # the bearish trend resumes from its genuine last BOS.
                    pre_choch_bear_bos_low = last_bear_bos_low
                    pre_choch_bull_bos_high = None
                    last_bull_bos_high = choch_high_ref.price
                    last_bear_bos_low = None
                    # New leg: no previous BOS yet -> the first BOS reports the
                    # trailing reference, not the seeded CHoCH level.
                    prev_bull_bos_extreme = None
                    prev_bear_bos_extreme = None
                elif active_high is None:
                    if active_low is not None:
                        pending_high = pivot
                    if last_high_pivot is not None and price < last_high_pivot.price:
                        if candidate_choch_high is None or price > candidate_choch_high.price:
                            candidate_choch_high_baseline = active_low
                            candidate_choch_high = pivot
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
                        if candidate_choch_high is not None and price > candidate_choch_high.price:
                            candidate_choch_high = pivot
                            candidate_choch_high_baseline = active_low
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
                        # The formed high the *previous* BOS made (the level this
                        # continuation broke); `None` for the first BOS of the leg
                        # -> the emit falls back to the trailing `ref_price`.
                        floor_at_advance = prev_bull_bos_extreme
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
                            trend = MarketDirection.BULLISH
                            active_low = pending_low
                            pending_low = None
                            # Trigger "chain": count bullish BOS advances in this
                            # leg; at the threshold re-anchor the stale low-side
                            # references up to the most recent in-leg low (the
                            # forming candle's timestamp anchors the CHoCH line).
                            if chain_dir is MarketDirection.BULLISH:
                                bos_chain += 1
                            else:
                                chain_dir = MarketDirection.BULLISH
                                bos_chain = 1
                            if (
                                self._reanchor_mode == "chain"
                                and bos_chain >= self._reanchor_chain_threshold
                            ):
                                seg_start = max(0, prev_any_pivot_index + 1)
                                low_candle = min(
                                    candles[seg_start : current_index + 1], key=lambda c: c.low
                                )
                                reanchor_opposite(
                                    low_candle.low,
                                    low_candle.timestamp,
                                    current_price=candles[current_index].close,
                                )
                                bos_chain = 0
                                chain_dir = MarketDirection.NEUTRAL
                            # Extend the BOS staircase: the next bullish
                            # continuation must break above this new high.
                            last_bull_bos_high = price
                            # This BOS's extreme is the formed level the next
                            # bullish continuation will report as its reference.
                            prev_bull_bos_extreme = price
                            # This BOS confirms the bullish CHoCH: it can no
                            # longer fail (origin retired, stashed floor dropped).
                            bull_choch_origin = None
                            pre_choch_bear_bos_low = None
                            if not self._confluence_filter or bos_confluence(
                                candles[close_idx], bullish=True
                            ):
                                emit(
                                    candles[close_idx].timestamp,
                                    StructureEvent.BREAK_OF_STRUCTURE,
                                    MarketDirection.BULLISH,
                                    price,
                                    floor_at_advance
                                    if floor_at_advance is not None
                                    else ref_price,
                                )
                                # BOS confirmed (close break) -> promote the
                                # CHoCH reference.
                                if candidate_choch_low is not None and (
                                    candidate_choch_low_baseline is None
                                    or price > candidate_choch_low_baseline.price
                                ):
                                    validated_choch_low = candidate_choch_low
                                    choch_origin_low = None
                                    candidate_choch_low = None
                                    candidate_choch_low_baseline = None
                elif price < active_high.price:
                    emit(
                        timestamp,
                        StructureEvent.LOWER_HIGH,
                        MarketDirection.BEARISH,
                        price,
                        active_high.price,
                    )
                    pending_low = self._extreme(pending_low, active_low, higher=False)
                    if candidate_choch_high is None or price > candidate_choch_high.price:
                        candidate_choch_high_baseline = active_low
                        candidate_choch_high = pivot
                # Freeze the reference on a wick-only break (see above): the
                # pivot must not become the new trailing active_high, so the
                # broken level persists until a candle closes beyond it.
                if not wick_only_break:
                    active_high = pivot
                    last_high_pivot = pivot
                    prev_high_pivot_index = current_index

            else:
                wick_only_break = False
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
                    validated_choch_high = None
                    validated_choch_low = None
                    candidate_choch_high = None
                    candidate_choch_high_baseline = None
                    candidate_choch_low = None
                    candidate_choch_low_baseline = None
                    # Bearish trend resumes: floor the staircase at its genuine
                    # last BOS low (preserved across the provisional CHoCH), not
                    # the higher CHoCH origin -- a non-extending BOS must not
                    # print above the previous bearish BOS.
                    last_bear_bos_low = (
                        bull_choch_origin.price
                        if pre_choch_bear_bos_low is None
                        else min(pre_choch_bear_bos_low, bull_choch_origin.price)
                    )
                    last_bull_bos_high = None
                    # Bearish trend resumed -> previous BOS extreme is the
                    # restored staircase floor (a genuine level, not a seed).
                    prev_bear_bos_extreme = last_bear_bos_low
                    prev_bull_bos_extreme = None
                    pre_choch_bear_bos_low = None
                    pre_choch_bull_bos_high = None
                    # One-shot: a failed-CHoCH flip arms no opposite origin /
                    # blind-spot fallback, so failures cannot ping-pong.
                    choch_origin_low = None
                    choch_origin_high = None
                    bull_choch_origin = None
                    bear_choch_origin = None
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
                    # CHoCH's origin (CHOCH_FAILED if broken before a BOS).
                    bear_choch_origin = active_high
                    bull_choch_origin = None
                    active_high = pending_high
                    pending_high = None
                    validated_choch_high = None
                    choch_origin_low = None
                    choch_origin_high = active_high
                    candidate_choch_high = None
                    candidate_choch_high_baseline = None
                    # New regime: the bearish BOS staircase is *floored at the
                    # CHoCH level* -- a continuation BOS must break BELOW the
                    # level the CHoCH broke, never re-break a higher low formed
                    # after price rose back above the CHoCH (the active reference
                    # trails up during that rise). The bullish staircase is
                    # irrelevant in the new bearish leg.
                    # Stash the bullish ceiling in case this CHoCH later fails and
                    # the bullish trend resumes from its genuine last BOS.
                    pre_choch_bull_bos_high = last_bull_bos_high
                    pre_choch_bear_bos_low = None
                    last_bear_bos_low = choch_low_ref.price
                    last_bull_bos_high = None
                    # New leg: no previous BOS yet -> the first BOS reports the
                    # trailing reference, not the seeded CHoCH level.
                    prev_bear_bos_extreme = None
                    prev_bull_bos_extreme = None
                elif active_low is None:
                    if active_high is not None:
                        pending_low = pivot
                    if last_low_pivot is not None and price > last_low_pivot.price:
                        if candidate_choch_low is None or price < candidate_choch_low.price:
                            candidate_choch_low_baseline = active_high
                            candidate_choch_low = pivot
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
                        if candidate_choch_low is not None and price < candidate_choch_low.price:
                            candidate_choch_low = pivot
                            candidate_choch_low_baseline = active_high
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
                        # The formed low the *previous* BOS made (the level this
                        # continuation broke); `None` for the first BOS of the leg
                        # -> the emit falls back to the trailing `ref_price`.
                        floor_at_advance = prev_bear_bos_extreme
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
                            trend = MarketDirection.BEARISH
                            # Extend the BOS staircase: the next bearish
                            # continuation must break below this new low.
                            last_bear_bos_low = price
                            # This BOS's extreme is the formed level the next
                            # bearish continuation will report as its reference.
                            prev_bear_bos_extreme = price
                            # This BOS confirms the bearish CHoCH: it can no
                            # longer fail (origin retired, stashed ceiling dropped).
                            bear_choch_origin = None
                            pre_choch_bull_bos_high = None
                            active_high = pending_high
                            pending_high = None
                            # Trigger "chain": count bearish BOS advances in this
                            # leg; at the threshold re-anchor the stale high-side
                            # references down to the most recent in-leg high (the
                            # forming candle's timestamp anchors the CHoCH line).
                            if chain_dir is MarketDirection.BEARISH:
                                bos_chain += 1
                            else:
                                chain_dir = MarketDirection.BEARISH
                                bos_chain = 1
                            if (
                                self._reanchor_mode == "chain"
                                and bos_chain >= self._reanchor_chain_threshold
                            ):
                                seg_start = max(0, prev_any_pivot_index + 1)
                                high_candle = max(
                                    candles[seg_start : current_index + 1], key=lambda c: c.high
                                )
                                reanchor_opposite(
                                    high_candle.high,
                                    high_candle.timestamp,
                                    current_price=candles[current_index].close,
                                )
                                bos_chain = 0
                                chain_dir = MarketDirection.NEUTRAL
                            if not self._confluence_filter or bos_confluence(
                                candles[close_idx], bullish=False
                            ):
                                emit(
                                    candles[close_idx].timestamp,
                                    StructureEvent.BREAK_OF_STRUCTURE,
                                    MarketDirection.BEARISH,
                                    price,
                                    floor_at_advance
                                    if floor_at_advance is not None
                                    else ref_price,
                                )
                                # BOS confirmed (close break) -> promote the
                                # CHoCH reference.
                                if candidate_choch_high is not None and (
                                    candidate_choch_high_baseline is None
                                    or price < candidate_choch_high_baseline.price
                                ):
                                    validated_choch_high = candidate_choch_high
                                    choch_origin_high = None
                                    candidate_choch_high = None
                                    candidate_choch_high_baseline = None
                elif price > active_low.price:
                    emit(
                        timestamp,
                        StructureEvent.HIGHER_LOW,
                        MarketDirection.BULLISH,
                        price,
                        active_low.price,
                    )
                    pending_high = self._extreme(pending_high, active_high, higher=True)
                    if candidate_choch_low is None or price < candidate_choch_low.price:
                        candidate_choch_low_baseline = active_high
                        candidate_choch_low = pivot
                # Freeze the reference on a wick-only break (see above): the
                # pivot must not become the new trailing active_low, so the
                # broken level persists until a candle closes beyond it.
                if not wick_only_break:
                    active_low = pivot
                    last_low_pivot = pivot
                    prev_low_pivot_index = current_index

            # Track the most recent pivot (any kind) so the next iteration's
            # displacement scan and chain segment-extreme bound the leg to the
            # candles since this pivot. Updated even on a wick-only break.
            prev_any_pivot_index = current_index

        self.final_trend = trend
        return events

    @staticmethod
    def _extreme(
        current: "Pivot | None", candidate: "Pivot | None", *, higher: bool
    ) -> "Pivot | None":
        """The more extreme of `current` and `candidate`, by price.

        Either may be `None`; returns whichever is non-`None`, or `None` if
        both are. `higher=True` keeps the higher-priced pivot (`pending_high`);
        `higher=False` keeps the lower-priced one (`pending_low`).
        """
        if candidate is None:
            return current
        if current is None:
            return candidate
        if higher:
            return candidate if candidate.price > current.price else current
        return candidate if candidate.price < current.price else current
