"""Integration tests for the FastAPI application."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from liquidity_hunter.api.cache import TTLCache
from liquidity_hunter.api.main import app
from liquidity_hunter.api.routes import dashboard
from liquidity_hunter.api.routes import overview as overview_route
from liquidity_hunter.app.dashboard_data import DashboardData, load_dashboard_data
from liquidity_hunter.app.overview import (
    OVERVIEW_TIMEFRAMES,
    TimeframeStructureSnapshot,
    load_timeframe_structure,
)
from liquidity_hunter.core.domain import (
    Candle,
    FundingRate,
    LongShortRatio,
    OpenInterestPoint,
    TimeFrame,
)
from liquidity_hunter.data.providers.base import FuturesDataProvider, OHLCVProvider
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


class _FakeFuturesProvider(FuturesDataProvider):
    """Empty futures state, so the estimator runs without network access."""

    def get_open_interest_history(
        self, symbol: str, timeframe: TimeFrame, limit: int = 500
    ) -> list[OpenInterestPoint]:
        return []

    def get_funding_rate_history(self, symbol: str, limit: int = 500) -> list[FundingRate]:
        return []

    def get_long_short_ratio(
        self, symbol: str, timeframe: TimeFrame, limit: int = 500
    ) -> list[LongShortRatio]:
        return []


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    candles = make_series(HIGHS, LOWS, symbol="BTCUSDT")
    monkeypatch.setattr(
        dashboard,
        "load_dashboard_data",
        lambda **kwargs: load_dashboard_data(
            provider=_FakeProvider(candles),
            futures_provider=_FakeFuturesProvider(),
            **kwargs,
        ),
    )
    monkeypatch.setattr(dashboard, "_cache", TTLCache[DashboardData]())
    monkeypatch.setattr(
        overview_route,
        "load_timeframe_structure",
        lambda **kwargs: load_timeframe_structure(provider=_FakeProvider(candles), **kwargs),
    )
    monkeypatch.setattr(
        overview_route, "_snapshot_cache", TTLCache[TimeframeStructureSnapshot]()
    )
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
    assert "internal_structure_events" in body
    assert body["retail_bias"]["symbol"] == "BTCUSDT"
    assert "liquidation_map" in body
    liquidation_map = body["liquidation_map"]
    assert liquidation_map is not None
    assert liquidation_map["dominant_leveraged_side"] == "neutral"
    assert isinstance(liquidation_map["bands"], list)
    assert isinstance(body["consolidation_ranges"], list)
    # Narrative/anomaly synthesis is off by default (the multi-timeframe
    # overview panel took over its sidebar slot); see the opt-in test below.
    assert "narrative" in body
    assert body["narrative"] is None


def test_dashboard_narrative_is_opt_in(client: TestClient) -> None:
    response = client.get(
        "/api/dashboard",
        params={"symbol": "BTCUSDT", "timeframe": "1h", "narrative": "true"},
    )

    assert response.status_code == 200
    narrative = response.json()["narrative"]
    assert narrative is not None
    assert narrative["symbol"] == "BTCUSDT"
    assert narrative["timeframe"] == "1h"
    assert isinstance(narrative["summary"], str)
    assert isinstance(narrative["timeline"], list)
    assert isinstance(narrative["anomalies"], list)
    assert narrative["confluence_count"] >= 0
    assert narrative["confluence_total"] >= 0


def test_overview_returns_ladder(client: TestClient) -> None:
    response = client.get("/api/overview", params={"symbol": "BTCUSDT"})

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "BTCUSDT"
    assert [entry["timeframe"] for entry in body["entries"]] == [
        tf.value for tf in OVERVIEW_TIMEFRAMES
    ]
    for entry in body["entries"]:
        assert entry["trend"] in ("bullish", "bearish", "neutral")
        assert entry["hunt_phase"] in (
            "none",
            "counter_trend",
            "hunt_in_progress",
            "captured",
        )
        assert entry["current_price"] > 0
        assert isinstance(entry["in_consolidation"], bool)


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


def test_dashboard_accepts_limit_up_to_1200(client: TestClient) -> None:
    assert client.get("/api/dashboard", params={"limit": 1200}).status_code == 200


def test_dashboard_rejects_limit_above_1200(client: TestClient) -> None:
    # 1200 + the 300-candle buffer is the futures klines per-request max (1500).
    assert client.get("/api/dashboard", params={"limit": 1201}).status_code == 422


def test_dashboard_rejects_non_positive_swing_lookback(client: TestClient) -> None:
    response = client.get("/api/dashboard", params={"swing_lookback": 0})

    assert response.status_code == 422


def test_dashboard_ignores_unknown_internal_swing_lookback(client: TestClient) -> None:
    response = client.get("/api/dashboard", params={"internal_swing_lookback": 0})

    assert response.status_code == 200
