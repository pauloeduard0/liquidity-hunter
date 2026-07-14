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

Once confirmed, the box absorbs subsequent candles while its total height
stays within the cap. A candle poking beyond that cannot be absorbed is
either a **resolution** (its close is beyond the boundary and holds for
`resolve_persistence` further closes -- `_common.is_sustained_break`) or a
boundary sweep (kept outside the frozen box). A structure advance ending the
segment resolves any open range in the advance's direction; a range still
open at the series end is reported `ACTIVE`.
"""

from collections.abc import Sequence

from liquidity_hunter.core.domain.candle import Candle
from liquidity_hunter.core.domain.consolidation import ConsolidationRange
from liquidity_hunter.core.domain.enums import ConsolidationStatus, MarketDirection
from liquidity_hunter.liquidity.detectors._common import is_sustained_break

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
    if not candles:
        return []
    # Deduplicate by index (keep the last direction recorded for a candle)
    # and sort: emissions are appended in pivot order, but break-candle
    # attribution can place a later emission at an earlier candle.
    by_index: dict[int, MarketDirection] = {}
    for index, direction in advances:
        by_index[index] = direction
    ordered = sorted(by_index.items())

    ranges: list[ConsolidationRange] = []
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
                )
            )
        segment_start = advance_index + 1
    return ranges


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
) -> list[ConsolidationRange]:
    symbol = candles[0].symbol
    timeframe = candles[0].timeframe
    found: list[ConsolidationRange] = []

    start = segment_start
    box_high = float("-inf")
    box_low = float("inf")
    # (start index, box high, box low) once a range is confirmed and unresolved.
    active: tuple[int, float, float] | None = None

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
        else:
            range_start, high, low = active
            absorbed_high = max(high, candle.high)
            absorbed_low = min(low, candle.low)
            if _height_pct(absorbed_high, absorbed_low) <= max_height_pct:
                # Still inside the volatility envelope: the box widens.
                active = (range_start, absorbed_high, absorbed_low)
            elif candle.close > high and is_sustained_break(
                candles, index, high, bullish=True, persistence_candles=resolve_persistence
            ):
                found.append(
                    _resolved(
                        candles, range_start, index, high, low,
                        MarketDirection.BULLISH,
                    )
                )
                active = None
                start = index + 1
                box_high = float("-inf")
                box_low = float("inf")
            elif candle.close < low and is_sustained_break(
                candles, index, low, bullish=False, persistence_candles=resolve_persistence
            ):
                found.append(
                    _resolved(
                        candles, range_start, index, high, low,
                        MarketDirection.BEARISH,
                    )
                )
                active = None
                start = index + 1
                box_high = float("-inf")
                box_low = float("inf")
            # Otherwise: a boundary sweep (wick or unsustained close beyond) --
            # the frozen box holds, the poke stays outside it.
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
