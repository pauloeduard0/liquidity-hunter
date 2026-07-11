"""Multi-timeframe structural overview endpoint."""

from functools import partial

from fastapi import APIRouter

from liquidity_hunter.api.cache import TTLCache
from liquidity_hunter.app.overview import (
    OVERVIEW_TIMEFRAMES,
    TimeframeStructureSnapshot,
    build_overview,
    load_timeframe_structure,
)
from liquidity_hunter.core.domain import MarketOverview, TimeFrame

router = APIRouter(tags=["overview"])

# Each timeframe's snapshot is cached independently, with a TTL proportional
# to how fast its reading can actually change (a structure event lands at most
# once per candle; only live-edge provisional marks move faster). This bounds
# the Binance load: a cold overview costs one buffered-klines fetch per
# timeframe, but the coarse ones then stay cached for minutes.
_SNAPSHOT_TTL_SECONDS: dict[TimeFrame, float] = {
    TimeFrame.M1: 15.0,
    TimeFrame.M5: 30.0,
    TimeFrame.M15: 60.0,
    TimeFrame.M30: 90.0,
    TimeFrame.H1: 120.0,
    TimeFrame.H4: 300.0,
    TimeFrame.D1: 600.0,
    TimeFrame.W1: 1200.0,
}
_DEFAULT_SNAPSHOT_TTL_SECONDS = 60.0

_snapshot_cache: TTLCache[TimeframeStructureSnapshot] = TTLCache()


@router.get("/api/overview", response_model=MarketOverview)
def get_overview(symbol: str = "BTCUSDT") -> MarketOverview:
    """Return the per-timeframe structural ladder (M5 → W1) for `symbol`.

    Snapshots are cached per (symbol, timeframe) with timeframe-proportional
    TTLs; the cross-timeframe assembly (each entry's hunt read against its
    higher-timeframe anchor from the same batch) is recomputed per request.
    """
    snapshots = [
        _snapshot_cache.get_or_set(
            (symbol, timeframe),
            partial(load_timeframe_structure, symbol=symbol, timeframe=timeframe),
            ttl_seconds=_SNAPSHOT_TTL_SECONDS.get(timeframe, _DEFAULT_SNAPSHOT_TTL_SECONDS),
        )
        for timeframe in OVERVIEW_TIMEFRAMES
    ]
    return build_overview(symbol, snapshots)
