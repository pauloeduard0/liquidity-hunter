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
    at.run()

    assert not at.exception

    headers = [header.value for header in at.header]
    assert headers == [
        "1. Market Structure",
        "2. Retail Bias",
        "3. Detected Liquidity Zones",
        "4. Liquidity Ranking",
        "5. Retail Trap Score",
    ]
