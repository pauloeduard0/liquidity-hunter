"""Walk-forward do par que sobreviveu a grade do `deep_reclaim`: visita RASA e CURTA.

A grade M15 imprimiu ~30 cortes na mesma tabela e dois se destacaram:
`r_atr<=2` (o fundo da visita ficou perto da entrada) e `visit_candles<3` (a
visita durou uma ou duas velas). Eles **nao sao a mesma coisa** -- juntos dao
52,7% na busca e 51,8% no holdout contra 22-23% do controle, e a duracao
sozinha PERDE dinheiro (22,7%). Mas dois numeros escolhidos entre trinta e
exatamente o que o PBO existe para punir, e holdout nao e walk-forward.

Por isso as candidatas declaradas aqui incluem **as perdedoras**: os eixos que
mediram fraco (`penetration`, `gap_to_trigger`, `block_age`, `sweeps_in_block`)
entram como regra propria. Declarar so as duas vencedoras faria o PBO medir uma
busca que nao aconteceu -- a busca real foi a tabela inteira, e o numero de
tentativas tem que refletir isso.

A serie e diaria e liquida de custo (`COST_PCT / r_pct`), porque as regras
mudam o numero de operacoes e comparar em R bruto favorece quem opera mais.

Run:
    poetry run python -m research.deep_visit_walkforward research/.datasets/deep_m15_open.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from research._wf import run_walkforward
from research.deep_reclaim import COST_PCT


def _visit(row: dict) -> float:
    v = row.get("visit_candles")
    return 99.0 if v is None else float(v)


def _num(row: dict, field: str) -> float:
    v = row.get(field)
    return 9e9 if v is None else float(v)


def _base(row: dict) -> bool:
    """Os dois gates que a passada de portas abertas PAGOU: EMA9 a favor e o
    bloco nao atravessado. Entram como base porque cada um replicou sozinho
    nas duas amostras -- e a versao sem eles fica declarada abaixo, para o
    walk-forward poder desmenti-los em vez de os herdar."""
    return (row.get("ema9_slope_lag1") or -1) > 0 and not row.get("pierced")


def _visit(row: dict) -> float:
    v = row.get("visit_candles")
    return 99.0 if v is None else float(v)


def _num(row: dict, field: str) -> float:
    v = row.get(field)
    return 9e9 if v is None else float(v)


#: As candidatas declaradas -- as vencedoras, as variantes de limiar E as
#: perdedoras da grade. Declarar so a regra final faria o PBO medir uma busca
#: que nao aconteceu: a busca real foi a tabela inteira.
RULES = {
    "tudo": lambda r: True,
    "so os gates": _base,
    "r_atr<=2": lambda r: _base(r) and r["r_atr"] <= 2,
    "r_atr<=3": lambda r: _base(r) and r["r_atr"] <= 3,
    "visita<3": lambda r: _base(r) and _visit(r) < 3,
    "visita<6": lambda r: _base(r) and _visit(r) < 6,
    "pen<0.25": lambda r: _base(r) and _num(r, "penetration") < 0.25,
    "gap<=1": lambda r: _base(r) and _num(r, "gap_to_trigger") <= 1,
    "bloco<30": lambda r: _base(r) and _num(r, "block_age") < 30,
    "sem sweep": lambda r: _base(r) and _num(r, "sweeps_in_block") < 1,
    "FINAL r<=2 & vis<3": lambda r: _base(r) and r["r_atr"] <= 2 and _visit(r) < 3,
    "r<=2 & vis<6": lambda r: _base(r) and r["r_atr"] <= 2 and _visit(r) < 6,
    "r<=3 & vis<3": lambda r: _base(r) and r["r_atr"] <= 3 and _visit(r) < 3,
    "r<=2 & pen<0.25": lambda r: (_base(r) and r["r_atr"] <= 2
                                  and _num(r, "penetration") < 0.25),
    # Sem os gates, para o walk-forward poder dizer que eles nao valem nada.
    "FINAL sem os gates": lambda r: r["r_atr"] <= 2 and _visit(r) < 3,
}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dataset")
    p.add_argument("--key", default="r2_h120")
    p.add_argument("--sample", default="all",
                   choices=("all", "search", "holdout"),
                   help="o padrao usa o universo inteiro: o walk-forward ja "
                        "separa treino de teste no TEMPO, e a divisao por "
                        "simbolo responde outra pergunta")
    a = p.parse_args()
    rows = [r for r in json.loads(Path(a.dataset).read_text())
            if r["arm"] != "aleatorio"]
    if a.sample != "all":
        rows = [r for r in rows if r["sample"] == a.sample]
    print(f"{len(rows)} entradas · alvo/horizonte {a.key} · amostra {a.sample}\n")
    run_walkforward(rows, RULES, key=a.key, cost_pct=COST_PCT)


if __name__ == "__main__":
    main()
