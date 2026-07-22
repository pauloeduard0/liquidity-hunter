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
    HuntCaptureQuality,
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


class LiquidityHuntEpisode(DomainModel):
    """A *concluded* counter-trend hunt from earlier in the visible window.

    Where :class:`LiquidityHuntState` describes only the live snapshot, an
    episode is a past counter-trend leg (structure ran against the
    higher-timeframe trend) that has since resolved — the larger trend resumed
    and consumed the counter-trend entrants. ``start_timestamp`` is the
    counter-trend flip that opened the leg; ``end_timestamp`` is the event that
    realigned structure with the higher timeframe (the capture). Descriptive
    history only, so the chart can mark where prior hunts completed.
    """

    hunted_side: RetailPositioning
    correction_direction: MarketDirection
    start_timestamp: datetime
    end_timestamp: datetime
    # Weighted evidence that closed the hunt at ``end_timestamp`` (sweep / VSA
    # climax-thrust / OI flush / zone mitigation, plus a volume-delta modifier);
    # ``capture_sources`` names the contributing components. A hunt is recorded
    # only when the score reaches the capture threshold.
    capture_score: float = Field(default=0.0, ge=0.0)
    capture_sources: list[str] = Field(default_factory=list)
    # Quality of the grab from CVD-aggression x OI at the grab candle: an
    # ``EXHAUSTION_GRAB`` ran the stops on no new money (reversal-prone), a
    # ``GENUINE_BREAK`` had fresh money behind the capture direction;
    # ``UNKNOWN`` when the market-control series does not cover the grab.
    capture_quality: HuntCaptureQuality = HuntCaptureQuality.UNKNOWN
    description: str


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
    # Quality of the grab from CVD-aggression x OI (``MarketControlState``):
    # a ``GENUINE_BREAK`` capture is backed by fresh money taking the capture
    # side, an ``EXHAUSTION_GRAB`` runs the stops on no new money (short
    # covering / stop-hunting) and is reversal-prone; ``UNKNOWN`` when no
    # market-control reading is available (spot / no OI). Descriptive.
    capture_quality: HuntCaptureQuality = HuntCaptureQuality.UNKNOWN
    description: str
