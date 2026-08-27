"""O Block Reclaim nos indices e no petroleo da corretora -- o instrumento real.

Ate aqui os dois lados estavam separados: o custo foi medido no CFD que se
opera (`research/index_cost.py`) e o setup foi medido em outro ativo
(`research/equity_reclaim.py`, acao americana). Este script junta os dois no
mesmo lugar: o setup roda no proprio CFD, e cada entrada paga **o spread da
sua propria barra**, lido do feed da corretora.

Mesma medicao de sempre (`quality_features.scan` so troca de provider),
mesmos gates de producao, alvo 2R.

Duas coisas que o numero daqui nao cobre, e que nenhum resultado apaga:

* **A VWAP e pesada por tick volume**, nao por contratos -- um CFD nao
  publica volume real. Ver `research/_mt5.py` para por que isso e
  defensavel e por que continua sendo uma aproximacao.
* **Simbolo nao e aposta.** US500/US100/US30/US2000 se movem quase juntos, e
  a Europa idem. Quinze tickers aqui valem talvez cinco apostas
  independentes, entao o `n` total superestima a informacao que existe. O
  relatorio quebra por bloco geografico exatamente por isso.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from datetime import datetime
from pathlib import Path

from liquidity_hunter.core.domain import TimeFrame
from research._mt5 import FTMO_INDICES, MT5CsvProvider
from research.quality_features import scan

MAIN = "r2_h40"

#: Onde cada ticker aposta. Dentro de um bloco os ativos sao quase o mesmo
#: trade; entre blocos, nao.
BLOCS: dict[str, tuple[str, ...]] = {
    "EUA": ("US500.cash", "US100.cash", "US30.cash", "US2000.cash"),
    "Europa": ("GER40.cash", "UK100.cash", "FRA40.cash", "EU50.cash",
               "SPN35.cash", "N25.cash"),
    "Asia/Pacifico": ("AUS200.cash", "JP225.cash", "HK50.cash"),
    "Petroleo": ("USOIL.cash", "UKOIL.cash"),
}

#: O M5 nao tem gate calibrado -- `OPERATING_GATES` para no M15, porque o M5
#: foi medido e rejeitado em cripto (o custo comia o R). Aqui ele roda com o
#: `r_atr <= 1.0`, que e o gate universal validado em todo timeframe, e com
#: piso de acumulacao da VWAP igual a 1, ou seja, NENHUM: o piso e por
#: timeframe e para o M5 nao existe medido. Escolher um numero aqui seria
#: ajustar; declarar que nao ha e honesto, e so pode SUBESTIMAR o resultado.
UNMEASURED_GATES = {TimeFrame.M5: (1.0, 1)}

LIMITS = {
    TimeFrame.M5: 120_000,
    TimeFrame.M15: 120_000, TimeFrame.M30: 120_000,
    TimeFrame.H1: 45_000, TimeFrame.H4: 12_000,
}


#: **A coluna `spread` da barra e o MINIMO do periodo, nao a media nem a do
#: fechamento.** Medido contra o bid/ask tick a tick (`research/spread_audit.py`):
#: a coluna bate com o minimo dos ticks em 99,0-99,9% das barras e com a media
#: em 0,3-10,6%. Cobrar essa coluna e cobrar o melhor caso de cada barra.
#:
#: **Mas so importa onde o spread FLUTUA, e ele nao flutua em todo lugar.**
#: Medido por classe, e o resultado separou as tres:
#:
#: * **Indice: fixo.** Em 6 simbolos x ~2.800 barras de M5, `min == max ==
#:   coluna` (US500 60 points, GER40 133, JP225 1000, USOIL 68, N25 60); so o
#:   US100 varia, e pouco. Fator **1,0** -- nao ha o que corrigir.
#: * **Cripto: fixo na maioria.** Dos 7 CFD auditados, 4 nao variam nada
#:   (SOL, VEC, MAN, DOGE) e a mediana entre simbolos e 1,00. BNBUSD e o
#:   outlier (2,6x), mas sobre o menor spread da lista (0,0015%).
#: * **Cambio: flutua.** Aqui sim a coluna subestima, e o fator abaixo
#:   corrige para o spread no INSTANTE DA ENTRADA (os ultimos 20 segundos da
#:   barra que dispara, onde a ordem vai). Ele cresce com o timeframe pelo
#:   motivo mecanico esperado: barra maior tem mais ticks, entao o minimo
#:   afunda mais.
#:
#: A licao e que o fator NAO e propriedade do terminal -- o mecanismo (a
#: coluna e o minimo) e, mas a magnitude e de como a corretora cota aquele
#: instrumento. Aplicar o numero do cambio a indice foi exatamente o erro que
#: a medicao seguinte desfez.
FOREX_SPREAD_UNDERESTIMATE: dict[TimeFrame, float] = {
    TimeFrame.M5: 1.29, TimeFrame.M15: 1.34, TimeFrame.M30: 1.38,
    TimeFrame.H1: 1.42, TimeFrame.H4: 1.45,
}

#: Coeficiente de swap da sexta-feira na ficha dos indices: a virada de sexta
#: cobra tres noites de uma vez (fim de semana). As demais cobram uma.
FRIDAY_SWAP_COEFFICIENT = 3
#: Ate onde procurar a resolucao de uma entrada, para contar quantas viradas
#: ela atravessa. Alem disso a posicao ja nao e a mesma operacao.
RESOLVE_SCAN_BARS = 200


def _nights_held(
    candles: list[dict], start: int, row: dict, triple_weekday: int = 4
) -> float:
    """Viradas de dia entre a entrada e a resolucao, com a virada tripla pesando tres.

    A duracao nao e assumida: a entrada, o stop e o alvo 2R estao na linha, e
    o caminho a frente esta no CSV, entao a posicao e resolvida vela a vela
    como no proprio backtest.
    """
    bullish = row["direction"] == "bullish"
    entry, stop = row["entry"], row["stop"]
    target = entry + 2 * (entry - stop) if bullish else entry - 2 * (stop - entry)
    end = min(start + RESOLVE_SCAN_BARS, len(candles) - 1)
    for i in range(start + 1, end + 1):
        high, low = float(candles[i]["high"]), float(candles[i]["low"])
        hit_stop = low <= stop if bullish else high >= stop
        hit_target = high >= target if bullish else low <= target
        if hit_stop or hit_target:
            end = i
            break
    nights = 0.0
    for i in range(start, end):
        day = datetime.fromisoformat(candles[i]["time"])
        if datetime.fromisoformat(candles[i + 1]["time"]).date() != day.date():
            nights += FRIDAY_SWAP_COEFFICIENT if day.weekday() == triple_weekday else 1
    return nights


def attach_costs(
    rows: list[dict], timeframe: TimeFrame, export: Path, triple_weekday: int = 4,
    spread_factor: float = 1.0,
) -> list[dict]:
    """Cada entrada paga o spread da SUA barra, mais o swap das noites que dormiu.

    O swap e cobrado **em pontos** e e fortemente assimetrico: no US30 a compra
    paga 1173 pontos por noite e a venda RECEBE 50, no UK100 e o contrario. Uma
    media entre os dois lados apagaria justamente isso, entao cada entrada paga
    o lado dela. A sexta cobra tres noites.

    Custa pouco no total porque quase nada dorme -- a duracao mediana e de 3 a
    4 velas e so 2% (M5) a 14% (M15) das entradas atravessam a virada -- mas o
    caso ruim existe: uma compra que atravessa a sexta pagou 0,72R numa
    operacao so.
    """
    provider = MT5CsvProvider(export)
    meta = json.loads((export / "meta.json").read_text(encoding="utf-8"))
    info = {s["symbol"]: s for s in meta["symbols"]}
    spreads: dict[str, dict[str, float]] = {}
    series: dict[str, tuple[dict[str, int], list[dict]]] = {}
    out = []
    for row in rows:
        symbol = row["symbol"]
        if symbol not in info:
            continue
        point = info[symbol]["point"]
        if symbol not in spreads:
            spreads[symbol] = provider.spread_pct_at(symbol, timeframe, point)
            candles = provider.rows(symbol, timeframe)
            series[symbol] = ({c["time"]: i for i, c in enumerate(candles)}, candles)
        spread = spreads[symbol].get(row["timestamp"])
        index, candles = series[symbol]
        start = index.get(row["timestamp"])
        if spread is None or start is None:
            continue
        swap_points = (
            info[symbol]["swap_long"]
            if row["direction"] == "bullish"
            else info[symbol]["swap_short"]
        )
        nights = _nights_held(candles, start, row, triple_weekday)
        # Swap negativo = a corretora cobra. Em R contra a distancia do stop,
        # a mesma unidade do resto.
        swap_cost = -nights * swap_points * point / abs(row["entry"] - row["stop"])
        # O que a entrada paga de verdade, e nao o melhor caso da barra --
        # onde o spread flutua. Onde ele e fixo o fator e 1,0 e isto e no-op.
        spread *= spread_factor
        row["spread_pct"] = spread
        row["swap_r"] = swap_cost
        row["nights"] = nights
        # Ida e volta paga um spread cheio (entra no ask, sai no bid).
        row["cost_r"] = spread / row["r_pct"] + swap_cost
        out.append(row)
    return out


def line(rows: list[dict], label: str, width: int = 26) -> None:
    if not rows:
        print(f"  {label:<{width}}      --")
        return
    hit = sum(1 for r in rows if r[MAIN] > 0) / len(rows)
    gross = st.fmean(r[MAIN] for r in rows)
    cost = st.fmean(r["cost_r"] for r in rows)
    print(
        f"  {label:<{width}}{len(rows):>6}{hit:>9.1%}{gross:>10.3f}"
        f"{cost:>9.3f}{gross - cost:>10.3f}{(gross - cost) * len(rows):>9.1f}"
    )


def report(rows: list[dict], timeframe: TimeFrame, months: float) -> None:
    head = (
        f"  {'':<26}{'n':>6}{'acerto 2R':>9}{'bruto':>10}"
        f"{'custo':>9}{'liquido':>10}{'total':>9}"
    )
    print(f"\n{'=' * 82}\n{timeframe.value.upper()}   alvo 2R   horizonte 40 velas   "
          f"custo = spread da propria barra\n{'=' * 82}")
    print(head)
    line(rows, "TODOS")
    print()
    for bloc, symbols in BLOCS.items():
        line([r for r in rows if r["symbol"] in symbols], f"bloco: {bloc}")
    print()
    for symbol in sorted({r["symbol"] for r in rows}):
        line([r for r in rows if r["symbol"] == symbol], symbol)
    print()
    stamps = sorted(r["timestamp"] for r in rows)
    if stamps:
        cut = stamps[int(0.6 * len(stamps))]
        line([r for r in rows if r["timestamp"] < cut], f"calendario: ate {cut[:10]}")
        line([r for r in rows if r["timestamp"] >= cut], f"calendario: de {cut[:10]}")
        span = len(set(s[:7] for s in stamps))
        print(f"\n  {len(rows)} entradas em {span} meses  ->  {len(rows) / span:.1f} por mes "
              f"no conjunto todo")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--symbols", nargs="*", default=list(FTMO_INDICES))
    parser.add_argument("--export", default="/mnt/c/mt5-export")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--recost", action="store_true",
                        help="reprecifica a base salva sem repetir a varredura")
    args = parser.parse_args()

    timeframe = TimeFrame(args.timeframe)
    export = Path(args.export)
    out = f"research/.datasets/ftmo_{timeframe.value}.json"
    Path(out).parent.mkdir(parents=True, exist_ok=True)

    if args.report_only or args.recost:
        rows = json.loads(Path(out).read_text())
        if args.recost:
            rows = attach_costs(rows, timeframe, export)
            Path(out).write_text(json.dumps(rows))
    else:
        available = [s for s in args.symbols if MT5CsvProvider(export).path(s, timeframe).exists()]
        missing = [s for s in args.symbols if s not in available]
        if missing:
            print(f"nao exportados (pulando): {', '.join(missing)}\n")
        rows = scan(available, timeframe, LIMITS[timeframe], out,
                    provider=MT5CsvProvider(export),
                    gates=UNMEASURED_GATES.get(timeframe))
        rows = attach_costs(rows, timeframe, export)
        Path(out).write_text(json.dumps(rows))
    report(rows, timeframe, 0)


if __name__ == "__main__":
    main()
