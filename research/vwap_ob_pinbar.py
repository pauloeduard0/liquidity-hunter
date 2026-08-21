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
import math
import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from liquidity_hunter.app import load_dashboard_data
from liquidity_hunter.app.dashboard_data import (
    _run_internal_structure,
    default_ohlcv_provider,
)
from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.core.domain.enums import (
    LiquidityZoneType,
    MarketControlSide,
    MarketDirection,
    OIRegime,
    POIZoneKind,
    POIZoneStatus,
)
from liquidity_hunter.data.exceptions import DataProviderError
from liquidity_hunter.data.providers.base import FuturesDataProvider, OHLCVProvider
from liquidity_hunter.indicators import volume_delta
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


def _atr(candles: Sequence[Candle]) -> float:
    trs = [
        max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
        for p, c in zip(candles, candles[1:], strict=False)
    ]
    return statistics.mean(trs) if trs else 0.0


def _measure(
    ev: Ev,
    candles: Sequence[Candle],
    horizons: Sequence[int],
    targets: Sequence[float],
    target_horizon: int,
) -> Ev:
    i0 = ev.entry_index
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


def _reclaims(candle: Candle, vwap_value: float, *, bull: bool) -> bool:
    """The wick pierces the VWAP and the close lands back on the other side."""
    return (
        candle.low < vwap_value <= candle.close
        if bull
        else candle.high > vwap_value >= candle.close
    )


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


def _ob_triggers(
    data, candles, vwap_at, *, bull: bool, fresh_only: bool = False, merge_gap: int = 3
) -> list[int]:
    """Candles that test an order block on the far side of the VWAP.

    With ``fresh_only``, only a block's **first** visit counts. A zone price
    has already traded back into has had its resting orders worked; the SMC
    claim is about the untouched one, so counting every later return measures
    a different thing. Freshness is judged against *any* prior touch, not only
    the ones that qualified as triggers: a visit that happened above the VWAP
    still spent the block.
    """
    want = BULL if bull else BEAR
    zones = [
        z
        for z in data.poi_zones
        if z.kind is POIZoneKind.ORDER_BLOCK and z.direction is want
    ]
    hits: set[int] = set()
    for z in zones:
        first: int | None = None
        for i, c in enumerate(candles):
            if z.created_at > c.timestamp:
                continue  # the block did not exist yet
            if z.status is POIZoneStatus.INVALIDATED and (
                z.invalidated_at is not None and z.invalidated_at <= c.timestamp
            ):
                break  # already spent
            if not (c.low <= z.price_high and c.high >= z.price_low):
                continue
            if first is None:
                first = i
            elif fresh_only and i > first + merge_gap:
                break  # a later return is no longer a fresh block
            v = vwap_at.get(c.timestamp)
            if v is None:
                continue
            if (c.low < v) if bull else (c.high > v):
                hits.add(i)
    return sorted(hits)


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
    htf_steps: Sequence[tuple[datetime, MarketDirection]] | None,
    random_reps: int,
    rng: random.Random,
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
    if len(candles) < 200 or data.vwap is None:
        return [], {}
    vwap_at = {p.timestamp: p.value for p in data.vwap.points}
    idx_of = {c.timestamp: i for i, c in enumerate(candles)}
    control_at = {}
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
    atr = _atr(candles)
    min_r = min_r_atr * atr
    max_h = max(horizons)

    out: list[Ev] = []
    counts: dict[str, int] = {}
    counts["span-days"] = int(
        (candles[-1].timestamp - candles[0].timestamp).total_seconds() // 86400
    )

    for arm, collect in (("ob", _ob_triggers), ("eql", _eql_triggers)):
        for bull in (True, False):
            kwargs = (
                {"lag": eql_lag}
                if arm == "eql"
                else {"fresh_only": fresh_ob, "merge_gap": merge_gap}
            )
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
                    if r < min_r:
                        break
                    out.append(_tag_control(_tag_delta(_measure(
                        Ev(arm, symbol, timeframe.value, direction, start, e, entry, stop, r),
                        candles, horizons, targets, target_horizon,
                    ), candles), control_at, strong_floor))
                    for _ in range(random_reps):
                        ri = rng.randrange(50, len(candles) - max_h - 1)
                        e2 = candles[ri].close
                        s2 = e2 - r if bull else e2 + r
                        out.append(_tag_control(_tag_delta(_measure(
                            Ev(f"rand-{arm}", symbol, timeframe.value, direction,
                               ri, ri, e2, s2, r),
                            candles, horizons, targets, target_horizon,
                        ), candles), control_at, strong_floor))
                    break  # one entry per test episode

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
            if r < min_r:
                continue
            out.append(_tag_control(_tag_delta(_measure(
                Ev("vwap", symbol, timeframe.value, direction, e - max_wait, e, entry, stop, r),
                candles, horizons, targets, target_horizon,
            ), candles), control_at, strong_floor))
            for _ in range(random_reps):
                ri = rng.randrange(50, len(candles) - max_h - 1)
                e2 = candles[ri].close
                s2 = e2 - r if bull else e2 + r
                out.append(_tag_control(_tag_delta(_measure(
                    Ev("rand-vwap", symbol, timeframe.value, direction, ri, ri, e2, s2, r),
                    candles, horizons, targets, target_horizon,
                ), candles), control_at, strong_floor))
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
    for arm in ("vwap", "ob", "eql"):
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
    for arm in ("ob", "eql"):
        print(
            f"  {arm:>4}: {counts.get(f'{arm}-episodes', 0):>5} test episodes"
            f"  ->{counts.get(f'{arm}-entries', 0):>5} pinbar reclaims"
            f"  ->{len([e for e in events if e.arm == arm]):>5} measurable"
        )

    head = " ".join(f"{'favor@' + str(h):>7} {'MFE/MAE':>7}" for h in horizons)
    tgt = " ".join(f"{str(k) + 'R':>6}" for k in targets)
    print(f"\n{'arm':>10} {'n':>5} {head} {tgt}")
    for arm in ("vwap", "rand-vwap", "ob", "rand-ob", "eql", "rand-eql"):
        rows = [e for e in events if e.arm == arm]
        if rows:
            print(f"{arm:>10} {len(rows):>5} {_row(rows, horizons, targets)}")

    print(f"\nper symbol/timeframe\n{'combo':>18} {'arm':>9} {'n':>5} {head} {tgt}")
    combos = sorted({(e.symbol, e.timeframe) for e in events})
    for sym, tf in combos:
        for arm in ("ob", "rand-ob", "eql", "rand-eql"):
            rows = [e for e in events if e.arm == arm and e.symbol == sym and e.timeframe == tf]
            if rows:
                print(
                    f"{sym + ' ' + tf:>18} {arm:>9} {len(rows):>5} "
                    f"{_row(rows, horizons, targets)}"
                )


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
          f"\n{'arm':>24} {'n':>6} {head} {tgt}")
    for base in ("vwap", "ob", "eql"):
        for agg_against in (True, False):
            for oi in (True, False):
                rows = [
                    e for e in covered
                    if e.arm == base
                    and ((e.agg_bull is not (e.direction is BULL)) is agg_against)
                    and e.oi_up is oi
                ]
                if len(rows) >= 20:
                    a = "agg-against" if agg_against else "agg-with"
                    o = "OI-up" if oi else "OI-down"
                    print(f"{base + '|' + a + '|' + o:>24} {len(rows):>6} "
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
        for arm in ("vwap", "ob", "eql"):
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
    p.add_argument("--min-r-atr", type=float, default=0.25)
    p.add_argument("--eql-lag", type=int, default=EQL_CONFIRM_LAG)
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
                    fresh_ob=args.fresh_ob, htf_steps=steps,
                    random_reps=args.random_reps, rng=rng,
                )
            except DataProviderError as exc:
                # one delisted or renamed symbol must not sink the sweep
                print(f"  ! {symbol} {tf.value}: {exc}")
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


if __name__ == "__main__":
    main()
