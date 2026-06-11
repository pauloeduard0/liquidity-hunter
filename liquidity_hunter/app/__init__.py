"""Application layer: composition root, orchestration, and entry points.

Wires together `data`, `indicators`, `liquidity`, `psychology`, and
`scoring` for use by `dashboard` or other interfaces. Depends on all
other layers; no other layer depends on `app`.
"""
