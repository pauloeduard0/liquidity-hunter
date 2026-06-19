"""Plotly chart builders for the dashboard.

These are pure functions that build `plotly.graph_objects.Figure`
instances from domain data, with no Streamlit dependency, so they can be
unit tested and reused independently of the rendering layer. All charts
share an institutional dark theme (see `_apply_dark_theme`).
"""

from datetime import datetime

import plotly.graph_objects as go

from liquidity_hunter.app import ScoredLiquidityZone
from liquidity_hunter.core.domain import (
    Candle,
    LiquidityZone,
    LiquidityZoneType,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    StructureScope,
)
from liquidity_hunter.core.domain.poi_zone import POIZone

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

_ZONE_TYPE_LABELS: dict[LiquidityZoneType, str] = {
    LiquidityZoneType.EQUAL_HIGHS: "EQH",
    LiquidityZoneType.EQUAL_LOWS: "EQL",
    LiquidityZoneType.SWING_HIGH: "SH",
    LiquidityZoneType.SWING_LOW: "SL",
    LiquidityZoneType.ORDER_BLOCK: "OB",
    LiquidityZoneType.FAIR_VALUE_GAP: "FVG",
    LiquidityZoneType.LIQUIDITY_POOL: "LP",
}

_STRUCTURE_EVENT_STYLES: dict[StructureEvent, tuple[str, str]] = {
    StructureEvent.BREAK_OF_STRUCTURE: ("BOS", "#26A69A"),
    StructureEvent.CHANGE_OF_CHARACTER: ("CHoCH", "#FFB74D"),
    StructureEvent.LIQUIDITY_SWEEP: ("Sweep", "#EF5350"),
}

_DIRECTION_ICONS: dict[MarketDirection, str] = {
    MarketDirection.BULLISH: "▲",
    MarketDirection.BEARISH: "▼",
    MarketDirection.NEUTRAL: "▬",
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
    mitigated_zones: list[LiquidityZone] | None = None,
    title: str = "",
) -> go.Figure:
    """Build a candlestick chart with `zones` overlaid as price levels/bands.

    If `ranked_zones` is given, each zone's composite score (see
    `LiquidityScoringEngine`) is appended to its label. `mitigated_zones`
    are rendered at lower opacity with a dotted border.
    """
    fig = candlestick_chart(candles, title=title)
    scores = {scored.zone: scored.score for scored in ranked_zones or []}
    for zone in zones:
        color = _ZONE_COLORS.get(zone.zone_type, _DEFAULT_ZONE_COLOR)
        zone_label = _ZONE_TYPE_LABELS.get(zone.zone_type, zone.zone_type.value)
        label = f"{zone_label} ({zone.strength:.2f})"
        if zone in scores:
            label += f" · {scores[zone]:.0f}"
        if zone.price_high == zone.price_low:
            fig.add_hline(y=zone.price_high, line={"color": color, "width": 1, "dash": "dot"})
        else:
            fig.add_hrect(
                y0=zone.price_low,
                y1=zone.price_high,
                line_width=0,
                fillcolor=color,
                opacity=0.2,
            )
        fig.add_annotation(
            x=zone.formed_at,
            y=zone.price_high,
            text=label,
            showarrow=False,
            xanchor="left",
            yanchor="bottom",
            font={"color": color, "size": 10},
        )

    for zone in mitigated_zones or []:
        color = _ZONE_COLORS.get(zone.zone_type, _DEFAULT_ZONE_COLOR)
        zone_label = _ZONE_TYPE_LABELS.get(zone.zone_type, zone.zone_type.value)
        end_time = zone.invalidated_at or candles[-1].timestamp
        if zone.price_high == zone.price_low:
            fig.add_shape(
                type="line",
                x0=zone.formed_at,
                x1=end_time,
                y0=zone.price_high,
                y1=zone.price_high,
                line={"color": color, "width": 1, "dash": "dot"},
                opacity=0.3,
            )
        else:
            fig.add_shape(
                type="rect",
                x0=zone.formed_at,
                x1=end_time,
                y0=zone.price_low,
                y1=zone.price_high,
                line={"color": color, "width": 1, "dash": "dot"},
                fillcolor=color,
                opacity=0.08,
            )
        fig.add_annotation(
            x=zone.formed_at,
            y=zone.price_high,
            text=f"{zone_label} (swept)",
            showarrow=False,
            xanchor="left",
            yanchor="bottom",
            font={"color": color, "size": 10},
            opacity=0.4,
        )

    return fig


def _is_duplicate_of_major(event: MarketStructure, major_events: list[MarketStructure]) -> bool:
    """Whether `event` reports the same pivot as one already in `major_events`.

    The internal-scope detector can re-detect the same swing pivot as the
    major-scope detector (a major extreme is, by construction, also a local
    extreme at a smaller lookback); such duplicates are skipped to avoid
    rendering the same marker twice.
    """
    return any(
        major.timestamp == event.timestamp
        and major.event is event.event
        and major.price_level == event.price_level
        for major in major_events
    )


def _structure_line_end_time(
    event: MarketStructure, events: list[MarketStructure], last_candle_time: datetime
) -> datetime:
    """Where `event`'s line should stop.

    A BOS/CHoCH/Sweep marks the active level on its `direction` side as of
    `event.timestamp`. Only a *later* BOS or CHoCH of the same scope and
    direction moves that active level (a Sweep, by definition, leaves it
    unchanged), so the line is bounded there - otherwise it extends to
    `last_candle_time` as the current active level.
    """
    superseded_at = [
        other.timestamp
        for other in events
        if other.scope is event.scope
        and other.direction is event.direction
        and other.event in (StructureEvent.BREAK_OF_STRUCTURE, StructureEvent.CHANGE_OF_CHARACTER)
        and other.timestamp > event.timestamp
    ]
    return min(superseded_at) if superseded_at else last_candle_time


def _add_structure_events(
    fig: go.Figure, candles: list[Candle], events: list[MarketStructure]
) -> go.Figure:
    """Overlay BOS/CHoCH/liquidity-sweep levels as horizontal line segments on `fig`.

    Each major-scope event is drawn as a dashed line at its `price_level`,
    annotated with its type abbreviation and price (matching the liquidity
    zone lines in `liquidity_zones_chart`). Internal-scope events of the
    same type are drawn as dotted, lower-opacity lines annotated with
    " (Internal)". Internal events that duplicate a major-scope event (same
    timestamp, event type, and price level) are skipped. HH/HL/LH/LL pivot
    events are not rendered on this chart.

    Each line spans from the event's timestamp to where its level was
    superseded (see `_structure_line_end_time`), so a historical level that
    has since been overtaken doesn't visually extend across the most recent
    price action as if it were still the active reference. The label is
    anchored at the line's start (`event.timestamp`, where the structure
    actually occurred) rather than its end, so it doesn't drift to the right
    edge of the chart for levels that are still active.

    For `CHANGE_OF_CHARACTER`, the line/label is drawn at
    `reference_price_level` (the validated level that was broken) rather
    than `price_level` (the confirming pivot's own extreme, which can be far
    beyond the level it confirmed) -- so the marker sits on the structural
    level that flipped, not on the wick that happened to confirm it. BOS and
    Sweep keep using `price_level`, where it coincides with the breaking
    level.
    """
    major_events = [event for event in events if event.scope is StructureScope.MAJOR]
    last_candle_time = candles[-1].timestamp

    for event in events:
        style = _STRUCTURE_EVENT_STYLES.get(event.event)
        if style is None:
            continue
        is_internal = event.scope is StructureScope.INTERNAL
        if is_internal and _is_duplicate_of_major(event, major_events):
            continue

        label, color = style
        if is_internal:
            label = f"{label} (Internal)"
        icon = _DIRECTION_ICONS[event.direction]
        end_time = _structure_line_end_time(event, events, last_candle_time)
        line_price = (
            event.reference_price_level
            if event.event is StructureEvent.CHANGE_OF_CHARACTER
            and event.reference_price_level is not None
            else event.price_level
        )
        fig.add_shape(
            type="line",
            x0=event.timestamp,
            x1=end_time,
            y0=line_price,
            y1=line_price,
            line={"color": color, "width": 1, "dash": "dot" if is_internal else "dash"},
            opacity=0.5 if is_internal else 1.0,
        )
        fig.add_annotation(
            x=event.timestamp,
            y=line_price,
            text=f"{label} {icon} · {line_price:,.2f}",
            showarrow=False,
            xanchor="left",
            yanchor="bottom",
            font={"color": color, "size": 10},
        )

    return fig


_POI_COLORS: dict[str, str] = {
    "bullish": "#26A69A",  # demand zone — teal
    "bearish": "#EF5350",  # supply zone — red
    "mitigated": "#888888",
}

DEFAULT_TOP_N_ZONES = 5


def _add_poi_zones(fig: go.Figure, candles: list[Candle], poi_zones: list[POIZone]) -> go.Figure:
    """Overlay POI order block zones as filled rectangles on `fig`.

    Each ACTIVE zone is drawn as a semi-transparent filled box spanning
    from `created_at` to the last candle. MITIGATED zones are drawn dimmer
    and end at their `mitigated_at` timestamp. INVALIDATED zones are hidden.
    """
    last_candle_time = candles[-1].timestamp

    for zone in poi_zones:
        if zone.status.value == "invalidated":
            continue

        is_mitigated = zone.status.value == "mitigated"
        color = (
            _POI_COLORS["mitigated"]
            if is_mitigated
            else _POI_COLORS.get(zone.direction.value, "#888888")
        )
        opacity = 0.12 if is_mitigated else 0.25
        end_time = zone.mitigated_at if (is_mitigated and zone.mitigated_at) else last_candle_time

        fig.add_shape(
            type="rect",
            x0=zone.created_at,
            x1=end_time,
            y0=zone.price_low,
            y1=zone.price_high,
            line={"color": color, "width": 1, "dash": "dot" if is_mitigated else "solid"},
            fillcolor=color,
            opacity=opacity,
        )
        direction_icon = "▲" if zone.direction.value == "bullish" else "▼"
        status_suffix = " ✓" if is_mitigated else ""
        fig.add_annotation(
            x=zone.created_at,
            y=zone.price_high,
            text=f"OB {direction_icon}{status_suffix} · {zone.price_low:,.2f}–{zone.price_high:,.2f}",  # noqa: E501
            showarrow=False,
            xanchor="left",
            yanchor="bottom",
            font={"color": color, "size": 10},
        )

    return fig


def main_chart(
    candles: list[Candle],
    ranked_zones: list[ScoredLiquidityZone],
    structure_events: list[MarketStructure],
    *,
    poi_zones: list[POIZone] | None = None,
    mitigated_zones: list[LiquidityZone] | None = None,
    top_n_zones: int = DEFAULT_TOP_N_ZONES,
    title: str = "",
) -> go.Figure:
    """Build the primary chart: candlesticks with the top `top_n_zones`
    liquidity zones by score (matching the "Liquidity Targets" panel),
    market structure annotations, and POI order block zones. Swept zones
    are rendered at lower opacity.
    """
    top = ranked_zones[:top_n_zones]
    zones = [scored.zone for scored in top]
    fig = liquidity_zones_chart(
        candles, zones, ranked_zones=top, mitigated_zones=mitigated_zones, title=title
    )
    fig = _add_structure_events(fig, candles, structure_events)
    if poi_zones:
        fig = _add_poi_zones(fig, candles, poi_zones)
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
