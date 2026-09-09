"""O `structural_stall` repinta? (risco 4 da Etapa 0.6, agora medido)

A condicao de stall e causal -- `liquidity/structural_stall.py` nao le uma vela
alem do trigger, e ha teste disso. Mas ela e calculada sobre as PERNAS, e as
pernas vem do pipeline, que tem reescrita retroativa medida (15,4% dos refreshes
reescrevem evento nao-provisional a mais de 100 velas da borda,
`research/atr_window_stability.py`). Se o BOS que abre a perna se move, o stall
se move junto -- sem nenhum lookahead na regra.

Este modulo simula refreshes de producao (janela deslizando `STEP` velas por
vez, exatamente como `atr_window_stability`) e compara o stall de um refresh com
o do seguinte, sobre a MESMA perna:

- `estavel`      -- mesma perna, mesmo `stale_since`;
- `deslocado`    -- mesma perna, `stale_since` diferente (repintura);
- `sumiu`        -- mesma perna, o refresh seguinte nao ve mais stall nenhum;
- `retroativo`   -- surgiu um stall cujo `stale_since` esta a mais de
                    `RETRO_MARGIN` velas da borda, ou seja, apareceu ja velho
                    em estrutura que deveria estar assentada;
- `perna nova`   -- o advance mudou (nao e instabilidade: e a estrutura andando).

Nao corrige nada e nao toca producao: mede.

    poetry run python -m research.stall_replay_stability
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.core.domain import StructuralStall, TimeFrame
from liquidity_hunter.liquidity.structural_stall import (
    _last_advance,
    detect_structural_stall,
    is_stall_eligible_leg,
)
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT", "AAVEUSDT"]
TIMEFRAMES = [TimeFrame.M15, TimeFrame.H1, TimeFrame.H4]
#: Velas novas por refresh simulado (o passo de `atr_window_stability`).
STEP = 24
#: Refreshes por combo.
REFRESHES = 20
#: Um stall que nasce ja com mais que isto de idade apareceu em estrutura
#: assentada, nao na borda viva.
RETRO_MARGIN = 100


def snapshot(series: Sequence, end: int, timeframe: TimeFrame, symbol: str):
    """Um refresh de producao: mesma fatia, mesma fiacao, mesmo stall."""
    start = end - LIMIT - BUFFER
    run = dd._run_internal_structure(
        provider=SliceProvider(list(series[max(start, 0) : end])),
        symbol=symbol,
        timeframe=timeframe,
        limit=LIMIT,
        confluence_filter=True,
    )
    eligible = is_stall_eligible_leg(run.candles, run.events)
    stall = detect_structural_stall(run.candles, run.events)
    gated = stall if eligible else None
    return run, stall, eligible, gated


def _age_in_bars(run, stall: StructuralStall) -> int:
    index = {candle.timestamp: i for i, candle in enumerate(run.candles)}
    return len(run.candles) - 1 - index.get(stall.stale_since, len(run.candles) - 1)


def compare(previous, current, slot: int = 1) -> str:
    """Como o stall do refresh anterior sobreviveu ao seguinte.

    `slot` escolhe o stall comparado: 1 = generico, 3 = com o gate de expansao.
    """
    before, (run_after, after) = previous[slot], (current[0], current[slot])
    if before is None and after is None:
        return "sem stall"
    if before is None:
        assert after is not None
        return "retroativo" if _age_in_bars(run_after, after) > RETRO_MARGIN else "novo"
    if after is None:
        # Sumir porque a maquina emitiu um advance novo NAO e repintura: a
        # perna acabou de verdade e nao ha o que declarar parado. So conta
        # como "sumiu" se aquela mesma perna ainda for a vigente.
        index = {candle.timestamp: i for i, candle in enumerate(run_after.candles)}
        advance = _last_advance(run_after.events, index)
        if advance is None or advance[0].timestamp != before.last_advance_timestamp:
            return "perna nova"
        return "sumiu"
    if after.last_advance_timestamp != before.last_advance_timestamp:
        return "perna nova"
    return "estavel" if after.stale_since == before.stale_since else "deslocado"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS)
    parser.add_argument("--timeframes", nargs="+", default=[t.value for t in TIMEFRAMES])
    parser.add_argument("--refreshes", type=int, default=REFRESHES)
    parser.add_argument("--step", type=int, default=STEP)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    totals: Counter[str] = Counter()
    gated_counts: Counter[str] = Counter()
    eligibility: Counter[str] = Counter()
    rows: list[tuple[str, str, Counter[str]]] = []
    for symbol in args.symbols:
        for value in args.timeframes:
            timeframe = TimeFrame(value)
            if not (CACHE_DIR / f"{symbol}_{timeframe.value}.json").exists():
                print(f"! sem cache: {symbol} {timeframe.value}")
                continue
            series = load_series(symbol, timeframe)
            counts: Counter[str] = Counter()
            previous = None
            for refresh in range(args.refreshes, -1, -1):
                end = len(series) - refresh * args.step
                current = snapshot(series, end, timeframe, symbol)
                if previous is not None:
                    counts[compare(previous, current)] += 1
                    gated_counts[compare(previous, current, slot=3)] += 1
                    if previous[2] != current[2]:
                        # A elegibilidade da perna VIGENTE mudou. So e
                        # instabilidade se a perna for a mesma nos dois.
                        index = {c.timestamp: i for i, c in enumerate(current[0].candles)}
                        adv_now = _last_advance(current[0].events, index)
                        prev_index = {c.timestamp: i for i, c in enumerate(previous[0].candles)}
                        adv_before = _last_advance(previous[0].events, prev_index)
                        same_leg = (
                            adv_now is not None
                            and adv_before is not None
                            and adv_now[0].timestamp == adv_before[0].timestamp
                        )
                        eligibility["virou na mesma perna" if same_leg else "perna nova"] += 1
                    else:
                        eligibility["estavel"] += 1
                previous = current
            rows.append((symbol, timeframe.value, counts))
            totals.update(counts)

    header = (
        f"{'simbolo':<10} {'tf':<4} {'estavel':>8} {'deslocado':>10} {'sumiu':>7} "
        f"{'retroativo':>11} {'perna nova':>11} {'sem stall':>10}"
    )
    print(f"\n== {args.refreshes} refreshes de {args.step} velas por combo ==")
    print(header)
    print("-" * len(header))
    for symbol, value, counts in rows:
        print(
            f"{symbol:<10} {value:<4} {counts['estavel']:>8} {counts['deslocado']:>10} "
            f"{counts['sumiu']:>7} {counts['retroativo']:>11} {counts['perna nova']:>11} "
            f"{counts['sem stall'] + counts['novo']:>10}"
        )

    tracked = totals["estavel"] + totals["deslocado"] + totals["sumiu"]
    print(f"\ntransicoes com a MESMA perna nos dois refreshes: {tracked}")
    if tracked:
        print(
            f"  estavel   {totals['estavel']:>4} = {totals['estavel'] / tracked * 100:.1f}%\n"
            f"  deslocado {totals['deslocado']:>4} = {totals['deslocado'] / tracked * 100:.1f}%\n"
            f"  sumiu     {totals['sumiu']:>4} = {totals['sumiu'] / tracked * 100:.1f}%"
        )
    print("\n== elegibilidade (gate de expansao) entre refreshes ==")
    total_e = sum(eligibility.values())
    for key, value in eligibility.most_common():
        print(f"  {key:<22} {value:>4} = {value / max(total_e, 1) * 100:.1f}%")
    tracked_g = gated_counts["estavel"] + gated_counts["deslocado"] + gated_counts["sumiu"]
    print(f"\n== STALE COM gate: {tracked_g} transicoes com a mesma perna ==")
    if tracked_g:
        for key in ("estavel", "deslocado", "sumiu"):
            share = gated_counts[key] / tracked_g * 100
            print(f"  {key:<10} {gated_counts[key]:>4} = {share:.1f}%")
    print(f"  retroativos: {gated_counts['retroativo']}  novos: {gated_counts['novo']}")
    print(f"\nstalls que nasceram velhos (>{RETRO_MARGIN} velas): {totals['retroativo']}")
    print(f"trocas de perna (estrutura andando, nao instabilidade): {totals['perna nova']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
