"""Section 5: Retail Trap Score."""

import streamlit as st

from liquidity_hunter.app import DashboardData
from liquidity_hunter.dashboard.charts import confidence_gauge


def render(data: DashboardData) -> None:
    """Render the retail trap score section for `data`."""
    st.header("5. Retail Trap Score")
    st.caption(
        "Estimates how strongly the current setup is likely to draw "
        "retail attention and conviction -- a crowd-psychology "
        "description, not a trading signal."
    )

    fig = confidence_gauge(data.retail_bias.confidence)
    st.plotly_chart(fig, use_container_width=True)
