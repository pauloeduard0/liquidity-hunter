"""Etapa 0: quantos CHoCH INTERNAL nascem dentro de uma lateralizacao -- e quantos morrem.

A hipotese do usuario (2026-09-08): depois de uma expansao forte o preco
lateraliza, e dentro da lateralizacao aparecem CHoCH/BOS internos que a maquina
trata como se pertencessem a mesma perna externa. O resultado no grafico e uma
estrutura "perdida", com eventos conflitantes.

Este modulo NAO muda nada. Ele so mede o estado atual, para decidir se a Etapa 1
(ligar `_CONSOLIDATION_RANGE_RESET_CYCLE`) tem alvo.

Uma constatacao de leitura vem antes dos numeros, porque ela molda a medicao:
`app.dashboard_data._advance_boundaries` corta os segmentos NOS advances, e
`liquidity.detectors.consolidation` so procura caixa DENTRO de um segmento
quieto. Logo um `ConsolidationRange` confirmado **nao pode conter** um
BOS/CHoCH nao-provisional -- a pergunta literal ("CHoCH dentro de range ACTIVE")
tende a zero por construcao, e um zero ai nao e evidencia de que o problema nao
existe. Entao cada CHoCH e classificado por *zona*:

- `in_range`      -- timestamp dentro de [inicio, fim) de alguma caixa. Para um
                     CHoCH nao-provisional isso e quase impossivel; para um
                     `CHoCH?` (provisional, que nao conta como advance) e o
                     caso central da hipotese.
- `range_edge`    -- ate `EDGE_CANDLES` velas depois do fim de uma caixa: a
                     saida do range, onde o evento conflitante aparece.
- `outside`       -- o resto. **E o controle.** Sem ele um "X% falha dentro do
                     range" nao quer dizer nada: se a taxa fora for igual, a
                     lateralizacao nao e o fator.

E por *desfecho*, varrendo o proprio stream que o grafico desenha, do CHoCH em
diante ate o proximo CHoCH da mesma direcao:

- `failed`    -- `CHOCH_FAILED` real (nao-provisional) da mesma direcao: a
                 maquina desfez o flip.
- `fizzled`   -- `CHOCH_FAILED` provisional (o marcador de fizzle aditivo): a
                 linha ficou obsoleta sem flip de estado.
- `reversed`  -- um CHoCH nao-provisional da direcao OPOSTA dentro de
                 `REVERSED_CANDLES` velas: o flip foi desfeito por outro flip.
- `confirmed` -- um BOS nao-provisional da mesma direcao veio antes de qualquer
                 um dos acima: a reversao virou estrutura. **Um CHoCH em range
                 nao e automaticamente um sinal falso**, e esta coluna e o que
                 impede a leitura preguicosa.
- `open`      -- nada depois (borda viva / fim da janela). Nao entra no
                 denominador das taxas de falha.

Offline por padrao: le `research/.klines_cache` (mesmo formato e mesmo
`klines_row_to_candle` que `research/atr_window_stability.py` usa), sem rede.
Cada janela e uma rodada de producao completa (`_run_internal_structure`), com
a mesma fiacao, o mesmo anchor estrutural e os mesmos passes de composicao que
o dashboard usa. Janelas sao disjuntas (passo = `--limit`), entao nenhum evento
e contado duas vezes.

O JSON de saida (`--json`) guarda TODOS os CHoCH, nao so os interessantes, com
zona, desfecho e a caixa correspondente -- e a linha de base para comparar
antes/depois quando a Etapa 1 (ou a 4) mudar comportamento.

Uso:

    poetry run python -m research.range_choch --windows 6
    poetry run python -m research.range_choch --symbols BTCUSDT ETHUSDT --json out.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.core.domain import (
    Candle,
    ConsolidationRange,
    MarketStructure,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.data.providers.base import OHLCVProvider
from liquidity_hunter.data.providers.binance import klines_row_to_candle

CACHE_DIR = Path(__file__).parent / ".klines_cache"

#: A matriz "live" que `docs/structure_decisions.md` usa nas medicoes do
#: detector (BTC/ETH/SOL/NEAR/AAVE/ENA x M15..D1). Nao e uma lista nova: e a
#: mesma dos blocos 2026-07 do changelog, escrita aqui porque so existia em
#: prosa. `research/_symbols.py` (o universo de 72 simbolos do block-reclaim) e
#: outra coisa -- aquele e o universo dos estudos de setup, com split
#: search/holdout; este e o painel de regressao visual do detector.
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT", "AAVEUSDT", "ENAUSDT"]
TIMEFRAMES = [TimeFrame.M15, TimeFrame.H1, TimeFrame.H4, TimeFrame.D1]

#: Janela visivel de producao (`load_dashboard_data(limit=...)`).
LIMIT = 1200
#: Buffer de bootstrap que a producao prepende.
BUFFER = dd._INTERNAL_STRUCTURE_BOOTSTRAP_BUFFER
#: Velas apos o fim de uma caixa que ainda contam como "saida do range".
EDGE_CANDLES = 20
#: Janela para considerar que um CHoCH oposto desfez o anterior.
REVERSED_CANDLES = 60


class SliceProvider(OHLCVProvider):
    """Serve uma fatia fixa da serie, sem rede (igual ao de atr_window_stability)."""

    max_fetch_limit = 60_000

    def __init__(self, window: list[Candle]) -> None:
        self._window = window

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        return self._window[-limit:]


def load_series(symbol: str, timeframe: TimeFrame) -> list[Candle]:
    path = CACHE_DIR / f"{symbol}_{timeframe.value}.json"
    rows: list[list[Any]] = json.loads(path.read_text())
    return [klines_row_to_candle(symbol, timeframe, r) for r in rows]


@dataclass(frozen=True)
class Case:
    """Um CHoCH INTERNAL e tudo que se sabe sobre ele."""

    symbol: str
    timeframe: str
    timestamp: str
    direction: str
    provisional: bool
    reference_structural: bool | None
    price_level: float
    reference_price_level: float | None
    zone: str                      # in_range | range_edge | outside
    range_start: str | None
    range_end: str | None
    range_status: str | None
    range_low: float | None
    range_high: float | None
    outcome: str                   # failed | fizzled | reversed | confirmed | open
    outcome_timestamp: str | None
    outcome_detail: str | None     # o porque, quando ha um


def _range_of(
    event: MarketStructure, ranges: Sequence[ConsolidationRange], edge_floor: datetime
) -> tuple[str, ConsolidationRange | None]:
    """Zona do evento e a caixa que a justifica.

    `edge_floor` e o timestamp `EDGE_CANDLES` velas ANTES do evento: uma caixa
    que terminou nesse intervalo deixa o evento na zona `range_edge`.
    `in_range` ganha de `range_edge` quando as duas se aplicam; entre caixas
    igualmente aplicaveis vence a mais recente (a que o preco acabou de deixar).
    """
    inside: ConsolidationRange | None = None
    edge: ConsolidationRange | None = None
    for box in ranges:
        if event.timestamp < box.start_timestamp:
            continue
        if box.end_timestamp is None:
            # ACTIVE: aberta ate o fim da serie.
            inside = box
            continue
        if event.timestamp < box.end_timestamp:
            inside = box
        elif edge_floor <= box.end_timestamp <= event.timestamp:
            edge = box
    if inside is not None:
        return "in_range", inside
    if edge is not None:
        return "range_edge", edge
    return "outside", None


def _outcome(
    event: MarketStructure,
    later: Sequence[MarketStructure],
    reversed_cutoff: datetime | None,
) -> tuple[str, str | None, str | None]:
    """Desfecho do CHoCH, lendo o stream dali para a frente.

    Para na primeira coisa que sela o destino: falha, fizzle, confirmacao por
    BOS, ou reversao por CHoCH oposto. Um CHoCH da MESMA direcao depois encerra
    a varredura (dali em diante a historia e do outro evento).
    """
    for other in later:
        if other.timestamp <= event.timestamp:
            continue
        same_dir = other.direction is event.direction
        if other.event is StructureEvent.CHOCH_FAILED and same_dir:
            same_ref = other.reference_price_level == event.reference_price_level
            detail = f"ref {other.reference_price_level}" + (
                " (mesma ref do CHoCH)" if same_ref else ""
            )
            return ("fizzled" if other.provisional else "failed", str(other.timestamp), detail)
        if other.event is StructureEvent.BREAK_OF_STRUCTURE and same_dir and not other.provisional:
            return "confirmed", str(other.timestamp), f"BOS em {other.price_level}"
        if other.event is StructureEvent.CHANGE_OF_CHARACTER and not other.provisional:
            if same_dir:
                break
            if reversed_cutoff is not None and other.timestamp <= reversed_cutoff:
                return "reversed", str(other.timestamp), f"CHoCH oposto em {other.price_level}"
            break
    return "open", None, None


def cases_of_run(
    events: Sequence[MarketStructure],
    ranges: Sequence[ConsolidationRange],
    candles: Sequence[Candle],
) -> list[Case]:
    """Classifica cada CHANGE_OF_CHARACTER de uma rodada de producao."""
    index_by_ts = {c.timestamp: i for i, c in enumerate(candles)}

    def offset(ts: datetime | None, steps: int) -> datetime | None:
        if ts is None:
            return None
        i = index_by_ts.get(ts)
        if i is None:
            return None
        return candles[min(max(i + steps, 0), len(candles) - 1)].timestamp

    cases: list[Case] = []
    for event in events:
        if event.event is not StructureEvent.CHANGE_OF_CHARACTER:
            continue
        edge_floor = offset(event.timestamp, -EDGE_CANDLES) or event.timestamp
        zone, box = _range_of(event, ranges, edge_floor)
        outcome, out_ts, detail = _outcome(
            event, events, offset(event.timestamp, REVERSED_CANDLES)
        )
        cases.append(
            Case(
                symbol=event.symbol,
                timeframe=event.timeframe.value,
                timestamp=str(event.timestamp),
                direction=event.direction.value,
                provisional=event.provisional,
                reference_structural=event.reference_structural,
                price_level=event.price_level,
                reference_price_level=event.reference_price_level,
                zone=zone,
                range_start=str(box.start_timestamp) if box else None,
                range_end=str(box.end_timestamp) if box and box.end_timestamp else None,
                range_status=box.status.value if box else None,
                range_low=box.price_low if box else None,
                range_high=box.price_high if box else None,
                outcome=outcome,
                outcome_timestamp=out_ts,
                outcome_detail=detail,
            )
        )
    return cases



def run_combo(
    symbol: str, timeframe: TimeFrame, windows: int, limit: int
) -> tuple[list[Case], int]:
    """Roda `windows` janelas DISJUNTAS de producao e devolve os casos + velas cobertas."""
    series = load_series(symbol, timeframe)
    cases: list[Case] = []
    covered = 0
    for w in range(windows):
        end = len(series) - w * limit
        start = end - limit - BUFFER
        if start < 0:
            break
        window = series[start:end]
        run = dd._run_internal_structure(
            provider=SliceProvider(window),
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            confluence_filter=True,
        )
        cases.extend(cases_of_run(run.events, run.consolidation_ranges, run.candles))
        covered += len(run.candles)
    return cases, covered


FAIL_OUTCOMES = ("failed", "fizzled", "reversed")
SETTLED = FAIL_OUTCOMES + ("confirmed",)


def summarize(cases: Sequence[Case]) -> dict[str, Any]:
    """Contagens por zona, com a taxa de falha entre os desfechos SELADOS."""
    by_zone: dict[str, Counter[str]] = defaultdict(Counter)
    for case in cases:
        by_zone[case.zone][case.outcome] += 1
    out: dict[str, Any] = {}
    for zone, counts in by_zone.items():
        settled = sum(counts[o] for o in SETTLED)
        failed = sum(counts[o] for o in FAIL_OUTCOMES)
        out[zone] = {
            "total": sum(counts.values()),
            **{o: counts[o] for o in (*SETTLED, "open")},
            "settled": settled,
            "fail_rate": (failed / settled) if settled else None,
        }
    return out


def _fmt_rate(value: float | None) -> str:
    return "  n/a" if value is None else f"{value * 100:5.1f}%"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS)
    parser.add_argument("--timeframes", nargs="+", default=[t.value for t in TIMEFRAMES])
    parser.add_argument("--windows", type=int, default=6, help="janelas disjuntas por combo")
    parser.add_argument("--limit", type=int, default=LIMIT, help="janela visivel (producao=1200)")
    parser.add_argument("--json", type=Path, default=None, help="dump de TODOS os casos")
    args = parser.parse_args(argv)

    timeframes = [TimeFrame(t) for t in args.timeframes]
    all_cases: list[Case] = []
    per_combo: list[tuple[str, str, list[Case], int]] = []

    for symbol in args.symbols:
        for timeframe in timeframes:
            if not (CACHE_DIR / f"{symbol}_{timeframe.value}.json").exists():
                print(f"! sem cache: {symbol} {timeframe.value}")
                continue
            cases, covered = run_combo(symbol, timeframe, args.windows, args.limit)
            per_combo.append((symbol, timeframe.value, cases, covered))
            all_cases.extend(cases)

    print("\n== CHoCH INTERNAL por combo (zona x desfecho) ==")
    header = (
        f"{'simbolo':<10} {'tf':<4} {'velas':>6} {'CHoCH':>6} "
        f"{'in_rng':>7} {'edge':>6} {'fora':>6} {'%range':>7} "
        f"{'falha_rng':>10} {'falha_fora':>11}"
    )
    print(header)
    print("-" * len(header))
    for symbol, tf, cases, covered in per_combo:
        stats = summarize(cases)
        in_range = stats.get("in_range", {}).get("total", 0)
        edge = stats.get("range_edge", {}).get("total", 0)
        outside = stats.get("outside", {}).get("total", 0)
        total = len(cases)
        near = in_range + edge
        print(
            f"{symbol:<10} {tf:<4} {covered:>6} {total:>6} "
            f"{in_range:>7} {edge:>6} {outside:>6} "
            f"{(near / total * 100 if total else 0):>6.1f}% "
            f"{_fmt_rate(_near_fail_rate(cases)):>10} "
            f"{_fmt_rate(stats.get('outside', {}).get('fail_rate')):>11}"
        )

    print("\n== agregado ==")
    total_stats = summarize(all_cases)
    for zone in ("in_range", "range_edge", "outside"):
        s = total_stats.get(zone)
        if not s:
            print(f"{zone:<11} 0")
            continue
        print(
            f"{zone:<11} n={s['total']:<5} failed={s['failed']:<4} fizzled={s['fizzled']:<4} "
            f"reversed={s['reversed']:<4} confirmed={s['confirmed']:<4} open={s['open']:<4} "
            f"falha={_fmt_rate(s['fail_rate'])} (de {s['settled']} selados)"
        )

    prov = Counter(c.zone for c in all_cases if c.provisional)
    print(f"\nprovisionais (`CHoCH?`) por zona: {dict(prov) or 'nenhum'}")

    interesting = [c for c in all_cases if c.zone != "outside"]
    print(f"\n== casos individuais em/junto de range ({len(interesting)}) ==")
    for c in sorted(interesting, key=lambda c: (c.symbol, c.timeframe, c.timestamp))[:60]:
        box = (
            f"[{c.range_low}-{c.range_high}] {c.range_status} "
            f"de {c.range_start} a {c.range_end or 'aberto'}"
        )
        mark = "CHoCH?" if c.provisional else "CHoCH "
        print(
            f"{c.symbol} {c.timeframe} {c.timestamp} {c.direction:<7} "
            f"{mark} @{c.price_level} ref={c.reference_price_level} "
            f"| {c.zone} {box} | -> {c.outcome} "
            f"{c.outcome_timestamp or ''} {c.outcome_detail or ''}"
        )

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "params": {
                        "symbols": args.symbols,
                        "timeframes": [t.value for t in timeframes],
                        "windows": args.windows,
                        "limit": args.limit,
                        "edge_candles": EDGE_CANDLES,
                        "reversed_candles": REVERSED_CANDLES,
                    },
                    "aggregate": total_stats,
                    "cases": [asdict(c) for c in all_cases],
                },
                indent=2,
            )
        )
        print(f"\n{len(all_cases)} casos -> {args.json}")
    return 0


def _near_fail_rate(cases: Sequence[Case]) -> float | None:
    """Taxa de falha juntando `in_range` e `range_edge` (a amostra e pequena)."""
    near = [c for c in cases if c.zone != "outside"]
    settled = [c for c in near if c.outcome in SETTLED]
    if not settled:
        return None
    return sum(1 for c in settled if c.outcome in FAIL_OUTCOMES) / len(settled)


if __name__ == "__main__":
    raise SystemExit(main())
