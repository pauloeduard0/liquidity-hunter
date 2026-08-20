"""Tests for `RoutingOHLCVProvider` and on-chain symbol recognition."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.data.providers.base import OHLCVProvider
from liquidity_hunter.data.providers.routing import RoutingOHLCVProvider, is_onchain_symbol

SOLANA_TOKEN = "Ge87EtsjwRQbHaqQmKRno69RFTwh9bfSsm99XNxTpump"
EVM_TOKEN = "0x" + "ab" * 20


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        (SOLANA_TOKEN, True),
        (f"solana:{SOLANA_TOKEN}", True),
        (EVM_TOKEN, True),
        (f"base:{EVM_TOKEN}", True),
        ("BTCUSDT", False),
        ("BTC/USDT", False),
        ("solana:BTC", False),
        ("", False),
    ],
)
def test_symbol_recognition(symbol: str, expected: bool) -> None:
    assert is_onchain_symbol(symbol) is expected


def _stub(limit: int) -> OHLCVProvider:
    provider = MagicMock(spec=OHLCVProvider)
    provider.max_fetch_limit = limit
    provider.get_ohlcv.return_value = [
        Candle(
            symbol="X",
            timeframe=TimeFrame.H1,
            timestamp=datetime.fromtimestamp(1_700_000_000, tz=UTC),
            open=1.0,
            high=2.0,
            low=0.5,
            close=1.5,
            volume=10.0,
            taker_buy_volume=5.0,
        )
    ]
    return provider


def test_exchange_symbols_go_to_the_exchange_source() -> None:
    exchange, onchain = _stub(1500), _stub(1000)
    router = RoutingOHLCVProvider(exchange=exchange, onchain=onchain)

    router.get_ohlcv("BTCUSDT", TimeFrame.H1, 1500)

    exchange.get_ohlcv.assert_called_once_with("BTCUSDT", TimeFrame.H1, 1500)  # type: ignore[attr-defined]
    onchain.get_ohlcv.assert_not_called()  # type: ignore[attr-defined]


def test_onchain_symbols_go_to_the_onchain_source_capped_to_its_limit() -> None:
    exchange, onchain = _stub(1500), _stub(1000)
    router = RoutingOHLCVProvider(exchange=exchange, onchain=onchain)

    router.get_ohlcv(f"solana:{SOLANA_TOKEN}", TimeFrame.H1, 1500)

    onchain.get_ohlcv.assert_called_once_with(f"solana:{SOLANA_TOKEN}", TimeFrame.H1, 1000)  # type: ignore[attr-defined]
    exchange.get_ohlcv.assert_not_called()  # type: ignore[attr-defined]


def test_advertised_limit_is_the_larger_of_the_two() -> None:
    router = RoutingOHLCVProvider(exchange=_stub(1500), onchain=_stub(1000))

    assert router.max_fetch_limit == 1500
