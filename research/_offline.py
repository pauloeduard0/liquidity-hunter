"""Offline `OHLCVProvider` sobre o cache de klines ja baixado.

`research/_paginated.py` re-baixa quando o arquivo passa de 6h, e uma auditoria
de 100+ simbolos x 3 timeframes nao pode depender da rede (nem gastar o
orcamento de requisicao da Binance). Este provider so *le* `.klines_cache`:
mesmas linhas, mesma conversao, zero requisicao. Quem nao esta em cache
simplesmente nao entra na amostra -- e a amostra e reportada.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.data.exceptions import DataProviderError
from liquidity_hunter.data.providers.base import OHLCVProvider
from liquidity_hunter.data.providers.binance import klines_row_to_candle
from research._paginated import CACHE_DIR, strip_dead_tail


class OfflineKlinesProvider(OHLCVProvider):
    """Serve series do cache de disco, sem rede e sem TTL."""

    max_fetch_limit = 100_000

    def path(self, symbol: str, timeframe: TimeFrame) -> Path:
        return CACHE_DIR / f"{symbol}_{timeframe.value}.json"

    def has(self, symbol: str, timeframe: TimeFrame) -> bool:
        return self.path(symbol, timeframe).exists()

    def get_ohlcv(
        self, symbol: str, timeframe: TimeFrame, limit: int = 500
    ) -> list[Candle]:
        path = self.path(symbol, timeframe)
        if not path.exists():
            raise DataProviderError(f"{symbol} {timeframe.value}: sem cache local")
        rows: list[list[Any]] = json.loads(path.read_text())
        rows, _dead = strip_dead_tail(rows)
        if not rows:
            raise DataProviderError(
                f"{symbol} {timeframe.value}: serie sem velas vivas"
            )
        return [klines_row_to_candle(symbol, timeframe, row) for row in rows[-limit:]]


def cached_symbols(timeframes: list[TimeFrame]) -> list[str]:
    """Simbolos com cache para *todos* os timeframes pedidos."""
    provider = OfflineKlinesProvider()
    sets = [
        {
            p.name.rsplit("_", 1)[0]
            for p in CACHE_DIR.glob(f"*_{tf.value}.json")
        }
        for tf in timeframes
    ]
    common = set.intersection(*sets) if sets else set()
    return sorted(s for s in common if provider.has(s, timeframes[0]))
