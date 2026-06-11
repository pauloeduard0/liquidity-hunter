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
    LiquidityZone,
    MarketDirection,
    MarketStructure,
    TimeFrame,
)
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
    retail_bias: RetailBiasEstimate
