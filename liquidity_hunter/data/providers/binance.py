"""Binance OHLCV data provider backed by CCXT."""

import logging
from datetime import UTC, datetime
from typing import Any

import ccxt

from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.data.exceptions import DataProviderConnectionError, DataProviderRequestError
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
    """
    timestamp_ms, open_, high, low, close, volume = row[:6]
    taker_buy_volume = row[9]
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        timestamp=datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=UTC),
        open=float(open_),
        high=float(high),
        low=float(low),
        close=float(close),
        volume=float(volume),
        taker_buy_volume=float(taker_buy_volume),
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
