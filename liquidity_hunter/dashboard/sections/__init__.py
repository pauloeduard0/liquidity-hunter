"""Dashboard sections.

Each module exposes a single `render(data: DashboardData) -> None`
function that draws one section of the dashboard with Streamlit.
"""

from liquidity_hunter.dashboard.sections import (
    liquidity_ranking,
    liquidity_zones,
    market_structure,
    retail_bias,
    retail_trap_score,
)

__all__ = [
    "liquidity_ranking",
    "liquidity_zones",
    "market_structure",
    "retail_bias",
    "retail_trap_score",
]
