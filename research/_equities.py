"""Candles de acoes e ETFs americanos, via Yahoo, para medir a VWAP onde ela e real.

Por que esta classe de ativo existe no projeto
----------------------------------------------
A tese do setup e que a VWAP e um **ponto de Schelling**: ela funciona porque
todo mundo olha a mesma linha (`project_vwap_schelling_point` -- ancora de
evento perde metade do lift, o calendario compartilhado e que importa). Em
acao americana essa premissa e mais forte do que em qualquer outro lugar que a
gente ja mediu: o tape e consolidado, a VWAP de sessao e o benchmark contra o
qual mesa de execucao e literalmente avaliada, e a ancora -- a abertura do
pregao -- e a mesma para todo mundo, todo dia. Se o efeito for maior aqui, e
evidencia a favor da tese; se sumir, e evidencia contra, e vale mais que a
maioria dos testes que a gente pode rodar em cripto.

O que este provider NAO tenta consertar
---------------------------------------
Yahoo entrega volume por barra, mas nao entrega o lado agressor. Igual ao
`GeckoTerminalDataProvider`, `taker_buy_volume` recebe metade do volume, o que
faz `volume_delta` ler zero e as camadas de fluxo (CVD, VSA,
`MarketControlAnalyzer`) ficarem **caladas**. Um proxy do tipo "candle verde =
60% comprador" alimentaria fluxo inventado numa camada que le aquilo como
medido. O volume total, que e o que a VWAP usa, esse e real.

Limites da fonte, que sao de teto e nao de qualidade:

* **M15 so nos ultimos 60 dias.** Teto do Yahoo, verificado. H1 vai a 730 dias.
* So **horario regular** (`includePrePost=false`). E o recorte certo de
  qualquer jeito: a VWAP institucional e a da sessao, e o pre-market negocia
  uma fracao do volume. Como o pregao (13:30-20:00 UTC) nao cruza a
  meia-noite UTC, cada dia UTC contem exatamente uma sessao -- entao a ancora
  `SESSION` que o `load_dashboard_data` ja usa cai na abertura sozinha, sem
  precisar de nenhum caso especial.
* Barra com campo nulo e **descartada**, nao interpolada.

O universo nao e "SPY e QQQ"
----------------------------
Dois ETFs de indice na janela do Yahoo dariam ~26 operacoes em H1, e os dois
sobem juntos. A tese nao e sobre indice -- vale para qualquer acao liquida com
tape real --, e cem nomes de setores diferentes sao muito mais independentes
entre si do que cinco indices. SPY e QQQ ficam dentro do universo, como os
casos de interesse, nao como a amostra.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.data.exceptions import DataProviderError
from liquidity_hunter.data.providers.base import OHLCVProvider

CACHE_DIR = Path(__file__).parent / ".equity_cache"
CACHE_TTL_SECONDS = 12 * 3600

_INTERVAL = {
    TimeFrame.M15: "15m",
    TimeFrame.M30: "30m",
    TimeFrame.H1: "1h",
    TimeFrame.D1: "1d",
}
#: O teto do Yahoo por intervalo, verificado contra a API: pedir alem disso
#: volta 422, nao volta menos dados.
_RANGE = {
    TimeFrame.M15: "60d",
    TimeFrame.M30: "60d",
    TimeFrame.H1: "730d",
    TimeFrame.D1: "10y",
}

#: ~100 nomes americanos liquidos, espalhados por setor de proposito: o que
#: quebra uma amostra correlacionada e diversidade de negocio, nao quantidade
#: de tickers. SPY/QQQ/DIA/IWM entram como os indices que motivaram o estudo.
UNIVERSE: tuple[str, ...] = (
    # indices e setores
    "SPY", "QQQ", "DIA", "IWM", "XLF", "XLE", "XLK", "XLV", "XLI", "XLP",
    "XLU", "XLB", "XLY", "XLRE", "SMH", "XBI", "KRE", "GDX", "XOP", "ITB",
    # mega cap tech
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "AMD", "NFLX",
    "CRM", "ORCL", "ADBE", "INTC", "QCOM", "TXN", "MU", "AMAT", "PANW", "NOW",
    # financeiro
    "JPM", "BAC", "WFC", "GS", "MS", "C", "SCHW", "AXP", "BLK", "COF",
    # saude
    "UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV", "TMO", "ABT", "BMY", "GILD",
    # consumo
    "WMT", "COST", "HD", "MCD", "NKE", "SBUX", "TGT", "LOW", "PG", "KO",
    "PEP", "DIS", "CMG", "LULU", "ABNB", "UBER", "DASH", "BKNG", "MAR", "F",
    # industria e energia
    "CAT", "DE", "BA", "GE", "HON", "UPS", "LMT", "RTX", "UNP", "MMM",
    "XOM", "CVX", "COP", "SLB", "OXY", "PSX", "MPC", "VLO", "EOG", "HAL",
)


class YahooEquityProvider(OHLCVProvider):
    """Um `OHLCVProvider` sobre o endpoint de chart do Yahoo.

    Cacheia em disco por serie inteira: o teto de dias e da fonte, entao nao
    ha o que paginar -- uma requisicao ja traz tudo que existe daquele
    intervalo, e repetir a requisicao so gasta orcamento
    (`project_binance_ban_request_budget`, a licao que custou um ban).
    """

    max_fetch_limit = 20_000

    def __init__(self, *, pause_seconds: float = 0.4) -> None:
        self._pause = pause_seconds
        CACHE_DIR.mkdir(exist_ok=True)

    def _cache(self, symbol: str, timeframe: TimeFrame) -> Path:
        return CACHE_DIR / f"{symbol}_{timeframe.value}.json"

    def _fetch(self, symbol: str, timeframe: TimeFrame) -> dict[str, Any]:
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            f"?range={_RANGE[timeframe]}&interval={_INTERVAL[timeframe]}"
            f"&includePrePost=false"
        )
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise DataProviderError(f"{symbol}: {exc}") from exc
        chart = payload.get("chart", {})
        if chart.get("error"):
            raise DataProviderError(f"{symbol}: {chart['error']}")
        results = chart.get("result") or []
        if not results:
            raise DataProviderError(f"{symbol}: resposta vazia")
        time.sleep(self._pause)
        return results[0]

    def _series(self, symbol: str, timeframe: TimeFrame) -> dict[str, Any]:
        path = self._cache(symbol, timeframe)
        if path.exists() and time.time() - path.stat().st_mtime < CACHE_TTL_SECONDS:
            return json.loads(path.read_text())
        result = self._fetch(symbol, timeframe)
        path.write_text(json.dumps(result))
        return result

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        if timeframe not in _INTERVAL:
            raise DataProviderError(f"{timeframe.value} nao existe nesta fonte")
        result = self._series(symbol, timeframe)
        stamps = result.get("timestamp") or []
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]
        candles: list[Candle] = []
        for i, stamp in enumerate(stamps):
            row = [quote.get(k, [])[i] if i < len(quote.get(k, [])) else None
                   for k in ("open", "high", "low", "close", "volume")]
            if any(value is None for value in row):
                continue
            open_, high, low, close, volume = (float(v) for v in row)
            if min(open_, high, low, close) <= 0:
                continue
            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=datetime.fromtimestamp(int(stamp), UTC),
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    # Sem lado agressor na fonte: metade, para o delta ler
                    # zero em vez de inventar fluxo. Mesmo contrato do
                    # provider on-chain.
                    taker_buy_volume=volume / 2,
                )
            )
        return candles[-limit:]
