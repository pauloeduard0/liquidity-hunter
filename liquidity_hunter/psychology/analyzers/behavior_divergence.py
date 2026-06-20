"""Behavioral divergence detector.

Cross-references ``volume_delta_series`` with ``LiquidityZone`` proximity
and ``MarketStructure`` events to detect when institutional flow (volume
delta) opposes the visible price direction — e.g. price rising while net
taker flow is selling near a buy-side zone (institutional distribution).
"""

from collections.abc import Sequence

from liquidity_hunter.core.domain import (
    Candle,
    DivergenceType,
    LiquiditySide,
    LiquidityZone,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.core.domain.behavior_divergence import BehaviorDivergence

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

_EXHAUSTION_MIN_POST_BOS = 4
_EXHAUSTION_VD_DECLINE_THRESHOLD = 0.3
_ABSORPTION_VOL_RATIO_THRESHOLD = 1.5


class BehaviorDivergenceAnalyzer:
    """Detects divergence between price action and volume delta.

    Parameters
    ----------
    window_size:
        Candles per analysis window.  ``None`` resolves per timeframe from
        ``_TIMEFRAME_WINDOW``.
    proximity_pct:
        How close price must be to a zone boundary to qualify, as a
        fraction of price (e.g. 0.02 = 2%).
    min_price_change_pct:
        Minimum absolute price change over a window for
        distribution/accumulation detection.
    min_vd_ratio:
        Minimum ``abs(avg_vd) / avg_volume`` over a window for
        distribution/accumulation detection.
    """

    def __init__(
        self,
        window_size: int | None = None,
        proximity_pct: float = 0.02,
        min_price_change_pct: float = 0.005,
        min_vd_ratio: float = 0.1,
    ) -> None:
        self._window_override = window_size
        self._proximity_pct = proximity_pct
        self._min_price_change = min_price_change_pct
        self._min_vd_ratio = min_vd_ratio

    def analyze(
        self,
        candles: list[Candle],
        volume_deltas: Sequence[float],
        liquidity_zones: list[LiquidityZone],
        structure_events: list[MarketStructure],
    ) -> list[BehaviorDivergence]:
        if len(candles) < 3:
            return []

        window = self._resolve_window(candles[0].timeframe)
        if len(candles) < window:
            return []

        results: list[BehaviorDivergence] = []
        results.extend(
            self._detect_zone_divergences(candles, volume_deltas, liquidity_zones, window)
        )
        results.extend(
            self._detect_exhaustion(candles, volume_deltas, structure_events, window)
        )
        results.extend(
            self._detect_absorption(candles, volume_deltas, liquidity_zones, window)
        )

        return _deduplicate(sorted(results, key=lambda d: d.timestamp), window)

    # ------------------------------------------------------------------
    # Distribution / Accumulation (zone-anchored)
    # ------------------------------------------------------------------

    def _detect_zone_divergences(
        self,
        candles: list[Candle],
        volume_deltas: Sequence[float],
        zones: list[LiquidityZone],
        window: int,
    ) -> list[BehaviorDivergence]:
        active = [z for z in zones if not z.is_mitigated]
        buy_zones = [z for z in active if z.side == LiquiditySide.BUY_SIDE]
        sell_zones = [z for z in active if z.side == LiquiditySide.SELL_SIDE]
        results: list[BehaviorDivergence] = []

        step = max(1, window // 2)
        for end_idx in range(window - 1, len(candles), step):
            start_idx = end_idx - window + 1
            w_candles = candles[start_idx : end_idx + 1]
            w_vd = list(volume_deltas[start_idx : end_idx + 1])

            price_start = w_candles[0].open
            price_end = w_candles[-1].close
            if price_start == 0:
                continue
            price_change = (price_end - price_start) / price_start

            if abs(price_change) < self._min_price_change:
                continue

            avg_vd = sum(w_vd) / len(w_vd)
            avg_vol = sum(c.volume for c in w_candles) / len(w_candles)
            if avg_vol == 0:
                continue

            vd_ratio = abs(avg_vd) / avg_vol
            if vd_ratio < self._min_vd_ratio:
                continue

            price_rising = price_change > 0
            vd_positive = avg_vd > 0

            if price_rising == vd_positive:
                continue

            if price_rising:
                nearest = self._find_nearest_zone(price_end, buy_zones)
                if nearest is None:
                    continue
                div_type = DivergenceType.DISTRIBUTION
                direction = MarketDirection.BULLISH
            else:
                nearest = self._find_nearest_zone(price_end, sell_zones)
                if nearest is None:
                    continue
                div_type = DivergenceType.ACCUMULATION
                direction = MarketDirection.BEARISH

            zone_mid = (nearest.price_high + nearest.price_low) / 2
            confidence = self._divergence_confidence(
                vd_ratio, price_change, price_end, zone_mid
            )

            results.append(
                BehaviorDivergence(
                    symbol=candles[0].symbol,
                    timeframe=candles[0].timeframe,
                    timestamp=w_candles[-1].timestamp,
                    divergence_type=div_type,
                    direction=direction,
                    price_level=price_end,
                    volume_delta_avg=avg_vd,
                    price_change_pct=price_change,
                    nearest_zone_side=nearest.side,
                    nearest_zone_price_low=nearest.price_low,
                    nearest_zone_price_high=nearest.price_high,
                    confidence=confidence,
                    description=_describe_zone_divergence(
                        div_type, direction, price_change, avg_vd
                    ),
                )
            )

        return results

    # ------------------------------------------------------------------
    # Exhaustion (structure-anchored)
    # ------------------------------------------------------------------

    def _detect_exhaustion(
        self,
        candles: list[Candle],
        volume_deltas: Sequence[float],
        structure_events: list[MarketStructure],
        window: int,
    ) -> list[BehaviorDivergence]:
        bos_events = [
            e for e in structure_events if e.event == StructureEvent.BREAK_OF_STRUCTURE
        ]
        ts_to_idx = {c.timestamp: i for i, c in enumerate(candles)}
        results: list[BehaviorDivergence] = []

        for bos in bos_events:
            bos_idx = ts_to_idx.get(bos.timestamp)
            if bos_idx is None:
                continue

            end_idx = min(bos_idx + window, len(candles) - 1)
            span = end_idx - bos_idx
            if span < _EXHAUSTION_MIN_POST_BOS:
                continue

            post_vd = list(volume_deltas[bos_idx : end_idx + 1])
            price_start = candles[bos_idx].open
            price_end = candles[end_idx].close
            if price_start == 0:
                continue
            price_change = (price_end - price_start) / price_start

            if bos.direction == MarketDirection.BULLISH and price_change <= 0:
                continue
            if bos.direction == MarketDirection.BEARISH and price_change >= 0:
                continue

            mid = len(post_vd) // 2
            first_mag = sum(abs(v) for v in post_vd[:mid]) / mid if mid > 0 else 0
            second_mag = (
                sum(abs(v) for v in post_vd[mid:]) / (len(post_vd) - mid)
                if len(post_vd) > mid
                else 0
            )
            if first_mag == 0:
                continue

            decline = 1 - (second_mag / first_mag)
            if decline < _EXHAUSTION_VD_DECLINE_THRESHOLD:
                continue

            confidence = min(95.0, 40.0 + decline * 55.0)
            avg_vd = sum(post_vd) / len(post_vd)

            results.append(
                BehaviorDivergence(
                    symbol=candles[0].symbol,
                    timeframe=candles[0].timeframe,
                    timestamp=candles[end_idx].timestamp,
                    divergence_type=DivergenceType.EXHAUSTION,
                    direction=bos.direction,
                    price_level=price_end,
                    volume_delta_avg=avg_vd,
                    price_change_pct=price_change,
                    confidence=confidence,
                    description=(
                        f"Volume delta declining {decline:.0%} after"
                        f" {bos.direction.value} BOS while price continues"
                    ),
                )
            )

        return results

    # ------------------------------------------------------------------
    # Absorption (zone-anchored, high volume + small price movement)
    # ------------------------------------------------------------------

    def _detect_absorption(
        self,
        candles: list[Candle],
        volume_deltas: Sequence[float],
        zones: list[LiquidityZone],
        window: int,
    ) -> list[BehaviorDivergence]:
        active = [z for z in zones if not z.is_mitigated]
        if not active:
            return []

        overall_avg_vol = sum(c.volume for c in candles) / len(candles)
        if overall_avg_vol == 0:
            return []

        results: list[BehaviorDivergence] = []
        step = max(1, window // 2)

        for end_idx in range(window - 1, len(candles), step):
            start_idx = end_idx - window + 1
            w_candles = candles[start_idx : end_idx + 1]
            w_vd = list(volume_deltas[start_idx : end_idx + 1])

            price_start = w_candles[0].open
            price_end = w_candles[-1].close
            if price_start == 0:
                continue
            price_change = abs((price_end - price_start) / price_start)

            if price_change > self._min_price_change:
                continue

            avg_vol = sum(c.volume for c in w_candles) / len(w_candles)
            vol_ratio = avg_vol / overall_avg_vol
            if vol_ratio < _ABSORPTION_VOL_RATIO_THRESHOLD:
                continue

            nearest = self._find_nearest_zone(price_end, active)
            if nearest is None:
                continue

            avg_vd = sum(w_vd) / len(w_vd)
            direction = MarketDirection.BULLISH if avg_vd >= 0 else MarketDirection.BEARISH
            zone_mid = (nearest.price_high + nearest.price_low) / 2
            zone_dist = abs(price_end - zone_mid) / price_end if price_end else 1.0
            proximity_score = max(0.0, 20.0 * (1 - zone_dist / self._proximity_pct))
            confidence = min(95.0, 30.0 + (vol_ratio - 1) * 25.0 + proximity_score)

            results.append(
                BehaviorDivergence(
                    symbol=candles[0].symbol,
                    timeframe=candles[0].timeframe,
                    timestamp=w_candles[-1].timestamp,
                    divergence_type=DivergenceType.ABSORPTION,
                    direction=direction,
                    price_level=price_end,
                    volume_delta_avg=avg_vd,
                    price_change_pct=price_change if price_end >= price_start else -price_change,
                    nearest_zone_side=nearest.side,
                    nearest_zone_price_low=nearest.price_low,
                    nearest_zone_price_high=nearest.price_high,
                    confidence=confidence,
                    description=(
                        f"High volume ({vol_ratio:.1f}x avg) absorbed near"
                        f" {nearest.side.value} zone with minimal price movement"
                    ),
                )
            )

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_window(self, tf: TimeFrame) -> int:
        if self._window_override is not None:
            return self._window_override
        return _TIMEFRAME_WINDOW.get(tf, 10)

    def _find_nearest_zone(
        self, price: float, zones: list[LiquidityZone]
    ) -> LiquidityZone | None:
        threshold = price * self._proximity_pct
        best: LiquidityZone | None = None
        best_dist = float("inf")
        for zone in zones:
            zone_mid = (zone.price_high + zone.price_low) / 2
            dist = abs(price - zone_mid)
            if dist <= threshold and dist < best_dist:
                best = zone
                best_dist = dist
        return best

    def _divergence_confidence(
        self,
        vd_ratio: float,
        price_change: float,
        price: float,
        zone_mid: float,
    ) -> float:
        base = 40.0
        vd_score = min(25.0, vd_ratio * 50.0)
        zone_dist = abs(price - zone_mid) / price if price else 1.0
        proximity_score = max(0.0, 20.0 * (1 - zone_dist / self._proximity_pct))
        price_score = min(15.0, abs(price_change) / self._min_price_change * 5.0)
        return min(95.0, base + vd_score + proximity_score + price_score)


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _deduplicate(
    events: list[BehaviorDivergence], window: int
) -> list[BehaviorDivergence]:
    """Keep the highest-confidence event per type within *window* candles."""
    kept: list[BehaviorDivergence] = []
    for ev in events:
        merged = False
        for i, prev in enumerate(kept):
            if prev.divergence_type != ev.divergence_type:
                continue
            gap = abs((ev.timestamp - prev.timestamp).total_seconds())
            if gap > window * 3600 * 4:
                continue
            if ev.confidence > prev.confidence:
                kept[i] = ev
            merged = True
            break
        if not merged:
            kept.append(ev)
    return sorted(kept, key=lambda d: d.timestamp)


def _describe_zone_divergence(
    div_type: DivergenceType,
    direction: MarketDirection,
    price_change: float,
    avg_vd: float,
) -> str:
    pct = abs(price_change) * 100
    if div_type == DivergenceType.DISTRIBUTION:
        return (
            f"Price rising {pct:.1f}% but volume delta negative"
            f" ({avg_vd:+.1f}) — institutional distribution"
        )
    return (
        f"Price falling {pct:.1f}% but volume delta positive"
        f" ({avg_vd:+.1f}) — institutional accumulation"
    )
