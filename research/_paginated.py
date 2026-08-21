"""Um `OHLCVProvider` que pagina para tras, alem do teto de 1500 do endpoint.

`BinanceFuturesOHLCVProvider` faz uma requisicao so, entao `load_dashboard_data`
nunca ve mais que 1500 candles -- ~4 dias em M5, ~12 dias em M15. Para uma
medicao isso e uma janela curta demais: e um regime de mercado, e um resultado
medido em um regime nao diz nada sobre o proximo.

Este provider caminha para tras com `endTime` ate juntar `limit` candles, e
guarda a serie em disco. A paginacao nao muda nenhuma leitura: as linhas sao as
mesmas 12 colunas do mesmo endpoint, so em maior quantidade -- o que muda e
quantos regimes distintos entram na amostra.

Fora de `liquidity_hunter/` de proposito: e ferramenta de pesquisa, e o
provider de producao serve o dashboard, que nao precisa de historia profunda.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import ccxt
from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.data.exceptions import DataProviderError
from liquidity_hunter.data.providers.base import FuturesDataProvider, OHLCVProvider
from liquidity_hunter.data.providers.binance import klines_row_to_candle, to_ccxt_symbol

CACHE_DIR = Path(__file__).parent / ".klines_cache"
PAGE = 1500
CACHE_TTL_SECONDS = 6 * 3600


class PaginatedFuturesProvider(OHLCVProvider):
    """Serve ate `max_fetch_limit` candles, paginando o `/fapi/v1/klines`."""

    max_fetch_limit = 60_000

    def __init__(self, exchange: ccxt.Exchange | None = None) -> None:
        self._exchange = exchange or ccxt.binanceusdm({"enableRateLimit": True})

    def _cache(self, symbol: str, timeframe: TimeFrame) -> Path:
        return CACHE_DIR / f"{symbol}_{timeframe.value}.json"

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        rows = self._rows(symbol, timeframe, limit)
        return [klines_row_to_candle(symbol, timeframe, row) for row in rows[-limit:]]

    def _rows(self, symbol: str, timeframe: TimeFrame, limit: int) -> list[list[Any]]:
        path = self._cache(symbol, timeframe)
        if path.exists() and time.time() - path.stat().st_mtime < CACHE_TTL_SECONDS:
            cached: list[list[Any]] = json.loads(path.read_text())
            if len(cached) >= limit:
                return cached

        binance_symbol = to_ccxt_symbol(symbol).replace("/", "")
        collected: list[list[Any]] = []
        end: int | None = None
        while len(collected) < limit:
            params: dict[str, Any] = {
                "symbol": binance_symbol,
                "interval": timeframe.value,
                "limit": PAGE,
            }
            if end is not None:
                params["endTime"] = end
            try:
                page: list[list[Any]] = self._exchange.fapiPublicGetKlines(params)
            except ccxt.ExchangeError as exc:
                raise DataProviderError(f"{symbol} {timeframe.value}: {exc}") from exc
            if not page:
                break
            collected = page + collected
            end = int(page[0][0]) - 1
            if len(page) < PAGE:
                break  # inicio da historia disponivel

        # de-duplica por open time, mantendo ordem cronologica
        seen: dict[int, list[Any]] = {}
        for row in collected:
            seen[int(row[0])] = row
        rows = [seen[k] for k in sorted(seen)]
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows))
        return rows


class NoFuturesProvider(FuturesDataProvider):
    """Recusa toda consulta de estado de futuros.

    `load_dashboard_data` ja degrada para `oi_analysis=None` /
    `liquidation_map=None` num `DataProviderError`, entao isto pula a busca de
    OI/funding/long-short -- que esta medicao nao le, e que so custaria tempo e
    ficaria limitada aos ~30 dias de retencao de OI da Binance de qualquer jeito.
    """

    def get_open_interest_history(self, *args: Any, **kwargs: Any) -> Any:
        raise DataProviderError("futures state disabled for this measurement")

    def get_funding_rate_history(self, *args: Any, **kwargs: Any) -> Any:
        raise DataProviderError("futures state disabled for this measurement")

    def get_long_short_ratio(self, *args: Any, **kwargs: Any) -> Any:
        raise DataProviderError("futures state disabled for this measurement")
