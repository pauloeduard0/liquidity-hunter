"""Routes a candle request to the venue that can serve its symbol."""

import logging
import re

from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.data.providers.base import OHLCVProvider

logger = logging.getLogger(__name__)

#: A bare on-chain address: base58 (Solana) or 0x-hex (EVM). Long enough that
#: no exchange ticker can collide with it.
_ADDRESS = re.compile(r"^(0x[0-9a-fA-F]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})$")


def is_onchain_symbol(symbol: str) -> bool:
    """Whether `symbol` names an on-chain pair rather than an exchange listing.

    Either an explicit `"<network>:<address>"` pair or a bare token/pool
    address.
    """
    _, separator, address = symbol.partition(":")
    if separator:
        return bool(_ADDRESS.match(address.strip()))
    return bool(_ADDRESS.match(symbol.strip()))


class RoutingOHLCVProvider(OHLCVProvider):
    """Serves on-chain symbols from `onchain` and everything else from `exchange`.

    `max_fetch_limit` advertises the larger of the two so an exchange symbol
    keeps its full window; an on-chain request is capped to its own source's
    limit inside `get_ohlcv`, the same shape `FallbackOHLCVProvider` uses.
    """

    def __init__(self, exchange: OHLCVProvider, onchain: OHLCVProvider) -> None:
        self._exchange = exchange
        self._onchain = onchain
        self.max_fetch_limit = max(exchange.max_fetch_limit, onchain.max_fetch_limit)

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        if is_onchain_symbol(symbol):
            capped = min(limit, self._onchain.max_fetch_limit)
            logger.debug("Routing %s to the on-chain source (limit=%d)", symbol, capped)
            return self._onchain.get_ohlcv(symbol, timeframe, capped)
        capped = min(limit, self._exchange.max_fetch_limit)
        return self._exchange.get_ohlcv(symbol, timeframe, capped)
