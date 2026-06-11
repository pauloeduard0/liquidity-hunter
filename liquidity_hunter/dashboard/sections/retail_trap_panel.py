"""Right sidebar: retail crowd psychology and trap-risk estimate."""

import streamlit as st

from liquidity_hunter.app import DashboardData

_TRAP_RISK_THRESHOLDS: list[tuple[float, str]] = [(40.0, "Low"), (70.0, "Medium")]
_TRAP_RISK_HIGH = "High"


def _trap_risk_label(confidence: float) -> str:
    """A descriptive Low/Medium/High bucket for `confidence`, for display only."""
    for threshold, label in _TRAP_RISK_THRESHOLDS:
        if confidence < threshold:
            return label
    return _TRAP_RISK_HIGH


def render(data: DashboardData) -> None:
    """Render the retail trap analysis panel for `data`."""
    bias = data.retail_bias

    st.markdown('<p class="lh-section-title">Retail Trap Analysis</p>', unsafe_allow_html=True)

    columns = st.columns(2)
    columns[0].metric("Dominant Side", bias.dominant_side.value.upper())
    columns[1].metric("Trap Risk", _trap_risk_label(bias.confidence))

    st.progress(int(bias.confidence) / 100, text=f"Confidence: {bias.confidence:.0f}/100")
    st.caption(bias.explanation)
