"""An OHLCV provider that falls back to a secondary source on rejection."""

import logging

from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.data.exceptions import DataProviderRequestError
from liquidity_hunter.data.providers.base import OHLCVProvider

logger = logging.getLogger(__name__)


class FallbackOHLCVProvider(OHLCVProvider):
    """Tries `primary`, falling back to `secondary` when the symbol is rejected.

    Used to prefer perpetual-futures candles (aligned with the futures-derived
    analysis, and a larger per-request window) while still serving symbols that
    have no perpetual contract from spot. A `DataProviderRequestError` from the
    primary (e.g. unknown futures symbol) triggers the fallback; connection
    errors propagate, since they are not symbol-specific.

    `max_fetch_limit` follows the primary, but a fallback request is capped to
    the secondary's own limit so it never asks spot for more than spot allows.
    """

    def __init__(self, primary: OHLCVProvider, secondary: OHLCVProvider) -> None:
        self._primary = primary
        self._secondary = secondary
        self.max_fetch_limit = primary.max_fetch_limit

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        try:
            return self._primary.get_ohlcv(symbol, timeframe, limit)
        except DataProviderRequestError as exc:
            capped = min(limit, self._secondary.max_fetch_limit)
            logger.info(
                "Primary OHLCV source rejected %s (%s); falling back to secondary (limit=%d)",
                symbol,
                exc,
                capped,
            )
            return self._secondary.get_ohlcv(symbol, timeframe, capped)
