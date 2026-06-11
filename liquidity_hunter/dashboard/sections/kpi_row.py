"""Top KPI row: price, retail bias, dominant liquidity, and trend."""

import streamlit as st

from liquidity_hunter.app import DashboardData
from liquidity_hunter.core.domain import MarketDirection

_TREND_ICONS: dict[MarketDirection, str] = {
    MarketDirection.BULLISH: "▲",
    MarketDirection.BEARISH: "▼",
    MarketDirection.NEUTRAL: "▬",
}


def render(data: DashboardData) -> None:
    """Render the top KPI row for `data`."""
    columns = st.columns(4)

    columns[0].metric(f"{data.symbol} Price", f"{data.current_price:,.2f}")

    bias = data.retail_bias
    columns[1].metric("Retail Bias", f"{bias.dominant_side.value.upper()} {bias.confidence:.0f}%")

    if data.ranked_zones:
        top_zone = data.ranked_zones[0].zone
        dominant_price = (top_zone.price_high + top_zone.price_low) / 2
        columns[2].metric("Dominant Liquidity", f"{dominant_price:,.2f}")
    else:
        columns[2].metric("Dominant Liquidity", "—")

    direction = data.higher_timeframe_direction
    columns[3].metric("Trend", f"{_TREND_ICONS[direction]} {direction.value.title()}")
