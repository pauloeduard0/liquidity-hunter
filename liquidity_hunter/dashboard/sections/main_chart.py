"""Main area: candlestick chart with liquidity zones and structure events."""

import streamlit as st

from liquidity_hunter.app import DashboardData
from liquidity_hunter.dashboard.charts import main_chart


def render(data: DashboardData) -> None:
    """Render the primary price chart for `data`."""
    fig = main_chart(
        data.candles,
        data.ranked_zones,
        data.market_structure_events,
        title=f"{data.symbol} · {data.timeframe.value.upper()}",
    )
    st.plotly_chart(fig, theme=None)
