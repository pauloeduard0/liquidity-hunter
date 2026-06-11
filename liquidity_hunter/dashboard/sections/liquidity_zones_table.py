"""Bottom area: detected liquidity zones table."""

import streamlit as st

from liquidity_hunter.app import DashboardData


def render(data: DashboardData) -> None:
    """Render the detected liquidity zones table for `data`."""
    st.dataframe(
        [
            {
                "Type": zone.zone_type.value.replace("_", " ").title(),
                "Side": zone.side.value.replace("_", " ").title(),
                "Price High": zone.price_high,
                "Price Low": zone.price_low,
                "Strength": round(zone.strength, 2),
                "Mitigated": zone.is_mitigated,
            }
            for zone in data.liquidity_zones
        ],
        hide_index=True,
    )
