"""A coluna `spread` da barra mente? Compara com o bid/ask tick a tick.

O custo de uma entrada e medido pela coluna `spread` do candle
(`research/_mt5.py`), um numero por barra. A documentacao do terminal nao diz
de que instante ele e -- abertura, minimo, ultimo tick -- e a diferenca so
importa quando o custo e grande em relacao ao R. No M5 de cambio ele e dois
tercos do R, entao passa a importar muito.

**A previsao, escrita antes de rodar:** espero que a coluna da barra seja o
spread do ULTIMO tick da barra, e que o spread do instante do gatilho seja
MAIOR que ela -- o setup dispara em movimento, e o spread abre em movimento.
Se for isso, a medicao vinha subestimando o custo e o M5 fica ainda mais
morto, enquanto o H4 nao se mexe.

O que este script NAO faz: substituir a coluna. Tick para 30 simbolos por
anos nao cabe em disco. Se a coluna estiver honesta na janela auditada, ela
serve para o resto da amostra; se nao estiver, o vies medido aqui e o que se
aplica.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics as st
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from liquidity_hunter.core.domain import TimeFrame

#: Quanto de uma barra conta como "o instante do gatilho". O setup entra no
#: fechamento da barra que dispara, entao a janela relevante e o fim dela.
TRIGGER_TAIL_SECONDS = 20

_MINUTES = {
    TimeFrame.M5: 5, TimeFrame.M15: 15, TimeFrame.M30: 30,
    TimeFrame.H1: 60, TimeFrame.H4: 240,
}


def load_ticks(path: Path) -> list[tuple[datetime, float, float]]:
    out = []
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            bid, ask = float(row["bid"]), float(row["ask"])
            # Tick sem uma das pontas nao mede spread nenhum.
            if bid <= 0 or ask <= 0:
                continue
            out.append((datetime.fromisoformat(row["time"]), bid, ask))
    return out


def audit(symbol: str, timeframe: TimeFrame, export: Path) -> dict | None:
    tick_path = export / f"{symbol.replace('.', '_')}_TICKS.csv"
    bar_path = export / f"{symbol.replace('.', '_')}_{timeframe.name}.csv"
    if not tick_path.exists() or not bar_path.exists():
        return None
    meta = json.loads((export / "meta.json").read_text(encoding="utf-8"))
    point = {s["symbol"]: s["point"] for s in meta["symbols"]}[symbol]

    ticks = load_ticks(tick_path)
    if not ticks:
        return None
    span = timedelta(minutes=_MINUTES[timeframe])
    first, last = ticks[0][0], ticks[-1][0]

    # Agrupa os ticks pela barra a que pertencem, e separa a cauda -- os
    # ultimos segundos, que e onde a entrada acontece.
    whole: dict[datetime, list[float]] = defaultdict(list)
    tail: dict[datetime, list[float]] = defaultdict(list)
    for time, bid, ask in ticks:
        opened = time - timedelta(
            seconds=(time - time.replace(hour=0, minute=0, second=0, microsecond=0))
            .total_seconds() % span.total_seconds()
        )
        spread = (ask - bid) / point
        whole[opened].append(spread)
        if (opened + span - time).total_seconds() <= TRIGGER_TAIL_SECONDS:
            tail[opened].append(spread)

    pairs = []
    with bar_path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            opened = datetime.fromisoformat(row["time"])
            if not (first <= opened <= last) or opened not in whole:
                continue
            pairs.append((
                int(row["spread"]),
                st.fmean(whole[opened]),
                min(whole[opened]),
                max(whole[opened]),
                st.fmean(tail[opened]) if tail.get(opened) else None,
            ))
    if not pairs:
        return None
    with_tail = [p for p in pairs if p[4] is not None]
    return {
        "symbol": symbol, "n": len(pairs),
        "coluna": st.median(p[0] for p in pairs),
        "tick_medio": st.median(p[1] for p in pairs),
        "tick_min": st.median(p[2] for p in pairs),
        "tick_max": st.median(p[3] for p in pairs),
        "no_gatilho": st.median(p[4] for p in with_tail) if with_tail else float("nan"),
        # A razao e a leitura que importa: >1 significa que a coluna
        # SUBESTIMA o que a entrada realmente paga.
        "razao": (
            st.median(p[4] / p[0] for p in with_tail if p[0] > 0)
            if any(p[0] > 0 for p in with_tail) else float("nan")
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--export", default="/mnt/c/mt5-export")
    parser.add_argument("--symbols", nargs="*")
    args = parser.parse_args()

    export = Path(args.export)
    timeframe = TimeFrame(args.timeframe)
    symbols = args.symbols or sorted(
        p.name.replace("_TICKS.csv", "").replace("_", ".")
        for p in export.glob("*_TICKS.csv")
    )
    print(f"\n{'=' * 78}\nSPREAD: coluna da barra vs tick real   {timeframe.value.upper()}"
          f"   (em points)\n{'=' * 78}")
    print(f"  {'simbolo':<10}{'barras':>8}{'coluna':>9}{'tick med':>10}"
          f"{'min':>8}{'max':>8}{'no gatilho':>12}{'razao':>8}")
    rows = [r for s in symbols if (r := audit(s, timeframe, export))]
    for r in rows:
        print(f"  {r['symbol']:<10}{r['n']:>8}{r['coluna']:>9.1f}{r['tick_medio']:>10.1f}"
              f"{r['tick_min']:>8.1f}{r['tick_max']:>8.1f}{r['no_gatilho']:>12.1f}"
              f"{r['razao']:>8.2f}")
    if rows:
        good = [r["razao"] for r in rows if r["razao"] == r["razao"]]
        if good:
            print(f"\n  razao mediana entre simbolos: {st.median(good):.2f}"
                  f"   (>1 = a coluna subestima o que a entrada paga)")
    else:
        print("\n  nenhum par (ticks, barras) encontrado -- exporte com "
              "`mt5_export.py --ticks` primeiro")


if __name__ == "__main__":
    main()
