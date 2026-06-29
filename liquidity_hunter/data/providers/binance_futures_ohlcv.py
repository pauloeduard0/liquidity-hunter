"""Binance USDT-M perpetual-futures OHLCV provider backed by CCXT."""

import logging
from typing import Any

import ccxt

from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.data.exceptions import DataProviderConnectionError, DataProviderRequestError
from liquidity_hunter.data.providers.base import OHLCVProvider
from liquidity_hunter.data.providers.binance import klines_row_to_candle, to_ccxt_symbol
from liquidity_hunter.data.retry import retry_with_backoff

logger = logging.getLogger(__name__)


class BinanceFuturesOHLCVProvider(OHLCVProvider):
    """Fetches OHLCV candles from Binance USDT-M perpetual futures via CCXT.

    Perpetual-futures candles align the chart with the futures-derived analysis
    already overlaid on it (open interest, funding, long/short ratio, and the
    leverage-liquidation map), and reflect the leveraged/speculative flow this
    platform studies better than spot. The `/fapi/v1/klines` response shares
    spot's 12-column layout, so taker buy base asset volume (the basis for
    `volume_delta`) is still available, and its `limit` cap is 1500 (vs spot's
    1000), covering a larger window in a single request. Symbols without a
    perpetual contract raise `DataProviderRequestError`; pair this with spot via
    `FallbackOHLCVProvider` to degrade gracefully.
    """

    # Binance futures' `/fapi/v1/klines` endpoint accepts `limit` up to 1500.
    max_fetch_limit = 1500

    def __init__(
        self,
        exchange: ccxt.Exchange | None = None,
        max_retries: int = 3,
        retry_base_delay_seconds: float = 1.0,
    ) -> None:
        self._exchange = (
            exchange if exchange is not None else ccxt.binanceusdm({"enableRateLimit": True})
        )
        self._max_retries = max_retries
        self._retry_base_delay_seconds = retry_base_delay_seconds

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        """Fetch up to `limit` futures candles for `symbol`/`timeframe`.

        Raises:
            DataProviderConnectionError: if the exchange cannot be reached
                after retries.
            DataProviderRequestError: if Binance rejects the request (e.g. the
                symbol has no perpetual contract, or an invalid timeframe).
        """
        ccxt_symbol = to_ccxt_symbol(symbol)
        raw_rows = self._fetch_klines(ccxt_symbol, timeframe, limit)
        return [klines_row_to_candle(symbol, timeframe, row) for row in raw_rows]

    def _fetch_klines(self, ccxt_symbol: str, timeframe: TimeFrame, limit: int) -> list[list[Any]]:
        # As with the spot provider, ccxt's unified `fetch_ohlcv` drops taker
        # buy volume, so the raw `/fapi/v1/klines` endpoint is used via ccxt's
        # implicit `fapiPublicGetKlines`, returning Binance's native 12-column
        # rows including taker buy base asset volume.
        binance_symbol = ccxt_symbol.replace("/", "")

        @retry_with_backoff(
            exceptions=(ccxt.NetworkError,),
            max_attempts=self._max_retries,
            base_delay_seconds=self._retry_base_delay_seconds,
        )
        def _fetch() -> list[list[Any]]:
            logger.debug(
                "Fetching futures klines: symbol=%s timeframe=%s limit=%d",
                binance_symbol,
                timeframe.value,
                limit,
            )
            result: list[list[Any]] = self._exchange.fapiPublicGetKlines(
                {"symbol": binance_symbol, "interval": timeframe.value, "limit": limit}
            )
            return result

        try:
            rows = _fetch()
        except ccxt.NetworkError as exc:
            raise DataProviderConnectionError(
                f"Failed to reach Binance futures for {ccxt_symbol} {timeframe.value}: {exc}"
            ) from exc
        except ccxt.ExchangeError as exc:
            raise DataProviderRequestError(
                f"Binance futures rejected OHLCV request for {ccxt_symbol} {timeframe.value}: {exc}"
            ) from exc

        logger.info(
            "Fetched %d futures candle(s): symbol=%s timeframe=%s",
            len(rows),
            ccxt_symbol,
            timeframe.value,
        )
        return rows
