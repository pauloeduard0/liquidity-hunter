"""Section 2: Retail Bias."""

import streamlit as st

from liquidity_hunter.app import DashboardData


def render(data: DashboardData) -> None:
    """Render the retail bias section for `data`."""
    st.header("2. Retail Bias")
    bias = data.retail_bias

    columns = st.columns(2)
    columns[0].metric("Dominant side", bias.dominant_side.value.upper())
    columns[1].metric("Confidence", f"{bias.confidence:.0f} / 100")
    st.write(bias.explanation)
