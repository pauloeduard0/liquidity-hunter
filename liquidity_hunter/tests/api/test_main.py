"""Integration tests for the FastAPI application."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from liquidity_hunter.api.cache import TTLCache
from liquidity_hunter.api.main import app
from liquidity_hunter.api.routes import dashboard
from liquidity_hunter.app.dashboard_data import DashboardData, load_dashboard_data
from liquidity_hunter.core.domain import Candle, TimeFrame
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


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    candles = make_series(HIGHS, LOWS, symbol="BTCUSDT")
    monkeypatch.setattr(
        dashboard,
        "load_dashboard_data",
        lambda **kwargs: load_dashboard_data(provider=_FakeProvider(candles), **kwargs),
    )
    monkeypatch.setattr(dashboard, "_cache", TTLCache[DashboardData]())
    with TestClient(app) as test_client:
        yield test_client


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_dashboard_returns_snapshot(client: TestClient) -> None:
    response = client.get("/api/dashboard", params={"symbol": "BTCUSDT", "timeframe": "1h"})

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "BTCUSDT"
    assert body["timeframe"] == "1h"
    assert len(body["candles"]) == len(HIGHS)
    assert "ranked_zones" in body
    assert "market_structure_events" in body
    assert body["retail_bias"]["symbol"] == "BTCUSDT"


def test_dashboard_uses_default_query_params(client: TestClient) -> None:
    response = client.get("/api/dashboard")

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "BTCUSDT"
    assert body["timeframe"] == "1h"


def test_dashboard_rejects_invalid_timeframe(client: TestClient) -> None:
    response = client.get("/api/dashboard", params={"timeframe": "not-a-timeframe"})

    assert response.status_code == 422


def test_dashboard_rejects_non_positive_limit(client: TestClient) -> None:
    response = client.get("/api/dashboard", params={"limit": 0})

    assert response.status_code == 422


def test_dashboard_rejects_non_positive_swing_lookback(client: TestClient) -> None:
    response = client.get("/api/dashboard", params={"swing_lookback": 0})

    assert response.status_code == 422
