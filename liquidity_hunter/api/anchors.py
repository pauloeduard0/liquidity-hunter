"""Where the structural anchor's hysteresis keeps its state.

`dashboard_data._structural_anchor_index` picks the candle internal-structure
detection starts at. The extreme it picks is a fixed price point, but the
*region* it is picked from slides with the window's right edge, so a fresh
candle entering from the right can be a new extreme and steal the anchor --
measured on 26-32% of refreshes across 12 symbols x 15m/1h/4h. Each move
re-seeds the detector's bootstrap and rewrites structure that was already
settled: 36,8% of refreshes changed non-provisional events sitting more than
100 candles behind the live edge, which is repainting resolved history rather
than the live-edge repaint the `provisional` marks exist to license.
(`research/anchor_stability.py`, `research/atr_window_stability.py`.)

The fix is hysteresis -- hold the previous anchor while its candle is still in
range -- and hysteresis needs memory of the previous run. No pure rule gets
there: a wider region cannot exist under the production wiring, the dominant
extreme is worse, and rolling the hysteresis inside the window scores 100%
because it pins its seed to the window's *left* edge, which is what slides.

So the memory lives **here**, in the presentation layer that already caches
per `(symbol, timeframe)`, and never in `app/`. That keeps the pipeline a pure
function of the series: a replay, a fixture and a measurement pass no hint and
reproduce exactly the stateless behaviour, so a study cannot silently depend on
which requests a server happened to serve first. The store is best-effort by
design -- a lost entry costs one re-derived anchor, which is what happens today
on every request anyway.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime

from liquidity_hunter.core.domain import TimeFrame

#: A hint is only useful while it names a candle the next window still covers.
#: Well past that the entry is just occupying memory, and re-deriving costs
#: nothing, so entries older than this since their last use are dropped.
TTL_SECONDS = 3600.0

#: Ceiling on tracked (symbol, timeframe) pairs, so a caller sweeping many
#: symbols cannot grow this without bound. The oldest use is evicted first.
MAX_ENTRIES = 512


class AnchorStore:
    """Remembers the structural anchor last used per (symbol, timeframe)."""

    def __init__(self, ttl_seconds: float = TTL_SECONDS, max_entries: int = MAX_ENTRIES) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, TimeFrame], tuple[datetime, float]] = {}

    def get(self, symbol: str, timeframe: TimeFrame) -> datetime | None:
        """The anchor to hint with, or ``None`` (which reproduces today)."""
        now = _now()
        with self._lock:
            entry = self._entries.get((symbol, timeframe))
            if entry is None:
                return None
            anchor, seen = entry
            if now - seen > self._ttl:
                del self._entries[(symbol, timeframe)]
                return None
            return anchor

    def remember(self, symbol: str, timeframe: TimeFrame, anchor: datetime | None) -> None:
        """Record the anchor a run actually used.

        A ``None`` anchor (no pre-visible buffer, so detection started at 0)
        is recorded as *forgetting*: hinting a candle the next run may not
        reach is worse than re-deriving.
        """
        with self._lock:
            key = (symbol, timeframe)
            if anchor is None:
                self._entries.pop(key, None)
                return
            self._entries[key] = (anchor, _now())
            if len(self._entries) > self._max:
                oldest = min(self._entries, key=lambda k: self._entries[k][1])
                del self._entries[oldest]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def _now() -> float:
    return time.monotonic()


#: Process-wide store, shared by the dashboard and overview routes so both
#: read one timeframe's structure off the same anchor.
anchor_store = AnchorStore()
