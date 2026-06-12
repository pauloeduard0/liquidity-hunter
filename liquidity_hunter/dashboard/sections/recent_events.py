"""Bottom area: recent market structure events."""

import streamlit as st

from liquidity_hunter.app import DashboardData


def render(data: DashboardData) -> None:
    """Render the recent market structure events table for `data`."""
    events = sorted(
        [*data.market_structure_events, *data.internal_structure_events],
        key=lambda event: event.timestamp,
        reverse=True,
    )
    st.dataframe(
        [
            {
                "Event": event.event.value.replace("_", " ").title(),
                "Scope": event.scope.value.title(),
                "Direction": event.direction.value.title(),
                "Price Level": event.price_level,
                "Reference Level": event.reference_price_level,
                "Timestamp": event.timestamp,
            }
            for event in events
        ],
        hide_index=True,
    )
