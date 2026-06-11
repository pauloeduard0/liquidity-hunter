"""Right sidebar: top-ranked liquidity targets."""

import streamlit as st

from liquidity_hunter.app import DashboardData

_TOP_N = 5


def render(data: DashboardData) -> None:
    """Render the liquidity targets panel for `data`."""
    st.markdown('<p class="lh-section-title">Liquidity Targets</p>', unsafe_allow_html=True)

    if not data.ranked_zones:
        st.caption("No liquidity zones detected.")
        return

    rows = []
    for scored in data.ranked_zones[:_TOP_N]:
        zone = scored.zone
        price = (zone.price_high + zone.price_low) / 2
        distance_pct = (price - data.current_price) / data.current_price * 100
        rows.append(
            {
                "Price": round(price, 2),
                "Type": zone.zone_type.value.replace("_", " ").title(),
                "Score": round(scored.score, 1),
                "Distance %": round(distance_pct, 2),
            }
        )

    st.dataframe(rows, hide_index=True)
