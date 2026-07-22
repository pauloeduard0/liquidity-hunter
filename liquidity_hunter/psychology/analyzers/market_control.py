"""Market-control analyzer: who is in control, from CVD aggression × OI.

Reads taker aggression (Cumulative Volume Delta over a recent window) against
open interest to classify the current joint regime on the *aggression* axis and
credit a controlling side only when fresh money backs the aggression. The
output is a single ``MarketControlState`` snapshot — a descriptive "who's in
control right now" reading, not a signal.
"""

import bisect
from dataclasses import dataclass
from datetime import datetime

from liquidity_hunter.core.domain import (
    Candle,
    MarketControlPoint,
    MarketControlSide,
    MarketControlState,
    OIRegime,
    OpenInterestPoint,
    TimeFrame,
)
from liquidity_hunter.indicators import volume_delta

# Candles per analysis window, per timeframe. Mirrors the OIRegimeAnalyzer
# window so the CVD aggression and the OI change are measured over the same
# horizon (they are meant to be read jointly).
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

# When OI rises, it *confirms* the aggressor is opening fresh positions, so the
# conviction is amplified toward the full CVD magnitude. When OI falls, the
# aggression is position-closing (covering/liquidation), so conviction is
# attenuated — the move is exhausting, not fresh. Flat OI sits between.
_OI_CONFIRM_FACTOR = 1.0
_OI_FLAT_FACTOR = 0.55
_OI_DIVERGE_FACTOR = 0.35


@dataclass(frozen=True)
class _Reading:
    """One trailing window's raw control computation."""

    cvd_change: float
    cvd_ratio: float
    oi_change: float
    regime: OIRegime
    control_score: float
    controller: MarketControlSide


class MarketControlAnalyzer:
    """Classifies who controls the tape from CVD aggression and open interest.

    Parameters
    ----------
    window_size:
        Candles per window. ``None`` resolves per timeframe from
        ``_TIMEFRAME_WINDOW``.
    min_cvd_ratio:
        Minimum absolute ``cvd_change / window_volume`` for the aggression to
        be directional (below it the regime is ``FLAT``/``BALANCED``).
    min_oi_change_pct:
        Minimum absolute fractional OI change for OI to count as rising/falling
        (below it OI is treated as flat).
    neutral_threshold:
        Minimum ``|control_score|`` (0-100) for a side to be credited as the
        controller (guards against a marginal reading flapping sides).
    """

    def __init__(
        self,
        window_size: int | None = None,
        min_cvd_ratio: float = 0.06,
        min_oi_change_pct: float = 0.003,
        neutral_threshold: float = 12.0,
    ) -> None:
        self._window_override = window_size
        self._min_cvd_ratio = min_cvd_ratio
        self._min_oi_change = min_oi_change_pct
        self._neutral_threshold = neutral_threshold

    def analyze(
        self,
        candles: list[Candle],
        open_interest: list[OpenInterestPoint],
    ) -> MarketControlState | None:
        if not candles:
            return None
        window = self._resolve_window(candles[0].timeframe)
        if len(candles) < window:
            return None

        oi_points = sorted(open_interest, key=lambda p: p.timestamp)
        oi_ts = [p.timestamp for p in oi_points]

        # Rolling reading per candle (for the chart oscillator) plus the final
        # snapshot. Each end index evaluates the trailing `window`.
        series: list[MarketControlPoint] = []
        for end in range(window - 1, len(candles)):
            reading = self._evaluate(candles[end - window + 1 : end + 1], oi_points, oi_ts)
            if reading is None:
                continue
            series.append(
                MarketControlPoint(
                    timestamp=candles[end].timestamp,
                    control_score=reading.control_score,
                    controller=reading.controller,
                )
            )

        final = self._evaluate(candles[-window:], oi_points, oi_ts)
        if final is None:
            return None

        return MarketControlState(
            symbol=candles[-1].symbol,
            timeframe=candles[-1].timeframe,
            timestamp=candles[-1].timestamp,
            controller=final.controller,
            regime=final.regime,
            cvd_change=final.cvd_change,
            cvd_change_ratio=final.cvd_ratio,
            oi_change_pct=final.oi_change,
            conviction=abs(final.control_score),
            control_score=final.control_score,
            fade_warning=final.controller is not MarketControlSide.BALANCED,
            window_candles=window,
            description=_describe(
                final.controller,
                final.regime,
                final.cvd_ratio,
                final.oi_change,
                final.control_score,
            ),
            series=series,
        )

    def _evaluate(
        self,
        w_candles: list[Candle],
        oi_points: list[OpenInterestPoint],
        oi_ts: list[datetime],
    ) -> "_Reading | None":
        """The control reading for one trailing window, or ``None`` if uncovered."""
        oi_change = self._oi_change(w_candles, oi_points, oi_ts)
        if oi_change is None:
            return None
        # Aggression over the window: net taker delta, normalized by the total
        # volume traded so it is a comparable [-1, 1] ratio across symbols.
        cvd_change = sum(volume_delta(c) for c in w_candles)
        total_volume = sum(c.volume for c in w_candles)
        cvd_ratio = cvd_change / total_volume if total_volume > 0 else 0.0
        cvd_ratio = max(-1.0, min(1.0, cvd_ratio))

        regime = self._regime_for(cvd_ratio, oi_change)
        control_score = self._control_score(cvd_ratio, oi_change)
        controller = self._controller_for(regime, control_score)
        return _Reading(cvd_change, cvd_ratio, oi_change, regime, control_score, controller)

    # ------------------------------------------------------------------

    def _regime_for(self, cvd_ratio: float, oi_change: float) -> OIRegime:
        if abs(cvd_ratio) < self._min_cvd_ratio or abs(oi_change) < self._min_oi_change:
            return OIRegime.FLAT
        if cvd_ratio > 0:
            return OIRegime.LONG_BUILDUP if oi_change > 0 else OIRegime.SHORT_COVERING
        return OIRegime.SHORT_BUILDUP if oi_change > 0 else OIRegime.LONG_LIQUIDATION

    def _control_score(self, cvd_ratio: float, oi_change: float) -> float:
        # Continuous conviction from aggression magnitude, saturating at 4x the
        # floor. Deliberately *not* zeroed below the regime floor: the oscillator
        # should always reflect the live aggression (no dead zone / visual
        # vacuum) — the floor only governs whether a *side is credited*
        # (`_controller_for`), coloring a weak bar dim/balanced instead of
        # blanking it.
        base = min(1.0, abs(cvd_ratio) / (4 * self._min_cvd_ratio))
        if oi_change >= self._min_oi_change:
            factor = _OI_CONFIRM_FACTOR
        elif oi_change <= -self._min_oi_change:
            factor = _OI_DIVERGE_FACTOR
        else:
            factor = _OI_FLAT_FACTOR
        magnitude = 100.0 * base * factor
        return magnitude if cvd_ratio > 0 else -magnitude

    def _controller_for(self, regime: OIRegime, control_score: float) -> MarketControlSide:
        # Only the OI-rising (new-money) quadrants credit a controlling side;
        # covering/liquidation are position-closing, so no conviction-backed
        # control. The score threshold guards against a marginal reading.
        if abs(control_score) < self._neutral_threshold:
            return MarketControlSide.BALANCED
        if regime is OIRegime.LONG_BUILDUP:
            return MarketControlSide.BUYERS
        if regime is OIRegime.SHORT_BUILDUP:
            return MarketControlSide.SELLERS
        return MarketControlSide.BALANCED

    def _resolve_window(self, tf: TimeFrame) -> int:
        if self._window_override is not None:
            return self._window_override
        return _TIMEFRAME_WINDOW.get(tf, 10)

    def _oi_change(
        self,
        w_candles: list[Candle],
        oi_points: list[OpenInterestPoint],
        oi_ts: list[datetime],
    ) -> float | None:
        """Fractional OI change across the window, or ``None`` if uncovered."""
        if len(oi_points) < 2:
            return None
        oi_start = _oi_at(oi_points, oi_ts, w_candles[0].timestamp)
        oi_end = _oi_at(oi_points, oi_ts, w_candles[-1].timestamp)
        if oi_start is None or oi_end is None or oi_start.open_interest == 0:
            return None
        return (oi_end.open_interest - oi_start.open_interest) / oi_start.open_interest


def _oi_at(
    oi_points: list[OpenInterestPoint], oi_timestamps: list[datetime], timestamp: datetime
) -> OpenInterestPoint | None:
    """The most recent OI sample at or before `timestamp`, or ``None``."""
    idx = bisect.bisect_right(oi_timestamps, timestamp) - 1
    return oi_points[idx] if idx >= 0 else None


def _describe(
    controller: MarketControlSide,
    regime: OIRegime,
    cvd_ratio: float,
    oi_change: float,
    control_score: float,
) -> str:
    cvd_pct = cvd_ratio * 100
    oi_pct = oi_change * 100
    ctx = f"(CVD {cvd_pct:+.0f}% agg · OI {oi_pct:+.1f}%, score {control_score:+.0f})"
    if controller is MarketControlSide.BUYERS:
        return f"Buyers in control — new longs entering behind buy aggression {ctx}"
    if controller is MarketControlSide.SELLERS:
        return f"Sellers in control — new shorts entering behind sell aggression {ctx}"
    if regime is OIRegime.SHORT_COVERING:
        return f"Balanced — buy pressure is shorts covering, no new money {ctx}"
    if regime is OIRegime.LONG_LIQUIDATION:
        return f"Balanced — sell pressure is longs liquidating, no new money {ctx}"
    return f"Balanced — no decisive aggression or open-interest displacement {ctx}"
