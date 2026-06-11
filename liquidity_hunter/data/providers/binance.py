"""Binance OHLCV data provider backed by CCXT."""

import logging
from datetime import UTC, datetime

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


class BinanceDataProvider(OHLCVProvider):
    """Fetches OHLCV candles from Binance via CCXT."""

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
        raw_rows = self._fetch_ohlcv(ccxt_symbol, timeframe, limit)
        return [self._to_candle(symbol, timeframe, row) for row in raw_rows]

    def _fetch_ohlcv(
        self, ccxt_symbol: str, timeframe: TimeFrame, limit: int
    ) -> list[list[float]]:
        @retry_with_backoff(
            exceptions=(ccxt.NetworkError,),
            max_attempts=self._max_retries,
            base_delay_seconds=self._retry_base_delay_seconds,
        )
        def _fetch() -> list[list[float]]:
            logger.debug(
                "Fetching OHLCV: symbol=%s timeframe=%s limit=%d",
                ccxt_symbol,
                timeframe.value,
                limit,
            )
            result: list[list[float]] = self._exchange.fetch_ohlcv(
                ccxt_symbol, timeframe=timeframe.value, limit=limit
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

    @staticmethod
    def _to_candle(symbol: str, timeframe: TimeFrame, row: list[float]) -> Candle:
        timestamp_ms, open_, high, low, close, volume = row
        return Candle(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC),
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )
