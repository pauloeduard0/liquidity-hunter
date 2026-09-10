"""Etapa 6.0 -- o VWAP qualifica um evento SMC ja confirmado?

A pergunta desta etapa e deliberadamente MENOR que as duas anteriores, e e
essa reducao de escopo que a torna respondivel. A Etapa 5.0 perguntou se
existe uma leitura de "pressao atual" independente da estrutura (nao existe,
com os dados de hoje). A Etapa 5.1 perguntou se o Tide antecipa a transicao
(nao antecipa -- a cor do Tide E o `final_trend`). Aqui nao se antecipa nada:

> **o evento ja aconteceu.** Dado um BOS ou um CHoCH nao-provisional, o
> comportamento do preco em relacao a VWAP nos candles SEGUINTES separa os
> eventos que a maquina depois invalidou dos que ela depois confirmou?

Isso e qualidade de evento, nao direcao nova. Nada aqui vai para producao.

--------------------------------------------------------------------------
1. O QUE E "O VWAP DO TIDE", E POR QUE ELE E REUSADO E NAO RECOPIADO
--------------------------------------------------------------------------

A geometria da fita ja foi reconstruida e auditada na Etapa 5.1
(`research/tide_structural_transition.py`): VWAP ponderada por volume sobre
`hlc3` mais/menos **1 sigma** ponderado, com a ancora de producao por
timeframe (`_VWAP_ANCHOR_PERIOD`): SESSION ate H1, WEEK em H4, MONTH em D1/W1.
`tide_geometry` e `phase_series` sao importados de la, e nao reescritos --
duas definicoes do mesmo envelope no mesmo repositorio seria exatamente o tipo
de divergencia silenciosa que estas medicoes existem para evitar.

Do Tide fica de fora tudo que a auditoria da 5.1 reprovou como feature causal:

- **`convictionScale` nao entra.** Ele normaliza pelo p90 da janela INTEIRA,
  futuro incluso. E lookahead, legitimo numa leitura retrospectiva e proibido
  aqui.
- **Nada de fluxo** (secao 12 do pedido): sem `aggression`, sem CVD, sem OI,
  sem `control_score`. Esta rodada e SMC + VWAP puros. Se a combinacao pura
  nao funcionar, acrescentar fluxo por cima seria procurar um resultado.

Canais usados, todos causais no candle em que sao lidos: VWAP (`mid`), banda
(`span` = 1 sigma do lado em que o preco esta), inclinacao da VWAP, `phase`
(posicao no envelope, formula do `buildPhase`), e distancia em ATR e em sigma.

--------------------------------------------------------------------------
2. A POPULACAO E DE EVENTOS, NAO DE CANDLES
--------------------------------------------------------------------------

Cada linha do painel e **um evento estrutural nao-provisional**: um
`CHANGE_OF_CHARACTER` ou um `BREAK_OF_STRUCTURE`, no candle em que a producao
o confirma. `CHOCH_FAILED` nao gera linha -- ele e desfecho, nao populacao.

Para cada evento sao medidas duas coisas em tempos diferentes, e a separacao
entre elas e o unico ponto onde esta etapa pode se enganar sozinha:

- **aceitacao**, sobre os candles `e+1 .. e+N` (N em 1/3/5/10/20);
- **desfecho**, varrido a partir de `e+N+1`.

Se o desfecho ja tivesse se resolvido DENTRO da janela de aceitacao, a
aceitacao estaria lendo o proprio alvo. Por isso todo evento cujo desfecho
resolve em `<= N` candles e **excluido do horizonte N** (devolve `None`, nao
zero) -- e por isso o `n` cai quando N cresce. Um painel que crescesse com N
estaria medindo o passado com o futuro.

--------------------------------------------------------------------------
3. OS DESFECHOS
--------------------------------------------------------------------------

Varredura para frente, ate `OUTCOME_HORIZON` candles, parando no primeiro:

| desfecho | o que aconteceu |
|---|---|
| `failed` | `CHOCH_FAILED` da mesma direcao: **a maquina invalidou o evento** |
| `continued` | `BOS` nao-provisional da mesma direcao: virou estrutura |
| `reversed` | `CHoCH` da direcao OPOSTA: outro flip desfez este |
| `open` | nada dentro do horizonte (borda viva / fim de janela) |

O alvo principal (secao 5) e `failed` x `continued`. `reversed` e `open` nao
entram no denominador: nenhum dos dois e "o evento se sustentou", e enfia-los
em qualquer um dos lados e o jeito mais facil de fabricar separacao.

Para o BOS (secao 7) o par e o mesmo com outra leitura: `continued` = novo BOS
na mesma direcao, `failed`/`reversed` = a estrutura virou antes de continuar.

--------------------------------------------------------------------------
4. O CONTROLE QUE DECIDE (secoes 9 e 10)
--------------------------------------------------------------------------

Aceitacao pode ser momentum disfarcado: se o preco andou muito na direcao do
evento, ele obviamente ficou do lado certo da VWAP, e a VWAP nao acrescentou
nada. O controle e o mesmo desenho que derrubou a Etapa 5.1:

> toda taxa disparada e lida contra a taxa do **mesmo estrato**, e o estrato
> severo inclui o **quintil de deslocamento pos-evento** `disp_N`.

Estrato base: `(timeframe, direcao, tercil de volatilidade)`. Estrato severo:
`+ quintil de disp_N`. Entre eventos que andaram o MESMO tanto nos mesmos N
candles, a aceitacao ainda separa? E esse numero -- e nao a AUC -- que
responde a secao 9.

--------------------------------------------------------------------------
5. O QUE ESTA ETAPA NAO FAZ
--------------------------------------------------------------------------

Nao muda CHoCH, nao muda `final_trend`, nao cria pressao, nao usa candle
anterior ao evento para antecipa-lo, e nao propoe producao. O maximo que um
resultado positivo autoriza e um ROTULO derivado (`acceptance = confirmed /
weak`) desenhado sobre um evento que a maquina ja emitiu.

Uso:

    poetry run python -m research.smc_vwap_acceptance --expanded
    poetry run python -m research.smc_vwap_acceptance --symbols BTCUSDT --json out.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from array import array
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    TimeFrame,
)
from research.choch_leg_opener import DISCOVERY_FRACTION, TIMEFRAMES, WINDOWS
from research.current_market_pressure import (
    ATR_WINDOW,
    atr_pct_series,
    auc,
    block_permutation,
    quantile,
    trend_by_index,
)
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series
from research.tide_structural_transition import (
    TideGeometry,
    phase_series,
    tide_geometry,
)

# --------------------------------------------------------------------------
# parametros -- fixados antes de olhar qualquer numero
# --------------------------------------------------------------------------

#: Janelas de aceitacao pos-evento (secao 3 do pedido).
WINDOWS_N = (1, 3, 5, 10, 20)

#: Ate onde a varredura de desfecho vai. Em D1 sao ~4 meses; em M15, ~30h.
OUTCOME_HORIZON = 120

#: Um "reteste" e o preco voltar a menos de meio sigma da VWAP.
RETEST_SIGMA = 0.5

#: Placebo de bloco, igual ao das etapas anteriores.
PLACEBO_BLOCK = 64
PLACEBO_SEED = 20260911

#: Piso de amostra para publicar uma taxa casada.
MIN_N = 200

#: Casos obrigatorios da secao 18.
CASES = (
    ("ZECUSDT", TimeFrame.D1),
    ("BTCUSDT", TimeFrame.H1),
    ("ETHUSDT", TimeFrame.H1),
    ("ETHUSDT", TimeFrame.H4),
)

OUTCOMES = ("failed", "continued", "reversed", "open")
OUTCOME_CODE = {name: code for code, name in enumerate(OUTCOMES)}

KIND_CHOCH = 0
KIND_BOS = 1


# --------------------------------------------------------------------------
# features: no evento, e depois do evento
# --------------------------------------------------------------------------


def _sign(direction: MarketDirection) -> int:
    return 1 if direction is MarketDirection.BULLISH else -1


AT_EVENT = (
    "side",
    "dist_atr",
    "dist_sigma",
    "slope_atr",
    "phase",
    "band_width",
)

#: Por janela N. `disp` e o CONTROLE (momentum puro), nao uma feature do VWAP.
PER_WINDOW = (
    "same_side",
    "reclaim",
    "cross",
    "first_cross",
    "ext",
    "mean_dist",
    "outside",
    "retest_reject",
    "slope",
    "disp",
)

#: As que a secao 11 manda testar individualmente, por janela.
ACCEPTANCE = tuple(name for name in PER_WINDOW if name != "disp")


def window_names(prefix: str = "") -> tuple[str, ...]:
    return tuple(f"{prefix}{name}_{n}" for n in WINDOWS_N for name in PER_WINDOW)


FEATURES = AT_EVENT + window_names()


def event_features(
    candles: Sequence[Candle],
    atr: Sequence[float],
    geometry: TideGeometry,
    phase: Sequence[float | None],
    index: int,
    direction: MarketDirection,
) -> dict[str, float] | None:
    """Leitura do envelope NO candle do evento, ja assinada pela direcao dele.

    Positivo = coerente com o evento (um CHoCH bearish com o preco abaixo da
    VWAP tem `side = +1`). A assinatura acontece so aqui, uma vez, para que
    bullish e bearish nunca precisem de dois caminhos de codigo.
    """
    mid = geometry.mid[index]
    span = geometry.span[index]
    if mid is None or span is None or span <= 0:
        return None
    unit = atr[index] * candles[index].close
    if unit <= 0:
        return None
    sign = _sign(direction)
    close = candles[index].close
    out = {
        "side": float(sign * (1 if close >= mid else -1)),
        "dist_atr": sign * (close - mid) / unit,
        "dist_sigma": sign * (close - mid) / span,
        "band_width": span / abs(mid) if mid else float("nan"),
        "phase": float("nan"),
        "slope_atr": float("nan"),
    }
    value = phase[index]
    if value is not None:
        out["phase"] = sign * value
    slope = _slope_atr(geometry, atr, candles, index)
    if slope is not None:
        out["slope_atr"] = sign * slope
    return out


def _slope_atr(
    geometry: TideGeometry,
    atr: Sequence[float],
    candles: Sequence[Candle],
    index: int,
    lag: int = 5,
) -> float | None:
    """Inclinacao da VWAP em ATR por candle, `None` na virada de ancora.

    A fita quebra em cada virada de periodo, e ali um "slope" seria a
    diferenca entre duas acumulacoes diferentes -- numero sem significado.
    """
    if index < lag:
        return None
    now = geometry.mid[index]
    before = geometry.mid[index - lag]
    if now is None or before is None:
        return None
    unit = atr[index] * candles[index].close
    if unit <= 0:
        return None
    return (now - before) / (unit * lag)


#: Fracao minima da janela que precisa ter fita para a leitura valer.
MIN_READABLE = 0.6


def acceptance_features(
    candles: Sequence[Candle],
    atr: Sequence[float],
    geometry: TideGeometry,
    index: int,
    direction: MarketDirection,
    n: int,
) -> dict[str, float] | None:
    """Comportamento relativo a VWAP nos candles `index+1 .. index+n`.

    Nenhuma destas leituras olha alem de `index + n`; o alvo comeca em
    `index + n + 1`. Cada candle da janela e lido contra a VWAP DELE, nao
    contra a VWAP do evento -- e o que a fita mostra, e o que faz "aceitacao"
    significar algo quando a media anda.

    **A fita quebra em cada virada de ancora**, e isso nao e um detalhe: em H1
    com ancora de SESSION uma janela de 20 candles atravessa quase sempre uma
    virada. Exigir a janela inteira legivel apagaria o timeframe. Entao os
    candles sem fita sao PULADOS, e a janela so e descartada quando sobra
    menos de `MIN_READABLE` dela -- caso em que as colunas de aceitacao ficam
    `nan` e o `disp` continua valido, porque o controle nao depende da VWAP.
    """
    if index + n >= len(candles):
        return None
    sign = _sign(direction)
    unit = atr[index] * candles[index].close
    if unit <= 0:
        return None
    origin = candles[index].close
    disp = sign * (candles[index + n].close - origin) / unit
    blank = {name: float("nan") for name in PER_WINDOW}
    blank["disp"] = disp

    sides: list[int] = []
    dists: list[float] = []
    steps: list[int] = []
    touched = False
    retest_reject = 0
    for step in range(1, n + 1):
        at = index + step
        mid = geometry.mid[at]
        span = geometry.span[at]
        if mid is None or span is None or span <= 0:
            continue
        distance = sign * (candles[at].close - mid) / span
        sides.append(1 if distance >= 0 else -1)
        dists.append(distance)
        steps.append(step)
        # Reteste seguido de rejeicao: o preco voltou para perto da VWAP e
        # depois fechou de novo na direcao do evento. Um reteste sozinho nao
        # e informacao; o que se pergunta e se a VWAP SEGUROU.
        if touched and distance > RETEST_SIGMA:
            retest_reject = 1
        if abs(distance) <= RETEST_SIGMA:
            touched = True

    if len(dists) < max(1, math.ceil(MIN_READABLE * n)):
        return blank

    crosses = sum(1 for a, b in zip(sides, sides[1:], strict=False) if a != b)
    first_wrong = next((steps[i] for i, s in enumerate(sides) if s < 0), n + 1)
    slope = _slope_atr(geometry, atr, candles, index + n)
    return {
        "same_side": sum(1 for s in sides if s > 0) / len(sides),
        "reclaim": 1.0 if first_wrong <= n else 0.0,
        "cross": float(crosses),
        "first_cross": float(first_wrong),
        "ext": max(dists),
        "mean_dist": statistics.fmean(dists),
        "outside": sum(1 for d in dists if d >= 1.0) / len(dists),
        "retest_reject": float(retest_reject),
        "slope": float("nan") if slope is None else sign * slope,
        "disp": disp,
    }


# --------------------------------------------------------------------------
# desfechos
# --------------------------------------------------------------------------


def outcome_of(
    events: Sequence[MarketStructure],
    by_ts: dict[Any, int],
    index: int,
    direction: MarketDirection,
    limit: int,
) -> tuple[str, int]:
    """O primeiro desfecho depois de `index`, e em quantos candles.

    `CHOCH_FAILED` da MESMA direcao e a invalidacao deste evento: o detector
    emite a falha com a direcao do CHoCH que caiu (e o `final_trend` vira o
    oposto). Um BOS na mesma direcao e a confirmacao. Um CHoCH oposto e um
    terceiro caso, que nao e nenhum dos dois.
    """
    best: tuple[int, str] | None = None
    for event in events:
        if event.provisional:
            continue
        at = by_ts.get(event.timestamp)
        if at is None or at <= index or at > index + limit:
            continue
        kind: str | None = None
        if event.event is StructureEvent.CHOCH_FAILED and event.direction is direction:
            kind = "failed"
        elif (
            event.event is StructureEvent.BREAK_OF_STRUCTURE
            and event.direction is direction
        ):
            kind = "continued"
        elif (
            event.event is StructureEvent.CHANGE_OF_CHARACTER
            and event.direction is not direction
        ):
            kind = "reversed"
        if kind is None:
            continue
        if best is None or at - index < best[0]:
            best = (at - index, kind)
    return ("open", -1) if best is None else (best[1], best[0])


def trend_duration(
    trends: Sequence[MarketDirection | None], index: int, direction: MarketDirection
) -> int:
    """Candles ate a estrutura confirmada deixar de ser `direction`."""
    for at in range(index, len(trends)):
        if trends[at] is not direction:
            return at - index
    return len(trends) - index


def excursion(
    candles: Sequence[Candle],
    atr: Sequence[float],
    index: int,
    direction: MarketDirection,
    horizon: int,
) -> tuple[float, float] | None:
    """MFE/MAE na direcao do evento, em ATR do candle do evento."""
    if index + horizon >= len(candles) or atr[index] <= 0:
        return None
    unit = atr[index] * candles[index].close
    if unit <= 0:
        return None
    sign = _sign(direction)
    moves = [
        sign * (candles[j].close - candles[index].close) / unit
        for j in range(index + 1, index + horizon + 1)
    ]
    return max(moves), -min(moves)


# --------------------------------------------------------------------------
# painel
# --------------------------------------------------------------------------

FLOAT_COLUMNS = (*FEATURES, *window_names("pl_"), "atr", "mfe40", "mae40")
INT_COLUMNS = (
    "tf",
    "kind",
    "dir",
    "ts",
    "outcome",
    "lead",
    "duration",
    "vol",
    *(f"disp_bin_{n}" for n in WINDOWS_N),
)


class Store:
    """Um evento por linha, em arrays paralelos."""

    def __init__(self) -> None:
        self.f: dict[str, array] = {name: array("d") for name in FLOAT_COLUMNS}
        self.i: dict[str, array] = {name: array("q") for name in INT_COLUMNS}

    def __len__(self) -> int:
        return len(self.i["ts"])


TF_CODE = {tf: code for code, tf in enumerate(TIMEFRAMES)}
TF_BY_CODE = {code: tf for tf, code in TF_CODE.items()}


def collect_combo(
    symbol: str,
    timeframe: TimeFrame,
    series: Sequence[Candle],
    end: int,
    store: Store,
    rng: random.Random,
    cases: dict[str, list[dict]],
) -> int:
    """Uma janela de producao inteira. Devolve quantos eventos entraram."""
    run = dd._run_internal_structure(
        provider=SliceProvider(list(series[end - LIMIT - BUFFER : end])),
        symbol=symbol,
        timeframe=timeframe,
        limit=LIMIT,
        confluence_filter=True,
    )
    candles = run.candles
    by_ts = {candle.timestamp: index for index, candle in enumerate(candles)}
    atr = atr_pct_series(candles, ATR_WINDOW)
    trends = trend_by_index(run.events, by_ts, len(candles))
    geometry = tide_geometry(candles, symbol, timeframe)
    phase = phase_series(candles, geometry)
    permutation = block_permutation(len(candles), rng)

    # O placebo precisa das features de aceitacao em QUALQUER candle, nao so
    # nos de evento: e assim que se pergunta "esta separacao existiria num
    # candle qualquer do mesmo periodo?".
    cache: dict[tuple[int, int, int], dict[str, float] | None] = {}

    def accept_at(at: int, direction: MarketDirection, n: int) -> dict[str, float] | None:
        key = (at, _sign(direction), n)
        if key not in cache:
            cache[key] = acceptance_features(candles, atr, geometry, at, direction, n)
        return cache[key]

    used = 0
    limit = len(candles) - max(WINDOWS_N) - 1
    for event in sorted(run.events, key=lambda e: e.timestamp):
        if event.provisional or event.event not in (
            StructureEvent.CHANGE_OF_CHARACTER,
            StructureEvent.BREAK_OF_STRUCTURE,
        ):
            continue
        index = by_ts.get(event.timestamp)
        if index is None or index < ATR_WINDOW or index >= limit:
            continue
        direction = event.direction
        at_event = event_features(candles, atr, geometry, phase, index, direction)
        if at_event is None:
            continue
        # Cada N e independente: uma janela longa ilegivel nao pode apagar a
        # curta, ou o painel de N=1 herdaria a cobertura do de N=20.
        windows = {n: accept_at(index, direction, n) for n in WINDOWS_N}
        if all(value is None for value in windows.values()):
            continue
        moves = excursion(candles, atr, index, direction, 40)
        if moves is None:
            continue
        outcome, lead = outcome_of(run.events, by_ts, index, direction, OUTCOME_HORIZON)

        for name in AT_EVENT:
            store.f[name].append(at_event[name])
        placebo = permutation[index]
        for n in WINDOWS_N:
            block = windows[n]
            fake = accept_at(placebo, direction, n) if placebo < limit else None
            for name in PER_WINDOW:
                store.f[f"{name}_{n}"].append(
                    float("nan") if block is None else block[name]
                )
                store.f[f"pl_{name}_{n}"].append(
                    float("nan") if fake is None else fake[name]
                )
            store.i[f"disp_bin_{n}"].append(0)  # quintil resolvido com o painel inteiro
        store.f["atr"].append(atr[index])
        store.f["mfe40"].append(moves[0])
        store.f["mae40"].append(moves[1])
        store.i["tf"].append(TF_CODE[timeframe])
        store.i["kind"].append(
            KIND_CHOCH if event.event is StructureEvent.CHANGE_OF_CHARACTER else KIND_BOS
        )
        store.i["dir"].append(_sign(direction))
        store.i["ts"].append(int(candles[index].timestamp.timestamp()))
        store.i["outcome"].append(OUTCOME_CODE[outcome])
        store.i["lead"].append(lead)
        store.i["duration"].append(trend_duration(trends, index, direction))
        store.i["vol"].append(0)
        used += 1

        key = f"{symbol}_{timeframe.value}"
        if key in cases:
            row = {
                "timestamp": candles[index].timestamp.isoformat(),
                "event": event.event.value,
                "direction": direction.value,
                "close": candles[index].close,
                "outcome": outcome,
                "lead": lead,
                "mfe40": moves[0],
                "mae40": moves[1],
                **{f"at_{name}": at_event[name] for name in AT_EVENT},
            }
            for n in WINDOWS_N:
                block = windows[n] or {}
                row[f"same_side_{n}"] = block.get("same_side", float("nan"))
                row[f"reclaim_{n}"] = block.get("reclaim", float("nan"))
                row[f"disp_{n}"] = block.get("disp", float("nan"))
                row[f"slope_{n}"] = block.get("slope", float("nan"))
            cases[key].append(row)
    return used


def resolve_bins(store: Store) -> None:
    """Tercil de volatilidade e quintil de deslocamento, POR timeframe.

    Por timeframe porque um corte global transformaria "regime de
    volatilidade" num rotulo de timeframe: o ATR% tipico de um M15 e o de um
    D1 nao vivem na mesma escala.
    """
    for code in set(store.i["tf"]):
        rows = [i for i in range(len(store)) if store.i["tf"][i] == code]
        values = sorted(store.f["atr"][i] for i in rows if store.f["atr"][i] > 0)
        if len(values) >= 3:
            low = quantile(values, 1 / 3)
            high = quantile(values, 2 / 3)
            for i in rows:
                got = store.f["atr"][i]
                store.i["vol"][i] = 0 if got <= low else (1 if got <= high else 2)
        for n in WINDOWS_N:
            disps = sorted(
                store.f[f"disp_{n}"][i]
                for i in rows
                if not math.isnan(store.f[f"disp_{n}"][i])
            )
            if len(disps) < 5:
                continue
            cuts = [quantile(disps, q / 5) for q in range(1, 5)]
            for i in rows:
                got = store.f[f"disp_{n}"][i]
                store.i[f"disp_bin_{n}"][i] = (
                    -1 if math.isnan(got) else sum(1 for cut in cuts if got > cut)
                )


# --------------------------------------------------------------------------
# alvo e controle
# --------------------------------------------------------------------------


def hit_outcome(store: Store, n: int, positives: Sequence[str]) -> Any:
    """1 = o evento nao se sustentou, 0 = confirmou, `None` = nao elegivel.

    Nao elegivel cobre tres coisas distintas e igualmente desqualificantes: o
    desfecho ja tinha se resolvido dentro da janela de aceitacao (a feature
    estaria lendo o proprio alvo), o desfecho ficou fora do par pedido, ou
    nada aconteceu dentro do horizonte.
    """
    codes = {OUTCOME_CODE[name] for name in positives}

    def inner(index: int) -> int | None:
        outcome = store.i["outcome"][index]
        lead = store.i["lead"][index]
        if lead <= n:
            return None
        if outcome in codes:
            return 1
        if outcome == OUTCOME_CODE["continued"]:
            return 0
        return None

    return inner


def hit_failed(store: Store, n: int) -> Any:
    """O alvo PRINCIPAL da secao 5: `CHOCH_FAILED` x BOS de continuacao."""
    return hit_outcome(store, n, ("failed",))


def hit_broken(store: Store, n: int) -> Any:
    """O alvo do BOS (secao 7), e o secundario do CHoCH.

    Um BOS nao morre por `CHOCH_FAILED` -- ele morre por um CHoCH oposto. E
    para o CHoCH este par e o que tem massa: `reversed` (outro flip desfez
    este) e tao "nao se sustentou" quanto `failed`, e enfia-lo no denominador
    do alvo principal seria trocar a pergunta da secao 5 sem avisar, entao ele
    e reportado **ao lado** e nunca no lugar.
    """
    return hit_outcome(store, n, ("failed", "reversed"))


TARGETS = {"failed": hit_failed, "broken": hit_broken}


def stratum(store: Store, index: int, n: int, *, by_disp: bool = False) -> tuple[int, ...]:
    """(timeframe, direcao, volatilidade), opcionalmente + quintil de `disp_N`.

    Com `by_disp` a pergunta vira a da secao 10: entre eventos que andaram o
    mesmo tanto nos mesmos N candles, a aceitacao ainda separa?
    """
    base = (store.i["tf"][index], store.i["dir"][index], store.i["vol"][index])
    return (*base, store.i[f"disp_bin_{n}"][index]) if by_disp else base


def matched_rate(
    store: Store,
    fired: Sequence[int],
    pool: Sequence[int],
    hit: Any,
    n: int,
    *,
    by_disp: bool = False,
    min_n: int = MIN_N,
) -> dict[str, Any]:
    """Taxa disparada x taxa do MESMO estrato, com o mesmo peso por estrato."""
    base: dict[tuple[int, ...], list[int]] = {}
    for index in pool:
        got = hit(index)
        if got is None:
            continue
        base.setdefault(stratum(store, index, n, by_disp=by_disp), []).append(got)

    observed = 0
    total = 0
    expected = 0.0
    for index in fired:
        got = hit(index)
        if got is None:
            continue
        rows = base.get(stratum(store, index, n, by_disp=by_disp))
        if not rows:
            continue
        observed += got
        total += 1
        expected += sum(rows) / len(rows)
    if total < min_n:
        return {"n": total, "rate": None, "matched": None, "lift_pp": None}
    return {
        "n": total,
        "rate": observed / total,
        "matched": expected / total,
        "lift_pp": 100.0 * (observed - expected) / total,
    }


# --------------------------------------------------------------------------
# recortes
# --------------------------------------------------------------------------


def cut_by_tf(store: Store, code: int) -> int:
    """O corte 70/30 por timeframe, em timestamp (secao 16)."""
    stamps = sorted(store.i["ts"][i] for i in range(len(store)) if store.i["tf"][i] == code)
    if not stamps:
        return 0
    return stamps[int(len(stamps) * DISCOVERY_FRACTION)]


def rows_for(
    store: Store,
    code: int | None = None,
    cut: int = 0,
    sample: str = "all",
    **filters: int,
) -> list[int]:
    out = []
    for index in range(len(store)):
        if code is not None and store.i["tf"][index] != code:
            continue
        if sample == "discovery" and store.i["ts"][index] >= cut:
            continue
        if sample == "holdout" and store.i["ts"][index] < cut:
            continue
        if any(store.i[name][index] != value for name, value in filters.items()):
            continue
        out.append(index)
    return out


def univariate(
    store: Store, rows: Sequence[int], n: int, target: str = "failed"
) -> dict[str, Any]:
    """AUC de cada feature contra `failed`, com o placebo de bloco ao lado.

    O sinal e orientado para que AUC > 0,5 signifique "mais desta leitura,
    mais falha". As features de aceitacao entram NEGADAS por isso: mais
    aceitacao deveria significar menos falha.
    """
    out: dict[str, Any] = {}
    hit = TARGETS[target](store, n)
    columns = [(name, name, 1.0) for name in AT_EVENT]
    columns += [(f"{name}_{n}", f"{name}_{n}", -1.0) for name in ACCEPTANCE]
    columns += [(f"disp_{n}", f"disp_{n}", -1.0)]
    for label, column, orientation in columns:
        entry: dict[str, Any] = {}
        for source, tag in ((column, "auc"), (f"pl_{column}", "placebo")):
            if source not in store.f:
                continue
            values: list[float] = []
            labels: list[int] = []
            for index in rows:
                got = hit(index)
                raw = store.f[source][index]
                if got is None or math.isnan(raw):
                    continue
                values.append(orientation * raw)
                labels.append(got)
            got_auc = auc(values, labels)
            if got_auc is None:
                continue
            entry[tag] = got_auc[0]
            entry["n"] = got_auc[1]
        if entry:
            out[label] = entry
    return out


def distribution(
    store: Store, rows: Sequence[int], column: str, n: int, target: str = "failed"
) -> dict[str, Any]:
    """Medianas e p25/p75 dos dois grupos, e o overlap entre eles (secao 19)."""
    hit = TARGETS[target](store, n)
    groups: dict[int, list[float]] = {0: [], 1: []}
    for index in rows:
        got = hit(index)
        raw = store.f[column][index]
        if got is None or math.isnan(raw):
            continue
        groups[got].append(raw)
    if len(groups[0]) < 30 or len(groups[1]) < 30:
        return {}
    out: dict[str, Any] = {}
    for label, name in ((0, "continued"), (1, "broke")):
        values = sorted(groups[label])
        out[name] = {
            "n": len(values),
            "p25": quantile(values, 0.25),
            "median": quantile(values, 0.5),
            "p75": quantile(values, 0.75),
        }
    lo = max(out["continued"]["p25"], out["broke"]["p25"])
    hi = min(out["continued"]["p75"], out["broke"]["p75"])
    span = max(
        out["continued"]["p75"] - out["continued"]["p25"],
        out["broke"]["p75"] - out["broke"]["p25"],
    )
    out["overlap"] = max(0.0, hi - lo) / span if span > 0 else None
    return out


# --------------------------------------------------------------------------
# a regra (secao 20) -- no maximo duas condicoes, escolhida so no discovery
# --------------------------------------------------------------------------


class Rule:
    """Uma regra de aceitacao: no maximo duas condicoes, por construcao."""

    def __init__(self, label: str, n: int, terms: Sequence[tuple[str, str, float]]) -> None:
        assert len(terms) <= 2, "secao 20: no maximo duas condicoes"
        self.label = label
        self.n = n
        self.terms = tuple(terms)

    def holds(self, store: Store, index: int) -> bool:
        for column, op, threshold in self.terms:
            value = store.f[f"{column}_{self.n}"][index]
            if math.isnan(value):
                return False
            if op == ">=" and not value >= threshold:
                return False
            if op == "<=" and not value <= threshold:
                return False
        return True


def rule_grid() -> list[Rule]:
    """A grade inteira, pequena de proposito.

    A secao 20 pede para nao testar centenas de regras, e o motivo nao e
    computacional: cada regra testada e uma chance a mais de que a melhor seja
    a mais sortuda. Sao quatro formas conceituais x cinco janelas.
    """
    grid: list[Rule] = []
    for n in WINDOWS_N:
        grid.append(Rule(f"same_side>=0.8 & slope>=0 (N={n})", n, [
            ("same_side", ">=", 0.8), ("slope", ">=", 0.0)
        ]))
        grid.append(Rule(f"same_side==1 (N={n})", n, [("same_side", ">=", 1.0)]))
        grid.append(Rule(f"reclaim==0 (N={n})", n, [("reclaim", "<=", 0.0)]))
        grid.append(Rule(f"mean_dist>=0.5 & cross<=1 (N={n})", n, [
            ("mean_dist", ">=", 0.5), ("cross", "<=", 1.0)
        ]))
    return grid


def rule_metrics(
    store: Store,
    rule: Rule,
    rows: Sequence[int],
    *,
    min_n: int = MIN_N,
    target: str = "failed",
) -> dict[str, Any]:
    fired = [index for index in rows if rule.holds(store, index)]
    hit = TARGETS[target](store, rule.n)
    eligible = [index for index in rows if hit(index) is not None]
    out: dict[str, Any] = {
        "label": rule.label,
        "n": rule.n,
        "target": target,
        "coverage": len(fired) / len(rows) if rows else None,
        "n_fired": len(fired),
        "accepted": matched_rate(store, fired, rows, hit, rule.n, min_n=min_n),
        "accepted_vs_disp": matched_rate(
            store, fired, rows, hit, rule.n, by_disp=True, min_n=min_n
        ),
    }
    fired_set = set(fired)
    rejected = [index for index in eligible if index not in fired_set]
    out["rejected"] = matched_rate(store, rejected, rows, hit, rule.n, min_n=min_n)
    return out


# --------------------------------------------------------------------------
# construcao do relatorio
# --------------------------------------------------------------------------


def build(symbols: Sequence[str], windows: int) -> dict[str, Any]:
    store = Store()
    cases: dict[str, list[dict]] = {f"{s}_{tf.value}": [] for s, tf in CASES}
    rng = random.Random(PLACEBO_SEED)
    combos = 0
    events = 0

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
                    events += collect_combo(
                        symbol,
                        timeframe,
                        series,
                        end,
                        store,
                        rng,
                        cases if window == 0 else {},
                    )
                except Exception as error:  # noqa: BLE001 - feed morto
                    print(f"! janela invalida: {symbol} {timeframe.value} w{window}: {error}")
                    continue
                combos += 1

    resolve_bins(store)
    print(f"combos medidos: {combos}  eventos: {events}")

    report: dict[str, Any] = {
        "combos": combos,
        "events": events,
        "windows_n": list(WINDOWS_N),
        "outcome_horizon": OUTCOME_HORIZON,
        "by_tf": {},
        "cases": cases,
    }

    for timeframe in TIMEFRAMES:
        code = TF_CODE[timeframe]
        cut = cut_by_tf(store, code)
        block: dict[str, Any] = {"cut": cut}
        for kind, kind_name in ((KIND_CHOCH, "choch"), (KIND_BOS, "bos")):
            rows = rows_for(store, code, kind=kind)
            if not rows:
                continue
            primary = "failed" if kind == KIND_CHOCH else "broken"
            entry: dict[str, Any] = {"n": len(rows), "primary": primary}
            entry["outcomes"] = {
                name: sum(1 for i in rows if store.i["outcome"][i] == OUTCOME_CODE[name])
                for name in OUTCOMES
            }
            # Os dois alvos lado a lado, nunca no lugar um do outro: `failed`
            # e a pergunta literal da secao 5, `broken` acrescenta `reversed`
            # (outro flip desfez este), que e onde mora a massa da amostra.
            entry["by_n"] = {}
            for n in WINDOWS_N:
                sub: dict[str, Any] = {}
                for target in TARGETS:
                    hit = TARGETS[target](store, n)
                    eligible = [i for i in rows if hit(i) is not None]
                    sub[target] = {
                        "eligible": len(eligible),
                        "base": (
                            sum(hit(i) for i in eligible) / len(eligible)
                            if eligible
                            else None
                        ),
                        "auc": univariate(store, rows, n, target),
                        "dist_same_side": distribution(
                            store, rows, f"same_side_{n}", n, target
                        ),
                    }
                entry["by_n"][n] = sub

            # direcoes separadas (secao 15), no melhor horizonte declarado
            entry["by_direction"] = {}
            for label, sign in (("bullish", 1), ("bearish", -1)):
                sub_rows = rows_for(store, code, kind=kind, **{"dir": sign})
                if not sub_rows:
                    continue
                entry["by_direction"][label] = {
                    "n": len(sub_rows),
                    "auc": univariate(store, sub_rows, 5, primary).get("same_side_5", {}),
                }

            # continuacao (secao 6), so para os CHoCH validos
            if kind == KIND_CHOCH:
                valid = [i for i in rows if store.i["outcome"][i] == OUTCOME_CODE["continued"]]
                if valid:
                    leads = sorted(store.i["lead"][i] for i in valid)
                    entry["continuation"] = {
                        "n": len(valid),
                        "bos_lead_median": quantile([float(v) for v in leads], 0.5),
                        "mfe40_median": quantile(
                            sorted(store.f["mfe40"][i] for i in valid), 0.5
                        ),
                        "mae40_median": quantile(
                            sorted(store.f["mae40"][i] for i in valid), 0.5
                        ),
                        "duration_median": quantile(
                            sorted(float(store.i["duration"][i]) for i in valid), 0.5
                        ),
                    }
            block[kind_name] = entry
        report["by_tf"][timeframe.value] = block

    # ---- a regra: escolhida no discovery do painel INTEIRO, congelada ------
    # `cut` global por timeframe: um evento e discovery se e discovery no SEU
    # timeframe, senao o D1 (que tem menos eventos e mais anos) decidiria o
    # corte de todo mundo.
    cuts = {code: cut_by_tf(store, code) for code in TF_CODE.values()}
    discovery = [
        i
        for i in rows_for(store, None, kind=KIND_CHOCH)
        if store.i["ts"][i] < cuts[store.i["tf"][i]]
    ]
    holdout = [
        i
        for i in rows_for(store, None, kind=KIND_CHOCH)
        if store.i["ts"][i] >= cuts[store.i["tf"][i]]
    ]
    grid = []
    for rule in rule_grid():
        entry = rule_metrics(store, rule, discovery, min_n=100)
        entry["broken"] = rule_metrics(
            store, rule, discovery, min_n=100, target="broken"
        )
        grid.append(entry)
    report["rules_tested"] = len(grid)
    report["grid"] = grid
    scored = [
        entry
        for entry in grid
        if entry["accepted_vs_disp"]["lift_pp"] is not None
        and entry["coverage"] is not None
        and 0.05 <= entry["coverage"] <= 0.95
    ]
    chosen = min(scored, key=lambda e: e["accepted_vs_disp"]["lift_pp"]) if scored else None
    report["chosen"] = chosen
    if chosen is not None:
        rule = next(r for r in rule_grid() if r.label == chosen["label"])
        report["holdout"] = rule_metrics(store, rule, holdout, min_n=100)
        report["by_tf_rule"] = {}
        for timeframe in TIMEFRAMES:
            code = TF_CODE[timeframe]
            sub = rows_for(store, code, kind=KIND_CHOCH)
            if sub:
                report["by_tf_rule"][timeframe.value] = rule_metrics(
                    store, rule, sub, min_n=50
                )
        report["holdout_broken"] = rule_metrics(
            store, rule, holdout, min_n=100, target="broken"
        )
        bos_rows = rows_for(store, None, kind=KIND_BOS)
        report["bos_rule"] = rule_metrics(
            store, rule, bos_rows, min_n=100, target="broken"
        )
    return report


# --------------------------------------------------------------------------
# saida
# --------------------------------------------------------------------------


def num(value: float | None, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "  --  "
    return f"{value:.{digits}f}"


def pct(value: float | None) -> str:
    return "  --  " if value is None else f"{100 * value:5.1f}%"


def signed_pp(value: float | None) -> str:
    return "  --   " if value is None else f"{value:+6.1f}pp"


def print_report(report: dict[str, Any]) -> None:
    print()
    print("=" * 78)
    print("ETAPA 6.0 -- SMC x VWAP ACCEPTANCE")
    print("=" * 78)
    print(f"combos {report['combos']}  eventos {report['events']}  "
          f"horizonte de desfecho {report['outcome_horizon']}")

    for tf, block in report["by_tf"].items():
        for kind in ("choch", "bos"):
            entry = block.get(kind)
            if not entry:
                continue
            print()
            print(f"--- {tf} / {kind.upper()}  n={entry['n']} ---")
            counts = entry["outcomes"]
            total = sum(counts.values()) or 1
            print("  desfechos: " + "  ".join(
                f"{name} {counts[name]} ({100 * counts[name] / total:.0f}%)"
                for name in OUTCOMES
            ))
            print(f"  {'alvo':>7} {'N':>3} {'eleg':>6} {'base':>7} | "
                  f"{'same_side':>9} {'plac':>6} | {'reclaim':>8} | "
                  f"{'mean_dist':>9} | {'disp':>7} | {'side@ev':>8}")
            for target in TARGETS:
                for n, sub in entry["by_n"].items():
                    got = sub[target]
                    aucs = got["auc"]
                    cells = [
                        num(aucs.get(name, {}).get(tag))
                        for name, tag in (
                            (f"same_side_{n}", "auc"),
                            (f"same_side_{n}", "placebo"),
                            (f"reclaim_{n}", "auc"),
                            (f"mean_dist_{n}", "auc"),
                            (f"disp_{n}", "auc"),
                            ("side", "auc"),
                        )
                    ]
                    print(f"  {target:>7} {n:>3} {got['eligible']:>6} "
                          f"{pct(got['base'])} | {cells[0]:>9} {cells[1]:>6} | "
                          f"{cells[2]:>8} | {cells[3]:>9} | {cells[4]:>7} | "
                          f"{cells[5]:>8}")
            if "continuation" in entry:
                c = entry["continuation"]
                print(f"  continuacao (n={c['n']}): BOS em {num(c['bos_lead_median'], 1)} velas, "
                      f"MFE40 {num(c['mfe40_median'], 2)} / MAE40 {num(c['mae40_median'], 2)} ATR, "
                      f"duracao {num(c['duration_median'], 0)} velas")
            for label, sub in entry.get("by_direction", {}).items():
                print(f"  {label:>8}: n={sub['n']}  AUC same_side_5 {num(sub['auc'].get('auc'))}")

    print()
    print(f"--- regra ({report['rules_tested']} testadas, escolhida so no discovery) ---")
    for entry in report["grid"]:
        acc = entry["accepted"]
        print(f"  {entry['label']:<34} cov {pct(entry['coverage'])} "
              f"aceito {pct(acc['rate'])} vs estrato {pct(acc['matched'])} "
              f"= {signed_pp(acc['lift_pp'])} | "
              f"casado {signed_pp(entry['accepted_vs_disp']['lift_pp'])} | "
              f"broken {signed_pp(entry['broken']['accepted']['lift_pp'])}"
              f" / casado {signed_pp(entry['broken']['accepted_vs_disp']['lift_pp'])}")
    chosen = report.get("chosen")
    if chosen is None:
        print("  nenhuma regra passou o piso de cobertura/amostra.")
        return
    print()
    print(f"  ESCOLHIDA: {chosen['label']}")
    acc = chosen["accepted"]
    rej = chosen["rejected"]
    print(f"    discovery: aceito {pct(acc['rate'])} "
          f"(estrato {pct(acc['matched'])}, {signed_pp(acc['lift_pp'])})  "
          f"rejeitado {pct(rej['rate'])} ({signed_pp(rej['lift_pp'])})")
    print(f"    casado por deslocamento: {signed_pp(chosen['accepted_vs_disp']['lift_pp'])}")
    hold = report.get("holdout")
    if hold:
        hacc = hold["accepted"]
        print(f"    HOLDOUT:   aceito {pct(hacc['rate'])} "
              f"(estrato {pct(hacc['matched'])}, {signed_pp(hacc['lift_pp'])})  "
              f"casado {signed_pp(hold['accepted_vs_disp']['lift_pp'])}  n={hacc['n']}")
    hb = report.get("holdout_broken")
    if hb:
        print(f"    HOLDOUT broken: {signed_pp(hb['accepted']['lift_pp'])} "
              f"(casado {signed_pp(hb['accepted_vs_disp']['lift_pp'])}, "
              f"n={hb['accepted']['n']})")
    for tf, entry in report.get("by_tf_rule", {}).items():
        print(f"    {tf:>4}: {signed_pp(entry['accepted']['lift_pp'])} "
              f"(casado {signed_pp(entry['accepted_vs_disp']['lift_pp'])}, "
              f"n={entry['accepted']['n']})")
    bos = report.get("bos_rule")
    if bos:
        print(f"    BOS:  {signed_pp(bos['accepted']['lift_pp'])} "
              f"(casado {signed_pp(bos['accepted_vs_disp']['lift_pp'])}, n={bos['accepted']['n']})")


def print_cases(report: dict[str, Any]) -> None:
    for key, rows in report["cases"].items():
        chochs = [r for r in rows if r["event"] == StructureEvent.CHANGE_OF_CHARACTER.value]
        if not chochs:
            continue
        print()
        print(f"--- caso {key}: {len(chochs)} CHoCH ---")
        for row in chochs[-12:]:
            print(f"  {row['timestamp'][:16]} {row['direction']:<7} -> {row['outcome']:<9} "
                  f"(lead {row['lead']:>3})  side@ev {row['at_side']:+.0f}  "
                  f"same_side_5 {row['same_side_5']:.2f}  reclaim_5 {row['reclaim_5']:.0f}  "
                  f"disp_5 {row['disp_5']:+.2f}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--expanded", action="store_true")
    parser.add_argument("--windows", type=int, default=WINDOWS)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--cases", action="store_true")
    args = parser.parse_args(argv)

    if args.symbols:
        symbols = list(args.symbols)
    elif args.expanded:
        from research.choch_leg_opener import expanded_symbols

        symbols = expanded_symbols()
    else:
        symbols = [symbol for symbol, _tf in CASES]

    report = build(symbols, args.windows)
    print_report(report)
    if args.cases:
        print_cases(report)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, default=str))
        print(f"\njson: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
