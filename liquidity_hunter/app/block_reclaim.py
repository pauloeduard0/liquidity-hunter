"""Detect VWAP reclaims that follow a test of an order block.

Composition-level, like `LiquidityHuntEngine` and the grab stream: the
reading crosses `liquidity` (order blocks), `indicators` (the VWAP), and the
candles themselves, so it belongs above all three rather than inside any of
them.

This is the production form of the rule measured in
`research/vwap_ob_pinbar.py`, and the two are deliberately the same rule --
the detector emits every reclaim it finds and records `r_atr` on each,
leaving the threshold to the reader. Anything else would make the layer and
the measurement drift apart, and the measurement is the only reason the
layer exists. Changing the rule here means re-running that study: export the
dated entries, then `research/vwap_exit_grid.py --max-r-atr N` against the
placebo, on symbols held out of the change.
"""

from collections.abc import Sequence
from datetime import datetime
from statistics import fmean

from liquidity_hunter.core.domain import (
    BlockReclaim,
    Candle,
    MarketDirection,
    POIZone,
    POIZoneKind,
    TimeFrame,
    VWAPSeries,
)

#: A visit to a block is one test even when price spends several candles
#: inside it; candles this far apart still count as the same visit.
MERGE_GAP_CANDLES = 3
#: How long after the visit a reclaim still belongs to it. A reader marking
#: trades off the chart does not carry this clock: on BTCUSDT M15 the block was
#: touched at 11:00 and the rejection printed at 17:45, 25 candles later, which
#: this window cuts at 20. Whether the tie really expires is an empirical
#: question, so it is a parameter (`detect_block_reclaims(max_wait_candles=)`)
#: measured in `research/wait_window.py` rather than a number argued about.
MAX_WAIT_CANDLES = 20
#: The reclaiming candle's shape: a long wick against the close, little body.
MIN_WICK_FRACTION = 0.5
MAX_BODY_FRACTION = 0.35
#: `legacy` caps the body but says nothing about the nose, so a *doji* clears
#: it: tail 0.58, body 0.026, and 0.39 of opposing wick nobody asks about --
#: buyers and sellers finishing level, read as a rejection. Raising the tail
#: floor is what rules that out without adding a fourth threshold: at 0.65 the
#: nose can be at most 0.35 by construction. Left at 0.5 here because every
#: measurement of this layer was run at 0.5 and the union's out-of-sample
#: Sharpe rests mostly on `legacy`; passed as 0.65 by the deep-stop study,
#: through `detect_block_reclaims(min_tail_fraction=...)`.
STRICT_WICK_FRACTION = 0.65
#: Local window for the volatility `r_atr` is normalized by.
ATR_PERIOD = 14
#: Reclaims closer than this to their test extreme are dropped: at that
#: distance the reading is dominated by the tick grid rather than by where
#: the two levels sit.
MIN_DISTANCE_ATR = 0.05


def _rejects_ema(
    candle: Candle, ema_value: float, vwap_value: float, *, bullish: bool
) -> bool:
    """The pinbar found the fast line while price held the VWAP side.

    The second route into the same observation. It only counts while the close
    is on the working side of the VWAP: a wick off the 9 *underneath* the
    average is a bounce inside the supply the setup is waiting to see spent,
    which is a different picture entirely.
    """
    holds = (candle.close > vwap_value) if bullish else (candle.close < vwap_value)
    if not holds:
        return False
    return (
        candle.low < ema_value <= candle.close
        if bullish
        else candle.high > ema_value >= candle.close
    )


def _body_clears_vwap(candle: Candle, vwap_value: float, *, bullish: bool) -> bool:
    """The trigger candle's whole body on the working side of the VWAP.

    The wick belongs to the line being tested; the body does not. A body that
    straddles the VWAP tested no level at all -- it changed sides inside its
    own candle, and the two populations the setup is about were still mixed
    when it closed. Reading that as a rejection is reading a crossing.

    The edge measured is `min(open, close)` rather than the open, so a pinbar
    whose small body closed *down* -- a real shape, and the one that motivated
    the rule -- is judged by the bottom of its body like any other.

    Off by default (`require_body_clears_vwap`): it changes which trades exist,
    and a change to the rule is a change to the measured object.
    """
    edge = min(candle.open, candle.close) if bullish else max(candle.open, candle.close)
    return edge > vwap_value if bullish else edge < vwap_value


def _ema_crossed(ema_value: float, vwap_value: float, *, bullish: bool) -> bool:
    """Whether the fast line has crossed the average in the reclaim's favour.

    A *state*, not an event: it asks where the two lines sit, not whether they
    crossed on this candle. Measured over every reclaim it separates 26.8% from
    17.9% on the 2R hit rate -- but inside the tight-stop population the layer
    actually reports it already holds 97% of the time, because a stop that
    close to the test means the recovery was fast enough to drag the 9 through
    the average on its own. It gates the EMA route, where it is not redundant.
    """
    return (ema_value > vwap_value) if bullish else (ema_value < vwap_value)


def _is_reclaim(candle: Candle, vwap_value: float, *, bullish: bool) -> bool:
    """The wick crossed the VWAP and the body closed back on the other side."""
    if bullish:
        return candle.low < vwap_value <= candle.close
    return candle.high > vwap_value >= candle.close


#: The golden rule: a level-1 pinbar is two thirds tail, with the tolerance
#: traders already work with. The nose -- the wick on the *far* side -- is
#: capped in both grades, because that is the part saying price was pushed back
#: the other way before the candle closed.
L1_TAIL_FRACTION = 0.65
NOSE_MAX_FRACTION = 0.15
#: A level-2 pinbar trades tail for body: a real body, a decent tail, almost no
#: nose. Proportions read from a trader rather than fitted here.
L2_BODY_FRACTION = 1.0 / 3.0
L2_TAIL_FRACTION = 0.20


def pinbar_grades(
    candle: Candle, *, bullish: bool,
    min_tail_fraction: float = MIN_WICK_FRACTION,
) -> frozenset[str]:
    """Which pinbar definitions this candle satisfies: `legacy`, `l1`, `l2`.

    Three, kept apart rather than collapsed into one threshold, because they
    are different candles wearing one name. `legacy` is what this layer shipped
    with -- tail >= 0.50, body <= 0.35, nose unconstrained -- and it is neither
    of the others: looser than the golden rule on the tail, and silent about
    the nose. `l1` is the golden rule. `l2` is body-heavy with a small nose: a
    candle that spent most of its range on a body and still left a tail
    underneath.

    **None of the three asks which way the candle closed.** The body is
    `abs(close - open)`, so a red candle satisfies the *bullish* `l2`, and that
    is deliberate rather than an oversight -- it was read as one (the earlier
    wording here said `l2` "closed most of the way through its own range",
    which is a claim about direction) and then measured. Requiring the close to
    agree, on the gated population where the layer is used: every grade
    (`cor=all`) drops the 2R hit rate from 55.1% to 49.5% on the search half
    and 52.4% to 47.9% on the holdout, cutting 58% of the trades -- a red
    hammer with a long tail is an ordinary rejection and what it reports is the
    tail. Requiring it of `l2` alone is a tie: 53.7% against 55.1% on search,
    52.4% against 52.4% on the holdout, for 18% fewer trades. A heavy body
    closing *against* the direction at a defended level is absorption, which is
    a reading, not a defect. `research/pinbar_color.py`, wired through
    `detect_block_reclaims(require_pinbar_color=...)`, off by default.

    Accepting the **union** is measured, not assumed. Over 22 walk-forward
    folds it pools the best out-of-sample Sharpe of 30 declared candidates,
    **7.90 against the legacy trigger's 6.75**, and it beats every one of its
    own subsets -- `legacy` alone 7.24, `l2` alone 4.56, `l1` 4.25 -- while
    carrying 43% more trades at the same hit rate. The subsets are named here
    so a reader can observe which grade fired; filtering on one is what the
    measurement rules out. See `docs/block_reclaim.md`.

    `min_tail_fraction` raises `legacy`'s tail floor (`l1` and `l2` cap the
    nose themselves and are untouched). The default keeps the measured
    behaviour; `STRICT_WICK_FRACTION` is the doji cut described above.
    """
    span = candle.high - candle.low
    if span <= 0:
        return frozenset()
    body = abs(candle.close - candle.open)
    tail = (
        min(candle.close, candle.open) - candle.low
        if bullish
        else candle.high - max(candle.close, candle.open)
    )
    nose = (
        candle.high - max(candle.close, candle.open)
        if bullish
        else min(candle.close, candle.open) - candle.low
    )
    out: set[str] = set()
    if tail >= min_tail_fraction * span and body <= MAX_BODY_FRACTION * span:
        out.add("legacy")
    if tail >= L1_TAIL_FRACTION * span and nose <= NOSE_MAX_FRACTION * span:
        out.add("l1")
    if (
        body >= L2_BODY_FRACTION * span
        and tail >= L2_TAIL_FRACTION * span
        and nose <= NOSE_MAX_FRACTION * span
    ):
        out.add("l2")
    return frozenset(out)


def pinbar_color_agrees(candle: Candle, *, bullish: bool) -> bool:
    """The trigger candle closed in the direction it is being read as.

    `pinbar_grades` measures the body as `abs(close - open)` and never asks
    which way it closed, so a **red** candle with a big body and a tail
    underneath satisfies the *bullish* `l2`. For `legacy` and `l1` that barely
    matters -- both cap the body at 35% and 15% of the range, so there is
    little body for a colour to be wrong about. `l2` is the opposite: it
    requires a body of at least a third of the range, which is exactly the
    grade where the close's direction carries the meaning its own docstring
    claims ("closed most of the way through its own range").

    Kept as a separate predicate rather than folded into `pinbar_grades`
    because the union of the three grades was validated out-of-sample with
    this behaviour inside it: some of that edge may be coming from the
    wrong-coloured candles, and finding out is a measurement, not an
    assumption. Wired through `require_pinbar_color`, off by default.
    """
    return candle.close > candle.open if bullish else candle.close < candle.open


def surviving_grades(
    candle: Candle, grades: frozenset[str], *, bullish: bool, scope: str
) -> frozenset[str]:
    """`grades` after the colour rule at `scope` is applied.

    `"all"` drops every grade when the candle closed the wrong way. `"l2"`
    drops only `l2`, on the reasoning that the other two cap the body at 35%
    and 15% of the range: a red candle with a long tail beneath it is an
    ordinary hammer, and what it reports is the tail, not the close. `l2`
    requires a body of at least a third of the range, so there the close's
    direction is the reading.

    Returning the surviving set rather than a boolean matters for `"l2"`: a
    candle that qualified as both `legacy` and `l2` keeps the trade and loses
    only the label, so the arm is not silently a different trigger.
    """
    if scope not in ("all", "l2"):
        raise ValueError(f"unknown colour scope: {scope}")
    if pinbar_color_agrees(candle, bullish=bullish):
        return grades
    return frozenset() if scope == "all" else grades - {"l2"}


def _is_pinbar(candle: Candle, *, bullish: bool) -> bool:
    span = candle.high - candle.low
    if span <= 0:
        return False
    body = abs(candle.close - candle.open)
    wick = (
        min(candle.close, candle.open) - candle.low
        if bullish
        else candle.high - max(candle.close, candle.open)
    )
    return wick >= MIN_WICK_FRACTION * span and body <= MAX_BODY_FRACTION * span


def _local_atr(candles: list[Candle], index: int) -> float | None:
    """Mean true range over the window ending at `index`, inclusive."""
    start = max(1, index - ATR_PERIOD + 1)
    trs = [
        max(
            candles[i].high - candles[i].low,
            abs(candles[i].high - candles[i - 1].close),
            abs(candles[i].low - candles[i - 1].close),
        )
        for i in range(start, index + 1)
    ]
    return fmean(trs) if trs else None


def _rests_until(candles: list[Candle], zone: POIZone, *, bullish: bool) -> int:
    """The first index at which the block is no longer resting liquidity.

    `POIZone.invalidated_at` cannot answer this on its own. The POI queue
    retires the **oldest** zone of a side whenever any zone of that queue
    breaks (the indicator's `array.shift`), so the stamp records when the
    queue got around to this box rather than when price took it -- measured
    on BTCUSDT 1h, a block closed through on 10 Aug carried a 19 Aug stamp.
    Reading it as the break keeps a spent block alive for days, and every
    "test" of it in between has nobody positioned in it, which is the whole
    premise of the reading.

    So the break is searched for directly: the first candle to *close* beyond
    the far boundary, from the candle that **confirmed** the box forward (the
    anchor candle is chosen in hindsight, and price moving through that level
    before the MSB confirmed broke nothing -- there was no box there yet).
    The retirement stamp still bounds it: a box the queue removed is off the
    board, and price closing past that level afterwards takes nothing.

    Returns `len(candles)` while the block is still resting at the live edge.
    """
    level = zone.price_low if bullish else zone.price_high
    retired = len(candles)
    for i, candle in enumerate(candles):
        if candle.timestamp < zone.created_at:
            continue
        if zone.invalidated_at is not None and candle.timestamp > zone.invalidated_at:
            retired = i
            break
        if (candle.close < level) if bullish else (candle.close > level):
            return i
    return retired


def _visits(
    candles: list[Candle],
    zone: POIZone,
    vwap_at: dict[datetime, float],
    *,
    bullish: bool,
) -> list[tuple[int, int, bool]]:
    """Every visit to one block, as `(start, end, is_first_visit)`.

    Freshness is judged against *any* prior touch, not only the ones that
    happened on the far side of the VWAP: a visit that did not qualify here
    still worked the orders resting in the block.
    """
    death = _rests_until(candles, zone, bullish=bullish)
    touches: list[int] = []
    qualifying: list[int] = []
    for i, candle in enumerate(candles):
        if zone.created_at > candle.timestamp:
            continue  # the block did not exist yet
        if i >= death:
            break  # broken by a close, or retired off the board
        if candle.low > zone.price_high or candle.high < zone.price_low:
            continue
        touches.append(i)
        vwap_value = vwap_at.get(candle.timestamp)
        if vwap_value is None:
            continue
        # the test has to happen on the far side of the VWAP
        if (candle.low < vwap_value) if bullish else (candle.high > vwap_value):
            qualifying.append(i)

    if not qualifying:
        return []
    first_touch = touches[0]
    visits: list[tuple[int, int, bool]] = []
    for i in qualifying:
        if visits and i - visits[-1][1] <= MERGE_GAP_CANDLES:
            visits[-1] = (visits[-1][0], i, visits[-1][2])
        else:
            visits.append((i, i, i <= first_touch + MERGE_GAP_CANDLES))
    return visits


def detect_block_reclaims(
    candles: list[Candle],
    poi_zones: list[POIZone],
    vwap: VWAPSeries | None,
    *,
    symbol: str,
    timeframe: TimeFrame,
    ema: Sequence[float | None] | None = None,
    scan_from_visit_start: bool = False,
    require_body_clears_vwap: bool = False,
    require_pinbar_color: str | None = None,
    min_tail_fraction: float = MIN_WICK_FRACTION,
    max_wait_candles: int = MAX_WAIT_CANDLES,
) -> list[BlockReclaim]:
    """Every VWAP reclaim that followed a test of an order block.

    Emitted in candle order, at most one per candle and direction. Several
    blocks, or several visits to one block, routinely resolve at the same
    reclaim: they are one observation, and the one kept is the **nearest**
    test, since that is the level the reclaim was measured against. Measured
    over the study's window, collapsing them this way leaves the reading
    unchanged (51.9% against 51.9% on the search set) while keeping the
    farther, staler tests -- which measure worse -- out of it.

    Both directions; a bullish block is tested from above and reclaimed
    upward, a bearish one mirrors it.

    A reclaim landing on the series' last candle is marked `provisional`: on a
    live feed that candle is still forming, and neither half of the reading --
    the wick crossing the VWAP, the close landing back across it -- is settled
    until it closes. It is emitted rather than withheld, because a reader
    watching the live edge wants to see it, but it carries the flag so nothing
    replaying history counts a candle that may still become something else.

    Under `scan_from_visit_start` the trigger is searched from the candle the
    visit **began** on rather than from the one it ended on. A visit's end is
    not knowable when it happens -- it keeps absorbing later touches across
    `MERGE_GAP_CANDLES` -- so a window anchored on it moves as candles arrive,
    and a reclaim emitted live can be gone from the same series read later
    (measured: 6.5% of live reclaims vanish, `research/reclaim_stability.py`).
    The visit's *start* is settled by the past alone, so anchoring there is
    stable by construction. Off by default: it changes which trades exist, and
    a change to the rule is a change to the measured object.

    `require_pinbar_color` applies the colour rule `pinbar_grades` omits, which
    is what lets a red candle qualify as a bullish `l2`. `"all"` requires every
    grade to close the trade's way; `"l2"` requires it only of `l2`, where the
    body is large enough for the close's direction to be the reading (see
    `surviving_grades`). `None` (the default) is the shipped behaviour --
    either setting changes which trades exist.

    Under `require_body_clears_vwap` the trigger candle's whole body must sit
    on the working side of the VWAP (`_body_clears_vwap`). A rejected
    candidate does **not** end the visit: the scan keeps running to the end of
    the wait window, so a straddling candle is passed over rather than
    consuming the episode. Off by default, same contract.
    """
    if vwap is None or len(candles) < ATR_PERIOD + 2:
        return []
    vwap_at = {point.timestamp: point.value for point in vwap.points}
    ema_at: dict[datetime, float] = (
        {}
        if ema is None
        else {
            candle.timestamp: value
            for candle, value in zip(candles, ema, strict=False)
            if value is not None
        }
    )
    # How much the average had accumulated at each candle: the reading is
    # weaker against a VWAP that has barely started.
    accumulated: dict[datetime, int] = {}
    run_anchor: datetime | None = None
    run = 0
    for point in vwap.points:
        if point.anchor_timestamp != run_anchor:
            run_anchor, run = point.anchor_timestamp, 0
        run += 1
        accumulated[point.timestamp] = run

    candidates: list[BlockReclaim] = []
    for zone in poi_zones:
        if zone.kind is not POIZoneKind.ORDER_BLOCK:
            continue
        bullish = zone.direction is MarketDirection.BULLISH
        for start, end, first_test in _visits(candles, zone, vwap_at, bullish=bullish):
            scan_from = start if scan_from_visit_start else end
            wait_end = min(scan_from + max_wait_candles + 1, len(candles))
            for i in range(scan_from, wait_end):
                candle = candles[i]
                vwap_value = vwap_at.get(candle.timestamp)
                if vwap_value is None:
                    continue
                grades = pinbar_grades(
                    candle, bullish=bullish, min_tail_fraction=min_tail_fraction
                )
                if not grades:
                    continue
                if require_pinbar_color is not None:
                    grades = surviving_grades(
                        candle, grades, bullish=bullish, scope=require_pinbar_color
                    )
                    if not grades:
                        continue  # closed the other way: not this shape
                on_vwap = _is_reclaim(candle, vwap_value, bullish=bullish)
                ema_value = ema_at.get(candle.timestamp)
                on_ema = (
                    ema_value is not None
                    and _ema_crossed(ema_value, vwap_value, bullish=bullish)
                    and _rejects_ema(candle, ema_value, vwap_value, bullish=bullish)
                )
                if not (on_vwap or on_ema):
                    continue
                if require_body_clears_vwap and not _body_clears_vwap(
                    candle, vwap_value, bullish=bullish
                ):
                    continue  # a straddling body is a crossing, not a test
                line = "both" if (on_vwap and on_ema) else ("vwap" if on_vwap else "ema")
                window = candles[start : i + 1]
                extreme = (
                    min(c.low for c in window) if bullish else max(c.high for c in window)
                )
                distance = abs(candle.close - extreme)
                atr = _local_atr(candles, i)
                r_atr = distance / atr if atr else None
                if distance <= 0 or (r_atr is not None and r_atr < MIN_DISTANCE_ATR):
                    break
                candidates.append(
                    BlockReclaim(
                        symbol=symbol,
                        timeframe=timeframe,
                        timestamp=candle.timestamp,
                        direction=(
                            MarketDirection.BULLISH if bullish else MarketDirection.BEARISH
                        ),
                        reclaim_price=candle.close,
                        vwap_price=vwap_value,
                        block_price_low=zone.price_low,
                        block_price_high=zone.price_high,
                        block_timestamp=zone.ob_candle_timestamp,
                        test_start_timestamp=candles[start].timestamp,
                        first_test=first_test,
                        test_extreme=extreme,
                        reclaim_distance=distance,
                        r_atr=r_atr,
                        provisional=i == len(candles) - 1,
                        trigger_line=line,
                        pinbar_grade=",".join(sorted(grades)),
                        color_agrees=pinbar_color_agrees(
                            candle, bullish=bullish
                        ),
                        vwap_candles=accumulated.get(candle.timestamp, 1),
                    )
                )
                break  # one reclaim per visit

    nearest: dict[tuple[datetime, MarketDirection], BlockReclaim] = {}
    for reclaim in candidates:
        key = (reclaim.timestamp, reclaim.direction)
        held = nearest.get(key)
        if held is None or reclaim.reclaim_distance < held.reclaim_distance:
            nearest[key] = reclaim
    return sorted(nearest.values(), key=lambda r: r.timestamp)
