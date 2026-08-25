"""VWAP reclaim after a liquidity test — is the pinbar entry worth anything?

The setup under test
--------------------
Described from the buy side (the sell side mirrors it exactly):

1. Price trades **below the session VWAP** and tests a resting-liquidity
   source there — either it trades back into an **order block** (arm ``ob``)
   or it pierces an **equal-lows** pool (arm ``eql``).
2. Within a few candles it comes back up to the VWAP and prints a **pinbar
   reclaim**: the candle's wick pierces the VWAP from below and the close
   lands above it.
3. Entry is that pinbar's **close**; the stop is the **lowest low between the
   liquidity test and the entry** — the bottom of where price tested.

The thesis is that the two levels hold two different populations. Whoever
bought the order block is positioned; whoever entered since the anchor has
the VWAP as break-even. Price reclaiming the VWAP is the moment the second
population stops being underwater, and therefore stops being supply. The
entry is where both sources of offer are spent at once.

What is measured
----------------
Per arm, forward from the entry candle's close:

* ``a favor``  — the close at horizon H is beyond the entry in the trade's
  own direction. A plain direction hit rate.
* ``MFE/MAE``  — the maximum favourable / adverse excursion within H,
  both in **R** (R = entry → stop, the risk the rule itself defines). The
  ratio is scale-free: it only rises if one side opened further than the
  other, so two tails widening together reads as the volatility it is.
* ``hit 2R`` / ``hit 3R`` — the multiple was reached before −1R was.

Every arm carries its own **direction-matched** random control drawn from the
same series with the same R (``rand-ob`` / ``rand-eql``). A control that is
not matched on direction measures the period's drift rather than the setup:
in a window that trended, everything looks predictive.

No lookahead
------------
Each ingredient is used only from the candle it becomes knowable on:

* an order block from ``created_at`` (the MSB confirmation candle) and only
  while it has not been invalidated yet;
* an equal-lows pool from ``formed_at`` plus ``--eql-lag`` candles, since the
  pool's last touch is a swing pivot and a pivot needs its lookback to
  confirm;
* the VWAP is a running accumulation, causal by construction.

Usage
-----
    poetry run python research/vwap_ob_pinbar.py
    poetry run python research/vwap_ob_pinbar.py --symbols BTCUSDT ETHUSDT \
        --timeframes 5m 15m --horizons 5 10 20 40 --wick-frac 0.5
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from liquidity_hunter.app import load_dashboard_data
from liquidity_hunter.app.block_reclaim import (
    MAX_BODY_FRACTION,
    MAX_WAIT_CANDLES,
    MERGE_GAP_CANDLES,
    MIN_WICK_FRACTION,
    _visits,
    detect_block_reclaims,
)
from liquidity_hunter.app.dashboard_data import (
    _run_internal_structure,
    default_ohlcv_provider,
)
from liquidity_hunter.core.domain import Candle, TimeFrame, VWAPSeries
from liquidity_hunter.core.domain.enums import (
    LiquidityZoneType,
    MarketControlSide,
    MarketDirection,
    OIRegime,
    POIZoneKind,
    StructureEvent,
    VWAPAnchor,
)
from liquidity_hunter.data.exceptions import DataProviderError
from liquidity_hunter.data.providers.base import FuturesDataProvider, OHLCVProvider
from liquidity_hunter.indicators import volume_delta
from liquidity_hunter.indicators import vwap as compute_vwap
from liquidity_hunter.indicators.ema import ema_series
from pydantic import ValidationError
from research._paginated import NoFuturesProvider, PaginatedFuturesProvider
from research._replay import scan_first_emissions

BULL = MarketDirection.BULLISH
BEAR = MarketDirection.BEARISH

# The equal-level detector's production swing lookback: a pool's last touch is
# a pivot, and a pivot is only knowable this many candles after it printed.
EQL_CONFIRM_LAG = 5

#: Minutes per timeframe, to turn an HTF candle's open into its close.
_TF_MINUTES: dict[TimeFrame, int] = {
    TimeFrame.M1: 1, TimeFrame.M5: 5, TimeFrame.M15: 15, TimeFrame.M30: 30,
    TimeFrame.H1: 60, TimeFrame.H4: 240, TimeFrame.D1: 1440, TimeFrame.W1: 10080,
}


def htf_trend_steps(
    symbol: str, htf: TimeFrame, *, limit: int, warmup: int
) -> list[tuple[datetime, MarketDirection]]:
    """The higher timeframe's standing trend, dated by when it became knowable.

    A `MarketStructure` carries the timestamp of the candle that broke the
    level, but the pipeline only emits it once the confirming pivot has formed
    -- a median of 13.5 candles later for a BOS. Filtering by the trend as of
    the event's own timestamp would therefore use a reading that did not exist
    yet. `research/_replay` runs the production pipeline over growing prefixes
    and returns the first prefix each mark appears in; the step is dated at the
    **close** of that candle, which is the first moment a reader could act.
    """
    run = _run_internal_structure(default_ohlcv_provider(), symbol, htf, limit, False)
    buffered = run.buffered_candles
    first_seen = scan_first_emissions(
        buffered, symbol=symbol, timeframe=htf, limit=limit, warmup=warmup
    )
    span = timedelta(minutes=_TF_MINUTES[htf])
    by_cut: dict[int, tuple[datetime, MarketDirection]] = {}
    for (timestamp, _event, direction), cut in first_seen.items():
        # several marks can surface on the same prefix; the latest break wins
        prev = by_cut.get(cut)
        if prev is None or timestamp > prev[0]:
            by_cut[cut] = (timestamp, direction)
    return [
        (buffered[cut].timestamp + span, direction)
        for cut, (_ts, direction) in sorted(by_cut.items())
    ]


def htf_trend_at(
    steps: Sequence[tuple[datetime, MarketDirection]], when: datetime
) -> MarketDirection | None:
    """The HTF trend in force at `when`, or None while none is knowable yet."""
    i = bisect.bisect_right([s[0] for s in steps], when) - 1
    return steps[i][1] if i >= 0 else None


@dataclass
class Ev:
    arm: str
    symbol: str
    timeframe: str
    direction: MarketDirection
    trigger_index: int
    entry_index: int
    entry: float
    stop: float
    r: float
    favor: dict[int, bool] = field(default_factory=dict)
    mfe: dict[int, float] = field(default_factory=dict)
    mae: dict[int, float] = field(default_factory=dict)
    hit: dict[float, bool] = field(default_factory=dict)
    #: Control state at the entry candle; None where OI does not reach.
    ctrl_aligned: bool | None = None
    ctrl_unwind: bool | None = None
    #: `control_score` signed the trade's way, and whether its magnitude sits
    #: in the window's top quartile of conviction.
    ctrl_with: bool | None = None
    ctrl_strong: bool | None = None
    #: The entry candle's OWN taker delta opposing the trade. No OI, no window
    #: -- pure candle anatomy, and the confound the control slice is suspected
    #: of re-measuring.
    delta_against: bool | None = None
    #: The two axes `control_score` crosses, taken apart: which side was
    #: aggressing (`agg_bull`) and whether open interest was rising (`oi_up`).
    #: Both None on a FLAT candle, where neither axis cleared its floor.
    agg_bull: bool | None = None
    oi_up: bool | None = None
    #: The aggression axis recomputed from candles alone -- the same
    #: `sum(volume_delta) / sum(volume)` over the same per-timeframe window
    #: `MarketControlAnalyzer` uses, signed so positive means buy aggression.
    #: Free of open interest, so it is defined over the whole deep window
    #: rather than only inside Binance's ~30-day OI retention.
    agg_ratio: float | None = None
    #: The R-multiple this trade actually returned: -1 stopped, +2 target, or
    #: marked to market at the horizon. The payoff a return series needs.
    r_outcome: float | None = None
    #: The same payoff at each target of the grid: the exit rule is the trader's
    #: choice ("2x, 2.5x or 3x"), so an edge that exists at one multiple and
    #: not the neighbours is a calibration, not a finding.
    r_grid: dict[float, float] = field(default_factory=dict)
    #: Payoff in R under each position-management variant, all aiming at 2R.
    r_manage: dict[str, float] = field(default_factory=dict)
    #: Which shared line the pinbar actually rejected: "vwap", "ema" or
    #: "both". Only the `ob-either` arm sets it -- the decomposition that says
    #: whether the second line contributes trades or only re-labels the first.
    trigger_line: str | None = None
    #: Which pinbar definitions the trigger candle satisfied, comma-joined.
    #: Emitted so each grade is an analysis-time cut on one collection.
    pinbar_grade: str | None = None
    #: The tested block's edges and the test extreme, so both the filter and
    #: the stop can be re-derived at analysis time rather than frozen into the
    #: scan. `r_atr` measures the entry against the *test extreme* -- the tip
    #: of the wick -- while the layer's thesis is about the two *levels* being
    #: close. Over 1198 M15 reclaims the distance to the block runs at a median
    #: 0.69 of the distance to the wick and under 0.17 a tenth of the time, so
    #: they are not the same measurement, and 27% of what the 1.0 threshold
    #: discards sits within one ATR of the block. Emitted, never filtered.
    block_low: float | None = None
    block_high: float | None = None
    #: Did price ever *leave* the block between its creation and the test?
    #: A block tall enough to swallow the range is touched by every candle,
    #: and a "test" of a level price never left is not a test of anything.
    #: `departure_atr` is the furthest price worked clear of the box (the
    #: whole candle beyond it) before coming back, in local ATR; 0.0 means it
    #: never got clear. `departure_candles` counts the candles fully outside.
    departure_atr: float | None = None
    departure_candles: int | None = None
    #: Candles between the block's anchor candle and the start of the test.
    block_age_candles: int | None = None
    test_extreme: float | None = None
    #: --- How the price ARRIVED at the block. All three are properties of
    #: candles that closed before the entry, so none of them peeks.
    #:
    #: A reader's 9.83R example approached its block on a near-vertical run and
    #: was rejected inside one candle, and neither `r_atr` nor the distance to
    #: the block edge would have kept it (1.87 and 1.44 against a 1.0 gate).
    #: Whatever separated it is not a distance, so these measure the *shape* of
    #: the approach instead: how far the leg travelled into the block, how many
    #: candles it took, and how much of the extreme candle was handed back.
    #: Emitted, never filtered.
    #:
    #: Net travel of the approach leg into the test extreme, in local ATR.
    approach_atr: float | None = None
    #: Candles from the leg's origin to the test extreme. With `approach_atr`
    #: this gives verticality: the same distance in fewer bars is displacement.
    approach_candles: int | None = None
    #: On the extreme candle itself, the fraction of its range given back by
    #: the close -- 1.0 is a full rejection, 0.0 closes at the extreme.
    rejection_frac: float | None = None
    #: --- EMA(9) context at the entry candle. EMITTED, NEVER FILTERED, the
    #: same discipline `r_atr` follows: the threshold is the reader's, so
    #: every variant stays an analysis-time cut on one collection rather than
    #: a scan that has to be repeated to be revisited.
    #: The line's value, and whether the entry closed beyond it in the trade's
    #: own direction (the cheap "alignment" gate that keeps the sample).
    ema9: float | None = None
    ema_side: bool | None = None
    #: Whether the entry candle also *reclaimed* the 9 -- wick through, close
    #: back across -- the way it reclaimed the VWAP. Strict confluence.
    ema_reclaimed: bool | None = None
    #: Whether the 9 sits BEYOND the VWAP in the trade's direction, i.e. it is
    #: the far level. This decides whether the confluence gate can bite at all:
    #: with the 9 inside, clearing the VWAP clears it for free.
    ema_is_far: bool | None = None
    #: |EMA9 - VWAP| in local ATR. How much room the gate has to matter.
    ema_gap_atr: float | None = None
    #: The 9 rising (True) / falling (False) over the previous 3 candles,
    #: signed to the trade: True means it slopes the trade's way.
    ema_slope_with: bool | None = None
    #: Whether this was the order block's FIRST visit -- the "pelo menos pela
    #: primeira vez" of the rule as described.
    first_test: bool | None = None
    #: How much the VWAP had accumulated at the entry, in candles. The lift
    #: was seen to track this across four measurements -- but those varied
    #: timeframe, anchor AND accumulation at once, so "the accumulation is the
    #: motor" is a reading of them, not a result. Carried per entry so the
    #: question can be asked *within* one timeframe, where nothing else moves.
    vwap_candles: int | None = None
    atr_pct: float | None = None
    #: R measured in the symbol's own ATR -- the scale-free version of "tight".
    r_atr: float | None = None
    entry_timestamp: str = ""


#: Position-management variants, all aiming at 2R. The plain arm is the one
#: every number in the study so far used: fixed 2R target, fixed 1R stop,
#: marked to market at the horizon. The rest move the stop, and the point of
#: measuring them together is that management is usually sold as free -- "you
#: can only win by protecting" -- when what it actually does is trade the
#: shape of the curve for its expectation, in a direction nobody checks.
MANAGE_VARIANTS = ("plain", "be0.5", "be1.0", "be1.5", "partial", "trail1.0")
#: What each variant costs in round trips: one entry and one exit, except the
#: partial, which exits twice.
MANAGE_COST_MULTIPLE = {v: 1.5 if v == "partial" else 1.0 for v in MANAGE_VARIANTS}


def _managed(
    ev: Ev, candles: Sequence[Candle], horizon: int
) -> dict[str, float]:
    """Each management variant's payoff in R, walked candle by candle.

    The whole reason to do this on the path rather than from the outcome grid
    is one case: a trade that reaches the arming level, comes back to entry,
    and *then* runs to target. The grid records it as a winner, so reading a
    breakeven rule off the grid credits it with a win the rule would not have
    taken -- which is exactly why breakeven always looks free on paper. Here it
    is a scratch, because the walk sees the order.

    Within a single candle the order of two touches is unknowable, so the
    adverse one is credited throughout, the same convention `hit` uses. That
    makes every managed number here a slight *under*statement, which is the
    right direction for a variant being asked to prove itself.
    """
    bull = ev.direction is BULL
    window = candles[ev.entry_index + 1 : ev.entry_index + 1 + horizon]
    target = ev.entry + 2.0 * ev.r if bull else ev.entry - 2.0 * ev.r
    out: dict[str, float] = {}

    def _level(mult: float) -> float:
        return ev.entry + mult * ev.r if bull else ev.entry - mult * ev.r

    def _hit(c: Candle, level: float, *, above: bool) -> bool:
        return c.high >= level if above else c.low <= level

    for variant in MANAGE_VARIANTS:
        stop = ev.stop
        armed = False
        booked = 0.0          # R already banked by a partial exit
        size = 1.0            # fraction of the position still open
        payoff: float | None = None
        arm_at = {"be0.5": 0.5, "be1.0": 1.0, "be1.5": 1.5}.get(variant)
        for c in window:
            # adverse first, always
            if _hit(c, stop, above=not bull):
                payoff = booked + size * ((stop - ev.entry) if bull
                                          else (ev.entry - stop)) / ev.r
                break
            if variant == "partial" and not armed and _hit(c, _level(1.0), above=bull):
                armed, booked, size, stop = True, 0.5 * 1.0, 0.5, ev.entry
            elif arm_at is not None and not armed and _hit(c, _level(arm_at), above=bull):
                armed, stop = True, ev.entry
            elif variant == "trail1.0":
                peak = (c.high - ev.entry if bull else ev.entry - c.low) / ev.r
                if peak > 1.0:
                    trailed = _level(peak - 1.0)
                    stop = max(stop, trailed) if bull else min(stop, trailed)
            if _hit(c, target, above=bull):
                payoff = booked + size * 2.0
                break
        else:
            if window:
                move = window[-1].close - ev.entry
                payoff = booked + size * ((move if bull else -move) / ev.r)
        out[variant] = 0.0 if payoff is None else payoff
    return out


def _measure(
    ev: Ev,
    candles: Sequence[Candle],
    horizons: Sequence[int],
    targets: Sequence[float],
    target_horizon: int,
) -> Ev:
    i0 = ev.entry_index
    ev.entry_timestamp = candles[i0].timestamp.isoformat()
    bull = ev.direction is BULL
    for h in horizons:
        w = candles[i0 + 1 : i0 + 1 + h]
        if not w:
            continue
        hi = max(c.high for c in w)
        lo = min(c.low for c in w)
        last = w[-1].close
        if bull:
            ev.mfe[h] = (hi - ev.entry) / ev.r
            ev.mae[h] = (ev.entry - lo) / ev.r
            ev.favor[h] = last > ev.entry
        else:
            ev.mfe[h] = (ev.entry - lo) / ev.r
            ev.mae[h] = (hi - ev.entry) / ev.r
            ev.favor[h] = last < ev.entry
    # The trade as actually taken, at each target of the grid: kR target, 1R
    # stop, marked to market if neither is reached inside the horizon.
    for k in (1.0, 1.5, 2.0, 2.5, 3.0):
        payoff = 0.0
        for c in candles[i0 + 1 : i0 + 1 + target_horizon]:
            if (c.low <= ev.stop) if bull else (c.high >= ev.stop):
                payoff = -1.0
                break
            reached = (
                c.high >= ev.entry + k * ev.r if bull else c.low <= ev.entry - k * ev.r
            )
            if reached:
                payoff = k
                break
        else:
            tail = candles[i0 + 1 : i0 + 1 + target_horizon]
            if tail:
                move = tail[-1].close - ev.entry
                payoff = (move if bull else -move) / ev.r
        ev.r_grid[k] = payoff
    ev.r_outcome = ev.r_grid[2.0]

    ev.r_manage = _managed(ev, candles, target_horizon)

    for k in targets:
        tgt = ev.entry + k * ev.r if bull else ev.entry - k * ev.r
        # bounded: "did it make kR within N candles", not "did it ever".
        # An unbounded scan credits whoever had more series left after them.
        ev.hit[k] = False
        for c in candles[i0 + 1 : i0 + 1 + target_horizon]:
            # the stop is checked first: within one candle we cannot know the
            # order, so credit the adverse side.
            stopped = c.low <= ev.stop if bull else c.high >= ev.stop
            reached = c.high >= tgt if bull else c.low <= tgt
            if stopped:
                break
            if reached:
                ev.hit[k] = True
                break
    return ev


# `MarketControlAnalyzer`'s own window and directional floor, mirrored so the
# OI-free aggression axis is the same reading the analyzer would report.
_AGG_WINDOW = {TimeFrame.M1: 20, TimeFrame.M5: 15, TimeFrame.M15: 10,
               TimeFrame.M30: 7, TimeFrame.H1: 7, TimeFrame.H4: 5,
               TimeFrame.D1: 5, TimeFrame.W1: 3}
_AGG_FLOOR = 0.06


def _tag_all(
    ev: Ev,
    candles: Sequence[Candle],
    horizons: Sequence[int],
    targets: Sequence[float],
    target_horizon: int,
    agg_window: int,
    control_at: dict,
    strong_floor: float,
) -> Ev:
    """Measure the trade forward, then attach every context reading to it.

    One pipeline rather than four nested calls: each pass reads the same entry
    from a different angle (outcome, candle anatomy, window aggression, local
    volatility, CVDxOI quadrant) and none depends on another's result.
    """
    _measure(ev, candles, horizons, targets, target_horizon)
    _tag_delta(ev, candles)
    _tag_atr(ev, candles)
    _tag_aggression(ev, candles, agg_window)
    _tag_control(ev, control_at, strong_floor)
    return ev


def _local_atr(candles: Sequence[Candle], index: int, period: int = 14) -> float | None:
    """Mean true range over the window ending at `index`, inclusive.

    The same window `liquidity_hunter.app.block_reclaim._local_atr` uses, and
    for the same reason: the floor and the reading both have to be stated in
    the volatility the entry actually sits in, not in the average of a window
    that may span a year of regimes.
    """
    start = max(1, index - period + 1)
    trs = [
        max(
            candles[i].high - candles[i].low,
            abs(candles[i].high - candles[i - 1].close),
            abs(candles[i].low - candles[i - 1].close),
        )
        for i in range(start, index + 1)
    ]
    if not trs:
        return None
    atr = sum(trs) / len(trs)
    return atr if atr > 0 else None


def _tag_approach(
    ev: Ev, candles: Sequence[Candle], *, lookback: int = 10,
) -> Ev:
    """Measure the shape of the leg that carried price into the block.

    The origin is the leg's own extreme within `lookback` candles before the
    test, so a slow drift and an impulse of the same size are told apart by
    `approach_candles` rather than by distance alone.
    """
    if ev.test_extreme is None:
        return ev
    bull = ev.direction is BULL
    # locate the candle that made the test extreme, at or before the entry
    lo = max(0, ev.trigger_index - lookback)
    span = range(lo, ev.entry_index + 1)
    if not span:
        return ev
    k = (
        min(span, key=lambda i: candles[i].low)
        if bull
        else max(span, key=lambda i: candles[i].high)
    )
    atr = _local_atr(candles, ev.entry_index)
    if not atr:
        return ev
    origin_span = range(max(0, k - lookback), k + 1)
    origin = (
        max(candles[i].high for i in origin_span)
        if bull
        else min(candles[i].low for i in origin_span)
    )
    extreme = candles[k].low if bull else candles[k].high
    ev.approach_atr = abs(origin - extreme) / atr
    j = max(
        origin_span,
        key=lambda i: candles[i].high if bull else -candles[i].low,
    )
    ev.approach_candles = max(1, k - j)
    c = candles[k]
    rng = c.high - c.low
    if rng > 0:
        ev.rejection_frac = (
            (c.close - c.low) / rng if bull else (c.high - c.close) / rng
        )
    return ev


def _tag_ema(
    ev: Ev, candles: Sequence[Candle], line: Sequence[float | None],
    vwap_at: dict, *, slope_lookback: int = 3,
) -> Ev:
    """Record where the EMA(9) stood at the entry, and nothing else.

    Six facts, none of them a decision. `ema_is_far` is the one that governs
    whether a confluence gate can do any work: after a decline into a block
    below the VWAP the fast line is usually below it too, so a bullish reclaim
    of the VWAP already clears the 9 and the gate never fires.
    """
    i = ev.entry_index
    v = line[i] if i < len(line) else None
    if v is None:
        return ev
    c = candles[i]
    bull = ev.direction is BULL
    ev.ema9 = v
    ev.ema_side = (c.close > v) if bull else (c.close < v)
    ev.ema_reclaimed = (
        c.low < v <= c.close if bull else c.high > v >= c.close
    )
    w = vwap_at.get(c.timestamp)
    if w is not None:
        ev.ema_is_far = (v > w) if bull else (v < w)
        atr = _local_atr(candles, i)
        if atr:
            ev.ema_gap_atr = abs(v - w) / atr
    j = i - slope_lookback
    if j >= 0 and j < len(line) and line[j] is not None:
        rising = v > line[j]
        ev.ema_slope_with = rising if bull else not rising
    return ev


def _tag_atr(ev: Ev, candles: Sequence[Candle], period: int = 14) -> Ev:
    """Local ATR at the entry, so R can be read in the symbol's own units.

    R as a fraction of *price* is not comparable across symbols: BNB's 3% and
    a small cap's 3% are different distances in the only unit that matters,
    which is that instrument's own movement. Without this, "tight R" could
    just be selecting low-volatility symbols.
    """
    atr = _local_atr(candles, ev.entry_index, period)
    if atr:
        ev.atr_pct = atr / ev.entry
        ev.r_atr = ev.r / atr
    return ev


def _tag_aggression(ev: Ev, candles: Sequence[Candle], window: int) -> Ev:
    w = candles[max(0, ev.entry_index - window + 1) : ev.entry_index + 1]
    total = sum(c.volume for c in w)
    if total <= 0:
        return ev
    ev.agg_ratio = max(-1.0, min(1.0, sum(volume_delta(c) for c in w) / total))
    return ev


def _tag_delta(ev: Ev, candles: Sequence[Candle]) -> Ev:
    """Whether the entry candle's own taker delta opposed the trade."""
    d = volume_delta(candles[ev.entry_index])
    ev.delta_against = d < 0 if ev.direction is BULL else d > 0
    return ev


def _tag_control(ev: Ev, control_at: dict, strong_floor: float) -> Ev:
    """Cross the entry with `MarketControlAnalyzer`'s CVD x OI reading.

    Two opposite hypotheses, and the setup's own logic argues for the second:

    * ``aligned`` -- a side is *credited* with control in the trade's direction
      (buy aggression on rising OI = fresh money buying into the reclaim).
    * ``unwind``  -- the **opposing** population is closing: shorts covering
      under a long, longs liquidating under a short. The setup's thesis is
      that the reclaim is the moment the trapped side stops being supply, and
      that is an exit quadrant, not a buildup one. `controller` alone cannot
      tell the two apart -- both read as buy aggression -- which is why the
      point carries `regime` as well.
    """
    point = control_at.get(ev.entry_index)
    if point is None:
        return ev
    bull = ev.direction is BULL
    ev.ctrl_aligned = point.controller is (
        MarketControlSide.BUYERS if bull else MarketControlSide.SELLERS
    )
    ev.ctrl_unwind = point.regime is (
        OIRegime.SHORT_COVERING if bull else OIRegime.LONG_LIQUIDATION
    )
    # `controller` credits a side on a small minority of candles, so it cannot
    # filter anything without collapsing the sample -- the same reason the Tide
    # ribbon reads `control_score` against the window's own distribution
    # instead. `with` is the signed score agreeing with the trade; `strong`
    # adds conviction in the window's top quartile.
    signed = point.control_score if bull else -point.control_score
    ev.ctrl_with = signed > 0
    ev.ctrl_strong = signed > 0 and abs(point.control_score) >= strong_floor
    # The quadrant names both axes, so the cross can be taken apart without
    # recomputing either: buildup = OI rising, covering/liquidation = falling.
    if point.regime is not OIRegime.FLAT:
        ev.agg_bull = point.regime in (OIRegime.LONG_BUILDUP, OIRegime.SHORT_COVERING)
        ev.oi_up = point.regime in (OIRegime.LONG_BUILDUP, OIRegime.SHORT_BUILDUP)
    return ev


def _is_pinbar(candle: Candle, *, bull: bool, wick_frac: float, body_frac: float) -> bool:
    rng = candle.high - candle.low
    if rng <= 0:
        return False
    body = abs(candle.close - candle.open)
    wick = (
        min(candle.close, candle.open) - candle.low
        if bull
        else candle.high - max(candle.close, candle.open)
    )
    return wick >= wick_frac * rng and body <= body_frac * rng


#: The golden rule: a level-1 pinbar is two thirds tail. Read from a trader
#: rather than fitted -- 0.65 is the tolerance they already work with.
L1_TAIL = 0.65
#: A level-2 pinbar trades tail for body: a real body with a decent tail and
#: almost no nose. The proportions are the reader's, not this study's.
L2_BODY = 1.0 / 3.0
L2_TAIL = 0.20
#: Both grades cap the nose -- the wick on the far side, which is the part that
#: says price was pushed back the other way before the candle closed.
NOSE_MAX = 0.15


def _pinbar_grades(candle: Candle, *, bull: bool) -> frozenset[str]:
    """Which pinbar definitions this candle satisfies.

    Three, deliberately kept apart rather than merged into one threshold:

    * ``legacy`` -- what this project has been measuring: tail >= 0.50, body
      <= 0.35, nose unconstrained. Worth naming as its own thing, because it is
      neither of the two below: looser than the golden rule on the tail, and
      silent about the nose entirely.
    * ``l1`` -- the golden rule, tail >= 0.65 with a capped nose.
    * ``l2`` -- body >= 1/3, tail >= 0.20, nose <= 0.15. A candle that closed
      most of the way through its own range but still left a tail underneath.

    A reader's 03 Aug example is the case that motivated this: 43% body, 53%
    tail, 4% nose. It fails `legacy` on the body by eight points, fails `l1` on
    the tail, and passes `l2` cleanly -- and fifteen minutes earlier the legacy
    rule fired a *losing* trade in the opposite direction on the same chart.
    """
    rng = candle.high - candle.low
    if rng <= 0:
        return frozenset()
    body = abs(candle.close - candle.open)
    tail = (
        min(candle.close, candle.open) - candle.low
        if bull
        else candle.high - max(candle.close, candle.open)
    )
    nose = (
        candle.high - max(candle.close, candle.open)
        if bull
        else min(candle.close, candle.open) - candle.low
    )
    out: set[str] = set()
    if tail >= MIN_WICK_FRACTION * rng and body <= MAX_BODY_FRACTION * rng:
        out.add("legacy")
    if tail >= L1_TAIL * rng and nose <= NOSE_MAX * rng:
        out.add("l1")
    if body >= L2_BODY * rng and tail >= L2_TAIL * rng and nose <= NOSE_MAX * rng:
        out.add("l2")
    return frozenset(out)


def _reclaims(candle: Candle, vwap_value: float, *, bull: bool) -> bool:
    """The wick pierces the VWAP and the close lands back on the other side."""
    return (
        candle.low < vwap_value <= candle.close
        if bull
        else candle.high > vwap_value >= candle.close
    )


def _ema_hook(
    candles: Sequence[Candle], line: Sequence[float | None], vwap_at: dict,
    reclaim: int, *, bull: bool, wait: int, wick_frac: float, body_frac: float,
) -> tuple[int, float] | None:
    """The pullback pinbar on the fast line, taken AFTER the VWAP is reclaimed.

    A different trade from the reclaim itself, not a filter on it: price has
    already crossed the shared level, and the entry is the first pullback that
    holds the 9 while staying on the reclaimed side of the VWAP. Returns the
    entry index and its stop (the pullback's extreme), or None if the wait
    elapses without one.

    Note what the geometry does to the risk unit: the stop is the pullback's
    low rather than the whole test's, which is *tighter*, and a tighter stop
    pays a larger share of its R to the same round trip. That is the M5 result
    restated inside one timeframe, and it is why this arm has to be read in net
    R and never in hit rate.
    """
    for j in range(reclaim + 1, min(reclaim + 1 + wait, len(candles))):
        v = line[j] if j < len(line) else None
        w = vwap_at.get(candles[j].timestamp)
        if v is None or w is None:
            continue
        c = candles[j]
        if bull and c.close < w:
            return None  # gave the VWAP back; the premise is gone
        if not bull and c.close > w:
            return None
        if not _is_pinbar(c, bull=bull, wick_frac=wick_frac, body_frac=body_frac):
            continue
        touched = c.low < v <= c.close if bull else c.high > v >= c.close
        if not touched:
            continue
        span = candles[reclaim + 1 : j + 1]
        stop = min(x.low for x in span) if bull else max(x.high for x in span)
        return j, stop
    return None


def _emit_hook(
    out: list[Ev], candles, line, vwap_at, reclaim: int, *, arm: str, bull: bool,
    direction, symbol: str, timeframe, hook_wait: int, wick_frac: float,
    body_frac: float, min_r_atr: float, max_h: int, horizons, targets,
    target_horizon: int, agg_window: int, control_at, strong_floor: float,
    random_reps: int, rng,
) -> None:
    """Append the `<arm>-hook` trade for one episode, plus its own controls.

    Additive by construction: the reclaim entry is already in `out` and is not
    touched, so the pair can be compared on one population rather than one
    replacing the other.
    """
    hook = _ema_hook(
        candles, line, vwap_at, reclaim, bull=bull, wait=hook_wait,
        wick_frac=wick_frac, body_frac=body_frac,
    )
    if hook is None:
        return
    hj, hstop = hook
    hr = abs(candles[hj].close - hstop)
    hatr = _local_atr(candles, hj)
    if not (hr > 0 and hatr and hr / hatr >= min_r_atr and hj + max_h < len(candles)):
        return
    out.append(_tag_all(
        Ev(f"{arm}-hook", symbol, timeframe.value, direction, reclaim, hj,
           candles[hj].close, hstop, hr),
        candles, horizons, targets, target_horizon,
        agg_window, control_at, strong_floor,
    ))
    for _ in range(random_reps):
        ri = rng.randrange(50, len(candles) - max_h - 1)
        e2 = candles[ri].close
        s2 = e2 - hr if bull else e2 + hr
        out.append(_tag_all(
            Ev(f"rand-{arm}-hook", symbol, timeframe.value, direction,
               ri, ri, e2, s2, hr),
            candles, horizons, targets, target_horizon,
            agg_window, control_at, strong_floor,
        ))


def _ema_level_entries(
    candles: Sequence[Candle], ema_at: dict, *, bull: bool,
    wick_frac: float, body_frac: float,
) -> list[int]:
    """Placebo for the fast line: every pinbar that reclaims the EMA(9).

    The mirror of `_vwap_only_entries`, so the two shared lines can be compared
    as levels on one population before any block is involved.
    """
    out: list[int] = []
    for i, c in enumerate(candles):
        v = ema_at.get(c.timestamp)
        if v is None:
            continue
        if _reclaims(c, v, bull=bull) and _is_pinbar(
            c, bull=bull, wick_frac=wick_frac, body_frac=body_frac
        ):
            out.append(i)
    return out


def _ob_ema_triggers(
    candles: Sequence[Candle], zones, ema_at: dict, *, bull: bool,
    wick_frac: float, body_frac: float, max_wait: int,
) -> list[tuple[int, int, bool]]:
    """The block setup with the EMA(9) standing in for the VWAP.

    The 9 *replaces* the session average rather than joining it: the test has
    to happen on the far side of the 9, and the reclaim has to be of the 9.
    This is the question behind "or on the 9" -- not whether the two levels can
    be combined, but whether the fast line is a shared reference of the same
    quality. `docs/block_reclaim.md` argues the VWAP earns its place by being
    widely *observed*, not by being anyone's break-even; if that is the whole
    mechanism, a line every chart also draws should do comparable work.

    Reuses the production `_visits`, so block lifetime and the FIFO retirement
    correction are identical -- only the level changes. Returns
    `(test_start, reclaim_index, first_visit)`.
    """
    want = BULL if bull else BEAR
    out: list[tuple[int, int, bool]] = []
    for zone in zones:
        if zone.direction is not want:
            continue
        for start, end, first in _visits(list(candles), zone, ema_at, bullish=bull):
            for j in range(end + 1, min(end + 1 + max_wait, len(candles))):
                v = ema_at.get(candles[j].timestamp)
                if v is None:
                    continue
                if _reclaims(candles[j], v, bull=bull) and _is_pinbar(
                    candles[j], bull=bull, wick_frac=wick_frac, body_frac=body_frac
                ):
                    out.append((start, j, first))
                    break
    return out


def _either_line_trigger(
    candles: Sequence[Candle], vwap_at: dict, ema_at: dict, start: int, end: int,
    *, bull: bool, wick_frac: float, body_frac: float, max_wait: int,
    accept: frozenset[str] = frozenset({"legacy"}),
    from_start: bool = False,
) -> tuple[int, str, str] | None:
    """The setup as described on the charts: reject EITHER shared line.

    Three conditions, in the order a reader states them. Price has already
    tested the block; it must be back on the working side of the VWAP; the
    EMA(9) must have *crossed* the VWAP (a state, not a candle); and then the
    pinbar counts whether it rejects the VWAP, the 9, or both.

    The second route is the one the existing arms could not produce: a pullback
    that finds the 9 while price stays clear of the VWAP entirely. It is not
    the `-hook` arm, which required a VWAP reclaim pinbar to have fired first
    and so could only ever be a subset of trades the main trigger already took.
    """
    # A visit's *end* is not knowable when it happens (it keeps absorbing
    # later touches), so a window anchored on it moves as candles arrive --
    # 6.5% of live reclaims vanish from a later read of the same series
    # (`research/reclaim_stability.py`). The *start* is settled by the past.
    anchor = start if from_start else end
    for j in range(anchor, min(anchor + max_wait + 1, len(candles))):
        c = candles[j]
        w, m = vwap_at.get(c.timestamp), ema_at.get(c.timestamp)
        if w is None or m is None:
            continue
        crossed = (m > w) if bull else (m < w)
        if not crossed:
            continue  # the 9 has not crossed the average yet
        grades = _pinbar_grades(c, bull=bull)
        if not (grades & accept):
            continue
        on_vwap = _reclaims(c, w, bull=bull)
        # a rejection of the 9 only counts while price holds the VWAP side --
        # otherwise it is a bounce underneath the average, a different picture.
        holds = (c.close > w) if bull else (c.close < w)
        on_ema = holds and (c.low < m <= c.close if bull else c.high > m >= c.close)
        tag = ",".join(sorted(grades))
        if on_vwap and on_ema:
            return j, "both", tag
        if on_vwap:
            return j, "vwap", tag
        if on_ema:
            return j, "ema", tag
    return None


def _episodes(indices: Sequence[int], merge_gap: int) -> list[tuple[int, int]]:
    """Group consecutive trigger candles into one test episode each.

    Price routinely spends several candles inside an order block; that is one
    test, not six. The episode's *start* is what the stop is measured from --
    "the bottom of where it tested" is the bottom of the whole visit.
    """
    out: list[tuple[int, int]] = []
    for i in indices:
        if out and i - out[-1][1] <= merge_gap:
            out[-1] = (out[-1][0], i)
        else:
            out.append((i, i))
    return out


def _eql_triggers(data, candles, vwap_at, *, bull: bool, lag: int) -> list[int]:
    want = LiquidityZoneType.EQUAL_LOWS if bull else LiquidityZoneType.EQUAL_HIGHS
    zones = [z for z in data.liquidity_zones if z.zone_type is want]
    if not zones:
        return []
    ts_index = {c.timestamp: i for i, c in enumerate(candles)}
    hits: list[int] = []
    for i, c in enumerate(candles):
        v = vwap_at.get(c.timestamp)
        if v is None:
            continue
        if (c.low >= v) if bull else (c.high <= v):
            continue
        prev = candles[i - 1] if i else None
        for z in zones:
            formed = ts_index.get(z.formed_at)
            if formed is None or i < formed + lag:
                continue  # the pivot behind the pool has not confirmed yet
            edge = z.price_low if bull else z.price_high
            took = c.low < edge if bull else c.high > edge
            if not took:
                continue
            # only the candle that first pierces it -- afterwards the pool is gone
            if prev is not None and (prev.low < edge if bull else prev.high > edge):
                continue
            hits.append(i)
            break
    return hits


def _vwap_only_entries(
    candles: Sequence[Candle], vwap_at: dict, *, bull: bool,
    wick_frac: float, body_frac: float,
) -> list[int]:
    """The placebo arm: the same pinbar reclaim with no liquidity test at all.

    The decisive control for this setup. A random entry only shows that the
    setup beats noise; this one asks the question that actually matters --
    does testing an order block (or clearing equal lows) add anything over
    simply taking every pinbar that reclaims the VWAP?
    """
    out: list[int] = []
    for i, c in enumerate(candles):
        v = vwap_at.get(c.timestamp)
        if v is None:
            continue
        if _reclaims(c, v, bull=bull) and _is_pinbar(
            c, bull=bull, wick_frac=wick_frac, body_frac=body_frac
        ):
            out.append(i)
    return out


def event_anchored_vwap(
    candles: Sequence[Candle],
    events: Sequence,
    *,
    symbol: str,
    timeframe: TimeFrame,
) -> VWAPSeries | None:
    """A VWAP that restarts at each trend flip instead of at midnight UTC.

    The setup's thesis is that the VWAP matters because it is a *population's*
    break-even: whoever entered since the anchor is underwater below it and
    therefore supply. A calendar anchor names no population -- 00:00 UTC is
    not an event anyone entered on. A structural reversal does: the crowd that
    bought a bullish CHoCH has that leg's average as its zero.

    So this re-anchors at every non-provisional `CHANGE_OF_CHARACTER` and
    `CHOCH_FAILED`, and the series it returns has the same shape as the
    calendar one -- several accumulations, delimited by `anchor_timestamp` --
    so `detect_block_reclaims` consumes it unchanged and the two anchors are
    compared with everything else held fixed.

    Sweeps are deliberately not anchors here. They are frequent enough that
    the average would restart every few candles and never accumulate anyone,
    which is the same degenerate case as a six-candle session VWAP on H4.
    """
    flips = sorted(
        {
            e.timestamp
            for e in events
            if not getattr(e, "provisional", False)
            and e.event in (StructureEvent.CHANGE_OF_CHARACTER,
                            StructureEvent.CHOCH_FAILED)
        }
    )
    if not flips:
        return None
    index_of = {c.timestamp: i for i, c in enumerate(candles)}
    cuts = sorted({index_of[t] for t in flips if t in index_of})
    if not cuts:
        return None
    bounds = [0, *cuts, len(candles)] if cuts[0] != 0 else [*cuts, len(candles)]
    points = []
    for start, end in zip(bounds, bounds[1:], strict=False):
        segment = candles[start:end]
        if not segment:
            continue
        part = compute_vwap(segment, symbol=symbol, timeframe=timeframe,
                            anchor=VWAPAnchor.EVENT,
                            anchor_timestamp=segment[0].timestamp)
        if part is not None:
            points.extend(part.points)
    if not points:
        return None
    return VWAPSeries(
        symbol=symbol,
        timeframe=timeframe,
        anchor=VWAPAnchor.EVENT,
        anchor_timestamp=points[0].anchor_timestamp,
        label="Flip",
        band_multipliers=(1.0, 2.0),
        estimated=True,
        points=points,
    )


def run_combo(
    symbol: str,
    timeframe: TimeFrame,
    *,
    provider: OHLCVProvider | None,
    futures_provider: FuturesDataProvider | None,
    limit: int,
    horizons: Sequence[int],
    targets: Sequence[float],
    target_horizon: int,
    max_wait: int,
    merge_gap: int,
    wick_frac: float,
    body_frac: float,
    min_r_atr: float,
    eql_lag: int,
    fresh_ob: bool,
    vwap_anchor: str | None,
    htf_steps: Sequence[tuple[datetime, MarketDirection]] | None,
    random_reps: int,
    rng: random.Random,
    ema_period: int = 9,
    hook_wait: int = 10,
) -> tuple[list[Ev], dict[str, int]]:
    data = load_dashboard_data(
        provider=provider,
        symbol=symbol,
        timeframe=timeframe,
        limit=limit,
        futures_provider=futures_provider,
        compute_narrative=False,
    )
    candles = data.candles
    # `_VWAP_ANCHOR_PERIOD` wires WEEK at H4 and MONTH on the dailies, so the
    # H4 chart's VWAP is not the session one the rule was described on. Passing
    # an explicit anchor recomputes the series rather than patching production
    # config, and makes which VWAP was measured part of the run.
    series = data.vwap
    if vwap_anchor == "event":
        # The rival anchor: a population named by a structural reversal rather
        # than by the calendar. Everything downstream is untouched, so the two
        # runs differ in exactly one thing.
        series = event_anchored_vwap(
            candles, data.internal_structure_events,
            symbol=symbol, timeframe=timeframe,
        )
    elif vwap_anchor is not None:
        series = compute_vwap(candles, symbol=symbol, timeframe=timeframe,
                              anchor=VWAPAnchor(vwap_anchor))
    if len(candles) < 200 or series is None:
        return [], {}
    vwap_at = {p.timestamp: p.value for p in series.points}
    ema_line = ema_series(candles, ema_period)
    ema_at_pre = {
        c.timestamp: v
        for c, v in zip(candles, ema_line, strict=False)
        if v is not None
    }
    # How many candles the average had accumulated at each point. Tagged on
    # every arm, placebo included: comparing the block arm's gradient against
    # a gradient the placebo also has is what separates "the accumulation is
    # the motor" from "late-session candles behave differently".
    accumulated: dict[datetime, int] = {}
    run_anchor: datetime | None = None
    run = 0
    for pt in series.points:
        if pt.anchor_timestamp != run_anchor:
            run_anchor, run = pt.anchor_timestamp, 0
        run += 1
        accumulated[pt.timestamp] = run
    idx_of = {c.timestamp: i for i, c in enumerate(candles)}
    control_at = {}
    agg_window = _AGG_WINDOW.get(timeframe, 10)
    strong_floor = 0.0
    if data.market_control is not None:
        control_at = {
            idx_of[p.timestamp]: p
            for p in data.market_control.series
            if p.timestamp in idx_of
        }
        magnitudes = sorted(abs(p.control_score) for p in control_at.values())
        if magnitudes:
            strong_floor = magnitudes[int(0.75 * (len(magnitudes) - 1))]
    max_h = max(horizons)

    out: list[Ev] = []
    counts: dict[str, int] = {}
    counts["span-days"] = int(
        (candles[-1].timestamp - candles[0].timestamp).total_seconds() // 86400
    )

    # The `ob` arm IS the production detector, imported rather than restated.
    # For most of this study the two were the same rule written twice, which
    # left a superset the measurement did not cover (~9% more entries, from a
    # scan-order difference: this script merged the candles that touched *any*
    # block into one stream, so a visit to one block could mask a visit to
    # another, while the detector scans per block). Importing it closes that,
    # and closes the standing risk behind it -- `POIDetector` is under active
    # development, and while these were two implementations any change to it
    # would move production without moving the measured object.
    #
    # What the arm still owns is everything downstream of the observation: the
    # entry, the stop, the forward outcome, the control. The detector names
    # none of those by design.
    if (max_wait, merge_gap, wick_frac, body_frac) != (
        MAX_WAIT_CANDLES, MERGE_GAP_CANDLES, MIN_WICK_FRACTION, MAX_BODY_FRACTION
    ):
        raise SystemExit(
            "the ob arm is `app.block_reclaim`, whose --max-wait/--merge-gap/"
            "--wick-frac/--body-frac are compiled in; sweeping them means "
            "changing the detector's constants, which is a change to the rule"
        )
    # Three arms off the production detector, differing in one thing each so
    # the pair that answers the question is measured on one collection:
    #
    #   ob       -- the VWAP route alone, the arm every prior result is stated
    #               against. Untouched, and it alone emits the random control
    #               and the hook, so those outputs stay comparable.
    #   ob-lines -- the same with the EMA route open: the BEFORE of the body
    #               rule, since the rule gates both routes.
    #   ob-body  -- ob-lines plus `require_body_clears_vwap`: the AFTER.
    #
    # The primary comparison is ob-body against ob-lines. Comparing against `ob`
    # instead would confound the body rule with opening the second route.
    for ob_arm, ob_ema, ob_body in (
        ("ob", None, False),
        ("ob-lines", ema_line, False),
        ("ob-body", ema_line, True),
    ):
      reclaims = detect_block_reclaims(
        candles, data.poi_zones, series, symbol=symbol, timeframe=timeframe,
        ema=ob_ema, require_body_clears_vwap=ob_body,
      )
      counts[f"{ob_arm}-episodes"] = counts.get(f"{ob_arm}-episodes", 0) + len(reclaims)
      for reclaim in reclaims:
        if reclaim.provisional:
            continue  # the forming candle is not an observation yet
        if fresh_ob and not reclaim.first_test:
            continue
        if reclaim.r_atr is None or reclaim.r_atr < min_r_atr:
            continue
        e = idx_of[reclaim.timestamp]
        start = idx_of[reclaim.test_start_timestamp]
        direction = reclaim.direction
        bull = direction is BULL
        if htf_steps is not None and htf_trend_at(
            htf_steps, reclaim.timestamp
        ) is not direction:
            continue  # the HTF is not on this trade's side
        counts[f"{ob_arm}-entries"] = counts.get(f"{ob_arm}-entries", 0) + 1
        if e + max_h >= len(candles):
            continue
        entry, stop, r = reclaim.reclaim_price, reclaim.test_extreme, reclaim.reclaim_distance
        ev = _tag_all(
            Ev(ob_arm, symbol, timeframe.value, direction, start, e, entry, stop, r),
            candles, horizons, targets, target_horizon,
            agg_window, control_at, strong_floor,
        )
        ev.first_test = reclaim.first_test
        ev.block_low = reclaim.block_price_low
        ev.block_high = reclaim.block_price_high
        ev.test_extreme = reclaim.test_extreme
        ev.trigger_line = reclaim.trigger_line
        born = idx_of.get(reclaim.block_timestamp)
        if born is not None and start > born:
            atr = _local_atr(candles, e)
            clear = 0.0
            fully_out = 0
            for c in candles[born + 1 : start]:
                gap = (
                    c.low - reclaim.block_price_high
                    if bull
                    else reclaim.block_price_low - c.high
                )
                if gap > 0:
                    fully_out += 1
                    clear = max(clear, gap)
            ev.departure_atr = (clear / atr) if atr else None
            ev.departure_candles = fully_out
            ev.block_age_candles = start - born
        out.append(ev)
        # Every arm gets its own direction-matched control, drawn with that
        # arm's own R: the arms differ in which candle triggers, so they
        # differ in R, and a control carrying a different R is not matched.
        for _ in range(random_reps):
            ri = rng.randrange(50, len(candles) - max_h - 1)
            e2 = candles[ri].close
            s2 = e2 - r if bull else e2 + r
            out.append(_tag_all(
                Ev(f"rand-{ob_arm}", symbol, timeframe.value, direction, ri, ri, e2, s2, r),
                candles, horizons, targets, target_horizon,
                agg_window, control_at, strong_floor,
            ))
        if ob_arm != "ob":
            continue  # the hook belongs to the untouched arm
        _emit_hook(
            out, candles, ema_line, vwap_at, e, arm="ob", bull=bull,
            direction=direction, symbol=symbol, timeframe=timeframe,
            hook_wait=hook_wait, wick_frac=wick_frac, body_frac=body_frac,
            min_r_atr=min_r_atr, max_h=max_h, horizons=horizons,
            targets=targets, target_horizon=target_horizon,
            agg_window=agg_window, control_at=control_at,
            strong_floor=strong_floor, random_reps=random_reps, rng=rng,
        )

    for arm, collect in (("eql", _eql_triggers),):
        for bull in (True, False):
            kwargs = {"lag": eql_lag}
            idx = collect(data, candles, vwap_at, bull=bull, **kwargs)  # type: ignore[operator]
            episodes = _episodes(idx, merge_gap)
            counts[f"{arm}-episodes"] = counts.get(f"{arm}-episodes", 0) + len(episodes)
            direction = BULL if bull else BEAR
            for start, end in episodes:
                for e in range(end, min(end + max_wait + 1, len(candles))):
                    v = vwap_at.get(candles[e].timestamp)
                    if v is None:
                        continue
                    c = candles[e]
                    if not _reclaims(c, v, bull=bull):
                        continue
                    if not _is_pinbar(c, bull=bull, wick_frac=wick_frac, body_frac=body_frac):
                        continue
                    if htf_steps is not None and htf_trend_at(
                        htf_steps, candles[e].timestamp
                    ) is not direction:
                        break  # the HTF is not on this trade's side
                    counts[f"{arm}-entries"] = counts.get(f"{arm}-entries", 0) + 1
                    if e + max_h >= len(candles):
                        break
                    stop = (
                        min(x.low for x in candles[start : e + 1])
                        if bull
                        else max(x.high for x in candles[start : e + 1])
                    )
                    entry = c.close
                    r = abs(entry - stop)
                    # The floor is stated in the *local* ATR at the entry and
                    # at the detector's own value, so the study and
                    # `app.block_reclaim` keep or drop the same visits. It was
                    # a fraction of the series-wide mean true range before,
                    # which is a different unit as well as a different number:
                    # in a calm stretch it discarded what the detector kept,
                    # in a volatile one the reverse, and a dropped visit is
                    # gone rather than filterable afterwards. Anything above
                    # this is a reader's threshold and belongs in
                    # `vwap_exit_grid.py --min-r-atr`, where it can be moved
                    # without re-scanning.
                    e_atr = _local_atr(candles, e)
                    if e_atr is None or r / e_atr < min_r_atr:
                        break
                    ev = _tag_all(
                        Ev(arm, symbol, timeframe.value, direction, start, e, entry, stop, r),
                        candles, horizons, targets, target_horizon,
                        agg_window, control_at, strong_floor,
                        )
                    out.append(ev)
                    for _ in range(random_reps):
                        ri = rng.randrange(50, len(candles) - max_h - 1)
                        e2 = candles[ri].close
                        s2 = e2 - r if bull else e2 + r
                        out.append(_tag_all(
                            Ev(f"rand-{arm}", symbol, timeframe.value, direction,
                               ri, ri, e2, s2, r),
                            candles, horizons, targets, target_horizon,
                            agg_window, control_at, strong_floor,
                        ))
                    _emit_hook(
                        out, candles, ema_line, vwap_at, e, arm=arm, bull=bull,
                        direction=direction, symbol=symbol, timeframe=timeframe,
                        hook_wait=hook_wait, wick_frac=wick_frac,
                        body_frac=body_frac, min_r_atr=min_r_atr, max_h=max_h,
                        horizons=horizons, targets=targets,
                        target_horizon=target_horizon, agg_window=agg_window,
                        control_at=control_at, strong_floor=strong_floor,
                        random_reps=random_reps, rng=rng,
                    )
                    break  # one entry per test episode

    # The charted rule: block, then a pinbar on EITHER shared line, gated on
    # the 9 having crossed the average.
    #
    # This mirrors `detect_block_reclaims` condition for condition -- ORDER_BLOCK
    # zones only, scan from the visit's own end, and the same collapse of
    # several blocks resolving on one candle down to the NEAREST test -- and
    # widens exactly one thing, the line the pinbar may reject. A first version
    # re-implemented the surrounding rule instead of copying it, took every zone
    # kind and emitted one trade per visit; its VWAP route then measured 33%
    # against the production arm's 53% on the same trigger, which is the
    # signature of a broken instrument rather than of a finding.
    for arm_name, accept, from_start in (
        ("ob-either", frozenset({"legacy"}), False),
        ("ob-pin2", frozenset({"legacy", "l1", "l2"}), False),
        # The same rule with the trigger window anchored on the visit's start
        # instead of its end: stable under replay by construction. Declared as
        # its own arm so the two are measured side by side on one collection.
        ("ob-pin2s", frozenset({"legacy", "l1", "l2"}), True),
    ):
      for bull in (True, False):
        direction = BULL if bull else BEAR
        cand: dict[tuple, tuple[float, int, int, str, bool]] = {}
        for zone in data.poi_zones:
            if zone.direction is not direction or zone.kind is not POIZoneKind.ORDER_BLOCK:
                continue
            for vstart, vend, vfirst in _visits(
                list(candles), zone, vwap_at, bullish=bull
            ):
                found = _either_line_trigger(
                    candles, vwap_at, ema_at_pre, vstart, vend, bull=bull,
                    wick_frac=wick_frac, body_frac=body_frac, max_wait=max_wait,
                    accept=accept, from_start=from_start,
                )
                if found is None:
                    continue
                e, which, tag = found
                stop = (
                    min(x.low for x in candles[vstart : e + 1])
                    if bull
                    else max(x.high for x in candles[vstart : e + 1])
                )
                r = abs(candles[e].close - stop)
                if r <= 0:
                    continue
                key = (candles[e].timestamp, direction)
                held = cand.get(key)
                if held is None or r < held[0]:
                    cand[key] = (r, vstart, e, which, vfirst, zone, tag)

        for r, vstart, e, which, vfirst, zone, tag in sorted(
            cand.values(), key=lambda x: x[2]
        ):
            if htf_steps is not None and htf_trend_at(
                htf_steps, candles[e].timestamp
            ) is not direction:
                continue
            if e + max_h >= len(candles):
                continue
            e_atr = _local_atr(candles, e)
            if e_atr is None or r / e_atr < min_r_atr:
                continue
            stop = candles[e].close - r if bull else candles[e].close + r
            ev = _tag_all(
                Ev(arm_name, symbol, timeframe.value, direction,
                   vstart, e, candles[e].close, stop, r),
                candles, horizons, targets, target_horizon,
                agg_window, control_at, strong_floor,
            )
            ev.trigger_line = which
            ev.pinbar_grade = tag
            ev.first_test = vfirst
            ev.block_low = zone.price_low
            ev.block_high = zone.price_high
            ev.test_extreme = stop
            # Did price ever work clear of the box before coming back to it?
            born = idx_of.get(zone.ob_candle_timestamp)
            if born is not None and vstart > born:
                clear = 0.0
                fully_out = 0
                for c in candles[born + 1 : vstart]:
                    gap = (
                        c.low - zone.price_high
                        if bull
                        else zone.price_low - c.high
                    )
                    if gap > 0:
                        fully_out += 1
                        clear = max(clear, gap)
                ev.departure_atr = clear / e_atr if e_atr else None
                ev.departure_candles = fully_out
                ev.block_age_candles = vstart - born
            out.append(ev)
            for _ in range(random_reps):
                ri = rng.randrange(50, len(candles) - max_h - 1)
                e2 = candles[ri].close
                s2 = e2 - r if bull else e2 + r
                out.append(_tag_all(
                    Ev(f"rand-{arm_name}", symbol, timeframe.value, direction,
                       ri, ri, e2, s2, r),
                    candles, horizons, targets, target_horizon,
                    agg_window, control_at, strong_floor,
                ))

    # The fast line standing in for the session average: the same block setup
    # and the same bare-level placebo, measured against the EMA(9) instead.
    ema_at = {
        c.timestamp: v for c, v in zip(candles, ema_line, strict=False) if v is not None
    }
    for bull in (True, False):
        direction = BULL if bull else BEAR
        for start, e, first in _ob_ema_triggers(
            candles, data.poi_zones, ema_at, bull=bull, wick_frac=wick_frac,
            body_frac=body_frac, max_wait=max_wait,
        ):
            if htf_steps is not None and htf_trend_at(
                htf_steps, candles[e].timestamp
            ) is not direction:
                continue
            if e + max_h >= len(candles):
                continue
            stop = (
                min(x.low for x in candles[start : e + 1])
                if bull
                else max(x.high for x in candles[start : e + 1])
            )
            entry = candles[e].close
            r = abs(entry - stop)
            e_atr = _local_atr(candles, e)
            if r <= 0 or e_atr is None or r / e_atr < min_r_atr:
                continue
            ev = _tag_all(
                Ev("ob-ema", symbol, timeframe.value, direction, start, e, entry, stop, r),
                candles, horizons, targets, target_horizon,
                agg_window, control_at, strong_floor,
            )
            ev.first_test = first
            out.append(ev)
            for _ in range(random_reps):
                ri = rng.randrange(50, len(candles) - max_h - 1)
                e2 = candles[ri].close
                s2 = e2 - r if bull else e2 + r
                out.append(_tag_all(
                    Ev("rand-ob-ema", symbol, timeframe.value, direction,
                       ri, ri, e2, s2, r),
                    candles, horizons, targets, target_horizon,
                    agg_window, control_at, strong_floor,
                ))

    for bull in (True, False):
        direction = BULL if bull else BEAR
        for e in _ema_level_entries(
            candles, ema_at, bull=bull, wick_frac=wick_frac, body_frac=body_frac
        ):
            if htf_steps is not None and htf_trend_at(
                htf_steps, candles[e].timestamp
            ) is not direction:
                continue
            if e + max_h >= len(candles) or e < max_wait:
                continue
            lo = candles[e - max_wait : e + 1]
            stop = min(x.low for x in lo) if bull else max(x.high for x in lo)
            entry = candles[e].close
            r = abs(entry - stop)
            e_atr = _local_atr(candles, e)
            if r <= 0 or e_atr is None or r / e_atr < min_r_atr:
                continue
            out.append(_tag_all(
                Ev("ema", symbol, timeframe.value, direction, e - max_wait, e, entry, stop, r),
                candles, horizons, targets, target_horizon,
                agg_window, control_at, strong_floor,
            ))

    # placebo: every pinbar VWAP reclaim, no liquidity test required.
    for bull in (True, False):
        direction = BULL if bull else BEAR
        for e in _vwap_only_entries(
            candles, vwap_at, bull=bull, wick_frac=wick_frac, body_frac=body_frac
        ):
            if htf_steps is not None and htf_trend_at(
                htf_steps, candles[e].timestamp
            ) is not direction:
                continue
            counts["vwap-entries"] = counts.get("vwap-entries", 0) + 1
            if e + max_h >= len(candles) or e < max_wait:
                continue
            lo = candles[e - max_wait : e + 1]
            stop = min(x.low for x in lo) if bull else max(x.high for x in lo)
            entry = candles[e].close
            r = abs(entry - stop)
            e_atr = _local_atr(candles, e)
            if e_atr is None or r / e_atr < min_r_atr:
                continue
            out.append(_tag_all(
                Ev("vwap", symbol, timeframe.value, direction, e - max_wait, e, entry, stop, r),
                candles, horizons, targets, target_horizon,
                agg_window, control_at, strong_floor,
            ))
            for _ in range(random_reps):
                ri = rng.randrange(50, len(candles) - max_h - 1)
                e2 = candles[ri].close
                s2 = e2 - r if bull else e2 + r
                out.append(_tag_all(
                    Ev("rand-vwap", symbol, timeframe.value, direction, ri, ri, e2, s2, r),
                    candles, horizons, targets, target_horizon,
                    agg_window, control_at, strong_floor,
                ))
    for ev in out:
        ev.vwap_candles = accumulated.get(candles[ev.entry_index].timestamp)
        _tag_ema(ev, candles, ema_line, vwap_at)
        _tag_approach(ev, candles)
    return out, counts


def _row(rows: Sequence[Ev], horizons: Sequence[int], targets: Sequence[float]) -> str:
    cells = []
    for h in horizons:
        fav = [e.favor[h] for e in rows if h in e.favor]
        mfe = [e.mfe[h] for e in rows if h in e.mfe]
        mae = [e.mae[h] for e in rows if h in e.mae]
        ratio = (
            statistics.mean(mfe) / statistics.mean(mae)
            if mae and statistics.mean(mae) > 0
            else float("nan")
        )
        cells.append(
            f"{(sum(fav) / len(fav) * 100 if fav else 0):>7.0f}% {ratio:>7.2f}"
        )
    for k in targets:
        hits = [e.hit[k] for e in rows if k in e.hit]
        cells.append(f"{(sum(hits) / len(hits) * 100 if hits else 0):>6.0f}%")
    return " ".join(cells)


def _binom_p(hits: int, n: int, p0: float) -> float:
    """One-sided P(X >= hits) under the control's own rate.

    With a sample this small the point estimate is nearly meaningless on its
    own: the question is whether the gap over the direction-matched control
    survives the noise of a few dozen trades.
    """
    if n == 0 or p0 <= 0.0:
        # a control with no hits at all makes the test degenerate (any single
        # hit reads as p=0); that is a sample-size problem, not evidence.
        return float("nan")
    if n > 1000:
        # the exact sum overflows well before this; the normal approximation is
        # accurate to several decimals once n*p0 and n*(1-p0) are both >> 10.
        sigma = math.sqrt(n * p0 * (1 - p0))
        if sigma == 0:
            return float("nan")
        z = (hits - 0.5 - n * p0) / sigma
        return 0.5 * math.erfc(z / math.sqrt(2))
    total = 0.0
    for k in range(hits, n + 1):
        total += math.comb(n, k) * p0**k * (1 - p0) ** (n - k)
    return min(1.0, total)


def significance(events: Sequence[Ev], targets: Sequence[float]) -> None:
    print("\nsignificance of the gap over the matched control (one-sided binomial)")
    for arm in ("vwap", "ob", "ob-lines", "ob-body", "eql"):
        rows = [e for e in events if e.arm == arm]
        ctrl = [e for e in events if e.arm == f"rand-{arm}"]
        if not rows or not ctrl:
            continue
        for k in targets:
            a = [e.hit[k] for e in rows if k in e.hit]
            c = [e.hit[k] for e in ctrl if k in e.hit]
            if not a or not c:
                continue
            p0 = sum(c) / len(c)
            pv = _binom_p(sum(a), len(a), p0)
            flag = (
                "control empty" if pv != pv
                else "significant" if pv < 0.05 else "NOT significant"
            )
            print(
                f"  {arm:>4} {k}R vs random: {sum(a)}/{len(a)} = {sum(a) / len(a):.0%}"
                f"  vs {p0:.0%}   p={pv:.4f}  ({flag})"
            )
            if arm == "vwap":
                continue
            plc = [e.hit[k] for e in events if e.arm == "vwap" and k in e.hit]
            if not plc:
                continue
            p1 = sum(plc) / len(plc)
            pv1 = _binom_p(sum(a), len(a), p1)
            f1 = (
                "control empty" if pv1 != pv1
                else "significant" if pv1 < 0.05 else "NOT significant"
            )
            print(
                f"  {arm:>4} {k}R vs placebo: {sum(a)}/{len(a)} = {sum(a) / len(a):.0%}"
                f"  vs {p1:.0%}   p={pv1:.4f}  ({f1})"
            )


def report(
    events: Sequence[Ev],
    horizons: Sequence[int],
    targets: Sequence[float],
    counts: dict[str, int],
) -> None:
    print("\nfunnel (how many of each stage survived)")
    print(f"  vwap: {'':>5}                 "
          f"  ->{counts.get('vwap-entries', 0):>5} pinbar reclaims"
          f"  ->{len([e for e in events if e.arm == 'vwap']):>5} measurable")
    for arm in ("ob", "ob-lines", "ob-body", "eql"):
        print(
            f"  {arm:>4}: {counts.get(f'{arm}-episodes', 0):>5} test episodes"
            f"  ->{counts.get(f'{arm}-entries', 0):>5} pinbar reclaims"
            f"  ->{len([e for e in events if e.arm == arm]):>5} measurable"
        )

    head = " ".join(f"{'favor@' + str(h):>7} {'MFE/MAE':>7}" for h in horizons)
    tgt = " ".join(f"{str(k) + 'R':>6}" for k in targets)
    print(f"\n{'arm':>10} {'n':>5} {head} {tgt}")
    for arm in (
        "vwap", "rand-vwap", "ob", "rand-ob",
        "ob-lines", "rand-ob-lines", "ob-body", "rand-ob-body",
        "eql", "rand-eql",
    ):
        rows = [e for e in events if e.arm == arm]
        if rows:
            print(f"{arm:>10} {len(rows):>5} {_row(rows, horizons, targets)}")

    print(f"\nper symbol/timeframe\n{'combo':>18} {'arm':>9} {'n':>5} {head} {tgt}")
    combos = sorted({(e.symbol, e.timeframe) for e in events})
    for sym, tf in combos:
        for arm in ("ob", "rand-ob", "ob-lines", "rand-ob-lines",
                    "ob-body", "rand-ob-body", "eql", "rand-eql"):
            rows = [e for e in events if e.arm == arm and e.symbol == sym and e.timeframe == tf]
            if rows:
                print(
                    f"{sym + ' ' + tf:>18} {arm:>9} {len(rows):>5} "
                    f"{_row(rows, horizons, targets)}"
                )


def export_events(events: Sequence[Ev], path: str) -> None:
    """Write one row per real (non-random) entry, for the walk-forward study.

    The measurement here answers "is the edge there"; walk-forward answers "is
    it there in *every stretch of time*, or only in the one this sample
    happened to cover". That is a different question and needs the trades
    themselves, dated, not the aggregate table.
    """
    rows = [
        {
            "timestamp": e.entry_timestamp,
            "symbol": e.symbol,
            "timeframe": e.timeframe,
            "arm": e.arm,
            "direction": e.direction.value,
            "agg_ratio": e.agg_ratio,
            "r_outcome": e.r_outcome,
            "r_grid": {str(k): v for k, v in e.r_grid.items()},
            "first_test": e.first_test,
            # R as a fraction of entry price: what decides whether an edge
            # measured in R survives a round-trip fee measured in basis points.
            "r_pct": e.r / e.entry,
            "r_atr": e.r_atr,
            "atr_pct": e.atr_pct,
            "vwap_candles": e.vwap_candles,
            "trigger_line": e.trigger_line,
            "pinbar_grade": e.pinbar_grade,
            "block_low": e.block_low,
            "block_high": e.block_high,
            "departure_atr": e.departure_atr,
            "departure_candles": e.departure_candles,
            "block_age_candles": e.block_age_candles,
            "test_extreme": e.test_extreme,
            "approach_atr": e.approach_atr,
            "approach_candles": e.approach_candles,
            "rejection_frac": e.rejection_frac,
            "ema9": e.ema9,
            "ema_side": e.ema_side,
            "ema_reclaimed": e.ema_reclaimed,
            "ema_is_far": e.ema_is_far,
            "ema_gap_atr": e.ema_gap_atr,
            "ema_slope_with": e.ema_slope_with,
            "r_manage": e.r_manage,
        }
        for e in events
        if not e.arm.startswith("rand")
        and e.agg_ratio is not None
        and e.r_outcome is not None
    ]
    rows.sort(key=lambda r: r["timestamp"])
    Path(path).write_text(json.dumps(rows))
    print(f"\nexported {len(rows)} dated trades -> {path}")


def control_report(
    events: Sequence[Ev], horizons: Sequence[int], targets: Sequence[float]
) -> None:
    """Each arm sliced by the control reading, against the same slice of control.

    The random arms are tagged with the control state at *their* entry too, so
    a filtered arm is compared with a control filtered identically -- otherwise
    the filter's own selection of market conditions would be credited to the
    setup.
    """
    covered = [e for e in events if e.ctrl_aligned is not None]
    print(f"\ncontrol (CVD x OI) conditioning -- {len(covered)}/{len(events)} "
          f"entries inside Binance's ~30-day OI retention")
    if not covered:
        return
    head = " ".join(f"{'favor@' + str(h):>7} {'MFE/MAE':>7}" for h in horizons)
    tgt = " ".join(f"{str(k) + 'R':>6}" for k in targets)
    print(f"{'arm':>16} {'n':>6} {head} {tgt}")
    for base in ("vwap", "ob", "eql"):
        for label, pick in (
            ("all", lambda e: True),
            ("|with", lambda e: e.ctrl_with),
            ("|strong", lambda e: e.ctrl_strong),
            ("|against", lambda e: e.ctrl_with is False),
            ("|aligned", lambda e: e.ctrl_aligned),
            ("|unwind", lambda e: e.ctrl_unwind),
        ):
            for prefix in (base, f"rand-{base}"):
                rows = [
                    e for e in covered if e.arm == prefix and pick(e)  # type: ignore[no-untyped-call]
                ]
                if rows:
                    name = f"{prefix}{'' if label == 'all' else label}"
                    print(f"{name:>16} {len(rows):>6} {_row(rows, horizons, targets)}")
        print()


def confound_report(
    events: Sequence[Ev], horizons: Sequence[int], targets: Sequence[float]
) -> None:
    """Is the control slice re-measuring the entry candle, or adding to it?

    The entry is a pinbar: a long wick against the trade, closing with it. Such
    a candle plausibly carries taker delta *opposing* its own close -- sellers
    hitting into the wick of a bullish reclaim -- and that candle feeds the
    window `control_score` is measured over. So the `against` slice may be
    selecting "a real absorption pinbar", a fact about candle anatomy that
    needs no open interest at all.

    Two decompositions settle it. First `delta_against`, the entry candle's own
    delta and nothing else: if that alone reproduces the effect, the control
    layer contributed nothing. Then the quadrant taken apart into its two axes,
    aggression and OI: the control layer only earns its place if the OI axis
    moves the reading at a fixed aggression.
    """
    head = " ".join(f"{'favor@' + str(h):>7} {'MFE/MAE':>7}" for h in horizons)
    tgt = " ".join(f"{str(k) + 'R':>6}" for k in targets)

    print("\nA) the entry candle's OWN delta -- no OI, no window")
    print(f"{'arm':>20} {'n':>6} {head} {tgt}")
    for base in ("vwap", "ob", "eql"):
        for prefix in (base, f"rand-{base}"):
            for label, pick in (
                ("|d-against", lambda e: e.delta_against is True),
                ("|d-with", lambda e: e.delta_against is False),
            ):
                rows = [e for e in events if e.arm == prefix and pick(e)]  # type: ignore[no-untyped-call]
                if rows:
                    print(f"{prefix + label:>20} {len(rows):>6} {_row(rows, horizons, targets)}")
        print()

    covered = [e for e in events if e.agg_bull is not None]
    if not covered:
        return
    print(f"B) the quadrant's two axes apart ({len(covered)} non-FLAT entries)"
          f"\n{'arm':>29} {'n':>6} {head} {tgt}")
    for base in ("vwap", "ob", "eql"):
        for agg_against in (True, False):
            for oi in (True, False):
                a = "agg-against" if agg_against else "agg-with"
                o = "OI-up" if oi else "OI-down"
                # the random arm is tagged with the quadrant at *its* own entry,
                # so each cell is read against a control drawn under the same
                # market condition rather than against the pooled baseline.
                for prefix in (base, f"rand-{base}"):
                    rows = [
                        e for e in covered
                        if e.arm == prefix
                        and ((e.agg_bull is not (e.direction is BULL)) is agg_against)
                        and e.oi_up is oi
                    ]
                    if len(rows) >= 20:
                        print(f"{prefix + '|' + a + '|' + o:>29} {len(rows):>6} "
                              f"{_row(rows, horizons, targets)}")
        print()


def control_consistency(events: Sequence[Ev], target: float) -> None:
    """Is the with/against split the same story in every symbol, or one symbol?

    Slicing three arms five ways is fifteen looks at the same data, so a single
    striking cell is what one *expects* to find. What a real effect owes is
    consistency: the same sign in symbol after symbol, and in both timeframes.
    """
    print(f"\nwith/against consistency at {target}R (hit% against, minus with)")
    print(f"{'combo':>18} " + " ".join(f"{a:>14}" for a in ("vwap", "ob", "eql")))
    combos = sorted({(e.symbol, e.timeframe) for e in events})
    tally: dict[str, list[int]] = {a: [0, 0] for a in ("vwap", "ob", "eql")}
    for sym, tf in combos:
        cells = []
        for arm in ("vwap", "ob", "ob-lines", "ob-body", "eql"):
            rows = [e for e in events if e.arm == arm and e.symbol == sym and e.timeframe == tf]
            w = [e.hit[target] for e in rows if e.ctrl_with is True and target in e.hit]
            a = [e.hit[target] for e in rows if e.ctrl_with is False and target in e.hit]
            if len(w) < 5 or len(a) < 5:
                cells.append(f"{'--':>14}")
                continue
            delta = sum(a) / len(a) - sum(w) / len(w)
            tally[arm][0 if delta > 0 else 1] += 1
            cells.append(f"{delta:>+9.0%} ({len(a):>2})")
        print(f"{sym + ' ' + tf:>18} " + " ".join(cells))
    print("\n  combos where `against` beat `with`:")
    for arm, (up, down) in tally.items():
        print(f"    {arm:>5}: {up}/{up + down}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    p.add_argument("--timeframes", nargs="+", default=["5m", "15m"])
    p.add_argument("--limit", type=int, default=1200,
                   help="candles per series; above 1500 needs --deep")
    p.add_argument("--export", default=None,
                   help="write the dated trades to this JSON, for walk-forward")
    p.add_argument("--control", action="store_true",
                   help="keep the real futures provider so market_control is "
                        "populated (OI reaches back ~30 days only)")
    p.add_argument("--deep", action="store_true",
                   help="paginate past the endpoint's 1500-candle cap "
                        "(research provider; skips the futures state)")
    p.add_argument("--horizons", nargs="+", type=int, default=[5, 10, 20, 40])
    p.add_argument("--targets", nargs="+", type=float, default=[2.0, 3.0])
    p.add_argument("--target-horizon", type=int, default=40,
                   help="candles the target has to be reached within")
    p.add_argument("--max-wait", type=int, default=20,
                   help="candles allowed between the liquidity test and the entry")
    p.add_argument("--merge-gap", type=int, default=3,
                   help="candles of separation that still count as one test episode")
    p.add_argument("--wick-frac", type=float, default=0.5)
    p.add_argument("--body-frac", type=float, default=0.35)
    p.add_argument("--min-r-atr", type=float, default=0.05,
                   help="scan-time floor on R, in the local ATR(14) at the "
                        "entry -- `app.block_reclaim.MIN_DISTANCE_ATR`, below "
                        "which the reading is the tick grid rather than the "
                        "two levels. A cost-driven floor is a reader's choice: "
                        "pass it to vwap_exit_grid.py --min-r-atr instead, "
                        "where moving it does not need a re-scan")
    p.add_argument("--eql-lag", type=int, default=EQL_CONFIRM_LAG)
    p.add_argument("--vwap-anchor",
                   choices=["session", "week", "month", "event"], default=None,
                   help="recompute the VWAP with this anchor instead of the "
                        "per-timeframe production default. `event` restarts the "
                        "accumulation at each non-provisional trend flip -- a "
                        "population named by structure rather than by the clock")
    p.add_argument("--fresh-ob", action="store_true",
                   help="only the first visit to each order block counts")
    p.add_argument("--htf", nargs="*", default=None,
                   help="higher timeframe per entry timeframe (e.g. --htf 1h 4h); "
                        "omit for no filter")
    p.add_argument("--htf-warmup", type=int, default=400)
    p.add_argument("--random-reps", type=int, default=3)
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args()

    rng = random.Random(args.seed)
    provider = PaginatedFuturesProvider() if args.deep else None
    futures_provider = NoFuturesProvider() if args.deep and not args.control else None
    if args.deep and args.htf:
        raise SystemExit(
            "--htf dates the higher-timeframe trend by replaying the pipeline "
            "once per candle, which is quadratic; not available with --deep"
        )
    events: list[Ev] = []
    counts: dict[str, int] = {}
    htf_map: dict[str, TimeFrame] = {}
    if args.htf:
        if len(args.htf) != len(args.timeframes):
            raise SystemExit("--htf needs one timeframe per --timeframes entry")
        htf_map = {t: TimeFrame(h) for t, h in zip(args.timeframes, args.htf, strict=True)}
    for symbol in args.symbols:
        for tf in (TimeFrame(t) for t in args.timeframes):
            steps = None
            if tf.value in htf_map:
                htf = htf_map[tf.value]
                try:
                    steps = htf_trend_steps(
                        symbol, htf, limit=args.limit, warmup=args.htf_warmup
                    )
                except Exception as exc:  # noqa: BLE001 - sweep
                    print(f"  ! {symbol} {htf.value} htf: {exc}")
                    continue
                print(f"  {symbol} {tf.value}: HTF {htf.value}, {len(steps)} trend steps")
            try:
                evs, cs = run_combo(
                    symbol, tf,
                    provider=provider, futures_provider=futures_provider,
                    limit=args.limit, horizons=args.horizons, targets=args.targets,
                    target_horizon=args.target_horizon,
                    max_wait=args.max_wait, merge_gap=args.merge_gap,
                    wick_frac=args.wick_frac, body_frac=args.body_frac,
                    min_r_atr=args.min_r_atr, eql_lag=args.eql_lag,
                    fresh_ob=args.fresh_ob,
                    vwap_anchor=args.vwap_anchor,
                    htf_steps=steps,
                    random_reps=args.random_reps, rng=rng,
                )
            except (DataProviderError, ValidationError) as exc:
                # One delisted symbol, or one exchange row where taker volume
                # exceeds total volume, must not sink a 43-symbol sweep. Both
                # are skipped and named rather than repaired: a bad row is the
                # venue's, and inventing a value for it would put fabricated
                # flow into a measurement.
                print(f"  ! {symbol} {tf.value}: {type(exc).__name__}: {exc}")
                continue
            events.extend(evs)
            for k, v in cs.items():
                counts[k] = counts.get(k, 0) + v
            n = len([e for e in evs if not e.arm.startswith("rand")])
            span = cs.get("span", "")
            print(f"  {symbol} {tf.value}: {n} setups {span}")

    report(events, args.horizons, args.targets, counts)
    significance(events, args.targets)
    control_report(events, args.horizons, args.targets)
    control_consistency(events, min(args.targets))
    confound_report(events, args.horizons, args.targets)
    if args.export:
        export_events(events, args.export)


if __name__ == "__main__":
    main()
