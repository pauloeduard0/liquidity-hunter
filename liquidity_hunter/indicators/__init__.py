"""Indicators layer: derived numerical series computed from `Candle` data.

Houses reusable, stateless computations (e.g. volatility, ranges,
volume profiles) consumed by `liquidity`, `psychology`, and `scoring`.
Depends only on `core` and `data`.
"""
