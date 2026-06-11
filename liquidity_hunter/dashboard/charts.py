"""Plotly chart builders for the dashboard.

These are pure functions that build `plotly.graph_objects.Figure`
instances from domain data, with no Streamlit dependency, so they can be
unit tested and reused independently of the rendering layer. All charts
share an institutional dark theme (see `_apply_dark_theme`).
"""

import plotly.graph_objects as go

from liquidity_hunter.app import ScoredLiquidityZone
from liquidity_hunter.core.domain import (
    Candle,
    LiquidityZone,
    LiquidityZoneType,
    MarketDirection,
    MarketStructure,
    StructureEvent,
)

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

_STRUCTURE_EVENT_STYLES: dict[StructureEvent, tuple[str, str]] = {
    StructureEvent.BREAK_OF_STRUCTURE: ("BOS", "#26A69A"),
    StructureEvent.CHANGE_OF_CHARACTER: ("CHoCH", "#FFB74D"),
    StructureEvent.LIQUIDITY_SWEEP: ("Sweep", "#EF5350"),
}

_DARK_BG = "#0E1117"
_GRID_COLOR = "#1F2430"
_FONT_COLOR = "#D1D4DC"
_ACCENT_COLOR = "#2962FF"


def _apply_dark_theme(fig: go.Figure) -> go.Figure:
    """Apply the dashboard's institutional dark theme to `fig`."""
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=_DARK_BG,
        plot_bgcolor=_DARK_BG,
        font={"color": _FONT_COLOR},
        margin={"l": 50, "r": 40, "t": 40, "b": 30},
        legend={"orientation": "h", "y": -0.15},
        showlegend=False,
    )
    fig.update_xaxes(gridcolor=_GRID_COLOR, showgrid=True, zeroline=False)
    fig.update_yaxes(gridcolor=_GRID_COLOR, showgrid=True, zeroline=False)
    return fig


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
                increasing={"line": {"color": "#26A69A"}, "fillcolor": "#26A69A"},
                decreasing={"line": {"color": "#EF5350"}, "fillcolor": "#EF5350"},
            )
        ]
    )
    fig.update_layout(title=title, xaxis_rangeslider_visible=False, height=550)
    return _apply_dark_theme(fig)


def liquidity_zones_chart(
    candles: list[Candle],
    zones: list[LiquidityZone],
    *,
    ranked_zones: list[ScoredLiquidityZone] | None = None,
    title: str = "",
) -> go.Figure:
    """Build a candlestick chart with `zones` overlaid as price levels/bands.

    If `ranked_zones` is given, each zone's composite score (see
    `LiquidityScoringEngine`) is appended to its label.
    """
    fig = candlestick_chart(candles, title=title)
    scores = {scored.zone: scored.score for scored in ranked_zones or []}
    for zone in zones:
        color = _ZONE_COLORS.get(zone.zone_type, _DEFAULT_ZONE_COLOR)
        label = f"{zone.zone_type.value.replace('_', ' ').title()} ({zone.strength:.2f})"
        if zone in scores:
            label += f" · {scores[zone]:.0f}"
        if zone.price_high == zone.price_low:
            fig.add_hline(
                y=zone.price_high,
                line={"color": color, "width": 1, "dash": "dot"},
                annotation_text=label,
                annotation_position="right",
                annotation_font={"color": color, "size": 10},
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
                annotation_font={"color": color, "size": 10},
            )
    return fig


def _add_structure_events(fig: go.Figure, events: list[MarketStructure]) -> go.Figure:
    """Overlay BOS/CHoCH/liquidity-sweep markers with hover labels onto `fig`."""
    for event_type, (label, color) in _STRUCTURE_EVENT_STYLES.items():
        matching = [event for event in events if event.event is event_type]
        if not matching:
            continue
        fig.add_trace(
            go.Scatter(
                x=[event.timestamp for event in matching],
                y=[event.price_level for event in matching],
                mode="markers+text",
                marker={
                    "symbol": [
                        "triangle-up" if event.direction is MarketDirection.BULLISH
                        else "triangle-down"
                        for event in matching
                    ],
                    "size": 11,
                    "color": color,
                    "line": {"width": 1, "color": _DARK_BG},
                },
                text=[label] * len(matching),
                textposition=[
                    "top center" if event.direction is MarketDirection.BULLISH
                    else "bottom center"
                    for event in matching
                ],
                textfont={"color": color, "size": 10},
                name=label,
                hovertext=[
                    f"{label} ({event.direction.value}) @ {event.price_level:,.2f}"
                    + (
                        f", ref {event.reference_price_level:,.2f}"
                        if event.reference_price_level is not None
                        else ""
                    )
                    for event in matching
                ],
                hoverinfo="text",
            )
        )
    if any(events):
        fig.update_layout(showlegend=True)
    return fig


DEFAULT_TOP_N_ZONES = 5


def main_chart(
    candles: list[Candle],
    ranked_zones: list[ScoredLiquidityZone],
    structure_events: list[MarketStructure],
    *,
    top_n_zones: int = DEFAULT_TOP_N_ZONES,
    title: str = "",
) -> go.Figure:
    """Build the primary chart: candlesticks with the top `top_n_zones`
    liquidity zones by score (matching the "Liquidity Targets" panel) and
    market structure (BOS/CHoCH/liquidity-sweep) annotations.

    Plotting every detected zone (there can be dozens of swing points)
    makes the chart unreadable, so only the highest-ranked zones are
    overlaid here; the full list remains available in the detected
    liquidity zones table.
    """
    top = ranked_zones[:top_n_zones]
    zones = [scored.zone for scored in top]
    fig = liquidity_zones_chart(candles, zones, ranked_zones=top, title=title)
    return _add_structure_events(fig, structure_events)


def ranking_chart(ranked_zones: list[ScoredLiquidityZone], *, top_n: int = 10) -> go.Figure:
    """Build a horizontal bar chart of the top `top_n` zones by score."""
    top = ranked_zones[:top_n]
    labels = [
        f"{scored.zone.zone_type.value.replace('_', ' ').title()} "
        f"@ {(scored.zone.price_high + scored.zone.price_low) / 2:,.2f}"
        for scored in top
    ]
    scores = [scored.score for scored in top]

    fig = go.Figure(go.Bar(x=scores, y=labels, orientation="h", marker_color=_ACCENT_COLOR))
    fig.update_layout(
        title="Liquidity Zone Ranking",
        xaxis_title="Score (0-100)",
        xaxis_range=[0, 100],
    )
    fig.update_yaxes(autorange="reversed")
    return _apply_dark_theme(fig)


def confidence_gauge(confidence: float, *, title: str = "Retail Trap Score") -> go.Figure:
    """Build a 0-100 gauge chart for `confidence`."""
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=confidence,
            title={"text": title, "font": {"size": 13}},
            number={"font": {"size": 24}},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": _ACCENT_COLOR},
                "bgcolor": _DARK_BG,
                "borderwidth": 0,
                "steps": [
                    {"range": [0, 40], "color": "#1F2430"},
                    {"range": [40, 70], "color": "#2A2E39"},
                    {"range": [70, 100], "color": "#3A3F4D"},
                ],
            },
        )
    )
    fig = _apply_dark_theme(fig)
    fig.update_layout(height=180, margin={"l": 20, "r": 20, "t": 30, "b": 10})
    return fig
