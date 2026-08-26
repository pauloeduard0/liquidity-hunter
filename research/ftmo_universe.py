"""O setup nos ativos que a FTMO lista como Crypto CFD, e o que custa la.

Recorte, nao medicao nova. As entradas vem das varreduras de
`research/quality_features.py` (uma por timeframe, com a regra de producao
ligada onde ela e wired: `MAX_BLOCK_PENETRATION` em M15/M30/H1, H4 sem). Este
arquivo so filtra os simbolos, mede o tempo de permanencia e re-precifica o
resultado sob o modelo de custo da corretora.

Por que existe
--------------
A pergunta era "compensa operar so nesses 30 ativos, pela FTMO, arriscando
0,25% por trade?". Ela tem tres partes independentes, e a segunda e a que
quase passou batido:

1. **O recorte de ativos muda a assertividade?** Nao. 28 dos 30 estao na
   amostra medida (XMRUSD e BCHUSD nao sao perpetuos USDT na Binance e nunca
   foram medidos), e o acerto deles bate o do universo inteiro em todos os
   timeframes. O que muda e o volume: sobram ~39% das operacoes.

2. **O swap de -30% ao ano quebra a conta?** Nao, porque a **permanencia
   mediana e de 4 a 5 velas** -- uma hora no M15, dezesseis no H4. Quase
   nenhuma posicao atravessa o rollover, entao o swap custa de 0,011R a
   0,034R. Era o numero mais assustador da tela da corretora e e o menor da
   conta; a comissao e que decide.

3. **O 0,25% de risco muda o custo?** Nao muda nada, e vale entender porque:
   o custo em R e `comissao_percentual / r_pct`, e o percentual arriscado
   cancela -- tanto o notional quanto a comissao escalam com ele. Quem decide
   o custo em R e a **distancia do stop**. Por isso o M15 paga 5x mais que o
   H4 pela mesma regra, e por isso, se o custo apertar, o caminho e subir de
   timeframe e nao reduzir risco.

O que este arquivo NAO sabe
---------------------------
A medicao e sobre **perpetuos USDT da Binance**; a FTMO vende **CFD**. O preco
de referencia acompanha, mas o *spread* do CFD nao entra em lugar nenhum desta
conta -- so a comissao anunciada. Se houver spread alem dela, some ao custo.
Esse e o buraco real da estimativa, e nenhum numero aqui o cobre.

E nao esta estabelecido se os 0,065% sao por lado ou ida e volta. Os dois
cenarios saem lado a lado; o pessimista (0,13% no total) e o que deve ser lido
enquanto isso nao for confirmado com a corretora.

Rodar
-----
    poetry run python -m research.quality_features --timeframe 15m --out /tmp/qf2.json
    (idem para 30m, 1h, 4h)
    poetry run python -m research.ftmo_universe
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import statistics as st
from pathlib import Path
from typing import Any

from liquidity_hunter.core.domain import TimeFrame
from liquidity_hunter.data.providers.binance import klines_row_to_candle
from research._paginated import CACHE_DIR, strip_dead_tail
from research._symbols import UNIVERSE, sample_of
from research.quality_features import MAIN_TARGET, metrics

#: O ticker da corretora -> o simbolo medido. `XMRUSD` e `BCHUSD` ficam de
#: fora: nao sao perpetuos USDT na Binance, entao nunca entraram na amostra.
#: Ficam listados para o recorte ser auditavel -- 28 de 30, nao "os 28".
FTMO_CRYPTO: dict[str, str] = {
    "BTCUSD": "BTCUSDT", "DASHUSD": "DASHUSDT", "ETHUSD": "ETHUSDT",
    "LTCUSD": "LTCUSDT", "XRPUSD": "XRPUSDT", "XMRUSD": "XMRUSDT",
    "NEOUSD": "NEOUSDT", "ADAUSD": "ADAUSDT", "DOTUSD": "DOTUSDT",
    "DOGEUSD": "DOGEUSDT", "SOLUSD": "SOLUSDT", "AVAUSD": "AVAXUSDT",
    "BCHUSD": "BCHUSDT", "ETCUSD": "ETCUSDT", "BNBUSD": "BNBUSDT",
    "SANUSD": "SANDUSDT", "LNKUSD": "LINKUSDT", "NERUSD": "NEARUSDT",
    "ALGUSD": "ALGOUSDT", "ICPUSD": "ICPUSDT", "AAVUSD": "AAVEUSDT",
    "BARUSD": "HBARUSDT", "GALUSD": "GALAUSDT", "GRTUSD": "GRTUSDT",
    "IMXUSD": "IMXUSDT", "MANUSD": "MANAUSDT", "VECUSD": "VETUSDT",
    "XLMUSD": "XLMUSDT", "UNIUSD": "UNIUSDT", "XTZUSD": "XTZUSDT",
}

#: Varredura por timeframe: (rotulo, arquivo, a regra de profundidade e wired
#: neste TF?, minutos por vela). O H4 entra sem o gate porque `passes_gates`
#: nao o aplica la -- foi medido e reprovado na regua diaria.
SCANS: tuple[tuple[str, str, bool, int], ...] = (
    ("M15", "/tmp/qf2.json", True, 15),
    ("M30", "/tmp/qf_m30.json", True, 30),
    ("H1", "/tmp/qf_h1.json", True, 60),
    ("H4", "/tmp/qf_h4.json", False, 240),
)
_TF_OF = {"M15": "15m", "M30": "30m", "H1": "1h", "H4": "4h"}

#: O teto de profundidade que `paper_journal.MAX_BLOCK_PENETRATION` aplica.
MAX_PENETRATION = 0.5
#: Custo do estudo, para as tabelas continuarem comparaveis com o resto.
STUDY_COST = 0.0010
#: O anunciado na ficha do instrumento (BTCUSD, Spot CFD), em fracao.
FTMO_COMMISSION = 0.00065
#: Swap anunciado: -30, tipo "percentage". Lido como -30% ao ano sobre o
#: notional, cobrado no rollover.
FTMO_SWAP_YEAR = 0.30
#: Limites da conta, para o resultado sair em % em vez de so em R.
RISK_PER_TRADE = 0.0025
FTMO_MAX_DRAWDOWN = 0.10
FTMO_MAX_DAILY_LOSS = 0.05
FTMO_PROFIT_TARGET = 0.10


def load(path: str, gated: bool) -> list[dict[str, Any]]:
    rows = json.loads(Path(path).read_text())
    if not gated:
        return rows
    return [
        r for r in rows
        if (9.0 if r["pen_pct"] is None else r["pen_pct"]) < MAX_PENETRATION
    ]


def holding_bars(rows: list[dict], tf_label: str, horizon: int = 120) -> list[int]:
    """Velas ate a posicao resolver (alvo ou stop), lidas do cache de klines.

    Sem re-rodar o pipeline: as linhas ja trazem entrada, stop e direcao, e o
    caminho a frente esta no cache. Uma linha cujo candle nao for encontrado e
    pulada em vez de estimada -- o numero que interessa e a mediana, e inventar
    a cauda para completa-la seria pior que uma amostra menor.
    """
    tf = _TF_OF[tf_label]
    by_symbol: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        by_symbol[row["symbol"]].append(row)
    bars: list[int] = []
    for symbol, group in by_symbol.items():
        path = CACHE_DIR / f"{symbol}_{tf}.json"
        if not path.exists():
            continue
        raw, _ = strip_dead_tail(json.loads(path.read_text()))
        candles = [klines_row_to_candle(symbol, TimeFrame(tf), row) for row in raw]
        index = {c.timestamp.isoformat(): i for i, c in enumerate(candles)}
        for row in group:
            i0 = index.get(row["timestamp"])
            if i0 is None:
                continue
            bull = row["direction"] == "bullish"
            entry, stop = row["entry"], row["stop"]
            r = abs(entry - stop)
            target = entry + MAIN_TARGET * r if bull else entry - MAIN_TARGET * r
            for k, c in enumerate(candles[i0 + 1 : i0 + 1 + horizon], 1):
                hit_stop = (c.low <= stop) if bull else (c.high >= stop)
                hit_target = (c.high >= target) if bull else (c.low <= target)
                if hit_stop or hit_target:
                    bars.append(k)
                    break
            else:
                bars.append(horizon)
    return bars


def cost_in_r(row: dict, commission: float, swap_days: float) -> float:
    """Custo de um round trip em R.

    `commission / r_pct` e a conta inteira, e e onde mora a assimetria entre
    timeframes: a comissao e uma fracao do PRECO e o R tambem, entao um stop
    apertado paga a mesma taxa em muito mais R.
    """
    swap = FTMO_SWAP_YEAR / 365 * swap_days
    return (commission + swap) / row["r_pct"]


def report(subset_only: bool = True) -> None:
    wanted = {v for v in FTMO_CRYPTO.values() if v in UNIVERSE}
    missing = sorted(k for k, v in FTMO_CRYPTO.items() if v not in UNIVERSE)
    print(f"{len(wanted)} de {len(FTMO_CRYPTO)} tickers na amostra medida "
          f"(fora: {', '.join(missing)})\n")

    scans: dict[str, list[dict]] = {}
    hold: dict[str, float] = {}
    for label, path, gated, _minutes in SCANS:
        rows = load(path, gated)
        scans[label] = [r for r in rows if not subset_only or r["symbol"] in wanted]

    print("=== permanencia ate resolver (alvo ou stop)")
    for label, _path, _gated, minutes in SCANS:
        bars = holding_bars(scans[label], label)
        if not bars:
            continue
        days = st.median(bars) * minutes / 1440
        hold[label] = st.fmean(bars) * minutes / 1440
        print(f"  {label:4} n={len(bars):5d}  mediana {st.median(bars):4.1f} velas "
              f"= {days:5.2f} dias  ·  media {st.fmean(bars):4.1f} velas")
    print("  -> quase nada atravessa o rollover, e por isso o swap quase nao"
          " aparece abaixo")

    print("\n=== custo por operacao, em R")
    print(f"  {'TF':4}{'r_pct med':>11}{'estudo':>9}{'FTMO ida+volta':>16}"
          f"{'FTMO por lado':>15}{'swap':>8}")
    for label, _path, _gated, _m in SCANS:
        rows = scans[label]
        med = st.median([r["r_pct"] for r in rows])
        days = hold.get(label, 0.0)


        def avg(commission: float, swap_days: float, rows: list[dict] = rows) -> float:
            return st.fmean([cost_in_r(r, commission, swap_days) for r in rows])

        print(f"  {label:4}{med:10.3%}{avg(STUDY_COST, 0):9.3f}"
              f"{avg(FTMO_COMMISSION, 0):16.3f}{avg(2 * FTMO_COMMISSION, 0):15.3f}"
              f"{avg(0, days):+8.3f}")

    print("\n=== acerto e resultado por timeframe (recorte FTMO)")
    print(f"  {'TF':4}{'n':>6}{'2R':>8}{'R/trade':>10}{'R total':>10}")
    for label, _path, _gated, _m in SCANS:
        x = metrics(scans[label], 120)
        print(f"  {label:4}{x['n']:6d}{x['wr_2R']:8.1%}{x['avg_R']:+10.3f}"
              f"{x['total_R']:+10.1f}")

    print("\n=== por ativo — acerto 2R (n)")
    print(f"  {'ticker':9}" + "".join(f"{name:>12}" for name, *_ in SCANS)
          + f"{'TOTAL':>14}  amostra")
    for broker, symbol in sorted(FTMO_CRYPTO.items(), key=lambda kv: kv[1]):
        if symbol not in wanted:
            continue
        cells, pooled = [], []
        for label, *_ in SCANS:
            rows = [r for r in scans[label] if r["symbol"] == symbol]
            pooled += rows
            cells.append(f"{metrics(rows, 120)['wr_2R']:.0%} ({len(rows)})"
                         if rows else "— (0)")
        x = metrics(pooled, 120)
        print(f"  {broker:9}" + "".join(f"{c:>12}" for c in cells)
              + f"{x['wr_2R']:>9.0%} ({x['n']:3d})  {sample_of(symbol)}")
    print("  ATENCAO: com n ~50 por ativo o erro padrao do acerto e ~7 pontos.")
    print("  Escolher ativos por esta tabela e ajustar ao passado, nao selecionar.")

    # --- a carteira, na janela em que os quatro timeframes coexistem
    rows = [dict(r, _tf=label, _days=hold.get(label, 0.0))
            for label, *_ in SCANS for r in scans[label]]
    start = max(min(r["timestamp"] for r in scans[label]) for label, *_ in SCANS)
    rows = sorted((r for r in rows if r["timestamp"] >= start),
                  key=lambda r: r["timestamp"])
    span = (dt.datetime.fromisoformat(rows[-1]["timestamp"])
            - dt.datetime.fromisoformat(rows[0]["timestamp"])).days / 30.44
    print(f"\n=== carteira dos {len(wanted)} ativos, janela comum aos 4 TF "
          f"(a partir de {start[:10]}, {span:.0f} meses)")
    scenarios = (
        ("estudo 0,10%", STUDY_COST, False),
        ("FTMO 0,13% (por lado) + swap", 2 * FTMO_COMMISSION, True),
        ("FTMO 0,065% (ida e volta) + swap", FTMO_COMMISSION, True),
    )
    for name, commission, with_swap in scenarios:
        equity = peak = drawdown = 0.0
        daily: dict[dt.date, float] = collections.defaultdict(float)
        for row in rows:
            net = row[f"r2_h{120}"] - cost_in_r(
                row, commission, row["_days"] if with_swap else 0.0
            )
            equity += net
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
            daily[dt.datetime.fromisoformat(row["timestamp"]).date()] += net
        monthly = equity / span
        worst = min(daily.values())
        print(f"\n  --- {name}")
        print(f"      {len(rows)} trades · {len(rows) / span:.0f}/mes · "
              f"total {equity:+.0f}R ({monthly:+.1f}R/mes)")
        print(f"      drawdown maximo {drawdown:.1f}R · pior dia {worst:+.1f}R")
        print(f"      a {RISK_PER_TRADE:.2%}/R -> {monthly * RISK_PER_TRADE:+.2%}/mes"
              f" · alvo {FTMO_PROFIT_TARGET:.0%} em "
              f"{FTMO_PROFIT_TARGET / (monthly * RISK_PER_TRADE):.1f} meses")
        print(f"      DD {drawdown * RISK_PER_TRADE:.1%} (limite "
              f"{FTMO_MAX_DRAWDOWN:.0%}) · pior dia "
              f"{worst * RISK_PER_TRADE:.1%} (limite -{FTMO_MAX_DAILY_LOSS:.0%})")
    print("\n  O spread do CFD NAO esta em nenhuma destas contas -- so a"
          " comissao anunciada.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--universe", action="store_true",
                   help="rodar sobre o universo inteiro, para comparar")
    a = p.parse_args()
    report(subset_only=not a.universe)


if __name__ == "__main__":
    main()
