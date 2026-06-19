"""Main area: candlestick chart with liquidity zones and structure events."""

import streamlit as st

from liquidity_hunter.app import DashboardData
from liquidity_hunter.core.domain import LiquidityZoneType
from liquidity_hunter.dashboard.charts import main_chart

_SWEPT_ZONE_TYPES = {LiquidityZoneType.EQUAL_HIGHS, LiquidityZoneType.EQUAL_LOWS}
_MAX_SWEPT_ZONES = 20
_SWEPT_TTL_CANDLES = 200


def render(data: DashboardData) -> None:
    """Render the primary price chart for `data`."""
    ttl_cutoff = (
        data.candles[-_SWEPT_TTL_CANDLES].timestamp
        if len(data.candles) >= _SWEPT_TTL_CANDLES
        else data.candles[0].timestamp
    )
    mitigated = sorted(
        [
            z
            for z in data.liquidity_zones
            if z.is_mitigated
            and z.zone_type in _SWEPT_ZONE_TYPES
            and z.invalidated_at is not None
            and z.invalidated_at >= ttl_cutoff
        ],
        key=lambda z: z.invalidated_at or z.formed_at,
        reverse=True,
    )[:_MAX_SWEPT_ZONES]

    fig = main_chart(
        data.candles,
        data.ranked_zones,
        [*data.market_structure_events, *data.internal_structure_events],
        poi_zones=data.poi_zones,
        mitigated_zones=mitigated,
        title=f"{data.symbol} · {data.timeframe.value.upper()}",
    )
    st.plotly_chart(fig, theme=None)
