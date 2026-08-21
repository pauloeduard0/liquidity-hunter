"""Tests for `liquidity_hunter.psychology.analyzers.market_control`."""

import pytest

from liquidity_hunter.core.domain import (
    Candle,
    MarketControlSide,
    OIRegime,
    OpenInterestPoint,
)
from liquidity_hunter.psychology import MarketControlAnalyzer
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle

WINDOW = 5


def _candles(taker_buy_volumes: list[float]) -> list[Candle]:
    """Series with a fixed volume of 1.0; delta = 2*tbv - 1 per candle."""
    return [
        make_candle(i, 101.0, 99.0, close=100.0, taker_buy_volume=tbv, volume=1.0)
        for i, tbv in enumerate(taker_buy_volumes)
    ]


def _oi(candles: list[Candle], values: list[float]) -> list[OpenInterestPoint]:
    return [
        OpenInterestPoint(symbol="BTCUSDT", timestamp=c.timestamp, open_interest=v)
        for c, v in zip(candles, values, strict=True)
    ]


def _analyzer() -> MarketControlAnalyzer:
    return MarketControlAnalyzer(window_size=WINDOW)


# ------------------------------------------------------------------
# The CVD-aggression x OI matrix
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tbv", "oi_values", "regime", "controller"),
    [
        # buy aggression (tbv 0.8 -> delta +0.6) + OI up: new longs, buyers control
        ([0.8] * 5, [1000, 1005, 1010, 1015, 1020], OIRegime.LONG_BUILDUP, MarketControlSide.BUYERS),
        # sell aggression (tbv 0.2 -> delta -0.6) + OI up: new shorts, sellers control
        ([0.2] * 5, [1000, 1005, 1010, 1015, 1020], OIRegime.SHORT_BUILDUP, MarketControlSide.SELLERS),
        # buy aggression + OI down: shorts covering, no conviction-backed control
        ([0.8] * 5, [1020, 1015, 1010, 1005, 1000], OIRegime.SHORT_COVERING, MarketControlSide.BALANCED),
        # sell aggression + OI down: longs liquidating, no control
        ([0.2] * 5, [1020, 1015, 1010, 1005, 1000], OIRegime.LONG_LIQUIDATION, MarketControlSide.BALANCED),
    ],
)
def test_control_matrix(
    tbv: list[float], oi_values: list[float], regime: OIRegime, controller: MarketControlSide
) -> None:
    candles = _candles(tbv)
    state = _analyzer().analyze(candles, _oi(candles, oi_values))

    assert state is not None
    assert state.regime is regime
    assert state.controller is controller
    assert state.window_candles == WINDOW


def test_buyers_control_has_positive_score() -> None:
    candles = _candles([0.85] * 5)
    state = _analyzer().analyze(candles, _oi(candles, [1000, 1010, 1020, 1030, 1040]))

    assert state is not None
    assert state.controller is MarketControlSide.BUYERS
    assert state.control_score > 0  # sign = aggressor side (buyers)
    assert state.conviction == pytest.approx(abs(state.control_score))


def test_sellers_control_has_negative_score() -> None:
    candles = _candles([0.15] * 5)
    state = _analyzer().analyze(candles, _oi(candles, [1000, 1010, 1020, 1030, 1040]))

    assert state is not None
    assert state.controller is MarketControlSide.SELLERS
    assert state.control_score < 0


def test_oi_confirmed_control_beats_diverging_conviction() -> None:
    """Same aggression scores higher when OI confirms than when it diverges."""
    candles = _candles([0.8] * 5)
    confirmed = _analyzer().analyze(candles, _oi(candles, [1000, 1010, 1020, 1030, 1040]))
    diverging = _analyzer().analyze(candles, _oi(candles, [1040, 1030, 1020, 1010, 1000]))

    assert confirmed is not None and diverging is not None
    assert confirmed.control_score > diverging.control_score
    # Covering is not conviction-backed control: no side is credited.
    assert diverging.controller is MarketControlSide.BALANCED


def test_below_floor_is_flat_balanced_but_score_stays_continuous() -> None:
    # tbv 0.52 -> delta +0.04 -> ratio 0.04 < 0.06 floor: no side credited, but
    # the oscillator still reflects the (weak) aggression rather than zeroing.
    candles = _candles([0.52] * 5)
    state = _analyzer().analyze(candles, _oi(candles, [1000, 1010, 1020, 1030, 1040]))

    assert state is not None
    assert state.regime is OIRegime.FLAT
    assert state.controller is MarketControlSide.BALANCED
    assert 0.0 < state.control_score < 100.0  # continuous, not a dead zone


def test_zero_aggression_scores_zero() -> None:
    # tbv 0.5 -> delta 0 -> ratio 0: genuinely no aggression, bar collapses.
    candles = _candles([0.5] * 5)
    state = _analyzer().analyze(candles, _oi(candles, [1000, 1010, 1020, 1030, 1040]))

    assert state is not None
    assert state.control_score == pytest.approx(0.0)


def test_series_has_one_point_per_covered_candle() -> None:
    candles = _candles([0.8] * 8)
    oi = _oi(candles, [1000 + 10 * i for i in range(8)])
    state = _analyzer().analyze(candles, oi)

    assert state is not None
    # window=5 over 8 candles -> readings at end indices 4..7 = 4 points.
    assert len(state.series) == 4
    assert state.series[-1].timestamp == candles[-1].timestamp
    assert state.series[-1].control_score == pytest.approx(state.control_score)
    assert all(p.controller is MarketControlSide.BUYERS for p in state.series)


def test_none_without_oi_coverage() -> None:
    candles = _candles([0.8] * 5)
    assert _analyzer().analyze(candles, []) is None


def test_none_when_series_shorter_than_window() -> None:
    candles = _candles([0.8] * 3)
    assert _analyzer().analyze(candles, _oi(candles, [1000, 1010, 1020])) is None


# ------------------------------------------------------------------
# OI sample alignment (candle close, not candle open)
# ------------------------------------------------------------------


def test_liquidation_flush_reads_as_covering_not_new_money() -> None:
    """A flush lands in the sample *after* the candle; it must still be seen.

    Buy aggression throughout, OI rising all window except the last candle,
    whose own displacement (a collapse) is only published one period later.
    Measured against the candle's *open* the window reads fresh money; against
    its *close* it reads what it is — shorts covering. Regression for the
    BTCUSDT M15 2026-08-20 08:00 UTC inversion.
    """
    candles = _candles([0.8] * WINDOW)
    oi_values = [1000.0, 1005.0, 1010.0, 1015.0, 1020.0]
    points = _oi(candles, oi_values)
    # The sample one period past the last candle: OI collapses.
    step = candles[-1].timestamp - candles[-2].timestamp
    points.append(
        OpenInterestPoint(
            symbol="BTCUSDT", timestamp=candles[-1].timestamp + step, open_interest=900.0
        )
    )

    state = _analyzer().analyze(candles, points)

    assert state is not None
    assert state.regime is OIRegime.SHORT_COVERING
    assert state.controller is MarketControlSide.BALANCED
    assert state.oi_change_pct < 0


def test_oi_gap_beyond_one_period_falls_back_to_at_or_before() -> None:
    """A sample far past the window is not reached across (coverage gap)."""
    candles = _candles([0.8] * WINDOW)
    points = _oi(candles, [1000.0, 1005.0, 1010.0, 1015.0, 1020.0])
    step = candles[-1].timestamp - candles[-2].timestamp
    points.append(
        OpenInterestPoint(
            symbol="BTCUSDT", timestamp=candles[-1].timestamp + 5 * step, open_interest=900.0
        )
    )

    state = _analyzer().analyze(candles, points)

    assert state is not None
    assert state.regime is OIRegime.LONG_BUILDUP
