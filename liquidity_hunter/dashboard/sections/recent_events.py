"""Bottom area: recent market structure events."""

import streamlit as st

from liquidity_hunter.app import DashboardData


def render(data: DashboardData) -> None:
    """Render the recent market structure events table for `data`."""
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
        hide_index=True,
    )
