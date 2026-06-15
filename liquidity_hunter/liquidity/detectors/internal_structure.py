"""Internal (minor) market structure detector: trailing-reference BOS/HL/LH
with a *validated* CHoCH reference.

`SwingStructureDetector` deliberately holds an active reference until the
*opposite* side breaks, so the reference reflects the true extreme of the
prior leg rather than whichever pivot formed last -- the right behavior for
`StructureScope.MAJOR`. For `StructureScope.INTERNAL` that same design can
freeze a side for long stretches, so `InternalStructureDetector` keeps
`active_high`/`active_low` as *trailing* references (normally the most
recently formed swing high/low pivot, updated after every pivot of that
kind). These drive:

- `BREAK_OF_STRUCTURE`: a pivot beyond the trailing reference *in the
  direction of* `trend` (or the first break while `trend` is `NEUTRAL`) --
  price alone, no confirmation needed.
- `LOWER_HIGH`/`HIGHER_LOW`: a pivot that does not break the trailing
  reference.
- `LIQUIDITY_SWEEP`: a counter-trend pivot that breaks the trailing
  reference but is not a confirmed reversal (see below).

`pending_high`/`pending_low` accumulate the most extreme high/low pivot for
their side, promoted to `active_<side>` when the opposite side breaks (the
leg that just ended is retired in favor of the extreme accumulated during
it). `_extreme` keeps the more extreme of the two.

The CHoCH reference (`CHANGE_OF_CHARACTER`)
==========================================

A change of character is a *reversal*, and the level it must break to count
as one is tracked explicitly per side as `validated_choch_high` /
`validated_choch_low` -- distinct from the trailing `active_<side>` and from
`pending_<side>`. The rule (mirrored for the low side):

- `validated_choch_high` is the swing high that a *bullish* CHoCH must break.
  It is **only updated when a new LL is confirmed** -- i.e. a sustained
  bearish break that prints a low *below the bearish leg's previous lowest
  low* (`last_ll`), not merely below the trailing `active_low` (which may be a
  pullback/higher low). At that moment it is set to `last_high_pivot`: the
  **last swing high before that new LL**. Note "last", not "highest": in a
  clean alternating high/low staircase the high between the two most recent
  LLs is unambiguous, but if extraction is non-alternating (two highs between
  two lows) the *most recent* high before the LL is the reversal-relevant
  one, not the tallest bounce earlier in the leg.
- While price makes no new LL (it prints a higher low instead),
  `validated_choch_high` is **frozen** at the last validated high -- a higher
  low does not move it.
- A *bullish CHoCH* fires when, with `trend` BEARISH, a high pivot breaks
  (sustained, see persistence below) **above `validated_choch_high`**; its
  `reference_price_level` is `validated_choch_high` (never the trailing
  `active_high`, never the breaking pivot). A high pivot that breaks the
  trailing `active_high` but not `validated_choch_high`, or whose break does
  not hold, is a `LIQUIDITY_SWEEP` (trend unchanged) -- an internal bounce
  within the still-intact bearish leg.

`last_high_pivot`/`last_low_pivot` track the most recent swing high/low pivot
*regardless* of the `active_<side>`/`pending_<side>` promotion machinery (so
the CHoCH reference is sourced from the real last pivot, never from a value
that was retired to `None`). `last_ll`/`last_hh` are the running extremes of
the current leg, so a "new LL/HH" is judged against the leg's true extreme
rather than the trailing reference.

The symmetric machinery on the bullish side: a new HH (a sustained bullish
break above `last_hh`) sets `validated_choch_low = last_low_pivot` (the last
low before that HH); a bearish CHoCH fires on a sustained break below
`validated_choch_low`. A confirmed reversal resets the opposite leg's running
extreme (`last_ll`/`last_hh`) so the next leg re-initialises cleanly.

Confirmation is *persistence*-based (see `_common.is_sustained_break`): the
breaking candle AND the `persistence_candles` candles immediately following
it must all close beyond the reference. A single candle that pokes through
`validated_choch_<side>` and reverts (a "false break") fails this and is a
`LIQUIDITY_SWEEP`; a break that holds is a `CHANGE_OF_CHARACTER`. If there
are not yet enough trailing candles to evaluate the window, the break is
treated as unconfirmed. This applies only to `InternalStructureDetector`;
`SwingStructureDetector`'s `volume_delta`-ratio confirmation is unaffected.

Every emitted `MarketStructure` has `scope = StructureScope.INTERNAL`.
"""

from datetime import datetime

from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    StructureScope,
)
from liquidity_hunter.liquidity.detectors._common import (
    Pivot,
    collect_pivots,
    find_sustained_break_index,
    find_wick_break_index,
    is_sustained_break,
    validate_candles,
)
from liquidity_hunter.liquidity.detectors.base import MarketStructureDetector
from liquidity_hunter.liquidity.detectors.swing_points import SwingHighDetector, SwingLowDetector


class InternalStructureDetector(MarketStructureDetector):
    """Detects internal BOS/CHoCH/HL/LH from trailing swing pivot references.

    Swing highs/lows are sourced from `SwingHighDetector`/`SwingLowDetector`
    using `swing_lookback`, then walked in chronological order. See the module
    docstring for the full model; in brief:

    - `active_high`/`active_low` are *trailing* references (the most recent
      pivot of each kind); `pending_high`/`pending_low` accumulate each side's
      extreme for promotion when the opposite side breaks.
    - A pivot beyond the trailing reference in the direction of `trend` is a
      `BREAK_OF_STRUCTURE`; one that does not break it is a `LOWER_HIGH`/
      `HIGHER_LOW` label.
    - The reversal (`CHANGE_OF_CHARACTER`) reference is `validated_choch_high`/
      `validated_choch_low`: the last swing high/low before the most recent
      *new LL/HH* (a break beyond the leg's running extreme `last_ll`/`last_hh`,
      not merely the trailing reference). A counter-trend break of the
      validated reference is a CHoCH if sustained for `persistence_candles`,
      else a `LIQUIDITY_SWEEP`.

    `persistence_candles` is the number of candles immediately following a
    counter-trend pivot that must also close beyond the reference for the
    break to be a `CHANGE_OF_CHARACTER` rather than a `LIQUIDITY_SWEEP`.
    """

    def __init__(self, swing_lookback: int = 1, persistence_candles: int = 2) -> None:
        if persistence_candles < 1:
            raise ValueError("persistence_candles must be at least 1")
        self._high_detector = SwingHighDetector(lookback=swing_lookback)
        self._low_detector = SwingLowDetector(lookback=swing_lookback)
        self._persistence_candles = persistence_candles

    def detect(self, candles: list[Candle]) -> list[MarketStructure]:
        validate_candles(candles)

        pivots = collect_pivots(candles, self._high_detector, self._low_detector)

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        index_by_timestamp = {candle.timestamp: index for index, candle in enumerate(candles)}

        def confirms_break(timestamp: datetime, level_price: float, *, bullish: bool) -> bool:
            return is_sustained_break(
                candles,
                index_by_timestamp[timestamp],
                level_price,
                bullish=bullish,
                persistence_candles=self._persistence_candles,
            )

        events: list[MarketStructure] = []
        # Trailing references (most recent pivot of each kind); drive BOS
        # detection and HL/LH labels.
        active_high: Pivot | None = None
        active_low: Pivot | None = None
        # Most extreme pivot of each side, promoted to active_<side> when the
        # opposite side breaks.
        pending_high: Pivot | None = None
        pending_low: Pivot | None = None
        # The most recent high/low pivot, period -- never retired to `None` by
        # a promotion. Sources the CHoCH reference (the last high/low before a
        # newly confirmed LL/HH).
        last_high_pivot: Pivot | None = None
        last_low_pivot: Pivot | None = None
        # The CHoCH reference levels. validated_choch_high is the swing high a
        # bullish CHoCH must break; set to last_high_pivot when a *new LL* is
        # confirmed and frozen otherwise. Mirror for validated_choch_low.
        validated_choch_high: Pivot | None = None
        validated_choch_low: Pivot | None = None
        # Running extremes of the current leg: a break is only a "new LL/HH"
        # (and only then moves the validated CHoCH reference) if it beats
        # last_ll/last_hh -- breaking a merely trailing pullback low/high does
        # not.
        last_ll: Pivot | None = None
        last_hh: Pivot | None = None
        trend = MarketDirection.NEUTRAL
        # Candle index of the previous pivot of each kind, used to bound the
        # break-candle search below to the leg between consecutive pivots of
        # that kind. -1 (no previous pivot) is never read: every branch below
        # that performs a search is only reachable once active_<side>/
        # validated_choch_<side> is set, which happens no earlier than the
        # first pivot of that kind, i.e. once these are no longer -1.
        prev_high_pivot_index = -1
        prev_low_pivot_index = -1

        def emit(
            timestamp: datetime,
            event: StructureEvent,
            direction: MarketDirection,
            price_level: float,
            reference_price_level: float,
        ) -> None:
            events.append(
                MarketStructure(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=timestamp,
                    event=event,
                    direction=direction,
                    price_level=price_level,
                    reference_price_level=reference_price_level,
                    scope=StructureScope.INTERNAL,
                )
            )

        for timestamp, kind, price in pivots:
            pivot = Pivot(price=price, timestamp=timestamp)
            current_index = index_by_timestamp[timestamp]

            if kind == "high":
                if (
                    trend is MarketDirection.BEARISH
                    and validated_choch_high is not None
                    and price > validated_choch_high.price
                    and confirms_break(timestamp, validated_choch_high.price, bullish=True)
                ):
                    # Bullish CHoCH: a sustained break above validated_choch_high
                    # (the last high before the bearish leg's lowest low). The
                    # reference is validated_choch_high -- never the trailing
                    # active_high, never the breaking pivot. Checked before the
                    # active_high bootstrap so a CHoCH still fires even if
                    # active_high was retired to `None`. Timestamped on the
                    # candle that first sustains the break, not the pivot that
                    # eventually confirmed it -- price_level remains the
                    # pivot's price (the extreme of the move).
                    break_candle = candles[
                        find_sustained_break_index(
                            candles,
                            prev_high_pivot_index + 1,
                            current_index,
                            validated_choch_high.price,
                            bullish=True,
                            persistence_candles=self._persistence_candles,
                        )
                    ]
                    emit(
                        break_candle.timestamp,
                        StructureEvent.CHANGE_OF_CHARACTER,
                        MarketDirection.BULLISH,
                        price,
                        validated_choch_high.price,
                    )
                    trend = MarketDirection.BULLISH
                    active_low = pending_low
                    pending_low = None
                    # The bullish leg begins: this high is its first HH, and the
                    # last low before it becomes the bearish CHoCH reference.
                    last_hh = pivot
                    validated_choch_low = last_low_pivot
                    last_ll = None
                elif active_high is None:
                    if active_low is not None:
                        pending_high = pivot
                elif price > active_high.price:
                    # Timestamped on the candle whose wick first breaks
                    # active_high, not the pivot that eventually confirmed it
                    # -- price_level remains the pivot's price (the extreme of
                    # the move).
                    break_candle = candles[
                        find_wick_break_index(
                            candles,
                            prev_high_pivot_index + 1,
                            current_index,
                            active_high.price,
                            bullish=True,
                        )
                    ]
                    if trend is MarketDirection.BEARISH:
                        # Broke the trailing high but not validated_choch_high
                        # (or the break did not hold): an internal bounce within
                        # the bearish leg, not a reversal.
                        emit(
                            break_candle.timestamp,
                            StructureEvent.LIQUIDITY_SWEEP,
                            MarketDirection.BULLISH,
                            price,
                            active_high.price,
                        )
                        pending_low = self._extreme(pending_low, active_low, higher=False)
                    else:
                        # BOS bullish (first break from NEUTRAL, or continuation).
                        emit(
                            break_candle.timestamp,
                            StructureEvent.BREAK_OF_STRUCTURE,
                            MarketDirection.BULLISH,
                            price,
                            active_high.price,
                        )
                        trend = MarketDirection.BULLISH
                        active_low = pending_low
                        pending_low = None
                        if last_hh is None or price > last_hh.price:
                            # A new HH: the last low before it is the level a
                            # bearish CHoCH must break.
                            validated_choch_low = last_low_pivot
                            last_hh = pivot
                        last_ll = None
                elif price < active_high.price:
                    emit(
                        timestamp,
                        StructureEvent.LOWER_HIGH,
                        MarketDirection.BEARISH,
                        price,
                        active_high.price,
                    )
                    pending_low = self._extreme(pending_low, active_low, higher=False)
                active_high = pivot
                last_high_pivot = pivot
                prev_high_pivot_index = current_index
                if validated_choch_high is None and trend is MarketDirection.BEARISH:
                    # Bootstrap fallback: the leg's BOS confirmed a new LL with
                    # no prior high pivot to serve as the CHoCH reference (the
                    # leg started at/before the window's first pivot). The next
                    # high pivot becomes that reference instead, frozen from
                    # here per the normal rule.
                    validated_choch_high = pivot
            else:
                if (
                    trend is MarketDirection.BULLISH
                    and validated_choch_low is not None
                    and price < validated_choch_low.price
                    and confirms_break(timestamp, validated_choch_low.price, bullish=False)
                ):
                    # Bearish CHoCH (mirror of the bullish case).
                    break_candle = candles[
                        find_sustained_break_index(
                            candles,
                            prev_low_pivot_index + 1,
                            current_index,
                            validated_choch_low.price,
                            bullish=False,
                            persistence_candles=self._persistence_candles,
                        )
                    ]
                    emit(
                        break_candle.timestamp,
                        StructureEvent.CHANGE_OF_CHARACTER,
                        MarketDirection.BEARISH,
                        price,
                        validated_choch_low.price,
                    )
                    trend = MarketDirection.BEARISH
                    active_high = pending_high
                    pending_high = None
                    last_ll = pivot
                    validated_choch_high = last_high_pivot
                    last_hh = None
                elif active_low is None:
                    if active_high is not None:
                        pending_low = pivot
                elif price < active_low.price:
                    # Timestamped on the candle whose wick first breaks
                    # active_low, not the pivot that eventually confirmed it
                    # -- price_level remains the pivot's price (the extreme of
                    # the move).
                    break_candle = candles[
                        find_wick_break_index(
                            candles,
                            prev_low_pivot_index + 1,
                            current_index,
                            active_low.price,
                            bullish=False,
                        )
                    ]
                    if trend is MarketDirection.BULLISH:
                        emit(
                            break_candle.timestamp,
                            StructureEvent.LIQUIDITY_SWEEP,
                            MarketDirection.BEARISH,
                            price,
                            active_low.price,
                        )
                        pending_high = self._extreme(pending_high, active_high, higher=True)
                    else:
                        # BOS bearish (first break from NEUTRAL, or continuation).
                        emit(
                            break_candle.timestamp,
                            StructureEvent.BREAK_OF_STRUCTURE,
                            MarketDirection.BEARISH,
                            price,
                            active_low.price,
                        )
                        trend = MarketDirection.BEARISH
                        active_high = pending_high
                        pending_high = None
                        if last_ll is None or price < last_ll.price:
                            # A new LL: the last high before it is the level a
                            # bullish CHoCH must break.
                            validated_choch_high = last_high_pivot
                            last_ll = pivot
                        last_hh = None
                elif price > active_low.price:
                    emit(
                        timestamp,
                        StructureEvent.HIGHER_LOW,
                        MarketDirection.BULLISH,
                        price,
                        active_low.price,
                    )
                    pending_high = self._extreme(pending_high, active_high, higher=True)
                active_low = pivot
                last_low_pivot = pivot
                prev_low_pivot_index = current_index
                if validated_choch_low is None and trend is MarketDirection.BULLISH:
                    # Bootstrap fallback, mirroring the high side above.
                    validated_choch_low = pivot

        return events

    @staticmethod
    def _extreme(current: Pivot | None, candidate: Pivot | None, *, higher: bool) -> Pivot | None:
        """The more extreme of `current` and `candidate`, by price.

        Either may be `None`; returns whichever of the two is non-`None`, or
        `None` if both are. `higher=True` keeps the higher-priced pivot (for
        `pending_high`); `higher=False` keeps the lower-priced one (for
        `pending_low`).
        """
        if candidate is None:
            return current
        if current is None:
            return candidate
        if higher:
            return candidate if candidate.price > current.price else current
        return candidate if candidate.price < current.price else current
