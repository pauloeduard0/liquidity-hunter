"""ATR% e custo em R nos indices e no petroleo, antes de construir qualquer coisa.

Esta e a conta que decidiu tudo em cripto e que decide aqui: o custo de uma
operacao, medido em R, e

    custo_R = custo_% / r_%

O percentual arriscado cancela; quem manda e a **distancia do stop**. Foi
assim que o M5 morreu (`project_block_reclaim_m5_rejected`: "o custo e % do
PRECO, o que ele come e % do R -- descer de TF cobra no DENOMINADOR"), e a
mesma conta e a que diz se indice de CFD e operavel.

O `r_%` nao e chutado: na amostra de cripto, dentro do gate `r_atr <= 1.0`, o
stop mede 0,79-0,81 x ATR nos quatro timeframes. Esse fator, aplicado ao ATR%
medido aqui, da o `r_%` que o setup teria neste ativo. E uma projecao, nao uma
medicao do setup -- o setup so roda depois, e so se esta conta passar.

O custo vem do proprio feed: a coluna `spread` do MT5, em points, no
instrumento que a corretora vende. Uma ida e volta paga um spread cheio
(entra no ask, sai no bid). Comissao de indice na corretora e zero; se nao
for, `--commission-pct` soma por cima.

Entrada: os CSV de `research/mt5_export.py`.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics as st
from pathlib import Path

#: O fator medido em cripto: quanto do ATR o stop do setup ocupa, dentro do
#: gate `r_atr <= 1.0`. Medido em 4226 operacoes, M15 0,785 / M30 0,807 /
#: H1 0,814 / H4 0,789.
R_OVER_ATR = 0.80

#: Ida e volta taker na Binance, o custo sob o qual o setup foi validado em
#: cripto. Serve de regua: o numero do indice so significa alguma coisa ao
#: lado deste.
CRYPTO_COST_R = {"M15": 0.0010 / 0.00407, "M30": 0.0010 / 0.00577,
                 "H1": 0.0010 / 0.00925, "H4": 0.0010 / 0.02086}

ATR_PERIOD = 14
TIMEFRAMES = ("M15", "M30", "H1", "H4")


def load(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [
            {
                "time": row["time"],
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "spread": int(row["spread"]),
                "tick_volume": int(row["tick_volume"]),
                "real_volume": int(row["real_volume"]),
            }
            for row in csv.DictReader(handle)
        ]


def atr_pct_series(rows: list[dict]) -> list[float]:
    """ATR(14) como percentual do preco, por barra.

    A media movel simples do true range, a mesma que `_local_atr` usa em
    producao -- comparar contra um ATR de Wilder mudaria o numero sem mudar
    o instrumento.
    """
    trs: list[float] = []
    out: list[float] = []
    prev_close = rows[0]["close"]
    for row in rows:
        tr = max(
            row["high"] - row["low"],
            abs(row["high"] - prev_close),
            abs(row["low"] - prev_close),
        )
        trs.append(tr)
        prev_close = row["close"]
        if len(trs) >= ATR_PERIOD and row["close"] > 0:
            out.append(st.fmean(trs[-ATR_PERIOD:]) / row["close"])
    return out


def hourly_profile(export: Path, symbol: str, points: dict) -> None:
    """Onde mora a cauda do spread.

    Uma p90 muito acima da mediana pode ser duas coisas muito diferentes: o
    spread abrindo no noticiario (que te pega, porque e quando o gatilho
    dispara) ou o instrumento fora do horario do seu proprio pregao (que nao
    te pega, porque o setup nao deveria operar ali). A hora do dia separa as
    duas, e a diferenca decide se o custo p90 e um problema real.
    """
    path = export / f"{symbol.replace('.', '_')}_M15.csv"
    rows = load(path)
    point = points[symbol]
    by_hour: dict[int, list[float]] = {}
    for row in rows:
        if row["close"] <= 0:
            continue
        hour = int(row["time"][11:13])
        by_hour.setdefault(hour, []).append(row["spread"] * point / row["close"])
    print(f"spread mediano por hora (UTC), M15 -- {symbol}")
    for hour in sorted(by_hour):
        values = by_hour[hour]
        bar = "#" * int(st.median(values) / 0.0001)
        print(f"  {hour:02d}h  {st.median(values):>8.4%}  n={len(values):>5}  {bar}")
    print()


def report(export: Path, commission_pct: float) -> None:
    meta = json.loads((export / "meta.json").read_text(encoding="utf-8"))
    points = {s["symbol"]: s["point"] for s in meta["symbols"]}
    descriptions = {s["symbol"]: s["description"] for s in meta["symbols"]}

    print(f"custo = 1 spread cheio (ida e volta) + comissao {commission_pct:.3%}")
    print(f"r_% projetado = {R_OVER_ATR} x ATR%   (fator medido em cripto, dentro do gate)\n")

    header = (
        f"{'ativo':<12}{'TF':<5}{'barras':>8}{'ATR%':>8}{'spread%':>9}"
        f"{'p90':>9}{'custo R':>9}{'p90 R':>8}{'cripto R':>10}"
    )
    for symbol in points:
        print(f"{descriptions[symbol]}  [{symbol}]")
        print(header)
        for timeframe in TIMEFRAMES:
            path = export / f"{symbol.replace('.', '_')}_{timeframe}.csv"
            if not path.exists():
                continue
            rows = load(path)
            atrs = atr_pct_series(rows)
            if not atrs:
                continue
            # Spread em points -> fracao do preco, barra a barra: o spread e
            # flutuante e o preco anda, entao a mediana da razao e a leitura,
            # nao a razao das medianas.
            point = points[symbol]
            spreads = [
                row["spread"] * point / row["close"] for row in rows if row["close"] > 0
            ]
            atr_pct = st.median(atrs)
            spread_pct = st.median(spreads)
            # A mediana esconde exatamente as barras em que voce e executado:
            # o spread do CFD abre no noticiario e na abertura do pregao, que
            # e quando o gatilho dispara. A p90 e a leitura pessimista honesta.
            spread_p90 = sorted(spreads)[int(0.90 * len(spreads))]
            r_pct = R_OVER_ATR * atr_pct
            def in_r(cost: float, r_pct: float = r_pct) -> float:
                return (cost + commission_pct) / r_pct if r_pct > 0 else float("nan")
            print(
                f"{'':<12}{timeframe:<5}{len(rows):>8}{atr_pct:>7.3%}{spread_pct:>9.4%}"
                f"{spread_p90:>9.4%}{in_r(spread_pct):>9.3f}{in_r(spread_p90):>8.3f}"
                f"{CRYPTO_COST_R[timeframe]:>10.3f}"
            )
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", default="/mnt/c/mt5-export")
    parser.add_argument("--by-hour", nargs="*", default=[], help="perfil horario do spread")
    parser.add_argument(
        "--commission-pct",
        type=float,
        default=0.0,
        help="comissao ida e volta em fracao do nocional (indice na FTMO: 0)",
    )
    args = parser.parse_args()
    export = Path(args.export)
    report(export, args.commission_pct)
    if args.by_hour:
        meta = json.loads((export / "meta.json").read_text(encoding="utf-8"))
        points = {s["symbol"]: s["point"] for s in meta["symbols"]}
        for symbol in args.by_hour:
            hourly_profile(export, symbol, points)


if __name__ == "__main__":
    main()
