"""Walk-forward do `trend_ride` no H4: o filtro e mecanismo ou e a amostra?

Tres filtros foram propostos para este setup e os tres mediram a mesma coisa:
melhoram no H4 e **nao replicam** em nenhum dos outros tres tempos graficos.
Pior, a melhora do H4 foi mostrada como aritmetica -- o ganho e exatamente o
balde removido, no mesmo dado em que ele foi escolhido, o que nao e evidencia
de nada. Este arquivo existe para separar as duas hipoteses que sobraram:

  (a) o H4 e genuinamente o terreno do setup (o custo por operacao la e 0,059R
      contra 0,381R no M15, e isso sozinho explica muito), ou
  (b) a amostra do H4 e pequena o bastante -- ~1100 entradas, 4 simbolos, boa
      parte do dinheiro em 2025 -- para que qualquer corte pareca funcionar.

Por isso as candidatas declaradas incluem **as que eu recomendei nao usar**:
`run_atr>=4`, `trend_age>=16` e a faixa `r_atr` 1,0-1,6 sao os baldes da cauda
gorda, e `dist<=1.5`/`dist<=2` sao o limiar que foi visto na mesma tabela em
que seria escolhido. Declarar so as vencedoras faria o PBO medir uma busca que
nao aconteceu: a busca real foi a grade inteira, tres vezes.

A base e a passada de PORTAS ABERTAS (`--no-line-sep --no-ema50`), a unica em
que toda variante e expressavel como predicado sobre a mesma populacao. As
janelas sao maiores que o padrao do `_wf` porque o H4 rende ~1100 entradas em
~7 anos: com 60 dias de treino a maioria dos folds ficaria vazia.

Run:
    poetry run python -m research.trend_ride_walkforward \\
        research/.datasets/tr_open_4h.json --key r3_h120
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from research._wf import run_walkforward
from research.trend_ride import COST_PCT


def _n(row: dict, field: str, default: float = -9e9) -> float:
    v = row.get(field)
    return default if v is None else float(v)


def _ordem(row: dict) -> bool:
    """A ORDEM das linhas: EMA9 do lado bom da VWAP. Sem magnitude."""
    return _n(row, "ema_side_atr") >= 0


def _ema50(row: dict) -> bool:
    """A EMA50 por baixo, ou no cruzamento. A leitura do usuario."""
    return _n(row, "ema50_stack_atr") >= 0


def _poc(row: dict) -> bool:
    """A sombra na linha do POC do footprint: `low <= poc <= close`.

    Primeiro filtro proposto aqui que replicou em mais de um tempo grafico --
    melhorou o por-operacao no M30, no H1 e no H4 na mesma direcao, e nao so
    no H4 como os anteriores. Custa metade do fluxo. E por isso mesmo que ele
    precisa passar por aqui: replicar entre TFs da mesma amostra ainda nao e
    replicar no TEMPO.
    """
    return bool(row.get("poc_ok"))


def _poc_side(row: dict) -> bool:
    """O POC dominado pelo lado da operacao (o azul contra o vermelho).

    Melhor filtro por operacao ja medido aqui (H4, 3R: +0,253 -> +0,278;
    5R: +0,349 -> +0,399) ao custo de 30% do fluxo. E tambem o SEXTO filtro
    testado na mesma amostra de 4 simbolos, e o ganho cabe no ruido com
    n=264 -- que e exatamente o que este arquivo existe para cobrar.
    """
    return row.get("poc_lado") == ("compra" if row["direction"] == "bullish"
                                   else "venda")


def _rsi_ma(row: dict) -> bool:
    """A media do RSI do lado certo do 50. Medida e REJEITADA (terceira
    rejeicao do RSI no projeto); declarada para o PBO contar a tentativa."""
    lvl = _n(row, "rsi_ma", -9e9)
    return lvl >= 50 if row["direction"] == "bullish" else lvl <= 50


def _base(row: dict) -> bool:
    """A EMA50 saiu do gate em 2026-09-06 (custava 20% do fluxo por zero de
    acerto); fica declarada abaixo para o walk-forward poder desmentir isso."""
    return _ordem(row) and _poc(row)


#: As candidatas: as duas leituras, suas combinacoes, e -- no mesmo pe -- os
#: cortes que eu argumentei serem cauda e nao eixo. O PBO so mede a busca que
#: de fato aconteceu.
RULES = {
    "tudo (portas abertas)": lambda r: True,
    "ordem das linhas": _ordem,
    "ema50 por baixo": _ema50,
    "poc (sombra na linha)": _poc,
    "ordem + ema50": lambda r: _ordem(r) and _ema50(r),
    "BASE ordem + poc": _base,
    "base & lado do poc": lambda r: _base(r) and _poc_side(r),
    "base & media do rsi": lambda r: _base(r) and _rsi_ma(r),
    "base & lado + media": lambda r: _base(r) and _poc_side(r) and _rsi_ma(r),
    "base & lado & r>=0.5": lambda r: (_base(r) and _poc_side(r)
                                       and _n(r, "r_atr", 9e9) >= 0.5),
    "ordem + poc + ema50": lambda r: _base(r) and _ema50(r),
    # A segunda metade da leitura do usuario: perto da EMA50 e apoio, longe e
    # esticada. Foi vista na mesma tabela; entra como candidata, nao como fato.
    "base & dist<=1.5": lambda r: _base(r) and _n(r, "ema50_dist_atr", 9e9) <= 1.5,
    "base & dist<=2": lambda r: _base(r) and _n(r, "ema50_dist_atr", 9e9) <= 2.0,
    "dist<=2 (sem base)": lambda r: _n(r, "ema50_dist_atr", 9e9) <= 2.0,
    # --- os baldes que eu recomendei NAO usar, declarados ---------------
    "base & run>=4": lambda r: _base(r) and _n(r, "run_atr") >= 4,
    "base & idade>=16": lambda r: _base(r) and _n(r, "trend_age") >= 16,
    "base & r_atr 1-1.6": lambda r: _base(r) and 1.0 <= _n(r, "r_atr", 9e9) <= 1.6,
    "base & gap>=0.8": lambda r: _base(r) and _n(r, "line_gap_atr") >= 0.8,
    "base & pavio ema": lambda r: _base(r) and r.get("wick_line") == "ema",
    # --- o momento, rejeitado duas vezes, declarado uma terceira --------
    "base & rsi<70": lambda r: _base(r) and _n(r, "rsi", 0) < 70,
    "base & rsi>=50": lambda r: _base(r) and _n(r, "rsi", 0) >= 50,
    # --- o tempo grafico de cima ----------------------------------------
    "base & htf a favor": lambda r: _base(r) and _n(r, "htf_slope") >= 0,
    # --- um piso de r_atr, a lacuna que os stops perdedores expuseram ---
    # Varias entradas morriam com R = 0,06% do preco, onde o custo de giro
    # sozinho passa de 1R. Nao e um eixo de leitura, e uma sanidade.
    "base & r_atr>=0.5": lambda r: _base(r) and _n(r, "r_atr", 9e9) >= 0.5,
}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dataset")
    p.add_argument("--key", default="r3_h120")
    p.add_argument("--aggregate", default="mean", choices=("mean", "sum"))
    # H4 rende ~1100 entradas em ~7 anos: as janelas do `_wf` (60/20) deixariam
    # a maioria dos folds vazia. Trimestre de treino, mes de teste.
    p.add_argument("--train-days", type=int, default=180)
    p.add_argument("--test-days", type=int, default=60)
    a = p.parse_args()
    rows = [r for r in json.loads(Path(a.dataset).read_text())
            if r["arm"] != "aleatorio"]
    print(f"{len(rows)} entradas · {a.key} · agregacao {a.aggregate}\n")
    run_walkforward(rows, RULES, key=a.key, cost_pct=COST_PCT,
                    aggregate=a.aggregate, train_days=a.train_days,
                    test_days=a.test_days, step_days=a.test_days)


if __name__ == "__main__":
    main()
