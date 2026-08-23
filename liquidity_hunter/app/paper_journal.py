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
#: VWAP accumulation each timeframe's measurement admits. M15 carries the
#: accumulation floor (walk-forwarded, PBO 0.000); H4 does not -- no filter
#: improved its plain gate. M30/H1 are journalled at the gate alone: they are
#: thin-positive, and a journal that only ever sees the good timeframes cannot
#: notice that the thin ones behave differently live.
OPERATING_GATES: dict[TimeFrame, tuple[float, int]] = {
    TimeFrame.M15: (1.0, 15),
    TimeFrame.M30: (1.0, 1),
    TimeFrame.H1: (1.0, 1),
    TimeFrame.H4: (1.0, 1),
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
    return entry.reclaim.vwap_candles >= min_vwap_candles


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
