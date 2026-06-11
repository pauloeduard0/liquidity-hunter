"""Output models for the psychology layer."""

from datetime import datetime

from pydantic import Field

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import RetailPositioning


class RetailBiasEstimate(DomainModel):
    """An inferred estimate of retail market participants' crowd psychology.

    Unlike `core.domain.RetailBias`, which represents a *measured*
    observation from an external sentiment/positioning source (e.g. a COT
    report or survey), `RetailBiasEstimate` is *inferred* from price
    structure context (trend direction, market structure, liquidity zones)
    by a `RetailBiasEstimator` such as `RetailTrapAnalyzer`.

    This describes likely crowd behavior, not a trade recommendation:
    `dominant_side` is what retail traders are estimated to be doing, not
    what should be done.
    """

    symbol: str
    generated_at: datetime
    dominant_side: RetailPositioning
    confidence: float = Field(ge=0.0, le=100.0)
    explanation: str
