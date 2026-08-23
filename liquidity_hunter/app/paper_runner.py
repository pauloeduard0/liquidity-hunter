"""Run one journal pass, and report what the journal knows so far.

    # one pass: resolve what closed, record what fired (cron this)
    poetry run python -m liquidity_hunter.app.paper_runner

    # just the report
    poetry run python -m liquidity_hunter.app.paper_runner --report-only

Records decisions and settles them. Sends no orders, holds no credentials,
reads only public market data. The number it exists to produce is the
**slippage in R**: every figure in `docs/block_reclaim.md` assumes an entry at
the trigger candle's close, and this is the measurement of what that
assumption costs live.
"""

import argparse
from pathlib import Path
from statistics import fmean, median

from liquidity_hunter.app.paper_journal import (
    DEFAULT_JOURNAL_PATH,
    read_journal,
    record_decisions,
    resolve_open,
)
from liquidity_hunter.core.domain import PaperDecision, PaperOutcome
from liquidity_hunter.data.exceptions import DataProviderBannedError


def report(decisions: list[PaperDecision]) -> str:
    """What the journal knows: fills, slippage, and the resolved tally."""
    if not decisions:
        return "journal empty -- no decisions recorded yet"
    lines = [f"{len(decisions)} decisions journalled"]
    slips_r = [d.slippage_r for d in decisions]
    lines.append(
        f"  slippage    median {median(slips_r):+.3f}R  "
        f"mean {fmean(slips_r):+.3f}R  worst {max(slips_r):+.3f}R"
    )
    settled = [d for d in decisions if d.outcome is not PaperOutcome.OPEN]
    lines.append(
        f"  open {sum(1 for d in decisions if d.outcome is PaperOutcome.OPEN)}"
        f"  settled {len(settled)}"
    )
    if settled:
        wins = sum(1 for d in settled if d.outcome is PaperOutcome.TARGET)
        realized = [d.realized_r for d in settled if d.realized_r is not None]
        lines.append(
            f"  hit rate {wins / len(settled):.1%}  "
            f"mean realized {fmean(realized):+.3f}R"
            if realized
            else f"  hit rate {wins / len(settled):.1%}"
        )
        # Per timeframe, since the plan's two cores behave differently.
        for tf in sorted({d.timeframe for d in settled}, key=lambda t: t.value):
            rows = [d for d in settled if d.timeframe is tf]
            w = sum(1 for d in rows if d.outcome is PaperOutcome.TARGET)
            rr = [d.realized_r for d in rows if d.realized_r is not None]
            lines.append(
                f"    {tf.value:>4}: n={len(rows):>3}  hit {w / len(rows):>5.1%}  "
                f"realized {fmean(rr):+.3f}R" if rr else
                f"    {tf.value:>4}: n={len(rows):>3}  hit {w / len(rows):>5.1%}"
            )
    lines.append(
        "\nThe number this exists for is the slippage: the study assumes an "
        "entry at the trigger candle's close, and M15 nets +0.23R on a cost "
        "of 0.41R, so a persistent slippage of even 0.05R matters there."
    )
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL_PATH)
    p.add_argument("--report-only", action="store_true")
    args = p.parse_args()

    if not args.report_only:
        try:
            settled = resolve_open(path=args.journal)
            for d in settled:
                print(
                    f"settled {d.symbol} {d.timeframe.value} {d.direction.value} "
                    f"-> {d.outcome.value} {d.realized_r:+.2f}R "
                    f"in {d.bars_to_resolution} bars"
                )
            fresh = record_decisions(path=args.journal)
            for d in fresh:
                print(
                    f"recorded {d.symbol} {d.timeframe.value} {d.direction.value} "
                    f"@ {d.observed_price:g} (close {d.signal_close:g}, "
                    f"slip {d.slippage_r:+.3f}R)"
                )
            if not settled and not fresh:
                print("nothing new")
        except DataProviderBannedError as exc:
            # Not a crash to debug: the venue cut us off, and the only correct
            # response is to stop asking until its own expiry passes. Nothing
            # is lost -- both passes are idempotent.
            print(f"venue rate limit / ban -- pass aborted: {exc}")
            print(
                "wait for the expiry named above, then run again; the cached "
                "units make the next pass cheaper."
            )
    print()
    print(report(read_journal(args.journal)))


if __name__ == "__main__":
    main()
