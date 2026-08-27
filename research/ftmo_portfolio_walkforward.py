"""Walk-forward da CARTEIRA, e nao de cada fluxo separado.

Cada fluxo ja passou pelo seu (`research/ftmo_walkforward.py`), o que responde
"esta regra funciona?". Sobra a pergunta que so a carteira faz: **combinar
estes cinco fluxos foi uma escolha boa, ou eu escolhi a combinacao que ficou
bonita no periodo inteiro?**

E uma pergunta diferente e mais dificil. Um fluxo pode passar sozinho e ainda
assim nao merecer entrar: se ele perde exatamente nos dias em que os outros
perdem, ele piora a serie diaria -- que e onde moram os limites da corretora --
sem melhorar o retorno o bastante para pagar por isso.

As regras que competem sao **composicoes**, nao gatilhos. Nenhuma delas mexe em
como uma entrada e detectada; todas escolhem quais fluxos ligar. A serie e
diaria e liquida do custo real (comissao + spread da barra + swap do lado),
porque um dia sem operacao rende zero e nao desaparece da conta.

A janela e a **comum aos cinco** (mar/2025 em diante, limitada pelo M5, cuja
profundidade e a retencao da corretora). Fora dela um fluxo esta ausente por
falta de dado e nao por falta de sinal, e uma composicao seria comparada
contra outra medida em outro mercado.
"""

from __future__ import annotations

import argparse
import statistics as st
from collections import defaultdict
from datetime import datetime

from research._wf import run_walkforward
from research.ftmo_portfolio import (
    CRYPTO_MAX_SPREAD,
    DATASETS,
    M5_MAX_COST_R,
    RISK_PER_TRADE,
    crypto_stream,
    index_stream,
)

#: Onde a serie dos cinco existe ao mesmo tempo.
COMMON_START = "2025-03"

INDEX = ("indice M5", "indice M15", "indice M30")
CRYPTO = ("cripto M15", "cripto H4")


def build_rows() -> list[dict]:
    streams = {
        "indice M5": index_stream("5m", M5_MAX_COST_R),
        "indice M15": index_stream("15m"),
        "indice M30": index_stream("30m"),
        "cripto M15": crypto_stream("M15", str(DATASETS / "qf_m15.json"), True,
                                    max_spread=CRYPTO_MAX_SPREAD),
        "cripto H4": crypto_stream("H4", str(DATASETS / "qf_h4.json"), False,
                                   max_spread=CRYPTO_MAX_SPREAD),
    }
    rows = []
    for name, trades in streams.items():
        for trade in trades:
            if trade["timestamp"] < COMMON_START:
                continue
            rows.append({
                "timestamp": trade["timestamp"], "stream": name,
                # `net` ja e liquido; o motor subtrai `cost` de novo, entao
                # ele recebe zero em vez de um custo em duplicidade.
                "net": trade["net"], "cost": 0.0, "r_pct": 1.0,
            })
    return sorted(rows, key=lambda r: r["timestamp"])


def rules() -> dict:
    def only(*names: str):
        return lambda r, names=names: r["stream"] in names
    return {
        "carteira completa": lambda r: True,
        "so indices": only(*INDEX),
        "so cripto": only(*CRYPTO),
        "indices + cripto H4": only(*INDEX, "cripto H4"),
        "indices + cripto M15": only(*INDEX, "cripto M15"),
        "sem o M5 de indice": only("indice M15", "indice M30", *CRYPTO),
        "os dois maiores": only("indice M5", "cripto M15"),
    }


def contribution(rows: list[dict]) -> None:
    """O que cada fluxo faz com o DIA, que e a unidade que a corretora limita.

    Retorno por operacao nao diz se um fluxo merece entrar: o que decide e se
    ele melhora ou piora os dias em que os outros ja estao operando.
    """
    by_day: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        day = datetime.fromisoformat(row["timestamp"]).date().isoformat()
        by_day[day][row["stream"]] += row["net"]
    days = sorted(by_day)
    print(f"\n  {'fluxo':<20}{'dias':>7}{'R/dia seu':>12}{'nos dias dele,':>17}")
    print(f"  {'':<20}{'':>7}{'':>12}{'o resto rende':>17}")
    for name in [*INDEX, *CRYPTO]:
        mine = [d for d in days if name in by_day[d]]
        if not mine:
            continue
        own = st.fmean(by_day[d][name] for d in mine)
        others = st.fmean(
            sum(v for k, v in by_day[d].items() if k != name) for d in mine
        )
        print(f"  {name:<20}{len(mine):>7}{own:>+12.3f}{others:>+17.3f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=int, default=60)
    parser.add_argument("--test", type=int, default=20)
    args = parser.parse_args()

    rows = build_rows()
    span = f"{rows[0]['timestamp'][:7]} -> {rows[-1]['timestamp'][:7]}"
    print(f"\n{'=' * 78}\nCARTEIRA   {len(rows)} operacoes   {span}   "
          f"custo real ja embutido\n{'=' * 78}")
    for aggregate, why in (
        ("sum", "SOMA do dia -- a pergunta de carteira: ligar um fluxo e pegar "
                "mais operacoes, e o teto diario limita a soma"),
        ("mean", "MEDIA do dia -- a pergunta de regra: normaliza pelo numero de "
                 "operacoes, para a carteira maior nao ganhar so por operar mais"),
    ):
        print(f"\n--- {why}\n")
        run_walkforward(rows, rules(), key="net", cost_pct=0.0, cost_key="cost",
                        aggregate=aggregate,
                        train_days=args.train, test_days=args.test)
    contribution(rows)
    print(f"\n  (R/dia x {RISK_PER_TRADE:.2%} = % da conta por dia)")


if __name__ == "__main__":
    main()
