"""Right sidebar: market structure trend summary.

This currently shows the trend for the dashboard's single loaded
timeframe. A future phase may extend this to a per-timeframe
(D1/H4/H1/M15) view once multi-timeframe data loading is available.
"""

import streamlit as st

from liquidity_hunter.app import DashboardData
from liquidity_hunter.core.domain import MarketDirection

_DIRECTION_ICONS: dict[MarketDirection, str] = {
    MarketDirection.BULLISH: "🟢",
    MarketDirection.BEARISH: "🔴",
    MarketDirection.NEUTRAL: "⚪",
}


def render(data: DashboardData) -> None:
    """Render the market structure trend panel for `data`."""
    st.markdown('<p class="lh-section-title">Market Structure</p>', unsafe_allow_html=True)

    direction = data.higher_timeframe_direction
    icon = _DIRECTION_ICONS[direction]
    st.markdown(f"**{data.timeframe.value.upper()}** &nbsp; {icon} {direction.value.title()}")

    if data.market_structure_events:
        latest = data.market_structure_events[-1]
        st.caption(
            f"Latest: {latest.event.value.replace('_', ' ').title()} "
            f"@ {latest.price_level:,.2f}"
        )
    else:
        st.caption("No structure events detected yet.")

    if data.internal_structure_events:
        latest_internal = data.internal_structure_events[-1]
        st.caption(
            f"Latest (Internal): {latest_internal.event.value.replace('_', ' ').title()} "
            f"@ {latest_internal.price_level:,.2f}"
        )
    else:
        st.caption("No internal structure events detected yet.")
