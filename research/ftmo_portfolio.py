"""Como a conta se comporta operando as duas listas da corretora ao mesmo tempo.

Os dois estudos anteriores mediram metades separadas: `ftmo_universe.py` os
Crypto CFD, `ftmo_index_reclaim.py` os indices e o petroleo. Uma conta so
opera as duas juntas, e a soma nao e a soma -- o que decide um teto de perda
diaria e quantas operacoes caem NO MESMO DIA, nao quantas existem por mes.

Cada fluxo entra pela regra que o walk-forward aprovou para ele, e por
nenhuma outra:

* **Indice M30 e M15** -- sem filtro. Os folds recusaram todos os que ofereci
  (`docs/block_reclaim.md`).
* **Indice M5** -- so os instrumentos baratos. Cru ele e zero: o spread come
  0,671R de 0,750R de bruto. Os folds escolheram um filtro de custo em 7 de 8.
* **Indice H1** -- fora. Reprovado (SR de teste negativo em 10 folds).
* **Cripto M15 e H4** -- os nucleos do plano operacional
  (`project_operating_plan_block_reclaim`), com o custo da corretora.
* **Cambio H1 e H4** -- os dois que passaram o walk-forward proprio, sem
  filtro (no cambio a busca por filtro deu PBO 0,933). M5 e M30 de cambio
  foram medidos e **reprovados**: o custo come o R inteiro, com o M5 acertando
  59,5% e ainda assim perdendo dinheiro. O M15 de cambio passa, mas **fica de
  fora da carteira**: e o fluxo mais fraco de todos (+0,142R por dia contra
  +0,217 do H1), e sobe o total por operar muito, nao por ser bom.

O R nao e comparavel entre fluxos por acaso: R e a distancia ate o stop, que
e a mesma unidade de risco em qualquer ativo e timeframe. Por isso a conta
fecha somando R -- e por isso arriscar 0,25% por operacao converte tudo
para % da conta de uma vez.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics as st
from collections import defaultdict
from pathlib import Path

from research.ftmo_universe import (
    FTMO_COMMISSION,
    FTMO_CRYPTO,
    FTMO_MAX_DAILY_LOSS,
    FTMO_MAX_DRAWDOWN,
    FTMO_PROFIT_TARGET,
    RISK_PER_TRADE,
    cost_in_r,
    load,
)

DATASETS = Path(__file__).parent / ".datasets"
MAIN = "r2_h40"
#: Ida e volta, **confirmado na ficha do instrumento** (2026-08-27): a tabela
#: de comissao cobra `0,0325 % em USD por lote` nas transacoes de entrada e de
#: saida, o que fecha o giro em 0,065%. Os estudos anteriores cobravam o dobro
#: disso, por nao saber se os 0,065% anunciados eram por lado ou pelo giro --
#: a leitura pessimista enquanto a duvida existia, e ela custava 0,62 ponto
#: percentual de retorno mensal.
CRYPTO_COMMISSION = FTMO_COMMISSION
#: Permanencia mediana medida em `ftmo_universe.holding_bars`: 4-5 velas.
#: Quase nada atravessa o rollover, mas o swap entra de qualquer forma.
SWAP_DAYS = {"M15": 0.05, "H4": 0.7}

#: Custo mediano por instrumento abaixo do qual o M5 vale a pena, e a lista
#: que ele produz. Nao e escolha minha: os folds pegaram um filtro de custo em
#: 7 de 8 janelas, vendo so o proprio treino.
M5_MAX_COST_R = 0.30
#: No cripto o corte NAO pode ser no custo total, e essa distincao custou uma
#: conclusao errada. Nos indices a comissao e zero, entao custo == spread e um
#: teto em R separa caro de barato. No cripto a comissao e um PISO: a 0,13%
#: sobre um stop de 0,2% do preco ela sozinha ja passa de 0,6R em TODO
#: instrumento, entao um teto de custo reprova a lista inteira por causa de um
#: numero que nao distingue ninguem. O que distingue e o spread, que varia 500x
#: (BTCUSD 0,009%, NEOUSD 4,8%).
CRYPTO_MAX_SPREAD = 0.001


def index_stream(timeframe: str, max_cost_r: float | None = None) -> list[dict]:
    rows = json.loads((DATASETS / f"ftmo_{timeframe}.json").read_text())
    if max_cost_r is not None:
        by: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            by[row["symbol"]].append(row["cost_r"])
        keep = {s for s, v in by.items() if st.median(v) < max_cost_r}
        rows = [r for r in rows if r["symbol"] in keep]
    return [
        {"timestamp": r["timestamp"], "symbol": r["symbol"],
         "net": r[MAIN] - r["cost_r"], "won": r[MAIN] > 0}
        for r in rows
    ]


def forex_stream(timeframe: str) -> list[dict]:
    """O cambio, com comissao, spread da barra e swap ja embutidos no `cost_r`.

    Sem corte por simbolo: no cambio a busca por filtro deu PBO 0,933 -- as
    regras de selecao que servem em indice e cripto se revezam ganhando por
    acaso aqui, e o que passou no walk-forward foi a regra sem filtro nenhum.
    """
    rows = json.loads((DATASETS / f"ftmo_fx_{timeframe}.json").read_text())
    return [
        {"timestamp": r["timestamp"], "symbol": r["symbol"],
         "net": r[MAIN] - r["cost_r"], "won": r[MAIN] > 0}
        for r in rows
    ]


def crypto_stream(label: str, path: str, gated: bool,
                  max_spread: float | None = None) -> list[dict]:
    """Cripto com o spread REAL da corretora quando ele existir.

    `research/ftmo_crypto_spread.py` grava `ftmo_crypto_<tf>.json` com o custo
    de cada entrada lido do feed. Sem esse arquivo, o custo cai para "so
    comissao", que e a estimativa que este estudo carregava antes de o spread
    ser medido -- e que superestimava o cripto em muito.
    """
    measured = DATASETS / f"ftmo_crypto_{label.lower()}.json"
    if measured.exists():
        rows = json.loads(measured.read_text())
        if max_spread is not None:
            by: dict[str, list[float]] = defaultdict(list)
            for row in rows:
                by[row["symbol"]].append(row["spread_pct"])
            keep = {s for s, v in by.items() if st.median(v) < max_spread}
            rows = [r for r in rows if r["symbol"] in keep]
        return [
            {"timestamp": r["timestamp"], "symbol": r["symbol"],
             "net": r[MAIN] - r["cost_r"], "won": r[MAIN] > 0}
            for r in rows
        ]
    wanted = set(FTMO_CRYPTO.values())
    rows = [r for r in load(path, gated) if r["symbol"] in wanted]
    swap = SWAP_DAYS.get(label, 0.2)
    return [
        {"timestamp": r["timestamp"], "symbol": r["symbol"],
         "net": r[MAIN] - cost_in_r(r, CRYPTO_COMMISSION, swap), "won": r[MAIN] > 0}
        for r in rows
    ]


def months_of(trades: list[dict]) -> int:
    return len({t["timestamp"][:7] for t in trades})


def describe(name: str, trades: list[dict]) -> None:
    if not trades:
        print(f"  {name:<22}      --")
        return
    months = months_of(trades)
    hit = sum(1 for t in trades if t["won"]) / len(trades)
    net = st.fmean(t["net"] for t in trades)
    print(f"  {name:<22}{len(trades):>7}{hit:>9.1%}{net:>+9.3f}"
          f"{len(trades) / months:>9.1f}{net * len(trades) / months:>+9.2f}"
          f"   {trades[0]['timestamp'][:7]} -> {trades[-1]['timestamp'][:7]}")


def curve(trades: list[dict]) -> tuple[list[dt.date], list[float], list[float]]:
    """Serie diaria de R e a curva acumulada.

    Diaria porque os limites da corretora sao diarios: o teto de perda do dia
    nao pergunta quantas operacoes foram, pergunta quanto o dia perdeu.
    """
    by_day: dict[dt.date, float] = defaultdict(float)
    for t in trades:
        by_day[dt.datetime.fromisoformat(t["timestamp"]).date()] += t["net"]
    days = sorted(by_day)
    daily = [by_day[d] for d in days]
    equity, total = [], 0.0
    for value in daily:
        total += value
        equity.append(total)
    return days, daily, equity


def report(streams: dict[str, list[dict]], risk: float) -> None:
    print(f"\n  {'fluxo':<22}{'n':>7}{'acerto':>9}{'R/op':>9}{'ops/mes':>9}"
          f"{'R/mes':>9}  janela")
    for name, trades in streams.items():
        describe(name, sorted(trades, key=lambda t: t["timestamp"]))

    combined = sorted(
        (t for trades in streams.values() for t in trades), key=lambda t: t["timestamp"]
    )
    print()
    describe("TUDO JUNTO", combined)

    # A janela comum e a unica em que a soma significa alguma coisa: fora dela
    # um fluxo esta ausente por falta de dado, nao por falta de sinal.
    start = max(min(t["timestamp"] for t in trades) for trades in streams.values())
    overlap = [t for t in combined if t["timestamp"] >= start]
    print()
    describe(f"janela comum (>= {start[:7]})", overlap)

    days, daily, equity = curve(overlap)
    if not days:
        return
    peak, drawdown = equity[0], 0.0
    for value in equity:
        peak = max(peak, value)
        drawdown = max(drawdown, peak - value)
    span_months = (days[-1] - days[0]).days / 30.44
    per_month = equity[-1] / span_months
    worst = min(daily)
    traded = sum(1 for value in daily if value != 0)

    calendar_days = (days[-1] - days[0]).days + 1
    print(f"\n  {calendar_days} dias de calendario, {traded} com operacao "
          f"({traded / calendar_days:.0%})")
    print(f"  R total {equity[-1]:+.1f}   R/mes {per_month:+.2f}   "
          f"drawdown maximo {drawdown:.1f}R   pior dia {worst:.1f}R")

    print(f"\n  a {risk:.2%} de risco por operacao:")
    print(f"    ganho mensal      {per_month * risk:+.2%}"
          f"   (alvo da corretora {FTMO_PROFIT_TARGET:.0%} em "
          f"{FTMO_PROFIT_TARGET / max(per_month * risk, 1e-9):.1f} meses)")
    print(f"    drawdown maximo   {drawdown * risk:.2%}"
          f"   (teto {FTMO_MAX_DRAWDOWN:.0%})")
    print(f"    pior dia          {worst * risk:.2%}"
          f"   (teto {FTMO_MAX_DAILY_LOSS:.0%})")

    # O que um teto diario realmente testa nao e a media, e o dia cheio.
    counts = defaultdict(int)
    for t in overlap:
        counts[dt.datetime.fromisoformat(t["timestamp"]).date()] += 1
    busy = sorted(counts.values(), reverse=True)
    concurrent = busy[0] if busy else 0
    print(f"\n  operacoes num mesmo dia: mediana {st.median(busy):.0f}, "
          f"p90 {busy[int(0.1 * len(busy))]}, maximo {concurrent}")
    print(f"    o pior caso aritmetico -- {concurrent} operacoes perdendo juntas -- "
          f"custa {concurrent * risk:.2%}")

    # M15 e M30 rodam nos MESMOS quinze instrumentos. Se as duas leituras
    # marcam a mesma jogada, o dia carrega risco dobrado sob dois nomes --
    # que e o modo silencioso de estourar um teto diario.
    same: dict[tuple, list[str]] = defaultdict(list)
    for name, trades in streams.items():
        for t in trades:
            if t["timestamp"] < start:
                continue
            key = (t["symbol"], dt.datetime.fromisoformat(t["timestamp"]).date())
            same[key].append(name)
    doubled = {k: v for k, v in same.items() if len(set(v)) > 1}
    print(f"\n  mesmo ativo, mesmo dia, em mais de um fluxo: {len(doubled)} casos "
          f"({len(doubled) / max(len(same), 1):.1%} dos pares ativo-dia)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--risk", type=float, default=RISK_PER_TRADE)
    args = parser.parse_args()

    streams = {
        "indice M5 (baratos)": index_stream("5m", M5_MAX_COST_R),
        "indice M15": index_stream("15m"),
        "indice M30": index_stream("30m"),
        "cripto M15": crypto_stream("M15", str(DATASETS / "qf_m15.json"), True,
                                    max_spread=CRYPTO_MAX_SPREAD),
        "cripto H4": crypto_stream("H4", str(DATASETS / "qf_h4.json"), False,
                                   max_spread=CRYPTO_MAX_SPREAD),
        "cambio H4": forex_stream("4h"),
    }
    report(streams, args.risk)


if __name__ == "__main__":
    main()
