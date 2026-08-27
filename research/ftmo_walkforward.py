"""Walk-forward dos indices da corretora -- o padrao que falta antes de operar.

`research/ftmo_index_reclaim.py` mostrou seis anos positivos no M30 e um bruto
forte no M5. Consistencia ano a ano e evidencia; o padrao que este projeto
exige de regra em producao e outro: escolher a regra dentro de cada janela de
treino e medir o que ela rende FORA dela, e depois perguntar quanta daquela
vantagem e so a busca (`PBO`).

A serie e **diaria e liquida**, com o custo lido do spread da propria barra
(`cost_r`). Diaria porque uma regra que filtra operacoes filtra DIAS, e o dia
que ela nao opera rende zero -- comparar por operacao premia sistematicamente
quem opera menos (`project_ema9_slope_filter`, onde 20pp por trade viraram
empate por dia).

As regras que competem sao variacoes de **selecao de instrumento por custo** e
de aperto do gate. Nenhuma delas mexe no gatilho: o setup nao esta sob
re-otimizacao aqui, so a decisao de onde e quando aplica-lo.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

from research._wf import run_walkforward

MAIN = "r2_h40"


def build_rules(rows: list[dict]) -> dict:
    """O corte por custo e por SIMBOLO, nao por operacao.

    Filtrar a operacao cara depois de conhece-la seria olhar o futuro: o
    spread da barra so existe quando a barra fecha. Filtrar o *instrumento*
    pela sua mediana e uma decisao que se toma antes, e e assim que a regra
    fica operavel.
    """
    by: dict[str, list[float]] = {}
    for row in rows:
        by.setdefault(row["symbol"], []).append(row["cost_r"])
    median = {s: st.median(v) for s, v in by.items()}
    cheap = {t: {s for s, m in median.items() if m < t} for t in (0.10, 0.20, 0.30)}
    return {
        "todos": lambda r: True,
        "custo < 0,30R": lambda r: r["symbol"] in cheap[0.30],
        "custo < 0,20R": lambda r: r["symbol"] in cheap[0.20],
        "custo < 0,10R": lambda r: r["symbol"] in cheap[0.10],
        "r_atr <= 0,7": lambda r: r["r_atr"] <= 0.7,
        "r_atr <= 0,5": lambda r: r["r_atr"] <= 0.5,
        "custo<0,20 + r_atr<=0,7": lambda r: r["symbol"] in cheap[0.20] and r["r_atr"] <= 0.7,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeframes", nargs="*", default=["5m", "15m", "30m", "1h"])
    parser.add_argument("--train", type=int, default=60)
    parser.add_argument("--test", type=int, default=20)
    args = parser.parse_args()

    for timeframe in args.timeframes:
        path = Path(f"research/.datasets/ftmo_{timeframe}.json")
        rows = json.loads(path.read_text())
        print(f"\n{'=' * 78}\n{timeframe.upper()}   {len(rows)} operacoes   "
              f"custo = spread da propria barra\n{'=' * 78}")
        run_walkforward(
            rows, build_rules(rows), key=MAIN, cost_pct=0.0, cost_key="cost_r",
            train_days=args.train, test_days=args.test,
        )


if __name__ == "__main__":
    main()
