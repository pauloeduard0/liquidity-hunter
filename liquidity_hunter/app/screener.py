"""Universe-wide block-reclaim screener.

The block-reclaim setup fires ~0.5 times a month per symbol on a gated
intraday timeframe (measured 2026-08-23 across the 71-symbol universe: M15 39,
M30 36, H1 38 fired/month universe-wide inside `r_atr <= 1.0`; H4 ~5). A
watchlist of a few symbols therefore reads "almost never" -- the scarcity is
coverage, not the filter, and the fix is to watch the whole universe at once.

Composition-level, like `app.overview`, and split the same way so the API can
cache each (symbol, timeframe) unit independently:

- :func:`scan_symbol_timeframe` -- the cacheable I/O + detection unit: one
  klines fetch, `POIDetector`, the production VWAP anchor for that timeframe,
  the EMA(9), and `detect_block_reclaims` -- exactly the pipeline
  `load_dashboard_data` wires, so a screener row and the chart agree.
- :func:`build_screen` -- pure assembly of per-unit rows into a
  :class:`BlockReclaimScreen`.
- :func:`load_screen` -- both, fanned out over a thread pool (the providers
  are stateless public GETs, the same property the dashboard prefetch pool
  relies on). A symbol that fails is reported in `symbols_failed`, never
  allowed to kill the scan -- one bad row killing a whole timeframe is the
  EGLD failure mode this project has already met.

Descriptive throughout: a row states that the setup is armed or has fired and
carries `r_atr`; every threshold stays the reader's choice.
"""

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from time import monotonic

from liquidity_hunter.app.block_reclaim import (
    MAX_WAIT_CANDLES,
    _visits,
    detect_block_reclaims,
)
from liquidity_hunter.app.dashboard_data import (
    _BLOCK_RECLAIM_EMA_PERIOD,
    _VWAP_ANCHOR_PERIOD,
    _VWAP_DEFAULT_ANCHOR_PERIOD,
    default_ohlcv_provider,
)
from liquidity_hunter.core.domain import (
    BlockReclaim,
    BlockReclaimScanEntry,
    BlockReclaimScreen,
    Candle,
    MarketDirection,
    POIZone,
    POIZoneKind,
    ScreenerStatus,
    TimeFrame,
    VWAPSeries,
)
from liquidity_hunter.data import OHLCVProvider
from liquidity_hunter.data.exceptions import (
    DataProviderBannedError,
    DataProviderError,
)
from liquidity_hunter.indicators import ema_series, vwap
from liquidity_hunter.liquidity import POIDetector

#: The measured universe -- mirrors `research/_symbols.py::UNIVERSE`, which is
#: the population every number in `docs/block_reclaim.md` was measured on.
#: Duplicated rather than imported because `research/` is not a package the
#: application depends on; a symbol added here without a measurement behind it
#: is scanned all the same (the rows are descriptive), it just isn't covered
#: by the study's numbers yet.
SCREEN_SYMBOLS: tuple[str, ...] = (
    "AAVEUSDT", "ADAUSDT", "ALGOUSDT", "ANKRUSDT", "APTUSDT", "ARBUSDT",
    "ATOMUSDT", "AVAXUSDT", "AXSUSDT", "BANDUSDT", "BATUSDT", "BNBUSDT",
    "BTCUSDT", "CELRUSDT", "CHZUSDT", "COMPUSDT", "CRVUSDT", "DASHUSDT",
    "DOGEUSDT", "DOTUSDT", "EGLDUSDT", "ENJUSDT", "EOSUSDT", "ETCUSDT",
    "ETHUSDT", "FILUSDT", "GALAUSDT", "GRTUSDT", "HBARUSDT", "ICPUSDT",
    "IMXUSDT", "INJUSDT", "IOSTUSDT", "IOTAUSDT", "KAVAUSDT", "KNCUSDT",
    "LDOUSDT", "LINKUSDT", "LRCUSDT", "LTCUSDT", "MANAUSDT", "MKRUSDT",
    "NEARUSDT", "NEOUSDT", "ONEUSDT", "ONTUSDT", "OPUSDT", "QTUMUSDT",
    "RUNEUSDT", "RVNUSDT", "SANDUSDT", "SEIUSDT", "SNXUSDT", "SOLUSDT",
    "STORJUSDT", "STXUSDT", "SUIUSDT", "SUSHIUSDT", "THETAUSDT", "TIAUSDT",
    "TRXUSDT", "UNIUSDT", "VETUSDT", "WAVESUSDT", "WLDUSDT", "XLMUSDT",
    "XRPUSDT", "XTZUSDT", "YFIUSDT", "ZECUSDT", "ZILUSDT", "ZRXUSDT",
)

#: The timeframes the setup has a positive measured net on (M30/H1 are
#: thin-positive inside the gate; M5 is measured dead and deliberately absent).
SCREEN_TIMEFRAMES: tuple[TimeFrame, ...] = (
    TimeFrame.M15,
    TimeFrame.M30,
    TimeFrame.H1,
    TimeFrame.H4,
)

#: Candles fetched per unit. Binance prices a klines request by size: up to
#: 500 candles costs weight 2, above that weight 5. A universe pass is ~284
#: requests, so 600 candles spends 1420 of the 2400/min IP budget in one burst
#: and 300 spends 568 -- and 300 still covers the POI queue and several VWAP
#: accumulations on every screened timeframe. Lowered after a live scan got
#: this IP banned (2026-08-23).
SCAN_CANDLES = 300

#: How far back a fired reclaim still makes the list, in candles. The setup
#: resolves its 2R-or-stop within ~40 candles; past that the row is history.
DEFAULT_FIRED_WITHIN = 12

#: Concurrency of a universe pass. ccxt's own rate limiter is per-instance and
#: not thread-aware, so the pool size *is* the burst size against the venue.
_SCAN_POOL_WORKERS = 3

#: Per-(symbol, timeframe) TTLs for the library-level cache. A reclaim can
#: only appear once per candle, so a cron pass every few minutes should reuse
#: almost everything: without this each pass refetched the whole universe.
_UNIT_TTL_SECONDS: dict[TimeFrame, float] = {
    TimeFrame.M15: 60.0,
    TimeFrame.M30: 90.0,
    TimeFrame.H1: 120.0,
    TimeFrame.H4: 300.0,
}
_DEFAULT_UNIT_TTL_SECONDS = 120.0

#: Module-scoped so repeated `load_screen` calls in one process (the journal's
#: record pass, then a report) share it. A plain dict under a lock rather than
#: `api.cache.TTLCache`: dependencies flow inward, and `app` may not import
#: `api`. The lock matters here in a way it does not there -- this cache is
#: read by every worker of the scan pool.
_unit_cache: dict[tuple[str, TimeFrame], tuple[float, "ScanUnit"]] = {}
_unit_cache_lock = Lock()


def _cached_unit(
    key: tuple[str, TimeFrame], ttl_seconds: float, factory: "Callable[[], ScanUnit]"
) -> "ScanUnit":
    """Return the cached unit for `key`, computing it outside the lock.

    Two workers racing the same key both fetch, which costs one extra request
    and never a corrupt entry; holding the lock across a network call would
    serialize the whole pass instead.
    """
    now = monotonic()
    with _unit_cache_lock:
        hit = _unit_cache.get(key)
        if hit is not None and now < hit[0]:
            return hit[1]
    unit = factory()
    with _unit_cache_lock:
        _unit_cache[key] = (monotonic() + ttl_seconds, unit)
    return unit


def clear_unit_cache() -> None:
    """Drop every cached unit (tests, and a forced refresh)."""
    with _unit_cache_lock:
        _unit_cache.clear()


@dataclass(frozen=True)
class ScanUnit:
    """One (symbol, timeframe)'s raw scan output -- the cacheable unit."""

    symbol: str
    timeframe: TimeFrame
    candles: list[Candle]
    reclaims: list[BlockReclaim]
    armed: list[BlockReclaimScanEntry]


def scan_symbol_timeframe(
    symbol: str,
    timeframe: TimeFrame,
    *,
    provider: OHLCVProvider | None = None,
    limit: int = SCAN_CANDLES,
) -> ScanUnit:
    """Fetch one symbol/timeframe and run the production reclaim pipeline."""
    provider = provider or default_ohlcv_provider()
    candles = provider.get_ohlcv(
        symbol, timeframe, min(limit, provider.max_fetch_limit)
    )
    poi_zones = POIDetector().detect(candles)
    anchor = _VWAP_ANCHOR_PERIOD.get(timeframe, _VWAP_DEFAULT_ANCHOR_PERIOD)
    session_vwap = vwap(
        candles, symbol=symbol, timeframe=timeframe, anchor=anchor
    )
    reclaims = detect_block_reclaims(
        candles,
        poi_zones,
        session_vwap,
        symbol=symbol,
        timeframe=timeframe,
        ema=ema_series(candles, _BLOCK_RECLAIM_EMA_PERIOD),
    )
    armed = armed_entries(
        candles, poi_zones, session_vwap, reclaims,
        symbol=symbol, timeframe=timeframe,
    )
    return ScanUnit(
        symbol=symbol,
        timeframe=timeframe,
        candles=candles,
        reclaims=reclaims,
        armed=armed,
    )


def armed_entries(
    candles: list[Candle],
    poi_zones: Sequence[POIZone],
    session_vwap: VWAPSeries | None,
    reclaims: Sequence[BlockReclaim],
    *,
    symbol: str,
    timeframe: TimeFrame,
) -> list[BlockReclaimScanEntry]:
    """The visits whose wait window is still open and whose trigger hasn't printed.

    Mirrors the detector's own visit semantics (`_visits`): a test on the far
    side of the VWAP, merged across small gaps. A visit whose
    `MAX_WAIT_CANDLES` window has expired, or that already produced a reclaim
    in its direction, is not armed -- it is history, or it is the FIRED row.
    """
    if session_vwap is None or not candles:
        return []
    vwap_at = {p.timestamp: p.value for p in session_vwap.points}
    last = len(candles) - 1
    current_price = candles[-1].close
    armed: list[BlockReclaimScanEntry] = []
    for zone in poi_zones:
        if zone.kind is not POIZoneKind.ORDER_BLOCK:
            continue
        bullish = zone.direction is MarketDirection.BULLISH
        for start, end, _first in _visits(candles, zone, vwap_at, bullish=bullish):
            if end + MAX_WAIT_CANDLES < last:
                continue
            direction = (
                MarketDirection.BULLISH if bullish else MarketDirection.BEARISH
            )
            visit_ts = candles[start].timestamp
            if any(
                r.direction is direction and r.timestamp >= visit_ts
                for r in reclaims
            ):
                continue
            armed.append(
                BlockReclaimScanEntry(
                    symbol=symbol,
                    timeframe=timeframe,
                    status=ScreenerStatus.ARMED,
                    direction=direction,
                    timestamp=visit_ts,
                    candles_ago=last - start,
                    current_price=current_price,
                    block_price_low=zone.price_low,
                    block_price_high=zone.price_high,
                )
            )
    return armed


def build_screen(
    units: Sequence[ScanUnit],
    *,
    timeframes: Sequence[TimeFrame],
    symbols_scanned: int,
    symbols_failed: Sequence[str] = (),
    fired_within: int = DEFAULT_FIRED_WITHIN,
) -> BlockReclaimScreen:
    """Assemble scan units into one screen (pure; no I/O)."""
    entries: list[BlockReclaimScanEntry] = []
    for unit in units:
        last = len(unit.candles) - 1
        index_of = {c.timestamp: i for i, c in enumerate(unit.candles)}
        for reclaim in unit.reclaims:
            i = index_of.get(reclaim.timestamp)
            if i is None or last - i > fired_within:
                continue
            entries.append(
                BlockReclaimScanEntry(
                    symbol=unit.symbol,
                    timeframe=unit.timeframe,
                    status=ScreenerStatus.FIRED,
                    direction=reclaim.direction,
                    timestamp=reclaim.timestamp,
                    candles_ago=last - i,
                    current_price=unit.candles[-1].close,
                    block_price_low=reclaim.block_price_low,
                    block_price_high=reclaim.block_price_high,
                    r_atr=reclaim.r_atr,
                    reclaim=reclaim,
                )
            )
        entries.extend(unit.armed)
    # Fired first (freshest on top), then armed; tight readings before wide
    # ones within a candle so the gated population leads the list.
    entries.sort(
        key=lambda e: (
            e.status is not ScreenerStatus.FIRED,
            e.candles_ago,
            e.r_atr if e.r_atr is not None else float("inf"),
        )
    )
    return BlockReclaimScreen(
        generated_at=datetime.now(UTC),
        timeframes=list(timeframes),
        symbols_scanned=symbols_scanned,
        symbols_failed=list(symbols_failed),
        entries=entries,
    )


def load_screen(
    *,
    provider: OHLCVProvider | None = None,
    symbols: Sequence[str] = SCREEN_SYMBOLS,
    timeframes: Sequence[TimeFrame] = SCREEN_TIMEFRAMES,
    fired_within: int = DEFAULT_FIRED_WITHIN,
    limit: int = SCAN_CANDLES,
) -> BlockReclaimScreen:
    """Scan the whole universe, fanned out over a thread pool."""
    provider = provider or default_ohlcv_provider()
    jobs = [(s, tf) for s in symbols for tf in timeframes]
    units: list[ScanUnit] = []
    failed: set[str] = set()

    banned: list[DataProviderBannedError] = []

    def run(job: tuple[str, TimeFrame]) -> ScanUnit | None:
        symbol, timeframe = job
        if banned:
            return None  # the venue has cut us off; stop spending the budget
        try:
            return _cached_unit(
                (symbol, timeframe),
                _UNIT_TTL_SECONDS.get(timeframe, _DEFAULT_UNIT_TTL_SECONDS),
                lambda: scan_symbol_timeframe(
                    symbol, timeframe, provider=provider, limit=limit
                ),
            )
        except DataProviderBannedError as exc:
            # Fatal to the whole pass, by design: every further request during
            # a ban fails *and* extends it.
            banned.append(exc)
            return None
        except (DataProviderError, ValueError):
            # A symbol with a bad row or no contract degrades to a reported
            # failure; the rest of the universe still screens.
            failed.add(symbol)
            return None

    with ThreadPoolExecutor(max_workers=_SCAN_POOL_WORKERS) as pool:
        for unit in pool.map(run, jobs):
            if unit is not None:
                units.append(unit)
    if banned:
        raise banned[0]
    return build_screen(
        units,
        timeframes=timeframes,
        symbols_scanned=len(symbols),
        symbols_failed=sorted(failed),
        fired_within=fired_within,
    )
