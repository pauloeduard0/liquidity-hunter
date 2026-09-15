"""K10: confirmar o alvo 3R e medir faixas de stop na retomada da VWAP H4.

De onde vem
-----------
K9 (`research/tide_reclaim_h4_trades.py`): com uma posição por vez, X2
(+0,032R) e X3 (+0,043R) ficam positivos nos três recortes, mas a saída
escolhida pela regra (XV) falhou, e X3 não foi a escolhida. Dois testes,
regras fixadas ANTES de rodar, braço V0, uma posição por vez, mesma simulação.

Teste 1 -- X3 vs X2 por ano
    Por ano-calendário com n >= 100 nas duas saídas: Sharpe diário e R total.
    X3 é CONFIRMADO se bater X2 em Sharpe diário em pelo menos 2/3 dos anos
    E tiver R total > 0 em late e em holdout.

Teste 2 -- faixas de r_atr (risco / ATR14 no gatilho)
    faixas: <=1 | (1,2] | (2,3] | >3.  Para cada faixa, isolada (uma posição
    por vez dentro dela), net e R total em late e holdout, com X2 e X3.
    Uma faixa só é CORTADA se, na saída confirmada (X3 se o teste 1 passar,
    senão X2), for negativa em R total em late E em holdout, E o conjunto
    sem ela (uma posição por vez refeita) tiver Sharpe diário >= o conjunto
    inteiro em late E em holdout. "Melhorou ao tirar o pior" sem a faixa ser
    negativa sozinha nas duas não conta (feedback_removed_bucket_is_not_evidence).

Predição escrita antes: X3 confirma em ~metade dos anos (inconclusivo); a
faixa >3 é a candidata a corte (stop muito largo), as outras ficam.

Run:
    poetry run python -m research.tide_reclaim_h4_refine
"""

from __future__ import annotations

import json
from collections import defaultdict

from research.tide_reclaim_h4_trades import BASELINE, fmt, one_at_a_time, rebuild, stats

BANDS = {
    "<=1": lambda x: x <= 1,
    "1-2": lambda x: 1 < x <= 2,
    "2-3": lambda x: 2 < x <= 3,
    ">3": lambda x: x > 3,
}
CUTS = {"late": lambda r: r["time"] == "late", "holdout": lambda r: r["sample"] == "holdout",
        "tudo": lambda r: True}


def main() -> None:
    payload = json.loads(BASELINE.read_text())
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for r in payload["rows"]:
        if r["tf"] == "4h" and "V0" in r["arms"]:
            by_symbol[r["symbol"]].append(r)
    rows = [x for s, rs in by_symbol.items() for x in rebuild(s, rs)]
    all_days = sorted({r["day"] for r in rows})
    print(f"\nK10 | V0 H4 {len(rows)} gatilhos")

    # ---- teste 1
    print("\n=== Teste 1: X3 vs X2 por ano (1 posicao por vez) ===")
    taken = {x: one_at_a_time(rows, x) for x in ("X2", "X3")}
    years = sorted({r["day"][:4] for r in rows})
    wins = counted = 0
    for y in years:
        st = {x: stats([r for r in taken[x] if r["day"][:4] == y], all_days) for x in taken}
        if any(s is None or s["n"] < 100 for s in st.values()):
            continue
        counted += 1
        wins += st["X3"]["sr"] > st["X2"]["sr"]
        print(f"  {y}  X2 {fmt(st['X2'])}\n        X3 {fmt(st['X3'])}")
    late_hold_ok = all(
        (s := stats([r for r in taken["X3"] if CUTS[c](r)], all_days)) and s["total"] > 0
        for c in ("late", "holdout")
    )
    x3_ok = counted > 0 and wins >= 2 * counted / 3 and late_hold_ok
    print(f"  X3 bate X2 em {wins}/{counted} anos; total>0 late/holdout: {late_hold_ok}"
          f" => X3 {'CONFIRMADO' if x3_ok else 'nao confirmado'}")
    exit_rule = "X3" if x3_ok else "X2"

    # ---- teste 2
    print(f"\n=== Teste 2: faixas de r_atr (saida de decisao: {exit_rule}) ===")
    full = one_at_a_time(rows, exit_rule)
    full_st = {c: stats([r for r in full if f(r)], all_days) for c, f in CUTS.items()}
    for c in CUTS:
        print(f"  inteiro [{c:7s}] {fmt(full_st[c])}")
    for band, f_band in BANDS.items():
        inside = [r for r in rows if f_band(r["r_atr"])]
        print(f"\n  faixa {band}")
        for x in ("X2", "X3"):
            t = one_at_a_time(inside, x)
            for c in ("late", "holdout", "tudo"):
                print(f"     {x} [{c:7s}] {fmt(stats([r for r in t if CUTS[c](r)], all_days))}")
        t = one_at_a_time(inside, exit_rule)
        neg = all(
            (s := stats([r for r in t if CUTS[c](r)], all_days)) is None or s["total"] < 0
            for c in ("late", "holdout")
        )
        rest = one_at_a_time([r for r in rows if not f_band(r["r_atr"])], exit_rule)
        rest_st = {c: stats([r for r in rest if CUTS[c](r)], all_days) for c in CUTS}
        better = all(
            rest_st[c] is not None and full_st[c] is not None
            and rest_st[c]["sr"] >= full_st[c]["sr"]
            for c in ("late", "holdout")
        )
        for c in CUTS:
            print(f"     sem a faixa [{c:7s}] {fmt(rest_st[c])}")
        print(f"     => {'CORTAR' if neg and better else 'manter'} "
              f"(negativa nas duas: {neg}; SR sem ela >= inteiro nas duas: {better})")


if __name__ == "__main__":
    main()
