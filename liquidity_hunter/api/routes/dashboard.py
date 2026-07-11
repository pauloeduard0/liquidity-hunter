"""Dashboard data endpoint."""

from typing import Annotated

from fastapi import APIRouter, Query

from liquidity_hunter.api.cache import TTLCache
from liquidity_hunter.api.schemas import DashboardDataResponse
from liquidity_hunter.app.dashboard_data import (
    DEFAULT_SWING_LOOKBACK,
    DashboardData,
    load_dashboard_data,
)
from liquidity_hunter.core.domain import TimeFrame

router = APIRouter(tags=["dashboard"])

# Shorter than `cache.DEFAULT_TTL_SECONDS`: the frontend polls this endpoint
# to keep the chart/price near-live, so a long TTL would make it feel frozen.
_cache: TTLCache[DashboardData] = TTLCache(ttl_seconds=10.0)


@router.get("/api/dashboard", response_model=DashboardDataResponse)
def get_dashboard(
    symbol: str = "BTCUSDT",
    timeframe: TimeFrame = TimeFrame.H1,
    # Cap at 1200: with the 300-candle internal-structure buffer, 1200 + 300
    # hits the futures klines per-request max of 1500, while keeping the buffer
    # fully fed (it covers `_STRUCTURAL_ANCHOR_REGION`).
    limit: Annotated[int, Query(gt=0, le=1200)] = 1200,
    swing_lookback: Annotated[int, Query(gt=0)] = DEFAULT_SWING_LOOKBACK,
    # Narrative/anomaly synthesis is off by default while the multi-timeframe
    # overview takes over the sidebar slot; pass `narrative=true` to re-enable
    # (the frontend NarrativePanel renders whenever the field is non-null).
    narrative: bool = False,
) -> DashboardDataResponse:
    """Return a `DashboardData` snapshot for `symbol`/`timeframe` as JSON.

    Results are cached in-memory per parameter combination for
    `cache.DEFAULT_TTL_SECONDS` seconds to avoid redundant Binance requests.
    """
    cache_key = (symbol, timeframe, limit, swing_lookback, narrative)
    data = _cache.get_or_set(
        cache_key,
        lambda: load_dashboard_data(
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            swing_lookback=swing_lookback,
            compute_narrative=narrative,
        ),
    )
    return DashboardDataResponse.model_validate(data)
