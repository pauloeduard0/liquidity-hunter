"""Section 1: Market Structure."""

import streamlit as st

from liquidity_hunter.app import DashboardData
from liquidity_hunter.dashboard.charts import candlestick_chart


def render(data: DashboardData) -> None:
    """Render the market structure section for `data`."""
    st.header("1. Market Structure")
    st.caption(
        "Swing (major) structure: break of structure (BOS) and change of "
        "character (CHoCH) events detected from swing highs/lows. The "
        "trend below is the direction of the most recent confirmed event."
    )
    st.metric("Higher timeframe trend", data.higher_timeframe_direction.value.title())

    fig = candlestick_chart(data.candles, title=f"{data.symbol} ({data.timeframe.value})")
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(
        [
            {
                "Event": event.event.value.replace("_", " ").title(),
                "Direction": event.direction.value.title(),
                "Price Level": event.price_level,
                "Reference Level": event.reference_price_level,
                "Timestamp": event.timestamp,
            }
            for event in reversed(data.market_structure_events)
        ],
        use_container_width=True,
    )
