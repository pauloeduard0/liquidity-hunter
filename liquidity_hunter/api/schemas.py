"""Pydantic response models for the `api` layer.

`DashboardData` is a plain `dataclass`, so `DashboardDataResponse` mirrors
its fields as a `pydantic.BaseModel` (with `from_attributes=True`) to make
it serializable to JSON. The nested domain types (`Candle`, `LiquidityZone`,
`MarketStructure`, `ScoredLiquidityZone`, `RetailBiasEstimate`) are already
`DomainModel`s and serialize as-is.
"""

from pydantic import BaseModel, ConfigDict

from liquidity_hunter.core.domain import (
    Candle,
    LeverageLiquidationMap,
    LiquidityHeatmap,
    LiquidityHuntState,
    LiquidityZone,
    ManipulationCycle,
    MarketDirection,
    MarketNarrative,
    MarketStructure,
    OIAnalysis,
    TimeFrame,
)
from liquidity_hunter.core.domain.behavior_divergence import BehaviorDivergence
from liquidity_hunter.core.domain.poi_zone import POIZone, RTOSweepEvent
from liquidity_hunter.psychology import RetailBiasEstimate
from liquidity_hunter.scoring import ScoredLiquidityZone


class DashboardDataResponse(BaseModel):
    """JSON representation of `app.dashboard_data.DashboardData`."""

    model_config = ConfigDict(from_attributes=True)

    symbol: str
    timeframe: TimeFrame
    candles: list[Candle]
    current_price: float
    higher_timeframe_direction: MarketDirection
    liquidity_zones: list[LiquidityZone]
    ranked_zones: list[ScoredLiquidityZone]
    market_structure_events: list[MarketStructure]
    internal_structure_events: list[MarketStructure]
    retail_bias: RetailBiasEstimate
    poi_zones: list[POIZone]
    poi_sweep_events: list[RTOSweepEvent]
    manipulation_cycles: list[ManipulationCycle]
    behavior_divergences: list[BehaviorDivergence]
    liquidity_heatmap: LiquidityHeatmap | None = None
    liquidation_map: LeverageLiquidationMap | None = None
    narrative: MarketNarrative | None = None
    oi_analysis: OIAnalysis | None = None
    liquidity_hunt: LiquidityHuntState | None = None
