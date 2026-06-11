"""Section 4: Liquidity Ranking."""

import streamlit as st

from liquidity_hunter.app import DashboardData
from liquidity_hunter.dashboard.charts import ranking_chart


def render(data: DashboardData) -> None:
    """Render the liquidity ranking section for `data`."""
    st.header("4. Liquidity Ranking")

    fig = ranking_chart(data.ranked_zones)
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(
        [
            {
                "Type": scored.zone.zone_type.value.replace("_", " ").title(),
                "Score": round(scored.score, 1),
                "Distance Score": round(scored.distance_score, 1),
                "Touch Score": round(scored.touch_score, 1),
                "Timeframe Score": round(scored.timeframe_score, 1),
            }
            for scored in data.ranked_zones[:10]
        ],
        use_container_width=True,
    )
