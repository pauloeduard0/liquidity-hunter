"""O criterio de teste do bloco sobrevive ao tempo?

`research/block_test_quality.py` mediu, no corte transversal, que o *teste raso*
e o bom -- o oposto do que a inspecao de um grafico sugeria:

- visita de 0-2 velas: 65,1%/66,3% de acerto contra 51,6%/38,6% de 5-10 velas,
  monotono e replicando nas duas metades;
- vela que FECHOU dentro do bloco mede PIOR que o toque de pavio
  (58,7%/53,6% contra 61,8%/60,6%);
- atravessar o bloco inteiro (`pen_frac > 1`) e o unico corte geometrico ruim,
  e e ruim de verdade (34,8%/32,0%, liquido negativo);
- blocos maiores medem melhor.

A leitura e de mitigacao: um bloco que o preco encostou e rejeitou na hora
segue intacto; um em que ele entrou, ficou e fechou dentro esta sendo consumido.

Tudo isso foi lido depois de ver a tabela, sobre eixos correlacionados entre si
-- a mesma situacao em que os 20pp da EMA9 viraram empate. As regras abaixo sao
declaradas antes da rodada, com as perdedoras incluidas para que a contagem de
tentativas nao seja escolhida a dedo.

Run:
    poetry run python -m research.block_test_walkforward --trades /tmp/blocktest.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from research._wf import Rule, run_walkforward

COST_PCT = 0.0010
KEY = "r2_h40"

RULES: dict[str, Rule] = {
    "base": lambda r: True,
    # o eixo mais forte do corte transversal
    "visita<=1": lambda r: r["visit_candles"] <= 1,
    "visita<=2": lambda r: r["visit_candles"] <= 2,
    "visita<=3": lambda r: r["visit_candles"] <= 3,
    "visita<=5": lambda r: r["visit_candles"] <= 5,
    # seu primo quase colinear
    "toques<=1": lambda r: r["touch_candles"] <= 1,
    "toques<=2": lambda r: r["touch_candles"] <= 2,
    # a tese de mitigacao, e o seu complemento (tem de ser pior)
    "so pavio": lambda r: not r["closed_in"],
    "fechou dentro": lambda r: r["closed_in"],
    # geometria: o unico corte que separou, e o resto que nao separou
    "nao atravessou": lambda r: r["pen_frac"] <= 1.0,
    "atravessou": lambda r: r["pen_frac"] > 1.0,
    "pen<=0.25": lambda r: r["pen_frac"] <= 0.25,
    "bloco>=1atr": lambda r: r["block_atr"] >= 1.0,
    "bloco>=2atr": lambda r: r["block_atr"] >= 2.0,
    # combinacoes: e aqui que o sobreajuste costuma aparecer
    "visita<=2+so pavio": lambda r: r["visit_candles"] <= 2 and not r["closed_in"],
    "visita<=2+bloco>=1": lambda r: r["visit_candles"] <= 2 and r["block_atr"] >= 1.0,
}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trades", required=True)
    p.add_argument("--gate", type=float, default=1.0)
    p.add_argument("--min-vwap", type=int, default=4)
    p.add_argument("--sample", default="all", choices=["all", "search", "holdout"])
    p.add_argument("--train-days", type=int, default=60)
    p.add_argument("--test-days", type=int, default=20)
    a = p.parse_args()

    rows = [r for r in json.loads(Path(a.trades).read_text())
            if r["r_atr"] <= a.gate and r["vwap_candles"] >= a.min_vwap]
    if a.sample != "all":
        rows = [r for r in rows if r["sample"] == a.sample]
    span = {datetime.fromisoformat(r["timestamp"]).date() for r in rows}
    print(f"{len(rows)} trades, gate<={a.gate}, vwap>={a.min_vwap}, "
          f"amostra={a.sample}, {min(span)} .. {max(span)}  |  "
          f"{len(RULES)} regras declaradas\n")
    run_walkforward(rows, RULES, key=KEY, cost_pct=COST_PCT,
                    train_days=a.train_days, test_days=a.test_days)


if __name__ == "__main__":
    main()
