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
    TimeFrame.M5: 60_000,
    TimeFrame.M15: 60_000, TimeFrame.M30: 60_000,
    TimeFrame.H1: 45_000, TimeFrame.H4: 12_000,
}


def attach_spread(rows: list[dict], timeframe: TimeFrame, export: Path) -> list[dict]:
    """Cada entrada paga o spread da barra em que ela disparou."""
    provider = MT5CsvProvider(export)
    meta = json.loads((export / "meta.json").read_text(encoding="utf-8"))
    points = {s["symbol"]: s["point"] for s in meta["symbols"]}
    by_symbol: dict[str, dict[str, float]] = {}
    out = []
    for row in rows:
        symbol = row["symbol"]
        if symbol not in by_symbol:
            by_symbol[symbol] = provider.spread_pct_at(symbol, timeframe, points[symbol])
        spread = by_symbol[symbol].get(row["timestamp"])
        if spread is None:
            continue
        row["spread_pct"] = spread
        # Ida e volta paga um spread cheio (entra no ask, sai no bid).
        row["cost_r"] = spread / row["r_pct"]
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
    args = parser.parse_args()

    timeframe = TimeFrame(args.timeframe)
    export = Path(args.export)
    out = f"research/.datasets/ftmo_{timeframe.value}.json"
    Path(out).parent.mkdir(parents=True, exist_ok=True)

    if args.report_only:
        rows = json.loads(Path(out).read_text())
    else:
        available = [s for s in args.symbols if MT5CsvProvider(export).path(s, timeframe).exists()]
        missing = [s for s in args.symbols if s not in available]
        if missing:
            print(f"nao exportados (pulando): {', '.join(missing)}\n")
        rows = scan(available, timeframe, LIMITS[timeframe], out,
                    provider=MT5CsvProvider(export),
                    gates=UNMEASURED_GATES.get(timeframe))
        rows = attach_spread(rows, timeframe, export)
        Path(out).write_text(json.dumps(rows))
    report(rows, timeframe, 0)


if __name__ == "__main__":
    main()
