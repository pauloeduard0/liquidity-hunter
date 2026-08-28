"""Binance OHLCV data provider backed by CCXT."""

import logging
from datetime import UTC, datetime
from typing import Any

import ccxt

from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.data.exceptions import (
    DataProviderBannedError,
    DataProviderConnectionError,
    DataProviderRequestError,
)
from liquidity_hunter.data.providers.base import OHLCVProvider
from liquidity_hunter.data.retry import retry_with_backoff

logger = logging.getLogger(__name__)

# Quote assets ordered longest-first so e.g. "BUSD" is matched before "USD".
_QUOTE_ASSETS = ("USDT", "BUSD", "USDC", "FDUSD", "TUSD", "BTC", "ETH", "BNB", "EUR", "USD")


def to_ccxt_symbol(symbol: str) -> str:
    """Convert a concatenated symbol (e.g. "BTCUSDT") to CCXT's unified form ("BTC/USDT").

    Symbols already containing "/" are returned unchanged.
    """
    if "/" in symbol:
        return symbol
    for quote in _QUOTE_ASSETS:
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return f"{symbol[: -len(quote)]}/{quote}"
    raise ValueError(f"Unable to determine base/quote split for symbol '{symbol}'")


def klines_row_to_candle(symbol: str, timeframe: TimeFrame, row: list[Any]) -> Candle:
    """Map one raw Binance kline row (12 columns) onto a `Candle`.

    Shared by the spot and USDT-M futures providers, whose `/api/v3/klines`
    and `/fapi/v1/klines` responses share the same column layout -- notably
    taker buy base asset volume at column index 9, the basis for `volume_delta`.

    **A row can arrive with `taker_buy_volume > volume`**, which is impossible
    -- the taker buy side is a part of the whole. It is rare (5 rows across 432
    cached series) and it is not rounding: the excesses measured 4% to 77%. But
    `Candle` rejects it, correctly, and that `ValidationError` used to travel
    all the way up and kill the whole symbol/timeframe, so one bad print of
    2023 removed EGLDUSDT 4h from every study that touched it.

    What is corrupt there is the buy/sell *split*, not the price: OHLC and the
    bar's place in the series are fine. So the split is treated as **unknown**
    and set to half the volume, which is exactly the contract the on-chain and
    equity providers already keep for sources that publish no aggressor side --
    `volume_delta` then reads a flat zero and every flow layer goes quiet for
    that bar, instead of a clamp to `volume` inventing a maximally bullish
    candle out of a data error. The candle itself survives, so the series keeps
    its shape and no gap opens where a real bar traded.

    The domain invariant stays strict. Deciding what a malformed response means
    is the provider's job, which is the layer that knows it came from a wire.
    """
    timestamp_ms, open_, high, low, close, volume = row[:6]
    volume = float(volume)
    taker_buy_volume = float(row[9])
    if taker_buy_volume > volume:
        logger.warning(
            "%s %s: taker buy volume %.4f exceeds volume %.4f at %s; "
            "reading the aggressor split as unknown",
            symbol,
            timeframe.value,
            taker_buy_volume,
            volume,
            timestamp_ms,
        )
        taker_buy_volume = volume / 2
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=UTC),
        open=float(open_),
        high=float(high),
        low=float(low),
        close=float(close),
        volume=volume,
        taker_buy_volume=taker_buy_volume,
    )


class BinanceDataProvider(OHLCVProvider):
    """Fetches OHLCV candles from Binance spot via CCXT."""

    # Binance spot's `/api/v3/klines` endpoint accepts `limit` up to 1000.
    max_fetch_limit = 1000

    def __init__(
        self,
        exchange: ccxt.Exchange | None = None,
        max_retries: int = 3,
        retry_base_delay_seconds: float = 1.0,
    ) -> None:
        self._exchange = (
            exchange if exchange is not None else ccxt.binance({"enableRateLimit": True})
        )
        self._max_retries = max_retries
        self._retry_base_delay_seconds = retry_base_delay_seconds

    def series_key(self, symbol: str) -> str:
        """`binance-spot:<symbol>` -- the venue is part of the identity.

        The spot and perpetual books for one ticker are different series
        with the same name, and `FallbackOHLCVProvider` can serve either,
        so a cache keyed on the symbol alone would splice the two.
        """
        return f"binance-spot:{symbol.upper()}"

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        """Fetch up to `limit` candles for `symbol`/`timeframe` from Binance.

        Raises:
            DataProviderConnectionError: if the exchange cannot be reached
                after retries.
            DataProviderRequestError: if Binance rejects the request (e.g.
                unknown symbol or invalid timeframe).
        """
        ccxt_symbol = to_ccxt_symbol(symbol)
        raw_rows = self._fetch_klines(ccxt_symbol, timeframe, limit)
        return [klines_row_to_candle(symbol, timeframe, row) for row in raw_rows]

    def _fetch_klines(self, ccxt_symbol: str, timeframe: TimeFrame, limit: int) -> list[list[Any]]:
        # ccxt's unified `fetch_ohlcv` only returns 6 columns (no taker buy
        # volume), so the raw `/api/v3/klines` endpoint is used instead via
        # ccxt's implicit `publicGetKlines` method, which returns Binance's
        # native 12-column rows including taker buy base asset volume.
        binance_symbol = ccxt_symbol.replace("/", "")

        @retry_with_backoff(
            exceptions=(ccxt.NetworkError,),
            max_attempts=self._max_retries,
            base_delay_seconds=self._retry_base_delay_seconds,
        )
        def _fetch() -> list[list[Any]]:
            # A ban is not transient: raising it past the retry decorator is
            # what stops 284 jobs from hammering a banned IP.

            logger.debug(
                "Fetching klines: symbol=%s timeframe=%s limit=%d",
                binance_symbol,
                timeframe.value,
                limit,
            )
            result: list[list[Any]] = self._exchange.publicGetKlines(
                {"symbol": binance_symbol, "interval": timeframe.value, "limit": limit}
            )
            return result

        try:
            rows = _fetch()
        except (ccxt.DDoSProtection, ccxt.RateLimitExceeded) as exc:
            raise DataProviderBannedError(str(exc)) from exc
        except ccxt.NetworkError as exc:
            raise DataProviderConnectionError(
                f"Failed to reach Binance for {ccxt_symbol} {timeframe.value}: {exc}"
            ) from exc
        except ccxt.ExchangeError as exc:
            raise DataProviderRequestError(
                f"Binance rejected OHLCV request for {ccxt_symbol} {timeframe.value}: {exc}"
            ) from exc

        logger.info(
            "Fetched %d candle(s): symbol=%s timeframe=%s",
            len(rows),
            ccxt_symbol,
            timeframe.value,
        )
        return rows
