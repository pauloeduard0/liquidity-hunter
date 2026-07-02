"""Tests for `BinanceFuturesDataProvider`."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import ccxt
import pytest

from liquidity_hunter.core.domain import TimeFrame
from liquidity_hunter.data.exceptions import DataProviderConnectionError, DataProviderRequestError
from liquidity_hunter.data.providers.binance_futures import BinanceFuturesDataProvider

# Unified ccxt open-interest structure.
SAMPLE_OI = {
    "symbol": "BTC/USDT:USDT",
    "openInterestAmount": 12345.6,
    "openInterestValue": 1.2e9,
    "timestamp": 1_700_000_000_000,
}
# Unified ccxt funding-rate-history structure.
SAMPLE_FUNDING = {
    "symbol": "BTC/USDT:USDT",
    "fundingRate": 0.00012,
    "timestamp": 1_700_000_000_000,
}
# Raw Binance globalLongShortAccountRatio row.
SAMPLE_LS = {
    "symbol": "BTCUSDT",
    "longShortRatio": "1.85",
    "longAccount": "0.6491",
    "shortAccount": "0.3509",
    "timestamp": 1_700_000_000_000,
}


def _provider() -> tuple[BinanceFuturesDataProvider, MagicMock]:
    exchange = MagicMock()
    provider = BinanceFuturesDataProvider(
        exchange=exchange, max_retries=3, retry_base_delay_seconds=0.01
    )
    return provider, exchange


def test_open_interest_history_maps_rows() -> None:
    provider, exchange = _provider()
    exchange.fetch_open_interest_history.return_value = [SAMPLE_OI]

    points = provider.get_open_interest_history("BTCUSDT", TimeFrame.H1, limit=1)

    exchange.fetch_open_interest_history.assert_called_once_with(
        "BTC/USDT:USDT", "1h", limit=1
    )
    assert len(points) == 1
    assert points[0].symbol == "BTCUSDT"
    assert points[0].open_interest == 12345.6
    assert points[0].open_interest_value == 1.2e9
    assert points[0].timestamp == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)


def test_funding_rate_history_maps_rows() -> None:
    provider, exchange = _provider()
    exchange.fetch_funding_rate_history.return_value = [SAMPLE_FUNDING]

    rates = provider.get_funding_rate_history("BTCUSDT", limit=1)

    exchange.fetch_funding_rate_history.assert_called_once_with("BTC/USDT:USDT", limit=1)
    assert rates[0].funding_rate == 0.00012


def test_long_short_ratio_maps_rows() -> None:
    provider, exchange = _provider()
    exchange.fapiDataGetGlobalLongShortAccountRatio.return_value = [SAMPLE_LS]

    ratios = provider.get_long_short_ratio("BTCUSDT", TimeFrame.M15, limit=1)

    exchange.fapiDataGetGlobalLongShortAccountRatio.assert_called_once_with(
        {"symbol": "BTCUSDT", "period": "15m", "limit": 1}
    )
    assert ratios[0].ratio == 1.85
    assert ratios[0].long_account_pct == pytest.approx(0.6491)
    assert ratios[0].short_account_pct == pytest.approx(0.3509)


@patch("liquidity_hunter.data.retry.time.sleep")
def test_network_error_translated_to_connection_error(mock_sleep: MagicMock) -> None:
    provider, exchange = _provider()
    exchange.fetch_funding_rate_history.side_effect = ccxt.NetworkError("down")

    with pytest.raises(DataProviderConnectionError):
        provider.get_funding_rate_history("BTCUSDT")


def test_exchange_error_translated_to_request_error() -> None:
    provider, exchange = _provider()
    exchange.fetch_open_interest_history.side_effect = ccxt.ExchangeError("bad symbol")

    with pytest.raises(DataProviderRequestError):
        provider.get_open_interest_history("BTCUSDT", TimeFrame.H1)


def _oi_row(timestamp_ms: int) -> dict[str, Any]:
    return {
        "symbol": "BTC/USDT:USDT",
        "openInterestAmount": 1000.0 + timestamp_ms % 97,
        "openInterestValue": 1.0e9,
        "timestamp": timestamp_ms,
    }


def test_open_interest_history_paginates_past_500_rows() -> None:
    provider, exchange = _provider()
    hour_ms = 3_600_000
    now_ms = 1_700_000_000_000
    exchange.milliseconds.return_value = now_ms
    # 600 hourly samples ending one period before "now".
    all_rows = [_oi_row(now_ms - (600 - i) * hour_ms) for i in range(600)]

    def fake_fetch(
        symbol: str, period: str, since: int | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        rows = [row for row in all_rows if since is None or row["timestamp"] >= since]
        return rows[:limit]

    exchange.fetch_open_interest_history.side_effect = fake_fetch

    points = provider.get_open_interest_history("BTCUSDT", TimeFrame.H1, limit=600)

    assert len(points) == 600
    timestamps = [p.timestamp for p in points]
    assert timestamps == sorted(timestamps)
    assert len(set(timestamps)) == 600  # de-duplicated across page overlaps
    assert exchange.fetch_open_interest_history.call_count >= 2


def test_open_interest_history_pagination_clamps_to_30_days() -> None:
    provider, exchange = _provider()
    hour_ms = 3_600_000
    now_ms = 1_700_000_000_000
    exchange.milliseconds.return_value = now_ms
    exchange.fetch_open_interest_history.side_effect = (
        lambda symbol, period, since=None, limit=500: []
    )

    provider.get_open_interest_history("BTCUSDT", TimeFrame.H1, limit=2000)

    first_since = exchange.fetch_open_interest_history.call_args_list[0].kwargs["since"]
    # 2000 hourly candles would reach back ~83 days; the request must clamp to
    # 30 days (Binance's retention) plus the safety margin inside the boundary
    # (a startTime at exactly -30d is rejected with -1130).
    assert first_since == now_ms - 30 * 24 * hour_ms + hour_ms
