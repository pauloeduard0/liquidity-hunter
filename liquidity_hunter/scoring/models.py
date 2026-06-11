"""Output models for the scoring layer."""

from pydantic import Field

from liquidity_hunter.core.domain import LiquidityZone
from liquidity_hunter.core.domain.base import DomainModel


class ScoredLiquidityZone(DomainModel):
    """A `LiquidityZone` paired with its computed relevance score.

    `score` is a weighted combination of `distance_score`, `touch_score`,
    and `timeframe_score` (each in `[0, 100]`). See
    `LiquidityScoringEngine` and `liquidity_hunter/docs/scoring.md` for the
    full methodology.
    """

    zone: LiquidityZone
    score: float = Field(ge=0.0, le=100.0)
    distance_score: float = Field(ge=0.0, le=100.0)
    touch_score: float = Field(ge=0.0, le=100.0)
    timeframe_score: float = Field(ge=0.0, le=100.0)
