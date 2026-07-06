"""Liquidity-hunt state entities.

A descriptive reading of *who is the resting liquidity* of the current move.
When the current timeframe's structure runs counter to the higher-timeframe
trend, the counter-trend entrants' stops and projected liquidation levels are
the nearby fuel; these entities describe which of those pools are mapped, and
how far their capture has progressed. Observations only — no signals or
recommendations.
"""

from datetime import datetime

from pydantic import Field

from liquidity_hunter.core.domain.base import DomainModel
from liquidity_hunter.core.domain.enums import (
    LiquidityHuntPhase,
    LiquidityHuntTargetKind,
    MarketDirection,
    RetailPositioning,
    TimeFrame,
)


class LiquidityHuntTarget(DomainModel):
    """One nearby opposing-liquidity pool tracked by the hunt state.

    A ``captured`` pool was consumed *after* the counter-trend structure began
    (``captured_at`` is the sweeping/liquidating candle's timestamp); an
    intact pool still rests beyond current price.
    """

    kind: LiquidityHuntTargetKind
    label: str
    price_level: float = Field(gt=0)
    captured: bool = False
    captured_at: datetime | None = None


class LiquidityHuntState(DomainModel):
    """Who is the resting liquidity right now, and how far its capture went.

    ``hunted_side`` is the positioning side whose stops/liquidations are the
    nearby fuel: SHORT during a bearish correction inside a bullish
    higher-timeframe trend (sellers of the correction get swept), LONG in the
    mirror case, NEUTRAL when structure is aligned. ``targets`` lists the
    nearest mapped pools (capped for display), while ``targets_captured`` /
    ``targets_total`` count the full mapped set — the CAPTURED phase requires
    the full set consumed *and* open interest no longer unwinding against the
    hunted side.
    """

    symbol: str
    timeframe: TimeFrame
    phase: LiquidityHuntPhase
    hunted_side: RetailPositioning
    correction_direction: MarketDirection | None = None
    counter_structure_timestamp: datetime | None = None
    targets: list[LiquidityHuntTarget] = Field(default_factory=list)
    targets_captured: int = Field(default=0, ge=0)
    targets_total: int = Field(default=0, ge=0)
    oi_unwinding: bool = False
    last_flush_timestamp: datetime | None = None
    captured_at: datetime | None = None
    description: str
