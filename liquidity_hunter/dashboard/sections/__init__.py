"""Dashboard sections.

Each module exposes a single `render(data: DashboardData) -> None`
function that draws one section of the dashboard with Streamlit.
"""

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

__all__ = [
    "kpi_row",
    "liquidity_targets",
    "liquidity_zones_table",
    "main_chart",
    "market_structure_panel",
    "recent_events",
    "retail_trap_panel",
    "statistics",
]
