"""K14: robustez da retomada da VWAP do Tide no D1 (custo e concentração).

De onde vem
-----------
K13: no D1 a retomada (V0) e V_hunt passam os 4 recortes; como operação (uma
posição por vez, alvo 3R) V0 dá +0,098R e V_hunt +0,111R. Duas perguntas antes
de escrever como setup, regras fixadas ANTES de rodar, braços V0 e V_hunt, X3.

Teste 1 -- custo: 0,13% (base), 0,20% e 0,30% ida e volta.
    Passa se a 0,30% o R total for > 0 em late E em holdout.
Teste 2 -- concentração (custo 0,13%)
    a) R total sem os 5 símbolos de maior R total (tirados do conjunto todo,
       o corte mais duro possível) > 0;
    b) pelo menos metade dos símbolos com >= 5 trades tem R total > 0.
    Passa se a) e b).

Predição escrita antes: sobrevive a 0,30% (stops do D1 são largos em %);
a) passa com folga pequena; b) fica perto de 50%.

Run:
    poetry run python -m research.tide_reclaim_d1_robust
"""

from __future__ import annotations

import json
from collections import defaultdict
from statistics import median

import research.tide_reclaim_h4_trades as k9
from liquidity_hunter.core.domain import TimeFrame
from research.tide_reclaim_btc_replication import CUTS, rebuild_tf
from research.tide_reclaim_h4_trades import fmt, one_at_a_time, stats

D1_BASELINE = k9.BASELINE.parent / "tide_reclaim_setup_d1_baseline.json"
ARMS = ("V0", "V_hunt")
EXIT = "X3"


def load(cost: float) -> list[dict]:
    k9.COST = cost  # `simulate` lê o custo do módulo
    payload = json.loads(D1_BASELINE.read_text())
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for r in payload["rows"]:
        if "V0" in r["arms"]:
            by_symbol[r["symbol"]].append(r)
    return [x for s, rs in by_symbol.items() for x in rebuild_tf(s, TimeFrame.D1, rs)]


def main() -> None:
    print("\nK14 - ROBUSTEZ DA RETOMADA VWAP D1 (X3, 1 posicao por vez)")
    base_rows: list[dict] = []
    print("\n=== Teste 1: custo ===")
    cost_ok = {}
    for cost in (0.0013, 0.0020, 0.0030):
        rows = load(cost)
        if cost == 0.0013:
            base_rows = rows
        days = sorted({r["day"] for r in rows})
        for arm in ARMS:
            t = one_at_a_time([r for r in rows if arm in r["arms"]], EXIT)
            sts = {c: stats([r for r in t if CUTS[c](r)], days)
                   for c in ("late", "holdout", "tudo")}
            for c, st in sts.items():
                print(f"  custo {cost:.2%} {arm:6s} [{c:7s}] {fmt(st)}")
            if cost == 0.0030:
                cost_ok[arm] = all(sts[c] and sts[c]["total"] > 0 for c in ("late", "holdout"))
    for arm, ok in cost_ok.items():
        print(f"  {arm}: a 0,30% total>0 late+holdout => {'PASSA' if ok else 'reprovado'}")

    print("\n=== Teste 2: concentracao (custo 0,13%) ===")
    days = sorted({r["day"] for r in base_rows})
    for arm in ARMS:
        t = one_at_a_time([r for r in base_rows if arm in r["arms"]], EXIT)
        per: dict[str, list[float]] = defaultdict(list)
        for r in t:
            per[r["symbol"]].append(r["net_x"])
        totals = {s: sum(v) for s, v in per.items()}
        ranked = sorted(totals.items(), key=lambda kv: -kv[1])
        top5 = [s for s, _ in ranked[:5]]
        rest = [r for r in t if r["symbol"] not in top5]
        rest_st = stats(rest, days)
        eligible = {s: v for s, v in totals.items() if len(per[s]) >= 5}
        share = sum(v > 0 for v in eligible.values()) / len(eligible)
        print(f"  {arm}: top5 {[(s, round(v, 1)) for s, v in ranked[:5]]}")
        print(f"     piores 5 {[(s, round(v, 1)) for s, v in ranked[-5:]]}")
        print(f"     inteiro   {fmt(stats(t, days))}")
        print(f"     sem top5  {fmt(rest_st)}")
        print(f"     simbolos positivos: {share:.0%} de {len(eligible)} "
              f"(mediana {median(eligible.values()):+.1f}R por simbolo)")
        ok = bool(rest_st and rest_st["total"] > 0) and share >= 0.5
        print(f"     => {'PASSA' if ok else 'reprovado'}")


if __name__ == "__main__":
    main()
