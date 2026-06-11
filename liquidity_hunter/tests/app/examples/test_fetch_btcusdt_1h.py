"""Tests for the BTCUSDT 1h fetch example script."""

from datetime import UTC, datetime

from liquidity_hunter.app.examples.fetch_btcusdt_1h import LIMIT, SYMBOL, TIMEFRAME, main
from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.data.providers.base import OHLCVProvider


class _FakeProvider(OHLCVProvider):
    def __init__(self, candles: list[Candle]) -> None:
        self._candles = candles

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        assert symbol == SYMBOL
        assert timeframe == TIMEFRAME
        assert limit == LIMIT
        return self._candles


def _make_candles(count: int) -> list[Candle]:
    return [
        Candle(
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=10.0,
        )
        for _ in range(count)
    ]


def test_main_returns_all_fetched_candles(capsys) -> None:
    candles = _make_candles(10)
    result = main(provider=_FakeProvider(candles))

    assert result == candles


def test_main_prints_only_first_five(capsys) -> None:
    candles = _make_candles(10)
    main(provider=_FakeProvider(candles))

    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 5
