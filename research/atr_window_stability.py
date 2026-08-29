"""Instabilidade retroativa: quanto da estrutura JA RESOLVIDA muda a cada refresh.

Um `MarketStructure` nao-provisional descreve um trecho de grafico que ja
aconteceu. Velas novas nao podem mudar aquele trecho -- a maquina de estados
varre os pivos em ordem, e o contrato dos marks `provisional` e justamente que
so o live edge tem licenca para repintar.

Duas coisas quebram esse contrato hoje, e este modulo separa uma da outra:

1. **O limiar olha para o futuro.** `InternalStructureDetector.detect` calcula
   `mean_tr_pct` sobre a *serie inteira* (internal_structure.py ~L1137) e
   alimenta com ele todos os gates N x ATR (`choch_fail_level_buffer_atr`,
   `provisional_choch_break_buffer_atr`, release gap, displacement, fizzle).
   Uma vela nova muda a media, muda o limiar, e uma decisao de meses atras cai
   do outro lado dele.
2. **A janela desliza.** Producao le a cauda da serie, entao o inicio da
   janela anda junto com o fim, e com ele o `_structural_anchor_index` -- o
   bootstrap comeca em outro pivo e o stream inteiro pode se reescrever.

O desenho e um 2x2: janela deslizante vs ancorada, ATR global vs congelado.
O braco `sliding/global` e a producao de hoje; `sliding/frozen` isola quanto
da instabilidade e atribuivel ao ATR.

O ATR congelado NAO e o ATR trailing (a correcao proposta): ele fixa um unico
valor para a serie toda, o que nao e implementavel em producao (usa o futuro
para escolher a constante). Ele serve como *teste de atribuicao*: e o limite
superior do que um limiar estavel pode consertar. Se congelar nao remove a
maior parte do churn, o trailing nao e a correcao.

Comparacao restrita a **regiao estavel**: eventos com timestamp anterior a
`STABLE_MARGIN` velas antes do fim da janela mais curta, e dentro da janela
visivel dos dois passos. Marks provisionais sao excluidos por contrato.

Offline: le `research/.klines_cache` direto, sem tocar a rede.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.core.domain import Candle, MarketStructure, TimeFrame
from liquidity_hunter.data.providers.base import OHLCVProvider
from liquidity_hunter.data.providers.binance import klines_row_to_candle
from liquidity_hunter.liquidity.detectors import internal_structure as istruct

CACHE_DIR = Path(__file__).parent / ".klines_cache"

#: Tamanho da janela visivel de producao (`load_dashboard_data(limit=...)`).
VISIBLE = 1200
#: Buffer de bootstrap que a producao prepende (`_INTERNAL_STRUCTURE_BOOTSTRAP_BUFFER`).
BUFFER = dd._INTERNAL_STRUCTURE_BOOTSTRAP_BUFFER
#: Velas novas por refresh simulado.
STEP = 24
#: Quantos refreshes por combo.
STEPS = 20
#: Velas antes do fim da janela que NAO contam como regiao estavel. O detector
#: precisa do pivo confirmador para emitir, e `research/event_lag.py` mediu p90
#: de 30 velas para o BOS -- 100 e folga sobre isso, entao uma mudanca aqui e
#: repintura de estrutura resolvida, nao a chegada normal de uma confirmacao.
STABLE_MARGIN = 100


def load_series(symbol: str, timeframe: TimeFrame) -> list[Candle]:
    path = CACHE_DIR / f"{symbol}_{timeframe.value}.json"
    rows: list[list[Any]] = json.loads(path.read_text())
    return [klines_row_to_candle(symbol, timeframe, r) for r in rows]


def mean_tr_pct(candles: list[Candle]) -> float:
    """A mesma formula do detector, sobre a serie que lhe for dada."""
    return statistics.fmean(
        max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)) / c.close
        for p, c in zip(candles, candles[1:], strict=False)
    )


class SliceProvider(OHLCVProvider):
    """Serve uma fatia fixa da serie, sem rede."""

    max_fetch_limit = 60_000

    def __init__(self, window: list[Candle]) -> None:
        self._window = window

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        return self._window[-limit:]


@dataclass(frozen=True)
class Key:
    timestamp: str
    event: str
    direction: str


def events_of(
    series: list[Candle],
    start: int,
    end: int,
    timeframe: TimeFrame,
    frozen_atr: float | None,
    anchored: bool,
    anchor_hint: datetime | None = None,
) -> tuple[list[MarketStructure], Candle, datetime | None]:
    """Roda a pipeline de producao sobre `series[start:end]`.

    Com `frozen_atr`, o unico `fmean` do modulo do detector -- o que calcula
    `mean_tr_pct` -- devolve a constante. Verificado por leitura: `fmean` e
    importado uma vez e chamado num unico ponto.

    `anchor_hint` e a histerese de producao (`api.anchors`): o anchor que o
    refresh anterior usou, devolvido no terceiro item para o proximo.
    """
    window = series[start:end]
    # Ancorado: a janela visivel CRESCE, senao o provider devolveria a cauda
    # de novo (`window[-limit:]`) e o inicio voltaria a deslizar.
    visible = max(1, len(window) - BUFFER)
    if not anchored:
        visible = min(VISIBLE, visible)
    provider = SliceProvider(window)
    original = istruct.fmean
    if frozen_atr is not None:
        istruct.fmean = lambda _it, **_kw: frozen_atr  # type: ignore[assignment]
    try:
        run = dd._run_internal_structure(
            provider=provider,
            symbol=window[0].symbol,
            timeframe=timeframe,
            limit=visible,
            confluence_filter=True,
            anchor_hint=anchor_hint,  # type: ignore[arg-type]
        )
    finally:
        istruct.fmean = original  # type: ignore[assignment]
    return run.events, run.candles[0], run.structural_anchor


def compare(
    prev: list[MarketStructure],
    curr: list[MarketStructure],
    prev_visible_start: Candle,
    curr_visible_start: Candle,
    cutoff: str,
) -> tuple[int, int, int, int]:
    """(adicionados, removidos, alterados, total_na_regiao) na regiao estavel."""
    floor = max(str(prev_visible_start.timestamp), str(curr_visible_start.timestamp))

    def take(evs: Iterable[MarketStructure]) -> dict[Key, str]:
        out: dict[Key, str] = {}
        for e in evs:
            ts = str(e.timestamp)
            if e.provisional or ts >= cutoff or ts < floor:
                continue
            out[Key(ts, e.event.value, e.direction.value)] = (
                f"{round(e.reference_price_level or 0, 8)}|{e.reference_timestamp}"
            )
        return out

    a, b = take(prev), take(curr)
    added = len(b.keys() - a.keys())
    removed = len(a.keys() - b.keys())
    changed = sum(1 for k in a.keys() & b.keys() if a[k] != b[k])
    return added, removed, changed, len(a)


def run_combo(
    symbol: str,
    timeframe: TimeFrame,
    series: list[Candle],
    anchored: bool,
    frozen: bool,
    hysteresis: bool = False,
) -> tuple[int, int, int, int, int]:
    """Devolve (refreshes, refreshes_com_mudanca, churn_total, eventos_base, falhas)."""
    span = VISIBLE + BUFFER
    need = span + STEP * STEPS
    if len(series) < need:
        return (0, 0, 0, 0, 0)
    frozen_atr = mean_tr_pct(series[-need:]) if frozen else None

    prev: tuple[list[MarketStructure], Candle, datetime | None] | None = None
    prev_end = 0
    held: datetime | None = None
    refreshes = dirty = churn = base = fails = 0
    for k in range(STEPS + 1):
        end = len(series) - STEP * (STEPS - k)
        start = len(series) - need if anchored else end - span
        try:
            curr = events_of(
                series, start, end, timeframe, frozen_atr, anchored,
                held if hysteresis else None,
            )
        except Exception:
            fails += 1
            prev = None
            continue
        if prev is not None:
            cutoff = str(series[prev_end - STABLE_MARGIN - 1].timestamp)
            added, removed, changed, total = compare(
                prev[0], curr[0], prev[1], curr[1], cutoff
            )
            delta = added + removed + changed
            refreshes += 1
            base += total
            churn += delta
            dirty += 1 if delta else 0
        prev, prev_end, held = curr, end, curr[2]
    return refreshes, dirty, churn, base, fails


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframes", default="15m,1h,4h")
    ap.add_argument("--symbols", type=int, default=24)
    args = ap.parse_args()

    tfs = [TimeFrame(t) for t in args.timeframes.split(",")]
    names = sorted({p.name.rsplit("_", 1)[0] for p in CACHE_DIR.glob("*_1h.json")})[
        : args.symbols
    ]

    arms = {
        "sliding/global  (producao)": (False, False),
        "sliding/frozen": (False, True),
        "anchored/global": (True, False),
        "anchored/frozen": (True, True),
    }
    # A correcao de producao: janela deslizante (como e), com o anchor seguro
    # pela histerese do `api.anchors`.
    hyst_arm = "sliding/hysteresis (fix)"
    # A segunda metade da correcao: o limiar N x ATR medido no candle julgado
    # (`_MEAN_TR_TRAILING`), com e sem a histerese do anchor.
    trail_arm = "sliding/trailing-atr"
    both_arm = "sliding/hysteresis+trailing"
    extra = [hyst_arm, trail_arm, both_arm]
    totals: dict[str, list[int]] = {a: [0, 0, 0, 0, 0] for a in [*arms, *extra]}

    for tf in tfs:
        per_arm: dict[str, list[int]] = {a: [0, 0, 0, 0, 0] for a in [*arms, *extra]}
        for symbol in names:
            try:
                series = load_series(symbol, tf)
            except FileNotFoundError:
                continue
            for name, (anchored, frozen) in arms.items():
                r = run_combo(symbol, tf, series, anchored, frozen)
                for i in range(5):
                    per_arm[name][i] += r[i]
                    totals[name][i] += r[i]
            for name, (hyst, trail) in (
                (hyst_arm, (True, False)),
                (trail_arm, (False, True)),
                (both_arm, (True, True)),
            ):
                dd._MEAN_TR_TRAILING = trail
                try:
                    r = run_combo(symbol, tf, series, False, False, hysteresis=hyst)
                finally:
                    dd._MEAN_TR_TRAILING = False
                for i in range(5):
                    per_arm[name][i] += r[i]
                    totals[name][i] += r[i]
        print(f"\n=== {tf.value} ({len(names)} simbolos, {STEPS} refreshes de {STEP} velas)")
        report(per_arm)

    print(f"\n=== TOTAL ({args.timeframes})")
    report(totals)


def report(per_arm: dict[str, list[int]]) -> None:
    print(
        f"{'braco':<28}{'refresh':>8}{'sujos':>8}{'% sujo':>9}"
        f"{'churn':>8}{'churn/ref':>11}{'base':>8}"
    )
    for name, (refreshes, dirty, churn, base, fails) in per_arm.items():
        if not refreshes:
            print(f"{name:<28}{'sem dados':>8}")
            continue
        print(
            f"{name:<28}{refreshes:>8}{dirty:>8}{100*dirty/refreshes:>8.1f}%"
            f"{churn:>8}{churn/refreshes:>11.2f}{base//refreshes:>8}"
            + (f"  ({fails} falhas)" if fails else "")
        )


if __name__ == "__main__":
    main()
