"""Liquidity-hunt synthesizer: who is the resting liquidity of the current move.

When the current timeframe's structure runs counter to the higher-timeframe
trend (e.g. a bearish CHoCH inside an H4 uptrend), the traders entering with
that counter-move become the resting liquidity the larger trend feeds on:
their stops cluster at equal highs/lows and their leveraged positions project
liquidation bands just beyond price. This engine reads the assembled
:class:`DashboardData` snapshot and describes how far the capture of those
nearby opposing pools has progressed — which pools are mapped, which were
consumed since the counter-trend structure began (zone sweeps, liquidation
hits, OI flush events), and whether open interest is still unwinding against
the hunted side.

Lives in ``app/`` because it is a composition-level synthesizer depending on
outputs from several layers (structure, liquidity, psychology), like
:class:`~liquidity_hunter.app.narrative.NarrativeEngine`. Purely descriptive:
it states who the liquidity is and when it was captured, never what to do.
"""

from __future__ import annotations

from datetime import timedelta
from statistics import fmean
from typing import TYPE_CHECKING

from liquidity_hunter.core.domain.enums import (
    HuntCaptureQuality,
    LiquidityHuntPhase,
    LiquidityHuntTargetKind,
    LiquiditySide,
    LiquidityZoneType,
    MarketControlSide,
    MarketDirection,
    OIParticipation,
    OIRegime,
    RetailPositioning,
    StructureEvent,
    VSAPattern,
)
from liquidity_hunter.core.domain.liquidity_hunt import (
    LiquidityHuntEpisode,
    LiquidityHuntState,
    LiquidityHuntTarget,
)

if TYPE_CHECKING:
    from datetime import datetime

    from liquidity_hunter.app.dashboard_data import DashboardData
    from liquidity_hunter.core.domain.candle import Candle
    from liquidity_hunter.core.domain.liquidation import LiquidationBand
    from liquidity_hunter.core.domain.market_structure import MarketStructure

# Liquidation bands projected from nearby entry clusters can sit almost on top
# of each other (several entries, several tiers); near-equal levels are one
# pool, so they are clustered within this fraction of price (mirroring the
# estimator's own entry clustering) and the strongest band represents each.
_BAND_CLUSTER_PCT = 0.004

# Capture-side grabs closer than this many candles are one sweep cluster, so a
# single liquidity grab closes one hunt (not several near-identical ones).
_GRAB_MERGE_CANDLES = 3

# Weighted capture composition (confirmed with the user 2026-07-20): a grab
# closes a hunt when co-located evidence reaches _CAPTURE_THRESHOLD. Flat
# weights (not confidence-scaled); VSA is already gated by its own analyzer, so
# its mere presence on the grab side is the signal.
#
# Threshold 7 (raised 3 -> 5 -> 6 -> 7; 6 -> 7 on 2026-07-22): a lone strong
# signal (a single sweep / VSA / OI flush = 3) is not a capture — internal
# sweeps are frequent, so lone-signal grabs over-fired; a strong signal plus
# only the pool it swept (3 + zone 2 = 5) was still too many. At 6 a bare
# strong *pair* (realignment + zone, sweep + VSA) already fired, and once the
# realignment flip-back became a grab (_WEIGHT_REALIGNMENT, 2026-07-21) the
# counter-trend hunt started activating too often. Threshold 7 demands a strong
# pair *plus a confirmation* — realignment(4)+zone(2)+delta(1),
# sweep(3)+VSA(3)+delta(1), or a stronger pair like realignment(4)+VSA(3) —
# so only the sharpest, most confluent turning points are marked. The real
# NEAR 30m capture (realignment+zone+delta = 7) still qualifies. Tunable — the
# user is visually backtesting these.
_CAPTURE_THRESHOLD = 7.0
# Aligned trend-continuation grabs use a *lower* threshold than the
# counter-trend hunt: a continuation pullback is a common, lower-bar event
# (not the rare turning point the hunt marks). At threshold 6 (two strong
# signals) ordinary correction floors were skipped and the episode stretched to
# the next deep floor that qualified, reading as a capture "up high" where the
# trend had already run on. Threshold 4 (raised 3 -> 4 on 2026-07-21 — a lone
# strong signal at 3 was too noisy given how frequent internal sweeps are):
# a strong floor signal *plus a confirmation* — a down-sweep with net selling
# delta (3 + 1), a down-sweep on a swept EQL pool (3 + 2), or a
# climax/thrust + sweep — registers the grab; a lone sweep does not. Tunable.
_CONTINUATION_CAPTURE_THRESHOLD = 4.0
_WEIGHT_SWEEP = 3.0
_WEIGHT_VSA = 3.0
# A *strong* floor VSA (a high-confidence down-thrust / selling-climax) is the
# exhaustion candle at the pullback low on its own — the signature the user
# reads directly off the chart. It weighs 4 so it reaches the continuation
# threshold alone, without a co-located sweep/delta: a clean strong thrust
# whose structural sweep printed many candles away (beyond the merge window) or
# never fired was otherwise stuck at 3 and dropped (the ZEC 1h 2026-07-13/-17
# floors, confidence 82/83). A *weak* floor VSA stays at 3 and still needs a
# partner, so this does not reopen the lone-weak-signal noise threshold 4 shut.
# The counter-trend hunt (threshold 6) is unaffected — 4 < 6, still needs a
# pair.
_WEIGHT_VSA_STRONG = 4.0
_VSA_STRONG_CONFIDENCE = 70.0
_WEIGHT_OI_FLUSH = 3.0
_WEIGHT_ZONE = 2.0
# A **pool raid**: a candle whose wick takes out a hunted-side stop pool (an
# equal-highs level for hunted shorts, equal-lows for hunted longs) and whose
# *close comes back inside* it. This is the grab's raw price signature, visible
# without any detector agreeing: the stops above the pool were filled and the
# move did not hold. It exists because the two signatures the engine had before
# are both *derived* and both frequently silent at a real grab — a
# `LIQUIDITY_SWEEP` needs the structure detector to classify a pivot that way
# (it will not, if the leg's state machine is mid-flip), and a VSA
# climax/thrust needs the candle's own anatomy to be extreme enough for the
# analyzer's thresholds. The BTC 15m 2026-07-23 00:00 UTC raid of the 66190-
# 66208 equal-highs pool (the very pool a hunt had already grabbed hours
# earlier, then re-raided) printed neither, and was invisible at zone(2) alone.
# Weighted 4 (above a bare `LIQUIDITY_SWEEP`, which by construction only
# *labels* a pivot): the pool level is known-resting liquidity and the
# rejection close is the grab failing in the same candle.
_WEIGHT_RAID = 4.0
# Short covering / long liquidation OI participation on a capture-side event:
# the hunted side *closing out* is direct evidence its liquidity was the fuel,
# one notch below an outright FLUSH (which is the violent version of it).
_WEIGHT_OI_COVERING = 2.0
# A counter-trend leg's *terminating* realignment break — a confirmed
# capture-direction BOS/CHoCH that flips structure back toward the HTF trend —
# is itself the grab that runs the counter-trend entrants: price broke decisively
# through their stops (the NEAR 30m bearish CHoCH that swept the longs who bought
# the counter-trend bounce, "validated at that moment" the user reads it). It
# weighs more than an unsustained LIQUIDITY_SWEEP (which by definition did not
# hold) but, like a lone sweep, is not a hunt on its own — it needs a co-located
# confluence (a swept stop zone, a VSA climax/thrust, or a delta confirmation) to
# reach the capture threshold, so a bare flip with no other evidence is still
# skipped (a counter leg that reverted without visibly running liquidity is not
# marked, keeping test_history_skips_counter_trend_leg_without_capture green). A
# CHOCH_FAILED reversion is excluded — that excursion is a failed pullback owned
# by the continuation layer (see build_history).
_WEIGHT_REALIGNMENT = 4.0
_WEIGHT_DELTA_MODIFIER = 1.0
# Net taker aggression must exceed this fraction of the candle's volume to count
# as a directional confirmation (|2*taker_buy - volume| / volume).
_DELTA_MODIFIER_MIN_RATIO = 0.1

# VSA capture patterns mapped by the *grab side* (the wick the sweep rejects),
# which is the mirror of VSA's own implied `direction`. A hunted-short capture
# is an up-sweep rejecting the high; a hunted-long capture rejects the low.
_VSA_SHORT_CAPTURE: frozenset[VSAPattern] = frozenset(
    {VSAPattern.UP_THRUST, VSAPattern.BUYING_CLIMAX}
)
_VSA_LONG_CAPTURE: frozenset[VSAPattern] = frozenset(
    {VSAPattern.DOWN_THRUST, VSAPattern.SELLING_CLIMAX}
)

# A grab cluster must carry at least one *floor signature* — a signal that says
# "this is where the move turned", not merely "liquidity sat here". VSA alone
# used to be that gate; it proved too narrow (a real grab does not always print
# a climax/thrust the analyzer's thresholds accept, and the counter-trend hunt
# then went blind for whole legs — the BTC 15m 2026-07-22/23 case, 1 of 3 hunts
# marked). A pool raid with a rejection close and a realignment flip-back are
# the same kind of statement made by price and by structure respectively, so
# any of the three opens the gate; the weighted threshold still decides.
_FLOOR_SIGNATURE_SOURCES = frozenset({"vsa", "raid", "realignment"})


def _opposite(direction: MarketDirection) -> MarketDirection:
    return (
        MarketDirection.BEARISH
        if direction is MarketDirection.BULLISH
        else MarketDirection.BULLISH
    )


class LiquidityHuntEngine:
    """Builds a :class:`LiquidityHuntState` from a completed :class:`DashboardData`.

    ``proximity_pct`` bounds which opposing pools count as "nearby" targets of
    the current corrective move (default 2%, in line with the other analyzers'
    proximity windows). ``proximity_atr`` (default ``None`` = off) makes that
    bound volatility-normalized instead: N x the visible series' mean
    true-range% of price, so "nearby" means the same number of typical candles
    on every asset/timeframe — a fixed 2% maps far too many pools on a calm
    M15 chart and almost none on a volatile daily (the same lesson as the
    detector's `bos_leg_origin_release_gap_atr`). Falls back to
    ``proximity_pct`` when the series is too short to measure a range.
    ``max_targets`` caps the *reported* target list; the captured/total
    counters always reflect the full mapped set.
    """

    def __init__(
        self,
        proximity_pct: float = 0.02,
        proximity_atr: float | None = None,
        max_targets: int = 8,
    ) -> None:
        self.proximity_pct = proximity_pct
        self.proximity_atr = proximity_atr
        self.max_targets = max_targets

    def _effective_proximity(self, candles: list[Candle]) -> float:
        """The proximity bound for this snapshot (ATR-normalized when enabled)."""
        if self.proximity_atr is None or len(candles) < 2:
            return self.proximity_pct
        mean_tr_pct = fmean(
            max(
                curr.high - curr.low,
                abs(curr.high - prev.close),
                abs(curr.low - prev.close),
            )
            / curr.close
            for prev, curr in zip(candles, candles[1:], strict=False)
        )
        return self.proximity_atr * mean_tr_pct

    def build(self, data: DashboardData) -> LiquidityHuntState:
        htf = data.higher_timeframe_direction
        trend, flip_timestamp = self._current_trend(data.internal_structure_events)
        directional = (MarketDirection.BULLISH, MarketDirection.BEARISH)
        counter_trend = (
            htf in directional
            and trend in directional
            and trend is not htf
            and flip_timestamp is not None
        )
        if not counter_trend or trend is None or flip_timestamp is None:
            return LiquidityHuntState(
                symbol=data.symbol,
                timeframe=data.timeframe,
                phase=LiquidityHuntPhase.NONE,
                hunted_side=RetailPositioning.NEUTRAL,
                description=(
                    "Current-timeframe structure is aligned with the higher "
                    "timeframe; no counter-trend pool of entrants in play."
                ),
            )

        # In a bullish HTF trend, a bearish correction's sellers are the fuel:
        # their stops/liquidations rest on the buy side above price. Mirror for
        # a bearish HTF trend. The capture direction is the sweep/flush side
        # that consumes them (an upward wick grabs short liquidity).
        hunted_short = htf is MarketDirection.BULLISH
        hunted_side = RetailPositioning.SHORT if hunted_short else RetailPositioning.LONG
        capture_direction = (
            MarketDirection.BULLISH if hunted_short else MarketDirection.BEARISH
        )

        proximity = self._effective_proximity(data.candles)
        # Confirm captures on *closed* candles only. The provider returns the
        # still-forming candle as the last element, and its wick repaints every
        # poll; a pool grabbed only by that live wick would flip the phase to
        # CAPTURED and then back when the wick retraces (and re-anchor
        # captured_at elsewhere). Anything captured strictly after the last
        # closed candle stays pending — an intact pool — until its candle
        # closes, so the reported phase moves one way per closed candle.
        confirm_cutoff = (
            data.candles[-2].timestamp if len(data.candles) >= 2 else None
        )
        targets = [
            *self._zone_targets(
                data, hunted_short, flip_timestamp, proximity, confirm_cutoff
            ),
            *self._band_targets(
                data, hunted_short, flip_timestamp, proximity, confirm_cutoff
            ),
        ]
        targets.sort(key=lambda t: abs(t.price_level - data.current_price))
        captured = [t for t in targets if t.captured]

        last_flush = self._last_flush(data, capture_direction, flip_timestamp)
        swept_since_flip = self._swept_since(
            data.internal_structure_events, capture_direction, flip_timestamp
        )
        # OI regime is a live, per-poll reading that oscillates between polls; it
        # is retained as *evidence* (the field + description) but no longer gates
        # the CAPTURED phase. Once every mapped pool is captured on closed
        # candles the hunt is concluded — a flickering OI regime must not
        # un-capture a structurally finished hunt (the CAPTURED <-> HUNT churn
        # the user saw live). It still keeps a not-yet-captured leg in
        # HUNT_IN_PROGRESS below.
        oi_unwinding = self._oi_unwinding(data, hunted_short)

        all_captured = bool(targets) and len(captured) == len(targets)
        captured_at: datetime | None = None
        if all_captured:
            phase = LiquidityHuntPhase.CAPTURED
            captured_at = max(
                (t.captured_at for t in captured if t.captured_at is not None),
                default=last_flush,
            )
        elif captured or last_flush is not None or swept_since_flip or oi_unwinding:
            phase = LiquidityHuntPhase.HUNT_IN_PROGRESS
        else:
            phase = LiquidityHuntPhase.COUNTER_TREND

        capture_quality = self._capture_quality(data, capture_direction)

        return LiquidityHuntState(
            symbol=data.symbol,
            timeframe=data.timeframe,
            phase=phase,
            hunted_side=hunted_side,
            correction_direction=trend,
            counter_structure_timestamp=flip_timestamp,
            targets=targets[: self.max_targets],
            targets_captured=len(captured),
            targets_total=len(targets),
            oi_unwinding=oi_unwinding,
            last_flush_timestamp=last_flush,
            captured_at=captured_at,
            capture_quality=capture_quality,
            description=self._describe(
                phase=phase,
                hunted_short=hunted_short,
                htf=htf,
                trend=trend,
                captured_count=len(captured),
                total=len(targets),
                oi_unwinding=oi_unwinding,
                last_flush=last_flush,
                capture_quality=capture_quality,
            ),
        )

    # ------------------------------------------------------------------
    # Historical (concluded) hunts
    # ------------------------------------------------------------------

    def build_history(self, data: DashboardData) -> list[LiquidityHuntEpisode]:
        """Reconstruct every *past* counter-trend hunt, anchored to its grab.

        A hunt is the **near-term** move that clears the counter-trend
        entrants' liquidity so price can flow on: within a counter-trend leg
        (structure opposed the higher-timeframe trend), each capture-side
        liquidity grab — an up-sweep of the shorts' stops for hunted shorts,
        a down-sweep for hunted longs (plus hunted-side equal-level zones
        swept) — **closes one hunt at that grab**. The episode therefore runs
        from the leg flip (or the previous grab) to the grab itself, not all
        the way to the eventual trend reversal, so a single leg can hold
        several short consecutive hunts (the SOL "captured, then a new
        shorts-hunted opens" case). Grabs inside the *current* open leg are
        included as long as they already happened; the still-open tail after
        the last grab is the live :class:`LiquidityHuntState`, not history.

        A counter excursion later reverted by a ``CHOCH_FAILED`` is kept if a
        capture-side grab already completed inside it (the liquidity was taken
        regardless of the later re-interpretation — the NEAR 30m case); only a
        failed excursion with *no* completed grab is dropped here, owned by
        :meth:`build_continuation_history` instead (which scans the opposite,
        pullback-direction sweep, so the two layers never double-count a grab).
        """
        htf = data.higher_timeframe_direction
        directional = (MarketDirection.BULLISH, MarketDirection.BEARISH)
        if htf not in directional:
            return []

        segments = self._trend_segments(data.internal_structure_events)
        now = data.candles[-1].timestamp if data.candles else None
        merge_gap = self._grab_merge_gap(data.candles)

        episodes: list[LiquidityHuntEpisode] = []
        for idx, (direction, start, _event) in enumerate(segments):
            # Leg spans until the next flip, or the live edge for the open leg.
            end = segments[idx + 1][1] if idx + 1 < len(segments) else now
            next_event = segments[idx + 1][2] if idx + 1 < len(segments) else None
            if end is None:
                continue
            if direction not in directional:
                continue
            if direction is htf:
                # An aligned leg is not a hunt — *except* a short-lived one
                # opened by a CHoCH that was then invalidated (CHOCH_FAILED).
                # That excursion is a reversal attempt in the capture direction
                # that ran the hunted side's stops at its extreme and could not
                # hold: the top before the fall. It sits in a blind spot
                # otherwise — build_history skips aligned legs, and
                # _continuation_legs absorbs the failed excursion into the
                # surrounding leg and scans the *opposite* (pullback) direction
                # inside it, so nothing ever looks at the extreme itself.
                if next_event is not StructureEvent.CHOCH_FAILED:
                    continue
                episodes.extend(
                    self._failed_excursion_episodes(
                        data, htf, direction, start, end, merge_gap
                    )
                )
                continue
            hunted_short = htf is MarketDirection.BULLISH
            hunted_side = (
                RetailPositioning.SHORT if hunted_short else RetailPositioning.LONG
            )
            capture_direction = (
                MarketDirection.BULLISH if hunted_short else MarketDirection.BEARISH
            )
            # A genuine reverting BOS/CHoCH that ends this counter-trend leg is
            # the realignment grab (the capture-direction break that ran the
            # entrants); pass it so it can close the leg's final hunt. Not the
            # open live leg (no next flip) and not a CHOCH_FAILED (rule below).
            realignment_ts = (
                end
                if (
                    idx + 1 < len(segments)
                    and next_event
                    in (
                        StructureEvent.BREAK_OF_STRUCTURE,
                        StructureEvent.CHANGE_OF_CHARACTER,
                    )
                )
                else None
            )
            # ...and if that realignment break is *itself* invalidated by the
            # next flip, the grab it closed was the high-water mark of the whole
            # move: the reversal that ran the entrants there did not hold. Same
            # phenomenon _failed_excursion_episodes names from the other side —
            # that one scans *inside* the failed excursion, this one catches the
            # break that opened it, which the raid signature can miss when the
            # breaking candle closes at its high (the rejection lands on the
            # following candles, not on it — BTCUSDT 15m 2026-07-22 16:30).
            realignment_failed = (
                realignment_ts is not None
                and idx + 2 < len(segments)
                and segments[idx + 2][2] is StructureEvent.CHOCH_FAILED
            )
            grabs = self._capture_grabs(
                data,
                hunted_short,
                capture_direction,
                start,
                end,
                merge_gap,
                require_vsa=True,
                realignment_ts=realignment_ts,
            )
            if next_event is StructureEvent.CHOCH_FAILED and not grabs:
                # A failed counter excursion with *no* completed capture-side
                # grab is a deep continuation pullback, not a hunt — the
                # continuation stream owns it (build_continuation_history scans
                # its floor's *pullback*-direction sweep, the opposite side of
                # this method). But a capture-side hunt grab that already
                # completed inside the excursion genuinely took that liquidity
                # at that moment, so it stays in history even though the
                # excursion later reverted — the NEAR 30m case: a bullish CHoCH
                # swept longs on a dip, did not recover, then failed. The two
                # layers stay disjoint by direction (the hunt grab a down-sweep,
                # the continuation grab an up-sweep), so no double coverage.
                continue
            side_word = "shorts" if hunted_short else "longs"
            sub_start = start
            for grab_ts, score, sources in grabs:
                episodes.append(
                    LiquidityHuntEpisode(
                        hunted_side=hunted_side,
                        correction_direction=direction,
                        start_timestamp=sub_start,
                        end_timestamp=grab_ts,
                        capture_score=score,
                        capture_sources=sources,
                        capture_quality=self._episode_quality(
                            data, capture_direction, grab_ts
                        ),
                        failed_reversal=realignment_failed
                        and "realignment" in sources,
                        description=(
                            f"Completed hunt: a {direction.value} move against "
                            f"the {htf.value} higher-timeframe trend swept "
                            f"{side_word} liquidity "
                            f"({', '.join(sources)}; score {score:.0f}), "
                            f"freeing the near-term move."
                        ),
                    )
                )
                sub_start = grab_ts
        return episodes

    def _failed_excursion_episodes(
        self,
        data: DashboardData,
        htf: MarketDirection,
        direction: MarketDirection,
        start: datetime,
        end: datetime,
        merge_gap: timedelta | None,
    ) -> list[LiquidityHuntEpisode]:
        """Grabs inside an aligned excursion whose CHoCH was invalidated.

        The excursion runs in the capture direction (a bullish reversal attempt
        under a bullish HTF, hunting shorts), takes out the pools its own break
        exposed, then fails. Its extreme is the strongest kind of grab the
        engine can name — the entrants who chased the break were run there and
        price never returned — so it is scanned with the ordinary weighted
        machinery rather than being asserted from the ``CHOCH_FAILED`` alone: a
        reversal that simply gave back its move without touching liquidity is
        not a hunt.

        Disjoint from :meth:`build_continuation_history`, which scans the same
        span for *pullback*-direction sweeps: the grab sides are opposite, the
        same argument that keeps the counter-trend and continuation layers from
        double-counting.
        """
        hunted_short = htf is MarketDirection.BULLISH
        capture_direction = (
            MarketDirection.BULLISH if hunted_short else MarketDirection.BEARISH
        )
        if direction is not capture_direction:
            return []
        grabs = self._capture_grabs(
            data,
            hunted_short,
            capture_direction,
            start,
            end,
            merge_gap,
            require_vsa=True,
        )
        side_word = "shorts" if hunted_short else "longs"
        hunted_side = (
            RetailPositioning.SHORT if hunted_short else RetailPositioning.LONG
        )
        episodes: list[LiquidityHuntEpisode] = []
        sub_start = start
        for grab_ts, score, sources in grabs:
            episodes.append(
                LiquidityHuntEpisode(
                    hunted_side=hunted_side,
                    correction_direction=direction,
                    start_timestamp=sub_start,
                    end_timestamp=grab_ts,
                    capture_score=score,
                    capture_sources=sources,
                    capture_quality=self._episode_quality(
                        data, capture_direction, grab_ts
                    ),
                    failed_reversal=True,
                    description=(
                        f"Failed-reversal hunt: a {direction.value} change of "
                        f"character swept {side_word} liquidity at its extreme "
                        f"({', '.join(sources)}; score {score:.0f}), then was "
                        f"invalidated — the high-water mark before the move "
                        f"resumed against it."
                    ),
                )
            )
            sub_start = grab_ts
        return episodes

    def build_continuation_history(
        self, data: DashboardData
    ) -> list[LiquidityHuntEpisode]:
        """Reconstruct every *aligned* trend-continuation liquidity grab.

        This is the sibling of :meth:`build_history`, for the opposite regime:
        a leg **aligned** with the higher-timeframe trend. There is no crowd
        trapped on the wrong side of the HTF here — instead the classic
        continuation pattern plays out inside the leg: price pulls back
        *against* the trend, sweeps the internal liquidity that pullback
        rests on (a down-sweep of the lows in a bull leg, an up-sweep of the
        highs in a bear leg), then resumes with the trend. Each such grab is a
        short episode ``[sub_start, grab]``.

        Deliberately kept a **separate** stream from the counter-trend hunt
        (different regime, different meaning, drawn in its own colour): a
        continuation grab is "where the trend caught its breath", not the
        turning-point read the counter-trend hunt gives. Because the grab is a
        counter-leg sweep, it reuses :meth:`_capture_grabs` with the sweep
        direction *opposite* the leg (the pullback direction) — the very same
        relationship that method already encodes.
        """
        htf = data.higher_timeframe_direction
        directional = (MarketDirection.BULLISH, MarketDirection.BEARISH)
        if htf not in directional:
            return []

        now = data.candles[-1].timestamp if data.candles else None
        if now is None:
            return []
        segments = self._trend_segments(data.internal_structure_events)
        merge_gap = self._grab_merge_gap(data.candles)

        # The grab is the pullback sweep *against* the aligned leg that then
        # resumes with it: a bull leg's grab is a down-sweep of the lows, a bear
        # leg's an up-sweep of the highs. So the grab side is the opposite of
        # the trend, and hunted_side follows the usual rule (a bullish
        # continuation hunts the shorts the pullback lured).
        grab_up = htf is MarketDirection.BEARISH
        capture_direction = (
            MarketDirection.BULLISH if grab_up else MarketDirection.BEARISH
        )
        hunted_side = (
            RetailPositioning.SHORT
            if htf is MarketDirection.BULLISH
            else RetailPositioning.LONG
        )
        trapped = "shorts" if htf is MarketDirection.BULLISH else "longs"

        episodes: list[LiquidityHuntEpisode] = []
        # Aligned legs run *through* failed-CHoCH excursions, so a CHoCH that
        # fizzled mid-trend (leaving VSA on its floor) is still scanned for its
        # continuation grab instead of falling into a vacuum between streams.
        for start, end in self._continuation_legs(segments, htf, now):
            grabs = self._capture_grabs(
                data,
                grab_up,
                capture_direction,
                start,
                end,
                merge_gap,
                threshold=_CONTINUATION_CAPTURE_THRESHOLD,
                require_vsa=True,
                # The pool raid is a *counter-trend hunt* signature: it reads
                # the stops of entrants trapped against the HTF trend. An
                # aligned continuation pullback rests on ordinary internal
                # liquidity, and at this layer's threshold 4 a raid would fire
                # on almost every wick through a stale equal level (measured:
                # ETHUSDT 1h 7 -> 14 episodes). The continuation floor keeps its
                # VSA/sweep signature.
                allow_raid=False,
            )
            sub_start = start
            for grab_ts, score, sources in grabs:
                episodes.append(
                    LiquidityHuntEpisode(
                        hunted_side=hunted_side,
                        correction_direction=htf,
                        start_timestamp=sub_start,
                        end_timestamp=grab_ts,
                        capture_score=score,
                        capture_sources=sources,
                        capture_quality=self._episode_quality(
                            data, capture_direction, grab_ts
                        ),
                        description=(
                            f"Continuation grab: a {htf.value} leg aligned "
                            f"with the {htf.value} higher-timeframe trend pulled "
                            f"back, swept internal liquidity "
                            f"({', '.join(sources)}; score {score:.0f}) trapping "
                            f"{trapped}, then resumed."
                        ),
                    )
                )
                sub_start = grab_ts
        return episodes

    @staticmethod
    def _trend_segments(
        events: list[MarketStructure],
    ) -> list[tuple[MarketDirection, datetime, StructureEvent]]:
        """Segment the event replay into (trend, flip timestamp, flip event) legs.

        Same replay rules as :meth:`_current_trend` (BOS/CHoCH set the trend,
        ``CHOCH_FAILED`` reverts it, provisional/descriptive events ignored),
        but returns one entry per trend leg instead of only the final state.
        The flip event that *started* each leg is carried so a caller can tell a
        counter-trend excursion reverted by a ``CHOCH_FAILED`` (a failed
        excursion, absorbed into the surrounding aligned continuation leg) from
        one reverted by a fresh aligned BOS/CHoCH (a real reversal-and-back).
        """
        segments: list[tuple[MarketDirection, datetime, StructureEvent]] = []
        trend: MarketDirection | None = None
        for event in sorted(events, key=lambda e: e.timestamp):
            if event.provisional:
                continue
            if event.event in (
                StructureEvent.BREAK_OF_STRUCTURE,
                StructureEvent.CHANGE_OF_CHARACTER,
            ):
                new_trend = event.direction
            elif event.event is StructureEvent.CHOCH_FAILED:
                new_trend = (
                    MarketDirection.BEARISH
                    if event.direction is MarketDirection.BULLISH
                    else MarketDirection.BULLISH
                )
            else:
                continue
            if new_trend is not trend:
                segments.append((new_trend, event.timestamp, event.event))
            trend = new_trend
        return segments

    @staticmethod
    def _continuation_legs(
        segments: list[tuple[MarketDirection, datetime, StructureEvent]],
        htf: MarketDirection,
        now: datetime,
    ) -> list[tuple[datetime, datetime]]:
        """Aligned-trend legs, absorbing counter-trend excursions that *failed*.

        A CHoCH against an aligned trend that is later reverted by a
        ``CHOCH_FAILED`` (before it confirmed a BOS) is not a real reversal — it
        is a deep continuation pullback that printed a CHoCH and reclaimed. Its
        floor (a sweep plus a VSA exhaustion candle) is a continuation grab, so
        the aligned leg is treated as running straight through the excursion.
        A counter excursion that instead *confirmed* (reverted by a fresh
        aligned BOS/CHoCH, not a failure) is a real hunt and breaks the aligned
        leg, as does an unresolved counter excursion still open at the live edge.
        """
        legs: list[tuple[datetime, datetime]] = []
        leg_start: datetime | None = None
        n = len(segments)
        for idx, (direction, start, _event) in enumerate(segments):
            if direction is htf:
                if leg_start is None:
                    leg_start = start
                continue
            # Counter-trend excursion: absorbed only if the leg it flips back
            # into is opened by a CHOCH_FAILED (i.e. this CHoCH failed).
            next_event = segments[idx + 1][2] if idx + 1 < n else None
            if next_event is StructureEvent.CHOCH_FAILED:
                continue  # failed excursion -> keep the aligned leg running
            if leg_start is not None:
                legs.append((leg_start, start))
                leg_start = None
        if leg_start is not None:
            legs.append((leg_start, now))
        return legs

    def _capture_grabs(
        self,
        data: DashboardData,
        hunted_short: bool,
        capture_direction: MarketDirection,
        start: datetime,
        end: datetime,
        merge_gap: timedelta | None,
        threshold: float = _CAPTURE_THRESHOLD,
        require_vsa: bool = False,
        realignment_ts: datetime | None = None,
        allow_raid: bool = True,
    ) -> list[tuple[datetime, float, list[str]]]:
        """Weighted capture grabs inside ``[start, end]`` as (ts, score, sources).

        Each capture-side signal — a ``LIQUIDITY_SWEEP`` (weight
        ``_WEIGHT_SWEEP``), a VSA climax/thrust on the grab side
        (``_WEIGHT_VSA``), an OI ``FLUSH`` (``_WEIGHT_OI_FLUSH``), or a
        hunted-side equal-level zone swept (``_WEIGHT_ZONE``) — is collected
        with its timestamp. Signals within ``merge_gap`` are one cluster (a
        single grab moment); a source type counts once per cluster, and a
        volume-delta confirmation in the capture direction adds
        ``_WEIGHT_DELTA_MODIFIER``. A cluster whose score reaches ``threshold``
        (``_CAPTURE_THRESHOLD`` for the counter-trend hunt,
        ``_CONTINUATION_CAPTURE_THRESHOLD`` for the aligned continuation grab)
        is a grab. It is anchored at its first signal (the hunt ends at the
        first touch), except when ``require_vsa`` is set, where it anchors at
        the VSA candle (see below). Below threshold, no grab.

        When ``require_vsa`` is set, a cluster without a grab-side VSA
        climax/thrust is rejected regardless of score: a genuine capture floor
        always prints an exhaustion candle (a down-thrust / selling-climax at
        the low of a bull pullback), so its absence means the cluster is not
        that floor. Both the counter-trend hunt and the aligned continuation
        require this now. The one exemption is a cluster carrying the
        ``realignment`` flip-back grab: a confirmed capture-direction break that
        ran the entrants' stops is self-sufficient as the grab and needs no
        co-located VSA (the NEAR 30m realignment case). When VSA is present it
        anchors the grab at that VSA candle (not the cluster's first signal), so
        the drawn box ends exactly on the exhaustion marker rather than a few
        candles before it.
        """
        signals = self._collect_capture_signals(
            data, hunted_short, capture_direction, start, end, allow_raid=allow_raid
        )
        if realignment_ts is not None:
            # The confirmed break that flipped the leg back to the HTF trend is
            # itself a strong capture-side grab (see _WEIGHT_REALIGNMENT).
            signals.append((realignment_ts, _WEIGHT_REALIGNMENT, "realignment"))
        signals.sort(key=lambda s: s[0])

        clusters: list[list[tuple[datetime, float, str]]] = []
        for signal in signals:
            if (
                clusters
                and merge_gap is not None
                and signal[0] - clusters[-1][-1][0] <= merge_gap
            ):
                clusters[-1].append(signal)
            else:
                clusters.append([signal])

        grabs: list[tuple[datetime, float, list[str]]] = []
        for cluster in clusters:
            # A source type counts once per cluster (two sweeps close together
            # are still one grab, not double weight).
            by_source: dict[str, float] = {}
            for _ts, weight, source in cluster:
                by_source[source] = max(by_source.get(source, 0.0), weight)
            if require_vsa and not (_FLOOR_SIGNATURE_SOURCES & by_source.keys()):
                # No floor signature (exhaustion candle / pool raid with a
                # rejection close / realignment flip-back) -> liquidity merely
                # rested here, this is not where the move turned.
                continue
            first_ts, last_ts = cluster[0][0], cluster[-1][0]
            if self._delta_confirms(data, capture_direction, first_ts, last_ts):
                by_source["delta"] = _WEIGHT_DELTA_MODIFIER
            elif "raid" in by_source and self._delta_confirms(
                data, _opposite(capture_direction), first_ts, last_ts
            ):
                # On a raid the *rejection* is the point: aggression against the
                # grab wick (sellers hitting into an up-raid of the shorts'
                # stops) is who took the other side of the trapped entrants, so
                # it confirms the grab exactly as capture-side aggression does
                # on a sweep. Same slot, so it never double-counts.
                by_source["delta"] = _WEIGHT_DELTA_MODIFIER
            if set(by_source) == {"raid"}:
                # A bare raid with nothing else — no pool recorded as taken, no
                # aggression either way, no structure or OI agreeing — is the
                # same lone-signal noise the thresholds were raised to shut out
                # (wicks poke stale levels constantly). It needs one partner.
                continue
            score = sum(by_source.values())
            if score < threshold:
                continue
            # Anchor: normally the first touch of the cluster. When VSA is the
            # mandatory floor signature (continuation grabs), anchor at the VSA
            # exhaustion candle instead of the structural sweep that usually
            # opens the cluster, so the drawn box ends exactly on the
            # thrust/climax marker the user sees, not a few candles before it.
            anchor = first_ts
            if require_vsa:
                floor_stamps = [
                    ts
                    for ts, _w, source in cluster
                    if source in ("vsa", "raid")
                ]
                if floor_stamps:
                    anchor = min(floor_stamps)
            grabs.append((anchor, score, sorted(by_source)))
        return grabs

    def _collect_capture_signals(
        self,
        data: DashboardData,
        hunted_short: bool,
        capture_direction: MarketDirection,
        start: datetime,
        end: datetime,
        allow_raid: bool = True,
    ) -> list[tuple[datetime, float, str]]:
        """All weighted capture-side signals inside ``[start, end]``."""
        signals: list[tuple[datetime, float, str]] = []
        for event in data.internal_structure_events:
            if (
                event.event is StructureEvent.LIQUIDITY_SWEEP
                and event.direction is capture_direction
                and start <= event.timestamp <= end
            ):
                signals.append((event.timestamp, _WEIGHT_SWEEP, "sweep"))

        zone_type = (
            LiquidityZoneType.EQUAL_HIGHS
            if hunted_short
            else LiquidityZoneType.EQUAL_LOWS
        )
        pool_levels: list[tuple[float, datetime]] = []
        for zone in data.liquidity_zones:
            if zone.zone_type is not zone_type:
                continue
            pool_levels.append(
                (zone.price_high if hunted_short else zone.price_low, zone.formed_at)
            )
            if (
                zone.is_mitigated
                and zone.invalidated_at is not None
                and start <= zone.invalidated_at <= end
            ):
                signals.append((zone.invalidated_at, _WEIGHT_ZONE, "zone"))

        if allow_raid:
            signals.extend(
                self._raid_signals(data, hunted_short, pool_levels, start, end)
            )

        # VSA maps by the *grab side*, not by VSA's implied direction (which is
        # the mirror): a hunted-short capture is an up-sweep rejecting the high
        # (UP_THRUST / BUYING_CLIMAX), a hunted-long capture rejects the low.
        vsa_patterns = _VSA_SHORT_CAPTURE if hunted_short else _VSA_LONG_CAPTURE
        for vsa in data.volume_spread_signals:
            if vsa.pattern in vsa_patterns and start <= vsa.timestamp <= end:
                weight = (
                    _WEIGHT_VSA_STRONG
                    if vsa.confidence >= _VSA_STRONG_CONFIDENCE
                    else _WEIGHT_VSA
                )
                signals.append((vsa.timestamp, weight, "vsa"))

        if data.oi_analysis is not None:
            for qualified in data.oi_analysis.qualified_events:
                if (
                    qualified.direction is not capture_direction
                    or not start <= qualified.event_timestamp <= end
                ):
                    continue
                if qualified.participation is OIParticipation.FLUSH:
                    signals.append(
                        (qualified.event_timestamp, _WEIGHT_OI_FLUSH, "oi_flush")
                    )
                elif qualified.participation is OIParticipation.COVERING:
                    # OI falling on a capture-direction break = the hunted side
                    # closing out into it, the softer sibling of a FLUSH.
                    signals.append(
                        (qualified.event_timestamp, _WEIGHT_OI_COVERING, "oi_flush")
                    )
        return signals

    @staticmethod
    def _raid_signals(
        data: DashboardData,
        hunted_short: bool,
        pool_levels: list[tuple[float, datetime]],
        start: datetime,
        end: datetime,
    ) -> list[tuple[datetime, float, str]]:
        """Candles that wick through a hunted-side pool and close back inside.

        One signal per candle (not per pool level it took out): several stacked
        equal-level pools raided by the same wick are still one grab moment.
        Only pools that had already *formed* by the raiding candle count — a
        level built later was not resting liquidity at that moment.
        """
        if not pool_levels:
            return []
        raids: list[tuple[datetime, float, str]] = []
        for candle in data.candles:
            if not start <= candle.timestamp <= end:
                continue
            if hunted_short:
                hit = any(
                    formed_at <= candle.timestamp
                    and candle.high > level
                    and candle.close < level
                    for level, formed_at in pool_levels
                )
            else:
                hit = any(
                    formed_at <= candle.timestamp
                    and candle.low < level
                    and candle.close > level
                    for level, formed_at in pool_levels
                )
            if hit:
                raids.append((candle.timestamp, _WEIGHT_RAID, "raid"))
        return raids

    @staticmethod
    def _delta_confirms(
        data: DashboardData,
        capture_direction: MarketDirection,
        first_ts: datetime,
        last_ts: datetime,
    ) -> bool:
        """Whether a candle in the cluster shows net taker aggression in the
        capture direction (volume delta beyond ``_DELTA_MODIFIER_MIN_RATIO`` of
        its volume) — confirming *who* won the grab candle."""
        want_positive = capture_direction is MarketDirection.BULLISH
        for candle in data.candles:
            if candle.timestamp < first_ts or candle.timestamp > last_ts:
                continue
            if candle.volume <= 0:
                continue
            delta = 2 * candle.taker_buy_volume - candle.volume
            if abs(delta) / candle.volume < _DELTA_MODIFIER_MIN_RATIO:
                continue
            if (delta > 0) is want_positive:
                return True
        return False

    @staticmethod
    def _grab_merge_gap(candles: list[Candle]) -> timedelta | None:
        """A few candles' worth of time — grabs within it are one sweep cluster."""
        if len(candles) < 2:
            return None
        spacing = candles[-1].timestamp - candles[-2].timestamp
        if spacing <= timedelta(0):
            return None
        return spacing * _GRAB_MERGE_CANDLES

    # ------------------------------------------------------------------
    # Current-timeframe structural trend
    # ------------------------------------------------------------------

    @staticmethod
    def _current_trend(
        events: list[MarketStructure],
    ) -> tuple[MarketDirection | None, datetime | None]:
        """Replay the internal structure stream into (trend, flip timestamp).

        BOS and CHoCH set the trend to their direction; a ``CHOCH_FAILED``
        reverts it (the failed CHoCH's direction never held). Provisional
        live-edge marks and descriptive pivot/sweep labels are ignored. The
        flip timestamp is the event that last *changed* the trend — the start
        of the current corrective leg, used to separate pools captured by this
        move from ones consumed long before it.
        """
        trend: MarketDirection | None = None
        flip_timestamp: datetime | None = None
        for event in sorted(events, key=lambda e: e.timestamp):
            if event.provisional:
                continue
            if event.event in (
                StructureEvent.BREAK_OF_STRUCTURE,
                StructureEvent.CHANGE_OF_CHARACTER,
            ):
                new_trend = event.direction
            elif event.event is StructureEvent.CHOCH_FAILED:
                new_trend = (
                    MarketDirection.BEARISH
                    if event.direction is MarketDirection.BULLISH
                    else MarketDirection.BULLISH
                )
            else:
                continue
            if new_trend is not trend:
                flip_timestamp = event.timestamp
            trend = new_trend
        return trend, flip_timestamp

    # ------------------------------------------------------------------
    # Targets: the nearby opposing pools
    # ------------------------------------------------------------------

    def _zone_targets(
        self,
        data: DashboardData,
        hunted_short: bool,
        flip_timestamp: datetime,
        proximity: float,
        confirm_cutoff: datetime | None,
    ) -> list[LiquidityHuntTarget]:
        """Equal highs/lows within proximity — the classic clustered-stop pools."""
        price = data.current_price
        zone_type = (
            LiquidityZoneType.EQUAL_HIGHS if hunted_short else LiquidityZoneType.EQUAL_LOWS
        )
        label = "EQH" if hunted_short else "EQL"
        targets: list[LiquidityHuntTarget] = []
        for zone in data.liquidity_zones:
            if zone.zone_type is not zone_type:
                continue
            mid = (zone.price_low + zone.price_high) / 2
            if mid <= 0 or abs(mid - price) / price > proximity:
                continue
            if zone.is_mitigated:
                # Only sweeps that happened during this corrective leg count as
                # captures of *its* liquidity; older sweeps are history.
                if zone.invalidated_at is None or zone.invalidated_at < flip_timestamp:
                    continue
                # Confirmed only once the sweeping candle has closed; a sweep on
                # the still-forming candle keeps the pool in play (uncaptured)
                # until it closes, so the phase does not repaint.
                confirmed = (
                    confirm_cutoff is None or zone.invalidated_at <= confirm_cutoff
                )
                targets.append(
                    LiquidityHuntTarget(
                        kind=LiquidityHuntTargetKind.EQUAL_LEVEL,
                        label=label,
                        price_level=mid,
                        captured=confirmed,
                        captured_at=zone.invalidated_at if confirmed else None,
                    )
                )
            else:
                ahead = mid > price if hunted_short else mid < price
                if not ahead:
                    continue
                targets.append(
                    LiquidityHuntTarget(
                        kind=LiquidityHuntTargetKind.EQUAL_LEVEL,
                        label=label,
                        price_level=mid,
                    )
                )
        return targets

    def _band_targets(
        self,
        data: DashboardData,
        hunted_short: bool,
        flip_timestamp: datetime,
        proximity: float,
        confirm_cutoff: datetime | None,
    ) -> list[LiquidityHuntTarget]:
        """Nearby leveraged-liquidation bands on the hunted side.

        Shorts liquidate above price (``BUY_SIDE`` bands), longs below. A live
        band (``end_time`` is None) is an intact pool; one whose ``end_time``
        falls after the counter-trend flip was consumed by this move. Bands
        clustered within ``_BAND_CLUSTER_PCT`` are one pool — represented by
        the strongest member, and intact as long as any member is still live.
        """
        if data.liquidation_map is None:
            return []
        price = data.current_price
        band_side = LiquiditySide.BUY_SIDE if hunted_short else LiquiditySide.SELL_SIDE

        candidates: list[tuple[float, LiquidationBand]] = []
        for band in data.liquidation_map.bands:
            if band.side is not band_side:
                continue
            mid = (band.price_low + band.price_high) / 2
            if abs(mid - price) / price > proximity:
                continue
            if band.end_time is None:
                ahead = mid > price if hunted_short else mid < price
                if not ahead:
                    continue
            elif band.end_time < flip_timestamp:
                continue
            candidates.append((mid, band))
        candidates.sort(key=lambda c: c[0])

        groups: list[list[tuple[float, LiquidationBand]]] = []
        for candidate in candidates:
            if groups and (candidate[0] - groups[-1][-1][0]) / price <= _BAND_CLUSTER_PCT:
                groups[-1].append(candidate)
            else:
                groups.append([candidate])

        targets: list[LiquidityHuntTarget] = []
        for group in groups:
            live = [c for c in group if c[1].end_time is None]
            pool = live if live else group
            mid, band = max(pool, key=lambda c: c[1].intensity)
            # Captured only when no member is still live *and* the consuming
            # candle has closed; a hit on the forming candle stays pending until
            # it closes, so the phase does not flicker on a live wick.
            hit_confirmed = (
                band.end_time is not None
                and (confirm_cutoff is None or band.end_time <= confirm_cutoff)
            )
            is_captured = not live and hit_confirmed
            targets.append(
                LiquidityHuntTarget(
                    kind=LiquidityHuntTargetKind.LIQUIDATION_BAND,
                    label=f"{band.leverage}x",
                    price_level=mid,
                    captured=is_captured,
                    captured_at=band.end_time if is_captured else None,
                )
            )
        return targets

    # ------------------------------------------------------------------
    # Capture evidence
    # ------------------------------------------------------------------

    @staticmethod
    def _last_flush(
        data: DashboardData, capture_direction: MarketDirection, flip_timestamp: datetime
    ) -> datetime | None:
        """Most recent OI-flush event against the hunted side during this leg."""
        if data.oi_analysis is None:
            return None
        stamps = [
            qualified.event_timestamp
            for qualified in data.oi_analysis.qualified_events
            if qualified.participation is OIParticipation.FLUSH
            and qualified.direction is capture_direction
            and qualified.event_timestamp >= flip_timestamp
        ]
        return max(stamps, default=None)

    @staticmethod
    def _swept_since(
        events: list[MarketStructure],
        capture_direction: MarketDirection,
        flip_timestamp: datetime,
    ) -> bool:
        """Whether a capture-side liquidity sweep fired during this leg."""
        return any(
            event.event is StructureEvent.LIQUIDITY_SWEEP
            and event.direction is capture_direction
            and event.timestamp >= flip_timestamp
            for event in events
        )

    @staticmethod
    def _oi_unwinding(data: DashboardData, hunted_short: bool) -> bool:
        """Whether open interest is still burning the hunted side.

        Short covering while shorts are hunted (or long liquidation while
        longs are) means the move is still feeding on forced position closes —
        the hunt is not concluded even if the mapped pools were all touched.
        """
        regime = data.oi_analysis.current_regime if data.oi_analysis else None
        if regime is None:
            return False
        expected = OIRegime.SHORT_COVERING if hunted_short else OIRegime.LONG_LIQUIDATION
        return regime.regime is expected

    @staticmethod
    def _quality_for_controller(
        controller: MarketControlSide | None, capture_direction: MarketDirection
    ) -> HuntCaptureQuality:
        """Map a credited controller to a grab quality against the capture side.

        Fresh money on the capture side (buyers backing an upward short-hunt /
        sellers backing a downward long-hunt) is a genuine break that cleared
        liquidity along the way; anything else (short-covering / balanced / no
        reading) means the grab ran the stops on no new money — an exhausting,
        reversal-prone move. ``None`` controller → ``UNKNOWN``.
        """
        if controller is None:
            return HuntCaptureQuality.UNKNOWN
        backing = (
            MarketControlSide.BUYERS
            if capture_direction is MarketDirection.BULLISH
            else MarketControlSide.SELLERS
        )
        return (
            HuntCaptureQuality.GENUINE_BREAK
            if controller is backing
            else HuntCaptureQuality.EXHAUSTION_GRAB
        )

    @classmethod
    def _capture_quality(
        cls, data: DashboardData, capture_direction: MarketDirection
    ) -> HuntCaptureQuality:
        """Quality of the *live* grab, from the current ``MarketControlState``.

        Degrades to ``UNKNOWN`` on spot / no OI (the ladder's slim snapshot has
        no ``market_control``).
        """
        control = data.market_control
        controller = control.controller if control is not None else None
        return cls._quality_for_controller(controller, capture_direction)

    @classmethod
    def _episode_quality(
        cls,
        data: DashboardData,
        capture_direction: MarketDirection,
        grab_ts: datetime,
    ) -> HuntCaptureQuality:
        """Quality of a *past* grab, from the control series at the grab candle.

        Unlike the live reading, a historical episode is qualified by the
        ``MarketControlPoint`` at (or the last before) its grab timestamp — the
        control reading *at the moment the liquidity was taken*, not the current
        snapshot. ``UNKNOWN`` when the series does not cover the grab.
        """
        control = data.market_control
        if control is None or not control.series:
            return HuntCaptureQuality.UNKNOWN
        controller: MarketControlSide | None = None
        for point in control.series:
            if point.timestamp <= grab_ts:
                controller = point.controller
            else:
                break
        return cls._quality_for_controller(controller, capture_direction)

    # ------------------------------------------------------------------
    # Description
    # ------------------------------------------------------------------

    @staticmethod
    def _describe(
        phase: LiquidityHuntPhase,
        hunted_short: bool,
        htf: MarketDirection,
        trend: MarketDirection,
        captured_count: int,
        total: int,
        oi_unwinding: bool,
        last_flush: datetime | None,
        capture_quality: HuntCaptureQuality,
    ) -> str:
        side_word = "shorts" if hunted_short else "longs"
        pool_side = "buy-side" if hunted_short else "sell-side"
        regime_word = "short covering" if hunted_short else "long liquidation"
        # The grab's fuel, from CVD-aggression x OI: an exhaustion grab (stops
        # run on no new money) is reversal-prone, a genuine break has fresh
        # money behind the move.
        if capture_quality is HuntCaptureQuality.EXHAUSTION_GRAB:
            quality_note = (
                f" Grab on no new money ({side_word} run without fresh flow) — "
                f"reversal-prone."
            )
        elif capture_quality is HuntCaptureQuality.GENUINE_BREAK:
            quality_note = " Grab backed by fresh money — genuine break, not exhaustion."
        else:
            quality_note = ""
        base = (
            f"{trend.value.capitalize()} move against a {htf.value} higher-timeframe "
            f"trend: {side_word} entering it are the resting liquidity."
        )
        if phase is LiquidityHuntPhase.CAPTURED:
            if not total:
                oi_note = ""
            elif oi_unwinding:
                oi_note = f" Open interest still {regime_word} (residual)."
            else:
                oi_note = " Open interest no longer unwinding."
            return (
                f"{base} All {total} mapped {pool_side} pool(s) nearby were "
                f"captured during this leg.{oi_note}{quality_note}"
            )
        parts = [base]
        if total:
            parts.append(f"{captured_count}/{total} nearby {pool_side} pool(s) captured.")
        else:
            parts.append(f"No {pool_side} pools mapped within proximity.")
        if last_flush is not None:
            parts.append(f"Leveraged {side_word} flushed at {last_flush:%Y-%m-%d %H:%M}.")
        if oi_unwinding:
            parts.append(f"OI regime still {regime_word} — {side_word} still being consumed.")
        if quality_note:
            parts.append(quality_note.strip())
        return " ".join(parts)
