"""Um `OHLCVProvider` sobre os CSV exportados do MetaTrader 5.

E a ponta final da cadeia: o instrumento que se opera de fato, com o preco e
o spread da propria corretora. `research/mt5_export.py` gera os arquivos do
lado Windows; este modulo os le.

O volume e `tick_volume` -- contagem de ticks, nao contratos, porque num CFD
de indice o `real_volume` vem zerado. Isso faz da VWAP daqui uma
**aproximacao**, e vale registrar por que ela e defensavel: enquanto a
hipotese era "a VWAP vale por ser a linha institucional consolidada", pesar
por tick seria trair o mecanismo. Mas a medicao em acao americana
(`research/equity_reclaim.py`) -- tape real, ancora na abertura do pregao --
empatou com cripto, ou seja, o premio institucional da VWAP nao apareceu. Se
a qualidade da VWAP nao e o que dirige o resultado, pesar por tick e uma
aproximacao aceitavel. Aceitavel, nao verificada: o teste proprio dela e
rodar o mesmo simbolo com os dois volumes, que so o SPY permite.

`taker_buy_volume` recebe metade, como nos providers on-chain e de acoes: o
delta le zero e as camadas de fluxo ficam caladas em vez de inventar lado
agressor.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.data.exceptions import DataProviderError
from liquidity_hunter.data.providers.base import OHLCVProvider

EXPORT_DIR = Path("/mnt/c/mt5-export")

_SUFFIX = {
    TimeFrame.M5: "M5",
    TimeFrame.M15: "M15",
    TimeFrame.M30: "M30",
    TimeFrame.H1: "H1",
    TimeFrame.H4: "H4",
}

#: Os indices e o petroleo da lista da corretora. US500/US100/US30 sao quase
#: a mesma aposta, e a Europa idem -- a contagem de simbolos supera em muito
#: a contagem de apostas independentes, e o relatorio diz isso de novo.
FTMO_INDICES: tuple[str, ...] = (
    "US500.cash", "US100.cash", "US30.cash", "US2000.cash",
    "GER40.cash", "UK100.cash", "FRA40.cash", "EU50.cash",
    "SPN35.cash", "N25.cash", "AUS200.cash", "JP225.cash", "HK50.cash",
    "USOIL.cash", "UKOIL.cash",
)


class MT5CsvProvider(OHLCVProvider):
    """Le os CSV do exportador. Sem rede, sem cache -- o arquivo e o cache."""

    max_fetch_limit = 100_000

    def __init__(self, export_dir: Path = EXPORT_DIR) -> None:
        self._dir = export_dir

    def path(self, symbol: str, timeframe: TimeFrame) -> Path:
        if timeframe not in _SUFFIX:
            raise DataProviderError(f"{timeframe.value} nao foi exportado")
        return self._dir / f"{symbol.replace('.', '_')}_{_SUFFIX[timeframe]}.csv"

    def rows(self, symbol: str, timeframe: TimeFrame) -> list[dict[str, str]]:
        path = self.path(symbol, timeframe)
        if not path.exists():
            raise DataProviderError(f"{path.name} nao existe -- exporte pelo MT5")
        with path.open(encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        candles: list[Candle] = []
        for row in self.rows(symbol, timeframe):
            volume = float(row["tick_volume"])
            open_, high, low, close = (
                float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
            )
            if min(open_, high, low, close) <= 0 or volume <= 0:
                continue
            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=datetime.fromisoformat(row["time"]),
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    taker_buy_volume=volume / 2,
                )
            )
        return candles[-limit:]

    def spread_pct_at(self, symbol: str, timeframe: TimeFrame, point: float) -> dict[str, float]:
        """Spread em fracao do preco, por timestamp -- o custo real da barra.

        Cada entrada paga o spread da SUA barra, nao a mediana do periodo: o
        gatilho dispara em momento de movimento, que e onde o spread abre.
        """
        return {
            row["time"]: int(row["spread"]) * point / float(row["close"])
            for row in self.rows(symbol, timeframe)
            if float(row["close"]) > 0
        }
