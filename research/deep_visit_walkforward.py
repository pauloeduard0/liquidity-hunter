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


def _final(row: dict) -> bool:
    """A regra, com o gate de duracao afrouxado de 3 para 6 velas (2026-09-06).

    O corte veio da superficie de afrouxamento, nao de uma busca: varrendo
    `r_atr` de 1,5 a 99 contra `visit_candles` de 3 a 99, o R TOTAL fica plano
    (+118 a +140R na busca, +57 a +93R no holdout) em toda ela. Os gates nao
    produzem dinheiro, **concentram** o mesmo dinheiro em menos operacoes --
    e `vis<6` e o unico ponto que aumenta o fluxo em 40% e SOBE o total nas
    duas amostras ao mesmo tempo. O `<3` fica declarado logo abaixo, para o
    walk-forward poder desmentir a troca.

    O preco esta no R/trade (+0,46 -> +0,37) contra um custo de 0,16R que nao
    se mexe: o custo passa de 35% para 43% do bruto, o que aumenta a exposicao
    ao unico parametro do estudo que ainda e constante chutada.
    """
    return _base(row) and row["r_atr"] <= 2 and _visit(row) < 6


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
    "FINAL r<=2 & vis<6": _final,
    "antiga r<=2 & vis<3": lambda r: _base(r) and r["r_atr"] <= 2 and _visit(r) < 3,
    "r<=3 & vis<3": lambda r: _base(r) and r["r_atr"] <= 3 and _visit(r) < 3,
    "r<=2 & pen<0.25": lambda r: (_base(r) and r["r_atr"] <= 2
                                  and _num(r, "penetration") < 0.25),
    # Sem os gates, para o walk-forward poder dizer que eles nao valem nada.
    # --- o momento (2026-09-06) ---------------------------------------
    # Declarados AQUI, ao lado dos perdedores, porque o PBO so mede a busca
    # que de fato aconteceu: esconder as tentativas que morreram e a forma
    # mais barata de fabricar um numero limpo.
    "FINAL & rsi>=50": lambda r: _final(r) and (r.get("rsi") or 0) >= 50,
    "FINAL & rsi_rec>=2": lambda r: _final(r) and (r.get("rsi_recovery") or -1) >= 2,
    "FINAL & rsi_slope>=0": lambda r: _final(r) and (r.get("rsi_slope_lag1") or -1) >= 0,
    "FINAL & rsi_lag1>=45": lambda r: _final(r) and (r.get("rsi_lag1") or 0) >= 45,
    "FINAL sem os gates": lambda r: r["r_atr"] <= 2 and _visit(r) < 6,
}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dataset")
    p.add_argument("--key", default="r2_h120")
    # Como o dia agrega as operacoes que caem nele, e a pergunta muda com a
    # resposta: `mean` arrisca 1R por DIA repartido entre os sinais daquele
    # dia (uma segunda entrada dilui a primeira), `sum` arrisca 1R por
    # OPERACAO (uma segunda entrada acrescenta). Afrouxar um gate de fluxo so
    # parece bom ou ruim conforme esta escolha, entao ela vira botao em vez
    # de ficar no default -- ver `docs/deep_reclaim.md`.
    p.add_argument("--aggregate", default="mean", choices=("mean", "sum"))
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
    run_walkforward(rows, RULES, key=a.key, cost_pct=COST_PCT,
                    aggregate=a.aggregate)


if __name__ == "__main__":
    main()
