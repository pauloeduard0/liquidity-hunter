"""Open-interest regime analyzer.

Cross-references the open-interest series with price action and
``MarketStructure`` events to produce a joint reading: whether the current
move is backed by *new* positions entering (conviction) or by positions
closing (unwinding), and how OI behaved into each structure break —
e.g. a BOS on rising OI (fresh money behind the break) vs a BOS on falling
OI (short covering), or a liquidity sweep that flushed leveraged positions.
"""

import bisect
from datetime import datetime

from liquidity_hunter.core.domain import (
    Candle,
    MarketStructure,
    OIAnalysis,
    OIParticipation,
    OIQualifiedEvent,
    OIRegime,
    OIRegimeReading,
    OpenInterestPoint,
    StructureEvent,
    TimeFrame,
)

_TIMEFRAME_WINDOW: dict[TimeFrame, int] = {
    TimeFrame.M1: 20,
    TimeFrame.M5: 15,
    TimeFrame.M15: 10,
    TimeFrame.M30: 7,
    TimeFrame.H1: 7,
    TimeFrame.H4: 5,
    TimeFrame.D1: 5,
    TimeFrame.W1: 3,
}

# Structure events worth qualifying with OI context. Descriptive pivot labels
# (HH/HL/LH/LL) and failed-CHoCH marks are skipped: they describe pivots, not
# breaks, so "participation into the break" has no meaning for them.
_QUALIFIED_EVENTS = frozenset(
    {
        StructureEvent.BREAK_OF_STRUCTURE,
        StructureEvent.CHANGE_OF_CHARACTER,
        StructureEvent.LIQUIDITY_SWEEP,
    }
)

_REGIME_DESCRIPTIONS: dict[OIRegime, str] = {
    OIRegime.LONG_BUILDUP: "price rising with OI rising — new longs entering (conviction move)",
    OIRegime.SHORT_COVERING: (
        "price rising with OI falling — shorts covering, no new money behind the move"
    ),
    OIRegime.SHORT_BUILDUP: "price falling with OI rising — new shorts entering (conviction move)",
    OIRegime.LONG_LIQUIDATION: (
        "price falling with OI falling — longs closing/liquidating, not fresh selling"
    ),
    OIRegime.FLAT: "no meaningful joint price/OI displacement over the window",
}


class OIRegimeAnalyzer:
    """Classifies the joint price/open-interest regime and qualifies structure events.

    Parameters
    ----------
    window_size:
        Candles per analysis window (both the current-regime window and the
        per-event measurement window). ``None`` resolves per timeframe from
        ``_TIMEFRAME_WINDOW``.
    min_price_change_pct:
        Minimum absolute fractional price change over the window for the
        regime to be directional (below it the regime is ``FLAT``).
    min_oi_change_pct:
        Minimum absolute fractional OI change for the regime to be
        directional, and for an event's participation to be
        ``NEW_MONEY``/``COVERING`` rather than ``FLAT``.
    flush_oi_drop_pct:
        Minimum fractional OI *drop* around a ``LIQUIDITY_SWEEP`` for it to
        qualify as a ``FLUSH`` (leveraged positions force-closed).
    """

    def __init__(
        self,
        window_size: int | None = None,
        min_price_change_pct: float = 0.002,
        min_oi_change_pct: float = 0.003,
        flush_oi_drop_pct: float = 0.005,
    ) -> None:
        self._window_override = window_size
        self._min_price_change = min_price_change_pct
        self._min_oi_change = min_oi_change_pct
        self._flush_oi_drop = flush_oi_drop_pct

    def analyze(
        self,
        candles: list[Candle],
        open_interest: list[OpenInterestPoint],
        structure_events: list[MarketStructure],
    ) -> OIAnalysis:
        symbol = candles[0].symbol if candles else ""
        timeframe = candles[0].timeframe if candles else TimeFrame.H1
        oi_points = sorted(open_interest, key=lambda p: p.timestamp)
        analysis = OIAnalysis(
            symbol=symbol,
            timeframe=timeframe,
            coverage_start=oi_points[0].timestamp if oi_points else None,
            coverage_end=oi_points[-1].timestamp if oi_points else None,
        )
        if not candles or len(oi_points) < 2:
            return analysis

        window = self._resolve_window(timeframe)
        oi_timestamps = [p.timestamp for p in oi_points]

        return analysis.model_copy(
            update={
                "current_regime": self._classify_regime(candles, oi_points, oi_timestamps, window),
                "qualified_events": self._qualify_events(
                    candles, oi_points, oi_timestamps, structure_events, window
                ),
            }
        )

    # ------------------------------------------------------------------
    # Current regime (rolling window over the series tail)
    # ------------------------------------------------------------------

    def _classify_regime(
        self,
        candles: list[Candle],
        oi_points: list[OpenInterestPoint],
        oi_timestamps: list[datetime],
        window: int,
    ) -> OIRegimeReading | None:
        if len(candles) < window:
            return None
        w_candles = candles[-window:]
        changes = self._window_changes(w_candles, oi_points, oi_timestamps)
        if changes is None:
            return None
        price_change, oi_change = changes

        regime = self._regime_for(price_change, oi_change)
        return OIRegimeReading(
            symbol=candles[0].symbol,
            timeframe=candles[0].timeframe,
            timestamp=w_candles[-1].timestamp,
            regime=regime,
            price_change_pct=price_change,
            oi_change_pct=oi_change,
            window_candles=window,
            intensity=self._intensity(price_change, oi_change, regime),
            description=_REGIME_DESCRIPTIONS[regime],
        )

    def _regime_for(self, price_change: float, oi_change: float) -> OIRegime:
        if abs(price_change) < self._min_price_change or abs(oi_change) < self._min_oi_change:
            return OIRegime.FLAT
        if price_change > 0:
            return OIRegime.LONG_BUILDUP if oi_change > 0 else OIRegime.SHORT_COVERING
        return OIRegime.SHORT_BUILDUP if oi_change > 0 else OIRegime.LONG_LIQUIDATION

    def _intensity(self, price_change: float, oi_change: float, regime: OIRegime) -> float:
        if regime is OIRegime.FLAT:
            return 0.0
        # Each axis contributes up to 50, saturating at 4x its significance
        # floor -- so a reading right at the floors scores 25 and a clearly
        # displaced one approaches 100.
        price_score = 50.0 * min(1.0, abs(price_change) / (4 * self._min_price_change))
        oi_score = 50.0 * min(1.0, abs(oi_change) / (4 * self._min_oi_change))
        return min(100.0, price_score + oi_score)

    # ------------------------------------------------------------------
    # Structure event qualification
    # ------------------------------------------------------------------

    def _qualify_events(
        self,
        candles: list[Candle],
        oi_points: list[OpenInterestPoint],
        oi_timestamps: list[datetime],
        structure_events: list[MarketStructure],
        window: int,
    ) -> list[OIQualifiedEvent]:
        ts_to_idx = {c.timestamp: i for i, c in enumerate(candles)}
        results: list[OIQualifiedEvent] = []

        for event in sorted(structure_events, key=lambda e: e.timestamp):
            if event.event not in _QUALIFIED_EVENTS:
                continue
            event_idx = ts_to_idx.get(event.timestamp)
            if event_idx is None:
                continue
            # Measure OI from `window` candles before the event through one
            # candle after it: the breaking candle's own OI change only lands
            # at the *next* OI sample (samples mark period ends), so a
            # sweep's liquidation flush is invisible without that extra step.
            start_idx = max(0, event_idx - window + 1)
            end_idx = min(event_idx + 1, len(candles) - 1)
            changes = self._window_changes(
                candles[start_idx : end_idx + 1], oi_points, oi_timestamps
            )
            if changes is None:
                continue
            _, oi_delta = changes

            participation = self._participation_for(event.event, oi_delta)
            results.append(
                OIQualifiedEvent(
                    symbol=event.symbol,
                    timeframe=event.timeframe,
                    event_timestamp=event.timestamp,
                    event_type=event.event,
                    direction=event.direction,
                    price_level=event.price_level,
                    oi_delta_pct=oi_delta,
                    participation=participation,
                    description=_describe_event(event.event, participation, oi_delta),
                )
            )

        return results

    def _participation_for(self, event: StructureEvent, oi_delta: float) -> OIParticipation:
        if event is StructureEvent.LIQUIDITY_SWEEP and oi_delta <= -self._flush_oi_drop:
            return OIParticipation.FLUSH
        if oi_delta >= self._min_oi_change:
            return OIParticipation.NEW_MONEY
        if oi_delta <= -self._min_oi_change:
            return OIParticipation.COVERING
        return OIParticipation.FLAT

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_window(self, tf: TimeFrame) -> int:
        if self._window_override is not None:
            return self._window_override
        return _TIMEFRAME_WINDOW.get(tf, 10)

    def _window_changes(
        self,
        w_candles: list[Candle],
        oi_points: list[OpenInterestPoint],
        oi_timestamps: list[datetime],
    ) -> tuple[float, float] | None:
        """Fractional (price, OI) change across `w_candles`, or ``None`` if uncovered."""
        oi_start = _oi_at(oi_points, oi_timestamps, w_candles[0].timestamp)
        oi_end = _oi_at(oi_points, oi_timestamps, w_candles[-1].timestamp)
        if oi_start is None or oi_end is None or oi_start.open_interest == 0:
            return None
        price_start = w_candles[0].close
        if price_start == 0:
            return None
        price_change = (w_candles[-1].close - price_start) / price_start
        oi_change = (oi_end.open_interest - oi_start.open_interest) / oi_start.open_interest
        return price_change, oi_change


def _oi_at(
    oi_points: list[OpenInterestPoint], oi_timestamps: list[datetime], timestamp: datetime
) -> OpenInterestPoint | None:
    """The most recent OI sample at or before `timestamp`, or ``None``."""
    idx = bisect.bisect_right(oi_timestamps, timestamp) - 1
    return oi_points[idx] if idx >= 0 else None


def _describe_event(
    event: StructureEvent, participation: OIParticipation, oi_delta: float
) -> str:
    pct = abs(oi_delta) * 100
    name = {
        StructureEvent.BREAK_OF_STRUCTURE: "BOS",
        StructureEvent.CHANGE_OF_CHARACTER: "CHoCH",
        StructureEvent.LIQUIDITY_SWEEP: "sweep",
    }[event]
    if participation is OIParticipation.FLUSH:
        return f"{name} with OI dropping {pct:.1f}% — leveraged positions flushed"
    if participation is OIParticipation.NEW_MONEY:
        return f"{name} with OI rising {pct:.1f}% — new positions behind the move"
    if participation is OIParticipation.COVERING:
        return f"{name} with OI falling {pct:.1f}% — move driven by position unwinding"
    return f"{name} with no meaningful OI change"
