"""Tests for the BTCUSDT liquidity scoring example script."""

from liquidity_hunter.app.examples.score_btcusdt_liquidity import LIMIT, SYMBOL, TIMEFRAME, main
from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.data.providers.base import OHLCVProvider
from liquidity_hunter.tests.liquidity.detectors._factories import make_series

# Two equal highs (110) and one swing low (90), each with ten flat
# candles either side so they are pivots under the equal-level
# detectors' production lookback of 10.
HIGHS = [102.0] * 33
HIGHS[10] = HIGHS[21] = 110.0
LOWS = [h - 5 for h in HIGHS]
LOWS[16] = 90.0


class _FakeProvider(OHLCVProvider):
    def __init__(self, candles: list[Candle]) -> None:
        self._candles = candles

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        assert symbol == SYMBOL
        assert timeframe == TIMEFRAME
        assert limit == LIMIT
        return self._candles


def test_main_scores_and_ranks_zones(capsys) -> None:
    candles = make_series(HIGHS, LOWS, symbol=SYMBOL)

    ranked = main(provider=_FakeProvider(candles))

    # 2 swing highs + 1 swing low + 1 equal-highs zone
    assert len(ranked) == 4
    scores = [scored.score for scored in ranked]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= score <= 100.0 for score in scores)

    captured = capsys.readouterr()
    assert "Current price: 99.50" in captured.out
    assert captured.out.count("Score:") == 4
