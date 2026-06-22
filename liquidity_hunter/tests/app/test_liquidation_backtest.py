"""Tests for `liquidity_hunter.app.liquidation_backtest`."""

from liquidity_hunter.app.liquidation_backtest import (
    LiquidationBacktester,
    _intensity_quartiles,
    _Outcome,
    _reach_index,
    _reacted,
)
from liquidity_hunter.core.domain import Candle, LiquiditySide
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle, make_series


def _candles(*highlows: tuple[float, float]) -> list[Candle]:
    return [make_candle(i, hi, lo) for i, (hi, lo) in enumerate(highlows)]


# --- _reach_index -----------------------------------------------------------


def test_reach_index_sell_side_hits_on_low_cross() -> None:
    candles = _candles((110, 105), (104, 99), (103, 101))
    # sell-side level at 100: first candle whose low <= 100 is index 1 (low 99).
    assert _reach_index(candles, 100.0, LiquiditySide.SELL_SIDE) == 1


def test_reach_index_buy_side_hits_on_high_cross() -> None:
    candles = _candles((101, 99), (106, 100), (103, 101))
    # buy-side level at 105: first candle whose high >= 105 is index 1 (high 106).
    assert _reach_index(candles, 105.0, LiquiditySide.BUY_SIDE) == 1


def test_reach_index_returns_none_when_never_reached() -> None:
    candles = _candles((101, 100), (102, 100.5))
    assert _reach_index(candles, 90.0, LiquiditySide.SELL_SIDE) is None


# --- _reacted ---------------------------------------------------------------


def test_reacted_sell_side_bounce() -> None:
    # Reached 100 from above; price then rallies to 102 (+2%) within window.
    candles = _candles((100, 99), (102, 100), (103, 101))
    assert _reacted(candles, 0, 100.0, LiquiditySide.SELL_SIDE, 0.01, 12) is True


def test_reacted_sell_side_no_bounce() -> None:
    # Reached 100, only crawls to 100.5 (+0.5%) -- below 1% threshold.
    candles = _candles((100, 99), (100.5, 99.5))
    assert _reacted(candles, 0, 100.0, LiquiditySide.SELL_SIDE, 0.01, 12) is False


def test_reacted_buy_side_drop() -> None:
    # Reached 100 from below; price then drops to 98 (-2%) within window.
    candles = _candles((100, 99.5), (99, 98), (99.5, 98.5))
    assert _reacted(candles, 0, 100.0, LiquiditySide.BUY_SIDE, 0.01, 12) is True


def test_reacted_respects_window() -> None:
    # Bounce only happens at index 3, outside a window of 2.
    candles = _candles((100, 99), (100.2, 99.8), (100.3, 99.9), (103, 100))
    assert _reacted(candles, 0, 100.0, LiquiditySide.SELL_SIDE, 0.01, 2) is False


# --- _intensity_quartiles ---------------------------------------------------


def test_intensity_quartiles_split_by_base_weight() -> None:
    outcomes = [
        _Outcome(
            leverage=10,
            distance=0.01,
            base_weight=float(w),
            reached=True,
            reacted=w > 4,
            bars_to_reach=1,
        )
        for w in range(1, 9)
    ]
    quartiles = _intensity_quartiles(outcomes)
    assert set(quartiles) == {1, 2, 3, 4}
    # Strongest quartile reacts more than the weakest (reacted only when w > 4).
    assert quartiles[4][1] > quartiles[1][1]


def test_intensity_quartiles_empty_when_too_few() -> None:
    outcomes = [_Outcome(10, 0.01, 1.0, True, True, 1), _Outcome(10, 0.01, 2.0, True, False, 1)]
    assert _intensity_quartiles(outcomes) == {}


# --- end-to-end run ---------------------------------------------------------


def _zigzag(num: int) -> list[Candle]:
    highs, lows = [], []
    for i in range(num):
        phase = i % 20
        wave = 15.0 * (phase / 10 if phase < 10 else (1 - (phase - 10) / 10))
        level = 100.0 + 0.5 * i + wave
        highs.append(level + 2.0)
        lows.append(level - 2.0)
    return make_series(highs, lows, symbol="BTCUSDT")


def test_run_produces_valid_populated_result() -> None:
    candles = _zigzag(400)
    result = LiquidationBacktester().run(
        candles,
        forward_horizon=20,
        reaction_window=6,
        step=10,
        min_history=120,
    )

    assert result.n_eval_points > 0
    assert result.n_levels > 0
    assert 0.0 <= result.reach_rate <= 1.0
    assert 0.0 <= result.reaction_rate <= 1.0
    assert result.by_distance_bucket  # buckets always present
    assert result.params["forward_horizon"] == 20


def test_run_is_deterministic_with_seed() -> None:
    candles = _zigzag(400)
    a = LiquidationBacktester().run(
        candles, forward_horizon=20, reaction_window=6, step=10, min_history=120, seed=7
    )
    b = LiquidationBacktester().run(
        candles, forward_horizon=20, reaction_window=6, step=10, min_history=120, seed=7
    )

    assert a.reaction_rate == b.reaction_rate
    assert a.baseline_reaction_rate == b.baseline_reaction_rate
    assert a.n_levels == b.n_levels


def test_run_too_short_history_yields_empty_result() -> None:
    candles = _zigzag(50)
    result = LiquidationBacktester().run(candles, forward_horizon=20, min_history=120)

    assert result.n_eval_points == 0
    assert result.n_levels == 0
    assert result.reach_rate == 0.0
