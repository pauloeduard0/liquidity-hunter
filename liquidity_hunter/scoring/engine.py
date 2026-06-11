"""Liquidity zone scoring engine."""

from liquidity_hunter.core.domain import LiquidityZone, TimeFrame
from liquidity_hunter.scoring.models import ScoredLiquidityZone
from liquidity_hunter.scoring.weights import DEFAULT_TIMEFRAME_WEIGHTS

_WEIGHT_SUM_TOLERANCE = 1e-9


class LiquidityScoringEngine:
    """Ranks `LiquidityZone` objects by their relevance as liquidity targets.

    The composite `score` (0-100) is a weighted sum of three factors, each
    normalized to `[0, 100]`:

    1. **Distance score** — how close the zone is to `current_price`. Decays
       linearly from 100 (at the current price) to 0 at `max_distance_pct`
       away, and stays at 0 beyond that.
    2. **Touch score** — `zone.strength * 100`. For equal-level zones,
       `strength` already reflects the number of touches; for swing points
       it reflects prominence.
    3. **Timeframe score** — `timeframe_weights[zone.timeframe] * 100`.
       Higher timeframes represent more structurally significant liquidity.

    See `liquidity_hunter/docs/scoring.md` for the full methodology and
    worked examples.
    """

    def __init__(
        self,
        distance_weight: float = 0.4,
        touch_weight: float = 0.4,
        timeframe_weight: float = 0.2,
        max_distance_pct: float = 0.05,
        timeframe_weights: dict[TimeFrame, float] | None = None,
    ) -> None:
        weights = (distance_weight, touch_weight, timeframe_weight)
        if any(weight < 0 for weight in weights):
            raise ValueError("distance_weight, touch_weight, and timeframe_weight must be >= 0")
        if abs(sum(weights) - 1.0) > _WEIGHT_SUM_TOLERANCE:
            raise ValueError("distance_weight + touch_weight + timeframe_weight must equal 1.0")
        if max_distance_pct <= 0:
            raise ValueError("max_distance_pct must be > 0")

        self._distance_weight = distance_weight
        self._touch_weight = touch_weight
        self._timeframe_weight = timeframe_weight
        self._max_distance_pct = max_distance_pct
        self._timeframe_weights = (
            timeframe_weights if timeframe_weights is not None else DEFAULT_TIMEFRAME_WEIGHTS
        )

    def score(
        self, zones: list[LiquidityZone], current_price: float
    ) -> list[ScoredLiquidityZone]:
        """Score `zones` relative to `current_price`.

        Returns the zones as `ScoredLiquidityZone`, sorted by descending
        `score` (the most relevant liquidity targets first).
        """
        if current_price <= 0:
            raise ValueError("current_price must be > 0")

        scored = [self._score_zone(zone, current_price) for zone in zones]
        return sorted(scored, key=lambda scored_zone: scored_zone.score, reverse=True)

    def _score_zone(self, zone: LiquidityZone, current_price: float) -> ScoredLiquidityZone:
        reference_price = (zone.price_high + zone.price_low) / 2
        distance_pct = abs(reference_price - current_price) / current_price

        distance_score = self._distance_score(distance_pct)
        touch_score = zone.strength * 100.0
        timeframe_score = self._timeframe_weights.get(zone.timeframe, 0.0) * 100.0

        composite = (
            distance_score * self._distance_weight
            + touch_score * self._touch_weight
            + timeframe_score * self._timeframe_weight
        )
        composite = min(100.0, max(0.0, composite))

        return ScoredLiquidityZone(
            zone=zone,
            score=composite,
            distance_score=distance_score,
            touch_score=touch_score,
            timeframe_score=timeframe_score,
        )

    def _distance_score(self, distance_pct: float) -> float:
        proximity = 1.0 - distance_pct / self._max_distance_pct
        return min(100.0, max(0.0, proximity * 100.0))
