"""Composition root for the research dashboard.

Wires together `data`, `liquidity`, `scoring`, and `psychology` into a
single `DashboardData` snapshot for `dashboard` to render.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime
from statistics import fmean

from liquidity_hunter.core.domain import (
    Candle,
    ConsolidationRange,
    ConsolidationStatus,
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
    detect_consolidation_ranges_with_resets,
    mark_swept_zones,
    stage_breakout_events,
)
from liquidity_hunter.liquidity.detectors._common import (
    RangeReset,
    resolve_break_origin_timestamp,
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
    TimeFrame.M5: (5, 2),
    TimeFrame.M15: (5, 2),
    TimeFrame.M30: (5, 2),
    TimeFrame.H1: (5, 2),
    TimeFrame.H4: (5, 2),
    TimeFrame.D1: (5, 2),
    TimeFrame.W1: (5, 2),
}
_DEFAULT_INTERNAL_PARAMS = (5, 2)

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

# Displacement release for the staleness re-anchor
# (`InternalStructureDetector.stale_reanchor_displacement_atr` /
# `stale_reanchor_displacement_candles`, internal detector only). The staleness
# timer above is blind to how far a leg stretched: after a violent move the
# reversal reference stays pinned at the pre-move leg origin for the full
# window, so the strongest bounce of the cycle is consumed as a sweep against a
# level many ATRs overhead (the ETHUSDT H4 2026-06-05 crash: -26% in days, the
# +23% bounce to 1848 nine days later printed as a `LIQUIDITY_SWEEP` against
# the 2046 leg origin, and the chart sat on the mid-crash BOS for a month).
# When the gap between the reversal reference and the leg's running extreme
# reaches N x mean true-range% (volatility-normalized, so the same N means the
# same number of typical candles on every asset/timeframe -- the per-timeframe
# adaptivity the fixed timer lacks), the staleness threshold shrinks to the
# displacement candle count and the re-anchor window starts at the last
# advance, landing the reference on the post-move range's first pullback
# extreme. (A companion sweep ratchet for weak references -- tracking the range
# extreme when a bounce swept beyond the released reference -- was prototyped
# alongside and rejected by measurement: on a grinding decline it chased the
# reference away from the confirming closes, erasing the genuine ETH H4 March
# 2026 bearish cycle; see the detector docstring.)
#
# N=16 measured 2026-07-11 (BTC/ETH/SOL/AAVE/NEAR x 15m/30m/1h/4h/1d,
# limit=1200): 6/25 combos change, all the motivating pattern (a large bounce
# inside/after a displaced leg that printed only sweeps) -- ETH 4h resolves the
# month-long stuck cycle into CHoCH 06-15 (ref 1722, the first pullback) ->
# CHoCH 06-23 -> BOS 06-24 -> CHoCH 07-02 (structural); BTC 4h gains the
# honest 06-14 CHoCH + 06-27 CHOCH_FAILED pair around the June crash; AAVE 4h
# gains the missing Feb-Mar -30% bearish cycle (CHoCH 03-07 + BOS staircase
# 104->92); NEAR 1h / SOL 4h flip the June bottom ~6-17 days earlier with a
# confirming BOS staircase. N=8 fires on routine legs (25/25 combos -- a leg's
# ref-to-extreme gap IS its height, and 8 ATR legs are normal); N=14 starts
# reshaping BTC 30m/1d (trend flip); N=18 loses AAVE 4h / NEAR 1h; N=20 loses
# the ETH 4h target itself (~19 ATR). The candle count M is a wide plateau --
# 10/15/20/25 are byte-identical on the whole matrix (by the time a displaced
# leg's next pivot registers, the candles-since-advance already exceed it);
# 15 keeps a real quiet-period requirement without delaying the release.
_STALE_REANCHOR_DISPLACEMENT_ATR: float | None = 16.0
_STALE_REANCHOR_DISPLACEMENT_CANDLES: int | None = 15

# Consolidation (lateral range) detection
# (`liquidity.detectors.consolidation.detect_consolidation_ranges`, run at the
# composition level over the *surviving* internal event stream). Inside a
# range the structure detector is correctly silent -- both references sit
# outside the box (the staircase at a pre-range wick above, the CHoCH
# reference at the leg origin below), so nothing inside can trigger -- but
# that silence is indistinguishable on the chart from a stuck detector (the
# BTC/ETH H1 2026-07 locks: 10 days of sweeps/labels only). The post-pass
# turns the silence into an explicit `ConsolidationRange` observation.
# Segment boundaries are the post-composition-pass, non-provisional
# BOS/CHoCH/`CHOCH_FAILED` -- the events the chart draws -- rather than the
# detector's internal advances: a detector advance later dropped as
# wick-only would split a visible range at an invisible point (measured on
# BTC H1 07/2026: split at a dropped 07-10 BOS).
#
# A range confirms after `_CONSOLIDATION_MIN_CANDLES` candles inside a box no
# taller than `_CONSOLIDATION_MAX_HEIGHT_ATR` x the series' mean true-range%
# (volatility-normalized like the displacement release), with alternating
# touches of both boundary zones; `_CONSOLIDATION_RESOLVE_PERSISTENCE` closes
# beyond a boundary resolve it. Calibrated 2026-07-14 on the
# btcusdt/ethusdt_1h_2026_05_13_07_14 fixtures (the motivating locks) plus a
# BTC/ETH/SOL/AAVE/NEAR x 15m/1h/4h/1d live matrix: both July H1 locks
# confirm as single live ranges (BTC 07-04-> [61297-64692], ETH 07-06->
# [1712-1830]) and the matrix stays at 2-8 ranges per 1200-candle combo, with
# BTC H1/H4 independently finding the same July box. N=40 adds sub-2.5-day
# boxes that read as routine pauses; N=80 only delays confirmation (~3.3
# days on H1) without cleaning anything the fixtures care about. K=8 keeps
# boundary-sweep wicks outside the box (K=10 absorbs ETH's 07-12 1848 spike
# into the box top, hiding the sweep).
_CONSOLIDATION_MIN_CANDLES = 60
_CONSOLIDATION_MAX_HEIGHT_ATR = 8.0
_CONSOLIDATION_RESOLVE_PERSISTENCE = 4

# Consolidation breakout staging (phase 2,
# `liquidity.detectors.consolidation.stage_breakout_events`). A range's
# boundary is the structural level its breakout actually broke -- often
# breakable while the state machine's own references (staircase bar at a
# pre-range wick, CHoCH ref at the leg origin) remain out of reach -- so each
# range resolved by a sustained boundary break stages one additive event at
# the breakout candle: a BOS when the break continues the segment's standing
# trend, a `provisional=True` CHoCH when it reverses it (the additive
# contract: the state-machine trend never flipped, so hunt/narrative replay
# skip it while the chart shows the dimmed mark). Deduped when a real
# same-direction BOS/CHoCH sits within `_CONSOLIDATION_STAGE_DEDUP_CANDLES`
# of the breakout -- the state machine caught the break itself (e.g. BTC H1
# 07-02: the June-bottom range resolves bullish on the same candle as the
# real weak-ref CHoCH). Window = 12 (the internal persistence: within one
# confirmation window the real event and the staged one are the same break
# read twice; beyond it they are separate structural facts).
_CONSOLIDATION_STAGE_BREAKOUT_EVENTS = True
_CONSOLIDATION_STAGE_DEDUP_CANDLES = 12

# Consolidation cycle reset (phase 3, `range_reset_cycle`). Where phase 2 only
# *stages* an additive mark at a breakout, phase 3 re-seeds the state machine's
# structural references at the box boundaries (a second
# `InternalStructureDetector.detect` pass fed the `RangeReset` directives the
# consolidation scanner emits): the counter-trend CHoCH reference collapses to
# the opposite boundary, the with-trend staircase to the near boundary, so
# while price sits in the box the references track the box instead of levels
# pinned far outside it.
#
# Scoped to the single **ACTIVE** range (`_scope_resets_to_live_range`): the
# one still open at the edge, the range that looks stuck *now*. The blanket
# re-seed of every historical range was measured and rejected -- re-seeding a
# resolved range cascades through the settled structure after it and rewrote
# months of history (it flipped ETH 4H's July conclusion to bearish against a
# +12% rally; see `docs/structure_decisions.md`). Resolved ranges keep their
# additive phase-2 staged marks instead.
#
# Because a range un-scopes the moment it *resolves* (4 sustained closes past a
# boundary), the effect is conservative and tail-bounded: while price ranges
# inside, the re-seed suppresses premature mid-box provisional marks (the
# "looks stuck" clutter); during the first candles of a breakout, before the
# range formally resolves, the forming mark is anchored at the real boundary;
# once resolved, phase-2 staging takes over. It does *not* by itself turn a
# range-exit reversal into a real trend flip -- that needs the re-seed to
# persist through resolution (a follow-up, with the CHOCH_FAILED net preserved
# through the re-seed). Default OFF; measured 0/20 structural changes and
# 0 trend flips on the live matrix (only BTC 4H's spurious mid-box `BOS?`
# dropped). With no active range the second pass is skipped and the output is
# identical to phase 2.
_CONSOLIDATION_RANGE_RESET_CYCLE = False

# Provisional (live-edge) CHoCH against *weak* references
# (`InternalStructureDetector.emit_provisional_choch_weak`). After any
# re-anchor the standing reference is weak, so exactly in the released/reset
# cycles the displacement release creates, the forming reversal was invisible
# (the ETH H4 case: price closed above the weak 1779 reference with nothing on
# screen). Weak references sustain the weak-ref barrier persistence where
# wired; the mark carries `reference_structural=False` (dimmed + `*` styling
# on top of the provisional `?`).
_EMIT_PROVISIONAL_CHOCH_WEAK = True

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
# by 6h. Coarse timeframes (H4+) are left alone: since the base dropped to a
# uniform 2 they are covered by the confirmed-trend barrier below instead.
# (M1 deliberately absent: not on the production ladder, never measured.)
_CHOCH_WEAK_REF_PERSISTENCE: dict[TimeFrame, int] = {
    TimeFrame.M5: 4,
    TimeFrame.M15: 4,
    TimeFrame.M30: 4,
    TimeFrame.H1: 4,
}

# Confirmed-trend barrier, all timeframes
# (`InternalStructureDetector.choch_confirmed_trend_persistence_candles`):
# hysteresis on trend flips. A trend set by a CHoCH is *pending* until an
# emitted BOS in its direction confirms it (the same moment the CHoCH origin
# retires; a displacement-success retirement counts) -- while pending, the
# reverse CHoCH keeps the cheap base persistence and CHOCH_FAILED remains the
# escape valve. Once *confirmed*, a counter-trend CHoCH must sustain this many
# closes: with the base persistence dropped to 2 (fast flips), a single
# stop-hunt poke through the reversal reference would otherwise flip a
# structure that already printed a confirming BOS -- the barrier reports it as
# a LIQUIDITY_SWEEP instead (existing non-sustained branch), or the CHoCH
# simply confirms a few candles later when the break is real. Measured
# 2026-07-16 (BTC/ETH/SOL/AAVE/NEAR x 5m..1d, barrier 4/6/8 vs off, at base
# persistence 2): the diff signature at every level is the intended one --
# whipsaw CHoCH+CHOCH_FAILED pairs reclassified to sweeps, the genuine CHoCH
# re-confirming a few candles later (barrier 4: -68 CHoCH/+36 re-timed,
# CHOCH_FAILED 7->4, +58 sweeps, one standing-trend change -- a BTC 15m
# live-edge whipsaw correctly killed). 4 chosen (2x base, the same value the
# weak-ref barrier measured best): 6 doubles the churn and starts changing
# standing conclusions that need visual review (AAVE 1h), 8 rewrites settled
# coarse history (4 trend flips). Raise after visual review if stop hunts
# still flip confirmed structures.
_CHOCH_CONFIRMED_TREND_PERSISTENCE: int | None = 4

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

# Pending-CHoCH invalidation at the broken level, structural references too
# (`InternalStructureDetector.choch_pending_fail_at_broken_level` +
# `choch_pending_fail_persistence_candles`): the pending half of the
# PENDING/CONFIRMED hysteresis. A CHoCH with no confirming BOS also dies on a
# sustained reclaim of the very level it broke -- without this, an impulsive
# counter-move that never printed a BOS leaves both exits (origin CHOCH_FAILED
# and the reverse-CHoCH reference) pinned at the reversed leg's extreme, and a
# full recovery prints as a chain of sweeps under a stale trend (AAVEUSDT H1
# 2026-07-08: bearish CHoCH at the structural 87.90, no bearish BOS, +14% of
# rally read as three bullish sweeps until the 97.4 origin broke three days
# later). The structural-level failure demands its own persistence (below),
# stronger than base, so an ordinary retest of a genuine leg origin does not
# kill the reversal; weak references keep the existing weak-fail behavior.
# Measured 2026-07-16 (BTC/ETH/SOL/AAVE/NEAR x 5m..1d, persistence 5/6/8 vs
# off): signature is the intended one -- +89 real CHOCH_FAILED at pers 6 (each
# a pending CHoCH properly disregarded on the reclaim), net sweeps down, two
# standing-trend corrections (AAVE 30m + 1h bearish->bullish, the motivating
# stale-trend case). 6 chosen: 5 also kills an ordinary retest (the AAVE H1
# 07-04 dip against the correct 07-03 bullish CHoCH, and a SOL 15m standing
# flip); 8 is near-identical to 6 -- the plateau between "ordinary retest"
# (held < 5) and "real fizzle reclaim" (held >> 8) is wide.
_CHOCH_PENDING_FAIL_AT_BROKEN_LEVEL = True
_CHOCH_PENDING_FAIL_PERSISTENCE: int | None = 6

# Weak-referenced CHoCH invalidation at the broken level itself
# (`InternalStructureDetector.choch_weak_ref_fail_at_broken_level`). A CHoCH
# fired against a *weak* reference (a synthetic re-anchor level or the
# cold-start fallback) has the break of that level as its only reversal
# evidence, so a sustained close back through it fails the CHoCH
# (CHOCH_FAILED, real trend flip) without waiting for the far leg origin.
# Motivating case (BTCUSDT D1): the 2026-04-30 bullish CHoCH against the weak
# 75998.9 re-anchor collapsed within days, but the 59800 leg origin was never
# sustained-broken -- the trend sat bullish through the entire 82.8k -> 57.7k
# crash (-30%), every new low printed as a counter-trend sweep, and the chart
# showed no bearish BOS at the bottom (unlike ETH D1, whose rally never fired
# a CHoCH and whose June break below 1736 printed the continuation BOS).
# Structural CHoCHs keep the origin-only invalidation (base persistence, both
# levels).
_CHOCH_WEAK_REF_FAIL_AT_BROKEN_LEVEL = True

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

# Failed-CHoCH re-activation
# (`InternalStructureDetector.choch_failed_rearm`). A `CHOCH_FAILED`
# permanently discards its CHoCH (one-shot origins + the fallback suppression
# above), so when the "reclaim" that failed it turns out to be the old trend's
# last gasp -- price rolls back over and sustains beyond the very level the
# CHoCH had broken -- nothing re-fires, and the resumed move prints as a chain
# of sweeps under the wrong trend. The motivating MUUSDT H4 2026-07 case: a
# bearish CHoCH at the weak 1026.86 failed on a flat drift hugging the level
# (closes 0.1-1.9% above it), then the -19% collapse read as two sweeps (one
# inside the suppression window with ref=None, one against the dead-cat
# bounce's 875.67 leg origin) while the trend sat bullish for weeks. When set,
# the failure arms the broken level as a re-arm reference: a later sustained
# break back beyond it (scanned from the failure onward, at the original
# reference's weak/structural persistence class) re-emits the CHoCH and flips
# the trend. One-shot per failure chain (a re-fired CHoCH's own failure does
# not re-arm); the memory drops at any CHoCH emission or once the opposite
# trend is confirmed (emitted BOS / displacement-success). Measured 2026-07-16
# (BTC/ETH/SOL/AAVE/NEAR/MU x 5m..1d, limit=1200): 31/36 combos change with
# the intended signature -- sweep chains under stale trends become re-fired
# CHoCH + BOS staircases (sweeps -69/+46, CHoCH -57/+80, CHOCH_FAILED -24/+39
# -- the extra failures are honest re-failures of re-fires), 2 standing-trend
# changes (NEAR 15m/30m bearish -> bullish, the 30m one correcting a false
# CHOCH_FAILED against a +7% rally that then made higher lows).
_CHOCH_FAILED_REARM = True

# Persistent re-arm memory
# (`InternalStructureDetector.choch_failed_rearm_persistent`). The re-arm
# above is one-shot: it retires at opposite-trend confirmation and a failed
# re-fire never re-arms. The motivating BTCUSDT D1 2025-08..11 case: a bearish
# CHoCH at 111850 (ref formed 2025-08-03) failed on 2025-09-10, a late-September
# dip re-fired it, the re-fire failed on the October rally to a marginal new
# high (126208 over 124546 -- one continuation BOS at the sweep-shaped top),
# and the chain died -- so when the whole leg was given back nothing could
# re-fire, and the November crash's reversal waited for a late, weak trailing
# reference (98889 on 11-14, eleven candles after the 111850 break). When set,
# every failure (re-fires included) re-arms the level, and opposite-trend
# confirmation *demotes* the memory instead of retiring it: a demoted re-arm
# coexisting with a live fallback is arbitrated by whichever level price
# crosses first in the break direction, so a far armed level cannot shadow a
# nearer live reference (full rank swallowed the fixture's April 2026 CHoCH
# behind a 94760 re-arm) while a nearer armed level still catches the round
# trip early (a strict below-fallback rank lost the October re-fire to the
# farther 103470 trailing low). The sustained October break re-fires the
# CHoCH at the proven 111850 level on 10-15 (a month earlier than HEAD's
# 11-14 weak reference), and the crash prints as a bearish BOS staircase.
# Ping-pong stays bounded by the per-flip persistence rules and the
# failed-re-fire collapse pass (which re-anchors a surviving re-fire's
# reference_timestamp to the surviving failure). Measured 2026-07-17
# (BTC/ETH/SOL/AAVE/NEAR/ENA x 15m..1d, limit=1200): 11/30 combos change,
# 0 standing-trend flips; ETH 1D's 2024-08 carry-trade crash flips bearish on
# 08-01 (CHoCH ref 3205) instead of 08-27 (ref 2533), and the noisy 15m
# combos net *fewer* events (whipsaw CHoCH pairs read as sweeps). Known
# window sensitivity: on the frozen D1 fixture (same candles, window shifted
# 6 days earlier) the January cascade leaves a different trailing
# `active_high`, the staleness re-anchor's tighten-only guard then never
# establishes the frozen 75998.9 reference, and the April 2026 bullish cycle
# reads as sweeps -- the live window keeps it intact.
_CHOCH_FAILED_REARM_PERSISTENT = True

# Live-edge CHOCH_FAILED emission
# (`InternalStructureDetector.choch_fail_live_edge`). The failure checks are
# pivot-gated, so on a relentless one-way move (every candle a new extreme, no
# swing pivot forming for days) a CHoCH whose fail level has long been
# sustained-broken keeps its wrong trend standing at the live edge: the MUUSDT
# H4 2026-07-14 re-fired bullish CHoCH sat with the trend bullish while price
# fell 19% below the 962.15 level and six closes cleared the pending-fail
# persistence -- the vertical drop never formed the low pivot that would have
# emitted the failure (the additive fizzle marker showed, but it never flips
# the trend, so the ladder/hunt read bullish through the crash). When set, the
# same failure check runs once more over the final state at the end of
# `detect` and emits the real `CHOCH_FAILED` (trend flip included) at the
# sustained-break candle. Deterministic across runs, and the in-loop path
# emits the identical event once a pivot finally forms.
_CHOCH_FAIL_LIVE_EDGE = True

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

# Displacement-success CHoCH-origin retirement
# (`InternalStructureDetector.choch_success_displacement_atr`). An impulsive
# reversal leg can run far past the level whose reclaim would fail it without
# ever emitting a confirming BOS (the impulse forms no pullback pivot, so the
# state machine confirms none) -- especially the first leg after a CHoCH, which
# has no prior staircase floor for the impulse-BOS staging to fill. The origin
# stays armed and the eventual mean-reversion fires a false CHOCH_FAILED on a
# move that plainly succeeded (the NEAR H1 2026-06 case: two bullish CHoCHs
# rallied +11% / ~5.0 ATR and +16% / ~7.6 ATR, then both got marked failed on
# the pullback). Once the leg extreme has displaced this many ATR% beyond the
# fail level, retire the origin as a confirming BOS would: the reversal is
# established, and a later reversal is a fresh opposite CHoCH, not a failure of
# this one. 4.5 catches both NEAR cases (the shallower is ~5.0 ATR, so ~0.5 ATR
# of margin against live drift) while staying well clear of a shallow
# pop-then-fail (a genuine failed reversal rarely runs 4.5 ATR). Measured
# (BTC/ETH/SOL/AAVE/NEAR x 5m..1d, limit=1200): non-provisional CHOCH_FAILED
# 30 -> 23, CHANGE_OF_CHARACTER 171 -> 182 (genuine reversals surfaced where a
# false failure had masked them), and the standing final_trend is *unchanged*
# on every combo -- the retirement only rewrites intermediate narration, never
# the trend state.
_CHOCH_SUCCESS_DISPLACEMENT_ATR: float | None = 4.5

# Percentage cap on the displacement-success threshold
# (`InternalStructureDetector.choch_success_displacement_max_pct`). The ATR
# unit above self-adapts to each series' volatility, but on an extremely
# volatile series it degenerates: on an alt daily with a ~10% mean TR, 4.5 ATR
# demands a 32-49% move (survey 2026-07-17: BTC 1D 16%, ETH 23%, SOL 32%,
# AAVE 33%, AERO 44%, ENA 49%), so a plainly successful impulse still gets its
# CHoCH cancelled on the give-back (the AEROUSDT 1D 2026-06 case: a bearish
# CHoCH + close-broken BOS fell -31% -- ~2.6 ATR as the detector measures it --
# then the V-recovery reclaimed the level and fired a retroactive CHOCH_FAILED
# instead of a fresh bullish CHoCH). Capping the threshold at 20% of price
# bounds the requirement exactly where the ATR unit breaks down: every
# intraday combo sits far below the cap (H1 3-9%, H4 6-15%, byte-for-byte
# identical) and only the volatile dailies are governed by it. Measured
# (BTC/ETH/SOL/AAVE/NEAR/AERO/ENA x 1h/4h/1d, limit=1200): only volatile-daily
# combos change, final_trend preserved; AERO 1D keeps its May-June bearish
# cycle valid and reads the June V-recovery as a fresh bullish CHoCH.
_CHOCH_SUCCESS_DISPLACEMENT_MAX_PCT: float | None = 0.20

# Reversal-eaten BOS staging (`InternalStructureDetector.stage_reversal_eaten_bos`).
# A BOS is only *emitted* once a confirming opposite pullback pivot forms after
# the close-break. On an impulsive final leg that reverses immediately -- the
# classic "last lower low that closes below the prior fundo, then a CHoCH the
# other way" -- the reversal CHoCH arrives first and the still-pending BOS is
# discarded without ever emitting, leaving that break (the close that *permits*
# the reversal) invisible. When the discarded pending BOS's staircase floor had
# already *closed*-broken, stage an additive mark for it at the CHoCH, keyed on
# the close through the floor (the trader's validation) rather than the impulse
# stager's displacement threshold -- deduped against real BOS and re-timed to
# the close-break by `_reanchor_bos_close_break` like the other staged marks.
# The ENA M30 2026-07-13 case: a bearish leg's final low (0.07679) closes below
# its 0.07760 fundo, then a bullish CHoCH; without staging the leg shows no BOS
# for that break at all.
_STAGE_REVERSAL_EATEN_BOS = True

# Leg-launch BOS rescue in `_reanchor_bos_close_break`. The first BOS of a leg
# reports the CHoCH-seeded launch level (the fundo/topo the reversal formed),
# but the re-anchor pass confirms each BOS only within its own window, which
# ends at the *next* same-direction BOS. On a leg that retests the CHoCH before
# breaking down, the launch level's first confirming close can land a few
# candles inside that successor's territory -- the launch BOS is dropped as
# "wick-only" and the chart promotes a shallow, late fundo to first-of-leg
# reference. When the *leg-launch* BOS (no same-direction BOS between the flip
# that started the leg and it) finds no close in its own window, extend the
# search through the *next* same-direction BOS's window -- the launch break may
# confirm at most one continuation late. A close through the launch level there
# confirms the break, the BOS is re-timed to it, and the passed-over shallower
# continuations are suppressed -- they are premature clutter next to the launch
# break, and their confirming close would re-kill the rescued mark in
# `_drop_pre_break_reference_bos` (whose leg reset the rescued BOS now owns).
# The one-continuation bound is load-bearing: unbounded (to the leg's death), an
# AAVEUSDT D1 launch BOS whose floor sat far beyond the leg scanned seven months
# and suppressed the real staircase it passed. See `_leg_launch_rescue_index`.
# A leg that reverses without ever closing through still drops (the wick-only
# protection is untouched). The ENA M30 2026-07-12 case: the bearish leg's
# launch BOS (ref 0.07908, the CHoCH fundo that retested the CHoCH) only
# close-breaks at 07-13 03:00, three candles past its successor (ref 0.07954,
# a shallow fundo formed 22 hours after the launch level) -- rescued, the leg
# reads CHoCH -> BOS 0.07908 -> BOS 0.07760 -> CHoCH.
_RESCUE_LEG_LAUNCH_BOS = True

# Superseded-continuation BOS staging
# (`InternalStructureDetector.stage_superseded_continuation_bos`). A BOS only
# *emits* once a confirming opposite pullback pivot forms. In an impulsive leg
# of consecutive same-side pivots, the next advance overwrites the still-pending
# BOS before that pivot appears -- and the reported floor has meanwhile ratcheted
# to the new pivot -- so only the *last* pending of the run ever emits, and the
# top/bottom that genuinely formed and was broken never gets a mark. Sibling of
# `_STAGE_REVERSAL_EATEN_BOS` (a pending eaten by the *reversal*); here the
# pending is eaten by the next *continuation*, and the same
# close-through-the-floor key applies. The NEARUSDT M15 2026-07-14 case: the
# 07:15 topo 2.0120 formed, price pulled back to 1.9670 and closed through it,
# but no low pivot formed between the 12:30 (2.0400) and 15:30 (2.0660)
# advances -- the leg's only BOS referenced 2.0400, its line starting at 12:30
# instead of the 07:15 topo. Staged, the leg reads BOS 2.0120 then BOS 2.0400.
_STAGE_SUPERSEDED_CONTINUATION_BOS = True

# Seed a first pending BOS's pullback ref with the CHoCH origin the leg
# launched from (`bos_pullback_seed_choch_origin`): the first advance of a
# CHoCH-launched leg often snapshots a `None` pullback ref (the flip promoted
# an empty pending_<side> and there is no prior pending to inherit from), so
# the BOS can never confirm -- and with no emission the whole reverse-CHoCH
# reference family (leg origin, candidate) is never built, leaving the
# counter-trend side with zero references (the ENAUSDT H4 2026-06 case: a
# -22% drop from 0.0905 to 0.070 printed only sweeps, then a CHoCH at the
# very low that instantly failed).
_BOS_PULLBACK_SEED_CHOCH_ORIGIN = True

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
    # Confirmed lateral consolidation ranges overlapping the visible window
    # (see `_detect_consolidations`): where the structure detector was
    # *correctly* silent because price was ranging, made explicit.
    consolidation_ranges: list[ConsolidationRange] = field(default_factory=list)


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


def _leg_launch_rescue_index(
    event: MarketStructure,
    ordered: list[MarketStructure],
    candles: list[Candle],
    index_by_ts: dict[datetime, int],
    end_index: int,
    floor: float,
    *,
    bearish: bool,
) -> int | None:
    """Find the close that rescues a leg-launch BOS past its primary window.

    Applies only to the *first* BOS of a leg: no same-direction BOS between the
    flip that started the leg -- a same-direction CHoCH or opposite-direction
    ``CHOCH_FAILED`` -- and the event. Its reported floor is the CHoCH-seeded
    launch level, whose first confirming close can land inside the *next*
    continuation's window (the ENA M30 case behind ``_RESCUE_LEG_LAUNCH_BOS``).
    The extended search runs from the end of the primary window through the
    *next* same-direction BOS's window only -- the launch break may confirm at
    most one continuation late. An unbounded search to the leg's death lets a
    launch BOS whose floor sits far beyond the leg (the AAVEUSDT D1 2025-11
    case: floor 80.01 on a leg trading at 145) scan for months and suppress the
    real staircase it passes (176.46 -> 145.0 -> 91.85). Leg death (the next
    non-provisional opposite-direction CHoCH or same-direction
    ``CHOCH_FAILED``) still caps it. The index of the first close through the
    floor is returned; ``None`` when the event is not a leg launch, or nothing
    closed through in that bounded span -- the wick-only drop then stands.
    """
    launch = False
    for other in reversed(ordered):
        if other.timestamp >= event.timestamp or other.provisional:
            continue
        if other.event is StructureEvent.BREAK_OF_STRUCTURE:
            if other.direction is event.direction:
                return None  # a prior continuation: not the leg's launch BOS
            continue
        if other.event is StructureEvent.CHANGE_OF_CHARACTER:
            launch = other.direction is event.direction
            break
        if other.event is StructureEvent.CHOCH_FAILED:
            # A failed CHoCH flips the trend back to the *opposite* of the
            # failed CHoCH's direction, starting a leg there.
            launch = other.direction is not event.direction
            break
    if not launch:
        return None

    limit_index = len(candles)
    successors = 0
    for other in ordered:
        if other.timestamp <= event.timestamp or other.provisional:
            continue
        dies = (
            other.event is StructureEvent.CHANGE_OF_CHARACTER
            and other.direction is not event.direction
        ) or (
            other.event is StructureEvent.CHOCH_FAILED and other.direction is event.direction
        )
        if not dies and not (
            other.event is StructureEvent.BREAK_OF_STRUCTURE
            and other.direction is event.direction
        ):
            continue
        if not dies:
            # The first same-direction continuation ends the primary window
            # (already `end_index`); the search may run through its window, so
            # only the *second* one bounds it.
            successors += 1
            if successors < 2:
                continue
        other_index = index_by_ts.get(other.timestamp)
        if other_index is not None:
            limit_index = other_index
        break

    for i in range(end_index + 1, limit_index):
        close = candles[i].close
        if (bearish and close < floor) or (not bearish and close > floor):
            return i
    return None


def _reanchor_bos_close_break(
    events: list[MarketStructure],
    candles: list[Candle],
    *,
    rescue_leg_launch: bool = False,
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

    With ``rescue_leg_launch``, a *leg-launch* BOS (the first of its leg, whose
    floor is the CHoCH-seeded launch level) that finds no close in its own
    window is given a bounded extended search -- through the next
    same-direction BOS's window -- instead of being dropped, suppressing the
    shallower continuations it passes over. See ``_RESCUE_LEG_LAUNCH_BOS`` and
    ``_leg_launch_rescue_index``.
    """
    if not events or not candles:
        return events

    index_by_ts = {candle.timestamp: i for i, candle in enumerate(candles)}
    ordered = sorted(events, key=lambda event: event.timestamp)
    last_index = len(candles) - 1
    result: list[MarketStructure] = []
    suppressed: set[int] = set()

    for event in ordered:
        if id(event) in suppressed:
            continue  # passed over by an earlier leg-launch rescue
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
            rescue_index = (
                _leg_launch_rescue_index(
                    event, ordered, candles, index_by_ts, end_index, floor, bearish=bearish
                )
                if rescue_leg_launch
                else None
            )
            if rescue_index is None:
                continue  # leg only wicked the formed level: not a confirmed BOS
            new_timestamp = candles[rescue_index].timestamp
            # The rescued launch BOS's close-break landed inside a successor's
            # window: the shallower same-direction continuations it passed over
            # are premature clutter next to the launch break (and their
            # confirming close would re-kill the rescued mark in
            # `_drop_pre_break_reference_bos`); suppress them.
            for other in ordered:
                if (
                    event.timestamp < other.timestamp <= new_timestamp
                    and other.event is StructureEvent.BREAK_OF_STRUCTURE
                    and not other.provisional
                    and other.direction is event.direction
                    and other.reference_price_level is not None
                    and (
                        other.reference_price_level > floor
                        if bearish
                        else other.reference_price_level < floor
                    )
                ):
                    suppressed.add(id(other))

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
        if reference_timestamp is None:
            # No exact same-side origin and the detector left no anchor -- the
            # first BOS of a leg reports the CHoCH-seeded floor, whose origin is
            # the reversal's *opposite*-polarity extreme (a bearish leg's floor is
            # the reversal top). Resolve it robustly so the line starts at the
            # level's real origin rather than nowhere (the ETH H4 first bearish
            # BOS at 1721.57, a high, drew from the chart edge).
            reference_timestamp = resolve_break_origin_timestamp(
                candles, start_index, floor, bearish=bearish
            )

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
    constraint for its direction; a (non-provisional) ``CHOCH_FAILED`` likewise
    starts a new leg in the *opposite* of its direction and resets that.
    Events without a resolved ``reference_timestamp`` are kept -- there is
    nothing to judge. Runs after ``_reanchor_bos_close_break`` so each BOS
    ``timestamp`` is its confirming close and ``reference_timestamp`` the candle
    that formed the level.
    """
    result: list[MarketStructure] = []
    last_bos_close: dict[MarketDirection, datetime] = {}
    # Two BOS can re-time to the same confirming candle (one close clearing two
    # levels at once); the one whose reference formed earlier is the earlier
    # structural break, so it must be judged (and set the leg's close) first.
    for event in sorted(events, key=lambda e: (e.timestamp, e.reference_timestamp or e.timestamp)):
        if event.event is StructureEvent.CHANGE_OF_CHARACTER:
            last_bos_close.pop(event.direction, None)
        elif event.event is StructureEvent.CHOCH_FAILED and not event.provisional:
            # A failed CHoCH flips the trend back to the *opposite* of the failed
            # CHoCH's direction, starting a new leg there whose first BOS
            # references the CHoCH-seeded level (formed before the flip) -- so it
            # resets that direction's constraint, mirroring the CHoCH reset. Only
            # a real flip counts; the provisional fizzle marker does not move the
            # trend, so it must not reset. Without this, the first BOS of the new
            # leg is dropped whenever the seeded level's origin happens to predate
            # the prior same-direction BOS (the AAVE H4 first bearish BOS at
            # 122.72, whose origin fell a few candles before the prior BOS close).
            flipped = (
                MarketDirection.BULLISH
                if event.direction is MarketDirection.BEARISH
                else MarketDirection.BEARISH
            )
            last_bos_close.pop(flipped, None)
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


def _drop_resumed_fizzle_markers(
    events: list[MarketStructure],
    candles: list[Candle],
) -> list[MarketStructure]:
    """Drop fizzle ``CHOCH_FAILED`` markers whose reversal later resumed on-chart.

    The fast-fizzle marker (a *provisional* ``CHOCH_FAILED``) flags a standing
    CHoCH whose broken level was reclaimed shortly after the flip, so the chart
    can terminate a stale line. But a reclaim the reversal *recovers* from is a
    deep pullback, not a fizzle. Two resumption proofs cancel the marker:

    - a *chart-surviving* same-direction BOS printing after the reclaim (the
      ETHUSDT H1 2026-06-30 case: price reclaimed the 1583 reference for a
      day, then printed a bullish BOS staircase to 1833 -- the CHoCH never
      fizzled). This pass runs after ``_reanchor_bos_close_break``, so a
      wick-only continuation dropped there (the SOL M15 motivating fizzle,
      whose only follow-up BOS never closed beyond its level) does not cancel
      the marker.
    - a candle *closing* beyond the marked CHoCH's own extreme
      (``price_level``, the triggering pivot's fundo/topo) after the marker:
      the leg made a new extreme, so the reversal plainly resumed even before
      its BOS confirms a pullback (the SOLUSDT M15 2026-07-16 case: a shallow
      6-close reclaim of 77.21 stamped the marker at the top of a bounce,
      then price crashed through the 76.64 CHoCH fundo within two hours --
      the ✕ sat next to a CHoCH that worked). Reclaim depth cannot make this
      call at emission time (the genuine June fizzle's reclaim was *shallower*
      than this false one, 0.98 vs 1.18 ATR); only the resumption can.

    The detector cannot make either call itself -- it does not know which of
    its BOS survive composition, and the marker is emitted from final state.
    No confirmed CHoCH can sit between the marker and later evidence (the
    fizzle only ever marks the *last* CHoCH of the stream), so any later
    same-direction BOS or new extreme belongs to the marked CHoCH's own cycle.
    """
    bos_times: dict[MarketDirection, list[datetime]] = {}
    for event in events:
        if event.event is StructureEvent.BREAK_OF_STRUCTURE and not event.provisional:
            bos_times.setdefault(event.direction, []).append(event.timestamp)

    def leg_resumed_past_choch_extreme(marker: MarketStructure) -> bool:
        standing = next(
            (
                e
                for e in reversed(events)
                if e.event is StructureEvent.CHANGE_OF_CHARACTER
                and e.direction is marker.direction
                and not e.provisional
                and e.timestamp <= marker.timestamp
            ),
            None,
        )
        if standing is None:
            return False
        bearish = marker.direction is MarketDirection.BEARISH
        return any(
            (candle.close < standing.price_level)
            if bearish
            else (candle.close > standing.price_level)
            for candle in candles
            if candle.timestamp > marker.timestamp
        )

    return [
        event
        for event in events
        if not (
            event.event is StructureEvent.CHOCH_FAILED
            and event.provisional
            and (
                any(t > event.timestamp for t in bos_times.get(event.direction, []))
                or leg_resumed_past_choch_extreme(event)
            )
        )
    ]


def _drop_failed_refire_cycles(events: list[MarketStructure]) -> list[MarketStructure]:
    """Drop a re-fired CHoCH that itself failed, together with its failure mark.

    Under ``choch_failed_rearm`` a ``CHOCH_FAILED`` can re-fire its CHoCH; the
    re-arm pivot carries the failure's own timestamp, so a re-fired CHoCH is
    identified by a prior same-direction real ``CHOCH_FAILED`` sitting exactly
    at its ``reference_timestamp`` (the same match the frontend keys its ``↻``
    suffix on) — or, since a CHoCH can re-attempt the same level through a
    *structural* reference rather than the re-arm memory (the ENAUSDT 4H
    0.07463 cluster, where the pending leg origin and the armed failure level
    are one pivot), by a prior same-direction failure at the exact same
    ``reference_price_level``. When the re-fire then dies too -- a later same-direction real
    ``CHOCH_FAILED`` with no intervening same-direction CHoCH -- the cycle
    added no standing structure: the level's story is already told by the
    original failure, and drawing the pair stacks three or four marks on one
    line (the MUUSDT H4 962.15 cluster: ✕ → ↻ → ✕ while the crash resumed).
    Drop both. Trend-replay safe: the pair flips the trend away and back, so
    every replay reading after it is unchanged. A re-fire that
    *survived* -- confirmed, still standing, or merely fizzle-marked (the
    additive marker never flips the trend, so hiding the CHoCH would desync
    the chart from ``final_trend``) -- is kept: it earned its ``↻``. So is a
    re-fire that failed only *after* a chart-surviving same-direction BOS:
    its leg broke structure, so the pair is real history (reversal confirmed,
    then ended), not a dead re-attempt (the ENAUSDT H1 2026-07-12 re-fire at
    0.08104 that dropped to a 0.0776 BOS before the V-recovery failed it).

    Under ``choch_failed_rearm_persistent`` the chain is no longer one-shot:
    a later surviving re-fire can reference a failure this pass just dropped
    (the BTCUSDT D1 2025-10 re-fire referencing the collapsed September
    cycle's failure). Its ``reference_timestamp`` is re-anchored to the
    nearest earlier *surviving* same-direction real failure, so the ``↻``
    suffix still matches and the line starts at the visible ``✕`` instead of
    at a dropped, invisible mark.
    """
    real_failures = [
        e for e in events if e.event is StructureEvent.CHOCH_FAILED and not e.provisional
    ]
    if not real_failures:
        return events

    dropped: set[int] = set()
    for i, event in enumerate(events):
        if (
            event.event is not StructureEvent.CHANGE_OF_CHARACTER
            or event.provisional
        ):
            continue
        # A re-fire carries the failure's timestamp as its reference anchor.
        # A CHoCH can also re-attempt the *same level* through a structural
        # reference instead of the re-arm memory (the ENAUSDT 4H 0.07463
        # cluster: the pending leg origin and the armed failure level are the
        # same pivot), so a prior same-direction failure at the exact same
        # reference level identifies the cycle too — different identity, same
        # story, same one-line ✕ → CHoCH → ✕ stack when it dies again.
        is_refire = any(
            f.direction is event.direction
            and f.timestamp <= event.timestamp
            and (
                f.timestamp == event.reference_timestamp
                or (
                    event.reference_price_level is not None
                    and f.reference_price_level == event.reference_price_level
                )
            )
            for f in real_failures
        )
        if not is_refire:
            continue
        own_failure_index = next(
            (
                j
                for j, other in enumerate(events)
                if j not in dropped
                and other.event is StructureEvent.CHOCH_FAILED
                and not other.provisional
                and other.direction is event.direction
                and other.timestamp > event.timestamp
                and not any(
                    mid.event is StructureEvent.CHANGE_OF_CHARACTER
                    and not mid.provisional
                    and mid.direction is event.direction
                    and event.timestamp < mid.timestamp <= other.timestamp
                    for mid in events
                    if mid is not event and mid is not other
                )
            ),
            None,
        )
        if own_failure_index is not None:
            # A re-fire whose leg *broke structure* -- a chart-surviving
            # same-direction BOS between the re-fire and its failure -- did
            # add standing structure: it demonstrably worked, and the later
            # failure marks where the confirmed move ended, not a dead
            # re-attempt. Collapsing the pair would erase a CHoCH that broke
            # structure and leave its BOS orphaned (the ENAUSDT H1 2026-07-12
            # re-fire at 0.08104: dropped to 0.0776 with a BOS, then the
            # V-recovery stamped the failure). Keep the whole cycle.
            failure_ts = events[own_failure_index].timestamp
            refire_worked = any(
                mid.event is StructureEvent.BREAK_OF_STRUCTURE
                and not mid.provisional
                and mid.direction is event.direction
                and event.timestamp < mid.timestamp <= failure_ts
                for mid in events
            )
            if refire_worked:
                continue
            dropped.add(i)
            dropped.add(own_failure_index)
    if not dropped:
        return events
    kept = [e for i, e in enumerate(events) if i not in dropped]
    dropped_failure_keys = {
        (events[j].direction, events[j].timestamp)
        for j in dropped
        if events[j].event is StructureEvent.CHOCH_FAILED
    }
    for i, event in enumerate(kept):
        if (
            event.event is not StructureEvent.CHANGE_OF_CHARACTER
            or event.provisional
            or event.reference_timestamp is None
            or (event.direction, event.reference_timestamp) not in dropped_failure_keys
        ):
            continue
        anchor = max(
            (
                f.timestamp
                for f in kept
                if f.event is StructureEvent.CHOCH_FAILED
                and not f.provisional
                and f.direction is event.direction
                and f.timestamp <= event.reference_timestamp
            ),
            default=None,
        )
        if anchor is not None:
            kept[i] = event.model_copy(update={"reference_timestamp": anchor})
    return kept


def _drop_superseded_provisional_choch(
    events: list[MarketStructure],
) -> list[MarketStructure]:
    """Drop provisional ``CHANGE_OF_CHARACTER`` marks that real structure superseded.

    A provisional CHoCH -- a live-edge forming reversal (``emit_provisional_choch``)
    or a range-breakout reversal staged against the segment trend
    (``stage_breakout_events``) -- carries a dimmed ``CHoCH?`` on the chart and is
    skipped by trend replay. The ``?`` promises a *forming* mark: superseded by the
    confirmed event once the pivots form, or gone if the move fails. A staged
    reversal, though, is fire-and-forget -- it never resolves, so a ``CHoCH?`` whose
    fate is already settled lingers on the chart forever (the ETHBTC H4 2026-06-01
    case: a bullish ``CHoCH?`` the market invalidated four candles later with a real
    bearish BOS through the range floor; and the 2026-06-16 case: superseded by the
    real bullish CHoCH that finally flipped the trend on 2026-07-02). Any later
    *non-provisional* BOS/CHoCH means the state machine has spoken again -- the
    reversal either failed (an opposite advance) or was confirmed by real structure
    (a same-direction advance) -- so the provisional mark has served its purpose and
    is dropped. A genuine live-edge forming mark has no later real advance and
    survives, honoring the ``?``. Provisional never affects replay, so ``final_trend``
    (computed upstream from the detector) is unchanged; this only cleans the chart.
    """
    real_advance_times = [
        event.timestamp
        for event in events
        if not event.provisional
        and event.event
        in (StructureEvent.BREAK_OF_STRUCTURE, StructureEvent.CHANGE_OF_CHARACTER)
    ]
    return [
        event
        for event in events
        if not (
            event.event is StructureEvent.CHANGE_OF_CHARACTER
            and event.provisional
            and any(t > event.timestamp for t in real_advance_times)
        )
    ]


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
        # Displacement release: when the leg has stretched N x ATR away from
        # its reversal reference, the cycle is spent -- shrink the staleness
        # timer so the reference re-anchors to the post-move range's first
        # pullback instead of pinning the old cycle for the full window (the
        # ETH H4 month-long stuck BOS). See _STALE_REANCHOR_DISPLACEMENT_ATR.
        stale_reanchor_displacement_atr=_STALE_REANCHOR_DISPLACEMENT_ATR,
        stale_reanchor_displacement_candles=_STALE_REANCHOR_DISPLACEMENT_CANDLES,
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
        # Confirmed-trend barrier: once an emitted BOS confirms the standing
        # trend, a reverse CHoCH must sustain this many closes (hysteresis --
        # a confirmed structure is harder to invalidate than a pending one).
        # See _CHOCH_CONFIRMED_TREND_PERSISTENCE.
        choch_confirmed_trend_persistence_candles=_CHOCH_CONFIRMED_TREND_PERSISTENCE,
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
        # ... including against weak (re-anchored) references, which are the
        # standing reference in every released/reset cycle -- without this the
        # forming reversal after a displacement release is invisible. See
        # _EMIT_PROVISIONAL_CHOCH_WEAK.
        emit_provisional_choch_weak=_EMIT_PROVISIONAL_CHOCH_WEAK,
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
        # Failed-CHoCH re-activation: when price later sustains back beyond the
        # failed CHoCH's broken level, re-fire the CHoCH instead of leaving the
        # resumed move to print as a chain of sweeps under the wrong trend (the
        # MUUSDT H4 stuck-bullish crash). See _CHOCH_FAILED_REARM.
        choch_failed_rearm=_CHOCH_FAILED_REARM,
        choch_failed_rearm_persistent=_CHOCH_FAILED_REARM_PERSISTENT,
        # Emit a long-since-confirmed CHOCH_FAILED at the live edge instead of
        # waiting for a swing pivot that a relentless one-way move never forms
        # (the same crash's trend stayed bullish for days otherwise). See
        # _CHOCH_FAIL_LIVE_EDGE.
        choch_fail_live_edge=_CHOCH_FAIL_LIVE_EDGE,
        # Retro-stage the continuation BOS a failed CHoCH's window ate (they
        # printed as sweeps while the trend was wrongly flipped), so the
        # resumed leg shows its staircase. See _STAGE_CHOCH_FAILED_WINDOW_BOS.
        stage_choch_failed_window_bos=_STAGE_CHOCH_FAILED_WINDOW_BOS,
        # Retire a CHoCH origin once its reversal leg has displaced this many
        # ATR% beyond the fail level, so an impulsive move that emitted no
        # confirming BOS is not marked a false CHOCH_FAILED on its pullback.
        # See _CHOCH_SUCCESS_DISPLACEMENT_ATR.
        choch_success_displacement_atr=_CHOCH_SUCCESS_DISPLACEMENT_ATR,
        # Cap the ATR-derived displacement threshold at a fraction of price, so
        # a volatile daily (mean TR ~10%) does not demand an unreachable 40%+
        # move to credit a successful reversal. See
        # _CHOCH_SUCCESS_DISPLACEMENT_MAX_PCT.
        choch_success_displacement_max_pct=_CHOCH_SUCCESS_DISPLACEMENT_MAX_PCT,
        # Additively mark the last continuation BOS an impulsive move made right
        # before it reversed: when the floor already closed-broke but the
        # reversal CHoCH arrived before a confirming pullback, the pending BOS is
        # discarded without emitting. Stage it (deduped, re-timed to the close-
        # break) so the leg's final lower low -- the close that "permits" the
        # reversal -- is not invisible. See _STAGE_REVERSAL_EATEN_BOS.
        stage_reversal_eaten_bos=_STAGE_REVERSAL_EATEN_BOS,
        # Sibling of the above: a pending BOS the *next advance* replaces (an
        # impulsive run of same-side pivots with no confirming pullback between)
        # is staged too, so each top/bottom that formed and broke keeps a mark
        # instead of only the run's last one. See
        # _STAGE_SUPERSEDED_CONTINUATION_BOS.
        stage_superseded_continuation_bos=_STAGE_SUPERSEDED_CONTINUATION_BOS,
        bos_pullback_seed_choch_origin=_BOS_PULLBACK_SEED_CHOCH_ORIGIN,
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
        # A weak-referenced CHoCH also fails on a sustained close back through
        # the level it broke (its only reversal evidence), not just the far
        # leg origin -- the BTC D1 -30% crash with the trend stuck bullish.
        # See _CHOCH_WEAK_REF_FAIL_AT_BROKEN_LEVEL.
        choch_weak_ref_fail_at_broken_level=_CHOCH_WEAK_REF_FAIL_AT_BROKEN_LEVEL,
        # Pending half of the hysteresis: an unconfirmed CHoCH (no emitted BOS
        # yet) dies on a sustained reclaim of its broken level even when the
        # reference was structural, at the stronger pending-fail persistence
        # (the AAVE H1 07-08 stale-trend case). See
        # _CHOCH_PENDING_FAIL_AT_BROKEN_LEVEL.
        choch_pending_fail_at_broken_level=_CHOCH_PENDING_FAIL_AT_BROKEN_LEVEL,
        choch_pending_fail_persistence_candles=_CHOCH_PENDING_FAIL_PERSISTENCE,
        # A pending BOS discarded without emitting -- a phantom advance whose
        # confirming pullback came in too deep (below the prior BOS's confirming
        # pullback but still above the leg origin, so it neither emits nor
        # reverses) -- rolls the staircase gate back to its pre-advance value
        # instead of leaving it pinned at that advance's pivot wick. Without this
        # a single failed push (a long upper wick to a new high that closed lower)
        # freezes the staircase at the wick top, so a later genuine continuation
        # to a slightly lower high can never advance and the chart sits on a stale
        # BOS while price makes new structure (the ETH M30 case: a 07-06 wick to
        # 1833 that closed at 1812 pinned the staircase at 1833, so the 07-11 rally
        # topping at 1829 printed no BOS -- the last one hung from 07-04).
        # See rollback_staircase_on_discard.
        rollback_staircase_on_discard=True,
    )


@dataclass(frozen=True)
class InternalStructureRun:
    """One timeframe's production internal-structure run.

    The buffered fetch, structural-anchor slice, detector wiring
    (`_build_internal_detector`), and composition passes for a single
    symbol/timeframe -- the shared unit behind `load_dashboard_data` (current
    timeframe and higher-timeframe trend) and `app.overview.load_timeframe_
    structure`, so every consumer reads exactly the structure the chart
    renders for that timeframe.
    """

    buffered_candles: list[Candle]
    # The visible window (the trailing `limit` of `buffered_candles`).
    candles: list[Candle]
    # The structurally anchored detection slice (see `_structural_anchor_index`).
    internal_candles: list[Candle]
    # Visible-window events, after the composition passes
    # (`_reanchor_bos_close_break` + `_drop_pre_break_reference_bos`).
    events: list[MarketStructure]
    # The detector's state-machine trend (`final_trend`) at the series end.
    trend: MarketDirection
    # Confirmed consolidation ranges overlapping the visible window (see
    # `_detect_consolidations`).
    consolidation_ranges: list[ConsolidationRange]


def _run_internal_structure(
    provider: OHLCVProvider,
    symbol: str,
    timeframe: TimeFrame,
    limit: int,
    confluence_filter: bool,
) -> InternalStructureRun:
    """Fetch and run the production internal-structure pipeline for one timeframe.

    Fetches the buffered series once and derives the visible window from its
    tail. `buffered_candles` prepends `_INTERNAL_STRUCTURE_BOOTSTRAP_BUFFER`
    candles of history before the visible window (for the internal detector's
    warm-up and the structural anchor); the visible `candles` are just its
    last `limit`, so a separate fetch would be redundant -- and a second call
    could even race a freshly-printed candle, desyncing the two series.
    """
    buffered_limit = min(limit + _INTERNAL_STRUCTURE_BOOTSTRAP_BUFFER, provider.max_fetch_limit)
    buffered_candles = provider.get_ohlcv(symbol, timeframe, buffered_limit)
    candles = buffered_candles[-limit:]
    visible_start = candles[0].timestamp
    visible_end = candles[-1].timestamp

    # The internal detector starts at a structural anchor (the most recent major
    # extreme before the visible window) rather than a fixed candle offset, so
    # the trend it bootstraps reflects the move actually entering the window
    # instead of a stale, far-back regime. See `_structural_anchor_index`.
    internal_candles = buffered_candles[_structural_anchor_index(buffered_candles, visible_start) :]

    detector = _build_internal_detector(timeframe, confluence_filter=confluence_filter)

    def run_passes(range_resets: list[RangeReset]) -> list[MarketStructure]:
        events = detector.detect(internal_candles, range_resets=range_resets)
        # Re-time each BOS to the first close beyond the formed level it broke
        # (dropping wick-only continuations), before the visible filter.
        events = _reanchor_bos_close_break(
            events, internal_candles, rescue_leg_launch=_RESCUE_LEG_LAUNCH_BOS
        )
        # A reference may only form *after* the prior same-direction BOS broke:
        # a continuation referencing a pre-break wick attempt at the prior level
        # is dropped (pre-break liquidity, not structure of the new leg).
        events = _drop_pre_break_reference_bos(events)
        # A fizzle marker followed by a surviving same-direction BOS -- or by a
        # close beyond the marked CHoCH's own extreme -- was a deep pullback
        # the reversal recovered from, not a fizzle. Runs after the BOS passes
        # so only chart-surviving BOS count.
        events = _drop_resumed_fizzle_markers(events, internal_candles)
        # A re-fired CHoCH that itself failed added no standing structure: the
        # level's story is already told by the original failure, so drop the
        # pair (re-fire + its own failure). Runs after the fizzle pass so a
        # *resumed* re-fire (its fizzle dropped above) is never collapsed.
        return _drop_failed_refire_cycles(events)

    all_events = run_passes([])
    # Consolidation post-pass over the *surviving* stream: segment boundaries
    # match the events the chart draws.
    all_ranges, range_resets = _detect_consolidations(all_events, internal_candles)

    if _CONSOLIDATION_RANGE_RESET_CYCLE:
        # Phase 3: re-seed the machine's structural references at the *live*
        # range's box boundaries and replay, so when that range breaks out the
        # machine emits a real BOS/CHoCH instead of staying pinned to a
        # pre-range level. Scoped to the single ACTIVE range (the one still
        # open at the edge -- the one that looks stuck now): resolved historical
        # ranges keep their additive phase-2 staged marks below, since
        # re-seeding settled structure cascades downstream (the blanket
        # re-seed rewrote months of history and flipped ETH 4H's July
        # conclusion -- see docs/structure_decisions.md). An active range has
        # no breakout yet, so its re-seed only arms the references and repaints
        # the live-edge provisional -- a bounded, tail-only effect. Re-detect
        # the boxes against the replayed stream so the drawn ranges stay
        # consistent (the re-detection's own resets are discarded -- a single
        # re-seed pass, not a fixpoint).
        scoped_resets = _scope_resets_to_live_range(
            range_resets, all_ranges, internal_candles
        )
        if scoped_resets:
            all_events = run_passes(scoped_resets)
            all_ranges, _ = _detect_consolidations(all_events, internal_candles)
    consolidation_ranges = [
        r for r in all_ranges if (r.end_timestamp or visible_end) >= visible_start
    ]
    if _CONSOLIDATION_STAGE_BREAKOUT_EVENTS:
        # Phase 2: stage additive structure events at *resolved* range breakouts
        # (a BOS with the trend / a provisional CHoCH against it, referencing
        # the broken boundary), deduped against the real events. Merged sorted
        # by timestamp, the same contract the detector's staged-BOS merge keeps.
        # Runs in both modes: phase 3 re-seeds only the live (unresolved) range,
        # which stages nothing here; once it breaks out the re-seeded machine
        # emits the real event and this staging's dedup drops the duplicate.
        staged = stage_breakout_events(
            internal_candles,
            all_ranges,
            _advance_boundaries(all_events, internal_candles),
            all_events,
            dedup_candles=_CONSOLIDATION_STAGE_DEDUP_CANDLES,
        )
        if staged:
            all_events = sorted([*all_events, *staged], key=lambda e: e.timestamp)
    # A provisional CHoCH? (staged range-breakout reversal, or a live-edge forming
    # mark) is meant to resolve -- confirm or vanish. Drop any whose fate real
    # structure already settled (a later non-provisional BOS/CHoCH), so a stale
    # `CHoCH?` never lingers in history; only live-edge marks survive.
    all_events = _drop_superseded_provisional_choch(all_events)
    events = [e for e in all_events if visible_start <= e.timestamp <= visible_end]
    return InternalStructureRun(
        buffered_candles=buffered_candles,
        candles=candles,
        internal_candles=internal_candles,
        events=events,
        trend=detector.final_trend,
        consolidation_ranges=consolidation_ranges,
    )


def _advance_boundaries(
    events: list[MarketStructure], candles: list[Candle]
) -> list[tuple[int, MarketDirection]]:
    """Structure-advance candle indices + the trend each advance established.

    Advances are the non-provisional BOS/CHoCH/`CHOCH_FAILED` that survived
    the composition passes (what the chart draws); a `CHOCH_FAILED`'s
    `direction` is the *failed* CHoCH's, so the trend it reverts to is the
    opposite. Consumed by the consolidation post-pass (segment boundaries a
    range may never span) and its breakout staging (the standing trend a
    breakout is classified against).
    """
    index_by_timestamp = {candle.timestamp: index for index, candle in enumerate(candles)}
    advances: list[tuple[int, MarketDirection]] = []
    for event in events:
        if event.provisional or event.event not in (
            StructureEvent.BREAK_OF_STRUCTURE,
            StructureEvent.CHANGE_OF_CHARACTER,
            StructureEvent.CHOCH_FAILED,
        ):
            continue
        index = index_by_timestamp.get(event.timestamp)
        if index is None:
            continue
        direction = event.direction
        if event.event is StructureEvent.CHOCH_FAILED:
            direction = (
                MarketDirection.BEARISH
                if direction is MarketDirection.BULLISH
                else MarketDirection.BULLISH
            )
        advances.append((index, direction))
    return advances


def _scope_resets_to_live_range(
    resets: list[RangeReset],
    ranges: list[ConsolidationRange],
    candles: list[Candle],
) -> list[RangeReset]:
    """Keep only the re-seed directives of the live (ACTIVE) range.

    At most one range is ever ACTIVE -- the last quiet segment, still open at
    the series edge (the range that looks stuck *now*, the pathology phase 3
    targets). Resolved ranges are earlier in the series and keep their additive
    phase-2 staged marks; re-seeding them cascades through the settled
    structure downstream (the blanket re-seed rewrote months of history and
    flipped ETH 4H's July conclusion). Because ranges are non-overlapping
    segments, every reset with a candle index at or after the active range's
    start belongs to it, and no resolved range's resets do.
    """
    active = next(
        (r for r in ranges if r.status is ConsolidationStatus.ACTIVE), None
    )
    if active is None:
        return []
    index_by_timestamp = {candle.timestamp: index for index, candle in enumerate(candles)}
    start_index = index_by_timestamp.get(active.start_timestamp)
    if start_index is None:
        return []
    return [reset for reset in resets if reset.candle_index >= start_index]


def _detect_consolidations(
    events: list[MarketStructure], candles: list[Candle]
) -> tuple[list[ConsolidationRange], list[RangeReset]]:
    """Run consolidation detection over the surviving internal event stream.

    Segment boundaries come from `_advance_boundaries`. The height cap is
    volatility-normalized against the detection series' mean true-range%,
    the same normalization the detector's displacement features use. Returns
    the confirmed ranges (for the chart boxes / ladder chip) and the
    `RangeReset` directives (empty unless a range confirms), replayed into the
    second detector pass under `_CONSOLIDATION_RANGE_RESET_CYCLE`.
    """
    if len(candles) < 2:
        return [], []
    advances = _advance_boundaries(events, candles)
    mean_tr_pct = fmean(
        max(
            curr.high - curr.low,
            abs(curr.high - prev.close),
            abs(curr.low - prev.close),
        )
        / curr.close
        for prev, curr in zip(candles, candles[1:], strict=False)
    )
    if mean_tr_pct <= 0:
        return [], []
    return detect_consolidation_ranges_with_resets(
        candles,
        advances,
        min_candles=_CONSOLIDATION_MIN_CANDLES,
        max_height_pct=_CONSOLIDATION_MAX_HEIGHT_ATR * mean_tr_pct,
        resolve_persistence=_CONSOLIDATION_RESOLVE_PERSISTENCE,
    )


def default_ohlcv_provider() -> OHLCVProvider:
    """The production candle source.

    Perpetual-futures candles (aligned with the futures-derived
    liquidation/OI/funding analysis, and a 1500-candle per-request window vs
    spot's 1000), falling back to spot for symbols without a perpetual.
    """
    return FallbackOHLCVProvider(BinanceFuturesOHLCVProvider(), BinanceDataProvider())


def load_dashboard_data(
    provider: OHLCVProvider | None = None,
    symbol: str = "BTCUSDT",
    timeframe: TimeFrame = TimeFrame.H1,
    limit: int = 1200,
    swing_lookback: int = DEFAULT_SWING_LOOKBACK,
    confluence_filter: bool = False,
    futures_provider: FuturesDataProvider | None = None,
    compute_narrative: bool = True,
) -> DashboardData:
    """Fetch candles and assemble liquidity, ranking, and retail bias data.

    ``compute_narrative=False`` skips the `NarrativeEngine` synthesis entirely
    (``narrative=None`` in the snapshot) -- a lighter profile for consumers
    that do not render the narrative/anomaly panel.
    """
    if provider is None:
        provider = default_ohlcv_provider()
    if futures_provider is None:
        futures_provider = BinanceFuturesDataProvider()

    htf = _HIGHER_TIMEFRAME_MAP.get(timeframe)

    # The cold load is dominated by sequential network round-trips: current-TF
    # klines, HTF klines, then the futures state (OI/funding/long-short). The
    # HTF run and the futures fetch depend on nothing computed below, so both
    # start on background threads here and are joined where their results are
    # consumed, overlapping their latency with the current-TF run and the
    # detector compute.
    futures_state_future = _PREFETCH_POOL.submit(
        _fetch_futures_state,
        futures_provider,
        symbol=symbol,
        timeframe=timeframe,
        oi_limit=limit,
    )
    htf_run_future = (
        _PREFETCH_POOL.submit(
            _run_internal_structure, provider, symbol, htf, limit, confluence_filter
        )
        if htf is not None
        else None
    )

    internal_run = _run_internal_structure(
        provider, symbol, timeframe, limit, confluence_filter
    )
    buffered_candles = internal_run.buffered_candles
    candles = internal_run.candles

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
    all_major_events = _reanchor_bos_close_break(
        all_major_events, buffered_candles, rescue_leg_launch=_RESCUE_LEG_LAUNCH_BOS
    )
    all_major_events = _drop_pre_break_reference_bos(all_major_events)
    market_structure_events = [
        e for e in all_major_events if visible_start <= e.timestamp <= visible_end
    ]

    # The internal run (fetch, structural anchor, detection, composition
    # passes) happened up front in `_run_internal_structure`.
    internal_structure_events = internal_run.events

    # The MSB order block detector is self-contained (it derives its own swing
    # pivots); it runs on the same structurally anchored slice as the internal
    # detector so zones anchored just before the visible window still render.
    all_poi_zones = POIDetector().detect(internal_run.internal_candles)
    poi_zones = [z for z in all_poi_zones if visible_start <= z.created_at <= visible_end]

    if htf_run_future is not None:
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
        higher_timeframe_direction = htf_run_future.result().trend
    else:
        # Top timeframe (no higher TF): degrade to the current series' own
        # internal trend, so downstream comparisons (the liquidity hunt's
        # counter-trend check) read "aligned" rather than pitting two
        # different methodologies against each other.
        higher_timeframe_direction = internal_run.trend

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
    # so structure events across the chart can be OI-qualified. The fetch was
    # started on a background thread at the top of this function.
    futures_state = futures_state_future.result()
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
        consolidation_ranges=internal_run.consolidation_ranges,
    )

    from liquidity_hunter.app.liquidity_hunt import LiquidityHuntEngine
    from liquidity_hunter.app.narrative import NarrativeEngine

    # Both synthesizers read the fully assembled snapshot (they cross-reference
    # outputs from every layer), so they run last, at the composition point.
    narrative = NarrativeEngine().build(data) if compute_narrative else None
    liquidity_hunt = LiquidityHuntEngine(proximity_atr=_HUNT_PROXIMITY_ATR).build(data)
    return replace(data, narrative=narrative, liquidity_hunt=liquidity_hunt)


# Shared pool for the independent network-bound units of a dashboard load
# (the HTF structure run and the futures-state fetch). Module-level so worker
# threads are reused across requests; sized for a couple of concurrent
# snapshot loads (each uses at most two workers). The ccxt sync clients are
# only issuing stateless public GETs here, which requests handles fine across
# threads.
_PREFETCH_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="dashboard-prefetch")


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
        # The three endpoints are independent; the paginated OI history is the
        # slow one, so funding and long/short ride alongside it.
        with ThreadPoolExecutor(max_workers=3) as pool:
            oi_future = pool.submit(
                futures_provider.get_open_interest_history, symbol, timeframe, limit=oi_limit
            )
            funding_future = pool.submit(futures_provider.get_funding_rate_history, symbol)
            long_short_future = pool.submit(
                futures_provider.get_long_short_ratio, symbol, timeframe
            )
            open_interest = oi_future.result()
            funding = funding_future.result()
            long_short = long_short_future.result()
    except DataProviderError:
        logger.warning(
            "Futures data unavailable for %s; skipping liquidation map and OI analysis", symbol
        )
        return None
    return open_interest, funding, long_short
