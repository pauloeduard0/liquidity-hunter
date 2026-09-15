"""Paper journal for the D1 tide reclaim (`app.tide_reclaim`).

Same contract as `app.paper_journal`: **it decides and records; it never sends
an order.** It exists for the one number the study could not observe -- what
entering after the trigger candle's close costs live, in R -- and to check
live that the setup behaves like its measurement (`docs/tide_reclaim.md`).

    # one pass: settle what closed, record what fired (run once a day after 00:00 UTC)
    poetry run python -m liquidity_hunter.app.tide_reclaim_journal

    # just the report
    poetry run python -m liquidity_hunter.app.tide_reclaim_journal --report-only

Rows are :class:`PaperDecision` in their own file, so the two setups' journals
never mix. Fields that belong to the block reclaim are reused for the tide
reading: ``vwap_candles`` holds the pullback length, ``trigger_line`` is
``"tide_vwap"`` and ``pinbar_grade`` records the HUNT context (``"hunt"`` or
``"no_hunt"``) so the live report can split the two variants the study measured.

One open position per symbol, as measured (K9): a symbol with an open decision
is skipped until it settles.
"""

import argparse
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from liquidity_hunter.app.dashboard_data import load_dashboard_data
from liquidity_hunter.app.paper_journal import (
    read_journal,
    resolve_open,
    write_journal,
)
from liquidity_hunter.app.paper_runner import report
from liquidity_hunter.app.screener import SCREEN_SYMBOLS
from liquidity_hunter.app.tide_reclaim import (
    HORIZON_CANDLES,
    TIDE_RECLAIM_TIMEFRAME,
    ClosedCandleProvider,
    TideReclaimSignal,
    confirmed_leg_trend,
    detect_tide_reclaim,
)
from liquidity_hunter.core.domain import (
    FundingRate,
    LongShortRatio,
    MarketDirection,
    OpenInterestPoint,
    PaperDecision,
    PaperOutcome,
    TimeFrame,
)
from liquidity_hunter.data import FuturesDataProvider, OHLCVProvider
from liquidity_hunter.data.exceptions import DataProviderBannedError, DataProviderError

DEFAULT_TIDE_JOURNAL_PATH = Path("tide_reclaim_journal.jsonl")
#: Candles behind each reading: what the study's snapshots saw.
VISIBLE_CANDLES = 500
#: Pause between symbols; a pass is 72 dashboard loads (two series each).
REQUEST_PAUSE_SECONDS = 0.5


class _NoFutures(FuturesDataProvider):
    """The reading uses no futures data; skip three requests per symbol."""

    def get_open_interest_history(
        self, symbol: str, timeframe: TimeFrame, limit: int = 500
    ) -> list[OpenInterestPoint]:
        return []

    def get_funding_rate_history(self, symbol: str, limit: int = 500) -> list[FundingRate]:
        return []

    def get_long_short_ratio(
        self, symbol: str, timeframe: TimeFrame, limit: int = 500
    ) -> list[LongShortRatio]:
        return []


def tide_decision_key(signal: TideReclaimSignal) -> str:
    return (
        f"tide|{signal.symbol}|{signal.timeframe.value}|{signal.direction.value}"
        f"|{signal.timestamp.isoformat()}"
    )


def build_tide_decision(
    signal: TideReclaimSignal, *, observed_price: float, recorded_at: datetime | None = None
) -> PaperDecision:
    """Levels from the signal close; slippage measured against `observed_price`."""
    bullish = signal.direction is MarketDirection.BULLISH
    slip = (
        (observed_price - signal.entry_price) if bullish else (signal.entry_price - observed_price)
    )
    return PaperDecision(
        key=tide_decision_key(signal),
        symbol=signal.symbol,
        timeframe=signal.timeframe,
        direction=signal.direction,
        signal_timestamp=signal.timestamp,
        signal_close=signal.entry_price,
        recorded_at=recorded_at or datetime.now(UTC),
        observed_price=observed_price,
        slippage_pct=slip / signal.entry_price,
        slippage_r=slip / signal.risk,
        stop_price=signal.stop_price,
        target_price=signal.target_price,
        r_pct=signal.risk / signal.entry_price,
        vwap_candles=signal.pullback_candles,
        trigger_line="tide_vwap",
        pinbar_grade="hunt" if signal.hunt_active else "no_hunt",
    )


def read_signal(
    symbol: str, provider: OHLCVProvider, timeframe: TimeFrame = TIDE_RECLAIM_TIMEFRAME
) -> TideReclaimSignal | None:
    """The tide reclaim on `symbol`'s last closed candle, if any."""
    data = load_dashboard_data(
        provider=ClosedCandleProvider(provider),
        symbol=symbol,
        timeframe=timeframe,
        limit=VISIBLE_CANDLES,
        futures_provider=_NoFutures(),
    )
    return detect_tide_reclaim(
        data.candles,
        data.vwap,
        htf_direction=data.higher_timeframe_direction,
        leg_trend=confirmed_leg_trend(data.internal_structure_events),
    )


def record_tide_decisions(
    *,
    path: Path = DEFAULT_TIDE_JOURNAL_PATH,
    provider: OHLCVProvider | None = None,
    symbols: Sequence[str] = SCREEN_SYMBOLS,
    pause_seconds: float = REQUEST_PAUSE_SECONDS,
) -> list[PaperDecision]:
    """Append any tide reclaim on the last closed D1 candle not already journalled."""
    from liquidity_hunter.app.dashboard_data import default_ohlcv_provider

    provider = provider or default_ohlcv_provider()
    existing = read_journal(path)
    known = {d.key for d in existing}
    busy = {d.symbol for d in existing if d.outcome is PaperOutcome.OPEN}
    fresh: list[PaperDecision] = []
    for symbol in symbols:
        if symbol in busy:
            continue
        try:
            signal = read_signal(symbol, provider)
        except DataProviderBannedError:
            raise
        except (DataProviderError, ValueError):
            continue
        finally:
            if pause_seconds:
                time.sleep(pause_seconds)
        if signal is None or tide_decision_key(signal) in known:
            continue
        # The tape now: the last trade price, i.e. the forming candle's close.
        price = provider.get_ohlcv(symbol, TIDE_RECLAIM_TIMEFRAME, 1)[-1].close
        fresh.append(build_tide_decision(signal, observed_price=price))
    if fresh:
        write_journal([*existing, *fresh], path)
    return fresh


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--journal", type=Path, default=DEFAULT_TIDE_JOURNAL_PATH)
    p.add_argument("--report-only", action="store_true")
    args = p.parse_args()
    if not args.report_only:
        try:
            for d in resolve_open(path=args.journal, horizon=HORIZON_CANDLES):
                print(f"settled {d.symbol} {d.direction.value} -> {d.outcome.value} "
                      f"{d.realized_r:+.2f}R in {d.bars_to_resolution} bars")
            fresh = record_tide_decisions(path=args.journal)
            for d in fresh:
                print(f"recorded {d.symbol} D1 {d.direction.value} ({d.pinbar_grade}) "
                      f"@ {d.observed_price:g} stop {d.stop_price:g} target {d.target_price:g} "
                      f"(slip {d.slippage_r:+.3f}R)")
            if not fresh:
                print("no new tide reclaim")
        except DataProviderBannedError as exc:
            print(f"venue rate limit / ban -- pass aborted: {exc}")
    print()
    print(report(read_journal(args.journal)))


if __name__ == "__main__":
    main()
