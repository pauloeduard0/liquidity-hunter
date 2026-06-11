"""Custom CSS for the dashboard's institutional dark theme.

Complements `.streamlit/config.toml` (base theme colors) with finer-grained
styling for metric cards, bordered containers, and section titles, applied
on top of Streamlit's built-in dark theme.
"""

import streamlit as st

_CUSTOM_CSS = """
<style>
.block-container {
    padding-top: 1.5rem;
    padding-bottom: 2rem;
    max-width: 1400px;
}

[data-testid="stMetric"] {
    background-color: #161A25;
    border: 1px solid #262B3D;
    border-radius: 8px;
    padding: 0.75rem 1rem;
}

[data-testid="stMetricLabel"] {
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #8A8F9C;
}

[data-testid="stMetricValue"] {
    font-size: 1.4rem;
    font-weight: 600;
}

div[data-testid="stVerticalBlockBorderWrapper"] {
    border-color: #262B3D !important;
    border-radius: 8px;
}

h1 {
    font-size: 1.6rem;
    font-weight: 700;
}

.lh-section-title {
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #8A8F9C;
    margin: 0 0 0.5rem 0;
}
</style>
"""


def inject() -> None:
    """Inject the dashboard's custom CSS into the current Streamlit app."""
    st.markdown(_CUSTOM_CSS, unsafe_allow_html=True)
