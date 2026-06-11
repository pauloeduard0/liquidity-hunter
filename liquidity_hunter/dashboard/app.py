"""Streamlit dashboard for liquidity-hunter.

Run with:

    poetry run streamlit run liquidity_hunter/dashboard/app.py
"""

import streamlit as st

from liquidity_hunter import app
from liquidity_hunter.app import DashboardData
from liquidity_hunter.core.domain import TimeFrame
from liquidity_hunter.dashboard import styles
from liquidity_hunter.dashboard.sections import (
    kpi_row,
    liquidity_targets,
    liquidity_zones_table,
    main_chart,
    market_structure_panel,
    recent_events,
    retail_trap_panel,
    statistics,
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
    styles.inject()

    st.title("Liquidity Hunter")
    st.caption(
        "Liquidity intelligence and retail crowd psychology research -- "
        "descriptive only, not trading advice."
    )

    data = _load(SYMBOL, TIMEFRAME.value, LIMIT)

    kpi_row.render(data)

    main_col, side_col = st.columns([3, 1])

    with main_col:
        main_chart.render(data)

    with side_col:
        with st.container(border=True):
            liquidity_targets.render(data)
        with st.container(border=True):
            retail_trap_panel.render(data)
        with st.container(border=True):
            market_structure_panel.render(data)

    zones_tab, events_tab, stats_tab = st.tabs(
        ["Detected Liquidity Zones", "Recent Events", "Statistics"]
    )
    with zones_tab:
        liquidity_zones_table.render(data)
    with events_tab:
        recent_events.render(data)
    with stats_tab:
        statistics.render(data)


main()
