"""Consolidation (lateral range) detection over a structure detector's timeline.

A range is a stretch of candles with **no structure advance** (no emitted
BOS/CHoCH/`CHOCH_FAILED`) where price oscillated inside a volatility-bounded
box. Inside such a stretch the structure detector is *correctly* silent -- a
range has no BOS/CHoCH by definition -- but that silence is indistinguishable
on the chart from a stuck detector, and every structural reference stays
pinned at pre-range levels (the staircase at an old wick above, the CHoCH
reference at the leg origin below). Detecting the range turns the silence
into an explicit observation.

`detect_consolidation_ranges` is a pure post-pass: it consumes the candle
series plus the detector's emitted advance indices and never feeds back into
the state machine (purely additive -- with the flags off the detector output
is byte-for-byte identical).

Definition (per quiet segment between consecutive advances):

- **Compression**: the longest trailing window whose total high-low box is no
  taller than `max_height_pct` (the caller resolves it as N x the series'
  mean true-range%, so the same N means the same number of "typical candles"
  of height on every asset/timeframe).
- **Duration**: the window must hold at least `min_candles` candles.
- **Oscillation**: price must have visited the box's top and bottom edge
  zones (the outer `_EDGE_ZONE_FRACTION` of the box height) alternately at
  least twice-plus-once (a compressed touch sequence of length >= 3, e.g.
  top-bottom-top) -- so a slow one-way drift inside the cap does not qualify.

Once confirmed, each candle is classified against the current box, resolution
first:

- a candle whose close is beyond a boundary and holds for `resolve_persistence`
  further closes (`_common.is_sustained_break`) **resolves** the range at that
  boundary -- even if the resulting box would still fit under the height cap;
- a close beyond a boundary that has *not* held **arms** that boundary: the
  close is a breakout test, so the boundary is frozen thereafter (a pending
  break, not part of the range);
- otherwise the close is inside the box (pure oscillation), and the box widens
  to include a wick beyond an **un-armed** boundary while total height stays
  within the cap; an armed boundary stays frozen and a wick beyond the cap is
  a boundary sweep left outside the box.

Checking resolution/arming before absorption is what keeps a genuine breakout
from being swallowed by a widening box: absorption has no strictness (any wick
within the height cap widens the box), so without the arm gate a boundary
trails a choppy directional push up with price and the break never registers
as a close beyond it (BTC H4 July 2026 ballooned its top from 64.7k to 65.6k
this way). A wick that *leads* the closes during two-sided oscillation still
widens the box (ETH H1 July 2026's 1829.52 top, its closes staying below it)
-- the arm only fires once a *close* actually breaches the boundary. A
structure advance ending the segment resolves any open range in the advance's
direction; a range still open at the series end is reported `ACTIVE`.
"""

from collections.abc import Sequence
from datetime import datetime

from liquidity_hunter.core.domain.candle import Candle
from liquidity_hunter.core.domain.consolidation import ConsolidationRange
from liquidity_hunter.core.domain.enums import (
    ConsolidationStatus,
    MarketDirection,
    StructureEvent,
    StructureScope,
)
from liquidity_hunter.core.domain.market_structure import MarketStructure
from liquidity_hunter.liquidity.detectors._common import RangeReset, is_sustained_break

# The outer fraction of the box height, at each boundary, that counts as an
# "edge zone" for the oscillation requirement.
_EDGE_ZONE_FRACTION = 0.25


def _height_pct(box_high: float, box_low: float) -> float:
    midpoint = (box_high + box_low) / 2
    if midpoint <= 0:
        return float("inf")
    return (box_high - box_low) / midpoint


def _oscillates(window: Sequence[Candle], box_high: float, box_low: float) -> bool:
    """Whether `window` visited both edge zones of the box alternately.

    Builds the compressed sequence of edge-zone touches (top/bottom, dropping
    consecutive repeats) and requires length >= 3 -- price crossed the box at
    least twice (e.g. top-bottom-top), the minimal footprint of a two-sided
    range rather than a one-way drift.
    """
    height = box_high - box_low
    top_zone = box_high - height * _EDGE_ZONE_FRACTION
    bottom_zone = box_low + height * _EDGE_ZONE_FRACTION
    sequence: list[str] = []
    for candle in window:
        touches_top = candle.high >= top_zone
        touches_bottom = candle.low <= bottom_zone
        marks: tuple[str, ...]
        if touches_top and touches_bottom:
            marks = ("T", "B")
        elif touches_top:
            marks = ("T",)
        elif touches_bottom:
            marks = ("B",)
        else:
            marks = ()
        for mark in marks:
            if not sequence or sequence[-1] != mark:
                sequence.append(mark)
        if len(sequence) >= 3:
            return True
    return False


def _box_over(candles: Sequence[Candle], start: int, end: int) -> tuple[float, float]:
    box_high = max(candles[index].high for index in range(start, end + 1))
    box_low = min(candles[index].low for index in range(start, end + 1))
    return box_high, box_low


def detect_consolidation_ranges(
    candles: Sequence[Candle],
    advances: Sequence[tuple[int, MarketDirection]],
    *,
    min_candles: int,
    max_height_pct: float,
    resolve_persistence: int,
) -> list[ConsolidationRange]:
    """Detect confirmed consolidation ranges across the quiet segments.

    `advances` are the candle indices of the detector's emitted structure
    advances (BOS/CHoCH/`CHOCH_FAILED`), paired with the trend direction the
    advance established -- segment boundaries a range may never span, and the
    fallback resolution when an advance ends a still-open range.
    """
    ranges, _ = detect_consolidation_ranges_with_resets(
        candles,
        advances,
        min_candles=min_candles,
        max_height_pct=max_height_pct,
        resolve_persistence=resolve_persistence,
    )
    return ranges


def detect_consolidation_ranges_with_resets(
    candles: Sequence[Candle],
    advances: Sequence[tuple[int, MarketDirection]],
    *,
    min_candles: int,
    max_height_pct: float,
    resolve_persistence: int,
) -> tuple[list[ConsolidationRange], list[RangeReset]]:
    """`detect_consolidation_ranges` plus the reference re-seed directives.

    Each confirmed range emits a `RangeReset` at its confirmation candle, and
    another at every absorbed candle that expands a boundary -- always the box
    **as known at that candle** (the final box would be lookahead). The second
    detector pass (`range_reset_cycle`) replays these into the state machine
    so the boundaries become the live structural references.
    """
    if not candles:
        return [], []
    # Deduplicate by index (keep the last direction recorded for a candle)
    # and sort: emissions are appended in pivot order, but break-candle
    # attribution can place a later emission at an earlier candle.
    by_index: dict[int, MarketDirection] = {}
    for index, direction in advances:
        by_index[index] = direction
    ordered = sorted(by_index.items())

    ranges: list[ConsolidationRange] = []
    resets: list[RangeReset] = []
    segment_start = 0
    boundaries: list[tuple[int, MarketDirection | None]] = [
        *ordered,
        (len(candles), None),
    ]
    for advance_index, advance_direction in boundaries:
        segment_end = advance_index - 1
        if segment_end - segment_start + 1 >= min_candles:
            ranges.extend(
                _scan_segment(
                    candles,
                    segment_start,
                    segment_end,
                    advance_index if advance_index < len(candles) else None,
                    advance_direction,
                    min_candles=min_candles,
                    max_height_pct=max_height_pct,
                    resolve_persistence=resolve_persistence,
                    resets=resets,
                )
            )
        segment_start = advance_index + 1
    return ranges, resets


def _scan_segment(
    candles: Sequence[Candle],
    segment_start: int,
    segment_end: int,
    advance_index: int | None,
    advance_direction: MarketDirection | None,
    *,
    min_candles: int,
    max_height_pct: float,
    resolve_persistence: int,
    resets: list[RangeReset] | None = None,
) -> list[ConsolidationRange]:
    symbol = candles[0].symbol
    timeframe = candles[0].timeframe
    found: list[ConsolidationRange] = []

    start = segment_start
    box_high = float("-inf")
    box_low = float("inf")
    # (start index, box high, box low) once a range is confirmed and unresolved.
    active: tuple[int, float, float] | None = None
    # A boundary is "armed" once a candle *closes* beyond it: that close is a
    # breakout test, so the boundary is frozen (later wicks past it must not
    # ratchet the box in the breakout direction). Cleared when a range ends.
    top_armed = False
    bottom_armed = False

    def emit_reset(index: int, range_start: int, high: float, low: float) -> None:
        if resets is not None:
            resets.append(
                RangeReset(
                    candle_index=index,
                    price_low=low,
                    price_high=high,
                    low_formed_timestamp=_boundary_formed_at(
                        candles, range_start, index, low, is_high=False
                    ),
                    high_formed_timestamp=_boundary_formed_at(
                        candles, range_start, index, high, is_high=True
                    ),
                )
            )

    index = segment_start
    while index <= segment_end:
        candle = candles[index]
        if active is None:
            box_high = max(box_high, candle.high)
            box_low = min(box_low, candle.low)
            # Keep the longest trailing window within the height cap.
            while start < index and _height_pct(box_high, box_low) > max_height_pct:
                start += 1
                box_high, box_low = _box_over(candles, start, index)
            if _height_pct(box_high, box_low) > max_height_pct:
                # A single candle taller than the cap: no window can hold it.
                start = index + 1
                box_high = float("-inf")
                box_low = float("inf")
            elif index - start + 1 >= min_candles and _oscillates(
                candles[start : index + 1], box_high, box_low
            ):
                active = (start, box_high, box_low)
                top_armed = False
                bottom_armed = False
                emit_reset(index, start, box_high, box_low)
        else:
            range_start, high, low = active
            close_above = candle.close > high
            close_below = candle.close < low
            # A sustained close beyond a boundary resolves the range there --
            # checked first so a real breakout is registered rather than
            # swallowed by a widening box.
            if close_above and is_sustained_break(
                candles, index, high, bullish=True, persistence_candles=resolve_persistence
            ):
                found.append(
                    _resolved(
                        candles, range_start, index, high, low,
                        MarketDirection.BULLISH,
                    )
                )
                active = None
                top_armed = False
                bottom_armed = False
                start = index + 1
                box_high = float("-inf")
                box_low = float("inf")
            elif close_below and is_sustained_break(
                candles, index, low, bullish=False, persistence_candles=resolve_persistence
            ):
                found.append(
                    _resolved(
                        candles, range_start, index, high, low,
                        MarketDirection.BEARISH,
                    )
                )
                active = None
                top_armed = False
                bottom_armed = False
                start = index + 1
                box_high = float("-inf")
                box_low = float("inf")
            elif close_above:
                # A close beyond the top that has not held yet: a breakout test.
                # Arm (freeze) the top so its retest wicks can no longer widen
                # the box -- letting them trail a directional push up is what
                # lets a real breakout be swallowed instead of resolving.
                top_armed = True
            elif close_below:
                bottom_armed = True
            else:
                # Close inside the box: pure oscillation. Widen an *un-armed*
                # boundary to include a wick beyond it while total height stays
                # within the volatility envelope. An armed boundary (already
                # close-breached, breakout pending) stays frozen; a wick beyond
                # the cap is a boundary sweep left outside the box.
                absorbed_high = high if top_armed else max(high, candle.high)
                absorbed_low = low if bottom_armed else min(low, candle.low)
                if (absorbed_high > high or absorbed_low < low) and _height_pct(
                    absorbed_high, absorbed_low
                ) <= max_height_pct:
                    active = (range_start, absorbed_high, absorbed_low)
                    emit_reset(index, range_start, absorbed_high, absorbed_low)
        index += 1

    if active is not None:
        range_start, high, low = active
        if advance_index is not None and advance_direction is not None:
            # A structure advance ended the segment: the range resolved there.
            found.append(
                ConsolidationRange(
                    symbol=symbol,
                    timeframe=timeframe,
                    start_timestamp=candles[range_start].timestamp,
                    end_timestamp=candles[advance_index].timestamp,
                    price_low=low,
                    price_high=high,
                    status=ConsolidationStatus.RESOLVED,
                    resolved_direction=advance_direction,
                    candle_count=segment_end - range_start + 1,
                )
            )
        else:
            found.append(
                ConsolidationRange(
                    symbol=symbol,
                    timeframe=timeframe,
                    start_timestamp=candles[range_start].timestamp,
                    price_low=low,
                    price_high=high,
                    status=ConsolidationStatus.ACTIVE,
                    candle_count=segment_end - range_start + 1,
                )
            )
    return found


def stage_breakout_events(
    candles: Sequence[Candle],
    ranges: Sequence[ConsolidationRange],
    advances: Sequence[tuple[int, MarketDirection]],
    existing_events: Sequence[MarketStructure],
    *,
    dedup_candles: int,
) -> list[MarketStructure]:
    """Stage additive structure events at consolidation-range breakouts.

    Inside a range every structural reference is pinned at a pre-range level,
    so the state machine often stays silent long after a genuine breakout
    (e.g. a range top at 1829.5 broken while the staircase bar sits at 1833).
    The range boundary the market defended for the whole box *is* the
    structural level the breakout broke, so each range resolved by a
    sustained boundary break stages one event at the breakout candle:

    - breaking **with** the segment's standing trend (the trend established
      by the advance that opened the quiet segment): a `BREAK_OF_STRUCTURE`
      referencing the broken boundary -- a continuation mark, safe for trend
      replay (it re-asserts the direction the replay already holds);
    - breaking **against** it: a `CHANGE_OF_CHARACTER` with
      `provisional=True` -- the additive contract: the state machine's trend
      never flipped, so replay consumers (hunt/narrative) must skip it while
      the chart still shows the dimmed reversal mark.

    Purely additive and deduplicated: a range resolved *by* a structure
    advance stages nothing (the real event is already there), a range in the
    bootstrap segment (no opening advance -- no trend context) stages
    nothing, and a staged event is dropped when a real same-direction
    BOS/CHoCH sits within `dedup_candles` of the breakout (the state machine
    caught the break itself). `reference_timestamp` is the first candle in
    the range that formed the broken boundary, so the drawn line spans the
    defended level.
    """
    if not candles:
        return []
    index_by_timestamp = {candle.timestamp: index for index, candle in enumerate(candles)}
    direction_by_index: dict[int, MarketDirection] = {}
    for index, direction in advances:
        direction_by_index[index] = direction
    advance_indices = sorted(direction_by_index)

    real_advance_indices: list[tuple[int, MarketDirection]] = []
    for event in existing_events:
        if event.provisional or event.event not in (
            StructureEvent.BREAK_OF_STRUCTURE,
            StructureEvent.CHANGE_OF_CHARACTER,
        ):
            continue
        event_index = index_by_timestamp.get(event.timestamp)
        if event_index is not None:
            real_advance_indices.append((event_index, event.direction))

    staged: list[MarketStructure] = []
    for range_ in ranges:
        if (
            range_.status is not ConsolidationStatus.RESOLVED
            or range_.end_timestamp is None
            or range_.resolved_direction is None
        ):
            continue
        start_index = index_by_timestamp.get(range_.start_timestamp)
        end_index = index_by_timestamp.get(range_.end_timestamp)
        if start_index is None or end_index is None:
            continue
        # Resolved by a structure advance, not by a boundary break: the real
        # event already marks this candle.
        if end_index in direction_by_index:
            continue
        # The trend standing during the range is the one established by the
        # advance that opened its quiet segment; with none (bootstrap) there
        # is no trend context to classify the breakout against.
        opening = [index for index in advance_indices if index < start_index]
        if not opening:
            continue
        segment_trend = direction_by_index[opening[-1]]
        if segment_trend is MarketDirection.NEUTRAL:
            continue
        # The real machine caught (or is about to catch) this same break.
        if any(
            direction is range_.resolved_direction
            and abs(index - end_index) <= dedup_candles
            for index, direction in real_advance_indices
        ):
            continue

        bullish = range_.resolved_direction is MarketDirection.BULLISH
        boundary = range_.price_high if bullish else range_.price_low
        reference_index = next(
            (
                index
                for index in range(start_index, end_index)
                if (candles[index].high if bullish else candles[index].low) == boundary
            ),
            start_index,
        )
        breakout = candles[end_index]
        is_continuation = range_.resolved_direction is segment_trend
        staged.append(
            MarketStructure(
                symbol=range_.symbol,
                timeframe=range_.timeframe,
                timestamp=range_.end_timestamp,
                event=(
                    StructureEvent.BREAK_OF_STRUCTURE
                    if is_continuation
                    else StructureEvent.CHANGE_OF_CHARACTER
                ),
                direction=range_.resolved_direction,
                price_level=breakout.high if bullish else breakout.low,
                reference_price_level=boundary,
                reference_timestamp=candles[reference_index].timestamp,
                scope=StructureScope.INTERNAL,
                provisional=not is_continuation,
            )
        )
    return staged


def _boundary_formed_at(
    candles: Sequence[Candle],
    start: int,
    end: int,
    level: float,
    *,
    is_high: bool,
) -> datetime:
    """The first candle in `[start, end]` whose extreme formed `level`."""
    for index in range(start, end + 1):
        extreme = candles[index].high if is_high else candles[index].low
        if extreme == level:
            return candles[index].timestamp
    return candles[start].timestamp


def _resolved(
    candles: Sequence[Candle],
    range_start: int,
    breakout_index: int,
    box_high: float,
    box_low: float,
    direction: MarketDirection,
) -> ConsolidationRange:
    return ConsolidationRange(
        symbol=candles[0].symbol,
        timeframe=candles[0].timeframe,
        start_timestamp=candles[range_start].timestamp,
        end_timestamp=candles[breakout_index].timestamp,
        price_low=box_low,
        price_high=box_high,
        status=ConsolidationStatus.RESOLVED,
        resolved_direction=direction,
        candle_count=breakout_index - range_start,
    )
