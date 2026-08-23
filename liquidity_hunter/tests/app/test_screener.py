"""Tests for `app.screener`."""

from datetime import UTC, datetime, timedelta

from liquidity_hunter.app.block_reclaim import detect_block_reclaims
from liquidity_hunter.app.screener import (
    ScanUnit,
    armed_entries,
    build_screen,
    load_screen,
)
from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    POIZone,
    POIZoneKind,
    POIZoneStatus,
    ScreenerStatus,
    TimeFrame,
    VWAPAnchor,
    VWAPPoint,
    VWAPSeries,
)
from liquidity_hunter.data.exceptions import DataProviderRequestError
from liquidity_hunter.data.providers.base import OHLCVProvider

START = datetime(2026, 8, 1, tzinfo=UTC)
SYMBOL = "BTCUSDT"
TF = TimeFrame.M15


def candle(i, *, open_, high, low, close, volume=100.0):
    return Candle(
        symbol=SYMBOL, timeframe=TF, timestamp=START + timedelta(minutes=15 * i),
        open=open_, high=high, low=low, close=close,
        volume=volume, taker_buy_volume=volume / 2,
    )


def flat(i, price):
    return candle(i, open_=price, high=price + 1, low=price - 1, close=price)


def vwap_series(candles, value):
    return VWAPSeries(
        symbol=SYMBOL, timeframe=TF, anchor=VWAPAnchor.SESSION,
        anchor_timestamp=candles[0].timestamp, label="Session",
        band_multipliers=(), estimated=True,
        points=[
            VWAPPoint(
                timestamp=c.timestamp,
                anchor_timestamp=candles[0].timestamp,
                value=value,
            )
            for c in candles
        ],
    )


def block(created_index, low, high):
    return POIZone(
        symbol=SYMBOL, timeframe=TF, direction=MarketDirection.BULLISH,
        kind=POIZoneKind.ORDER_BLOCK, price_low=low, price_high=high,
        created_at=START + timedelta(minutes=15 * created_index),
        ob_candle_timestamp=START + timedelta(minutes=15 * created_index),
        status=POIZoneStatus.ACTIVE,
    )


def reclaimed_case():
    """The test fixture from `test_block_reclaim`: test, then a pinbar reclaim."""
    candles = [flat(i, 108.0) for i in range(20)]
    candles += [
        candle(20, open_=104, high=104.5, low=100.5, close=101.5),
        candle(21, open_=101.5, high=103, low=100.2, close=102.5),
    ]
    candles += [flat(i, 104.0) for i in range(22, 25)]
    candles.append(candle(25, open_=105.4, high=105.9, low=103.0, close=105.6))
    candles += [flat(i, 106.0) for i in range(26, 30)]
    return candles, [block(0, 100.0, 102.0)], vwap_series(candles, 105.0)


def armed_case():
    """The same test, but the reclaim candle never prints: armed, not fired."""
    candles = [flat(i, 108.0) for i in range(20)]
    candles += [
        candle(20, open_=104, high=104.5, low=100.5, close=101.5),
        candle(21, open_=101.5, high=103, low=100.2, close=102.5),
    ]
    candles += [flat(i, 104.0) for i in range(22, 30)]
    return candles, [block(0, 100.0, 102.0)], vwap_series(candles, 105.0)


def test_a_pending_visit_is_armed() -> None:
    candles, zones, vwap = armed_case()
    entries = armed_entries(candles, zones, vwap, [], symbol=SYMBOL, timeframe=TF)
    assert len(entries) == 1
    e = entries[0]
    assert e.status is ScreenerStatus.ARMED
    assert e.direction is MarketDirection.BULLISH
    assert e.timestamp == candles[20].timestamp
    assert e.r_atr is None and e.reclaim is None


def test_a_visit_that_already_reclaimed_is_not_armed() -> None:
    candles, zones, vwap = reclaimed_case()
    reclaims = detect_block_reclaims(candles, zones, vwap, symbol=SYMBOL, timeframe=TF)
    assert reclaims  # sanity: the fixture fires
    assert armed_entries(
        candles, zones, vwap, reclaims, symbol=SYMBOL, timeframe=TF
    ) == []


def test_an_expired_wait_window_is_not_armed() -> None:
    candles, zones, vwap = armed_case()
    # push the visit far into the past: 30 more quiet candles
    n = len(candles)
    candles = candles + [flat(i, 106.0) for i in range(n, n + 30)]
    vwap = vwap_series(candles, 105.0)
    assert armed_entries(candles, zones, vwap, [], symbol=SYMBOL, timeframe=TF) == []


def test_build_screen_reports_fired_within_lookback_and_sorts() -> None:
    candles, zones, vwap = reclaimed_case()
    reclaims = detect_block_reclaims(candles, zones, vwap, symbol=SYMBOL, timeframe=TF)
    armed = armed_entries(candles, zones, vwap, reclaims, symbol=SYMBOL, timeframe=TF)
    unit = ScanUnit(
        symbol=SYMBOL, timeframe=TF, candles=candles, reclaims=reclaims, armed=armed
    )
    screen = build_screen(
        [unit], timeframes=[TF], symbols_scanned=1, fired_within=12
    )
    assert [e.status for e in screen.entries] == [ScreenerStatus.FIRED]
    fired = screen.entries[0]
    assert fired.reclaim is not None
    assert fired.candles_ago == len(candles) - 1 - 25
    # a tighter lookback drops it
    tight = build_screen([unit], timeframes=[TF], symbols_scanned=1, fired_within=2)
    assert tight.entries == []


class OneGoodOneBadProvider(OHLCVProvider):
    """BTC returns the armed fixture; everything else raises."""

    max_fetch_limit = 1000

    def get_ohlcv(self, symbol, timeframe, limit=1000):
        if symbol != SYMBOL:
            raise DataProviderRequestError(f"no contract for {symbol}")
        return armed_case()[0]


def test_load_screen_reports_failures_without_dying() -> None:
    screen = load_screen(
        provider=OneGoodOneBadProvider(),
        symbols=[SYMBOL, "NOPEUSDT"],
        timeframes=[TF],
    )
    assert screen.symbols_scanned == 2
    assert screen.symbols_failed == ["NOPEUSDT"]
    # BTC scanned fine (whether or not POIDetector finds a zone in the fixture)
    assert all(e.symbol == SYMBOL for e in screen.entries)
