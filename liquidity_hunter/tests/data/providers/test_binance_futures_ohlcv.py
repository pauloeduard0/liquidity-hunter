"""Tests for `BinanceFuturesOHLCVProvider`."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import ccxt
import pytest

from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.data.exceptions import DataProviderConnectionError, DataProviderRequestError
from liquidity_hunter.data.providers.binance_futures_ohlcv import BinanceFuturesOHLCVProvider

# Raw Binance futures kline row (12 columns), same layout as spot: taker buy
# base asset volume sits at index 9.
SAMPLE_ROW = [
    1_700_000_000_000,
    "100.0",
    "110.0",
    "90.0",
    "105.0",
    "1234.5",
    1_700_003_599_999,
    "129000.0",
    100,
    "617.25",
    "64500.0",
    "0",
]


def test_max_fetch_limit_is_1500() -> None:
    assert BinanceFuturesOHLCVProvider.max_fetch_limit == 1500


def test_get_ohlcv_returns_candles_from_futures_klines() -> None:
    mock_exchange = MagicMock()
    mock_exchange.fapiPublicGetKlines.return_value = [SAMPLE_ROW]

    provider = BinanceFuturesOHLCVProvider(exchange=mock_exchange)
    candles = provider.get_ohlcv("BTCUSDT", TimeFrame.H1, limit=1)

    assert candles == [
        Candle(
            symbol="BTCUSDT",
            timeframe=TimeFrame.H1,
            timestamp=datetime.fromtimestamp(1_700_000_000_000 / 1000, tz=UTC),
            open=100.0,
            high=110.0,
            low=90.0,
            close=105.0,
            volume=1234.5,
            taker_buy_volume=617.25,
        )
    ]
    mock_exchange.fapiPublicGetKlines.assert_called_once_with(
        {"symbol": "BTCUSDT", "interval": "1h", "limit": 1}
    )


@patch("liquidity_hunter.data.retry.time.sleep")
def test_get_ohlcv_retries_on_network_error_then_succeeds(mock_sleep) -> None:
    mock_exchange = MagicMock()
    mock_exchange.fapiPublicGetKlines.side_effect = [
        ccxt.NetworkError("timeout"),
        ccxt.NetworkError("timeout"),
        [SAMPLE_ROW],
    ]

    provider = BinanceFuturesOHLCVProvider(
        exchange=mock_exchange, max_retries=3, retry_base_delay_seconds=0.01
    )
    candles = provider.get_ohlcv("BTCUSDT", TimeFrame.H1, limit=1)

    assert len(candles) == 1
    assert mock_exchange.fapiPublicGetKlines.call_count == 3


@patch("liquidity_hunter.data.retry.time.sleep")
def test_get_ohlcv_raises_connection_error_after_max_retries(mock_sleep) -> None:
    mock_exchange = MagicMock()
    mock_exchange.fapiPublicGetKlines.side_effect = ccxt.NetworkError("timeout")

    provider = BinanceFuturesOHLCVProvider(
        exchange=mock_exchange, max_retries=3, retry_base_delay_seconds=0.01
    )

    with pytest.raises(DataProviderConnectionError):
        provider.get_ohlcv("BTCUSDT", TimeFrame.H1, limit=1)

    assert mock_exchange.fapiPublicGetKlines.call_count == 3


def test_get_ohlcv_raises_request_error_on_exchange_error_without_retry() -> None:
    mock_exchange = MagicMock()
    mock_exchange.fapiPublicGetKlines.side_effect = ccxt.BadSymbol("invalid symbol")

    provider = BinanceFuturesOHLCVProvider(
        exchange=mock_exchange, max_retries=3, retry_base_delay_seconds=0.01
    )

    with pytest.raises(DataProviderRequestError):
        provider.get_ohlcv("DOGEUSDT", TimeFrame.H1, limit=1)

    assert mock_exchange.fapiPublicGetKlines.call_count == 1
