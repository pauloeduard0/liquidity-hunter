"""Volume-Spread-Analysis (VSA) detector.

Reads classic VSA "effort vs result" patterns from a single candle's anatomy
— its spread (high-low range), the position of its close within that range,
its wick rejection, and its **raw** volume relative to the trailing mean —
with ``volume_delta`` (net taker aggression) as a directional confirmation.

Covers the three candle-anatomy patterns not already surfaced by the
window-aggregated :class:`BehaviorDivergenceAnalyzer`:

* **No Supply / No Demand** — a narrow bar on unusually low volume: the
  opposing side has gone quiet.
* **Selling / Buying Climax** — a wide bar on extreme volume with a rejecting
  wick: capitulation preceding a likely reversal.
* **Down Thrust / Up Thrust** — a wick-rejection bar closing back against the
  probe on above-average volume.

This is an *observation* layer; it emits no signals or recommendations.
"""

from collections.abc import Sequence

from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    TimeFrame,
    VolumeSpreadSignal,
    VSAPattern,
)

# Trailing candles used to establish the "normal" spread/volume baseline each
# candle is measured against.  Mirrors ``BehaviorDivergenceAnalyzer`` so the
# two volume layers read the market on the same horizon.
_TIMEFRAME_LOOKBACK: dict[TimeFrame, int] = {
    TimeFrame.M1: 20,
    TimeFrame.M5: 15,
    TimeFrame.M15: 10,
    TimeFrame.M30: 7,
    TimeFrame.H1: 7,
    TimeFrame.H4: 5,
    TimeFrame.D1: 5,
    TimeFrame.W1: 3,
}


class VolumeSpreadAnalyzer:
    """Detects single-candle VSA patterns from spread × close × wick × volume.

    Parameters
    ----------
    lookback:
        Trailing candles for the spread/volume baseline.  ``None`` resolves
        per timeframe from ``_TIMEFRAME_LOOKBACK``.
    narrow_spread_ratio:
        Spread-ratio ceiling for a "narrow" bar (No Supply / No Demand).
    low_volume_ratio:
        Volume-ratio ceiling for "low" volume (No Supply / No Demand).
    wide_spread_ratio:
        Spread-ratio floor for a "wide" bar (climax).
    climax_volume_ratio:
        Volume-ratio floor for "extreme" volume (climax).
    thrust_volume_ratio:
        Volume-ratio floor for "above-average" volume (thrust).
    wick_dominance:
        A rejection wick must be at least this multiple of the opposite wick
        to count as a thrust / climax rejection.
    """

    def __init__(
        self,
        lookback: int | None = None,
        narrow_spread_ratio: float = 0.7,
        low_volume_ratio: float = 0.7,
        wide_spread_ratio: float = 1.8,
        climax_volume_ratio: float = 2.0,
        thrust_volume_ratio: float = 1.2,
        wick_dominance: float = 1.5,
        dedup_window: int | None = None,
    ) -> None:
        self._lookback_override = lookback
        self._narrow_spread = narrow_spread_ratio
        self._low_volume = low_volume_ratio
        self._wide_spread = wide_spread_ratio
        self._climax_volume = climax_volume_ratio
        self._thrust_volume = thrust_volume_ratio
        self._wick_dominance = wick_dominance
        self._dedup_window_override = dedup_window

    def analyze(
        self,
        candles: list[Candle],
        volume_deltas: Sequence[float],
    ) -> list[VolumeSpreadSignal]:
        if len(candles) < 3:
            return []

        lookback = self._resolve_lookback(candles[0].timeframe)
        if len(candles) <= lookback:
            return []

        # (candle index, signal) so dedup can cluster by candle distance.
        raw: list[tuple[int, VolumeSpreadSignal]] = []
        for i in range(lookback, len(candles)):
            signal = self._classify(candles, volume_deltas, i, lookback)
            if signal is not None:
                raw.append((i, signal))

        window = (
            self._dedup_window_override
            if self._dedup_window_override is not None
            else lookback
        )
        return _deduplicate(raw, window)

    # ------------------------------------------------------------------
    # Per-candle classification
    # ------------------------------------------------------------------

    def _classify(
        self,
        candles: list[Candle],
        volume_deltas: Sequence[float],
        i: int,
        lookback: int,
    ) -> VolumeSpreadSignal | None:
        candle = candles[i]
        spread = candle.high - candle.low
        if spread <= 0:
            return None

        prior = candles[i - lookback : i]
        mean_spread = sum(c.high - c.low for c in prior) / len(prior)
        mean_volume = sum(c.volume for c in prior) / len(prior)
        if mean_spread <= 0 or mean_volume <= 0:
            return None

        spread_ratio = spread / mean_spread
        volume_ratio = candle.volume / mean_volume
        close_position = (candle.close - candle.low) / spread
        body_hi = max(candle.open, candle.close)
        body_lo = min(candle.open, candle.close)
        upper_shadow = candle.high - body_hi
        lower_shadow = body_lo - candle.low
        is_up = candle.close >= candle.open
        vd = volume_deltas[i]

        # Priority: climax (most significant) > thrust > no-supply/demand.
        pattern = self._match_climax(
            is_up, spread_ratio, volume_ratio, close_position, upper_shadow, lower_shadow
        )
        if pattern is None:
            pattern = self._match_thrust(
                volume_ratio, close_position, upper_shadow, lower_shadow
            )
        if pattern is None:
            pattern = self._match_quiet(is_up, spread_ratio, volume_ratio)
        if pattern is None:
            return None

        direction = _PATTERN_DIRECTION[pattern]
        confidence = self._confidence(pattern, spread_ratio, volume_ratio, close_position)
        return VolumeSpreadSignal(
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            timestamp=candle.timestamp,
            pattern=pattern,
            direction=direction,
            price_level=candle.close,
            spread_ratio=spread_ratio,
            close_position=close_position,
            volume_ratio=volume_ratio,
            volume_delta=vd,
            confidence=confidence,
            description=_describe(
                pattern, spread_ratio, volume_ratio, close_position, vd
            ),
        )

    def _match_climax(
        self,
        is_up: bool,
        spread_ratio: float,
        volume_ratio: float,
        close_position: float,
        upper_shadow: float,
        lower_shadow: float,
    ) -> VSAPattern | None:
        if spread_ratio < self._wide_spread or volume_ratio < self._climax_volume:
            return None
        # Selling climax: wide down-bar, extreme volume, lower-wick rejection
        # with the close recovering off the low.
        if not is_up and lower_shadow >= upper_shadow * self._wick_dominance:
            if close_position >= 0.3:
                return VSAPattern.SELLING_CLIMAX
        # Buying climax: mirror at the top.
        if is_up and upper_shadow >= lower_shadow * self._wick_dominance:
            if close_position <= 0.7:
                return VSAPattern.BUYING_CLIMAX
        return None

    def _match_thrust(
        self,
        volume_ratio: float,
        close_position: float,
        upper_shadow: float,
        lower_shadow: float,
    ) -> VSAPattern | None:
        if volume_ratio < self._thrust_volume:
            return None
        # Down thrust (bullish pin): lower wick dominates, close high.
        if (
            lower_shadow >= upper_shadow * self._wick_dominance
            and close_position >= 0.6
        ):
            return VSAPattern.DOWN_THRUST
        # Up thrust (bearish pin): upper wick dominates, close low.
        if (
            upper_shadow >= lower_shadow * self._wick_dominance
            and close_position <= 0.4
        ):
            return VSAPattern.UP_THRUST
        return None

    def _match_quiet(
        self, is_up: bool, spread_ratio: float, volume_ratio: float
    ) -> VSAPattern | None:
        if spread_ratio > self._narrow_spread or volume_ratio > self._low_volume:
            return None
        # No Supply: narrow down-bar on low volume — sellers absent.
        if not is_up:
            return VSAPattern.NO_SUPPLY
        # No Demand: narrow up-bar on low volume — buyers absent.
        return VSAPattern.NO_DEMAND

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_lookback(self, tf: TimeFrame) -> int:
        if self._lookback_override is not None:
            return self._lookback_override
        return _TIMEFRAME_LOOKBACK.get(tf, 10)

    def _confidence(
        self,
        pattern: VSAPattern,
        spread_ratio: float,
        volume_ratio: float,
        close_position: float,
    ) -> float:
        if pattern in (VSAPattern.SELLING_CLIMAX, VSAPattern.BUYING_CLIMAX):
            # Both effort (volume) and result (spread) drive a climax.
            vol_score = min(30.0, (volume_ratio - self._climax_volume) * 20.0)
            spread_score = min(20.0, (spread_ratio - self._wide_spread) * 15.0)
            return min(95.0, 45.0 + vol_score + spread_score)
        if pattern in (VSAPattern.DOWN_THRUST, VSAPattern.UP_THRUST):
            vol_score = min(25.0, (volume_ratio - self._thrust_volume) * 20.0)
            # Reward a close deep into the rejecting side of the range.
            reject = close_position if pattern == VSAPattern.DOWN_THRUST else 1 - close_position
            close_score = min(20.0, reject * 20.0)
            return min(90.0, 40.0 + vol_score + close_score)
        # No Supply / No Demand: the quieter, the stronger.
        vol_score = min(25.0, (self._low_volume - volume_ratio) * 40.0)
        spread_score = min(15.0, (self._narrow_spread - spread_ratio) * 30.0)
        return min(85.0, 35.0 + vol_score + spread_score)


# ------------------------------------------------------------------
# Module-level tables and helpers
# ------------------------------------------------------------------

_PATTERN_DIRECTION: dict[VSAPattern, MarketDirection] = {
    VSAPattern.NO_SUPPLY: MarketDirection.BULLISH,
    VSAPattern.NO_DEMAND: MarketDirection.BEARISH,
    VSAPattern.SELLING_CLIMAX: MarketDirection.BULLISH,
    VSAPattern.BUYING_CLIMAX: MarketDirection.BEARISH,
    VSAPattern.DOWN_THRUST: MarketDirection.BULLISH,
    VSAPattern.UP_THRUST: MarketDirection.BEARISH,
}

_PATTERN_LABEL: dict[VSAPattern, str] = {
    VSAPattern.NO_SUPPLY: "No Supply (narrow down-bar, low volume — sellers absent)",
    VSAPattern.NO_DEMAND: "No Demand (narrow up-bar, low volume — buyers absent)",
    VSAPattern.SELLING_CLIMAX: "Selling Climax (wide down-bar, extreme volume, lower wick)",
    VSAPattern.BUYING_CLIMAX: "Buying Climax (wide up-bar, extreme volume, upper-wick rejection)",
    VSAPattern.DOWN_THRUST: "Down Thrust (lower-wick rejection, close high, above-avg volume)",
    VSAPattern.UP_THRUST: "Up Thrust (upper-wick rejection, close low, above-avg volume)",
}


def _deduplicate(
    raw: list[tuple[int, VolumeSpreadSignal]],
    window: int,
) -> list[VolumeSpreadSignal]:
    """Collapse clustered signals of the same pattern.

    A run of adjacent candles all reading the same pattern (e.g. a stretch of
    quiet low-volume bars) would otherwise emit one marker per candle. Within
    ``window`` candles of the last *kept* signal of a given pattern, only the
    highest-confidence one survives; a signal farther than ``window`` from the
    last kept one starts a fresh cluster.  Different patterns never suppress
    each other.  Ties in confidence keep the earlier signal.
    """
    if window <= 0:
        return [sig for _, sig in raw]

    best_by_pattern: dict[VSAPattern, tuple[int, int]] = {}  # pattern -> (kept_idx, out_pos)
    kept: list[tuple[int, VolumeSpreadSignal]] = []
    for idx, sig in raw:
        prev = best_by_pattern.get(sig.pattern)
        if prev is not None and idx - prev[0] <= window:
            # Same cluster: replace the kept signal only if strictly stronger.
            kept_idx, out_pos = prev
            if sig.confidence > kept[out_pos][1].confidence:
                kept[out_pos] = (idx, sig)
                best_by_pattern[sig.pattern] = (idx, out_pos)
            continue
        best_by_pattern[sig.pattern] = (idx, len(kept))
        kept.append((idx, sig))

    kept.sort(key=lambda pair: pair[0])
    return [sig for _, sig in kept]


def _describe(
    pattern: VSAPattern,
    spread_ratio: float,
    volume_ratio: float,
    close_position: float,
    vd: float,
) -> str:
    return (
        f"{_PATTERN_LABEL[pattern]}: spread {spread_ratio:.1f}x, "
        f"volume {volume_ratio:.1f}x avg, close at {close_position:.0%} of range, "
        f"delta {vd:+.0f}"
    )
