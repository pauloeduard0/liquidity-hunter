"""GeckoTerminal OHLCV data provider for on-chain (DEX) pairs.

Where the Binance providers serve centralized-exchange listings, this one
serves anything that trades in an on-chain liquidity pool -- memecoins in
particular, which never reach a CEX order book.

Two deliberate limitations, both consequences of the source rather than of
this adapter:

- **No taker split.** GeckoTerminal's OHLCV rows carry a single aggregate
  volume, so `Candle.taker_buy_volume` is filled with exactly half the
  candle's volume, making `volume_delta` identically zero. That switches the
  delta-derived layers (CVD, VSA, `MarketControlAnalyzer`, the profile's
  delta colouring) off rather than feeding them a guess: a "green candle =
  60% buying" proxy would be a fabricated observation, and everything
  downstream reads it as measured flow.
- **No futures state.** There is no open interest, funding, or long/short
  ratio for a DEX pair; `load_dashboard_data` already degrades to
  `oi_analysis=None` / `liquidation_map=None` when the futures fetch fails.

Everything derived from OHLC alone -- the structure detectors, POI zones,
equal levels, consolidation, Supertrend, VWAP, volume profile -- is unaffected.
"""

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from enum import Enum
from typing import Any, TypeVar

from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.data.exceptions import DataProviderConnectionError, DataProviderRequestError
from liquidity_hunter.data.providers.base import OHLCVProvider
from liquidity_hunter.data.retry import retry_with_backoff

logger = logging.getLogger(__name__)

_API_ROOT = "https://api.geckoterminal.com/api/v2"

#: The API answers urllib's default `Python-urllib/3.x` agent with a 403, so a
#: conventional one is sent instead. Not evasion -- the endpoints are public
#: and unauthenticated; the default agent is simply on a blocklist.
_USER_AGENT = "liquidity-hunter/1.0 (+research; contact via repository)"

#: The free tier's rate limit is tight and shared per IP, so requests are
#: spaced by this minimum interval process-wide: the dashboard fetches the
#: current and the higher timeframe concurrently, and the overview fetches
#: seven timeframes, so a per-instance counter would not hold the line.
_MIN_REQUEST_INTERVAL_SECONDS = 2.2

#: A 429 means the shared budget is spent, so *every* thread has to stand down
#: -- otherwise each one retries into the same exhausted limiter and the storm
#: feeds itself. The first 429 opens this global cooldown; the retry ladders
#: then wait it out together instead of racing.
_COOLDOWN_AFTER_429_SECONDS = 8.0

#: Responses are cached by URL for slightly less than the CDN's own
#: `s-maxage=60`, the window in which a repeat of the exact same URL is served
#: from the edge (`cf-cache-status: HIT`) without touching the rate limiter at
#: all. Within it, this cache serves the repeat locally: the dashboard poll,
#: the overview ladder, and the higher-timeframe run all ask for the same URLs,
#: and each de-duplicated request is one that cannot be rate-limited.
_RESPONSE_CACHE_TTL_SECONDS = 50.0

_THROTTLE_LOCK = threading.Lock()
_last_request_at = 0.0
_cooldown_until = 0.0

#: Which pool a token trades on, and how many tokens exist, are properties of
#: the asset rather than of a request, and `default_ohlcv_provider()` builds a
#: fresh provider per API call -- so these live at module scope. Two requests
#: per dashboard load that would otherwise repeat forever.
_METADATA_TTL_SECONDS = 3600.0

_CACHE_LOCK = threading.Lock()
_response_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_inflight_locks: dict[str, threading.Lock] = {}
_pool_cache: dict[str, tuple[float, str]] = {}
_supply_cache: dict[str, tuple[float, float]] = {}


def _throttle() -> None:
    """Space requests, and hold everyone back while a cooldown is open."""
    global _last_request_at
    while True:
        with _THROTTLE_LOCK:
            now = time.monotonic()
            wait = max(_last_request_at + _MIN_REQUEST_INTERVAL_SECONDS, _cooldown_until) - now
            if wait <= 0:
                _last_request_at = now
                return
        time.sleep(wait)


def _open_cooldown() -> None:
    global _cooldown_until
    with _THROTTLE_LOCK:
        _cooldown_until = max(_cooldown_until, time.monotonic() + _COOLDOWN_AFTER_429_SECONDS)


def _cached_response(url: str) -> dict[str, Any] | None:
    with _CACHE_LOCK:
        entry = _response_cache.get(url)
        if entry is None:
            return None
        expires_at, payload = entry
        if expires_at <= time.monotonic():
            del _response_cache[url]
            return None
        return payload


def _store_response(url: str, payload: dict[str, Any]) -> None:
    with _CACHE_LOCK:
        _response_cache[url] = (time.monotonic() + _RESPONSE_CACHE_TTL_SECONDS, payload)


_T = TypeVar("_T")


def _cached_metadata(cache: dict[str, tuple[float, _T]], key: str) -> _T | None:
    with _CACHE_LOCK:
        entry = cache.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= time.monotonic():
            del cache[key]
            return None
        return value


def _store_metadata(cache: dict[str, tuple[float, _T]], key: str, value: _T) -> None:
    with _CACHE_LOCK:
        cache[key] = (time.monotonic() + _METADATA_TTL_SECONDS, value)


def clear_caches() -> None:
    """Forget every cached response and resolution (for tests and long runs)."""
    with _CACHE_LOCK:
        _response_cache.clear()
        _pool_cache.clear()
        _supply_cache.clear()


def _inflight_lock(url: str) -> threading.Lock:
    """One lock per URL, so concurrent callers of the same URL fetch it once."""
    with _CACHE_LOCK:
        return _inflight_locks.setdefault(url, threading.Lock())


#: GeckoTerminal exposes fixed `(period, aggregate)` pairs rather than a free
#: interval string.
_OHLCV_PERIOD: dict[TimeFrame, tuple[str, int]] = {
    TimeFrame.M1: ("minute", 1),
    TimeFrame.M5: ("minute", 5),
    TimeFrame.M15: ("minute", 15),
    TimeFrame.H1: ("hour", 1),
    TimeFrame.H4: ("hour", 4),
    TimeFrame.D1: ("day", 1),
}

#: M30 and W1 have no native period, so they are resampled from the source
#: resolution below (`(source timeframe, candles per bar)`). Merging N finished
#: candles into one is exact -- open of the first, close of the last, extreme
#: highs/lows, summed volume -- and keeps the whole M5..W1 ladder available for
#: on-chain pairs; the cost is that one upstream request yields N times fewer
#: bars at these two resolutions.
_RESAMPLED_FROM: dict[TimeFrame, tuple[TimeFrame, int]] = {
    TimeFrame.M30: (TimeFrame.M15, 2),
    TimeFrame.W1: (TimeFrame.D1, 7),
}

#: Bar length in seconds, used to floor a timestamp onto its resampled bucket.
_TIMEFRAME_SECONDS: dict[TimeFrame, int] = {
    TimeFrame.M30: 1800,
    TimeFrame.W1: 7 * 86_400,
}

#: 1 Jan 1970 was a Thursday, so a bare 7-day floor of the epoch starts weeks
#: on Thursday. Shifting by the 4 days to the following Monday puts weekly
#: buckets on the Monday 00:00 UTC boundary every exchange's weekly candle uses.
_WEEK_EPOCH_OFFSET_SECONDS = 4 * 86_400


class PriceDenomination(str, Enum):
    """What the candle's price axis measures."""

    #: Base token priced in USD.
    USD = "usd"
    #: Base token priced in the pool's quote token (e.g. Jimothy/SOL).
    QUOTE = "quote"
    #: USD price multiplied by the token's supply -- the axis a memecoin is
    #: normally read on. A constant scale factor, so every structural reading
    #: (all of which are scale-invariant) is identical to the USD chart; only
    #: the numbers on the axis change.
    MARKET_CAP = "market_cap"


class GeckoTerminalDataProvider(OHLCVProvider):
    """Fetches OHLCV candles for an on-chain pool from GeckoTerminal's public API.

    Symbols are `"<network>:<address>"` (e.g.
    `"solana:Ge87EtsjwRQbHaqQmKRno69RFTwh9bfSsm99XNxTpump"`), or a bare
    address resolved against `default_network`. The address may be either a
    **token** -- in which case its deepest pool (by USD reserve) is used, the
    one a chart should follow when liquidity is fragmented across dozens of
    pools -- or a **pool** address directly.
    """

    # `/ohlcv` accepts `limit` up to 1000 and this adapter issues one request.
    max_fetch_limit = 1000

    def __init__(
        self,
        *,
        default_network: str = "solana",
        denomination: PriceDenomination = PriceDenomination.MARKET_CAP,
        # The free tier answers a burst -- and, intermittently, an isolated
        # well-spaced request -- with 429, so the ladder is longer than the
        # exchange providers' (2s, 4s, 8s, 16s, 32s, 64s): a ~25% independent
        # failure rate needs attempts, and the later rungs cover a real
        # rate-limit cooldown.
        max_retries: int = 7,
        retry_base_delay_seconds: float = 2.0,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._default_network = default_network
        self._denomination = denomination
        self._max_retries = max_retries
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self._timeout_seconds = timeout_seconds

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        """Fetch up to `limit` candles for an on-chain pair.

        Raises:
            DataProviderConnectionError: if GeckoTerminal cannot be reached
                after retries.
            DataProviderRequestError: if the symbol, network, or timeframe is
                not something GeckoTerminal can serve.
        """
        resample = _RESAMPLED_FROM.get(timeframe)
        if resample is not None:
            source_timeframe, ratio = resample
            source = self.get_ohlcv(
                symbol, source_timeframe, min(limit * ratio, self.max_fetch_limit)
            )
            return _resample(source, timeframe)

        period = _OHLCV_PERIOD.get(timeframe)
        if period is None:
            raise DataProviderRequestError(
                f"GeckoTerminal has no OHLCV resolution for {timeframe.value}; "
                "supported: " + ", ".join(tf.value for tf in (*_OHLCV_PERIOD, *_RESAMPLED_FROM))
            )
        network, address = self._split_symbol(symbol)
        pool = self._resolve_pool(network, address)
        scale = self._price_scale(network, address)

        unit, aggregate = period
        rows = self._fetch_ohlcv(network, pool, unit, aggregate, min(limit, self.max_fetch_limit))
        candles = [self._row_to_candle(symbol, timeframe, row, scale) for row in rows]
        # GeckoTerminal returns newest-first; the port's contract is oldest-first.
        candles.sort(key=lambda candle: candle.timestamp)
        logger.info(
            "Fetched %d candle(s): symbol=%s pool=%s timeframe=%s",
            len(candles),
            symbol,
            pool,
            timeframe.value,
        )
        return candles

    def series_key(self, symbol: str) -> str:
        """`gt:<network>:<pool>:<denomination>` -- what the candles describe.

        The symbol is not the identity here. A *token* address resolves to
        whichever pool is deepest by USD reserve, and liquidity migrates, so
        the same symbol can name two different charts weeks apart. The
        denomination is part of it too: `MARKET_CAP` scales every price by the
        token's supply, so cached USD bars would be off by that factor.
        """
        network, address = self._split_symbol(symbol)
        pool = self._resolve_pool(network, address)
        return f"gt:{network}:{pool}:{self._denomination.value}"

    # -- symbol / pool resolution -------------------------------------------------

    def _split_symbol(self, symbol: str) -> tuple[str, str]:
        network, separator, address = symbol.partition(":")
        if not separator:
            return self._default_network, symbol.strip()
        if not address.strip():
            raise DataProviderRequestError(f"Missing address in on-chain symbol '{symbol}'")
        return network.strip().lower(), address.strip()

    def _resolve_pool(self, network: str, address: str) -> str:
        cache_key = f"{network}:{address}"
        cached = _cached_metadata(_pool_cache, cache_key)
        if cached is not None:
            return cached

        pools = self._token_pools(network, address)
        if pools:
            pool = max(pools, key=_pool_reserve_usd)
            resolved = str(pool["attributes"]["address"])
            logger.info(
                "Resolved token %s on %s to pool %s (%s, reserve $%s)",
                address,
                network,
                resolved,
                pool["attributes"].get("name"),
                pool["attributes"].get("reserve_in_usd"),
            )
        else:
            # Not a token on this network -- assume the address is the pool
            # itself; a wrong guess surfaces as a rejected OHLCV request.
            resolved = address
        _store_metadata(_pool_cache, cache_key, resolved)
        return resolved

    def _token_pools(self, network: str, address: str) -> list[dict[str, Any]]:
        try:
            payload = self._get(f"/networks/{network}/tokens/{address}/pools")
        except DataProviderRequestError:
            return []
        data = payload.get("data") or []
        return [pool for pool in data if isinstance(pool, dict)]

    def _price_scale(self, network: str, address: str) -> float:
        """The multiplier turning the API's price into the requested denomination."""
        if self._denomination is not PriceDenomination.MARKET_CAP:
            return 1.0
        cache_key = f"{network}:{address}"
        cached = _cached_metadata(_supply_cache, cache_key)
        if cached is not None:
            return cached

        supply = 1.0
        try:
            payload = self._get(f"/networks/{network}/tokens/{address}")
            attributes = payload.get("data", {}).get("attributes", {})
            raw_supply = attributes.get("normalized_total_supply")
            if raw_supply is not None and float(raw_supply) > 0:
                supply = float(raw_supply)
        except (DataProviderRequestError, TypeError, ValueError):
            # A pool address (or a token GeckoTerminal has no supply for)
            # leaves the chart on its USD price rather than failing the fetch.
            logger.warning(
                "No token supply for %s on %s; market-cap chart falls back to USD price",
                address,
                network,
            )
        _store_metadata(_supply_cache, cache_key, supply)
        return supply

    # -- fetching -----------------------------------------------------------------

    def _fetch_ohlcv(
        self, network: str, pool: str, unit: str, aggregate: int, limit: int
    ) -> list[list[Any]]:
        payload = self._get(
            f"/networks/{network}/pools/{pool}/ohlcv/{unit}",
            {
                "aggregate": str(aggregate),
                "limit": str(limit),
                "currency": ("token" if self._denomination is PriceDenomination.QUOTE else "usd"),
                "token": "base",
            },
        )
        rows = payload.get("data", {}).get("attributes", {}).get("ohlcv_list")
        if not isinstance(rows, list):
            raise DataProviderRequestError(
                f"GeckoTerminal returned no OHLCV list for pool {pool} on {network}"
            )
        return [row for row in rows if isinstance(row, list) and len(row) >= 6]

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        url = f"{_API_ROOT}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        cached = _cached_response(url)
        if cached is not None:
            logger.debug("Cache hit %s", url)
            return cached

        @retry_with_backoff(
            exceptions=(urllib.error.URLError, TimeoutError),
            max_attempts=self._max_retries,
            base_delay_seconds=self._retry_base_delay_seconds,
        )
        def _request() -> dict[str, Any]:
            logger.debug("GET %s", url)
            _throttle()
            request = urllib.request.Request(  # noqa: S310 -- fixed https API root
                url, headers={"Accept": "application/json", "User-Agent": _USER_AGENT}
            )
            try:
                with urllib.request.urlopen(  # noqa: S310
                    request, timeout=self._timeout_seconds
                ) as response:
                    body = response.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    _open_cooldown()
                raise
            parsed: dict[str, Any] = json.loads(body)
            return parsed

        # Single-flight: while one caller fetches a URL, the others wait and
        # then find it in the cache rather than issuing the same request again.
        with _inflight_lock(url):
            cached = _cached_response(url)
            if cached is not None:
                return cached
            try:
                payload = _request()
                _store_response(url, payload)
                return payload
            except urllib.error.HTTPError as exc:
                # 4xx is the request being wrong (unknown network/pool/token);
                # a 5xx or a spent rate limit is the venue being unreachable
                # right now.
                if 400 <= exc.code < 500 and exc.code != 429:
                    raise DataProviderRequestError(
                        f"GeckoTerminal rejected {url} (HTTP {exc.code})"
                    ) from exc
                raise DataProviderConnectionError(
                    f"GeckoTerminal is unavailable for {url} (HTTP {exc.code})"
                ) from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                raise DataProviderConnectionError(
                    f"Failed to reach GeckoTerminal at {url}: {exc}"
                ) from exc
            except json.JSONDecodeError as exc:
                raise DataProviderConnectionError(
                    f"GeckoTerminal returned a non-JSON body for {url}"
                ) from exc

    def _row_to_candle(
        self, symbol: str, timeframe: TimeFrame, row: list[Any], scale: float
    ) -> Candle:
        timestamp, open_, high, low, close, volume = row[:6]
        volume_value = float(volume)
        return Candle(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=datetime.fromtimestamp(int(timestamp), tz=UTC),
            open=float(open_) * scale,
            high=float(high) * scale,
            low=float(low) * scale,
            close=float(close) * scale,
            volume=volume_value,
            # No taker split on chain data: half the volume each way, so
            # `volume_delta` reads a flat zero instead of an invented bias.
            taker_buy_volume=volume_value / 2,
        )


def _pool_reserve_usd(pool: dict[str, Any]) -> float:
    try:
        return float(pool["attributes"]["reserve_in_usd"])
    except (KeyError, TypeError, ValueError):
        return 0.0


def _resample(candles: list[Candle], timeframe: TimeFrame) -> list[Candle]:
    """Merge finished candles into the coarser `timeframe`'s buckets.

    The trailing bucket is kept even when incomplete: it is the forming candle,
    exactly what the native resolutions also return.
    """
    bucket_seconds = _TIMEFRAME_SECONDS[timeframe]
    offset = _WEEK_EPOCH_OFFSET_SECONDS if timeframe is TimeFrame.W1 else 0
    merged: list[Candle] = []
    bucket: list[Candle] = []
    bucket_start: int | None = None

    for candle in candles:
        epoch = int(candle.timestamp.timestamp())
        start = ((epoch - offset) // bucket_seconds) * bucket_seconds + offset
        if bucket_start is not None and start != bucket_start:
            merged.append(_merge(bucket, timeframe, bucket_start))
            bucket = []
        bucket_start = start
        bucket.append(candle)
    if bucket and bucket_start is not None:
        merged.append(_merge(bucket, timeframe, bucket_start))
    return merged


def _merge(bucket: list[Candle], timeframe: TimeFrame, start: int) -> Candle:
    volume = sum(candle.volume for candle in bucket)
    return Candle(
        symbol=bucket[0].symbol,
        timeframe=timeframe,
        timestamp=datetime.fromtimestamp(start, tz=UTC),
        open=bucket[0].open,
        high=max(candle.high for candle in bucket),
        low=min(candle.low for candle in bucket),
        close=bucket[-1].close,
        volume=volume,
        taker_buy_volume=sum(candle.taker_buy_volume for candle in bucket),
    )
