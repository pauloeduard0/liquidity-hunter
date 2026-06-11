"""Streamlit dashboard for liquidity-hunter.

Run with:

    poetry run streamlit run liquidity_hunter/dashboard/app.py
"""

import streamlit as st

from liquidity_hunter import app
from liquidity_hunter.app import DashboardData
from liquidity_hunter.core.domain import TimeFrame
from liquidity_hunter.dashboard.sections import (
    liquidity_ranking,
    liquidity_zones,
    market_structure,
    retail_bias,
    retail_trap_score,
)

SYMBOL = "BTCUSDT"
TIMEFRAME = TimeFrame.H1
LIMIT = 500


@st.cache_data(ttl=300)
def _load(symbol: str, timeframe: str, limit: int) -> DashboardData:
    return app.load_dashboard_data(symbol=symbol, timeframe=TimeFrame(timeframe), limit=limit)


def main() -> None:
    """Render the liquidity-hunter research dashboard."""
    st.set_page_config(page_title="Liquidity Hunter", layout="wide")
    st.title("Liquidity Hunter")
    st.caption(
        "Research dashboard for market liquidity and retail crowd "
        "psychology. Descriptive only -- not trading advice."
    )

    data = _load(SYMBOL, TIMEFRAME.value, LIMIT)

    market_structure.render(data)
    retail_bias.render(data)
    liquidity_zones.render(data)
    liquidity_ranking.render(data)
    retail_trap_score.render(data)


main()
