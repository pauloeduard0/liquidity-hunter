"""Section 1: Market Structure."""

import streamlit as st

from liquidity_hunter.app import DashboardData
from liquidity_hunter.dashboard.charts import candlestick_chart


def render(data: DashboardData) -> None:
    """Render the market structure section for `data`."""
    st.header("1. Market Structure")
    st.caption(
        "Structural event detection (break of structure, change of "
        "character, HH/HL/LH/LL) is not yet implemented. The trend below "
        "is a simple descriptive comparison of recent average closes, "
        "used as the higher timeframe context for retail bias."
    )
    st.metric("Higher timeframe trend", data.higher_timeframe_direction.value.title())

    fig = candlestick_chart(data.candles, title=f"{data.symbol} ({data.timeframe.value})")
    st.plotly_chart(fig, use_container_width=True)
