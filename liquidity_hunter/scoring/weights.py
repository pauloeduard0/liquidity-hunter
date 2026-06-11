"""Default timeframe weights used by `LiquidityScoringEngine`.

Higher timeframes represent liquidity that is more structurally
significant and slower to be absorbed, so they receive higher weights.
Values are in `[0, 1]`; see `liquidity_hunter/docs/scoring.md`.
"""

from liquidity_hunter.core.domain import TimeFrame

DEFAULT_TIMEFRAME_WEIGHTS: dict[TimeFrame, float] = {
    TimeFrame.M1: 0.10,
    TimeFrame.M5: 0.20,
    TimeFrame.M15: 0.35,
    TimeFrame.M30: 0.50,
    TimeFrame.H1: 0.65,
    TimeFrame.H4: 0.80,
    TimeFrame.D1: 0.90,
    TimeFrame.W1: 1.00,
}
