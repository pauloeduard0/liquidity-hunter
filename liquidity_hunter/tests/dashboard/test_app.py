"""Integration test for the Streamlit dashboard entrypoint."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from liquidity_hunter import app
from liquidity_hunter.app.dashboard_data import load_dashboard_data
from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.data.providers.base import OHLCVProvider
from liquidity_hunter.tests.liquidity.detectors._factories import make_series

APP_PATH = str(Path(__file__).parents[2] / "dashboard" / "app.py")

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


def test_dashboard_renders_all_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    candles = make_series(HIGHS, LOWS, symbol="BTCUSDT")
    monkeypatch.setattr(
        app,
        "load_dashboard_data",
        lambda **kwargs: load_dashboard_data(provider=_FakeProvider(candles), **kwargs),
    )

    at = AppTest.from_file(APP_PATH)
    at.run(timeout=15)

    assert not at.exception

    assert at.title[0].value == "Liquidity Hunter"

    metric_labels = [metric.label for metric in at.metric]
    assert "BTCUSDT Price" in metric_labels
    assert "Retail Bias" in metric_labels
    assert "Dominant Liquidity" in metric_labels
    assert "Trend" in metric_labels
    assert "Dominant Side" in metric_labels
    assert "Trap Risk" in metric_labels
    assert "Liquidity Zones" in metric_labels

    tab_labels = [tab.label for tab in at.tabs]
    assert tab_labels == ["Detected Liquidity Zones", "Recent Events", "Statistics"]

    assert len(at.dataframe) == 3
