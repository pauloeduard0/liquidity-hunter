"""Internal (minor) market structure detector: trailing-reference BOS/CHoCH/HL/LH.

`SwingStructureDetector` deliberately holds an active reference until the
*opposite* side breaks, so the reference reflects the true extreme of the
prior leg rather than whichever pivot formed last -- the right behavior for
`StructureScope.MAJOR`, where the goal is not to flag a CHoCH against a minor
retracement.

For `StructureScope.INTERNAL`, that same design has a failure mode: if an
active reference happens to equal the extreme (max/min) of the entire
remaining candle window, it can never be broken again, which permanently
freezes the *opposite* side's reference too (it is only promoted when the
opposite side breaks). A large subsequent move then goes undetected as
BOS/CHoCH for the rest of the window -- only descriptive HH/HL/LH/LL labels
are emitted.

`InternalStructureDetector` keeps `active_high`/`active_low` as *trailing*
references -- normally the most recently formed swing high/low pivot,
updated after every pivot of that kind -- so both stay close to current
price. But a purely trailing reference has its own failure mode: comparing a
CHoCH against the last pivot, which may be a minor retracement rather than
the true extreme of the leg that just ended, can spuriously flag a
continuation BOS right after the reversal. To avoid that, `pending_high`/
`pending_low` accumulate the most extreme high/low pivot seen for their side
since it was last set as active, mirroring `SwingStructureDetector`'s pending
mechanism:

- A pivot that breaks the active reference on its side *and* is confirmed as
  a BOS/CHoCH promotes the *opposite* side's `pending_<side>` to
  `active_<side>` (or `None`, if nothing has accumulated there yet) -- the
  leg that just ended is over, so its trailing reference is retired in favor
  of the extreme accumulated during that leg. If `active_<side>` becomes
  `None`, the next pivot on that side silently re-bootstraps (no label) --
  the accepted cost of carrying forward "extreme of the prior leg" semantics
  instead of "last pivot".
- A pivot that breaks the active reference but is *not* confirmed (a
  `LIQUIDITY_SWEEP`), or that does not break it at all (a HL/LH label),
  instead folds the *opposite* side's current `active_<side>` into
  `pending_<side>` (via `_extreme`), so that value is not lost when
  `active_<side>` is later overwritten by its own next pivot.
- Bootstrapping a side (its `active_<side>` was `None`) also seeds
  `pending_<side>` with the same pivot, if the opposite side is already
  active -- the bootstrap pivot is simultaneously the new trailing reference
  and a valid promotion candidate for the window that is just beginning.

A pivot that exceeds the active reference on its side, in the direction of
`trend` (or the first such break while `trend` is still `NEUTRAL`), is a
`BREAK_OF_STRUCTURE` on price alone. A pivot that exceeds the active
reference *against* `trend` is a `CHANGE_OF_CHARACTER` if confirmed -- the
candle closes beyond the reference AND (its `volume_delta` ratio is at least
`min_volume_delta_ratio` in the breakout direction, the same rule
`SwingStructureDetector` uses (see `indicators.volume_delta`), OR a
finer-timeframe volume spike is observed during that candle, see
`has_volume_spike`) -- or a `LIQUIDITY_SWEEP` otherwise. A pivot that does
not exceed the active reference is labeled `LOWER_HIGH`/`HIGHER_LOW`.

A `volume_delta` ratio close to zero does not always mean a breakout candle
lacked conviction -- it can also mean the candle's net taker buy/sell volume
happened to cancel out at this timeframe despite a real, high-volume move.
`finer_candles`, if provided (a series of `Candle`s one `TimeFrame` finer
than `candles`, e.g. M30 candles alongside an H1 `candles` series), lets such
a candle still be confirmed if any finer candle within its time window shows
a volume spike (`volume_spike_lookback`, `volume_spike_multiplier`) -- an
additional, independent way to confirm a break, not a replacement for the
`volume_delta` check. This alternative applies only to
`InternalStructureDetector`; `SwingStructureDetector` is unaffected.
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
    has_volume_spike,
    is_confirmed_break,
    validate_candles,
)
from liquidity_hunter.liquidity.detectors.base import MarketStructureDetector
from liquidity_hunter.liquidity.detectors.swing_points import SwingHighDetector, SwingLowDetector


class InternalStructureDetector(MarketStructureDetector):
    """Detects internal BOS/CHoCH/HL/LH from a trailing swing pivot reference.

    Swing highs/lows are sourced from `SwingHighDetector`/`SwingLowDetector`
    using `swing_lookback`, then walked in chronological order maintaining
    `active_high`/`active_low` (trailing references, normally the most
    recently formed pivot of that kind) and `pending_high`/`pending_low`
    (the most extreme pivot of that kind accumulated for a future promotion).

    For each new pivot, it is compared against the *current* `active_high`/
    `active_low`:

    - If `active_<side>` is `None`, this pivot bootstraps it: `active_<side>
      = pivot`, with no event. If the opposite side is already active,
      `pending_<side>` is also seeded with this pivot.
    - A high pivot above `active_high` (a low pivot below `active_low`) in
      the direction of `trend` (or the first such break while `trend` is
      `NEUTRAL`) is a `BREAK_OF_STRUCTURE`; against `trend`, it is a
      `CHANGE_OF_CHARACTER` if the candle that formed it closes beyond the
      reference AND (its `volume_delta` ratio (`abs(volume_delta) /
      volume`) is at least `min_volume_delta_ratio` in the breakout
      direction, OR `finer_candles` shows a volume spike during that
      candle's time window), otherwise a `LIQUIDITY_SWEEP`.
      - On a confirmed BOS/CHoCH, `trend` is updated and the *opposite*
        side's `pending_<side>` is promoted to `active_<side>` (or `None`
        if `pending_<side>` is empty), then cleared.
      - On a `LIQUIDITY_SWEEP`, the opposite side's current `active_<side>`
        is folded into its `pending_<side>` via `_extreme` instead.
    - A high pivot below `active_high` (a low pivot above `active_low`) is a
      descriptive `LOWER_HIGH`/`HIGHER_LOW` label, and also folds the
      opposite side's `active_<side>` into its `pending_<side>`.
    - A pivot exactly equal to `active_<side>` produces no event and does
      not touch either `pending_<side>`.

    In every case, `active_<side>` is then set to this pivot (the trailing
    update). Every `MarketStructure` emitted has `scope =
    StructureScope.INTERNAL`.

    `finer_candles`, if given, is a chronologically ordered series of
    `Candle`s one `TimeFrame` finer than `candles` (see `TimeFrame.finer`),
    used as the alternative volume-spike confirmation described above.
    `volume_spike_lookback` is the number of preceding finer candles whose
    volume is averaged, and `volume_spike_multiplier` is how far above that
    average a finer candle's volume must be to count as a spike.
    """

    def __init__(
        self,
        swing_lookback: int = 10,
        min_volume_delta_ratio: float = 0.2,
        finer_candles: list[Candle] | None = None,
        volume_spike_lookback: int = 20,
        volume_spike_multiplier: float = 1.5,
    ) -> None:
        if not 0.0 <= min_volume_delta_ratio <= 1.0:
            raise ValueError("min_volume_delta_ratio must be between 0 and 1")
        if volume_spike_lookback < 1:
            raise ValueError("volume_spike_lookback must be at least 1")
        if volume_spike_multiplier <= 0.0:
            raise ValueError("volume_spike_multiplier must be positive")
        self._high_detector = SwingHighDetector(lookback=swing_lookback)
        self._low_detector = SwingLowDetector(lookback=swing_lookback)
        self._min_volume_delta_ratio = min_volume_delta_ratio
        self._finer_candles = finer_candles
        self._volume_spike_lookback = volume_spike_lookback
        self._volume_spike_multiplier = volume_spike_multiplier

    def detect(self, candles: list[Candle]) -> list[MarketStructure]:
        validate_candles(candles)

        pivots = collect_pivots(candles, self._high_detector, self._low_detector)

        symbol = candles[0].symbol
        timeframe = candles[0].timeframe
        timeframe_duration = timeframe.to_timedelta()
        candles_by_timestamp = {candle.timestamp: candle for candle in candles}

        def confirms_break(timestamp: datetime, active_price: float, *, bullish: bool) -> bool:
            volume_spike = self._finer_candles is not None and has_volume_spike(
                self._finer_candles,
                window_start=timestamp,
                window_end=timestamp + timeframe_duration,
                lookback=self._volume_spike_lookback,
                multiplier=self._volume_spike_multiplier,
            )
            return is_confirmed_break(
                candles_by_timestamp[timestamp],
                active_price,
                bullish=bullish,
                min_volume_delta_ratio=self._min_volume_delta_ratio,
                volume_spike=volume_spike,
            )

        events: list[MarketStructure] = []
        active_high: Pivot | None = None
        active_low: Pivot | None = None
        pending_high: Pivot | None = None
        pending_low: Pivot | None = None
        trend = MarketDirection.NEUTRAL

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

            if kind == "high":
                if active_high is None:
                    if active_low is not None:
                        pending_high = pivot
                elif price > active_high.price:
                    is_reversal = trend is MarketDirection.BEARISH
                    if not is_reversal or confirms_break(
                        timestamp, active_high.price, bullish=True
                    ):
                        emit(
                            timestamp,
                            StructureEvent.CHANGE_OF_CHARACTER
                            if is_reversal
                            else StructureEvent.BREAK_OF_STRUCTURE,
                            MarketDirection.BULLISH,
                            price,
                            active_high.price,
                        )
                        trend = MarketDirection.BULLISH
                        active_low = pending_low
                        pending_low = None
                    else:
                        emit(
                            timestamp,
                            StructureEvent.LIQUIDITY_SWEEP,
                            MarketDirection.BULLISH,
                            price,
                            active_high.price,
                        )
                        pending_low = self._extreme(pending_low, active_low, higher=False)
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
            else:
                if active_low is None:
                    if active_high is not None:
                        pending_low = pivot
                elif price < active_low.price:
                    is_reversal = trend is MarketDirection.BULLISH
                    if not is_reversal or confirms_break(
                        timestamp, active_low.price, bullish=False
                    ):
                        emit(
                            timestamp,
                            StructureEvent.CHANGE_OF_CHARACTER
                            if is_reversal
                            else StructureEvent.BREAK_OF_STRUCTURE,
                            MarketDirection.BEARISH,
                            price,
                            active_low.price,
                        )
                        trend = MarketDirection.BEARISH
                        active_high = pending_high
                        pending_high = None
                    else:
                        emit(
                            timestamp,
                            StructureEvent.LIQUIDITY_SWEEP,
                            MarketDirection.BEARISH,
                            price,
                            active_low.price,
                        )
                        pending_high = self._extreme(pending_high, active_high, higher=True)
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
