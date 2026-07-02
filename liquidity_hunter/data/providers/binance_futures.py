"""Binance USDT-M perpetual-futures data provider backed by CCXT."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
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

_PERIOD_MS: dict[str, int] = {
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}

# Binance caps each open-interest-history request at 500 rows and only keeps
# ~30 days of history at all; requests beyond either bound are paginated /
# clamped in `get_open_interest_history`. A `startTime` at (or beyond) exactly
# 30 days ago is rejected with -1130 "parameter 'startTime' is invalid", so the
# clamp keeps a safety margin inside the boundary (also absorbing clock drift
# between us and the venue).
_OI_MAX_ROWS_PER_REQUEST = 500
_OI_HISTORY_MAX_DAYS = 30
_OI_HISTORY_CLAMP_MARGIN_MS = 60 * 60_000  # 1 hour


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
        if limit <= _OI_MAX_ROWS_PER_REQUEST:
            rows = self._call(
                lambda: self._exchange.fetch_open_interest_history(
                    swap_symbol, period, limit=limit
                ),
                symbol,
                "open interest history",
            )
            return [self._to_open_interest(symbol, row) for row in rows]
        return self._paginated_open_interest(symbol, swap_symbol, period, limit)

    def _paginated_open_interest(
        self, symbol: str, swap_symbol: str, period: str, limit: int
    ) -> list[OpenInterestPoint]:
        """Page forward through open-interest history to exceed the 500-row cap.

        Starts at the requested window's beginning (clamped to Binance's ~30-day
        retention) and pages forward with `since` until "now" is reached or the
        venue stops returning new rows. Rows are de-duplicated by timestamp, so
        overlapping pages are harmless.
        """
        period_ms = _PERIOD_MS.get(period, _PERIOD_MS[_DEFAULT_PERIOD])
        now_ms = self._exchange.milliseconds()
        retention_floor = (
            now_ms - _OI_HISTORY_MAX_DAYS * 86_400_000 + _OI_HISTORY_CLAMP_MARGIN_MS
        )
        since = max(now_ms - limit * period_ms, retention_floor)
        by_timestamp: dict[int, dict[str, Any]] = {}
        while since < now_ms:
            page = self._call(
                partial(self._fetch_oi_page, swap_symbol, period, since),
                symbol,
                "open interest history",
            )
            if not page:
                break
            for row in page:
                by_timestamp[int(row["timestamp"])] = row
            last_ts = int(page[-1]["timestamp"])
            next_since = last_ts + period_ms
            if next_since <= since:
                break  # no forward progress; avoid an infinite loop
            since = next_since
        ordered = [by_timestamp[ts] for ts in sorted(by_timestamp)][-limit:]
        return [self._to_open_interest(symbol, row) for row in ordered]

    def _fetch_oi_page(
        self, swap_symbol: str, period: str, since: int
    ) -> list[dict[str, Any]]:
        page: list[dict[str, Any]] = self._exchange.fetch_open_interest_history(
            swap_symbol, period, since=since, limit=_OI_MAX_ROWS_PER_REQUEST
        )
        return page

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
