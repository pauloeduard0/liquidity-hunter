"""Abstract interface (port) for OHLCV market data providers."""

from abc import ABC, abstractmethod

from liquidity_hunter.core.domain import Candle, TimeFrame


class OHLCVProvider(ABC):
    """A source of historical OHLCV candle data.

    Concrete implementations are responsible for talking to a specific
    exchange/API and mapping its response onto `Candle` entities.
    """

    @abstractmethod
    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        """Return up to `limit` most recent candles for `symbol`/`timeframe`.

        Candles are returned in chronological order (oldest first).
        """
        raise NotImplementedError
