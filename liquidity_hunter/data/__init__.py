"""Data layer: market data acquisition, repositories, and persistence adapters.

Responsible for sourcing raw market data and mapping it to `core.domain`
entities (e.g. `Candle`). Depends only on `core`.
"""

from liquidity_hunter.data.exceptions import DataProviderBannedError
from liquidity_hunter.data.providers import (
    BinanceDataProvider,
    BinanceFuturesDataProvider,
    BinanceFuturesOHLCVProvider,
    CachingOHLCVProvider,
    FallbackOHLCVProvider,
    FuturesDataProvider,
    GeckoTerminalDataProvider,
    OHLCVProvider,
    PriceDenomination,
    RoutingOHLCVProvider,
    is_onchain_symbol,
)
from liquidity_hunter.data.repositories import SQLiteCandleStore, default_candle_store_path

__all__ = [
    "DataProviderBannedError",
    "BinanceDataProvider",
    "BinanceFuturesDataProvider",
    "BinanceFuturesOHLCVProvider",
    "CachingOHLCVProvider",
    "FallbackOHLCVProvider",
    "FuturesDataProvider",
    "GeckoTerminalDataProvider",
    "OHLCVProvider",
    "PriceDenomination",
    "RoutingOHLCVProvider",
    "SQLiteCandleStore",
    "default_candle_store_path",
    "is_onchain_symbol",
]
