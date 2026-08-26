"""Paper journal: record what the rule decided live, and what the tape gave.

Step 1 of taking the block-reclaim setup live, and the only step this project
takes: **the journal decides and records; it never sends an order.** Order
execution and position management remain out of scope -- what is in scope is
the measurement the study could not make, because it reads history where a
candle's close is a price you can act on. Live it is not: the close has
happened, and the first price available is the next one. The journal writes
both, so the difference becomes a measured number in R instead of an
assumption.

Two passes, both idempotent, meant to be run on a schedule (a cron every few
minutes is enough -- an M15 decision cannot appear more than once per candle):

- :func:`record_decisions` -- read the screener, keep the rows that pass the
  operating gates, and append any not already journalled.
- :func:`resolve_open` -- re-read the candles behind each open decision and
  settle it: target, stop, or expired past the study's horizon.

Storage is a JSONL file, one decision per line, rewritten in place on
resolution. A flat file rather than a database because the whole point is
that this is auditable by hand: the run that produces a number should be
readable in a text editor months later.
"""

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from liquidity_hunter.app.screener import (
    SCREEN_SYMBOLS,
    SCREEN_TIMEFRAMES,
    load_screen,
)
from liquidity_hunter.core.domain import (
    BlockReclaim,
    BlockReclaimScanEntry,
    Candle,
    MarketDirection,
    PaperDecision,
    PaperOutcome,
    ScreenerStatus,
    TimeFrame,
)
from liquidity_hunter.data import OHLCVProvider
from liquidity_hunter.data.exceptions import DataProviderError

#: The operating gates, per timeframe: the maximum `r_atr` and the minimum
#: VWAP accumulation each timeframe's measurement admits. M30/H1 are journalled
#: at the gate alone: they are thin-positive, and a journal that only ever sees
#: the good timeframes cannot notice that the thin ones behave differently live.
#:
#: The accumulation floor exists because the session VWAP **re-anchors**, and on
#: the re-anchor candle it jumps and crosses price without price having moved --
#: the trigger fires on the clock. Found by reading one stopped trade on a chart
#: (AVAXUSDT M15, 2026-08-17 21:00 UTC-3, `vwap_candles=1`) and then measured:
#: those entries are 15% of the M15 population and its worst subgroup by far
#: (34.2%/27.0% hit rate, -0.194/-0.477 net; the only negative rule of twelve in
#: `research/vwap_age_walkforward.py`, in both symbol halves).
#:
#: The floor was 15 on M15, which removed that but took the 8-14 band with it --
#: the best band of all (65.5%/60.9%). Walk-forward puts a **plateau from 2 to
#: 12** and a drop at 20, so the exact number does not matter and 15 sat just
#: past the plateau's edge: 4 beats 15 on Sharpe and total R in both halves
#: (7.31 vs 7.08 and +206 vs +198 R on search; 4.85 vs 4.67 and +107 vs +99 on
#: holdout).
#:
#: The floor is in **candles**, but what it measures is a fraction of the anchor
#: period -- and that period is per timeframe (`_VWAP_ANCHOR_PERIOD`: SESSION
#: intraday, WEEK on H4). So 15 candles is ~4h of a 96-candle M15 day and 2.5
#: days of a 42-candle H4 week. On H4 the defect is mild (its `vwap<=3` bucket
#: is positive) and any high floor is pure loss, so it stays at the plateau's
#: bottom.
OPERATING_GATES: dict[TimeFrame, tuple[float, int]] = {
    TimeFrame.M15: (1.0, 4),
    TimeFrame.M30: (1.0, 1),
    TimeFrame.H1: (1.0, 1),
    TimeFrame.H4: (1.0, 2),
}

#: How stale a fired row may be and still be a decision. A journal pass sees
#: the screener's whole recent window, but a row from ten candles ago is not a
#: decision -- acting on it is chasing, and pricing it against the tape *now*
#: measures the chase rather than the slippage. One closed candle is the
#: honest bound: the trigger closed, and the next price is what a live reader
#: could have taken. (Found by the journal's own first live pass, which
#: recorded a 1.6R "slippage" on a signal that had fired three hours earlier.)
MAX_DECISION_AGE_CANDLES = 1

#: The payoff the study settles on, and the horizon it settles within.
TARGET_R = 2.0
HORIZON_CANDLES = 40

#: Candles a resolution pass fetches per open decision.
RESOLVE_LOOKBACK = 200

DEFAULT_JOURNAL_PATH = Path("paper_journal.jsonl")

#: How far into the block the test may reach, as a fraction of the block's own
#: height, per timeframe. A test that dives most of the way through the box is
#: not the shallow rejection the layer is about, even when no candle closed out
#: the far side (which `test_pierced_the_block` already refuses).
#:
#: Only M15 is listed because only M15 is measured. A timeframe absent from
#: this dict is not gated on depth -- applying an unmeasured rule to the thin
#: H4 core would be exactly the move this project keeps refusing.
#:
#: Measured 2026-08-26 (`research/quality_features.py`, 1307 entries, 70
#: symbols, ~625 days, over the gated population): 2R hit rate 62.1% -> 64.0%,
#: R per trade +0.561 -> +0.623, PF 2.15 -> 2.36, and mean adverse excursion
#: 0.86R -> 0.80R. It keeps 84% of the trades, and it is the only one of five
#: candidate features that improves the **daily** series as well as the
#: per-trade one (R/day +0.521 -> +0.557, SR 8.65 -> 9.33) -- the distinction
#: that has retired three earlier candidates, since a day filtered out is a day
#: not traded. It gains in all four independent cuts, and gains *most* in the
#: symbol holdout (+0.086) and the recent half (+0.100).
#:
#: The threshold sits on a **plateau**, which is why it is not a fitted number:
#: every cut from 0.30 to 1.00 beats the ungated series (SR 9.03-9.33). Only
#: 0.20 and 0.25 fall back to it -- and `pen <= 0.25` was the one depth rule
#: `research/block_test_walkforward.py` declared in advance on 2026-08-25,
#: which is why that study concluded depth does not separate. Both readings are
#: right about what each tested; this one found the edge, at the cost of having
#: chosen its threshold after seeing the curve.
#:
#: What it costs: the discarded group is weak, not negative (+0.240R, 52.3% at
#: 2R, +51R in total over the window). Unlike `test_pierced_the_block`, this cut
#: gives up real profit for a better average -- worth it while attention and
#: capital are the binding constraint, not opportunity.
MAX_BLOCK_PENETRATION: dict[TimeFrame, float] = {
    TimeFrame.M15: 0.5,
}


def decision_key(entry: BlockReclaimScanEntry) -> str:
    """Identity of a decision: one per trigger candle, symbol and direction."""
    return (
        f"{entry.symbol}|{entry.timeframe.value}|{entry.direction.value}"
        f"|{entry.timestamp.isoformat()}"
    )


def passes_gates(
    entry: BlockReclaimScanEntry,
    gates: dict[TimeFrame, tuple[float, int]] = OPERATING_GATES,
) -> bool:
    """Whether a fired row is one the operating plan would have taken."""
    if entry.status is not ScreenerStatus.FIRED or entry.reclaim is None:
        return False
    if entry.reclaim.provisional:
        return False  # the trigger candle has not closed; nothing is settled
    if entry.candles_ago > MAX_DECISION_AGE_CANDLES:
        return False
    gate = gates.get(entry.timeframe)
    if gate is None:
        return False
    max_r_atr, min_vwap_candles = gate
    if entry.r_atr is None or entry.r_atr > max_r_atr:
        return False
    if entry.reclaim.vwap_candles < min_vwap_candles:
        return False
    if test_pierced_the_block(entry.reclaim):
        return False
    max_penetration = MAX_BLOCK_PENETRATION.get(entry.timeframe)
    return max_penetration is None or not test_penetrated_block_deeply(
        entry.reclaim, max_fraction=max_penetration
    )


def test_pierced_the_block(reclaim: BlockReclaim) -> bool:
    """Whether the test went clean through the block and out the far side.

    The detector already retires a block that a candle *closes* beyond, which
    is the `POIZone` lifecycle rule: a wick back inside does not break a zone.
    This is the case that rule does not cover -- a wick that crosses the whole
    block, on the very visit that produced the trigger. No close settled it, so
    the block is still on the board, but nothing held: price went in one side
    and out the other, and there is no resting order left to react to.

    Measured (`research/block_test_walkforward.py`, declared before the run
    among sixteen rules): 8% of the gated population, and the rare cut whose
    discarded half is **negative on its own** rather than merely weaker --
    -14.6R search, -21.0R holdout, hit rate 34.9%/31.4%. Excluding it raises
    both Sharpe and total R in both symbol halves. It is not a proxy for small
    blocks: inside the sub-1-ATR bucket alone the split is 34.9%/31.4% against
    55.7%/57.1%.

    Depth *within* the block was measured here and did not separate -- but that
    run declared a single depth rule, `pen <= 0.25`, and 0.25 turns out to sit
    just outside the band where the reading lives. Re-measured across the whole
    range it does separate, from 0.30 outward; see `MAX_BLOCK_PENETRATION`,
    which gates it on M15. Crossing all the way out, this function's case,
    remains the strongest and the only one negative on its own.
    """
    block_low, block_high = reclaim.block_price_low, reclaim.block_price_high
    if reclaim.direction is MarketDirection.BULLISH:
        return reclaim.test_extreme < block_low
    return reclaim.test_extreme > block_high


def test_penetrated_block_deeply(
    reclaim: BlockReclaim, *, max_fraction: float
) -> bool:
    """Whether the test dove more than `max_fraction` of the way into the block.

    Measured from the edge the price *arrives* at -- the top of the box for a
    bullish block, tested from above -- down to the extreme of the test. A
    block whose height is not positive cannot be divided into, and reads False.

    The rationale is the layer's own: the block is where resting orders sit, and
    a test that is turned back near its edge says they are there in size. A test
    that works most of the way through the box says the opposite, and the fact
    that no candle *closed* out the far side is then a detail of where the
    candle happened to close, not evidence that anything held.

    See `MAX_BLOCK_PENETRATION` for the measurement, the plateau the threshold
    sits on, and what the cut gives up.
    """
    height = reclaim.block_price_high - reclaim.block_price_low
    if height <= 0:
        return False
    if reclaim.direction is MarketDirection.BULLISH:
        depth = reclaim.block_price_high - reclaim.test_extreme
    else:
        depth = reclaim.test_extreme - reclaim.block_price_low
    return depth / height > max_fraction


def build_decision(
    entry: BlockReclaimScanEntry,
    *,
    observed_price: float,
    recorded_at: datetime | None = None,
) -> PaperDecision:
    """Turn a fired screener row into a journal entry.

    `observed_price` is the price the tape was showing when the row was read
    -- the first price actually available. The levels are measured from the
    signal close (what the study assumed), so `realized_r` later measured from
    `observed_price` carries the slippage rather than hiding it.
    """
    reclaim = entry.reclaim
    assert reclaim is not None  # guarded by `passes_gates`
    bullish = entry.direction is MarketDirection.BULLISH
    entry_price = reclaim.reclaim_price
    stop = reclaim.test_extreme
    r = abs(entry_price - stop)
    target = entry_price + TARGET_R * r if bullish else entry_price - TARGET_R * r
    # Signed against the trade: paying up on a long, selling lower on a short.
    slip = (observed_price - entry_price) if bullish else (entry_price - observed_price)
    return PaperDecision(
        key=decision_key(entry),
        symbol=entry.symbol,
        timeframe=entry.timeframe,
        direction=entry.direction,
        signal_timestamp=entry.timestamp,
        signal_close=entry_price,
        recorded_at=recorded_at or datetime.now(UTC),
        observed_price=observed_price,
        slippage_pct=slip / entry_price,
        slippage_r=slip / r if r else 0.0,
        stop_price=stop,
        target_price=target,
        r_pct=r / entry_price,
        r_atr=entry.r_atr,
        vwap_candles=reclaim.vwap_candles,
        trigger_line=reclaim.trigger_line,
        pinbar_grade=reclaim.pinbar_grade,
    )


def read_journal(path: Path = DEFAULT_JOURNAL_PATH) -> list[PaperDecision]:
    """Every decision on file, in write order."""
    if not path.exists():
        return []
    out: list[PaperDecision] = []
    for line in path.read_text().splitlines():
        if line.strip():
            out.append(PaperDecision.model_validate_json(line))
    return out


def write_journal(
    decisions: Iterable[PaperDecision], path: Path = DEFAULT_JOURNAL_PATH
) -> None:
    """Rewrite the file. Small by construction -- tens of rows a month."""
    path.write_text(
        "".join(f"{d.model_dump_json()}\n" for d in decisions)
    )


def record_decisions(
    *,
    path: Path = DEFAULT_JOURNAL_PATH,
    provider: OHLCVProvider | None = None,
    symbols: Sequence[str] = SCREEN_SYMBOLS,
    timeframes: Sequence[TimeFrame] = SCREEN_TIMEFRAMES,
    gates: dict[TimeFrame, tuple[float, int]] = OPERATING_GATES,
) -> list[PaperDecision]:
    """Append any fired-and-gated row not already journalled. Returns the new ones."""
    screen = load_screen(
        provider=provider, symbols=symbols, timeframes=timeframes
    )
    existing = read_journal(path)
    known = {d.key for d in existing}
    fresh: list[PaperDecision] = []
    for entry in screen.entries:
        if not passes_gates(entry, gates) or decision_key(entry) in known:
            continue
        # `current_price` is the last close of the series fetched moments ago:
        # the tape's price now, not the trigger candle's.
        fresh.append(build_decision(entry, observed_price=entry.current_price))
    if fresh:
        write_journal([*existing, *fresh], path)
    return fresh


def _settle(
    decision: PaperDecision, candles: Sequence[Candle]
) -> tuple[PaperOutcome, datetime | None, int | None, float | None]:
    """Walk the candles after the trigger and settle one decision.

    A candle that spans both levels credits the **stop**: the study's
    conservative attribution, kept here so the journal and the measurement
    stay comparable rather than the journal flattering itself.
    """
    bullish = decision.direction is MarketDirection.BULLISH
    after = [c for c in candles if c.timestamp > decision.signal_timestamp]
    r = abs(decision.signal_close - decision.stop_price)
    for i, candle in enumerate(after, start=1):
        hit_stop = (
            candle.low <= decision.stop_price
            if bullish
            else candle.high >= decision.stop_price
        )
        hit_target = (
            candle.high >= decision.target_price
            if bullish
            else candle.low <= decision.target_price
        )
        if hit_stop:
            move = (
                decision.stop_price - decision.observed_price
                if bullish
                else decision.observed_price - decision.stop_price
            )
            return PaperOutcome.STOP, candle.timestamp, i, (move / r if r else 0.0)
        if hit_target:
            move = (
                decision.target_price - decision.observed_price
                if bullish
                else decision.observed_price - decision.target_price
            )
            return PaperOutcome.TARGET, candle.timestamp, i, (move / r if r else 0.0)
        if i >= HORIZON_CANDLES:
            move = (
                candle.close - decision.observed_price
                if bullish
                else decision.observed_price - candle.close
            )
            return PaperOutcome.EXPIRED, candle.timestamp, i, (move / r if r else 0.0)
    return PaperOutcome.OPEN, None, None, None


def resolve_open(
    *,
    path: Path = DEFAULT_JOURNAL_PATH,
    provider: OHLCVProvider | None = None,
    lookback: int = RESOLVE_LOOKBACK,
) -> list[PaperDecision]:
    """Settle every open decision whose outcome the candles now show."""
    from liquidity_hunter.app.dashboard_data import default_ohlcv_provider

    decisions = read_journal(path)
    provider = provider or default_ohlcv_provider()
    resolved: list[PaperDecision] = []
    updated: list[PaperDecision] = []
    for decision in decisions:
        if decision.outcome is not PaperOutcome.OPEN:
            updated.append(decision)
            continue
        try:
            candles = provider.get_ohlcv(
                decision.symbol,
                decision.timeframe,
                min(lookback, provider.max_fetch_limit),
            )
        except (DataProviderError, ValueError):
            updated.append(decision)  # try again next pass
            continue
        outcome, at, bars, realized = _settle(decision, candles)
        if outcome is PaperOutcome.OPEN:
            updated.append(decision)
            continue
        settled = decision.model_copy(
            update={
                "outcome": outcome,
                "resolved_at": at,
                "bars_to_resolution": bars,
                "realized_r": realized,
            }
        )
        updated.append(settled)
        resolved.append(settled)
    if resolved:
        write_journal(updated, path)
    return resolved
