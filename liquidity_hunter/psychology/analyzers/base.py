"""Abstract port for retail crowd-psychology estimation."""

from abc import ABC, abstractmethod

from liquidity_hunter.core.domain import LiquidityZone, MarketDirection, MarketStructure
from liquidity_hunter.psychology.models import RetailBiasEstimate


class RetailBiasEstimator(ABC):
    """Estimates retail trader crowd psychology from market context.

    Implementations describe what retail traders are likely thinking and
    doing -- not what should be done. The inputs (trend direction, market
    structure events, liquidity zones, current price) are plain domain
    types that double as a feature set, so a rule-based implementation
    such as `RetailTrapAnalyzer` can later be swapped for a
    machine-learning-based implementation without changing callers.
    """

    @abstractmethod
    def analyze(
        self,
        symbol: str,
        higher_timeframe_direction: MarketDirection,
        market_structure_events: list[MarketStructure],
        liquidity_zones: list[LiquidityZone],
        current_price: float,
    ) -> RetailBiasEstimate:
        """Estimate the dominant retail positioning and its rationale.

        `market_structure_events` and `liquidity_zones` are typically
        lower-timeframe observations relative to `higher_timeframe_direction`.
        """
        raise NotImplementedError
