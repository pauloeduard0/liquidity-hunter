"""Universe-wide block-reclaim screener endpoint."""

from concurrent.futures import ThreadPoolExecutor
from functools import partial

from fastapi import APIRouter

from liquidity_hunter.api.cache import TTLCache
from liquidity_hunter.app.screener import (
    DEFAULT_FIRED_WITHIN,
    SCREEN_SYMBOLS,
    SCREEN_TIMEFRAMES,
    ScanUnit,
    build_screen,
    scan_symbol_timeframe,
)
from liquidity_hunter.core.domain import BlockReclaimScreen, TimeFrame
from liquidity_hunter.data.exceptions import (
    DataProviderBannedError,
    DataProviderError,
)

router = APIRouter(tags=["screener"])

# One (symbol, timeframe) scan is the cacheable unit, with the overview's
# timeframe-proportional TTLs: a reclaim can only appear once per candle.
_UNIT_TTL_SECONDS: dict[TimeFrame, float] = {
    TimeFrame.M15: 60.0,
    TimeFrame.M30: 90.0,
    TimeFrame.H1: 120.0,
    TimeFrame.H4: 300.0,
}
_DEFAULT_UNIT_TTL_SECONDS = 120.0

_unit_cache: TTLCache[ScanUnit] = TTLCache()


@router.get("/api/screener", response_model=BlockReclaimScreen)
def get_screener(
    timeframes: str = ",".join(tf.value for tf in SCREEN_TIMEFRAMES),
    fired_within: int = DEFAULT_FIRED_WITHIN,
) -> BlockReclaimScreen:
    """Scan the measured universe for armed and recently fired block reclaims.

    `timeframes` is a comma-separated subset of the screened ladder (default
    all four). Units are cached per (symbol, timeframe); a cold full scan is
    a few minutes of klines fetches, warm requests reuse every unexpired unit.
    """
    frames = [TimeFrame(t.strip()) for t in timeframes.split(",") if t.strip()]
    units: list[ScanUnit] = []
    failed: set[str] = set()
    banned: list[str] = []

    # Fanned out over threads: a cold scan is hundreds of klines fetches, and
    # the providers are stateless public GETs (the dashboard prefetch-pool
    # property). Each job owns a distinct cache key, so the lock-free
    # `TTLCache` at worst recomputes a unit, never corrupts one.
    def fetch(job: tuple[str, TimeFrame]) -> ScanUnit | None:
        symbol, timeframe = job
        if banned:
            return None
        try:
            return _unit_cache.get_or_set(
                (symbol, timeframe),
                partial(scan_symbol_timeframe, symbol, timeframe),
                ttl_seconds=_UNIT_TTL_SECONDS.get(
                    timeframe, _DEFAULT_UNIT_TTL_SECONDS
                ),
            )
        except DataProviderBannedError:
            # The venue cut us off: stop the pass rather than spend the rest
            # of the budget on requests that fail and extend the ban.
            banned.append(symbol)
            return None
        except (DataProviderError, ValueError):
            failed.add(symbol)
            return None

    jobs = [(s, tf) for s in SCREEN_SYMBOLS for tf in frames]
    with ThreadPoolExecutor(max_workers=8) as pool:
        units = [u for u in pool.map(fetch, jobs) if u is not None]
    return build_screen(
        units,
        timeframes=frames,
        symbols_scanned=len(SCREEN_SYMBOLS),
        symbols_failed=sorted(failed),
        fired_within=fired_within,
    )
