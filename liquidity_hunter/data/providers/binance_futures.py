"""Binance USDT-M perpetual-futures data provider backed by CCXT."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

import ccxt

from liquidity_hunter.core.domain import (
    FundingRate,
    LongShortRatio,
    OpenInterestPoint,
    TimeFrame,
)
from liquidity_hunter.data.exceptions import DataProviderConnectionError, DataProviderRequestError
from liquidity_hunter.data.providers.base import FuturesDataProvider
from liquidity_hunter.data.providers.binance import to_ccxt_symbol
from liquidity_hunter.data.retry import retry_with_backoff

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Binance's futures-data endpoints only accept a fixed set of periods. Map each
# `TimeFrame` to the nearest supported one (open-interest history and the
# long/short account ratio share this set).
_FUTURES_PERIOD: dict[TimeFrame, str] = {
    TimeFrame.M1: "5m",
    TimeFrame.M5: "5m",
    TimeFrame.M15: "15m",
    TimeFrame.M30: "30m",
    TimeFrame.H1: "1h",
    TimeFrame.H4: "4h",
    TimeFrame.D1: "1d",
    TimeFrame.W1: "1d",
}
_DEFAULT_PERIOD = "1h"


class BinanceFuturesDataProvider(FuturesDataProvider):
    """Fetches perpetual-futures market state from Binance USDT-M via CCXT."""

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

    def get_open_interest_history(
        self, symbol: str, timeframe: TimeFrame, limit: int = 500
    ) -> list[OpenInterestPoint]:
        period = _FUTURES_PERIOD.get(timeframe, _DEFAULT_PERIOD)
        swap_symbol = self._swap_symbol(symbol)
        rows = self._call(
            lambda: self._exchange.fetch_open_interest_history(swap_symbol, period, limit=limit),
            symbol,
            "open interest history",
        )
        return [self._to_open_interest(symbol, row) for row in rows]

    def get_funding_rate_history(self, symbol: str, limit: int = 500) -> list[FundingRate]:
        swap_symbol = self._swap_symbol(symbol)
        rows = self._call(
            lambda: self._exchange.fetch_funding_rate_history(swap_symbol, limit=limit),
            symbol,
            "funding rate history",
        )
        return [self._to_funding_rate(symbol, row) for row in rows]

    def get_long_short_ratio(
        self, symbol: str, timeframe: TimeFrame, limit: int = 500
    ) -> list[LongShortRatio]:
        period = _FUTURES_PERIOD.get(timeframe, _DEFAULT_PERIOD)
        binance_symbol = to_ccxt_symbol(symbol).replace("/", "")
        rows = self._call(
            lambda: self._exchange.fapiDataGetGlobalLongShortAccountRatio(
                {"symbol": binance_symbol, "period": period, "limit": limit}
            ),
            symbol,
            "long/short ratio",
        )
        return [self._to_long_short_ratio(symbol, row) for row in rows]

    @staticmethod
    def _swap_symbol(symbol: str) -> str:
        """The CCXT unified swap symbol (e.g. "BTC/USDT:USDT") for `symbol`."""
        unified = to_ccxt_symbol(symbol)
        quote = unified.split("/")[1]
        return f"{unified}:{quote}"

    def _call(self, fetch: Callable[[], T], symbol: str, what: str) -> T:
        """Run `fetch` with retry/backoff and translate ccxt errors."""

        @retry_with_backoff(
            exceptions=(ccxt.NetworkError,),
            max_attempts=self._max_retries,
            base_delay_seconds=self._retry_base_delay_seconds,
        )
        def _fetch() -> T:
            logger.debug("Fetching %s: symbol=%s", what, symbol)
            return fetch()

        try:
            return _fetch()
        except ccxt.NetworkError as exc:
            raise DataProviderConnectionError(
                f"Failed to reach Binance futures for {symbol} {what}: {exc}"
            ) from exc
        except ccxt.ExchangeError as exc:
            raise DataProviderRequestError(
                f"Binance futures rejected {what} request for {symbol}: {exc}"
            ) from exc

    @staticmethod
    def _timestamp(value: Any) -> datetime:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)

    @classmethod
    def _to_open_interest(cls, symbol: str, row: dict[str, Any]) -> OpenInterestPoint:
        amount = row.get("openInterestAmount")
        value = row.get("openInterestValue")
        return OpenInterestPoint(
            symbol=symbol,
            timestamp=cls._timestamp(row["timestamp"]),
            open_interest=float(amount) if amount is not None else 0.0,
            open_interest_value=float(value) if value is not None else 0.0,
        )

    @classmethod
    def _to_funding_rate(cls, symbol: str, row: dict[str, Any]) -> FundingRate:
        return FundingRate(
            symbol=symbol,
            timestamp=cls._timestamp(row["timestamp"]),
            funding_rate=float(row["fundingRate"]),
        )

    @classmethod
    def _to_long_short_ratio(cls, symbol: str, row: dict[str, Any]) -> LongShortRatio:
        long_pct = float(row["longAccount"])
        short_pct = float(row["shortAccount"])
        return LongShortRatio(
            symbol=symbol,
            timestamp=cls._timestamp(row["timestamp"]),
            long_account_pct=long_pct,
            short_account_pct=short_pct,
            ratio=float(row["longShortRatio"]),
        )
