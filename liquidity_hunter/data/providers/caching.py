"""Serves candles from a persistent store, fetching only what it is missing.

The sources answer "the last N candles" and nothing narrower, so a refresh
that gains one bar re-downloads the other 999. That is the whole cost model on
GeckoTerminal, where the binding constraint is a per-IP request budget rather
than bandwidth, and it is why the provider there already carries a response
cache, single-flight locks and a global 429 cooldown -- all of which cache
*requests*, for 50 seconds, in memory.

This decorator caches *history* instead. A closed candle is immutable, so the
only thing a refresh actually has to ask for is the tail since the last stored
bar, plus a few candles of overlap. Three consequences:

- a cold start after a restart is no longer cold;
- history accumulates past the source's per-request cap, simply by running --
  the window slides forward and the old bars stay;
- the request that does go out is small.

The live edge is deliberately **not** stored: the newest candle is still
forming, and persisting it would freeze a partial bar into a series the
structure detectors read as closed (`_reanchor_bos_close_break` confirms a BOS
on a *close*, so a wrong last print is not cosmetic). Since the store then ends
at the last closed bar, the next refresh's tail computation covers it
automatically, and the upsert replaces it with its final print.
"""

import logging
import threading
from datetime import UTC, datetime

from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.data.providers.base import OHLCVProvider
from liquidity_hunter.data.repositories.candle_store import SQLiteCandleStore

logger = logging.getLogger(__name__)

#: Bar length in seconds, used to size the tail request.
_TIMEFRAME_SECONDS: dict[TimeFrame, int] = {
    TimeFrame.M1: 60,
    TimeFrame.M5: 300,
    TimeFrame.M15: 900,
    TimeFrame.M30: 1_800,
    TimeFrame.H1: 3_600,
    TimeFrame.H4: 14_400,
    TimeFrame.D1: 86_400,
    TimeFrame.W1: 7 * 86_400,
}

#: Candles of overlap re-fetched beyond what the elapsed time strictly needs.
#: Covers the unstored live edge plus a margin for a source that revises a
#: freshly closed bar, and makes the contiguity check below meaningful: the
#: fresh segment is expected to reach *back into* stored history.
_OVERLAP_CANDLES = 3


class CachingOHLCVProvider(OHLCVProvider):
    """Wraps an `OHLCVProvider` with a persistent candle archive.

    Keyed by `inner.series_key(symbol)` rather than by the symbol -- an
    on-chain token resolves to whichever pool is deepest *today*, and cached
    bars from a pool that has since lost its liquidity describe a different
    chart.
    """

    def __init__(
        self,
        inner: OHLCVProvider,
        store: SQLiteCandleStore,
        *,
        max_fetch_limit: int | None = None,
    ) -> None:
        self._inner = inner
        self._store = store
        #: Series whose stored history already reaches back as far as the
        #: source will serve, mapped to *the window size that proved it*. The
        #: depth matters: a caller asking for 400 candles can only ever prove
        #: the source has nothing beyond 400, and a later caller asking for
        #: 1500 has to be allowed to try again. Recorded as a set, the smaller
        #: caller pinned the series at its own depth for every caller after it.
        #: Process-local rather than persisted: the cost of forgetting is one
        #: full request after a restart, which is the cold start already being
        #: paid, and it keeps the store's schema to candles.
        self._exhausted: dict[tuple[str, TimeFrame], int] = {}
        self._exhausted_lock = threading.Lock()
        #: May exceed the inner source's per-request cap: a request larger than
        #: one round-trip is served by topping up stored history, which is how
        #: the window grows past that cap over time.
        self.max_fetch_limit = (
            max_fetch_limit if max_fetch_limit is not None else inner.max_fetch_limit
        )

    def series_key(self, symbol: str) -> str:
        return self._inner.series_key(symbol)

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        series = self._inner.series_key(symbol)
        stored_last = self._store.last_timestamp(series, timeframe)
        stored_count = self._store.count(series, timeframe)
        stored_oldest = self._store.oldest_timestamp(series, timeframe)

        ceiling = min(limit, self._inner.max_fetch_limit)
        fetch_size = self._fetch_size(series, stored_last, stored_count, timeframe, limit)
        fresh = self._inner.get_ohlcv(symbol, timeframe, fetch_size)
        if fresh:
            # Everything but the still-forming last bar.
            self._store.save(series, fresh[:-1])
            # Only a *full-window* attempt says anything about how far back the
            # source goes. A tail request returns few candles starting after
            # the oldest stored bar by construction, so reading exhaustion off
            # one would retire a series that was never asked for its history.
            if fetch_size == ceiling and (
                len(fresh) < fetch_size
                or (stored_oldest is not None and fresh[0].timestamp >= stored_oldest)
            ):
                # The source gave back less than it was asked for, or nothing
                # older than what is already held: this series has no more
                # history behind it *at this depth*, so stop asking for a full
                # window until someone asks for a deeper one.
                with self._exhausted_lock:
                    self._exhausted[(series, timeframe)] = fetch_size

        merged = self._merge(series, symbol, timeframe, fresh, stored_last, limit)
        logger.info(
            "Cached fetch: series=%s tf=%s asked=%d fetched=%d served=%d stored=%d",
            series,
            timeframe.value,
            limit,
            fetch_size,
            len(merged),
            self._store.count(series, timeframe),
        )
        return merged

    # -- internals ----------------------------------------------------------------

    def _fetch_size(
        self,
        series: str,
        stored_last: datetime | None,
        stored_count: int,
        timeframe: TimeFrame,
        limit: int,
    ) -> int:
        """How many candles the source still has to be asked for."""
        ceiling = min(limit, self._inner.max_fetch_limit)
        if stored_last is None:
            return ceiling
        # A store shallower than one full source window cannot answer a request
        # this size, and asking only for the tail would leave it shallow
        # *forever* -- a series first seeded by a small caller (the overview
        # ladder's window is narrower than the dashboard's) would pin every
        # later request to that depth. Ask for the full window until the store
        # holds as much as one request could ever return; past that, depth only
        # comes from the window sliding forward.
        # `- 1` because the live edge is deliberately never stored, so a store
        # holding a full window still counts one short of it.
        if stored_count < ceiling - 1:
            with self._exhausted_lock:
                proven_depth = self._exhausted.get((series, timeframe))
            if proven_depth is None or ceiling > proven_depth:
                return ceiling
        period = _TIMEFRAME_SECONDS[timeframe]
        elapsed = (datetime.now(UTC) - stored_last).total_seconds()
        needed = int(elapsed // period) + 1 + _OVERLAP_CANDLES
        # Never below the overlap: a refresh has to re-read the live edge even
        # when no new bar has closed since the last one was stored.
        return max(_OVERLAP_CANDLES + 1, min(needed, ceiling))

    def _merge(
        self,
        series: str,
        symbol: str,
        timeframe: TimeFrame,
        fresh: list[Candle],
        stored_last: datetime | None,
        limit: int,
    ) -> list[Candle]:
        if not fresh:
            return self._store.load(series, timeframe, symbol, limit)
        missing = limit - len(fresh)
        if missing <= 0:
            return fresh[-limit:]

        oldest_fresh = fresh[0].timestamp
        # Contiguity: the fresh segment has to reach back into stored history.
        # If it starts *after* the newest stored bar, something in between was
        # never fetched, and splicing the two would hand the structure
        # detectors a series with an invisible discontinuity in it -- worse
        # than a shorter window. Serve the fresh segment alone.
        if stored_last is None or oldest_fresh > stored_last:
            if stored_last is not None:
                logger.warning(
                    "Gap between stored history and the fetched window"
                    " (series=%s tf=%s stored_last=%s fetched_from=%s); serving fresh only",
                    series,
                    timeframe.value,
                    stored_last,
                    oldest_fresh,
                )
            return fresh

        history = self._store.load(series, timeframe, symbol, missing, before=oldest_fresh)
        return history + fresh
