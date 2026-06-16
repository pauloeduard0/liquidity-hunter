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
    TimeFrame,
)
from liquidity_hunter.data import BinanceDataProvider, OHLCVProvider
from liquidity_hunter.liquidity import (
    EqualHighDetector,
    EqualLowDetector,
    InternalStructureDetector,
    SwingHighDetector,
    SwingLowDetector,
    SwingStructureDetector,
)
from liquidity_hunter.psychology import RetailBiasEstimate, RetailTrapAnalyzer
from liquidity_hunter.scoring import LiquidityScoringEngine, ScoredLiquidityZone

DEFAULT_SWING_LOOKBACK = 15
DEFAULT_INTERNAL_SWING_LOOKBACK = 2

# Binance's `/api/v3/klines` endpoint accepts `limit` values up to 1000.
_MAX_FETCH_LIMIT = 1000

# Extra candles fetched before the visible window so
# InternalStructureDetector's trend/validated_choch_<side> bootstrap (which
# depends on the *first* pivots in whatever series it's given) has stabilized
# before reaching the candles actually shown on the dashboard. Without this,
# a fixed-size sliding window re-fetched on every refresh shifts that
# bootstrap by one candle each time, causing the same pivot to flip between
# BREAK_OF_STRUCTURE/CHANGE_OF_CHARACTER/LIQUIDITY_SWEEP across refreshes.
_INTERNAL_STRUCTURE_BOOTSTRAP_BUFFER = 300


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
    confluence_filter: bool = True,
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

    market_structure_events = SwingStructureDetector(
        swing_lookback=swing_lookback, confluence_filter=confluence_filter
    ).detect(candles)

    buffered_limit = min(limit + _INTERNAL_STRUCTURE_BOOTSTRAP_BUFFER, _MAX_FETCH_LIMIT)
    internal_candles = provider.get_ohlcv(symbol, timeframe, buffered_limit)
    visible_start = candles[0].timestamp
    visible_end = candles[-1].timestamp
    internal_structure_events = [
        event
        for event in InternalStructureDetector(
            swing_lookback=internal_swing_lookback,
            confluence_filter=confluence_filter,
        ).detect(internal_candles)
        if visible_start <= event.timestamp <= visible_end
    ]
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
