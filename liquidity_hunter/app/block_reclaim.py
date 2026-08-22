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
#: How long after the visit a reclaim still belongs to it.
MAX_WAIT_CANDLES = 20
#: The reclaiming candle's shape: a long wick against the close, little body.
MIN_WICK_FRACTION = 0.5
MAX_BODY_FRACTION = 0.35
#: Local window for the volatility `r_atr` is normalized by.
ATR_PERIOD = 14
#: Reclaims closer than this to their test extreme are dropped: at that
#: distance the reading is dominated by the tick grid rather than by where
#: the two levels sit.
MIN_DISTANCE_ATR = 0.05


def _is_reclaim(candle: Candle, vwap_value: float, *, bullish: bool) -> bool:
    """The wick crossed the VWAP and the body closed back on the other side."""
    if bullish:
        return candle.low < vwap_value <= candle.close
    return candle.high > vwap_value >= candle.close


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
    """
    if vwap is None or len(candles) < ATR_PERIOD + 2:
        return []
    vwap_at = {point.timestamp: point.value for point in vwap.points}
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
            for i in range(end, min(end + MAX_WAIT_CANDLES + 1, len(candles))):
                candle = candles[i]
                vwap_value = vwap_at.get(candle.timestamp)
                if vwap_value is None:
                    continue
                if not _is_reclaim(candle, vwap_value, bullish=bullish):
                    continue
                if not _is_pinbar(candle, bullish=bullish):
                    continue
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
