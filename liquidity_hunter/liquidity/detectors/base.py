"""Shared interface for liquidity zone detectors."""

from abc import ABC, abstractmethod

from liquidity_hunter.core.domain import Candle, LiquidityZone


class LiquidityZoneDetector(ABC):
    """Detects `LiquidityZone` instances from a series of candles."""

    @abstractmethod
    def detect(self, candles: list[Candle]) -> list[LiquidityZone]:
        """Return liquidity zones detected in `candles`.

        `candles` must be in chronological order (oldest first) and share
        the same `symbol` and `timeframe`.
        """
        raise NotImplementedError
