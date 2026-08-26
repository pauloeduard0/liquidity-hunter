"""Tests for `app.paper_journal`."""

from datetime import UTC, datetime, timedelta

from liquidity_hunter.app.paper_journal import (
    MAX_BLOCK_PENETRATION,
    _settle,
    build_decision,
    decision_key,
    passes_gates,
    read_journal,
    write_journal,
)
from liquidity_hunter.core.domain import (
    BlockReclaim,
    BlockReclaimScanEntry,
    Candle,
    MarketDirection,
    PaperOutcome,
    ScreenerStatus,
    TimeFrame,
)

START = datetime(2026, 8, 1, tzinfo=UTC)
SYMBOL = "BTCUSDT"
TF = TimeFrame.M15


def reclaim(*, vwap_candles: int = 30, provisional: bool = False) -> BlockReclaim:
    return BlockReclaim(
        symbol=SYMBOL, timeframe=TF, timestamp=START,
        direction=MarketDirection.BULLISH,
        reclaim_price=100.0, vwap_price=99.0,
        block_price_low=90.0, block_price_high=92.0,
        block_timestamp=START - timedelta(hours=2),
        test_start_timestamp=START - timedelta(minutes=30),
        first_test=True, test_extreme=95.0, reclaim_distance=5.0,
        r_atr=0.8, provisional=provisional, trigger_line="vwap",
        pinbar_grade="legacy", vwap_candles=vwap_candles,
    )


def entry(*, r_atr: float = 0.8, vwap_candles: int = 30,
          status: ScreenerStatus = ScreenerStatus.FIRED,
          provisional: bool = False, price: float = 100.2,
          candles_ago: int = 1) -> BlockReclaimScanEntry:
    return BlockReclaimScanEntry(
        symbol=SYMBOL, timeframe=TF, status=status,
        direction=MarketDirection.BULLISH, timestamp=START, candles_ago=candles_ago,
        current_price=price, block_price_low=90.0, block_price_high=92.0,
        r_atr=r_atr,
        reclaim=reclaim(vwap_candles=vwap_candles, provisional=provisional),
    )


def candle(i: int, high: float, low: float, close: float) -> Candle:
    return Candle(
        symbol=SYMBOL, timeframe=TF,
        timestamp=START + timedelta(minutes=15 * i),
        open=(high + low) / 2, high=high, low=low, close=close,
        volume=10.0, taker_buy_volume=5.0,
    )


def test_gates_follow_the_operating_plan() -> None:
    assert passes_gates(entry()) is True
    # M15 carries the accumulation floor: it exists to drop the re-anchor
    # candle, where the VWAP jumps across price on the clock rather than on
    # price moving. Its 8-14 band is the best of all, so the floor sits at the
    # bottom of the walk-forward plateau, not past its edge.
    assert passes_gates(entry(vwap_candles=3)) is False
    assert passes_gates(entry(vwap_candles=5)) is True
    # outside the r_atr gate
    assert passes_gates(entry(r_atr=1.4)) is False
    # a forming trigger candle settles nothing
    assert passes_gates(entry(provisional=True)) is False
    # armed rows are not decisions
    assert passes_gates(entry(status=ScreenerStatus.ARMED)) is False


def test_a_test_that_pierced_the_block_is_not_a_decision() -> None:
    # The detector retires a block only on a *close* beyond it (the POIZone
    # rule). A wick that crosses the whole block on the trigger's own visit
    # leaves it on the board with nothing left holding: price went in one side
    # and out the other.
    e = entry()
    low = e.reclaim.block_price_low
    grazed = e.reclaim.model_copy(update={"test_extreme": 91.5})  # 25% deep
    through = e.reclaim.model_copy(update={"test_extreme": low - 0.01})
    assert passes_gates(e.model_copy(update={"reclaim": grazed})) is True
    assert passes_gates(e.model_copy(update={"reclaim": through})) is False


def test_a_test_that_dove_deep_into_the_block_is_not_a_decision() -> None:
    # Measured on M15 after the piercing rule was already wired: a test turned
    # back near the block's edge says resting orders are there in size; one
    # that works most of the way through says the opposite, and whether a
    # candle happened to *close* out the far side is then incidental. The
    # threshold sits on a plateau (every cut from 0.30 to 1.00 beats the
    # ungated series), so this asserts the shape of the rule, not the number.
    e = entry()  # block spans 90.0 -> 92.0, tested from above
    for extreme, taken in ((91.9, True), (91.0, True), (90.9, False), (90.1, False)):
        row = e.model_copy(
            update={"reclaim": e.reclaim.model_copy(update={"test_extreme": extreme})}
        )
        assert passes_gates(row) is taken, extreme


def test_the_depth_gate_is_only_wired_where_it_was_measured() -> None:
    # H4 has its own, much thinner core, and this rule was never measured
    # there. An unmeasured gate applied to it would be the move this project
    # keeps refusing -- so a timeframe absent from the dict is not gated.
    assert TimeFrame.M15 in MAX_BLOCK_PENETRATION
    assert TimeFrame.H4 not in MAX_BLOCK_PENETRATION
    deep = reclaim().model_copy(
        update={"timeframe": TimeFrame.H4, "test_extreme": 90.1}
    )
    h4 = entry().model_copy(update={"timeframe": TimeFrame.H4, "reclaim": deep})
    assert passes_gates(h4) is True


def test_a_stale_row_is_not_a_decision() -> None:
    # the screener lists recent fires; acting on one from hours ago is
    # chasing, and pricing it against the tape now measures the chase
    assert passes_gates(entry(candles_ago=6)) is False


def test_h4_only_drops_the_re_anchor_candle() -> None:
    # H4 anchors weekly, so a floor in candles bites far harder there (15
    # candles is 2.5 days of a 42-candle week) and every high floor measured
    # as pure loss. Only the re-anchor candle itself is dropped.
    e = entry(vwap_candles=2).model_copy(update={"timeframe": TimeFrame.H4})
    assert passes_gates(e) is True
    fresh = entry(vwap_candles=1).model_copy(update={"timeframe": TimeFrame.H4})
    assert passes_gates(fresh) is False


def test_decision_records_the_gap_between_close_and_tape() -> None:
    d = build_decision(entry(price=100.2), observed_price=100.2)
    assert d.signal_close == 100.0
    assert d.observed_price == 100.2
    # R is 5.0, so paying 0.2 above the close is 0.04R against the trade
    assert abs(d.slippage_r - 0.04) < 1e-9
    assert d.stop_price == 95.0
    assert d.target_price == 110.0  # 2R from the signal close
    assert d.outcome is PaperOutcome.OPEN


def test_slippage_is_signed_against_the_trade() -> None:
    # filled better than the close: the gap is negative (a gift, not a cost)
    d = build_decision(entry(price=99.9), observed_price=99.9)
    assert d.slippage_r < 0


def test_settle_credits_the_stop_when_a_candle_spans_both() -> None:
    d = build_decision(entry(), observed_price=100.0)
    spanning = [candle(1, high=111.0, low=94.0, close=100.0)]
    outcome, _at, bars, realized = _settle(d, spanning)
    assert outcome is PaperOutcome.STOP
    assert bars == 1
    assert realized is not None and realized < 0


def test_settle_reports_target_and_realized_r_from_the_observed_price() -> None:
    # entered 0.2 worse than the close, so a 2R target realizes under 2R
    d = build_decision(entry(price=100.2), observed_price=100.2)
    outcome, _at, bars, realized = _settle(d, [candle(1, 110.5, 100.0, 110.2)])
    assert outcome is PaperOutcome.TARGET
    assert bars == 1
    assert realized is not None and 1.9 < realized < 2.0


def test_settle_expires_past_the_horizon() -> None:
    d = build_decision(entry(), observed_price=100.0)
    quiet = [candle(i, 101.0, 99.0, 100.0) for i in range(1, 45)]
    outcome, _at, bars, _r = _settle(d, quiet)
    assert outcome is PaperOutcome.EXPIRED
    assert bars == 40


def test_journal_round_trips(tmp_path) -> None:
    path = tmp_path / "journal.jsonl"
    d = build_decision(entry(), observed_price=100.1)
    write_journal([d], path)
    back = read_journal(path)
    assert len(back) == 1
    assert back[0].key == decision_key(entry())
    assert back[0].observed_price == 100.1
    assert read_journal(tmp_path / "missing.jsonl") == []
