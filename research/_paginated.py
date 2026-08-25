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

#: Quantas vezes repetir um erro de rede transitorio antes de desistir do
#: simbolo. O erro traduzido vira `DataProviderError`, que o laco de
#: combos ja sabe pular nomeando o simbolo.
NETWORK_RETRIES = 4
RETRY_BACKOFF_SECONDS = 2.0
PAGE = 1500
CACHE_TTL_SECONDS = 6 * 3600


def strip_dead_tail(rows: list[list[Any]]) -> tuple[list[list[Any]], int]:
    """Corta o rabo de velas de amplitude zero no fim da serie.

    Depois que um perp e deslistado/liquidado, a Binance continua emitindo
    klines no preco de liquidacao: `high == low == close`, volume zero, por
    meses. Sao os mesmos 12 campos de uma vela de verdade, entao nada abaixo
    sabe que aquilo nao e mercado -- o ATR vira 0, o detector de consolidacao
    confirma uma caixa de altura zero (que o dominio recusa, derrubando o
    simbolo inteiro), e a medicao registraria "nenhum trade" onde na verdade
    nao havia preco.

    So o **rabo** e cortado. Uma vela de amplitude zero no meio da serie e
    iliquidez, uma observacao legitima, e fica onde esta.
    """
    end = len(rows)
    while end > 0 and float(rows[end - 1][2]) == float(rows[end - 1][3]):
        end -= 1
    return rows[:end], len(rows) - end


class PaginatedFuturesProvider(OHLCVProvider):
    """Serve ate `max_fetch_limit` candles, paginando o `/fapi/v1/klines`."""

    max_fetch_limit = 60_000

    def __init__(self, exchange: ccxt.Exchange | None = None) -> None:
        self._exchange = exchange or ccxt.binanceusdm({"enableRateLimit": True})

    def _cache(self, symbol: str, timeframe: TimeFrame) -> Path:
        return CACHE_DIR / f"{symbol}_{timeframe.value}.json"

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        rows, dead = strip_dead_tail(self._rows(symbol, timeframe, limit))
        if dead:
            print(
                f"  ! {symbol} {timeframe.value}: {dead} velas de amplitude zero "
                f"no fim da serie descartadas (perp liquidado/deslistado); "
                f"restam {len(rows)}"
            )
        if not rows:
            # Sem uma unica vela viva: o contrato de `get_ohlcv` e uma serie, e
            # devolver a lista vazia explodia la adiante num `IndexError` que o
            # laco de combos nao pega -- um simbolo morto derrubava a varredura
            # inteira. `DataProviderError` ele ja sabe pular, nomeando o simbolo.
            raise DataProviderError(
                f"{symbol} {timeframe.value}: serie sem velas vivas "
                "(perp liquidado/deslistado)"
            )
        return [klines_row_to_candle(symbol, timeframe, row) for row in rows[-limit:]]

    def _rows(self, symbol: str, timeframe: TimeFrame, limit: int) -> list[list[Any]]:
        path = self._cache(symbol, timeframe)
        if path.exists() and time.time() - path.stat().st_mtime < CACHE_TTL_SECONDS:
            cached: list[list[Any]] = json.loads(path.read_text())
            if len(strip_dead_tail(cached)[0]) >= limit:
                return cached

        if limit > self.max_fetch_limit:
            # Cortar em silencio custou caro uma vez: um estudo pediu 75 000
            # candles de 5m para casar a janela de calendario de outro em 15m,
            # recebeu 60 000, e a conclusao foi publicada afirmando um desenho
            # que nao tinha acontecido. Devolver menos do que se pediu e
            # legitimo; nao dizer nada, nao.
            print(
                f"  ! {symbol} {timeframe.value}: pedido {limit} candles, "
                f"teto do provider e {self.max_fetch_limit} -- a janela sera menor"
            )

        binance_symbol = to_ccxt_symbol(symbol).replace("/", "")
        collected: list[list[Any]] = []
        end: int | None = None
        # Conta so as velas vivas: num perp ja liquidado o rabo morto pode ser
        # mais longo que a janela pedida, e parar em `len(collected)` devolveria
        # uma serie inteira de preco de liquidacao -- ou seja, o simbolo sumiria
        # da medicao justamente por ter morrido, que e a amostra sobrevivente
        # classica. Paginar por velas vivas alcanca a historia real que ele teve.
        while len(strip_dead_tail(collected)[0]) < limit:
            params: dict[str, Any] = {
                "symbol": binance_symbol,
                "interval": timeframe.value,
                "limit": PAGE,
            }
            if end is not None:
                params["endTime"] = end
            # Uma varredura de 72 simbolos faz milhares de requisicoes, e uma
            # unica que expira derrubava a coleta inteira: `RequestTimeout` e
            # `NetworkError`, nao `ExchangeError`, entao escapava do except
            # abaixo e subia ate o topo. O provider de producao ja repete
            # transitorios (`data/retry.py`); este nao repetia. Erro de
            # protocolo (simbolo invalido) continua subindo na primeira vez --
            # repetir o que nao e transitorio so gasta tempo.
            page = None
            for attempt in range(NETWORK_RETRIES):
                try:
                    page = self._exchange.fapiPublicGetKlines(params)
                    break
                except ccxt.ExchangeError as exc:
                    raise DataProviderError(f"{symbol} {timeframe.value}: {exc}") from exc
                except ccxt.NetworkError as exc:
                    if attempt == NETWORK_RETRIES - 1:
                        raise DataProviderError(
                            f"{symbol} {timeframe.value}: {exc}"
                        ) from exc
                    time.sleep(RETRY_BACKOFF_SECONDS * 2 ** attempt)
            if page is None:  # pragma: no cover - defensivo
                break
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
