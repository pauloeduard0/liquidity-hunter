"""Tests for `liquidity_hunter.dashboard.charts`."""

import plotly.graph_objects as go

from liquidity_hunter.core.domain import LiquiditySide
from liquidity_hunter.dashboard.charts import (
    candlestick_chart,
    confidence_gauge,
    liquidity_zones_chart,
    ranking_chart,
)
from liquidity_hunter.scoring import LiquidityScoringEngine
from liquidity_hunter.tests.liquidity.detectors._factories import make_series
from liquidity_hunter.tests.psychology._factories import make_zone
from liquidity_hunter.tests.scoring._factories import make_zone as make_scored_zone

HIGHS = [
    100.0, 101.0, 102.0, 110.0, 103.0, 102.0, 101.0,
    100.0, 101.0, 102.0, 110.0, 103.0, 102.0, 101.0, 100.0,
]
LOWS = [h - 5 for h in HIGHS]


def test_candlestick_chart_builds_one_candlestick_trace() -> None:
    candles = make_series(HIGHS, LOWS)

    fig = candlestick_chart(candles, title="BTCUSDT")

    assert fig.layout.title.text == "BTCUSDT"
    assert len(fig.data) == 1
    assert isinstance(fig.data[0], go.Candlestick)
    assert len(fig.data[0].close) == len(candles)


def test_liquidity_zones_chart_adds_a_shape_per_zone() -> None:
    candles = make_series(HIGHS, LOWS)
    zones = [make_zone(95.0, side=LiquiditySide.SELL_SIDE)]

    fig = liquidity_zones_chart(candles, zones)

    assert isinstance(fig.data[0], go.Candlestick)
    assert len(fig.layout.shapes) == 1


def test_ranking_chart_limits_to_top_n() -> None:
    zones = [make_scored_zone(100.0 + i, strength=0.5) for i in range(15)]
    ranked = LiquidityScoringEngine().score(zones, current_price=100.0)

    fig = ranking_chart(ranked, top_n=5)

    assert isinstance(fig.data[0], go.Bar)
    assert len(fig.data[0].x) == 5


def test_confidence_gauge_sets_value() -> None:
    fig = confidence_gauge(82.0)

    assert isinstance(fig.data[0], go.Indicator)
    assert fig.data[0].value == 82.0
