"""Tests for `app.tide_reclaim` and `app.tide_reclaim_journal`."""

from datetime import UTC, datetime, timedelta

from liquidity_hunter.app.paper_journal import _settle, read_journal, write_journal
from liquidity_hunter.app.tide_reclaim import (
    MIN_PULLBACK,
    TARGET_R,
    ClosedCandleProvider,
    TideReclaimSignal,
    confirmed_leg_trend,
    detect_tide_reclaim,
)
from liquidity_hunter.app.tide_reclaim_journal import build_tide_decision, tide_decision_key
from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    PaperOutcome,
    StructureEvent,
    TimeFrame,
    VWAPAnchor,
    VWAPPoint,
    VWAPSeries,
)
from liquidity_hunter.data import OHLCVProvider

START = datetime(2026, 8, 1, tzinfo=UTC)
TF = TimeFrame.D1
SYMBOL = "BTCUSDT"


def candle(i: int, close: float, low: float | None = None, high: float | None = None) -> Candle:
    low = close - 1 if low is None else low
    high = close + 1 if high is None else high
    return Candle(
        symbol=SYMBOL, timeframe=TF, timestamp=START + timedelta(days=i),
        open=close, high=high, low=low, close=close, volume=10.0, taker_buy_volume=5.0,
    )


def tide(candles: list[Candle], value: float = 100.0, anchor_days: dict[int, int] | None = None
         ) -> VWAPSeries:
    anchor_days = anchor_days or {}
    points = [
        VWAPPoint(timestamp=c.timestamp,
                  anchor_timestamp=START + timedelta(days=anchor_days.get(k, 0)), value=value)
        for k, c in enumerate(candles)
    ]
    return VWAPSeries(symbol=SYMBOL, timeframe=TF, anchor=VWAPAnchor.MONTH,
                      anchor_timestamp=START, points=points)


def pullback_then_reclaim(run: int = MIN_PULLBACK) -> list[Candle]:
    cs = [candle(0, 102.0)]
    cs += [candle(1 + k, 98.0, low=95.0 - k) for k in range(run)]
    cs.append(candle(1 + run, 101.0, low=99.0))
    return cs


def test_bullish_reclaim_after_pullback() -> None:
    cs = pullback_then_reclaim()
    sig = detect_tide_reclaim(cs, tide(cs), htf_direction=MarketDirection.BULLISH,
                              leg_trend=MarketDirection.BEARISH)
    assert sig is not None
    assert sig.direction is MarketDirection.BULLISH
    assert sig.pullback_candles == MIN_PULLBACK
    assert sig.stop_price == 95.0 - (MIN_PULLBACK - 1)  # extreme of the pullback
    assert sig.hunt_active
    assert sig.target_price == sig.entry_price + TARGET_R * sig.risk


def test_short_pullback_is_not_a_reclaim() -> None:
    cs = pullback_then_reclaim(run=MIN_PULLBACK - 1)
    assert detect_tide_reclaim(cs, tide(cs), htf_direction=MarketDirection.BULLISH,
                               leg_trend=MarketDirection.BULLISH) is None


def test_reclaim_against_htf_is_ignored() -> None:
    cs = pullback_then_reclaim()
    assert detect_tide_reclaim(cs, tide(cs), htf_direction=MarketDirection.BEARISH,
                               leg_trend=MarketDirection.BEARISH) is None
    assert detect_tide_reclaim(cs, tide(cs), htf_direction=MarketDirection.NEUTRAL,
                               leg_trend=MarketDirection.BEARISH) is None


def test_pullback_does_not_cross_the_vwap_reanchor() -> None:
    cs = pullback_then_reclaim()
    last = len(cs) - 1
    # the reclaim candle opens a new anchor period: the pullback belongs to the old one
    sig = detect_tide_reclaim(cs, tide(cs, anchor_days={last: last}),
                              htf_direction=MarketDirection.BULLISH,
                              leg_trend=MarketDirection.BULLISH)
    assert sig is None


def test_hunt_inactive_when_leg_is_aligned() -> None:
    cs = pullback_then_reclaim()
    sig = detect_tide_reclaim(cs, tide(cs), htf_direction=MarketDirection.BULLISH,
                              leg_trend=MarketDirection.BULLISH)
    assert sig is not None and not sig.hunt_active


def _event(day: int, event: StructureEvent, direction: MarketDirection,
           provisional: bool = False) -> MarketStructure:
    return MarketStructure(symbol=SYMBOL, timeframe=TF, timestamp=START + timedelta(days=day),
                           event=event, direction=direction, price_level=100.0,
                           provisional=provisional)


def test_confirmed_leg_trend_replays_failed_choch_and_skips_provisional() -> None:
    events = [
        _event(1, StructureEvent.BREAK_OF_STRUCTURE, MarketDirection.BULLISH),
        _event(2, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH),
        _event(3, StructureEvent.CHOCH_FAILED, MarketDirection.BEARISH),
        _event(4, StructureEvent.CHANGE_OF_CHARACTER, MarketDirection.BEARISH, provisional=True),
    ]
    assert confirmed_leg_trend(events) is MarketDirection.BULLISH


class _Fixed(OHLCVProvider):
    max_fetch_limit = 1000

    def __init__(self, candles: list[Candle]) -> None:
        self.candles = candles

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        return self.candles[-limit:]


def test_closed_candle_provider_drops_the_forming_candle() -> None:
    cs = [candle(k, 100.0) for k in range(3)]
    now = START + timedelta(days=2, hours=5)  # day 2 still forming
    served = ClosedCandleProvider(_Fixed(cs), clock=lambda: now).get_ohlcv(SYMBOL, TF, 10)
    assert [c.timestamp for c in served] == [cs[0].timestamp, cs[1].timestamp]


def _signal() -> TideReclaimSignal:
    return TideReclaimSignal(symbol=SYMBOL, timeframe=TF, timestamp=START,
                             direction=MarketDirection.BULLISH, entry_price=100.0,
                             stop_price=90.0, vwap_price=99.0, pullback_candles=4,
                             hunt_active=True)


def test_decision_levels_and_slippage() -> None:
    d = build_tide_decision(_signal(), observed_price=101.0)
    assert d.key == tide_decision_key(_signal())
    assert d.target_price == 130.0
    assert d.slippage_r == 0.1
    assert d.pinbar_grade == "hunt" and d.vwap_candles == 4


def test_settle_uses_the_setup_horizon(tmp_path) -> None:
    d = build_tide_decision(_signal(), observed_price=100.0)
    flat = [candle(k, 100.0) for k in range(1, 70)]
    outcome, _, bars, realized = _settle(d, flat, 60)
    assert outcome is PaperOutcome.EXPIRED and bars == 60 and realized == 0.0
    assert _settle(d, flat[:50], 60)[0] is PaperOutcome.OPEN
    path = tmp_path / "j.jsonl"
    write_journal([d], path)
    assert read_journal(path) == [d]
