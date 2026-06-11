"""Bottom area: descriptive summary statistics."""

import streamlit as st

from liquidity_hunter.app import DashboardData
from liquidity_hunter.core.domain import LiquiditySide


def render(data: DashboardData) -> None:
    """Render summary statistics for `data`."""
    zones = data.liquidity_zones
    buy_side = sum(1 for zone in zones if zone.side is LiquiditySide.BUY_SIDE)
    sell_side = sum(1 for zone in zones if zone.side is LiquiditySide.SELL_SIDE)
    mitigated = sum(1 for zone in zones if zone.is_mitigated)
    avg_score = (
        sum(scored.score for scored in data.ranked_zones) / len(data.ranked_zones)
        if data.ranked_zones
        else 0.0
    )

    columns = st.columns(4)
    columns[0].metric("Liquidity Zones", len(zones))
    columns[1].metric("Buy-side / Sell-side", f"{buy_side} / {sell_side}")
    columns[2].metric("Mitigated Zones", mitigated)
    columns[3].metric("Avg. Liquidity Score", f"{avg_score:.1f}")
