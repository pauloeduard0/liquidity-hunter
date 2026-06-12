"""Tests for `liquidity_hunter.dashboard.charts`."""

import plotly.graph_objects as go

from liquidity_hunter.core.domain import (
    LiquiditySide,
    MarketDirection,
    StructureEvent,
    StructureScope,
)
from liquidity_hunter.dashboard.charts import (
    DEFAULT_TOP_N_ZONES,
    candlestick_chart,
    confidence_gauge,
    liquidity_zones_chart,
    main_chart,
    ranking_chart,
)
from liquidity_hunter.scoring import LiquidityScoringEngine
from liquidity_hunter.tests.liquidity.detectors._factories import make_series
from liquidity_hunter.tests.psychology._factories import make_structure_event, make_zone
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


def test_main_chart_limits_zones_to_top_n() -> None:
    candles = make_series(HIGHS, LOWS)
    zones = [make_scored_zone(100.0 + i, strength=0.5) for i in range(15)]
    ranked = LiquidityScoringEngine().score(zones, current_price=100.0)
    events = [
        make_structure_event(
            StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH, price_level=110.0
        ),
        make_structure_event(
            StructureEvent.LIQUIDITY_SWEEP, MarketDirection.BEARISH, price_level=95.0
        ),
    ]

    fig = main_chart(candles, ranked, events)

    assert isinstance(fig.data[0], go.Candlestick)
    # DEFAULT_TOP_N_ZONES zone lines, plus one hline per structure event.
    assert len(fig.layout.shapes) == DEFAULT_TOP_N_ZONES + len(events)
    annotation_texts = [annotation.text for annotation in fig.layout.annotations]
    assert any(text.startswith("BOS ▲ · 110.00") for text in annotation_texts)
    assert any(text.startswith("Sweep ▼ · 95.00") for text in annotation_texts)


def test_main_chart_adds_internal_scope_lines() -> None:
    candles = make_series(HIGHS, LOWS)
    zones = [make_scored_zone(100.0 + i, strength=0.5) for i in range(15)]
    ranked = LiquidityScoringEngine().score(zones, current_price=100.0)
    events = [
        make_structure_event(
            StructureEvent.BREAK_OF_STRUCTURE,
            MarketDirection.BULLISH,
            price_level=110.0,
            scope=StructureScope.MAJOR,
        ),
        make_structure_event(
            StructureEvent.BREAK_OF_STRUCTURE,
            MarketDirection.BULLISH,
            price_level=108.0,
            scope=StructureScope.INTERNAL,
        ),
    ]

    fig = main_chart(candles, ranked, events)

    annotation_texts = [annotation.text for annotation in fig.layout.annotations]
    assert any(text.startswith("BOS ▲ · 110.00") for text in annotation_texts)
    assert any(text.startswith("BOS (Internal) ▲ · 108.00") for text in annotation_texts)


def test_main_chart_skips_internal_event_duplicating_major() -> None:
    candles = make_series(HIGHS, LOWS)
    zones = [make_scored_zone(100.0 + i, strength=0.5) for i in range(15)]
    ranked = LiquidityScoringEngine().score(zones, current_price=100.0)
    major = make_structure_event(
        StructureEvent.BREAK_OF_STRUCTURE,
        MarketDirection.BULLISH,
        price_level=110.0,
        scope=StructureScope.MAJOR,
    )
    duplicate_internal = major.model_copy(update={"scope": StructureScope.INTERNAL})
    events = [major, duplicate_internal]

    fig = main_chart(candles, ranked, events)

    # Only one hline is added for the deduplicated pair.
    assert len(fig.layout.shapes) == DEFAULT_TOP_N_ZONES + 1
    annotation_texts = [annotation.text for annotation in fig.layout.annotations]
    assert not any("(Internal)" in text for text in annotation_texts)
