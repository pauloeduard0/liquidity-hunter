"""Section 3: Detected Liquidity Zones."""

import streamlit as st

from liquidity_hunter.app import DashboardData
from liquidity_hunter.dashboard.charts import liquidity_zones_chart


def render(data: DashboardData) -> None:
    """Render the detected liquidity zones section for `data`."""
    st.header("3. Detected Liquidity Zones")

    fig = liquidity_zones_chart(
        data.candles, data.liquidity_zones, title=f"{data.symbol} ({data.timeframe.value})"
    )
    st.plotly_chart(fig, use_container_width=True)

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
        use_container_width=True,
    )
