"""ManipulationCycle domain entity."""

from datetime import datetime

from pydantic import Field

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import (
    LiquiditySide,
    LiquidityZoneType,
    ManipulationCycleStatus,
    ManipulationPhase,
    MarketDirection,
    TimeFrame,
)


class ManipulationCycle(DomainModel):
    """An observed institutional manipulation cycle (accumulation -> sweep -> expansion).

    Describes the three-phase Wyckoff/SMC pattern where price consolidates
    near a liquidity zone (accumulation), sweeps the zone to capture stops
    (manipulation), then moves impulsively in the opposite direction
    (expansion).  ``direction`` is the expansion direction: a bullish cycle
    sweeps sell-side liquidity (lows) then expands upward.

    This is a descriptive observation of likely institutional behavior, not
    a trading signal.
    """

    symbol: str
    timeframe: TimeFrame
    direction: MarketDirection
    phase: ManipulationPhase
    status: ManipulationCycleStatus

    # Target zone
    target_zone_price_low: float = Field(gt=0)
    target_zone_price_high: float = Field(gt=0)
    target_zone_type: LiquidityZoneType
    target_zone_side: LiquiditySide

    # Accumulation
    accumulation_start: datetime
    accumulation_end: datetime
    consolidation_candles: int = Field(ge=0)
    accumulation_avg_volume_delta: float = 0.0

    # Manipulation (sweep)
    sweep_timestamp: datetime | None = None
    sweep_extreme: float | None = Field(default=None, gt=0)
    sweep_volume_delta: float | None = None

    # Expansion
    expansion_timestamp: datetime | None = None
    expansion_price: float | None = Field(default=None, gt=0)
    expansion_volume_delta: float | None = None
