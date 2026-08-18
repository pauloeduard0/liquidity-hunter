"""Tests for `liquidity_hunter.psychology.analyzers.supertrend_break`."""

from datetime import datetime

import pytest
from liquidity_hunter.core.domain import (
    Candle,
    MarketControlPoint,
    MarketControlSide,
    MarketControlState,
    MarketDirection,
    MarketStructure,
    OIRegime,
    StructureEvent,
    StructureScope,
    SupertrendBreakQuality,
    SupertrendPoint,
    TimeFrame,
    VolumeSpreadSignal,
    VSAPattern,
)
from liquidity_hunter.psychology import SupertrendBreakAnalyzer
from liquidity_hunter.tests.liquidity.detectors._factories import make_candle


def _candles(closes: list[float]) -> list[Candle]:
    return [
        make_candle(i, high=close + 1.0, low=close - 1.0, close=close)
        for i, close in enumerate(closes)
    ]


def _point(
    index: int, value: float, direction: MarketDirection, flip: bool = False
) -> SupertrendPoint:
    return SupertrendPoint(
        timestamp=make_candle(index, high=2.0, low=1.0).timestamp,
        value=value,
        direction=direction,
        flip=flip,
        upper_band=value + 1.0,
        lower_band=value - 1.0,
    )


def _flip_series(
    flip_index: int, level: float, direction: MarketDirection, length: int
) -> list[SupertrendPoint]:
    """A Supertrend series whose only flip is at `flip_index`, breaking `level`."""
    before = (
        MarketDirection.BEARISH
        if direction is MarketDirection.BULLISH
        else MarketDirection.BULLISH
    )
    return [
        _point(
            i,
            level,
            before if i < flip_index else direction,
            flip=i == flip_index,
        )
        for i in range(length)
    ]


def _regime_for(controller: MarketControlSide) -> OIRegime:
    """The OI-rising quadrant a credited controller implies (FLAT if none)."""
    if controller is MarketControlSide.BUYERS:
        return OIRegime.LONG_BUILDUP
    if controller is MarketControlSide.SELLERS:
        return OIRegime.SHORT_BUILDUP
    return OIRegime.FLAT


def _control(
    controller: MarketControlSide, timestamps: list[datetime]
) -> MarketControlState:
    return MarketControlState(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=timestamps[-1],
        controller=controller,
        regime=OIRegime.LONG_BUILDUP,
        cvd_change=1.0,
        cvd_change_ratio=0.5,
        oi_change_pct=0.01,
        conviction=50.0,
        control_score=50.0,
        fade_warning=True,
        window_candles=5,
        description="",
        series=[
            MarketControlPoint(
                timestamp=ts,
                control_score=50.0,
                controller=controller,
                regime=_regime_for(controller),
            )
            for ts in timestamps
        ],
    )


def _bos(index: int, direction: MarketDirection) -> MarketStructure:
    candle = make_candle(index, high=2.0, low=1.0)
    return MarketStructure(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=candle.timestamp,
        event=StructureEvent.BREAK_OF_STRUCTURE,
        direction=direction,
        price_level=100.0,
        scope=StructureScope.INTERNAL,
    )


def test_no_flip_yields_no_breaks() -> None:
    candles = _candles([100.0] * 10)
    points = [_point(i, 95.0, MarketDirection.BULLISH) for i in range(10)]

    assert SupertrendBreakAnalyzer().analyze(candles=candles, points=points) == []


def test_break_given_back_without_new_money_is_a_stop_run() -> None:
    # Breaks up through 100 at index 5, then closes back below three bars later.
    closes = [95.0] * 5 + [105.0, 104.0, 103.0, 99.0] + [98.0] * 3
    candles = _candles(closes)
    points = _flip_series(5, 100.0, MarketDirection.BULLISH, len(closes))
    control = _control(MarketControlSide.SELLERS, [c.timestamp for c in candles])

    breaks = SupertrendBreakAnalyzer().analyze(
        candles=candles, points=points, market_control=control
    )

    assert len(breaks) == 1
    assert breaks[0].quality is SupertrendBreakQuality.STOP_RUN
    assert breaks[0].reclaim_candles == 3
    assert breaks[0].reclaim_timestamp == candles[8].timestamp
    assert "reclaim:3c" in breaks[0].evidence
    assert "no-new-money" in breaks[0].evidence


def test_new_money_plus_structure_is_a_genuine_break() -> None:
    closes = [95.0] * 5 + [105.0, 108.0, 110.0, 112.0, 115.0, 118.0, 120.0]
    candles = _candles(closes)
    points = _flip_series(5, 100.0, MarketDirection.BULLISH, len(closes))
    control = _control(MarketControlSide.BUYERS, [c.timestamp for c in candles])

    breaks = SupertrendBreakAnalyzer().analyze(
        candles=candles,
        points=points,
        structure_events=[_bos(7, MarketDirection.BULLISH)],
        market_control=control,
    )

    assert breaks[0].quality is SupertrendBreakQuality.GENUINE
    assert breaks[0].structure_confirmed is True
    assert breaks[0].evidence == ["new-money", "bos"]


def test_reclaim_backed_by_new_money_is_not_accused() -> None:
    # Price came back inside, but fresh money took the flip's side and there is
    # no exhaustion fingerprint — not enough to call it a stop run.
    closes = [95.0] * 5 + [105.0, 104.0, 99.0] + [98.0] * 4
    candles = _candles(closes)
    points = _flip_series(5, 100.0, MarketDirection.BULLISH, len(closes))
    control = _control(MarketControlSide.BUYERS, [c.timestamp for c in candles])

    breaks = SupertrendBreakAnalyzer().analyze(
        candles=candles, points=points, market_control=control
    )

    assert breaks[0].quality is SupertrendBreakQuality.UNKNOWN
    assert breaks[0].reclaim_candles == 2


def test_reclaim_outside_the_window_is_not_a_reclaim() -> None:
    closes = [95.0] * 5 + [105.0, 105.0, 105.0, 105.0, 105.0, 105.0, 90.0]
    candles = _candles(closes)
    points = _flip_series(5, 100.0, MarketDirection.BULLISH, len(closes))
    control = _control(MarketControlSide.SELLERS, [c.timestamp for c in candles])

    breaks = SupertrendBreakAnalyzer().analyze(
        candles=candles, points=points, market_control=control
    )

    assert breaks[0].reclaim_timestamp is None
    assert breaks[0].quality is SupertrendBreakQuality.UNKNOWN


def test_downward_flip_reclaims_upward() -> None:
    closes = [105.0] * 5 + [95.0, 96.0, 101.0] + [102.0] * 4
    candles = _candles(closes)
    points = _flip_series(5, 100.0, MarketDirection.BEARISH, len(closes))
    control = _control(MarketControlSide.BUYERS, [c.timestamp for c in candles])

    breaks = SupertrendBreakAnalyzer().analyze(
        candles=candles, points=points, market_control=control
    )

    assert breaks[0].direction is MarketDirection.BEARISH
    assert breaks[0].quality is SupertrendBreakQuality.STOP_RUN
    assert breaks[0].reclaim_candles == 2


def test_without_market_control_the_break_is_unqualified() -> None:
    closes = [95.0] * 5 + [105.0, 104.0, 99.0] + [98.0] * 4
    candles = _candles(closes)
    points = _flip_series(5, 100.0, MarketDirection.BULLISH, len(closes))

    breaks = SupertrendBreakAnalyzer().analyze(candles=candles, points=points)

    # No credited controller is the same as "no new money" for the stop-run
    # gate, so the reclaim still names it — the flip is simply unbacked.
    assert breaks[0].controller is None
    assert breaks[0].quality is SupertrendBreakQuality.STOP_RUN


def test_wick_back_inside_is_not_a_reclaim() -> None:
    closes = [95.0] * 5 + [105.0, 104.0, 103.0, 102.0, 101.5, 101.0, 101.0]
    candles = _candles(closes)  # lows reach 100 but no candle closes below it
    points = _flip_series(5, 100.0, MarketDirection.BULLISH, len(closes))
    control = _control(MarketControlSide.SELLERS, [c.timestamp for c in candles])

    breaks = SupertrendBreakAnalyzer().analyze(
        candles=candles, points=points, market_control=control
    )

    assert breaks[0].reclaim_timestamp is None


def test_opposite_direction_bos_does_not_confirm() -> None:
    closes = [95.0] * 5 + [105.0] * 7
    candles = _candles(closes)
    points = _flip_series(5, 100.0, MarketDirection.BULLISH, len(closes))
    control = _control(MarketControlSide.BUYERS, [c.timestamp for c in candles])

    breaks = SupertrendBreakAnalyzer().analyze(
        candles=candles,
        points=points,
        structure_events=[_bos(7, MarketDirection.BEARISH)],
        market_control=control,
    )

    assert breaks[0].structure_confirmed is False
    assert breaks[0].quality is SupertrendBreakQuality.UNKNOWN


def test_non_positive_windows_are_rejected() -> None:
    with pytest.raises(ValueError):
        SupertrendBreakAnalyzer(reclaim_candles=0)


def test_exhaustion_signature_names_a_stop_run_despite_new_money() -> None:
    # Fresh money took the flip's side, but the break printed a buying climax
    # and price came back inside: the positive exhaustion fingerprint is enough.
    closes = [95.0] * 5 + [105.0, 104.0, 99.0] + [98.0] * 4
    candles = _candles(closes)
    points = _flip_series(5, 100.0, MarketDirection.BULLISH, len(closes))
    control = _control(MarketControlSide.BUYERS, [c.timestamp for c in candles])
    climax = VolumeSpreadSignal(
        symbol="BTCUSDT",
        timeframe=TimeFrame.H1,
        timestamp=candles[5].timestamp,
        pattern=VSAPattern.BUYING_CLIMAX,
        direction=MarketDirection.BEARISH,
        price_level=105.0,
        spread_ratio=2.5,
        close_position=0.9,
        volume_ratio=3.0,
        volume_delta=10.0,
        confidence=70.0,
        description="",
    )

    breaks = SupertrendBreakAnalyzer().analyze(
        candles=candles,
        points=points,
        market_control=control,
        volume_spread_signals=[climax],
    )

    assert breaks[0].quality is SupertrendBreakQuality.STOP_RUN
    assert "vsa-climax" in breaks[0].evidence


def test_break_that_never_left_the_band_is_not_a_stop_run() -> None:
    # Price pokes barely past 100 and returns: the indicator's own whipsaw, not
    # a trap — nobody was drawn into a break that went nowhere.
    closes = [99.0] * 5 + [100.4, 99.6] + [99.0] * 5
    candles = _candles(closes)
    points = _flip_series(5, 100.0, MarketDirection.BULLISH, len(closes))
    control = _control(MarketControlSide.SELLERS, [c.timestamp for c in candles])

    breaks = SupertrendBreakAnalyzer().analyze(
        candles=candles, points=points, market_control=control
    )

    assert breaks[0].reclaim_timestamp is None
    assert breaks[0].quality is SupertrendBreakQuality.UNKNOWN
    # The same give-back counts once the excursion gate is lifted.
    ungated = SupertrendBreakAnalyzer(min_excursion_atr=0.0).analyze(
        candles=candles, points=points, market_control=control
    )
    assert ungated[0].quality is SupertrendBreakQuality.STOP_RUN
