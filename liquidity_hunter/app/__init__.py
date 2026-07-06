"""Application layer: composition root, orchestration, and entry points.

Wires together `data`, `indicators`, `liquidity`, `psychology`, and
`scoring` for use by `dashboard` or other interfaces. Depends on all
other layers; no other layer depends on `app`.
"""

from liquidity_hunter.app.dashboard_data import DashboardData, load_dashboard_data
from liquidity_hunter.app.liquidation_backtest import (
    LiquidationBacktester,
    LiquidationBacktestResult,
)
from liquidity_hunter.app.liquidity_hunt import LiquidityHuntEngine
from liquidity_hunter.app.narrative import NarrativeEngine
from liquidity_hunter.scoring import ScoredLiquidityZone

__all__ = [
    "DashboardData",
    "LiquidationBacktester",
    "LiquidationBacktestResult",
    "LiquidityHuntEngine",
    "NarrativeEngine",
    "ScoredLiquidityZone",
    "load_dashboard_data",
]
