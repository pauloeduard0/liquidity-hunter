"""Abstract interfaces (ports) for market data providers."""

from abc import ABC, abstractmethod

from liquidity_hunter.core.domain import (
    Candle,
    FundingRate,
    LongShortRatio,
    OpenInterestPoint,
    TimeFrame,
)


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


class FuturesDataProvider(ABC):
    """A source of perpetual-futures market-state data.

    Concrete implementations talk to a perpetual-swap venue and map its
    responses onto the futures domain entities. All series are returned in
    chronological order (oldest first).
    """

    @abstractmethod
    def get_open_interest_history(
        self, symbol: str, timeframe: TimeFrame, limit: int = 500
    ) -> list[OpenInterestPoint]:
        """Return up to `limit` recent open-interest samples for `symbol`."""
        raise NotImplementedError

    @abstractmethod
    def get_funding_rate_history(self, symbol: str, limit: int = 500) -> list[FundingRate]:
        """Return up to `limit` recent funding-rate samples for `symbol`."""
        raise NotImplementedError

    @abstractmethod
    def get_long_short_ratio(
        self, symbol: str, timeframe: TimeFrame, limit: int = 500
    ) -> list[LongShortRatio]:
        """Return up to `limit` recent long/short account-ratio samples."""
        raise NotImplementedError
