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
    SwingHighDetector,
    SwingLowDetector,
)
from liquidity_hunter.psychology import RetailBiasEstimate, RetailTrapAnalyzer
from liquidity_hunter.scoring import LiquidityScoringEngine, ScoredLiquidityZone

DEFAULT_TREND_LOOKBACK = 20


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
    retail_bias: RetailBiasEstimate


def _infer_trend_direction(candles: list[Candle], lookback: int) -> MarketDirection:
    """Infer a higher timeframe trend direction from recent average closes.

    This is a simple descriptive placeholder for `MarketStructure`
    detection (not yet implemented, see CLAUDE.md "Project status"): it
    compares the average close of the most recent `lookback` candles to
    the average close of the `lookback` candles before that.
    """
    if lookback < 1:
        raise ValueError("lookback must be >= 1")
    if len(candles) < lookback * 2:
        return MarketDirection.NEUTRAL

    closes = [candle.close for candle in candles]
    recent_avg = sum(closes[-lookback:]) / lookback
    previous_avg = sum(closes[-2 * lookback : -lookback]) / lookback

    if recent_avg > previous_avg:
        return MarketDirection.BULLISH
    if recent_avg < previous_avg:
        return MarketDirection.BEARISH
    return MarketDirection.NEUTRAL


def load_dashboard_data(
    provider: OHLCVProvider | None = None,
    symbol: str = "BTCUSDT",
    timeframe: TimeFrame = TimeFrame.H1,
    limit: int = 500,
    trend_lookback: int = DEFAULT_TREND_LOOKBACK,
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
    higher_timeframe_direction = _infer_trend_direction(candles, trend_lookback)

    # MarketStructure detection is not yet implemented (see CLAUDE.md
    # "Project status"). RetailTrapAnalyzer falls back to
    # `higher_timeframe_direction` when no structure events are supplied.
    market_structure_events: list[MarketStructure] = []

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
        retail_bias=retail_bias,
    )
