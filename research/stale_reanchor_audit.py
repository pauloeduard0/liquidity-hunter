"""Etapa 4.5: o stale re-anchor chega a tempo numa perna sem pullback?

Quatro etapas fecharam quatro hipoteses sobre o ZEC D1 (4.1 opener de CHoCH,
4.2 ritmo estrutural, 4.3 gate de establishment, 4.4 escolha de referencia).
A 4.4 terminou mostrando que **nao havia referencia local melhor**: nos dois
CHoCH obrigatorios do ZEC o ultimo pullback confirmado ERA a referencia
oficial, com lead zero. O preco subiu 220% entre 29/04 e 16/05 sem formar
pullback intermediario, entao nao ha estrutura intermediaria para escolher.

Sobra um unico mecanismo no sistema que ataca esse cenario, e ele ja existe e
ja esta ligado. Esta etapa audita so ele.

Secao 2 -- o mecanismo real, reconstruido do codigo
===================================================

Ha **dois** re-anchors independentes, e o nome "stale" cobre so um deles:

1. `reanchor_mode` (`"off"` / `"chain"` / `"displacement"` -- os tres modos
   reais em `_REANCHOR_MODES`). Producao usa `"chain"` com
   `reanchor_chain_threshold=2` e `reanchor_chain_establish_only=True`.
   Dispara por CONTAGEM DE BOS na perna, nao por tempo.

2. `stale_reanchor_candles` -- o staleness propriamente dito, **independente**
   do `reanchor_mode`. Producao (`_STALE_REANCHOR_CANDLES`): M5 120, M15 90,
   M30 80, H1 80, H4 60, **D1 40**, W1 26.

   Condicao (linha ~1955 de `internal_structure.py`):

       trend != NEUTRAL and last_advance_index >= 0
       and current_index - last_advance_index >= stale_after

   `last_advance_index` e atualizado em `emit` a cada BOS ou flip de trend.
   **O contador zera em todo advance** -- e este e o ponto que decide a etapa.

   Release por deslocamento (`stale_reanchor_displacement_atr=16.0`,
   `stale_reanchor_displacement_candles=15`): quando o vao entre a referencia
   de reversao efetiva e o extremo da perna (`bear_leg_low`/`bull_leg_high`)
   passa de 16 x `mean_tr_pct`, o ciclo e considerado gasto e o limiar cai
   para 15 velas. So entao a janela passa a comecar no proprio advance
   (`stale_reanchor_displacement_post_extreme=True` reabre a janela logo apos
   o extremo da perna, para nao ancorar no meio do movimento).

   Nivel escolhido: o extremo da janela, ou -- com
   `stale_reanchor_swing_pivot=True`, que producao usa -- o **pivo de swing
   confirmado** mais extremo dentro dela, caindo no extremo bruto so quando a
   janela nao contem pivo nenhum.

O que IMPEDE um re-anchor (`reanchor_opposite`, linha 1713), em ordem:

- o nivel tem de estar do lado certo do preco (acima, em tendencia bearish);
- `reanchor_min_price_gap_pct`: perto demais do preco vira gatilho de cabelo;
- **guarda estrutural**: se `validated_choch_<side>` existe e e *structural*,
  o re-anchor so age se aquela referencia ja estiver mais longe que o
  `release_gap`; senao devolve `False`;
- **so aperta, nunca afrouxa**: comparado contra a referencia EFETIVA
  (`validated or choch_origin or active`), nao contra o maximo dos slots.

O que ele escreve: `validated_choch_<side>` (marcado
`structural=False`) e zera `choch_origin_<side>`. Sob
`bos_leg_origin_choch_ref` ele **nao** toca `active_<side>` nem
`candidate_choch_<side>`, para nao lavar um nivel sintetico em origem de
perna. E ele **nao escreve** `pending_bos.pullback_ref` -- o slot
`pending_leg_origin`, que na 4.4 foi a origem da referencia do ZEC.

Como isso e medido aqui
=======================

Sem tocar em producao, por dois caminhos ja validados na Etapa 4.4:

- **contrafactual de configuracao**: `_build_internal_detector` e o unico
  ponto de construcao do detector interno; um wrapper temporario constroi o
  detector de producao e sobrescreve os atributos privados de re-anchor. O
  `__init__` apenas guarda esses valores e `detect` os le de `self` em tempo
  de execucao, entao sobrescrever a instancia e equivalente a construir com
  outros kwargs -- e ha teste que compara "sobrescrever com os valores atuais"
  contra a producao byte a byte;
- **observacao do gatilho**: um `sys.settrace` no code object de
  `reanchor_opposite` registra toda TENTATIVA de re-anchor com seu nivel,
  timestamp, preco corrente e o valor de RETORNO -- ou seja, quais foram
  recusadas e quais moveram a referencia.

Secao 17 continua valendo: nada aqui cria CHoCH por deslocamento. O
re-anchor segue sendo selecao de referencia estrutural, e as unicas coisas
variadas sao os parametros que ja existem.

    poetry run python -m research.stale_reanchor_audit
    poetry run python -m research.stale_reanchor_audit --expanded --json \
        research/stale_reanchor_audit_baseline.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from types import FrameType
from typing import Any

from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.liquidity.detectors.internal_structure import InternalStructureDetector
from research.choch_leg_opener import (
    DISCOVERY_FRACTION,
    SYMBOLS,
    TIMEFRAMES,
    WINDOWS,
)
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series
from research.zec_d1_structure_lag import leg_extreme_before

#: O code object do gatilho, achado pelo nome entre as constantes de `detect`.
#: Se ele sumir ou for renomeado a medicao para, em vez de reportar zero.
_REANCHOR_CODE = next(
    (
        const
        for const in InternalStructureDetector.detect.__code__.co_consts
        if getattr(const, "co_name", None) == "reanchor_opposite"
    ),
    None,
)

MOVE_LOOKBACK = 120
OUTCOME_WINDOW = 80
#: Um CHoCH do contrafactual "e o mesmo" de producao se cair a ate tantas
#: velas de um CHoCH real da mesma direcao.
MATCH_WINDOW = 20
QUICK_BACK = (10, 20, 40)

ZEC_BEARISH = ("ZECUSDT", TimeFrame.D1, "2026-06-04T00:00:00+00:00")


@dataclass(frozen=True)
class Variant:
    """Uma configuracao do mecanismo EXISTENTE (secao 14: so calibragem)."""

    label: str
    overrides: dict[str, Any] = field(default_factory=dict)
    #: Multiplicador aplicado ao `stale_reanchor_candles` por timeframe --
    #: o valor de producao muda por TF, entao um numero absoluto compararia
    #: coisas diferentes entre timeframes.
    stale_scale: float | None = None

    def resolve(self, timeframe: TimeFrame) -> dict[str, Any]:
        out = dict(self.overrides)
        if self.stale_scale is not None:
            base = dd._STALE_REANCHOR_CANDLES.get(
                timeframe, dd._DEFAULT_STALE_REANCHOR_CANDLES
            )
            out["_stale_reanchor_candles"] = max(1, round(base * self.stale_scale))
        return out


BASELINE = Variant(label="producao")
VARIANTS = [
    BASELINE,
    # eixo 1: o gatilho por cadeia de BOS
    Variant("chain_threshold=1", {"_reanchor_chain_threshold": 1}),
    Variant("chain_threshold=3", {"_reanchor_chain_threshold": 3}),
    Variant("chain_threshold=4", {"_reanchor_chain_threshold": 4}),
    Variant("chain_threshold=5", {"_reanchor_chain_threshold": 5}),
    # eixo 2: o timer de staleness (escalado sobre o valor por TF)
    Variant("stale x0.25", stale_scale=0.25),
    Variant("stale x0.50", stale_scale=0.50),
    Variant("stale x0.75", stale_scale=0.75),
    Variant("stale x1.50", stale_scale=1.50),
    # eixo 3: o release por deslocamento
    Variant("disp_atr=8", {"_stale_reanchor_displacement_atr": 8.0}),
    Variant("disp_atr=12", {"_stale_reanchor_displacement_atr": 12.0}),
    Variant("disp_atr=20", {"_stale_reanchor_displacement_atr": 20.0}),
    Variant("disp_candles=5", {"_stale_reanchor_displacement_candles": 5}),
    # eixo 4: os modos reais
    Variant("mode=off", {"_reanchor_mode": "off"}),
    Variant("mode=displacement", {"_reanchor_mode": "displacement"}),
    # desligar o staleness por completo, para medir o que ele ja entrega
    Variant("stale=off", {"_stale_reanchor_candles": None}),
    Variant("disp=off", {"_stale_reanchor_displacement_atr": None,
                         "_stale_reanchor_displacement_candles": None}),
]


@contextmanager
def wired(overrides: dict[str, Any]):
    """`_build_internal_detector` com atributos de re-anchor sobrescritos.

    Nao altera o arquivo de producao: embrulha a funcao pelo tempo do bloco e
    restaura no fim (inclusive em excecao). Com `overrides` vazio o wrapper
    ainda e instalado, para que o caminho medido seja o mesmo nos dois lados.
    """
    original = dd._build_internal_detector

    def build(timeframe: TimeFrame, *, confluence_filter: bool):
        detector = original(timeframe, confluence_filter=confluence_filter)
        for name, value in overrides.items():
            if not hasattr(detector, name):
                raise AttributeError(f"{name} nao existe no detector")
            setattr(detector, name, value)
        return detector

    dd._build_internal_detector = build
    try:
        yield
    finally:
        dd._build_internal_detector = original


@dataclass
class ReanchorAttempt:
    """Uma chamada de `reanchor_opposite`, com o veredito."""

    level: float
    timestamp: str
    current_price: float
    moved: bool


def run_variant(
    symbol: str,
    timeframe: TimeFrame,
    series: Sequence[Candle],
    end: int,
    variant: Variant,
    limit: int = LIMIT,
):
    """Uma janela de producao sob `variant`, com as tentativas de re-anchor."""
    if _REANCHOR_CODE is None:
        raise RuntimeError("reanchor_opposite nao foi encontrado no detector")
    start = end - limit - BUFFER
    attempts: list[ReanchorAttempt] = []
    pending: list[dict] = []

    def tracer(frame: FrameType, event: str, arg: Any):
        if event == "call" and frame.f_code is _REANCHOR_CODE:
            local = frame.f_locals
            pending.append(
                {
                    "level": local.get("level"),
                    "ts": local.get("ts"),
                    "price": local.get("current_price"),
                }
            )
            return tracer
        if event == "return" and frame.f_code is _REANCHOR_CODE and pending:
            call = pending.pop()
            attempts.append(
                ReanchorAttempt(
                    level=float(call["level"]),
                    timestamp=call["ts"].isoformat() if call["ts"] else "",
                    current_price=float(call["price"]),
                    moved=bool(arg),
                )
            )
        return None

    previous = sys.gettrace()
    sys.settrace(tracer)
    try:
        with wired(variant.resolve(timeframe)):
            run = dd._run_internal_structure(
                provider=SliceProvider(list(series[max(start, 0) : end])),
                symbol=symbol,
                timeframe=timeframe,
                limit=limit,
                confluence_filter=True,
            )
    finally:
        sys.settrace(previous)
    return run, attempts


# --------------------------------------------------------------------------
# os CHoCH de um stream, com atraso
# --------------------------------------------------------------------------


@dataclass
class ChochRow:
    timestamp: str
    index: int
    direction: str
    reference_price: float | None
    reference_structural: bool | None
    lag_bars: int
    move_completed_pct: float | None
    outcome: str | None = None
    bars_to_outcome: int | None = None


def choch_rows(run) -> list[ChochRow]:
    """Todo CHoCH nao-provisional, com seu atraso contra o extremo do movimento."""
    candles = run.candles
    by_ts = {candle.timestamp: i for i, candle in enumerate(candles)}
    rows: list[ChochRow] = []
    for event in run.events:
        if event.provisional or event.event is not StructureEvent.CHANGE_OF_CHARACTER:
            continue
        index = by_ts.get(event.timestamp)
        if index is None or index < 2:
            continue
        bullish = event.direction is MarketDirection.BULLISH
        start_index, start_price = leg_extreme_before(
            candles, index, bullish_move=bullish, lookback=MOVE_LOOKBACK
        )
        close = candles[index].close
        tail = candles[start_index : min(len(candles), index + OUTCOME_WINDOW)]
        far = (
            max(candle.high for candle in tail)
            if bullish
            else min(candle.low for candle in tail)
        )
        total = abs(far - start_price)
        done = abs(close - start_price) / total * 100 if total > 0 else None
        outcome, bars = _outcome(run.events, by_ts, index, event.direction)
        rows.append(
            ChochRow(
                timestamp=event.timestamp.isoformat(),
                index=index,
                direction=event.direction.value,
                reference_price=event.reference_price_level,
                reference_structural=event.reference_structural,
                lag_bars=index - start_index,
                move_completed_pct=None if done is None else round(done, 1),
                outcome=outcome,
                bars_to_outcome=bars,
            )
        )
    return rows


def _outcome(
    events: Sequence[MarketStructure],
    by_ts: dict[datetime, int],
    index: int,
    direction: MarketDirection,
) -> tuple[str, int | None]:
    """O que a estrutura fez depois deste CHoCH, no proprio stream dele.

    - `confirmado`   -- BOS da mesma direcao (a reversao virou estrutura);
    - `choch_failed` -- a maquina marcou `✕` nele;
    - `voltou`       -- BOS da direcao antiga;
    - `sem_desfecho` -- a janela acabou.
    """
    for event in events:
        position = by_ts.get(event.timestamp)
        if position is None or position <= index or position > index + OUTCOME_WINDOW:
            continue
        if event.provisional:
            continue
        if event.event is StructureEvent.CHOCH_FAILED and event.direction is direction:
            return "choch_failed", position - index
        if event.event is StructureEvent.BREAK_OF_STRUCTURE:
            same = event.direction is direction
            return ("confirmado" if same else "voltou"), position - index
    return "sem_desfecho", None


def match_rows(
    baseline: Sequence[ChochRow], variant: Sequence[ChochRow]
) -> tuple[list[tuple[ChochRow, ChochRow]], list[ChochRow], list[ChochRow]]:
    """Casa os CHoCH do contrafactual com os de producao.

    Casamento por direcao e proximidade (`MATCH_WINDOW`): o mesmo CHoCH
    chegando mais cedo continua sendo o mesmo evento, e e essa diferenca de
    indice que vira o `lead`. O que nao casa e `extra` (so existe no
    contrafactual) ou `perdido` (existia em producao e sumiu) -- e as duas
    listas contam, porque um re-anchor mais agressivo tanto inventa reversao
    quanto engole reversao legitima.
    """
    remaining = list(variant)
    pairs: list[tuple[ChochRow, ChochRow]] = []
    lost: list[ChochRow] = []
    for row in baseline:
        best = None
        for other in remaining:
            if other.direction != row.direction:
                continue
            if abs(other.index - row.index) > MATCH_WINDOW:
                continue
            if best is None or abs(other.index - row.index) < abs(best.index - row.index):
                best = other
        if best is None:
            lost.append(row)
        else:
            pairs.append((row, best))
            remaining.remove(best)
    return pairs, remaining, lost


def variant_summary(
    baseline: Sequence[ChochRow], variant: Sequence[ChochRow]
) -> dict:
    """Secao 4/10: o que a configuracao ganha e o que ela custa."""
    pairs, extra, lost = match_rows(baseline, variant)
    leads = [before.index - after.index for before, after in pairs]
    earlier = [lead for lead in leads if lead > 0]
    outcomes = [row.outcome for row in extra]
    summary = {
        "n_baseline": len(baseline),
        "n_variant": len(variant),
        "casados": len(pairs),
        "extra": len(extra),
        "perdidos": len(lost),
        "adiantados": len(earlier),
        "atrasados": sum(1 for lead in leads if lead < 0),
        "lead_mediano": round(statistics.median(earlier), 1) if earlier else 0.0,
        "lag_mediano_baseline": (
            round(statistics.median([row.lag_bars for row in baseline]), 1)
            if baseline
            else None
        ),
        "lag_mediano_variant": (
            round(statistics.median([row.lag_bars for row in variant]), 1)
            if variant
            else None
        ),
        "move_pct_baseline": (
            round(
                statistics.median(
                    [r.move_completed_pct for r in baseline if r.move_completed_pct is not None]
                ),
                1,
            )
            if baseline
            else None
        ),
        "move_pct_variant": (
            round(
                statistics.median(
                    [r.move_completed_pct for r in variant if r.move_completed_pct is not None]
                ),
                1,
            )
            if variant
            else None
        ),
    }
    total = len(extra)
    for verdict in ("confirmado", "choch_failed", "voltou", "sem_desfecho"):
        summary[f"extra_{verdict}"] = (
            round(outcomes.count(verdict) / total * 100, 1) if total else 0.0
        )
    for horizon in QUICK_BACK:
        summary[f"extra_voltou_{horizon}"] = (
            round(
                sum(
                    1
                    for row in extra
                    if row.outcome == "voltou"
                    and row.bars_to_outcome is not None
                    and row.bars_to_outcome <= horizon
                )
                / total
                * 100,
                1,
            )
            if total
            else 0.0
        )
    return summary


# --------------------------------------------------------------------------
# 6/9. a populacao alvo e o controle
# --------------------------------------------------------------------------


@dataclass
class Episode:
    """Uma expansao longa sem pullback novo -- o cenario do ZEC, causal."""

    symbol: str
    timeframe: str
    window: int
    start_timestamp: str
    start_index: int
    direction: str
    bars_without_pullback: int
    advance_atr: float
    #: O que veio depois (avaliacao, nunca selecao).
    reversed_after: bool
    choch_timestamp: str | None
    choch_lag_bars: int | None
    move_completed_pct: float | None
    reanchors_during: int


#: Um episodio precisa de tantas velas sem pullback confirmado novo...
EPISODE_BARS = 30
#: ...e de tanto avanco em ATR desde o ultimo pullback.
EPISODE_ATR = 6.0


def episodes(
    run, symbol: str, timeframe: TimeFrame, window: int, attempts: Sequence[ReanchorAttempt]
) -> list[Episode]:
    """Expansoes longas sem pullback novo, selecionadas so com o passado.

    O criterio nao olha o desfecho: em cada vela pergunta ha quantas velas o
    ultimo pullback confirmado do lado contrario nao e substituido e quanto o
    preco andou desde ele. Se a reversao veio ou nao entra depois, como
    avaliacao -- que e o que separa a populacao alvo (secao 6) do controle de
    continuacao limpa (secao 9).
    """
    from research.choch_reference_audit import confirmed_pullbacks, swing_lookback_of

    candles = run.candles
    by_ts = {candle.timestamp: i for i, candle in enumerate(candles)}
    lookback = swing_lookback_of(timeframe)
    mean_tr = _mean_tr_pct(candles)
    if mean_tr <= 0:
        return []

    trend_by_index = _trend_by_index(run, by_ts, len(candles))
    out: list[Episode] = []
    open_since: dict[str, int] = {}

    for index in range(len(candles)):
        trend = trend_by_index[index]
        if trend is None:
            continue
        bullish_break = trend is MarketDirection.BEARISH
        pullbacks = confirmed_pullbacks(
            run.events, candles[index].timestamp, bullish_break, candles, lookback
        )
        if not pullbacks:
            continue
        last = pullbacks[-1]
        key = last.timestamp.isoformat()
        first_seen = open_since.setdefault(key, index)
        bars = index - first_seen
        if bars < EPISODE_BARS:
            continue
        advance = abs(candles[index].close - last.price_level) / last.price_level / mean_tr
        if advance < EPISODE_ATR:
            continue
        if any(episode.start_timestamp == key for episode in out):
            continue

        reversal = (
            MarketDirection.BEARISH
            if trend is MarketDirection.BULLISH
            else MarketDirection.BULLISH
        )
        choch = next(
            (
                event
                for event in run.events
                if not event.provisional
                and event.event is StructureEvent.CHANGE_OF_CHARACTER
                and event.direction is reversal
                and event.timestamp in by_ts
                and by_ts[event.timestamp] > index
            ),
            None,
        )
        lag = move = None
        if choch is not None:
            choch_index = by_ts[choch.timestamp]
            start_index, start_price = leg_extreme_before(
                candles, choch_index, bullish_move=reversal is MarketDirection.BULLISH,
                lookback=MOVE_LOOKBACK,
            )
            lag = choch_index - start_index
            tail = candles[start_index : min(len(candles), choch_index + OUTCOME_WINDOW)]
            far = (
                max(c.high for c in tail)
                if reversal is MarketDirection.BULLISH
                else min(c.low for c in tail)
            )
            total = abs(far - start_price)
            if total > 0:
                move = round(
                    abs(candles[choch_index].close - start_price) / total * 100, 1
                )
        during = sum(
            1
            for attempt in attempts
            if attempt.moved
            and candles[first_seen].timestamp.isoformat()
            <= attempt.timestamp
            <= candles[index].timestamp.isoformat()
        )
        out.append(
            Episode(
                symbol=symbol,
                timeframe=timeframe.value,
                window=window,
                start_timestamp=key,
                start_index=first_seen,
                direction=trend.value,
                bars_without_pullback=bars,
                advance_atr=round(advance, 2),
                reversed_after=choch is not None,
                choch_timestamp=choch.timestamp.isoformat() if choch else None,
                choch_lag_bars=lag,
                move_completed_pct=move,
                reanchors_during=during,
            )
        )
    return out


def _mean_tr_pct(candles: Sequence[Candle]) -> float:
    if len(candles) < 2:
        return 0.0
    total = 0.0
    for previous, current in zip(candles[:-1], candles[1:], strict=False):
        total += (
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
            / current.close
        )
    return total / (len(candles) - 1)


def _trend_by_index(run, by_ts: dict[datetime, int], length: int):
    """A tendencia vigente por vela, com a semantica do `_advance_boundaries`."""
    advances = sorted(
        (
            (by_ts[event.timestamp], event)
            for event in run.events
            if not event.provisional
            and event.event
            in (
                StructureEvent.BREAK_OF_STRUCTURE,
                StructureEvent.CHANGE_OF_CHARACTER,
                StructureEvent.CHOCH_FAILED,
            )
            and event.timestamp in by_ts
        ),
        key=lambda pair: pair[0],
    )
    out: list[MarketDirection | None] = [None] * length
    current: MarketDirection | None = None
    position = 0
    for index in range(length):
        while position < len(advances) and advances[position][0] <= index:
            event = advances[position][1]
            if event.event is StructureEvent.CHOCH_FAILED:
                current = (
                    MarketDirection.BEARISH
                    if event.direction is MarketDirection.BULLISH
                    else MarketDirection.BULLISH
                )
            else:
                current = event.direction
            position += 1
        out[index] = current
    return out


# --------------------------------------------------------------------------
# coleta e relatorio
# --------------------------------------------------------------------------


def split_holdout(rows: Sequence[Any]) -> tuple[list, list]:
    """70/30 por tempo, DENTRO de cada timeframe (a correcao da Etapa 4.2)."""
    discovery: list = []
    holdout: list = []
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(row.timeframe, []).append(row)
    for group in groups.values():
        group.sort(key=lambda row: row.start_timestamp)
        cut = int(len(group) * DISCOVERY_FRACTION)
        discovery.extend(group[:cut])
        holdout.extend(group[cut:])
    return discovery, holdout


def num(value, digits: int = 1) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def build(symbols: Sequence[str], windows: int, variants: Sequence[Variant]) -> dict:
    if _REANCHOR_CODE is None:
        raise RuntimeError("reanchor_opposite nao encontrado -- a medicao pararia vazia")

    per_variant: dict[str, list[dict]] = {variant.label: [] for variant in variants}
    all_episodes: list[Episode] = []
    attempts_total = {"tentativas": 0, "moveram": 0}
    zec_rows: list[dict] = []
    combos = 0

    for symbol in symbols:
        for timeframe in TIMEFRAMES:
            if not (CACHE_DIR / f"{symbol}_{timeframe.value}.json").exists():
                continue
            try:
                series = load_series(symbol, timeframe)
            except Exception as error:  # noqa: BLE001 - cache com vela corrompida
                print(f"! cache invalido: {symbol} {timeframe.value}: {error}")
                continue
            for window in range(windows):
                end = len(series) - window * LIMIT
                if end - LIMIT - BUFFER < 0:
                    break
                try:
                    base_run, base_attempts = run_variant(
                        symbol, timeframe, series, end, BASELINE
                    )
                except Exception as error:  # noqa: BLE001 - feed morto
                    print(f"! janela invalida: {symbol} {timeframe.value} w{window}: {error}")
                    continue
                combos += 1
                base_rows = choch_rows(base_run)
                attempts_total["tentativas"] += len(base_attempts)
                attempts_total["moveram"] += sum(1 for a in base_attempts if a.moved)
                all_episodes.extend(
                    episodes(base_run, symbol, timeframe, window, base_attempts)
                )

                for variant in variants:
                    if variant is BASELINE:
                        rows = base_rows
                    else:
                        try:
                            run, _attempts = run_variant(
                                symbol, timeframe, series, end, variant
                            )
                        except Exception:  # noqa: BLE001 - variante degenerada
                            continue
                        rows = choch_rows(run)
                    summary = variant_summary(base_rows, rows)
                    summary["symbol"] = symbol
                    summary["timeframe"] = timeframe.value
                    per_variant[variant.label].append(summary)
                    if (symbol, timeframe) == (ZEC_BEARISH[0], ZEC_BEARISH[1]) and window == 0:
                        near = [
                            row
                            for row in rows
                            if row.direction == MarketDirection.BEARISH.value
                            and "2026-05" <= row.timestamp <= "2026-07"
                        ]
                        zec_rows.append(
                            {
                                "variant": variant.label,
                                "choch": [asdict(row) for row in near],
                            }
                        )

    print(f"combos medidos: {combos}  episodios: {len(all_episodes)}")
    print(
        f"tentativas de re-anchor em producao: {attempts_total['tentativas']} "
        f"({attempts_total['moveram']} moveram a referencia)"
    )
    report: dict = {
        "symbols": list(symbols),
        "combos": combos,
        "reanchor_attempts": attempts_total,
    }

    # -- 4/10: as variantes --
    def aggregate(rows: Sequence[dict]) -> dict:
        if not rows:
            return {}
        out = {}
        for key in (
            "n_baseline", "n_variant", "extra", "perdidos", "adiantados", "atrasados",
        ):
            out[key] = sum(row[key] for row in rows)
        for key in ("lead_mediano", "lag_mediano_variant", "move_pct_variant"):
            values = [row[key] for row in rows if row.get(key) is not None]
            out[key] = round(statistics.median(values), 1) if values else None
        extra_total = out["extra"]
        for key in (
            "extra_confirmado", "extra_choch_failed", "extra_voltou",
            *[f"extra_voltou_{h}" for h in QUICK_BACK],
        ):
            weighted = sum(row[key] * row["extra"] for row in rows)
            out[key] = round(weighted / extra_total, 1) if extra_total else 0.0
        return out

    print("\n== 4/5/10. CONFIGURACOES DO MECANISMO EXISTENTE ==")
    header = (
        f"{'variante':<20} {'CHoCH':>6} {'extra':>6} {'perd':>5} {'adiant':>7} "
        f"{'lead':>5} {'lag':>5} {'mov%':>6} {'extra ok':>9} {'extra ✕':>8} {'volta20':>8}"
    )
    print(header)
    print("-" * len(header))
    variant_report = {}
    for variant in variants:
        summary = aggregate(per_variant[variant.label])
        if not summary:
            continue
        variant_report[variant.label] = summary
        print(
            f"{variant.label:<20} {summary['n_variant']:>6} {summary['extra']:>6} "
            f"{summary['perdidos']:>5} {summary['adiantados']:>7} "
            f"{num(summary['lead_mediano'], 0):>5} {num(summary['lag_mediano_variant'], 0):>5} "
            f"{num(summary['move_pct_variant']):>6} "
            f"{summary['extra_confirmado']:>8.1f}% {summary['extra_choch_failed']:>7.1f}% "
            f"{summary['extra_voltou_20']:>7.1f}%"
        )
    report["variants"] = variant_report

    # -- 6/9: episodios --
    target = [episode for episode in all_episodes if episode.reversed_after]
    control = [episode for episode in all_episodes if not episode.reversed_after]
    print(f"\n== 6/9. EPISODIOS ({EPISODE_BARS}+ velas sem pullback, {EPISODE_ATR}+ ATR) ==")
    print(f"  reverteram depois (alvo): {len(target)} | continuaram (controle): {len(control)}")
    for name, group in (("alvo", target), ("controle", control)):
        if not group:
            continue
        with_reanchor = sum(1 for e in group if e.reanchors_during > 0)
        print(
            f"  {name:<9} barras sem pullback (med) "
            f"{num(statistics.median([e.bars_without_pullback for e in group]), 0)} | "
            f"avanco {num(statistics.median([e.advance_atr for e in group]))} ATR | "
            f"com re-anchor durante {with_reanchor}/{len(group)} "
            f"({with_reanchor / len(group) * 100:.0f}%)"
        )
    lagged = [e for e in target if e.choch_lag_bars is not None]
    moves = [e.move_completed_pct for e in lagged if e.move_completed_pct is not None]
    if lagged:
        print(
            f"  alvo: lag mediano do CHoCH seguinte "
            f"{num(statistics.median([e.choch_lag_bars for e in lagged]), 0)} velas | "
            f"movimento feito {num(statistics.median(moves))}%" if moves else ""
        )
    report["episodes"] = {
        "n_target": len(target),
        "n_control": len(control),
        "target_with_reanchor": sum(1 for e in target if e.reanchors_during > 0),
        "control_with_reanchor": sum(1 for e in control if e.reanchors_during > 0),
    }

    # -- 11: holdout sobre os episodios --
    discovery, holdout = split_holdout(all_episodes)
    print(f"\n== 11. HOLDOUT (70/30 por timeframe): {len(discovery)} / {len(holdout)} episodios ==")
    report["holdout"] = {"discovery": len(discovery), "holdout": len(holdout)}

    # -- 12: por timeframe --
    print("\n== 12. POR TIMEFRAME (episodios) ==")
    per_tf = {}
    for timeframe in [t.value for t in TIMEFRAMES]:
        group = [e for e in all_episodes if e.timeframe == timeframe]
        if not group:
            continue
        reverted = [e for e in group if e.reversed_after]
        with_re = sum(1 for e in group if e.reanchors_during > 0)
        per_tf[timeframe] = {
            "n": len(group),
            "reverteram": len(reverted),
            "com_reanchor_pct": round(with_re / len(group) * 100, 1),
            "lag_mediano": (
                round(
                    statistics.median(
                        [e.choch_lag_bars for e in reverted if e.choch_lag_bars is not None]
                    ),
                    1,
                )
                if any(e.choch_lag_bars is not None for e in reverted)
                else None
            ),
        }
        row = per_tf[timeframe]
        print(
            f"  {timeframe:>4} n={row['n']:>4} reverteram={row['reverteram']:>4} "
            f"com re-anchor={row['com_reanchor_pct']:>5.1f}% "
            f"lag={num(row['lag_mediano'], 0)}"
        )
    report["by_timeframe"] = per_tf
    report["zec_variants"] = zec_rows
    return report


def zec_detail() -> dict:
    """Secao 3/8: o que o re-anchor tentou e conseguiu na perna do ZEC."""
    symbol, timeframe, _ts = ZEC_BEARISH
    series = load_series(symbol, timeframe)
    out = {}
    for variant in (BASELINE, Variant("stale x0.25", stale_scale=0.25),
                    Variant("disp_atr=8", {"_stale_reanchor_displacement_atr": 8.0})):
        run, attempts = run_variant(symbol, timeframe, series, len(series), variant)
        window = [
            asdict(attempt)
            for attempt in attempts
            if "2026-03" <= attempt.timestamp <= "2026-07"
        ]
        rows = [
            asdict(row)
            for row in choch_rows(run)
            if "2026-04" <= row.timestamp <= "2026-08"
        ]
        out[variant.label] = {"tentativas": window, "choch": rows}
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--expanded", action="store_true")
    parser.add_argument("--windows", type=int, default=WINDOWS)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.symbols:
        symbols = args.symbols
    elif args.expanded:
        from research.choch_leg_opener import expanded_symbols

        symbols = expanded_symbols()
    else:
        symbols = SYMBOLS

    report = build(symbols, args.windows, VARIANTS)

    print("\n== 3/8. ZEC D1 -- tentativas de re-anchor na perna bullish ==")
    detail = zec_detail()
    report["zec_detail"] = detail
    for label, data in detail.items():
        print(f"  [{label}]")
        for attempt in data["tentativas"]:
            print(
                f"    {attempt['timestamp'][:10]} nivel {attempt['level']:>8.2f} "
                f"preco {attempt['current_price']:>8.2f} "
                f"{'MOVEU' if attempt['moved'] else 'recusado'}"
            )
        for row in data["choch"]:
            print(
                f"    -> CHoCH {row['timestamp'][:10]} {row['direction']:<7} "
                f"ref {num(row['reference_price'], 2)} lag {row['lag_bars']} "
                f"mov {num(row['move_completed_pct'])}%"
            )

    if args.json:
        args.json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"\n-> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
