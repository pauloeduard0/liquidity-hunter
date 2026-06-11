"""Tests for `BinanceDataProvider`."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import ccxt
import pytest

from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.data.exceptions import DataProviderConnectionError, DataProviderRequestError
from liquidity_hunter.data.providers.binance import BinanceDataProvider, to_ccxt_symbol

SAMPLE_ROW = [1_700_000_000_000, 100.0, 110.0, 90.0, 105.0, 1234.5]


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("BTCUSDT", "BTC/USDT"),
        ("ETHBTC", "ETH/BTC"),
        ("ETHBUSD", "ETH/BUSD"),
        ("BTC/USDT", "BTC/USDT"),
    ],
)
def test_to_ccxt_symbol(symbol: str, expected: str) -> None:
    assert to_ccxt_symbol(symbol) == expected


def test_to_ccxt_symbol_rejects_unknown_format() -> None:
    with pytest.raises(ValueError, match="Unable to determine"):
        to_ccxt_symbol("NOTASYMBOL")


def test_get_ohlcv_returns_candles() -> None:
    mock_exchange = MagicMock()
    mock_exchange.fetch_ohlcv.return_value = [SAMPLE_ROW]

    provider = BinanceDataProvider(exchange=mock_exchange)
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
        )
    ]
    mock_exchange.fetch_ohlcv.assert_called_once_with("BTC/USDT", timeframe="1h", limit=1)


@patch("liquidity_hunter.data.retry.time.sleep")
def test_get_ohlcv_retries_on_network_error_then_succeeds(mock_sleep) -> None:
    mock_exchange = MagicMock()
    mock_exchange.fetch_ohlcv.side_effect = [
        ccxt.NetworkError("timeout"),
        ccxt.NetworkError("timeout"),
        [SAMPLE_ROW],
    ]

    provider = BinanceDataProvider(
        exchange=mock_exchange, max_retries=3, retry_base_delay_seconds=0.01
    )
    candles = provider.get_ohlcv("BTCUSDT", TimeFrame.H1, limit=1)

    assert len(candles) == 1
    assert mock_exchange.fetch_ohlcv.call_count == 3


@patch("liquidity_hunter.data.retry.time.sleep")
def test_get_ohlcv_raises_connection_error_after_max_retries(mock_sleep) -> None:
    mock_exchange = MagicMock()
    mock_exchange.fetch_ohlcv.side_effect = ccxt.NetworkError("timeout")

    provider = BinanceDataProvider(
        exchange=mock_exchange, max_retries=3, retry_base_delay_seconds=0.01
    )

    with pytest.raises(DataProviderConnectionError):
        provider.get_ohlcv("BTCUSDT", TimeFrame.H1, limit=1)

    assert mock_exchange.fetch_ohlcv.call_count == 3


def test_get_ohlcv_raises_request_error_on_exchange_error_without_retry() -> None:
    mock_exchange = MagicMock()
    mock_exchange.fetch_ohlcv.side_effect = ccxt.BadSymbol("invalid symbol")

    provider = BinanceDataProvider(
        exchange=mock_exchange, max_retries=3, retry_base_delay_seconds=0.01
    )

    with pytest.raises(DataProviderRequestError):
        provider.get_ohlcv("BTCUSDT", TimeFrame.H1, limit=1)

    assert mock_exchange.fetch_ohlcv.call_count == 1
