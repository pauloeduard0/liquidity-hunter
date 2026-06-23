"""Swing (major) market structure detector: BOS/CHoCH and LH/HL.

Architecture is identical to `InternalStructureDetector` (see
`internal_structure.py` for the full model description). Differences:

- `swing_lookback=15` default (vs. 2 for internal) surfaces only
  structurally significant pivots rather than every minor swing.
- `persistence_candles=10` default (vs. 5 for internal) requires a
  longer sustained window before a counter-trend break is confirmed as a
  CHoCH.
- Emitted events carry `scope = StructureScope.MAJOR` (the domain
  default), distinguishing them from `StructureScope.INTERNAL` events.
- `choch_origin_<side>` is **always set** on CHoCH (not one-shot like
  `InternalStructureDetector`): with `persistence_candles=10` the risk of
  origin-driven ping-pong is negligible, while the higher lookback makes
  the blind-spot window long enough that a one-shot would re-introduce the
  stuck-trend bug on the third event.

The CHoCH reference is `validated_choch_high`/`validated_choch_low`,
promoted from a `candidate_choch_*` (the *strongest* LOWER_HIGH /
HIGHER_LOW pivot of its window -- highest LH / lowest HL since the last
promotion, the pullback that confirmed the BOS, NOT the most recent
pivot -- or a functionally equivalent re-bootstrap pivot) via the same
two-step gate: a BOS in the leg's direction must occur *after* the
candidate was set *and* its pivot price must surpass
`candidate_choch_*_baseline` (the opposite-side trailing reference
snapshotted when the candidate was set), confirming a genuine structural
continuation. Keeping the candidate at the window extreme (rather than
overwriting it with each weaker, more recent LH/HL) stops the CHoCH from
anchoring early on a mid-leg pivot no BOS reached. Ghost-candidate fix: a
SWEEP that violates an unvalidated candidate updates the candidate to the
sweep pivot.

Every emitted `MarketStructure` has `scope = StructureScope.MAJOR`
(the field's default).
"""

from datetime import datetime

from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    StructureEvent,
)
from liquidity_hunter.liquidity.detectors._common import (
    Pivot,
    bos_confluence,
    collect_pivots,
    find_close_break_index,
    find_sustained_break_index,
    find_wick_break_index,
    is_sustained_break,
    validate_candles,
)
from liquidity_hunter.liquidity.detectors.base import MarketStructureDetector
from liquidity_hunter.liquidity.detectors.swing_points import SwingHighDetector, SwingLowDetector


class SwingStructureDetector(MarketStructureDetector):
    """Detects BOS/CHoCH and LH/HL from major (swing) pivots.

    Swing highs/lows are sourced from `SwingHighDetector`/`SwingLowDetector`
    using `swing_lookback`, then walked in chronological order. See the module
    docstring for the full model; in brief:

    - `active_high`/`active_low` are *trailing* references (the most recent
      pivot of each kind); `pending_high`/`pending_low` accumulate each
      side's extreme for promotion when the opposite side breaks.
    - A pivot beyond the trailing reference in the direction of `trend` is a
      `BREAK_OF_STRUCTURE`; one that does not break it is a `LOWER_HIGH`/
      `HIGHER_LOW` label.
    - The reversal (`CHANGE_OF_CHARACTER`) reference is
      `validated_choch_high`/`validated_choch_low`, promoted from
      `candidate_choch_high`/`candidate_choch_low` (the strongest LH/HL of its
      window) on the next BOS in that leg's direction whose pivot price also
      surpasses `candidate_choch_*_baseline`.

    `persistence_candles` is the number of candles immediately following a
    counter-trend pivot that must also close beyond the reference for the
    break to be a `CHANGE_OF_CHARACTER` rather than a `LIQUIDITY_SWEEP`.

    `confluence_filter` (default `True`) applies a LuxAlgo-style
    shadow-balance check to the BOS close candle.
    """

    def __init__(
        self,
        swing_lookback: int = 10,
        persistence_candles: int = 10,
        confluence_filter: bool = True,
    ) -> None:
        if persistence_candles < 1:
            raise ValueError("persistence_candles must be at least 1")
        self._high_detector = SwingHighDetector(lookback=swing_lookback)
        self._low_detector = SwingLowDetector(lookback=swing_lookback)
        self._persistence_candles = persistence_candles
        self._confluence_filter = confluence_filter

    def detect(self, candles: list[Candle]) -> list[MarketStructure]:
        validate_candles(candles)

        pivots = collect_pivots(candles, self._high_detector, self._low_detector)

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        index_by_timestamp = {candle.timestamp: i for i, candle in enumerate(candles)}

        def confirms_break(
            start_index: int, end_index: int, level_price: float, *, bullish: bool
        ) -> bool:
            return any(
                is_sustained_break(
                    candles,
                    index,
                    level_price,
                    bullish=bullish,
                    persistence_candles=self._persistence_candles,
                )
                for index in range(start_index, end_index + 1)
            )

        events: list[MarketStructure] = []
        active_high: Pivot | None = None
        active_low: Pivot | None = None
        pending_high: Pivot | None = None
        pending_low: Pivot | None = None
        last_high_pivot: Pivot | None = None
        last_low_pivot: Pivot | None = None
        validated_choch_high: Pivot | None = None
        validated_choch_low: Pivot | None = None
        candidate_choch_high: Pivot | None = None
        candidate_choch_low: Pivot | None = None
        candidate_choch_high_baseline: Pivot | None = None
        candidate_choch_low_baseline: Pivot | None = None
        choch_origin_high: Pivot | None = None
        choch_origin_low: Pivot | None = None
        trend = MarketDirection.NEUTRAL
        prev_high_pivot_index = -1
        prev_low_pivot_index = -1

        def emit(
            timestamp: datetime,
            event: StructureEvent,
            direction: MarketDirection,
            price_level: float,
            reference_price_level: float,
            reference_timestamp: datetime | None = None,
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
                    reference_timestamp=reference_timestamp,
                )
            )

        for timestamp, kind, price in pivots:
            pivot = Pivot(price=price, timestamp=timestamp)
            current_index = index_by_timestamp[timestamp]

            if kind == "high":
                choch_high_ref = validated_choch_high or choch_origin_high
                if (
                    trend is MarketDirection.BEARISH
                    and choch_high_ref is not None
                    and price > choch_high_ref.price
                    and confirms_break(
                        prev_high_pivot_index + 1,
                        current_index,
                        choch_high_ref.price,
                        bullish=True,
                    )
                ):
                    break_candle = candles[
                        find_sustained_break_index(
                            candles,
                            prev_high_pivot_index + 1,
                            current_index,
                            choch_high_ref.price,
                            bullish=True,
                            persistence_candles=self._persistence_candles,
                        )
                    ]
                    emit(
                        break_candle.timestamp,
                        StructureEvent.CHANGE_OF_CHARACTER,
                        MarketDirection.BULLISH,
                        price,
                        choch_high_ref.price,
                        reference_timestamp=choch_high_ref.timestamp,
                    )
                    trend = MarketDirection.BULLISH
                    active_low = pending_low
                    pending_low = None
                    validated_choch_low = None
                    choch_origin_high = None
                    choch_origin_low = active_low
                    candidate_choch_low = None
                    candidate_choch_low_baseline = None
                elif active_high is None:
                    if active_low is not None:
                        pending_high = pivot
                    if last_high_pivot is not None and price < last_high_pivot.price:
                        if candidate_choch_high is None or price > candidate_choch_high.price:
                            candidate_choch_high_baseline = active_low
                            candidate_choch_high = pivot
                elif price > active_high.price:
                    if trend is MarketDirection.BEARISH:
                        sweep_candle = candles[
                            find_wick_break_index(
                                candles,
                                prev_high_pivot_index + 1,
                                current_index,
                                active_high.price,
                                bullish=True,
                            )
                        ]
                        emit(
                            sweep_candle.timestamp,
                            StructureEvent.LIQUIDITY_SWEEP,
                            MarketDirection.BULLISH,
                            price,
                            active_high.price,
                        )
                        pending_low = self._extreme(pending_low, active_low, higher=False)
                        if candidate_choch_high is not None and price > candidate_choch_high.price:
                            candidate_choch_high = pivot
                            candidate_choch_high_baseline = active_low
                    else:
                        ref_price = active_high.price
                        trend = MarketDirection.BULLISH
                        active_low = pending_low
                        pending_low = None
                        close_idx = find_close_break_index(
                            candles,
                            prev_high_pivot_index + 1,
                            current_index,
                            ref_price,
                            bullish=True,
                        )
                        if close_idx is not None and (
                            not self._confluence_filter
                            or bos_confluence(candles[close_idx], bullish=True)
                        ):
                            emit(
                                candles[close_idx].timestamp,
                                StructureEvent.BREAK_OF_STRUCTURE,
                                MarketDirection.BULLISH,
                                price,
                                ref_price,
                            )
                            # BOS confirmed (close break) -> promote the CHoCH
                            # reference. A wick-only state advance must not
                            # promote it, else a CHoCH could fire with no
                            # confirmed BOS beneath it.
                            if candidate_choch_low is not None and (
                                candidate_choch_low_baseline is None
                                or price > candidate_choch_low_baseline.price
                            ):
                                validated_choch_low = candidate_choch_low
                                choch_origin_low = None
                                candidate_choch_low = None
                                candidate_choch_low_baseline = None
                elif price < active_high.price:
                    emit(
                        timestamp,
                        StructureEvent.LOWER_HIGH,
                        MarketDirection.BEARISH,
                        price,
                        active_high.price,
                    )
                    pending_low = self._extreme(pending_low, active_low, higher=False)
                    if candidate_choch_high is None or price > candidate_choch_high.price:
                        candidate_choch_high_baseline = active_low
                        candidate_choch_high = pivot
                active_high = pivot
                last_high_pivot = pivot
                prev_high_pivot_index = current_index

            else:
                choch_low_ref = validated_choch_low or choch_origin_low
                if (
                    trend is MarketDirection.BULLISH
                    and choch_low_ref is not None
                    and price < choch_low_ref.price
                    and confirms_break(
                        prev_low_pivot_index + 1,
                        current_index,
                        choch_low_ref.price,
                        bullish=False,
                    )
                ):
                    break_candle = candles[
                        find_sustained_break_index(
                            candles,
                            prev_low_pivot_index + 1,
                            current_index,
                            choch_low_ref.price,
                            bullish=False,
                            persistence_candles=self._persistence_candles,
                        )
                    ]
                    emit(
                        break_candle.timestamp,
                        StructureEvent.CHANGE_OF_CHARACTER,
                        MarketDirection.BEARISH,
                        price,
                        choch_low_ref.price,
                        reference_timestamp=choch_low_ref.timestamp,
                    )
                    trend = MarketDirection.BEARISH
                    active_high = pending_high
                    pending_high = None
                    validated_choch_high = None
                    choch_origin_low = None
                    choch_origin_high = active_high
                    candidate_choch_high = None
                    candidate_choch_high_baseline = None
                elif active_low is None:
                    if active_high is not None:
                        pending_low = pivot
                    if last_low_pivot is not None and price > last_low_pivot.price:
                        if candidate_choch_low is None or price < candidate_choch_low.price:
                            candidate_choch_low_baseline = active_high
                            candidate_choch_low = pivot
                elif price < active_low.price:
                    if trend is MarketDirection.BULLISH:
                        sweep_candle = candles[
                            find_wick_break_index(
                                candles,
                                prev_low_pivot_index + 1,
                                current_index,
                                active_low.price,
                                bullish=False,
                            )
                        ]
                        emit(
                            sweep_candle.timestamp,
                            StructureEvent.LIQUIDITY_SWEEP,
                            MarketDirection.BEARISH,
                            price,
                            active_low.price,
                        )
                        pending_high = self._extreme(pending_high, active_high, higher=True)
                        if candidate_choch_low is not None and price < candidate_choch_low.price:
                            candidate_choch_low = pivot
                            candidate_choch_low_baseline = active_high
                    else:
                        ref_price = active_low.price
                        trend = MarketDirection.BEARISH
                        active_high = pending_high
                        pending_high = None
                        close_idx = find_close_break_index(
                            candles,
                            prev_low_pivot_index + 1,
                            current_index,
                            ref_price,
                            bullish=False,
                        )
                        if close_idx is not None and (
                            not self._confluence_filter
                            or bos_confluence(candles[close_idx], bullish=False)
                        ):
                            emit(
                                candles[close_idx].timestamp,
                                StructureEvent.BREAK_OF_STRUCTURE,
                                MarketDirection.BEARISH,
                                price,
                                ref_price,
                            )
                            # BOS confirmed (close break) -> promote the CHoCH
                            # reference. A wick-only state advance must not
                            # promote it, else a CHoCH could fire with no
                            # confirmed BOS beneath it.
                            if candidate_choch_high is not None and (
                                candidate_choch_high_baseline is None
                                or price < candidate_choch_high_baseline.price
                            ):
                                validated_choch_high = candidate_choch_high
                                choch_origin_high = None
                                candidate_choch_high = None
                                candidate_choch_high_baseline = None
                elif price > active_low.price:
                    emit(
                        timestamp,
                        StructureEvent.HIGHER_LOW,
                        MarketDirection.BULLISH,
                        price,
                        active_low.price,
                    )
                    pending_high = self._extreme(pending_high, active_high, higher=True)
                    if candidate_choch_low is None or price < candidate_choch_low.price:
                        candidate_choch_low_baseline = active_high
                        candidate_choch_low = pivot
                active_low = pivot
                last_low_pivot = pivot
                prev_low_pivot_index = current_index

        return events

    @staticmethod
    def _extreme(
        current: "Pivot | None", candidate: "Pivot | None", *, higher: bool
    ) -> "Pivot | None":
        """The more extreme of `current` and `candidate`, by price.

        Either may be `None`; returns whichever is non-`None`, or `None` if
        both are. `higher=True` keeps the higher-priced pivot (`pending_high`);
        `higher=False` keeps the lower-priced one (`pending_low`).
        """
        if candidate is None:
            return current
        if current is None:
            return candidate
        if higher:
            return candidate if candidate.price > current.price else current
        return candidate if candidate.price < current.price else current
