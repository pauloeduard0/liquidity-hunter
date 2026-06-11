"""Tests for `liquidity_hunter.app.dashboard_data`."""

import pytest

from liquidity_hunter.app.dashboard_data import _infer_trend_direction, load_dashboard_data
from liquidity_hunter.core.domain import Candle, MarketDirection, RetailPositioning, TimeFrame
from liquidity_hunter.data.providers.base import OHLCVProvider
from liquidity_hunter.tests.liquidity.detectors._factories import make_series

HIGHS = [
    100.0, 101.0, 102.0, 110.0, 103.0, 102.0, 101.0,
    100.0, 101.0, 102.0, 110.0, 103.0, 102.0, 101.0, 100.0,
]
LOWS = [h - 5 for h in HIGHS]


class _FakeProvider(OHLCVProvider):
    def __init__(self, candles: list[Candle]) -> None:
        self._candles = candles

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        return self._candles


def test_load_dashboard_data_assembles_research_snapshot() -> None:
    candles = make_series(HIGHS, LOWS, symbol="BTCUSDT")

    data = load_dashboard_data(provider=_FakeProvider(candles), symbol="BTCUSDT")

    assert data.symbol == "BTCUSDT"
    assert data.candles == candles
    assert data.current_price == candles[-1].close
    assert data.market_structure_events == []

    # 2 swing highs + 1 swing low + 1 equal-highs zone (see detector tests)
    assert len(data.liquidity_zones) == 4
    assert len(data.ranked_zones) == len(data.liquidity_zones)
    scores = [scored.score for scored in data.ranked_zones]
    assert scores == sorted(scores, reverse=True)

    assert data.retail_bias.symbol == "BTCUSDT"
    assert isinstance(data.retail_bias.dominant_side, RetailPositioning)


@pytest.mark.parametrize(
    ("highs", "expected"),
    [
        ([101.0, 102.0, 103.0, 104.0], MarketDirection.BULLISH),
        ([104.0, 103.0, 102.0, 101.0], MarketDirection.BEARISH),
        ([101.0, 101.0, 101.0, 101.0], MarketDirection.NEUTRAL),
        ([101.0, 102.0], MarketDirection.NEUTRAL),  # fewer than 2 * lookback candles
    ],
)
def test_infer_trend_direction(highs: list[float], expected: MarketDirection) -> None:
    candles = make_series(highs, [h - 1 for h in highs])

    assert _infer_trend_direction(candles, lookback=2) == expected


def test_infer_trend_direction_invalid_lookback_raises() -> None:
    with pytest.raises(ValueError, match="lookback"):
        _infer_trend_direction([], lookback=0)
