"""Composition root for the research dashboard.

Wires together `data`, `liquidity`, `scoring`, and `psychology` into a
single `DashboardData` snapshot for `dashboard` to render.
"""

from dataclasses import dataclass

from liquidity_hunter.core.domain import (
    Candle,
    LiquidityZone,
    MarketDirection,
    MarketStructure,
    StructureScope,
    TimeFrame,
)
from liquidity_hunter.data import BinanceDataProvider, OHLCVProvider
from liquidity_hunter.liquidity import (
    EqualHighDetector,
    EqualLowDetector,
    SwingHighDetector,
    SwingLowDetector,
    SwingStructureDetector,
)
from liquidity_hunter.psychology import RetailBiasEstimate, RetailTrapAnalyzer
from liquidity_hunter.scoring import LiquidityScoringEngine, ScoredLiquidityZone

DEFAULT_SWING_LOOKBACK = 50
DEFAULT_INTERNAL_SWING_LOOKBACK = 10


@dataclass(frozen=True)
class DashboardData:
    """A snapshot of research data for a single symbol/timeframe."""

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


def _latest_structure_direction(events: list[MarketStructure]) -> MarketDirection:
    """The `direction` of the most recent `MarketStructure` event, or NEUTRAL.

    Used as the higher timeframe trend context: the prevailing bias implied
    by the latest confirmed BOS/CHoCH on the swing structure.
    """
    if not events:
        return MarketDirection.NEUTRAL
    return max(events, key=lambda event: event.timestamp).direction


def load_dashboard_data(
    provider: OHLCVProvider | None = None,
    symbol: str = "BTCUSDT",
    timeframe: TimeFrame = TimeFrame.H1,
    limit: int = 500,
    swing_lookback: int = DEFAULT_SWING_LOOKBACK,
    internal_swing_lookback: int = DEFAULT_INTERNAL_SWING_LOOKBACK,
) -> DashboardData:
    """Fetch candles and assemble liquidity, ranking, and retail bias data."""
    provider = provider if provider is not None else BinanceDataProvider()
    candles = provider.get_ohlcv(symbol, timeframe, limit)

    liquidity_zones = [
        *SwingHighDetector().detect(candles),
        *SwingLowDetector().detect(candles),
        *EqualHighDetector().detect(candles),
        *EqualLowDetector().detect(candles),
    ]

    current_price = candles[-1].close
    ranked_zones = LiquidityScoringEngine().score(liquidity_zones, current_price)

    market_structure_events = SwingStructureDetector(swing_lookback=swing_lookback).detect(
        candles
    )
    internal_structure_events = SwingStructureDetector(
        swing_lookback=internal_swing_lookback, scope=StructureScope.INTERNAL
    ).detect(candles)
    higher_timeframe_direction = _latest_structure_direction(market_structure_events)

    retail_bias = RetailTrapAnalyzer().analyze(
        symbol=symbol,
        higher_timeframe_direction=higher_timeframe_direction,
        market_structure_events=market_structure_events,
        liquidity_zones=liquidity_zones,
        current_price=current_price,
    )

    return DashboardData(
        symbol=symbol,
        timeframe=timeframe,
        candles=candles,
        current_price=current_price,
        higher_timeframe_direction=higher_timeframe_direction,
        liquidity_zones=liquidity_zones,
        ranked_zones=ranked_zones,
        market_structure_events=market_structure_events,
        internal_structure_events=internal_structure_events,
        retail_bias=retail_bias,
    )
