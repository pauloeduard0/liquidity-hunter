"""Persistent storage for candles already fetched once.

A *closed* candle is immutable: the bar that printed between 14:00 and 14:15
will never print differently. The providers, though, re-download the whole
window on every refresh -- GeckoTerminal's OHLCV endpoint only answers "the
last N candles", so gaining one new bar costs a thousand old ones. This store
is what makes the difference sayable: history is kept, and a refresh only has
to ask for the tail.

Deliberately SQLite over a bespoke file format: the access pattern is a range
query on `(series, timeframe, timestamp)`, which is an index, and the writes
come from several threads (the prefetch pool, the overview's ladder).

Keyed by *series*, not by symbol -- see `OHLCVProvider.series_key`.
"""

import logging
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from liquidity_hunter.core.domain import Candle, TimeFrame

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    series      TEXT    NOT NULL,
    timeframe   TEXT    NOT NULL,
    ts          INTEGER NOT NULL,
    symbol      TEXT    NOT NULL,
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      REAL    NOT NULL,
    taker_buy   REAL    NOT NULL,
    PRIMARY KEY (series, timeframe, ts)
) WITHOUT ROWID;
"""


def default_candle_store_path() -> Path:
    """Where the shared store lives when no path is configured."""
    return Path.home() / ".cache" / "liquidity-hunter" / "candles.sqlite3"


class SQLiteCandleStore:
    """A thread-safe candle archive.

    One connection per thread (SQLite connections are not shareable across
    threads), WAL enabled so a reader never blocks behind the writer.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else default_candle_store_path()
        if str(self._path) != ":memory:":
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        # A shared connection keeps an in-memory database alive across threads;
        # a file-backed one is opened per thread instead.
        self._shared: sqlite3.Connection | None = None
        if str(self._path) == ":memory:":
            self._shared = self._new_connection()
        else:
            self._new_connection().close()

    # -- connection ---------------------------------------------------------------

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._path), check_same_thread=False)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.executescript(_SCHEMA)
        connection.commit()
        return connection

    def _connection(self) -> sqlite3.Connection:
        if self._shared is not None:
            return self._shared
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = self._new_connection()
            self._local.connection = connection
        return connection

    # -- reads --------------------------------------------------------------------

    def last_timestamp(self, series: str, timeframe: TimeFrame) -> datetime | None:
        """Timestamp of the newest stored candle, or `None` if the series is empty."""
        row = (
            self._connection()
            .execute(
                "SELECT MAX(ts) FROM candles WHERE series = ? AND timeframe = ?",
                (series, timeframe.value),
            )
            .fetchone()
        )
        if row is None or row[0] is None:
            return None
        return datetime.fromtimestamp(row[0], tz=UTC)

    def oldest_timestamp(self, series: str, timeframe: TimeFrame) -> datetime | None:
        """Timestamp of the earliest stored candle, or `None` if the series is empty."""
        row = (
            self._connection()
            .execute(
                "SELECT MIN(ts) FROM candles WHERE series = ? AND timeframe = ?",
                (series, timeframe.value),
            )
            .fetchone()
        )
        if row is None or row[0] is None:
            return None
        return datetime.fromtimestamp(row[0], tz=UTC)

    def count(self, series: str, timeframe: TimeFrame) -> int:
        """How many candles are stored for the series."""
        row = (
            self._connection()
            .execute(
                "SELECT COUNT(*) FROM candles WHERE series = ? AND timeframe = ?",
                (series, timeframe.value),
            )
            .fetchone()
        )
        return int(row[0]) if row else 0

    def load(
        self,
        series: str,
        timeframe: TimeFrame,
        symbol: str,
        limit: int,
        *,
        before: datetime | None = None,
    ) -> list[Candle]:
        """The newest `limit` stored candles (strictly older than `before`), oldest first.

        `symbol` stamps the returned entities: the store is keyed by series, so
        the same rows serve every alias that resolves to it.
        """
        if limit <= 0:
            return []
        sql = (
            "SELECT ts, open, high, low, close, volume, taker_buy FROM candles"
            " WHERE series = ? AND timeframe = ?"
        )
        params: list[object] = [series, timeframe.value]
        if before is not None:
            sql += " AND ts < ?"
            params.append(int(before.timestamp()))
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        rows = self._connection().execute(sql, params).fetchall()
        candles = [
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=datetime.fromtimestamp(row[0], tz=UTC),
                open=row[1],
                high=row[2],
                low=row[3],
                close=row[4],
                volume=row[5],
                taker_buy_volume=row[6],
            )
            for row in rows
        ]
        candles.reverse()
        return candles

    # -- writes -------------------------------------------------------------------

    def save(self, series: str, candles: list[Candle]) -> int:
        """Upsert `candles` under `series`; returns how many rows were written.

        Upsert rather than insert-or-ignore because a re-fetched candle is the
        authority: the caller overlaps the live edge precisely so a bar that was
        still forming when it was last seen gets replaced by its final print.
        """
        if not candles:
            return 0
        rows = [
            (
                series,
                candle.timeframe.value,
                int(candle.timestamp.timestamp()),
                candle.symbol,
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume,
                candle.taker_buy_volume,
            )
            for candle in candles
        ]
        connection = self._connection()
        with connection:
            connection.executemany(
                "INSERT INTO candles"
                " (series, timeframe, ts, symbol, open, high, low, close, volume, taker_buy)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(series, timeframe, ts) DO UPDATE SET"
                " open=excluded.open, high=excluded.high, low=excluded.low,"
                " close=excluded.close, volume=excluded.volume,"
                " taker_buy=excluded.taker_buy, symbol=excluded.symbol",
                rows,
            )
        return len(rows)

    def clear(self) -> None:
        """Drop every stored candle (tests, and a corrupted-series reset)."""
        connection = self._connection()
        with connection:
            connection.execute("DELETE FROM candles")
