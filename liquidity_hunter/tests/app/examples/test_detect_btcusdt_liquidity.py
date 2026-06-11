"""Tests for the BTCUSDT liquidity detection example script."""

from liquidity_hunter.app.examples.detect_btcusdt_liquidity import LIMIT, SYMBOL, TIMEFRAME, main
from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.data.providers.base import OHLCVProvider
from liquidity_hunter.tests.liquidity.detectors._factories import make_series

HIGHS = [100, 101, 102, 110, 103, 102, 101, 100, 101, 102, 110, 103, 102, 101, 100]
LOWS = [h - 5 for h in HIGHS]


class _FakeProvider(OHLCVProvider):
    def __init__(self, candles: list[Candle]) -> None:
        self._candles = candles

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        assert symbol == SYMBOL
        assert timeframe == TIMEFRAME
        assert limit == LIMIT
        return self._candles


def test_main_detects_swing_and_equal_zones(capsys) -> None:
    candles = make_series(HIGHS, LOWS, symbol=SYMBOL)

    zones_by_type = main(provider=_FakeProvider(candles))

    assert len(zones_by_type["Swing Highs"]) == 2
    assert len(zones_by_type["Swing Lows"]) == 1
    assert len(zones_by_type["Equal Highs"]) == 1
    assert len(zones_by_type["Equal Lows"]) == 0

    captured = capsys.readouterr()
    assert "Swing Highs: 2 zone(s)" in captured.out
    assert "Equal Highs: 1 zone(s)" in captured.out
