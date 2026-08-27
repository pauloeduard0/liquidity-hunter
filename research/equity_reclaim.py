"""O Block Reclaim em acao americana: a VWAP onde ela e institucional de verdade.

Mesmo codigo de medicao da varredura de cripto (`research/quality_features.py`
-- `scan` so troca de provider), mesmos gates de producao, mesmo alvo 2R. O
que muda e a classe de ativo, e a pergunta e uma so:

    o efeito da VWAP e MAIOR onde a VWAP e um ponto de Schelling mais forte?

Em cripto a VWAP e uma convencao: cada corretora tem a sua, a ancora de 00:00
UTC nao e a abertura de nada, e o mercado nao dorme. Em acao americana o tape
e consolidado, a ancora e a abertura do pregao para todo mundo, e a VWAP de
sessao e o benchmark contra o qual execucao institucional e avaliada. Se
`project_vwap_schelling_point` esta certo -- o valor vem da observacao
compartilhada, nao do break-even -- o numero aqui tem que subir.

O custo e outro, e por isso o relatorio mostra tres. Acao liquida americana
nao tem comissao no varejo e o spread e de 0,1 a 3 bp, contra os 10 bp de ida
e volta taker da Binance sob os quais o setup foi validado.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

from liquidity_hunter.core.domain import TimeFrame
from research._equities import UNIVERSE, YahooEquityProvider
from research._symbols import sample_of
from research.quality_features import scan

#: Tres reguas de custo, ida e volta, em fracao do nocional. A do meio e a
#: leitura honesta para nome liquido; a da Binance esta ali para a comparacao
#: entre classes ser contra o custo sob o qual o setup foi aprovado.
COSTS = {
    "SPY/QQQ (0,5 bp)": 0.00005,
    "acao liquida (3 bp)": 0.0003,
    "cripto Binance (10 bp)": 0.0010,
}

#: Quantos candles visiveis pedir. O teto e da fonte: H1 vai a ~5000 barras
#: (730 dias de pregao), M15 a ~1560 (60 dias).
LIMITS = {TimeFrame.H1: 4500, TimeFrame.M30: 1200, TimeFrame.M15: 1200}

MAIN = "r2_h40"


def summarize(rows: list[dict], label: str) -> None:
    if not rows:
        print(f"{label}: nenhuma entrada")
        return
    hit = sum(1 for r in rows if r[MAIN] > 0) / len(rows)
    gross = st.fmean(r[MAIN] for r in rows)
    print(f"\n=== {label}   n={len(rows)}   acerto 2R {hit:.1%}   bruto {gross:+.3f}R")
    for name, cost in COSTS.items():
        net = st.fmean(r[MAIN] - cost / r["r_pct"] for r in rows)
        drag = st.fmean(cost / r["r_pct"] for r in rows)
        print(f"    {name:<24} custo {drag:.3f}R    liquido {net:+.3f}R")


def report(rows: list[dict]) -> None:
    summarize(rows, "todos")
    # Os mesmos quatro cortes independentes dos estudos de cripto: metade dos
    # nomes por hash (busca/holdout) e metade do calendario (cedo/tarde). Um
    # achado que so vive num deles nao e achado.
    for sample in ("search", "holdout"):
        summarize([r for r in rows if sample_of(r["symbol"]) == sample], f"nomes: {sample}")
    stamps = sorted(r["timestamp"] for r in rows)
    cut = stamps[int(0.6 * len(stamps))]
    summarize([r for r in rows if r["timestamp"] < cut], "calendario: cedo")
    summarize([r for r in rows if r["timestamp"] >= cut], "calendario: tarde")
    # Os ativos que motivaram o estudo, olhados por ultimo e sem promessa: sao
    # 4 nomes correlacionados dentro de 100, e a amostra deles nao decide nada.
    etfs = [r for r in rows if r["symbol"] in ("SPY", "QQQ", "DIA", "IWM")]
    summarize(etfs, "so os ETF de indice (amostra pequena, nao decide)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--symbols", nargs="*", default=list(UNIVERSE))
    parser.add_argument("--out", default=None)
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()

    timeframe = TimeFrame(args.timeframe)
    out = args.out or f"research/.datasets/eq_{timeframe.value}.json"
    Path(out).parent.mkdir(parents=True, exist_ok=True)

    if args.report_only:
        rows = json.loads(Path(out).read_text())
    else:
        rows = scan(
            args.symbols, timeframe, LIMITS[timeframe], out,
            provider=YahooEquityProvider(),
        )
    report(rows)


if __name__ == "__main__":
    main()
