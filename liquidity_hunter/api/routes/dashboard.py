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

_cache: TTLCache[DashboardData] = TTLCache()


@router.get("/api/dashboard", response_model=DashboardDataResponse)
def get_dashboard(
    symbol: str = "BTCUSDT",
    timeframe: TimeFrame = TimeFrame.H1,
    limit: Annotated[int, Query(gt=0, le=1000)] = 500,
    swing_lookback: Annotated[int, Query(gt=0)] = DEFAULT_SWING_LOOKBACK,
) -> DashboardDataResponse:
    """Return a `DashboardData` snapshot for `symbol`/`timeframe` as JSON.

    Results are cached in-memory per parameter combination for
    `cache.DEFAULT_TTL_SECONDS` seconds to avoid redundant Binance requests.
    """
    cache_key = (symbol, timeframe, limit, swing_lookback)
    data = _cache.get_or_set(
        cache_key,
        lambda: load_dashboard_data(
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            swing_lookback=swing_lookback,
        ),
    )
    return DashboardDataResponse.model_validate(data)
