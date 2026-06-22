"""Tests for the liquidation backtest example script."""

import pytest

from liquidity_hunter.app.examples.backtest_liquidations import LIMIT, SYMBOL, TIMEFRAME, main
from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.data.providers.base import OHLCVProvider
from liquidity_hunter.tests.liquidity.detectors._factories import make_series


def _zigzag(num: int) -> list[Candle]:
    highs, lows = [], []
    for i in range(num):
        phase = i % 20
        wave = 15.0 * (phase / 10 if phase < 10 else (1 - (phase - 10) / 10))
        level = 100.0 + 0.5 * i + wave
        highs.append(level + 2.0)
        lows.append(level - 2.0)
    return make_series(highs, lows, symbol=SYMBOL)


class _FakeProvider(OHLCVProvider):
    def __init__(self, candles: list[Candle]) -> None:
        self._candles = candles

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        assert symbol == SYMBOL
        assert timeframe == TIMEFRAME
        assert limit == LIMIT
        return self._candles


def test_main_runs_backtest_and_reports(capsys: pytest.CaptureFixture[str]) -> None:
    result = main(provider=_FakeProvider(_zigzag(LIMIT)))

    assert result.symbol == SYMBOL
    assert result.n_eval_points > 0
    assert result.n_levels > 0

    out = capsys.readouterr().out
    assert "Liquidation backtest" in out
    assert "by leverage" in out
    assert "by distance bucket" in out
