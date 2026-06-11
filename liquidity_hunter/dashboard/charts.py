"""Plotly chart builders for the dashboard.

These are pure functions that build `plotly.graph_objects.Figure`
instances from domain data, with no Streamlit dependency, so they can be
unit tested and reused independently of the rendering layer.
"""

import plotly.graph_objects as go

from liquidity_hunter.app import ScoredLiquidityZone
from liquidity_hunter.core.domain import Candle, LiquidityZone, LiquidityZoneType

_ZONE_COLORS: dict[LiquidityZoneType, str] = {
    LiquidityZoneType.EQUAL_HIGHS: "#EF553B",
    LiquidityZoneType.EQUAL_LOWS: "#636EFA",
    LiquidityZoneType.SWING_HIGH: "#FFA15A",
    LiquidityZoneType.SWING_LOW: "#19D3F3",
    LiquidityZoneType.ORDER_BLOCK: "#AB63FA",
    LiquidityZoneType.FAIR_VALUE_GAP: "#00CC96",
    LiquidityZoneType.LIQUIDITY_POOL: "#B6E880",
}
_DEFAULT_ZONE_COLOR = "#888888"


def candlestick_chart(candles: list[Candle], *, title: str = "") -> go.Figure:
    """Build a candlestick chart from `candles`."""
    fig = go.Figure(
        data=[
            go.Candlestick(
                x=[candle.timestamp for candle in candles],
                open=[candle.open for candle in candles],
                high=[candle.high for candle in candles],
                low=[candle.low for candle in candles],
                close=[candle.close for candle in candles],
                name="Price",
            )
        ]
    )
    fig.update_layout(title=title, xaxis_rangeslider_visible=False)
    return fig


def liquidity_zones_chart(
    candles: list[Candle], zones: list[LiquidityZone], *, title: str = ""
) -> go.Figure:
    """Build a candlestick chart with `zones` overlaid as price levels/bands."""
    fig = candlestick_chart(candles, title=title)
    for zone in zones:
        color = _ZONE_COLORS.get(zone.zone_type, _DEFAULT_ZONE_COLOR)
        label = f"{zone.zone_type.value.replace('_', ' ')} ({zone.strength:.2f})"
        if zone.price_high == zone.price_low:
            fig.add_hline(
                y=zone.price_high,
                line={"color": color, "width": 1, "dash": "dot"},
                annotation_text=label,
                annotation_position="right",
            )
        else:
            fig.add_hrect(
                y0=zone.price_low,
                y1=zone.price_high,
                line_width=0,
                fillcolor=color,
                opacity=0.2,
                annotation_text=label,
                annotation_position="right",
            )
    return fig


def ranking_chart(ranked_zones: list[ScoredLiquidityZone], *, top_n: int = 10) -> go.Figure:
    """Build a horizontal bar chart of the top `top_n` zones by score."""
    top = ranked_zones[:top_n]
    labels = [
        f"{scored.zone.zone_type.value.replace('_', ' ').title()} "
        f"@ {(scored.zone.price_high + scored.zone.price_low) / 2:,.2f}"
        for scored in top
    ]
    scores = [scored.score for scored in top]

    fig = go.Figure(go.Bar(x=scores, y=labels, orientation="h"))
    fig.update_layout(
        title="Liquidity Zone Ranking",
        xaxis_title="Score (0-100)",
        xaxis_range=[0, 100],
    )
    fig.update_yaxes(autorange="reversed")
    return fig


def confidence_gauge(confidence: float, *, title: str = "Retail Trap Score") -> go.Figure:
    """Build a 0-100 gauge chart for `confidence`."""
    return go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=confidence,
            title={"text": title},
            gauge={"axis": {"range": [0, 100]}},
        )
    )
