"""Example: backtest the leverage liquidation map point-in-time.

Reconstructs liquidation levels at each past candle (no lookahead) and measures
whether price reaches and reacts at them vs a distance-matched random baseline.

Run with:

    poetry run python -m liquidity_hunter.app.examples.backtest_liquidations
"""

import logging
import math

from liquidity_hunter.app.liquidation_backtest import (
    LiquidationBacktester,
    LiquidationBacktestResult,
)
from liquidity_hunter.core.domain import TimeFrame
from liquidity_hunter.data import BinanceDataProvider, OHLCVProvider

logger = logging.getLogger(__name__)

SYMBOL = "BTCUSDT"
TIMEFRAME = TimeFrame.H1
LIMIT = 1000


def _fmt(value: float) -> str:
    return "n/a" if math.isnan(value) else f"{value:.2f}"


def _print_report(result: LiquidationBacktestResult) -> None:
    print(f"\n=== Liquidation backtest: {result.symbol} {result.timeframe.value} ===")
    print(f"eval points: {result.n_eval_points} | levels: {result.n_levels}")
    print(
        f"reach rate: {result.reach_rate:.1%} | reaction|reached: {result.reaction_rate:.1%}"
        f" | baseline: {result.baseline_reaction_rate:.1%} | LIFT: {_fmt(result.lift)}x"
    )
    btr = result.median_bars_to_reach
    print(f"median bars to reach: {btr if btr is not None else 'n/a'}")

    print("\nby leverage (reach / reaction):")
    for lev, (reach, reaction) in result.by_leverage.items():
        print(f"  {lev:>3}x  reach {reach:.1%}  reaction {reaction:.1%}")

    print("\nby distance bucket (model reaction vs baseline, lift):")
    for b in result.by_distance_bucket:
        print(
            f"  {b.label:>8}  model {b.model_reaction_rate:.1%} (n={b.model_reached})"
            f"  baseline {b.baseline_reaction_rate:.1%} (n={b.baseline_reached})"
            f"  lift {_fmt(b.lift)}x"
        )

    print("\nby intensity quartile (1=weakest .. 4=strongest):")
    for q, (n_reached, reaction) in result.by_intensity_quartile.items():
        print(f"  Q{q}  reaction {reaction:.1%}  (reached n={n_reached})")


def main(provider: OHLCVProvider | None = None) -> LiquidationBacktestResult:
    """Fetch BTCUSDT candles, run the point-in-time liquidation backtest, report."""
    provider = provider if provider is not None else BinanceDataProvider()

    candles = provider.get_ohlcv(SYMBOL, TIMEFRAME, LIMIT)
    logger.info("Fetched %d candle(s) for %s %s", len(candles), SYMBOL, TIMEFRAME.value)

    result = LiquidationBacktester().run(candles, symbol=SYMBOL, timeframe=TIMEFRAME)
    _print_report(result)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
