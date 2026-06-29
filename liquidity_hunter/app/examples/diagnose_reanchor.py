"""Diagnostic: compare `InternalStructureDetector` re-anchor modes.

Runs the internal structure detector over the same candles under all three
`reanchor_mode` values (``off`` / ``displacement`` / ``chain``) and prints a
per-mode summary -- BOS / CHoCH / SWEEP / CHOCH_FAILED counts plus the BOS and
CHoCH levels -- so trigger 1 (displacement) vs trigger 3 (chain) can be chosen
empirically. This is a throwaway research instrument; it is NOT wired into
`DashboardData` (production stays ``reanchor_mode="off"``).

Run with (live Binance H4 -- inspect the real impulsive-leg gap):

    poetry run python -m liquidity_hunter.app.examples.diagnose_reanchor

It also always runs on the bundled 1h regression fixture (offline), so the
comparison is reproducible without network.
"""

import json
import logging
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from liquidity_hunter.core.domain import Candle, MarketStructure, StructureEvent, TimeFrame
from liquidity_hunter.data import BinanceDataProvider, OHLCVProvider
from liquidity_hunter.data.exceptions import DataProviderError
from liquidity_hunter.liquidity.detectors.internal_structure import InternalStructureDetector

logger = logging.getLogger(__name__)

SYMBOL = "BTCUSDT"
TIMEFRAME = TimeFrame.H4
LIMIT = 500
SWING_LOOKBACK = 2
PERSISTENCE = 5
MODES = ("off", "displacement", "chain")

# Bundled offline fixture (the same one the internal-structure tests use).
_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "liquidity"
    / "detectors"
    / "data"
    / "btcusdt_1h_2026_06_02_08.json"
)


def _load_fixture_candles() -> list[Candle]:
    rows = json.loads(_FIXTURE.read_text())
    return [
        Candle(
            symbol=SYMBOL,
            timeframe=TimeFrame.H1,
            timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC),
            open=close,
            high=high,
            low=low,
            close=close,
            volume=1.0,
            taker_buy_volume=0.5,
        )
        for timestamp_ms, high, low, close in rows
    ]


def run_modes(
    candles: list[Candle],
    *,
    swing_lookback: int = SWING_LOOKBACK,
    persistence_candles: int = PERSISTENCE,
) -> dict[str, list[MarketStructure]]:
    """Detect internal structure under every re-anchor mode over `candles`."""
    return {
        mode: InternalStructureDetector(
            swing_lookback=swing_lookback,
            persistence_candles=persistence_candles,
            confluence_filter=False,
            reanchor_mode=mode,
        ).detect(candles)
        for mode in MODES
    }


def _summarize(events: list[MarketStructure]) -> Counter[StructureEvent]:
    return Counter(event.event for event in events)


def _print_comparison(label: str, by_mode: dict[str, list[MarketStructure]]) -> None:
    print(f"\n=== {label} ===")
    tracked = (
        StructureEvent.BREAK_OF_STRUCTURE,
        StructureEvent.CHANGE_OF_CHARACTER,
        StructureEvent.LIQUIDITY_SWEEP,
        StructureEvent.CHOCH_FAILED,
    )
    header = f"{'mode':<13}" + "".join(f"{e.value:<16}" for e in tracked)
    print(header)
    for mode in MODES:
        counts = _summarize(by_mode[mode])
        row = f"{mode:<13}" + "".join(f"{counts.get(e, 0):<16}" for e in tracked)
        print(row)

    for mode in MODES:
        print(f"\n  -- {mode}: CHoCH / BOS levels --")
        for event in by_mode[mode]:
            if event.event in (
                StructureEvent.CHANGE_OF_CHARACTER,
                StructureEvent.BREAK_OF_STRUCTURE,
                StructureEvent.CHOCH_FAILED,
            ):
                print(
                    f"    {event.timestamp:%Y-%m-%d %H:%M} {event.event.value:<20}"
                    f" {event.direction.value:<8} price={event.price_level:.2f}"
                    f" ref={event.reference_price_level:.2f}"
                )


def main(provider: OHLCVProvider | None = None) -> dict[str, dict[str, list[MarketStructure]]]:
    """Run the comparison on the offline fixture and (if reachable) live H4."""
    results: dict[str, dict[str, list[MarketStructure]]] = {}

    fixture_by_mode = run_modes(_load_fixture_candles())
    results["fixture_1h"] = fixture_by_mode
    _print_comparison("Offline fixture (BTCUSDT 1h)", fixture_by_mode)

    provider = provider if provider is not None else BinanceDataProvider()
    try:
        candles = provider.get_ohlcv(SYMBOL, TIMEFRAME, LIMIT)
    except DataProviderError as exc:  # offline / unreachable venue
        logger.warning("Skipping live %s fetch: %s", TIMEFRAME.value, exc)
        return results

    live_by_mode = run_modes(candles)
    results["live_h4"] = live_by_mode
    _print_comparison(f"Live {SYMBOL} {TIMEFRAME.value}", live_by_mode)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
