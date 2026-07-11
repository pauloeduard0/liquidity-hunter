"""Composition root for the research dashboard.

Wires together `data`, `liquidity`, `scoring`, and `psychology` into a
single `DashboardData` snapshot for `dashboard` to render.
"""

import logging
from dataclasses import dataclass, replace
from datetime import datetime

from liquidity_hunter.core.domain import (
    Candle,
    FundingRate,
    LeverageLiquidationMap,
    LiquidityHeatmap,
    LiquidityHuntState,
    LiquidityZone,
    LongShortRatio,
    ManipulationCycle,
    MarketDirection,
    MarketNarrative,
    MarketStructure,
    OIAnalysis,
    OpenInterestPoint,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.core.domain.behavior_divergence import BehaviorDivergence
from liquidity_hunter.core.domain.poi_zone import POIZone
from liquidity_hunter.data import (
    BinanceDataProvider,
    BinanceFuturesDataProvider,
    BinanceFuturesOHLCVProvider,
    FallbackOHLCVProvider,
    FuturesDataProvider,
    OHLCVProvider,
)
from liquidity_hunter.data.exceptions import DataProviderError
from liquidity_hunter.indicators import volume_delta_series
from liquidity_hunter.liquidity import (
    EqualHighDetector,
    EqualLowDetector,
    InternalStructureDetector,
    POIDetector,
    SwingHighDetector,
    SwingLowDetector,
    SwingStructureDetector,
    mark_swept_zones,
)
from liquidity_hunter.psychology import (
    BehaviorDivergenceAnalyzer,
    LeverageLiquidationEstimator,
    ManipulationCycleDetector,
    OIRegimeAnalyzer,
    RetailBiasEstimate,
    RetailTrapAnalyzer,
)
from liquidity_hunter.scoring import (
    LiquidityHeatmapEngine,
    LiquidityScoringEngine,
    ScoredLiquidityZone,
)

logger = logging.getLogger(__name__)

DEFAULT_SWING_LOOKBACK = 10

_INTERNAL_STRUCTURE_PARAMS: dict[TimeFrame, tuple[int, int]] = {
    TimeFrame.M5: (5, 12),
    TimeFrame.M15: (5, 12),
    TimeFrame.M30: (5, 12),
    TimeFrame.H1: (5, 12),
    TimeFrame.H4: (5, 12),
    TimeFrame.D1: (5, 12),
    TimeFrame.W1: (5, 12),
}
_DEFAULT_INTERNAL_PARAMS = (5, 12)

# Staleness threshold for both structure detectors' reversal re-anchor
# (`*StructureDetector.stale_reanchor_candles`): how many candles a trend may run
# without a confirming BOS / trend flip before its reversal reference is pulled
# to the most recent local swing extreme so a CHoCH can fire locally and a new
# cycle can begin. Per timeframe (clock time differs), sized well above a normal
# leg so routine consolidations don't trip it -- it targets a cycle that has
# visibly stopped making sense (e.g. a bearish leg still pinning the bullish
# reversal at the leg origin months after price ranged/recovered). Applied to the
# *internal* detector (what the chart renders for all timeframes) as well as the
# major one.
_STALE_REANCHOR_CANDLES: dict[TimeFrame, int] = {
    TimeFrame.M5: 120,
    TimeFrame.M15: 90,
    TimeFrame.M30: 80,
    TimeFrame.H1: 80,
    TimeFrame.H4: 60,
    TimeFrame.D1: 40,
    TimeFrame.W1: 26,
}
_DEFAULT_STALE_REANCHOR_CANDLES = 60

# Impulse-BOS staging threshold for the internal detector
# (`InternalStructureDetector.impulse_bos_displacement_pct`). A clean impulsive
# leg advances the state machine at each lower low / higher high but, with no
# intervening pullback pivot to confirm them, emits no intermediate BOS -- a
# sharp displacement prints a single long event-free stretch instead of a
# staircase. This stages a BOS at each advance whose displacement beyond the
# prior BOS level clears the threshold (deduped against the real BOS, so it only
# fills the impulsive gaps). 1.5% is conservative: it surfaces the big multi-leg
# drops/rallies across timeframes without staging routine continuation steps.
_IMPULSE_BOS_DISPLACEMENT_PCT = 0.015

# Minimum gap (fraction of current price) between a re-anchored reversal
# reference and current price (`InternalStructureDetector.reanchor_min_price_gap_pct`).
# The chain/stale re-anchors can land the reversal reference on a local extreme
# sitting almost on top of price; that reference is hair-trigger, so a trivial
# bounce confirms a mid-range CHoCH that immediately fails (the "CHoCH in chop"
# clutter). 0.3% requires the re-anchored level to be a real distance away, so
# breaking it is a genuine reversal -- measured to remove exactly those premature
# CHoCH while staying neutral on the other timeframes.
_REANCHOR_MIN_PRICE_GAP_PCT = 0.003

# Release gap for the leg-origin CHoCH reference
# (`InternalStructureDetector.bos_leg_origin_release_gap_pct`). A structural
# reference (the fundo/topo a confirmed BOS's leg launched from) is immune to
# re-anchors while it sits within this fraction of current price -- the
# conservative reversal level stays authoritative (e.g. the H4 May CHoCH firing
# at the 78128 leg origin instead of a stale-window local low). Beyond it, the
# leg has run away from its origin and the staleness re-anchor may act again,
# preserving the un-stick behavior on impulsive legs that emit no BOS for long
# stretches (e.g. the H4 February drop). 4% measured best: 5% degrades M15, 6%
# loses the H4 April CHoCH.
_BOS_LEG_ORIGIN_RELEASE_GAP_PCT = 0.04

# Volatility-normalized release gap (`bos_leg_origin_release_gap_atr`, takes
# precedence over the fixed pct above, which stays as fallback for degenerate
# series). A fixed 4% is worth ~8 ATR on BTC 30m (the guard nearly always held,
# pinning whipsaw CHoCH/CHOCH_FAILED pairs across the June drop) but under 1 ATR
# on SOL D1 (a single average candle released it), so what "reachable" means
# depended on the asset/timeframe. N x mean true-range%% of the series keeps the
# release at the same number of typical candles everywhere. Measured 2026-07-03
# (BTC/ETH/SOL x 30m/1h/4h/1d, limit=1200): N in [2, 3] is a stable plateau
# (identical outputs); 8/12 combos unchanged vs the fixed 4%; BTC 30m resolves
# the 06-23..26 drop into one bearish CHoCH + BOS staircase (was 3 whipsaw
# pairs) and drops the 06-27..30 chop flips; N=4 reverts to fixed-pct behavior
# on the fine timeframes.
_BOS_LEG_ORIGIN_RELEASE_GAP_ATR = 3.0

# Shallow-pullback leg-origin promotion on the intraday timeframes
# (`InternalStructureDetector.bos_leg_origin_min_pullback_atr`). When a BOS's
# immediate pullback (the last swing low->high before the break) retraced less
# than N x mean true-range%% of price, the leg origin promoted to the CHoCH
# reference is a shallow secondary high/low well below/above the correction's
# true extreme, so the CHoCH line sits at a minor pivot rather than the visible
# leg top. Promoting to the correction's extreme pivot (`pending_high`/
# `pending_low`) puts the reference at the visible top/bottom -- and, because
# the reference is now higher/lower, a premature poke through the shallow level
# is reclassified as a sweep and the reversal CHoCH fires once price reclaims
# the true top (the AAVE H1 07-02 case: CHoCH ref 86.59 -> 87.82). Measured
# 2026-07-03 (BTC/ETH/SOL/AAVE x 5m..1d): N=1.5 is the minimum that catches the
# AAVE target (immediate depth 1.42 x ATR); every intraday change is a whipsaw
# CHoCH/CHOCH_FAILED pair reclassified to a sweep (AAVE 30m/1h, BTC 30m, SOL
# 1h), M15 near-neutral. M5 is noisy (net-adds marks) and 4h/1d reshape
# already-tuned coarse regions (e.g. BTC 4h May 78128 -> 78713), so both are
# left off -- mirroring the weak-ref barrier's intraday scope. `.get()` -> None
# (off) for the excluded timeframes.
_BOS_LEG_ORIGIN_MIN_PULLBACK_ATR: dict[TimeFrame, float] = {
    TimeFrame.M15: 1.5,
    TimeFrame.M30: 1.5,
    TimeFrame.H1: 1.5,
}

# Max pivot-side wick fraction for a pullback that *confirms* a BOS
# (`InternalStructureDetector.bos_pullback_max_wick_pct`). A small swing lookback
# can pick a single-candle wick (a spike whose body closes far away) as the
# confirming pullback, so a BOS prints off a "pullback" that never retraced. 0.4
# requires the pivot candle's body+opposite side to be >=60% of its range; a
# wick-dominant spike does not confirm and the BOS waits for a real pullback.
_BOS_PULLBACK_MAX_WICK_PCT = 0.4

# New-cycle CHoCH barrier on the intraday timeframes
# (`InternalStructureDetector.choch_weak_ref_persistence_candles`): a CHoCH
# about to fire against a *weak* reference (a synthetic re-anchor level or the
# trailing cold-start fallback -- not a level a leg actually launched from)
# must hold for this many candles instead of the base `persistence_candles`.
# With the intraday base persistence at 2, a brief poke through a weak level
# was enough to start (and dirty) a new cycle that then failed. Structural
# references (leg origin, candidate promotion, blind-spot origin) keep the
# base persistence -- the conservative CHoCH is not delayed. Measured
# 2026-07-03 (BTC/ETH/SOL x 5m/15m/30m/1h, barrier 3/4/5): every removal is a
# whipsaw CHoCH/CHOCH_FAILED pair (BTC 5m double-flip chop, BTC 15m two pairs
# with the bearish continuation staircase restored, BTC 30m 06-12 pair at 4+,
# SOL 30m one pair); costs are two small delays of genuine weak-ref CHoCH
# (ETH 30m 9h, ETH 15m one candle). 4 chosen: catches everything 3 does plus
# the BTC 30m pair, while 5 starts delaying a genuine BTC 30m reversal CHoCH
# by 6h. Coarse timeframes (H4+, base persistence 8+) are left alone.
# (M1 deliberately absent: its detector params fall to the default base
# persistence of 12, which a barrier of 4 would *weaken*, not harden.)
_CHOCH_WEAK_REF_PERSISTENCE: dict[TimeFrame, int] = {
    TimeFrame.M5: 4,
    TimeFrame.M15: 4,
    TimeFrame.M30: 4,
    TimeFrame.H1: 4,
}

# Fast-fizzle CHoCH invalidation marker
# (`InternalStructureDetector.choch_fizzle_reclaim_candles`, applied additively).
# A *standing* provisional CHoCH whose reversal never took hold -- price reclaims
# (sustained close) the very level the CHoCH broke within this many candles of the
# CHoCH -- gets an additive CHOCH_FAILED marker so the chart disregards the stale
# line, instead of it hanging until the far leg origin is reclaimed (the day-old
# SOL M15 bearish CHoCH at 80.72 that price reclaimed in 14 candles yet sat
# unfailed because the closes never cleared the 82.3 origin). A reclaim *after*
# the window is genuine follow-through and left alone, so the number that
# separates a fizzle from a held reversal is a wide plateau: the NEAR M5 genuine
# reversal held its level 133 candles before reclaiming, the SOL M15 fizzle 14 --
# any K in [~20, ~100] splits them. (An emission-time "closer origin" was ruled
# out by an origin-geometry collision, NEAR 2.23 ATR vs SOL 2.20 ATR; a real
# trend-flip CHOCH_FAILED was ruled out by measurement -- it cascades the whole
# downstream sequence, +206/-220 across the matrix -- so the marker is additive,
# never touching the state machine.)
_CHOCH_FIZZLE_RECLAIM_CANDLES: int | None = 30

# Post-failure fallback suppression
# (`InternalStructureDetector.choch_failed_fallback_suppress_candles`). A
# failed-CHoCH flip arms no blind-spot origin (one-shot, anti-ping-pong), so
# the cold-start `active_<side>` fallback -- suppressed while the origin was
# armed -- becomes live again the moment a failure confirms, and a brief bounce
# can flip the trend right back off a hair-trigger trailing level (the BTC H1
# 2026-06-25 case: a fallback bullish CHoCH at 61256 one day after the previous
# bullish CHoCH failed, mid-crash, which turned the final flush to 58030 into a
# sweep instead of a bearish BOS). Keep the fallback suppressed for this many
# candles after a same-direction failure; structural/validated references are
# untouched, so a genuine reversal (which promotes a leg origin via BOS) still
# fires. The motivating whipsaw fired 15 candles after the failure.
_CHOCH_FAILED_FALLBACK_SUPPRESS_CANDLES: int | None = 20

# Retro-staging of the continuation BOS a failed CHoCH ate
# (`InternalStructureDetector.stage_choch_failed_window_bos`). While a CHoCH
# awaits its confirming BOS the trend is flipped, so new extremes in the
# *resumed* direction print as sweeps; when the CHoCH then *fails*, that trend
# never ended and those staircase breaks were genuine continuations -- stage
# them additively at the failure (deduped and close-break re-anchored like the
# other staged marks) and fold the eaten extremes into the restored staircase
# floors, so the resumed leg shows its BOS staircase instead of an event-free
# stretch (the BTC H1 18-25/06 crash: one bearish BOS then only sweeps).
_STAGE_CHOCH_FAILED_WINDOW_BOS = True

# Volatility-normalized proximity for the liquidity-hunt pool map
# (`LiquidityHuntEngine.proximity_atr`): "nearby" opposing pools are the ones
# within N x the visible series' mean true-range% of price, instead of the
# fixed 2% (which is ~6 ATR on a calm BTC 15m chart -- mapping far too many
# pools for the strict all-captured gate to ever clear -- but under 0.5 ATR on
# a volatile daily, mapping none). Same normalization lesson as the detector's
# `bos_leg_origin_release_gap_atr`. Measured 2026-07-06 (BTC/ETH/SOL/AAVE x
# 15m/1h/4h/1d, live snapshots): N=2 preserves the validated SOL H1 map
# (2% was ~1.9 ATR there), unsticks AAVE 4h (a zero-pool map stuck at
# "hunting 0/0" forever becomes an honest captured 3/3) and gives ETH 1d a map
# at all (0/0 -> 0/4); N=3 regressed SOL 4h by pulling in a ~3-ATR-distant
# pool (captured 3/3 -> hunting 3/4), so the conservative end of the release
# gap's own [2, 3] plateau is kept.
_HUNT_PROXIMITY_ATR = 2.0

_HIGHER_TIMEFRAME_MAP: dict[TimeFrame, TimeFrame] = {
    TimeFrame.M1: TimeFrame.H1,
    TimeFrame.M5: TimeFrame.H1,
    TimeFrame.M15: TimeFrame.H1,
    TimeFrame.M30: TimeFrame.H1,
    TimeFrame.H1: TimeFrame.H4,
    TimeFrame.H4: TimeFrame.D1,
    TimeFrame.D1: TimeFrame.W1,
}

# OI points fed to the leverage-liquidation estimator: its
# `open_interest_change_pct` is measured first-to-last over the series it
# receives, so cap it at the pre-existing 500-point horizon even when a longer
# OI history was fetched for the OI regime analysis.
_LIQUIDATION_OI_POINTS = 500

# Extra candles fetched before the visible window so the internal-structure
# detector has history to bootstrap from before reaching the candles actually
# shown on the dashboard. This bounds the region the structural anchor (below)
# scans; it is *not* itself the detection start point.
_INTERNAL_STRUCTURE_BOOTSTRAP_BUFFER = 300

# The internal detector starts detection at the most recent *major extreme*
# (lowest low / highest high) within this many candles before the visible
# window, rather than at a fixed candle offset. A fixed offset lands the
# NEUTRAL->first-break bootstrap on whatever pivot happens to sit there, which
# can inherit a stale, far-back regime (e.g. a months-old downtrend carried into
# a window that has since clearly reversed), making the first CHoCH late and
# wrong-direction. Anchoring at the move's structural origin instead seeds the
# trend from the price action actually entering the window, while staying stable
# across refreshes (a major extreme is a fixed price point, not a sliding
# offset). See `_structural_anchor_index`.
_STRUCTURAL_ANCHOR_REGION = 300

@dataclass(frozen=True)
class DashboardData:
    """A snapshot of research data for a single symbol/timeframe."""

    symbol: str
    timeframe: TimeFrame
    candles: list[Candle]
    current_price: float
    higher_timeframe_direction: MarketDirection
    liquidity_zones: list[LiquidityZone]
    ranked_zones: list[ScoredLiquidityZone]
    market_structure_events: list[MarketStructure]
    internal_structure_events: list[MarketStructure]
    retail_bias: RetailBiasEstimate
    poi_zones: list[POIZone]
    manipulation_cycles: list[ManipulationCycle]
    behavior_divergences: list[BehaviorDivergence]
    liquidity_heatmap: LiquidityHeatmap | None = None
    liquidation_map: LeverageLiquidationMap | None = None
    narrative: MarketNarrative | None = None
    oi_analysis: OIAnalysis | None = None
    liquidity_hunt: LiquidityHuntState | None = None
    # The anchor timeframe `higher_timeframe_direction` was measured on (the
    # `_HIGHER_TIMEFRAME_MAP` pair; None for the top timeframe, whose direction
    # falls back to the current series' own internal trend). Exposed so the
    # frontend can say *which* pair a reading refers to ("vs 4H") instead of a
    # generic "HTF".
    higher_timeframe: TimeFrame | None = None


def _structural_anchor_index(candles: list[Candle], visible_start: datetime) -> int:
    """Index in ``candles`` where internal-structure detection should start.

    Returns the most recent *major extreme* -- the candle with the lowest low or
    the highest high, whichever is more recent -- within the
    ``_STRUCTURAL_ANCHOR_REGION`` candles preceding the visible window (the
    candles before ``visible_start``). Anchoring detection at this deterministic
    structural point seeds the detector's trend from the move actually heading
    into the visible window, while staying stable across refreshes (the extreme
    is a fixed price point, not an offset that slides with the window's right
    edge). Falls back to ``0`` when there is no pre-visible buffer (e.g. the
    provider returned only the visible window).
    """
    visible_start_index = next(
        (i for i, candle in enumerate(candles) if candle.timestamp >= visible_start),
        0,
    )
    region = candles[max(0, visible_start_index - _STRUCTURAL_ANCHOR_REGION) : visible_start_index]
    if not region:
        return 0
    lowest = min(region, key=lambda candle: candle.low)
    highest = max(region, key=lambda candle: candle.high)
    anchor = lowest if lowest.timestamp > highest.timestamp else highest
    return next(i for i, candle in enumerate(candles) if candle.timestamp == anchor.timestamp)


def _reanchor_bos_close_break(
    events: list[MarketStructure], candles: list[Candle]
) -> list[MarketStructure]:
    """Re-anchor each continuation BOS to the first *close* beyond the level it broke.

    A BOS's ``reference_price_level`` is the prior swing extreme it broke (the
    staircase floor). The detector advances state on a close beyond the
    *trailing* reference, which sits above (bearish) / below (bullish) that
    floor, so a BOS can be stamped while price has only *wicked* past the formed
    level. This conservative pass re-times each BOS to the first candle that
    actually *closes* beyond the formed level, searching the window the BOS
    stays active (up to the next same-direction BOS or opposite-direction
    CHoCH, matching the chart's line termination), and *drops* any BOS whose leg
    never closed beyond it -- a wick-only break is not a confirmed continuation.
    The trailing references and CHoCH promotion inside the detector are
    untouched; only the emitted BOS events are re-timed here.
    """
    if not events or not candles:
        return events

    index_by_ts = {candle.timestamp: i for i, candle in enumerate(candles)}
    ordered = sorted(events, key=lambda event: event.timestamp)
    last_index = len(candles) - 1
    result: list[MarketStructure] = []

    for event in ordered:
        if event.event is not StructureEvent.BREAK_OF_STRUCTURE or event.provisional:
            # A provisional BOS is already anchored to its close-break at the live
            # edge (and must not be dropped as "wick-only"); pass it through.
            result.append(event)
            continue
        start_index = index_by_ts.get(event.timestamp)
        if start_index is None:
            result.append(event)
            continue

        # The BOS stays active until the next same-direction BOS or the next
        # opposite-direction CHoCH; the formed level must close within that span.
        end_index = last_index
        for other in ordered:
            if other.timestamp <= event.timestamp:
                continue
            terminates = (
                other.event is StructureEvent.BREAK_OF_STRUCTURE
                and other.direction is event.direction
            ) or (
                other.event is StructureEvent.CHANGE_OF_CHARACTER
                and other.direction is not event.direction
            )
            if terminates:
                other_index = index_by_ts.get(other.timestamp)
                if other_index is not None:
                    end_index = other_index
                break

        floor = event.reference_price_level
        if floor is None:
            result.append(event)
            continue
        bearish = event.direction is MarketDirection.BEARISH
        new_timestamp = None
        for i in range(start_index, end_index + 1):
            close = candles[i].close
            if (bearish and close < floor) or (not bearish and close > floor):
                new_timestamp = candles[i].timestamp
                break

        if new_timestamp is None:
            continue  # leg only wicked the formed level: not a confirmed BOS

        # Anchor the line's *start* at the formed level's origin -- the candle
        # that made the prior swing extreme (low for bearish, high for bullish)
        # at this price -- so it runs from where the level formed to where it
        # broke, rather than starting at the break. Falls back to the break when
        # no exact match is found.
        reference_timestamp = event.reference_timestamp
        for i in range(start_index, -1, -1):
            extreme = candles[i].low if bearish else candles[i].high
            if extreme == floor:
                reference_timestamp = candles[i].timestamp
                break

        updates: dict[str, datetime] = {}
        if new_timestamp != event.timestamp:
            updates["timestamp"] = new_timestamp
        if reference_timestamp != event.reference_timestamp and reference_timestamp is not None:
            updates["reference_timestamp"] = reference_timestamp
        result.append(event.model_copy(update=updates) if updates else event)

    result.sort(key=lambda event: event.timestamp)
    return result


def _drop_pre_break_reference_bos(
    events: list[MarketStructure],
) -> list[MarketStructure]:
    """Drop continuation BOS whose reference formed before the prior BOS broke.

    A wick that pokes beyond the active BOS level without closing (a failed
    break attempt) still ratchets the detector's staircase extreme, so the
    *next* continuation can report that wick as the formed level it broke --
    but that level formed while the prior BOS was still unbroken. It is
    pre-break liquidity, not structure of the new leg: a reference may only
    come from price action *after* the confirming close of the previous
    same-direction BOS in the same leg. Any continuation BOS whose
    ``reference_timestamp`` predates that close is dropped.

    A CHoCH starts a new leg (its first BOS references the CHoCH-seeded
    level, which necessarily formed before the flip), so it resets the
    constraint for its direction. Events without a resolved
    ``reference_timestamp`` are kept -- there is nothing to judge. Runs after
    ``_reanchor_bos_close_break`` so each BOS ``timestamp`` is its confirming
    close and ``reference_timestamp`` the candle that formed the level.
    """
    result: list[MarketStructure] = []
    last_bos_close: dict[MarketDirection, datetime] = {}
    # Two BOS can re-time to the same confirming candle (one close clearing two
    # levels at once); the one whose reference formed earlier is the earlier
    # structural break, so it must be judged (and set the leg's close) first.
    for event in sorted(events, key=lambda e: (e.timestamp, e.reference_timestamp or e.timestamp)):
        if event.event is StructureEvent.CHANGE_OF_CHARACTER:
            last_bos_close.pop(event.direction, None)
        elif event.event is StructureEvent.BREAK_OF_STRUCTURE and not event.provisional:
            prior_close = last_bos_close.get(event.direction)
            if (
                prior_close is not None
                and event.reference_timestamp is not None
                and event.reference_timestamp < prior_close
            ):
                continue
            last_bos_close[event.direction] = event.timestamp
        result.append(event)
    return result


def _build_internal_detector(
    timeframe: TimeFrame, *, confluence_filter: bool
) -> InternalStructureDetector:
    """The production `InternalStructureDetector` wiring for ``timeframe``.

    Single construction point: the current-timeframe run (whose events the
    chart renders) and the higher-timeframe trend run in ``load_dashboard_data``
    must use identical wiring (per-timeframe params + flags), so the
    higher-timeframe direction reported for a pair (e.g. M15 anchored to H1)
    is exactly the trend the user sees when opening that higher timeframe.
    """
    internal_lookback, internal_persistence = _INTERNAL_STRUCTURE_PARAMS.get(
        timeframe, _DEFAULT_INTERNAL_PARAMS
    )
    return InternalStructureDetector(
        swing_lookback=internal_lookback,
        persistence_candles=internal_persistence,
        confluence_filter=confluence_filter,
        # Online re-anchor (flavor B), "chain" trigger: on an extended impulsive
        # leg the high/low references go blind, so the reversal CHoCH would fire
        # late at a stale level. The chain trigger re-anchors them to a local
        # level after `reanchor_chain_threshold` BOS advances in the leg, so the
        # CHoCH lands locally. Threshold 2 (not the constructor default 3): at
        # the production internal lookback legs run ~2 advances, so 3 almost
        # never fires; 2 catches the big impulses (e.g. surfaces the local
        # reversal after a sharp drop) while staying conservative and purely
        # additive. Conservative variant (vs "displacement").
        # See InternalStructureDetector.reanchor_mode.
        reanchor_mode="chain",
        reanchor_chain_threshold=2,
        # The chain trigger only *establishes* a blind reversal reference (an
        # impulse nulled it), never *tightens* a fresh one promoted from a real
        # pullback -- otherwise it degrades the CHoCH reference to a shallow
        # in-leg high so a weak reclaim fires a premature CHoCH. Staleness still
        # tightens genuinely-stale references.
        reanchor_chain_establish_only=True,
        # Reject a re-anchored reversal reference that sits within this fraction
        # of current price: a hair-trigger reference produces a mid-range CHoCH
        # that immediately fails (the chop clutter). See _REANCHOR_MIN_PRICE_GAP_PCT.
        reanchor_min_price_gap_pct=_REANCHOR_MIN_PRICE_GAP_PCT,
        # Retire a stale cycle (same as the major detector wiring in
        # `load_dashboard_data`): the internal detector is what the chart
        # renders for all timeframes, and on coarse ones its bearish/bullish
        # leg can stay pinned to the origin reversal reference while price
        # ranges/recovers, so the CHoCH only fires far overhead. After this
        # many candles with no fresh BOS/CHoCH the reversal reference is
        # pulled to the recent local swing extreme so a CHoCH lands locally
        # and a new cycle begins.
        stale_reanchor_candles=_STALE_REANCHOR_CANDLES.get(
            timeframe, _DEFAULT_STALE_REANCHOR_CANDLES
        ),
        # Stage a continuation BOS at each impulsive advance that displaces the
        # prior BOS level by this fraction, so a sharp multi-leg move shows a
        # staircase instead of one long event-free stretch (the impulsive leg
        # confirms no pullback, so the state machine alone emits no intermediate
        # BOS). Deduped against the real BOS -- only fills the gaps.
        impulse_bos_displacement_pct=_IMPULSE_BOS_DISPLACEMENT_PCT,
        # A BOS must confirm off a real pullback, not a single-candle wick spike:
        # the confirming pivot candle's pivot-side wick must be <= this fraction
        # of its range, else the BOS waits for a genuine pullback. See
        # _BOS_PULLBACK_MAX_WICK_PCT.
        bos_pullback_max_wick_pct=_BOS_PULLBACK_MAX_WICK_PCT,
        # A wick-only pullback keeps its BOS out of the state machine / CHoCH (so
        # the reversal reference stays anchored to a genuine pullback), but the
        # continuation still happened -- add an *additive* mark for it, deduped
        # against the real BOS. Purely visual: it never feeds the state machine,
        # so it cannot cascade into a wrong CHoCH.
        stage_wick_rejected_bos=True,
        # Every confirmed BOS promotes its leg origin (the fundo/topo the
        # breaking leg launched from) directly to the opposite CHoCH reference,
        # and re-anchors cannot slide that structural reference while it stays
        # within the release gap of price -- so the reversal CHoCH fires at the
        # level the leg actually launched from rather than a stale-window local
        # extreme. Once the leg runs away beyond the gap, the staleness
        # re-anchor regains authority (the un-stick pathologies stay fixed).
        bos_leg_origin_choch_ref=True,
        bos_leg_origin_release_gap_pct=_BOS_LEG_ORIGIN_RELEASE_GAP_PCT,
        # Volatility-normalized release gap (takes precedence; the fixed pct
        # above is the fallback for series too short to measure a range).
        bos_leg_origin_release_gap_atr=_BOS_LEG_ORIGIN_RELEASE_GAP_ATR,
        # Shallow-pullback leg-origin promotion: when the immediate pullback that
        # anchored the CHoCH reference retraced < N x ATR%, use the correction's
        # extreme pivot instead, so the CHoCH line sits at the visible leg top.
        bos_leg_origin_min_pullback_atr=_BOS_LEG_ORIGIN_MIN_PULLBACK_ATR.get(timeframe),
        # A leg origin is only a *structural* CHoCH reference if the continuation
        # actually *closed* beyond the prior BOS level. A BOS whose staircase
        # break only wicked past that level (the mark is dropped by
        # _reanchor_bos_close_break anyway) promotes its origin as a *weak*
        # reference instead -- so the new-cycle barrier governs the resulting
        # CHoCH rather than it firing at base persistence off an unconfirmed break.
        bos_leg_origin_require_close_break=True,
        # The reported staircase floor (the level the next continuation BOS
        # plots against) likewise only ratchets on close-confirmed breaks: a
        # wick that merely swept the prior BOS extreme (whose own mark
        # _reanchor_bos_close_break drops) must not become the next
        # continuation's reported reference, nor be reinjected into it via the
        # failed-CHoCH staircase restore. The state-machine gate is untouched.
        bos_floor_require_close_break=True,
        # New-cycle barrier: a CHoCH against a weak (re-anchored/fallback)
        # reference needs a longer sustained hold on the intraday timeframes;
        # structural references keep the base persistence.
        choch_weak_ref_persistence_candles=_CHOCH_WEAK_REF_PERSISTENCE.get(timeframe),
        # Emit a provisional (live-edge) BOS when a continuation has closed beyond
        # the staircase floor but its confirming swing pivots have not formed yet
        # (the swing-lookback lag). Purely additive and confined to the last few
        # candles of the current leg: it is superseded by the real BOS once pivots
        # confirm, or vanishes if the trend flips first. The frontend renders it
        # dimmed. Measured (walk-forward, BTC/ETH/SOL x 1h/4h/1d): ~67% of resolved
        # provisional marks confirm, ~7-candle median lead; the repaints cluster
        # on counter-trend pushes into chop, so it reads as an honest "forming".
        emit_provisional_bos=True,
        # Provisional (live-edge) CHoCH: mirror of the provisional BOS for the
        # reversal. When a *structural* opposite-side CHoCH reference has been
        # closed-broken in a sustained way (persistence consecutive closes beyond)
        # but its confirming swing pivot has not formed yet (the swing-lookback
        # lag), emit a dimmed forming CHoCH so the reversal is visible at the live
        # edge instead of waiting ~lookback candles for the pivot. The SOL M15 case:
        # price sustained a close-break below the 80.72 leg-origin reference but the
        # fundo was too fresh to be a confirmed pivot, so the bearish CHoCH was
        # invisible. Superseded by the confirmed CHoCH once the pivot forms, or it
        # vanishes if price reclaims the level (a mere sweep). Purely additive.
        emit_provisional_choch=True,
        # Fast-fizzle invalidation: a provisional CHoCH that reclaims its own
        # broken level (sustained close) within this many candles never took hold
        # -- fail it there rather than hanging until the far leg origin is
        # reclaimed. A later reclaim is genuine follow-through (leg-origin exit
        # governs). See _CHOCH_FIZZLE_RECLAIM_CANDLES.
        choch_fizzle_reclaim_candles=_CHOCH_FIZZLE_RECLAIM_CANDLES,
        # A failed-CHoCH flip arms no origin, so the fallback suppression above
        # lapses at the failure -- keep the cold-start fallback off for a
        # window so a bounce can't immediately re-flip the trend (the BTC H1
        # 06-25 whipsaw). See _CHOCH_FAILED_FALLBACK_SUPPRESS_CANDLES.
        choch_failed_fallback_suppress_candles=_CHOCH_FAILED_FALLBACK_SUPPRESS_CANDLES,
        # Retro-stage the continuation BOS a failed CHoCH's window ate (they
        # printed as sweeps while the trend was wrongly flipped), so the
        # resumed leg shows its staircase. See _STAGE_CHOCH_FAILED_WINDOW_BOS.
        stage_choch_failed_window_bos=_STAGE_CHOCH_FAILED_WINDOW_BOS,
        # The CHoCH origin (the level a sustained break back through invalidates
        # the unconfirmed reversal, a CHOCH_FAILED) is the *deepest* extreme of
        # the reversed leg, not the trailing `active_<side>`. The trailing
        # reference ratchets toward the new high/low through the reversal leg's
        # intermediate pivots, so at CHoCH confirm it can sit right next to the
        # new extreme (the NEAR M5 case: a bullish CHoCH origin at 2.004 just
        # below the 2.039 top), arming an instant failure on the first minor
        # pullback and ping-ponging the trend into weak CHoCH/CHOCH_FAILED pairs
        # -- so a genuine strong reversal never terminates its own line and it
        # stretches across the chart. Measured (BTC/ETH/SOL/AAVE/NEAR x 5m..1d,
        # limit=1200): CHOCH_FAILED drops ~33% (63 -> 42), converting whipsaw
        # CHoCH/fail pairs into sweeps or holding CHoCHs.
        choch_origin_leg_extreme=True,
    )


def load_dashboard_data(
    provider: OHLCVProvider | None = None,
    symbol: str = "BTCUSDT",
    timeframe: TimeFrame = TimeFrame.H1,
    limit: int = 1200,
    swing_lookback: int = DEFAULT_SWING_LOOKBACK,
    confluence_filter: bool = False,
    futures_provider: FuturesDataProvider | None = None,
) -> DashboardData:
    """Fetch candles and assemble liquidity, ranking, and retail bias data."""
    # Default to perpetual-futures candles (aligned with the futures-derived
    # liquidation/OI/funding analysis, and a 1500-candle per-request window vs
    # spot's 1000), falling back to spot for symbols without a perpetual.
    if provider is None:
        provider = FallbackOHLCVProvider(BinanceFuturesOHLCVProvider(), BinanceDataProvider())

    # Fetch the buffered series once and derive the visible window from its
    # tail. `buffered_candles` prepends `_INTERNAL_STRUCTURE_BOOTSTRAP_BUFFER`
    # candles of history before the visible window (for the internal detector's
    # warm-up and the structural anchor); the visible `candles` are just its
    # last `limit`, so a separate fetch would be redundant -- and a second call
    # could even race a freshly-printed candle, desyncing the two series.
    buffered_limit = min(limit + _INTERNAL_STRUCTURE_BOOTSTRAP_BUFFER, provider.max_fetch_limit)
    buffered_candles = provider.get_ohlcv(symbol, timeframe, buffered_limit)
    candles = buffered_candles[-limit:]

    liquidity_zones = mark_swept_zones(
        [
            *SwingHighDetector().detect(candles),
            *SwingLowDetector().detect(candles),
            *EqualHighDetector().detect(candles),
            *EqualLowDetector().detect(candles),
        ],
        candles,
    )

    current_price = candles[-1].close
    active_zones = [z for z in liquidity_zones if not z.is_mitigated]
    ranked_zones = LiquidityScoringEngine().score(active_zones, current_price)

    visible_start = candles[0].timestamp
    visible_end = candles[-1].timestamp

    # The major (swing) detector runs on the full buffered series. Its BOS are
    # re-anchored to the formed level's close-break (same as the internal
    # detector) to keep the two consistent.
    major_detector = SwingStructureDetector(
        swing_lookback=swing_lookback,
        confluence_filter=confluence_filter,
        # Mirror the internal detector's online re-anchor: see the internal
        # detector call below for the threshold=2 rationale.
        reanchor_mode="chain",
        reanchor_chain_threshold=2,
        # Retire a stale cycle: after this many candles with no fresh BOS/CHoCH
        # the reversal reference is pulled to the recent local swing extreme so a
        # CHoCH fires locally rather than waiting for price to climb back to the
        # leg origin (the long-stuck-BOS pathology on coarse timeframes).
        stale_reanchor_candles=_STALE_REANCHOR_CANDLES.get(
            timeframe, _DEFAULT_STALE_REANCHOR_CANDLES
        ),
    )
    all_major_events = major_detector.detect(buffered_candles)
    all_major_events = _reanchor_bos_close_break(all_major_events, buffered_candles)
    all_major_events = _drop_pre_break_reference_bos(all_major_events)
    market_structure_events = [
        e for e in all_major_events if visible_start <= e.timestamp <= visible_end
    ]

    # The internal detector starts at a structural anchor (the most recent major
    # extreme before the visible window) rather than a fixed candle offset, so
    # the trend it bootstraps reflects the move actually entering the window
    # instead of a stale, far-back regime. See `_structural_anchor_index`.
    internal_candles = buffered_candles[_structural_anchor_index(buffered_candles, visible_start) :]

    internal_detector = _build_internal_detector(timeframe, confluence_filter=confluence_filter)
    all_internal_events = internal_detector.detect(internal_candles)
    # Re-time each BOS to the first close beyond the formed level it broke
    # (dropping wick-only continuations), before the visible filter and POI.
    all_internal_events = _reanchor_bos_close_break(all_internal_events, internal_candles)
    # A reference may only form *after* the prior same-direction BOS broke: a
    # continuation referencing a pre-break wick attempt at the prior level is
    # dropped (pre-break liquidity, not structure of the new leg).
    all_internal_events = _drop_pre_break_reference_bos(all_internal_events)
    internal_structure_events = [
        e for e in all_internal_events if visible_start <= e.timestamp <= visible_end
    ]

    # The MSB order block detector is self-contained (it derives its own swing
    # pivots); it runs on the same structurally anchored slice as the internal
    # detector so zones anchored just before the visible window still render.
    all_poi_zones = POIDetector().detect(internal_candles)
    poi_zones = [z for z in all_poi_zones if visible_start <= z.created_at <= visible_end]

    htf = _HIGHER_TIMEFRAME_MAP.get(timeframe)
    if htf is not None:
        # The higher-timeframe trend comes from the *internal* detector run on
        # the HTF series with that timeframe's own production wiring (params +
        # flags via `_build_internal_detector`, buffered fetch + structural
        # anchor) -- the same run the HTF view renders -- so the reported HTF
        # direction always matches the structure the user sees when opening
        # that timeframe, and the hunt's "counter-trend?" comparison uses the
        # same trend semantics on both sides of the pair. The previous source
        # (the major swing detector on a 100-candle window) used a different
        # methodology on a window too short for its lookback, so it could flip
        # weeks late, sit NEUTRAL, or outright contradict the HTF chart.
        # State-machine trend, not the last event's direction: the latter flips
        # on a descriptive HL/LH pivot or a LIQUIDITY_SWEEP whose `direction`
        # is the pivot/wick side rather than the standing trend.
        htf_candles = provider.get_ohlcv(symbol, htf, buffered_limit)
        htf_visible_start = htf_candles[max(0, len(htf_candles) - limit)].timestamp
        htf_detector = _build_internal_detector(htf, confluence_filter=confluence_filter)
        htf_detector.detect(
            htf_candles[_structural_anchor_index(htf_candles, htf_visible_start) :]
        )
        higher_timeframe_direction = htf_detector.final_trend
    else:
        # Top timeframe (no higher TF): degrade to the current series' own
        # internal trend, so downstream comparisons (the liquidity hunt's
        # counter-trend check) read "aligned" rather than pitting two
        # different methodologies against each other.
        higher_timeframe_direction = internal_detector.final_trend

    retail_bias = RetailTrapAnalyzer().analyze(
        symbol=symbol,
        higher_timeframe_direction=higher_timeframe_direction,
        market_structure_events=market_structure_events,
        liquidity_zones=liquidity_zones,
        current_price=current_price,
    )

    all_structure = market_structure_events + internal_structure_events
    vd = volume_delta_series(candles)
    manipulation_cycles = ManipulationCycleDetector().detect(
        candles=candles,
        structure_events=all_structure,
        liquidity_zones=liquidity_zones,
        volume_deltas=vd,
    )

    behavior_divergences = BehaviorDivergenceAnalyzer().analyze(
        candles=candles,
        volume_deltas=vd,
        liquidity_zones=liquidity_zones,
        structure_events=all_structure,
    )

    liquidity_heatmap = LiquidityHeatmapEngine().build(
        symbol=symbol,
        timeframe=timeframe,
        candles=candles,
        current_price=current_price,
        liquidity_zones=liquidity_zones,
        poi_zones=poi_zones,
        manipulation_cycles=manipulation_cycles,
        retail_bias=retail_bias,
    )

    # One futures fetch feeds both the liquidation map and the OI analysis.
    # The OI history is requested for the whole visible window (the provider
    # paginates past Binance's 500-row cap, clamped to its ~30-day retention),
    # so structure events across the chart can be OI-qualified.
    futures_state = _fetch_futures_state(
        futures_provider if futures_provider is not None else BinanceFuturesDataProvider(),
        symbol=symbol,
        timeframe=timeframe,
        oi_limit=limit,
    )
    if futures_state is None:
        liquidation_map = None
        oi_analysis = None
    else:
        open_interest, funding, long_short = futures_state
        liquidation_map = LeverageLiquidationEstimator().estimate(
            symbol=symbol,
            timeframe=timeframe,
            current_price=current_price,
            candles=candles,
            liquidity_zones=liquidity_zones,
            # Keep the estimator's OI-change horizon at its historical 500
            # points; the longer series fetched for the OI analysis would
            # silently stretch `open_interest_change_pct` otherwise.
            open_interest=open_interest[-_LIQUIDATION_OI_POINTS:],
            funding=funding,
            long_short=long_short,
            poi_zones=poi_zones,
        )
        oi_analysis = OIRegimeAnalyzer().analyze(
            candles=candles,
            open_interest=open_interest,
            structure_events=internal_structure_events,
        )

    data = DashboardData(
        symbol=symbol,
        timeframe=timeframe,
        candles=candles,
        current_price=current_price,
        higher_timeframe_direction=higher_timeframe_direction,
        higher_timeframe=htf,
        liquidity_zones=liquidity_zones,
        ranked_zones=ranked_zones,
        market_structure_events=market_structure_events,
        internal_structure_events=internal_structure_events,
        retail_bias=retail_bias,
        poi_zones=poi_zones,
        manipulation_cycles=manipulation_cycles,
        behavior_divergences=behavior_divergences,
        liquidity_heatmap=liquidity_heatmap,
        liquidation_map=liquidation_map,
        oi_analysis=oi_analysis,
    )

    from liquidity_hunter.app.liquidity_hunt import LiquidityHuntEngine
    from liquidity_hunter.app.narrative import NarrativeEngine

    # Both synthesizers read the fully assembled snapshot (they cross-reference
    # outputs from every layer), so they run last, at the composition point.
    narrative = NarrativeEngine().build(data)
    liquidity_hunt = LiquidityHuntEngine(proximity_atr=_HUNT_PROXIMITY_ATR).build(data)
    return replace(data, narrative=narrative, liquidity_hunt=liquidity_hunt)


def _fetch_futures_state(
    futures_provider: FuturesDataProvider,
    symbol: str,
    timeframe: TimeFrame,
    oi_limit: int,
) -> tuple[list[OpenInterestPoint], list[FundingRate], list[LongShortRatio]] | None:
    """Fetch perpetual-futures market state (OI history, funding, long/short).

    Degrades to ``None`` if futures data is unavailable (e.g. the symbol has no
    perpetual contract, or the venue is unreachable), so the dashboard still
    renders for spot-only symbols — the liquidation map and OI analysis both
    become ``None``.
    """
    try:
        open_interest = futures_provider.get_open_interest_history(
            symbol, timeframe, limit=oi_limit
        )
        funding = futures_provider.get_funding_rate_history(symbol)
        long_short = futures_provider.get_long_short_ratio(symbol, timeframe)
    except DataProviderError:
        logger.warning(
            "Futures data unavailable for %s; skipping liquidation map and OI analysis", symbol
        )
        return None
    return open_interest, funding, long_short
