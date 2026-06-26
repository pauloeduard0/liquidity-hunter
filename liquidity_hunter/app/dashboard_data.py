"""Composition root for the research dashboard.

Wires together `data`, `liquidity`, `scoring`, and `psychology` into a
single `DashboardData` snapshot for `dashboard` to render.
"""

import logging
from dataclasses import dataclass, replace
from datetime import datetime

from liquidity_hunter.core.domain import (
    Candle,
    LeverageLiquidationMap,
    LiquidityHeatmap,
    LiquidityZone,
    ManipulationCycle,
    MarketDirection,
    MarketNarrative,
    MarketStructure,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.core.domain.behavior_divergence import BehaviorDivergence
from liquidity_hunter.core.domain.poi_zone import POIZone, RTOSweepEvent
from liquidity_hunter.data import (
    BinanceDataProvider,
    BinanceFuturesDataProvider,
    FuturesDataProvider,
    OHLCVProvider,
)
from liquidity_hunter.data.exceptions import DataProviderError
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
    LeverageLiquidationEstimator,
    ManipulationCycleDetector,
    RetailBiasEstimate,
    RetailTrapAnalyzer,
)
from liquidity_hunter.scoring import (
    LiquidityHeatmapEngine,
    LiquidityScoringEngine,
    ScoredLiquidityZone,
)

logger = logging.getLogger(__name__)

DEFAULT_SWING_LOOKBACK = 10

_INTERNAL_STRUCTURE_PARAMS: dict[TimeFrame, tuple[int, int]] = {
    TimeFrame.M5: (2, 5),
    TimeFrame.M15: (3, 8),
    TimeFrame.M30: (5, 12),
    TimeFrame.H1: (5, 12),
    TimeFrame.H4: (5, 8),
    TimeFrame.D1: (5, 8),
    TimeFrame.W1: (5, 12),
}
_DEFAULT_INTERNAL_PARAMS = (5, 12)

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

# Extra candles fetched before the visible window so the internal-structure
# detector has history to bootstrap from before reaching the candles actually
# shown on the dashboard. This bounds the region the structural anchor (below)
# scans; it is *not* itself the detection start point.
_INTERNAL_STRUCTURE_BOOTSTRAP_BUFFER = 300

# The internal detector starts detection at the most recent *major extreme*
# (lowest low / highest high) within this many candles before the visible
# window, rather than at a fixed candle offset. A fixed offset lands the
# NEUTRAL->first-break bootstrap on whatever pivot happens to sit there, which
# can inherit a stale, far-back regime (e.g. a months-old downtrend carried into
# a window that has since clearly reversed), making the first CHoCH late and
# wrong-direction. Anchoring at the move's structural origin instead seeds the
# trend from the price action actually entering the window, while staying stable
# across refreshes (a major extreme is a fixed price point, not a sliding
# offset). See `_structural_anchor_index`.
_STRUCTURAL_ANCHOR_REGION = 300

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
    liquidity_heatmap: LiquidityHeatmap | None = None
    liquidation_map: LeverageLiquidationMap | None = None
    narrative: MarketNarrative | None = None


def _structural_anchor_index(candles: list[Candle], visible_start: datetime) -> int:
    """Index in ``candles`` where internal-structure detection should start.

    Returns the most recent *major extreme* -- the candle with the lowest low or
    the highest high, whichever is more recent -- within the
    ``_STRUCTURAL_ANCHOR_REGION`` candles preceding the visible window (the
    candles before ``visible_start``). Anchoring detection at this deterministic
    structural point seeds the detector's trend from the move actually heading
    into the visible window, while staying stable across refreshes (the extreme
    is a fixed price point, not an offset that slides with the window's right
    edge). Falls back to ``0`` when there is no pre-visible buffer (e.g. the
    provider returned only the visible window).
    """
    visible_start_index = next(
        (i for i, candle in enumerate(candles) if candle.timestamp >= visible_start),
        0,
    )
    region = candles[max(0, visible_start_index - _STRUCTURAL_ANCHOR_REGION) : visible_start_index]
    if not region:
        return 0
    lowest = min(region, key=lambda candle: candle.low)
    highest = max(region, key=lambda candle: candle.high)
    anchor = lowest if lowest.timestamp > highest.timestamp else highest
    return next(i for i, candle in enumerate(candles) if candle.timestamp == anchor.timestamp)


def _reanchor_bos_close_break(
    events: list[MarketStructure], candles: list[Candle]
) -> list[MarketStructure]:
    """Re-anchor each continuation BOS to the first *close* beyond the level it broke.

    A BOS's ``reference_price_level`` is the prior swing extreme it broke (the
    staircase floor). The detector advances state on a close beyond the
    *trailing* reference, which sits above (bearish) / below (bullish) that
    floor, so a BOS can be stamped while price has only *wicked* past the formed
    level. This conservative pass re-times each BOS to the first candle that
    actually *closes* beyond the formed level, searching the window the BOS
    stays active (up to the next same-direction BOS or opposite-direction
    CHoCH, matching the chart's line termination), and *drops* any BOS whose leg
    never closed beyond it -- a wick-only break is not a confirmed continuation.
    The trailing references and CHoCH promotion inside the detector are
    untouched; only the emitted BOS events are re-timed here.
    """
    if not events or not candles:
        return events

    index_by_ts = {candle.timestamp: i for i, candle in enumerate(candles)}
    ordered = sorted(events, key=lambda event: event.timestamp)
    last_index = len(candles) - 1
    result: list[MarketStructure] = []

    for event in ordered:
        if event.event is not StructureEvent.BREAK_OF_STRUCTURE:
            result.append(event)
            continue
        start_index = index_by_ts.get(event.timestamp)
        if start_index is None:
            result.append(event)
            continue

        # The BOS stays active until the next same-direction BOS or the next
        # opposite-direction CHoCH; the formed level must close within that span.
        end_index = last_index
        for other in ordered:
            if other.timestamp <= event.timestamp:
                continue
            terminates = (
                other.event is StructureEvent.BREAK_OF_STRUCTURE
                and other.direction is event.direction
            ) or (
                other.event is StructureEvent.CHANGE_OF_CHARACTER
                and other.direction is not event.direction
            )
            if terminates:
                other_index = index_by_ts.get(other.timestamp)
                if other_index is not None:
                    end_index = other_index
                break

        floor = event.reference_price_level
        if floor is None:
            result.append(event)
            continue
        bearish = event.direction is MarketDirection.BEARISH
        new_timestamp = None
        for i in range(start_index, end_index + 1):
            close = candles[i].close
            if (bearish and close < floor) or (not bearish and close > floor):
                new_timestamp = candles[i].timestamp
                break

        if new_timestamp is None:
            continue  # leg only wicked the formed level: not a confirmed BOS

        # Anchor the line's *start* at the formed level's origin -- the candle
        # that made the prior swing extreme (low for bearish, high for bullish)
        # at this price -- so it runs from where the level formed to where it
        # broke, rather than starting at the break. Falls back to the break when
        # no exact match is found.
        reference_timestamp = event.reference_timestamp
        for i in range(start_index, -1, -1):
            extreme = candles[i].low if bearish else candles[i].high
            if extreme == floor:
                reference_timestamp = candles[i].timestamp
                break

        updates: dict[str, datetime] = {}
        if new_timestamp != event.timestamp:
            updates["timestamp"] = new_timestamp
        if reference_timestamp != event.reference_timestamp and reference_timestamp is not None:
            updates["reference_timestamp"] = reference_timestamp
        result.append(event.model_copy(update=updates) if updates else event)

    result.sort(key=lambda event: event.timestamp)
    return result


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
    confluence_filter: bool = False,
    futures_provider: FuturesDataProvider | None = None,
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

    buffered_limit = min(limit + _INTERNAL_STRUCTURE_BOOTSTRAP_BUFFER, _MAX_FETCH_LIMIT)
    buffered_candles = provider.get_ohlcv(symbol, timeframe, buffered_limit)
    visible_start = candles[0].timestamp
    visible_end = candles[-1].timestamp

    # The major (swing) detector runs on the full buffered series.
    all_major_events = SwingStructureDetector(
        swing_lookback=swing_lookback, confluence_filter=confluence_filter
    ).detect(buffered_candles)
    market_structure_events = [
        e for e in all_major_events if visible_start <= e.timestamp <= visible_end
    ]

    # The internal detector starts at a structural anchor (the most recent major
    # extreme before the visible window) rather than a fixed candle offset, so
    # the trend it bootstraps reflects the move actually entering the window
    # instead of a stale, far-back regime. See `_structural_anchor_index`.
    internal_candles = buffered_candles[_structural_anchor_index(buffered_candles, visible_start) :]

    internal_lookback, internal_persistence = _INTERNAL_STRUCTURE_PARAMS.get(
        timeframe, _DEFAULT_INTERNAL_PARAMS
    )
    all_internal_events = InternalStructureDetector(
        swing_lookback=internal_lookback,
        persistence_candles=internal_persistence,
        confluence_filter=confluence_filter,
    ).detect(internal_candles)
    # Re-time each BOS to the first close beyond the formed level it broke
    # (dropping wick-only continuations), before the visible filter and POI.
    all_internal_events = _reanchor_bos_close_break(all_internal_events, internal_candles)
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

    liquidity_heatmap = LiquidityHeatmapEngine().build(
        symbol=symbol,
        timeframe=timeframe,
        candles=candles,
        current_price=current_price,
        liquidity_zones=liquidity_zones,
        poi_zones=poi_zones,
        manipulation_cycles=manipulation_cycles,
        retail_bias=retail_bias,
    )

    liquidation_map = _build_liquidation_map(
        futures_provider if futures_provider is not None else BinanceFuturesDataProvider(),
        symbol=symbol,
        timeframe=timeframe,
        current_price=current_price,
        candles=candles,
        liquidity_zones=liquidity_zones,
        poi_zones=poi_zones,
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
        liquidity_heatmap=liquidity_heatmap,
        liquidation_map=liquidation_map,
    )

    from liquidity_hunter.app.narrative import NarrativeEngine

    narrative = NarrativeEngine().build(data)
    return replace(data, narrative=narrative)


def _build_liquidation_map(
    futures_provider: FuturesDataProvider,
    symbol: str,
    timeframe: TimeFrame,
    current_price: float,
    candles: list[Candle],
    liquidity_zones: list[LiquidityZone],
    poi_zones: list[POIZone],
) -> LeverageLiquidationMap | None:
    """Fetch futures market state and estimate the leverage-liquidation map.

    Degrades to ``None`` if futures data is unavailable (e.g. the symbol has no
    perpetual contract, or the venue is unreachable), so the dashboard still
    renders for spot-only symbols.
    """
    try:
        open_interest = futures_provider.get_open_interest_history(symbol, timeframe)
        funding = futures_provider.get_funding_rate_history(symbol)
        long_short = futures_provider.get_long_short_ratio(symbol, timeframe)
    except DataProviderError:
        logger.warning("Futures data unavailable for %s; skipping liquidation map", symbol)
        return None

    return LeverageLiquidationEstimator().estimate(
        symbol=symbol,
        timeframe=timeframe,
        current_price=current_price,
        candles=candles,
        liquidity_zones=liquidity_zones,
        open_interest=open_interest,
        funding=funding,
        long_short=long_short,
        poi_zones=poi_zones,
    )
