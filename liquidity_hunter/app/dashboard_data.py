"""Composition root for the research dashboard.

Wires together `data`, `liquidity`, `scoring`, and `psychology` into a
single `DashboardData` snapshot for `dashboard` to render.
"""

from dataclasses import dataclass, replace

from liquidity_hunter.core.domain import (
    Candle,
    LiquidityZone,
    ManipulationCycle,
    MarketDirection,
    MarketNarrative,
    MarketStructure,
    TimeFrame,
)
from liquidity_hunter.core.domain.behavior_divergence import BehaviorDivergence
from liquidity_hunter.core.domain.poi_zone import POIZone, RTOSweepEvent
from liquidity_hunter.data import BinanceDataProvider, OHLCVProvider
from liquidity_hunter.indicators import volume_delta_series
from liquidity_hunter.liquidity import (
    EqualHighDetector,
    EqualLowDetector,
    InternalStructureDetector,
    POIDetector,
    SwingHighDetector,
    SwingLowDetector,
    SwingStructureDetector,
    mark_swept_zones,
)
from liquidity_hunter.psychology import (
    BehaviorDivergenceAnalyzer,
    ManipulationCycleDetector,
    RetailBiasEstimate,
    RetailTrapAnalyzer,
)
from liquidity_hunter.scoring import LiquidityScoringEngine, ScoredLiquidityZone

DEFAULT_SWING_LOOKBACK = 10
DEFAULT_INTERNAL_SWING_LOOKBACK = 2

# Binance's `/api/v3/klines` endpoint accepts `limit` values up to 1000.
_MAX_FETCH_LIMIT = 1000

_HIGHER_TIMEFRAME_MAP: dict[TimeFrame, TimeFrame] = {
    TimeFrame.M1: TimeFrame.H1,
    TimeFrame.M5: TimeFrame.H1,
    TimeFrame.M15: TimeFrame.H1,
    TimeFrame.M30: TimeFrame.H1,
    TimeFrame.H1: TimeFrame.H4,
    TimeFrame.H4: TimeFrame.D1,
    TimeFrame.D1: TimeFrame.W1,
}

_HIGHER_TIMEFRAME_CANDLE_LIMIT = 100

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
    poi_zones: list[POIZone]
    poi_sweep_events: list[RTOSweepEvent]
    manipulation_cycles: list[ManipulationCycle]
    behavior_divergences: list[BehaviorDivergence]
    narrative: MarketNarrative | None = None


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
    limit: int = 700,
    swing_lookback: int = DEFAULT_SWING_LOOKBACK,
    internal_swing_lookback: int = DEFAULT_INTERNAL_SWING_LOOKBACK,
    confluence_filter: bool = False,
) -> DashboardData:
    """Fetch candles and assemble liquidity, ranking, and retail bias data."""
    provider = provider if provider is not None else BinanceDataProvider()
    candles = provider.get_ohlcv(symbol, timeframe, limit)

    liquidity_zones = mark_swept_zones(
        [
            *SwingHighDetector().detect(candles),
            *SwingLowDetector().detect(candles),
            *EqualHighDetector().detect(candles),
            *EqualLowDetector().detect(candles),
        ],
        candles,
    )

    current_price = candles[-1].close
    active_zones = [z for z in liquidity_zones if not z.is_mitigated]
    ranked_zones = LiquidityScoringEngine().score(active_zones, current_price)

    market_structure_events = SwingStructureDetector(
        swing_lookback=swing_lookback, confluence_filter=confluence_filter
    ).detect(candles)

    buffered_limit = min(limit + _INTERNAL_STRUCTURE_BOOTSTRAP_BUFFER, _MAX_FETCH_LIMIT)
    internal_candles = provider.get_ohlcv(symbol, timeframe, buffered_limit)
    visible_start = candles[0].timestamp
    visible_end = candles[-1].timestamp

    # Run InternalStructureDetector once on the full buffered series; reuse
    # the result for both the display window filter and POI detection (which
    # needs the unfiltered set so CHoCH anchors from the buffer can produce
    # zones visible in the display window).
    all_internal_events = InternalStructureDetector(
        swing_lookback=internal_swing_lookback,
        confluence_filter=confluence_filter,
    ).detect(internal_candles)
    internal_structure_events = [
        e for e in all_internal_events if visible_start <= e.timestamp <= visible_end
    ]

    poi_result = POIDetector().detect(internal_candles, all_internal_events)
    poi_zones = [z for z in poi_result.zones if visible_start <= z.created_at <= visible_end]
    poi_sweep_events = [
        e for e in poi_result.sweep_events if visible_start <= e.timestamp <= visible_end
    ]

    htf = _HIGHER_TIMEFRAME_MAP.get(timeframe)
    if htf is not None:
        htf_candles = provider.get_ohlcv(symbol, htf, _HIGHER_TIMEFRAME_CANDLE_LIMIT)
        htf_events = SwingStructureDetector(
            swing_lookback=swing_lookback, confluence_filter=confluence_filter
        ).detect(htf_candles)
        higher_timeframe_direction = _latest_structure_direction(htf_events)
    else:
        higher_timeframe_direction = _latest_structure_direction(market_structure_events)

    retail_bias = RetailTrapAnalyzer().analyze(
        symbol=symbol,
        higher_timeframe_direction=higher_timeframe_direction,
        market_structure_events=market_structure_events,
        liquidity_zones=liquidity_zones,
        current_price=current_price,
    )

    all_structure = market_structure_events + internal_structure_events
    vd = volume_delta_series(candles)
    manipulation_cycles = ManipulationCycleDetector().detect(
        candles=candles,
        structure_events=all_structure,
        liquidity_zones=liquidity_zones,
        poi_sweep_events=poi_sweep_events,
        volume_deltas=vd,
    )

    behavior_divergences = BehaviorDivergenceAnalyzer().analyze(
        candles=candles,
        volume_deltas=vd,
        liquidity_zones=liquidity_zones,
        structure_events=all_structure,
    )

    data = DashboardData(
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
        poi_zones=poi_zones,
        poi_sweep_events=poi_sweep_events,
        manipulation_cycles=manipulation_cycles,
        behavior_divergences=behavior_divergences,
    )

    from liquidity_hunter.app.narrative import NarrativeEngine

    narrative = NarrativeEngine().build(data)
    return replace(data, narrative=narrative)
