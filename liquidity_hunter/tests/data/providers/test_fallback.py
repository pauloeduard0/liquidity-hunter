"""Tests for `FallbackOHLCVProvider`."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.data.exceptions import (
    DataProviderConnectionError,
    DataProviderRequestError,
)
from liquidity_hunter.data.providers.base import OHLCVProvider
from liquidity_hunter.data.providers.fallback import FallbackOHLCVProvider


def _candle(symbol: str) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=TimeFrame.H1,
        timestamp=datetime.fromtimestamp(1_700_000_000, tz=UTC),
        open=100.0,
        high=110.0,
        low=90.0,
        close=105.0,
        volume=1000.0,
        taker_buy_volume=500.0,
    )


def _provider(max_fetch_limit: int) -> MagicMock:
    provider = MagicMock(spec=OHLCVProvider)
    provider.max_fetch_limit = max_fetch_limit
    return provider


def test_uses_primary_when_it_succeeds() -> None:
    primary = _provider(1500)
    secondary = _provider(1000)
    primary.get_ohlcv.return_value = [_candle("PRIMARY")]

    provider = FallbackOHLCVProvider(primary, secondary)
    candles = provider.get_ohlcv("BTCUSDT", TimeFrame.H1, limit=700)

    assert candles == [_candle("PRIMARY")]
    primary.get_ohlcv.assert_called_once_with("BTCUSDT", TimeFrame.H1, 700)
    secondary.get_ohlcv.assert_not_called()


def test_falls_back_to_secondary_on_request_error() -> None:
    primary = _provider(1500)
    secondary = _provider(1000)
    primary.get_ohlcv.side_effect = DataProviderRequestError("no perpetual contract")
    secondary.get_ohlcv.return_value = [_candle("SECONDARY")]

    provider = FallbackOHLCVProvider(primary, secondary)
    candles = provider.get_ohlcv("OBSCURECOIN", TimeFrame.H1, limit=700)

    assert candles == [_candle("SECONDARY")]
    secondary.get_ohlcv.assert_called_once_with("OBSCURECOIN", TimeFrame.H1, 700)


def test_caps_fallback_limit_to_secondary_max() -> None:
    primary = _provider(1500)
    secondary = _provider(1000)
    primary.get_ohlcv.side_effect = DataProviderRequestError("no perpetual contract")
    secondary.get_ohlcv.return_value = [_candle("SECONDARY")]

    provider = FallbackOHLCVProvider(primary, secondary)
    provider.get_ohlcv("OBSCURECOIN", TimeFrame.H1, limit=1400)

    # 1400 exceeds spot's 1000 cap, so the fallback request is clamped.
    secondary.get_ohlcv.assert_called_once_with("OBSCURECOIN", TimeFrame.H1, 1000)


def test_connection_error_propagates_without_fallback() -> None:
    primary = _provider(1500)
    secondary = _provider(1000)
    primary.get_ohlcv.side_effect = DataProviderConnectionError("network down")

    provider = FallbackOHLCVProvider(primary, secondary)
    with pytest.raises(DataProviderConnectionError):
        provider.get_ohlcv("BTCUSDT", TimeFrame.H1, limit=700)

    secondary.get_ohlcv.assert_not_called()


def test_max_fetch_limit_follows_primary() -> None:
    provider = FallbackOHLCVProvider(_provider(1500), _provider(1000))
    assert provider.max_fetch_limit == 1500
