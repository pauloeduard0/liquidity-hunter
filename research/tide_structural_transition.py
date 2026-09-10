"""Etapa 5.1 -- o Tide contem sinal de transicao estrutural?

A pergunta: *algum componente interno do Tide carrega sinal causal que o SMC
confirmado nao tem sozinho?* Nada aqui vai para producao.

--------------------------------------------------------------------------
1. O TIDE REAL, lido da implementacao e nao da UI
--------------------------------------------------------------------------

O Tide **nao existe no backend**. Ele e composto inteiramente no cliente, em
`frontend/src/utils/tideRibbon.ts` (`buildRibbon` / `buildPhase`), a partir de
tres coisas que a API ja manda. Nao ha modelo, nao ha detector, nao ha estado
discreto proprio alem dos abaixo.

**Geometria (envelope).** `mid` = `data.vwap.points[].value`; `upper`/`lower` =
`upper_1`/`lower_1`, ou seja **VWAP +/- 1 sigma ponderado por volume**
(`indicators/vwap.py`, momentos acumulados `E[p^2]-E[p]^2` sobre `hlc3`). A
ancora e periodica e depende do timeframe (`_VWAP_ANCHOR_PERIOD` em
`app/dashboard_data.py`): **SESSION** (dia UTC) ate H1, **WEEK** em H4,
**MONTH** em D1/W1. Uma banda so existe quando ha dispersao acumulada, entao a
fita **quebra em cada virada de periodo** -- e nao ha leitura nenhuma nos
primeiros candles de cada segmento.

**Matiz (a cor bullish/bearish/neutral).** `structureTrendByCandle`. E aqui
que a etapa inteira muda de forma: essa funcao **reproduz o `final_trend`**.
Ela filtra `provisional`, ordena por timestamp e aplica exatamente
`BOS -> direction`, `CHoCH -> direction`, `CHOCH_FAILED -> direction
invertida`, segurando o estado ate o proximo evento. O proprio docstring do
arquivo diz para que serve: *"so the ribbon can never disagree with the labels
drawn over it"*.

> **A cor do Tide E a estrutura confirmada.** `tide_direction` nao e uma
> segunda leitura da direcao: e a MESMA leitura, repintada. Logo
> `opposing_tide` -- estrutura bullish e Tide bearish -- **nao pode ocorrer**,
> por construcao. Nao e um resultado empirico; e uma identidade no codigo.

Isso esvazia, como enunciadas, as secoes 3, 4 (A/B/C), 10, 11 e 12 do pedido:
nao existe episodio de conflito `structure != tide_direction`, e nenhum "flip
antecipado do Tide" pode preceder um CHoCH, porque o flip do Tide **e** o
CHoCH. As secoes sao reinterpretadas abaixo sobre os canais que sobram.

**Saturacao (`conviction`).** `|control_score| / p90(|control_score|)` da
janela, com `control_score` vindo de `MarketControlAnalyzer` (CVD sobre janela
por TF, x fator de OI: 1.0 subindo, 0.55 plano, 0.35 caindo). Dois problemas,
os dois documentados no proprio frontend:

- **lookahead.** `convictionScale` toma o p90 sobre a janela visivel INTEIRA,
  futuro incluso. A saturacao de um candle depende de candles posteriores. E
  inofensivo num canal visual e seria fatal como feature: aqui a normalizacao
  e refeita **expandida** (so o passado), o que muda o numero.
- **cobertura.** `control_score` exige OI, e a Binance retem ~30 dias. O
  proprio arquivo mede: ~99% de cobertura em 15m, ~60% em 1h, **15% em 4h**.
  Num painel historico de anos, a cobertura de OI e **zero**. O canal
  OI-confirmado, e com ele `controller`/funded, **nao e mensuravel
  historicamente** -- nao por escolha, por falta de dado.

O que sobra e o fallback que o proprio frontend usa quando o OI falta:
`aggressionByCandle`, delta taker liquido (`2*taker_buy - volume`) somado numa
janela por TF e dividido pelo volume dela. Esse e reproduzivel candle a candle,
e e o unico canal do Tide com **direcao propria** (independente da estrutura).

**`controller` / funded / edges.** `MarketControlSide`, creditado so nos
quadrantes de OI subindo (`LONG_BUILDUP`/`SHORT_BUILDUP`) e acima de
`|score|>=12`. Mesma dependencia de OI: nao mensuravel no painel. Nao ha flag
`funded duration` no codigo -- e uma leitura por candle, sem memoria.

**Repaint.** A geometria e causal (acumulacao corrente). A cor herda o
`provisional` do backend: marcas provisorias sao filtradas, entao a cor nao
repinta. A saturacao repinta (o p90 da janela), e o `phase` tem um clamp em
+/-150 e um piso de largura (`MIN_SPAN_FRAC`) que descarta candle.

--------------------------------------------------------------------------
2. O QUE RESTA MEDIVEL, e como as perguntas foram reinterpretadas
--------------------------------------------------------------------------

Canais do Tide que existem, sao causais e **nao sao a estrutura**:

| canal | feature | assinada? |
|---|---|---|
| posicao no envelope | `phase` (formula exata do `buildPhase`) | sim |
| deslocamento da posicao | `phase_change_5` | sim |
| linha central | `mid_slope_atr`, `mid_accel_atr` | sim |
| largura | `width_frac`, `width_change_5` | nao (magnitude) |
| saturacao (fallback sem OI) | `aggression`, `aggression_change_5` | sim |

Como a cor E a estrutura, "Tide oposto a estrutura" so pode significar: **um
canal direcional do Tide aponta contra a estrutura confirmada**. Sao duas
oposicoes possiveis, medidas separadas e juntas:

- `phase_opp` -- o preco esta do lado errado do VWAP para a estrutura vigente;
- `agg_opp` -- a agressao taker recente esta contra a estrutura vigente.

E as tres perguntas da secao 4 viram alvos separados sobre esses estados:
(A) STALE, (B) CHoCH na direcao da oposicao, (C) BOS retomando a estrutura.

Aviso metodologico que atravessa tudo: `close_vs_vwap_atr`, `vwap_slope_atr`,
`delta_share_10`, `cvd_slope_10` ja foram medidos na Etapa 5.0 e sairam
levemente ANTI-preditivos (AUC 0,459-0,505 contra placebo 0,500). `phase` e
`aggression` sao parentes proximos desses, com normalizador diferente (sigma da
VWAP em vez de ATR) e com alvos ESTRUTURAIS em vez de persistencia de preco --
por isso valem uma medicao propria, e nao por serem features novas.

--------------------------------------------------------------------------
3. O CONTROLE QUE DECIDE
--------------------------------------------------------------------------

O erro que a Etapa 5.0 quase cometeu: comparar uma taxa contra 50%. Aqui todo
numero de um estado disparado e lido contra a **taxa do mesmo estrato**
(timeframe x tendencia confirmada x tercil de volatilidade), calculada sobre
TODOS os candles amostrados daquele estrato -- e nao contra 50%, nem contra a
media global. Um estado que nao bate o proprio estrato nao acrescenta nada,
por mais alta que seja sua taxa bruta.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from array import array
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.core.domain import (
    Candle,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    TimeFrame,
    VWAPAnchor,
)
from liquidity_hunter.indicators.volume_delta import volume_delta
from liquidity_hunter.indicators.vwap import vwap as vwap_series
from research.choch_leg_opener import DISCOVERY_FRACTION, TIMEFRAMES, WINDOWS
from research.current_market_pressure import (
    ADVANCES,
    ATR_WINDOW,
    atr_pct_series,
    auc,
    block_permutation,
    first_resume,
    leg_state_by_index,
    quantile,
    rate,
    trend_by_index,
)
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series

# --------------------------------------------------------------------------
# parametros -- todos fixados ANTES de olhar qualquer numero
# --------------------------------------------------------------------------

#: Horizontes dos alvos (secao 5).
HORIZONS = (5, 10, 20, 40, 80)

#: Persistencias testadas (secao 9): o primeiro flip e tres exigencias.
PERSISTENCE = (1, 3, 5, 10)

#: Amostragem por candle. Igual a Etapa 5.0, pelo mesmo motivo: candles
#: vizinhos sao quase a mesma observacao e inflariam o n sem informacao.
STRIDE = 4

#: Deslocamento usado em toda feature de variacao.
CHANGE_LAG = 5

#: Tolerancia de buraco dentro de um episodio de oposicao (secao 10).
EPISODE_GAP = 3

#: Sementes/parametros do placebo, herdados da Etapa 5.0 para que o controle
#: seja o MESMO controle -- permutacao em blocos preserva distribuicao e
#: autocorrelacao local e destroi so o alinhamento temporal.
PLACEBO_BLOCK = 64
PLACEBO_SEED = 20260910

#: `MainChart` desenha a fita com a mesma janela de agressao do
#: `MarketControlAnalyzer`. Espelhada aqui (secao 1).
AGGRESSION_WINDOW: dict[TimeFrame, int] = {
    TimeFrame.M1: 20,
    TimeFrame.M5: 15,
    TimeFrame.M15: 10,
    TimeFrame.M30: 7,
    TimeFrame.H1: 7,
    TimeFrame.H4: 5,
    TimeFrame.D1: 5,
    TimeFrame.W1: 3,
}

#: `_VWAP_ANCHOR_PERIOD` de `app/dashboard_data.py`, espelhado: a fita usa a
#: ancora que a producao escolheu para aquele timeframe, e nao o dia UTC em
#: todos (num D1 o dia tem UMA vela e nao ha dispersao nenhuma).
VWAP_ANCHOR: dict[TimeFrame, VWAPAnchor] = {
    TimeFrame.H4: VWAPAnchor.WEEK,
    TimeFrame.D1: VWAPAnchor.MONTH,
    TimeFrame.W1: VWAPAnchor.MONTH,
}
DEFAULT_ANCHOR = VWAPAnchor.SESSION

#: `buildPhase`: o clamp e o piso de largura, termo a termo.
PHASE_CLAMP = 150.0
MIN_SPAN_FRAC = 1e-4

#: Casos obrigatorios da secao 8.
CASES = (("BTCUSDT", TimeFrame.H1), ("BTCUSDT", TimeFrame.H4), ("ZECUSDT", TimeFrame.D1))

#: Amostra minima para reportar qualquer taxa.
MIN_N = 200


# --------------------------------------------------------------------------
# os canais do Tide, reconstruidos candle a candle
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TideGeometry:
    """`mid`/`upper`/`lower` por indice, `None` onde a fita nao existe."""

    mid: list[float | None]
    span: list[float | None]


def tide_geometry(
    candles: Sequence[Candle], symbol: str, timeframe: TimeFrame
) -> TideGeometry:
    """A geometria da fita: VWAP periodica +/- 1 sigma, pela ancora de producao.

    Chama o `indicators.vwap` de producao em vez de reimplementar. A fita nao
    existe nos primeiros candles de cada segmento (sem dispersao acumulada
    nao ha banda), e esses indices ficam `None` de proposito -- descartar o
    candle e o que o `buildRibbon` faz.
    """
    anchor = VWAP_ANCHOR.get(timeframe, DEFAULT_ANCHOR)
    series = vwap_series(candles, symbol=symbol, timeframe=timeframe, anchor=anchor)
    mid: list[float | None] = [None] * len(candles)
    span: list[float | None] = [None] * len(candles)
    if series is None:
        return TideGeometry(mid, span)
    by_ts = {candle.timestamp: index for index, candle in enumerate(candles)}
    for point in series.points:
        index = by_ts.get(point.timestamp)
        if index is None or point.upper_1 is None or point.lower_1 is None:
            continue
        mid[index] = point.value
        # `buildPhase` mede contra a banda do lado em que o preco esta: uma
        # acumulacao torta poe a media fora do centro, e as duas metades nao
        # sao iguais.
        above = candles[index].close >= point.value
        edge = point.upper_1 if above else point.lower_1
        span[index] = abs(edge - point.value)
    return TideGeometry(mid, span)


def phase_series(candles: Sequence[Candle], geometry: TideGeometry) -> list[float | None]:
    """`buildPhase` termo a termo: 0 = VWAP, +/-50 = +/-1 sigma, clamp em 150."""
    out: list[float | None] = [None] * len(candles)
    for index, candle in enumerate(candles):
        mid = geometry.mid[index]
        span = geometry.span[index]
        if mid is None or span is None:
            continue
        if span <= abs(mid) * MIN_SPAN_FRAC:
            continue
        raw = ((candle.close - mid) / span) * 50.0
        out[index] = max(-PHASE_CLAMP, min(PHASE_CLAMP, raw))
    return out


def aggression_series(candles: Sequence[Candle], window: int) -> list[float | None]:
    """`aggressionByCandle` termo a termo: delta taker liquido / volume, em %.

    E o fallback que a propria fita usa onde o OI nao alcanca -- e no painel
    historico o OI nao alcanca em lugar nenhum, entao aqui ele e a UNICA
    leitura possivel do canal de saturacao. `volume_delta` de producao da o
    numerador para nao haver duas definicoes de delta no repositorio.
    """
    out: list[float | None] = [None] * len(candles)
    if window < 1 or len(candles) < window:
        return out
    deltas = [volume_delta(candle) for candle in candles]
    delta = 0.0
    volume = 0.0
    for index, candle in enumerate(candles):
        delta += deltas[index]
        volume += candle.volume
        if index >= window:
            delta -= deltas[index - window]
            volume -= candles[index - window].volume
        if index >= window - 1 and volume > 0:
            out[index] = 100.0 * delta / volume
    return out


def _change(values: Sequence[float | None], index: int, lag: int) -> float | None:
    if index < lag:
        return None
    now = values[index]
    before = values[index - lag]
    if now is None or before is None:
        return None
    return now - before


#: nome -> assinada (positivo = bullish) ou magnitude pura.
SIGNED_FEATURES = (
    "phase",
    "phase_change_5",
    "mid_slope_atr",
    "mid_accel_atr",
    "aggression",
    "aggression_change_5",
)
MAGNITUDE_FEATURES = ("width_frac", "width_change_5")
#: Baseline price-only da secao 6C -- as duas do painel da Etapa 5.0.
PRICE_FEATURES = ("ret_atr_10", "eff_10")
FEATURES = SIGNED_FEATURES + MAGNITUDE_FEATURES + PRICE_FEATURES

#: Ablacao da secao 7: qual canal do Tide carrega o que houver.
CHANNELS: dict[str, tuple[str, ...]] = {
    "geometria (mid)": ("mid_slope_atr", "mid_accel_atr"),
    "posicao (phase)": ("phase", "phase_change_5"),
    "largura": ("width_frac", "width_change_5"),
    "saturacao (agressao)": ("aggression", "aggression_change_5"),
    "preco (controle 6C)": PRICE_FEATURES,
}


def feature_matrix(
    candles: Sequence[Candle],
    atr: Sequence[float],
    geometry: TideGeometry,
    phase: Sequence[float | None],
    aggression: Sequence[float | None],
) -> dict[str, list[float | None]]:
    """Toda feature do painel, assinada pelo mercado (positivo = bullish).

    A normalizacao pela direcao acontece UMA vez, em `aligned`, e nunca aqui --
    duplicar a logica bullish/bearish e como se produz uma assimetria que
    ninguem consegue mais achar.
    """
    length = len(candles)
    out: dict[str, list[float | None]] = {name: [None] * length for name in FEATURES}
    width: list[float | None] = [None] * length
    for index in range(length):
        mid = geometry.mid[index]
        span = geometry.span[index]
        if mid is not None and span is not None and abs(mid) > 0:
            width[index] = span / abs(mid)

    for index in range(length):
        close = candles[index].close
        unit = atr[index] * close if atr[index] > 0 else 0.0
        out["phase"][index] = phase[index]
        out["phase_change_5"][index] = _change(phase, index, CHANGE_LAG)
        out["aggression"][index] = aggression[index]
        out["aggression_change_5"][index] = _change(aggression, index, CHANGE_LAG)
        out["width_frac"][index] = width[index]
        out["width_change_5"][index] = _change(width, index, CHANGE_LAG)

        if unit > 0 and index >= 2 * CHANGE_LAG:
            mid_now = geometry.mid[index]
            mid_prev = geometry.mid[index - CHANGE_LAG]
            mid_prev2 = geometry.mid[index - 2 * CHANGE_LAG]
            if mid_now is not None and mid_prev is not None:
                slope = (mid_now - mid_prev) / unit
                out["mid_slope_atr"][index] = slope
                if mid_prev2 is not None:
                    out["mid_accel_atr"][index] = slope - (mid_prev - mid_prev2) / unit
        if unit > 0 and index >= 10:
            move = close - candles[index - 10].close
            out["ret_atr_10"][index] = move / unit
            path = sum(
                abs(candles[j].close - candles[j - 1].close)
                for j in range(index - 9, index + 1)
            )
            out["eff_10"][index] = move / path if path > 0 else 0.0
    return out


def aligned(value: float | None, trend: MarketDirection) -> float | None:
    """O unico lugar onde o sinal vira relativo a estrutura.

    Positivo = a favor da estrutura confirmada; negativo = **oposicao**, que e
    o que esta etapa mede.
    """
    if value is None:
        return None
    return value if trend is MarketDirection.BULLISH else -value


# --------------------------------------------------------------------------
# alvos -- tres perguntas separadas (secao 4), nenhuma delas otimizada
# --------------------------------------------------------------------------


def stale_onset(legs: Sequence[Any], index: int, horizon: int) -> int | None:
    """A perna vira STALE dentro de `horizon`? `None` se ja estava, ou nao ha perna.

    Ja estar parado nao e uma transicao; incluir esses candles mediria
    persistencia do proprio estado e nao antecipacao dele.
    """
    if legs[index].direction is None or legs[index].stale:
        return None
    for ahead in range(index + 1, min(index + horizon + 1, len(legs))):
        if legs[ahead].stale:
            return 1
        if legs[ahead].direction is None:
            break
    return 0


def first_choch(
    events: Sequence[MarketStructure],
    by_ts: dict[Any, int],
    index: int,
    want: MarketDirection,
    limit: int,
) -> int:
    """Candles ate o primeiro CHoCH nao-provisional na direcao `want`, ou -1.

    `CHOCH_FAILED` conta invertido, pela mesma semantica do `final_trend`: a
    falha de um CHoCH bullish e uma confirmacao bearish.
    """
    best = -1
    for event in events:
        if event.provisional or event.event not in ADVANCES:
            continue
        if event.event is StructureEvent.BREAK_OF_STRUCTURE:
            continue
        direction = event.direction
        if event.event is StructureEvent.CHOCH_FAILED:
            direction = (
                MarketDirection.BEARISH
                if direction is MarketDirection.BULLISH
                else MarketDirection.BULLISH
            )
        if direction is not want:
            continue
        at = by_ts.get(event.timestamp)
        if at is None or at <= index or at > index + limit:
            continue
        if best < 0 or at - index < best:
            best = at - index
    return best


def excursion(
    candles: Sequence[Candle], atr: Sequence[float], index: int, sign: int, horizon: int
) -> tuple[float, float] | None:
    """MFE/MAE na direcao `sign`, em ATR do candle de origem, sobre fechamentos."""
    if index + horizon >= len(candles) or atr[index] <= 0:
        return None
    close = candles[index].close
    unit = atr[index] * close
    if unit <= 0:
        return None
    moves = [
        sign * (candles[j].close - close) / unit
        for j in range(index + 1, index + horizon + 1)
    ]
    return max(moves), -min(moves)


# --------------------------------------------------------------------------
# o painel
# --------------------------------------------------------------------------

#: Colunas guardadas por candle amostrado. `array` e nao dataclass porque o
#: painel passa de 150 mil linhas.
FLOAT_COLUMNS = (
    *FEATURES,
    *(f"pl_{name}" for name in FEATURES),
    "atr",
    "mfe40",
    "mae40",
)
INT_COLUMNS = ("tf", "trend", "stale", "ts", "resume", "choch_lead", "vol", "ret_bin")


class Store:
    """Painel por timeframe, em arrays paralelos."""

    def __init__(self) -> None:
        self.f: dict[str, array] = {name: array("d") for name in FLOAT_COLUMNS}
        self.i: dict[str, array] = {name: array("q") for name in INT_COLUMNS}
        self.stale: dict[int, array] = {h: array("b") for h in HORIZONS}
        self.persist: dict[int, array] = {p: array("b") for p in PERSISTENCE}
        self.agg_persist: dict[int, array] = {p: array("b") for p in PERSISTENCE}

    def __len__(self) -> int:
        return len(self.i["ts"])


def opposition_runs(states: Sequence[bool | None]) -> list[int]:
    """Por candle, ha quantos candles o estado de oposicao esta ligado.

    Zero quando desligado. E a base das persistencias da secao 9: `>=3` le-se
    "a oposicao ja dura tres candles", nao "houve um flip tres candles atras".
    """
    out = [0] * len(states)
    run = 0
    for index, state in enumerate(states):
        run = run + 1 if state else 0
        out[index] = run
    return out


def collect_combo(
    symbol: str,
    timeframe: TimeFrame,
    series: Sequence[Candle],
    end: int,
    store: Store,
    eps: EpisodeStore,
    rng: Any,
    cases: dict[str, list[dict]],
) -> int:
    """Uma janela de producao inteira. Devolve quantos candles entraram."""
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
    legs = leg_state_by_index(candles, run.events, by_ts)
    trends = trend_by_index(run.events, by_ts, len(candles))
    geometry = tide_geometry(candles, symbol, timeframe)
    phase = phase_series(candles, geometry)
    aggression = aggression_series(candles, AGGRESSION_WINDOW.get(timeframe, 10))
    matrix = feature_matrix(candles, atr, geometry, phase, aggression)
    permutation = block_permutation(len(candles), rng)

    # Oposicao por candle: o canal direcional aponta contra a estrutura.
    phase_opp: list[bool | None] = [None] * len(candles)
    agg_opp: list[bool | None] = [None] * len(candles)
    for index in range(len(candles)):
        trend = trends[index]
        if trend is None:
            continue
        p = aligned(matrix["phase"][index], trend)
        a = aligned(matrix["aggression"][index], trend)
        phase_opp[index] = None if p is None else p < 0
        agg_opp[index] = None if a is None else a < 0
    phase_run = opposition_runs(phase_opp)
    agg_run = opposition_runs(agg_opp)

    horizon = max(HORIZONS)
    used = 0
    for index in range(0, len(candles) - horizon, STRIDE):
        trend = trends[index]
        if trend is None or matrix["phase"][index] is None:
            continue
        against = (
            MarketDirection.BEARISH if trend is MarketDirection.BULLISH else MarketDirection.BULLISH
        )
        sign = -1 if trend is MarketDirection.BULLISH else 1
        moves = excursion(candles, atr, index, sign, 40)
        if moves is None:
            continue
        placebo = permutation[index]
        for name in FEATURES:
            value = matrix[name][index]
            store.f[name].append(float("nan") if value is None else value)
            pl = matrix[name][placebo]
            store.f[f"pl_{name}"].append(float("nan") if pl is None else pl)
        store.f["atr"].append(atr[index])
        store.f["mfe40"].append(moves[0])
        store.f["mae40"].append(moves[1])
        store.i["tf"].append(TF_CODE[timeframe])
        store.i["trend"].append(1 if trend is MarketDirection.BULLISH else -1)
        store.i["stale"].append(1 if legs[index].stale else 0)
        store.i["ts"].append(int(candles[index].timestamp.timestamp()))
        store.i["resume"].append(first_resume(run.events, by_ts, index, trend, horizon))
        store.i["choch_lead"].append(first_choch(run.events, by_ts, index, against, horizon))
        store.i["vol"].append(0)  # tercil resolvido depois, com o painel inteiro
        store.i["ret_bin"].append(0)  # idem, o quintil de retorno recente
        for h in HORIZONS:
            got = stale_onset(legs, index, h)
            store.stale[h].append(-1 if got is None else got)
        for p in PERSISTENCE:
            store.persist[p].append(1 if phase_run[index] >= p else 0)
            store.agg_persist[p].append(1 if agg_run[index] >= p else 0)
        used += 1

    # ---- episodios de oposicao (secao 10), sem stride: a unidade principal --
    for channel_code, states in enumerate((phase_opp, agg_opp)):
        for start, last in scan_episodes(states, trends):
            trend = trends[start]
            if trend is None or start >= len(candles) - horizon:
                continue
            against = (
                MarketDirection.BEARISH
                if trend is MarketDirection.BULLISH
                else MarketDirection.BULLISH
            )
            sign = -1 if trend is MarketDirection.BULLISH else 1
            moves = excursion(candles, atr, start, sign, 40)
            if moves is None:
                continue
            # A intensidade e a oposicao MEDIA do episodio, no canal que o
            # definiu -- um episodio raso e um profundo nao sao o mesmo aviso.
            column = "phase" if channel_code == 0 else "aggression"
            intensity = [
                -value
                for j in range(start, last + 1)
                if (value := aligned(matrix[column][j], trend)) is not None
            ]
            width = matrix["width_frac"][start]
            eps.add(
                tf=TF_CODE[timeframe],
                trend=1 if trend is MarketDirection.BULLISH else -1,
                stale=1 if legs[start].stale else 0,
                duration=last - start + 1,
                choch_lead=first_choch(run.events, by_ts, start, against, horizon),
                resume=first_resume(run.events, by_ts, start, trend, horizon),
                ts=int(candles[start].timestamp.timestamp()),
                channel=channel_code,
                intensity=statistics.median(intensity) if intensity else float("nan"),
                width=float("nan") if width is None else width,
                mfe=moves[0],
                mae=moves[1],
            )

    key = f"{symbol}_{timeframe.value}"
    if key in cases:
        for index in range(len(candles)):
            trend = trends[index]
            cases[key].append(
                {
                    "timestamp": candles[index].timestamp.isoformat(),
                    "close": candles[index].close,
                    "atr_pct": atr[index],
                    "trend": trend.value if trend else None,
                    "leg": (
                        "stale"
                        if legs[index].stale
                        else ("active" if legs[index].direction else "none")
                    ),
                    "phase": matrix["phase"][index],
                    "aggression": matrix["aggression"][index],
                    "mid_slope": matrix["mid_slope_atr"][index],
                    "phase_opp": phase_opp[index],
                    "agg_opp": agg_opp[index],
                    "phase_run": phase_run[index],
                    "agg_run": agg_run[index],
                    "event": next(
                        (
                            e.event.value
                            + ("!" if e.event is StructureEvent.CHOCH_FAILED else "")
                            for e in run.events
                            if not e.provisional
                            and e.event in ADVANCES
                            and by_ts.get(e.timestamp) == index
                        ),
                        None,
                    ),
                    "event_direction": next(
                        (
                            e.direction.value
                            for e in run.events
                            if not e.provisional
                            and e.event in ADVANCES
                            and by_ts.get(e.timestamp) == index
                        ),
                        None,
                    ),
                }
            )
    return used


TF_CODE = {tf: code for code, tf in enumerate(TIMEFRAMES)}
TF_BY_CODE = {code: tf for tf, code in TF_CODE.items()}


# --------------------------------------------------------------------------
# episodios de oposicao -- a unidade principal da secao 10
# --------------------------------------------------------------------------

EPISODE_COLUMNS = (
    "tf",
    "trend",
    "stale",
    "duration",
    "choch_lead",
    "resume",
    "ts",
    "channel",
)


class EpisodeStore:
    """Um episodio por linha: a oposicao ligou, durou, e terminou em quê."""

    def __init__(self) -> None:
        self.i: dict[str, array] = {name: array("q") for name in EPISODE_COLUMNS}
        self.f: dict[str, array] = {
            name: array("d") for name in ("intensity", "width", "mfe", "mae")
        }

    def __len__(self) -> int:
        return len(self.i["ts"])

    def add(self, **row: Any) -> None:
        for name in EPISODE_COLUMNS:
            self.i[name].append(int(row[name]))
        for name in self.f:
            self.f[name].append(float(row[name]))


def scan_episodes(
    states: Sequence[bool | None],
    trends: Sequence[MarketDirection | None],
    gap: int = EPISODE_GAP,
) -> list[tuple[int, int]]:
    """Blocos contiguos de oposicao, tolerando `gap` candles de buraco.

    Um episodio termina quando a oposicao fica desligada por mais de `gap`
    candles OU quando a estrutura confirmada vira -- depois do flip a
    oposicao ja e outra pergunta, sobre outra perna.
    """
    out: list[tuple[int, int]] = []
    index = 0
    while index < len(states):
        if not states[index]:
            index += 1
            continue
        start = index
        last = index
        trend = trends[index]
        cursor = index
        # `gap + 1`, e nao `gap`: quando o buraco tem exatamente `gap` candles
        # desligados, o candle que RELIGA a oposicao esta a `gap + 1` do ultimo
        # aceso, e a versao ingenua sai do laco um candle antes de ve-lo --
        # partindo em dois um episodio que a tolerancia declarada mandava
        # manter inteiro. Vale reler assim: o que se compara com `gap` e o
        # tamanho do buraco (`cursor - last - 1`).
        while cursor < len(states) and cursor - last <= gap + 1 and trends[cursor] is trend:
            if states[cursor]:
                last = cursor
            cursor += 1
        out.append((start, last))
        index = last + 1
    return out


# --------------------------------------------------------------------------
# estatistica -- toda taxa lida contra o proprio estrato
# --------------------------------------------------------------------------


def vol_terciles(store: Store) -> None:
    """Resolve o tercil de volatilidade e o quintil de retorno, POR timeframe.

    Um corte global misturaria o ATR% tipico de um M15 com o de um D1 e o
    "regime de volatilidade" viraria um rotulo de timeframe.

    O quintil de `ret_atr_10` ALINHADO e o controle 6C na sua forma severa: em
    vez de comparar a AUC do Tide com a do preco lado a lado, ele permite
    perguntar se o Tide ainda separa DENTRO de candles que ja recuaram o
    mesmo tanto. Um canal que so reordena o retorno recente perde o lift ali.
    """
    for code in set(store.i["tf"]):
        rows = [i for i in range(len(store)) if store.i["tf"][i] == code]
        values = sorted(store.f["atr"][i] for i in rows if store.f["atr"][i] > 0)
        if len(values) >= 3:
            low = quantile(values, 1 / 3)
            high = quantile(values, 2 / 3)
            for i in rows:
                atr = store.f["atr"][i]
                store.i["vol"][i] = 0 if atr <= low else (1 if atr <= high else 2)

        rets = []
        for i in rows:
            raw = store.f["ret_atr_10"][i]
            if math.isnan(raw):
                continue
            trend = MarketDirection.BULLISH if store.i["trend"][i] > 0 else MarketDirection.BEARISH
            value = aligned(raw, trend)
            assert value is not None
            rets.append(value)
        if len(rets) < 5:
            continue
        rets.sort()
        cuts = [quantile(rets, q / 5) for q in range(1, 5)]
        for i in rows:
            raw = store.f["ret_atr_10"][i]
            if math.isnan(raw):
                store.i["ret_bin"][i] = -1
                continue
            trend = MarketDirection.BULLISH if store.i["trend"][i] > 0 else MarketDirection.BEARISH
            value = aligned(raw, trend)
            assert value is not None
            store.i["ret_bin"][i] = sum(1 for cut in cuts if value > cut)


def stratum(store: Store, index: int, *, by_price: bool = False) -> tuple[int, ...]:
    """(timeframe, tendencia, volatilidade) -- o controle 6B.

    Com `by_price`, o quintil de retorno recente entra no estrato: e a unica
    forma de responder "o Tide acrescenta ALEM do preco recente?" sem trocar a
    pergunta por uma comparacao de AUCs, que nao responde nada quando as duas
    leituras sao parentes.
    """
    base = (store.i["tf"][index], store.i["trend"][index], store.i["vol"][index])
    return (*base, store.i["ret_bin"][index]) if by_price else base


def matched_rate(
    store: Store,
    fired: Sequence[int],
    pool: Sequence[int],
    hit: Any,
    *,
    by_price: bool = False,
) -> dict[str, Any]:
    """Taxa disparada x taxa do MESMO estrato, com o mesmo peso por estrato.

    O controle da secao 6B, calculado sobre o pool inteiro em vez de um sorteio:
    a expectativa e a media das taxas de estrato ponderada por quantos disparos
    caíram em cada um. Um sorteio daria o mesmo numero com ruido a mais.
    """
    base_hits: dict[tuple[int, int, int], list[int]] = {}
    for index in pool:
        got = hit(index)
        if got is None:
            continue
        base_hits.setdefault(stratum(store, index, by_price=by_price), []).append(got)

    observed = 0
    total = 0
    expected = 0.0
    for index in fired:
        got = hit(index)
        if got is None:
            continue
        pool_rows = base_hits.get(stratum(store, index, by_price=by_price))
        if not pool_rows:
            continue
        observed += got
        total += 1
        expected += sum(pool_rows) / len(pool_rows)
    if total < MIN_N:
        return {"n": total, "rate": None, "matched": None, "lift_pp": None}
    return {
        "n": total,
        "rate": observed / total,
        "matched": expected / total,
        "lift_pp": 100.0 * (observed / total - expected / total),
    }


def hit_stale(store: Store, horizon: int) -> Any:
    def inner(index: int) -> int | None:
        value = store.stale[horizon][index]
        return None if value < 0 else value

    return inner


def hit_choch(store: Store, horizon: int) -> Any:
    def inner(index: int) -> int:
        lead = store.i["choch_lead"][index]
        return 1 if 0 <= lead <= horizon else 0

    return inner


def hit_resume(store: Store, horizon: int) -> Any:
    """False conflict (secao 11): a estrutura original retomou com BOS antes."""

    def inner(index: int) -> int:
        resume = store.i["resume"][index]
        lead = store.i["choch_lead"][index]
        if not 0 <= resume <= horizon:
            return 0
        return 1 if lead < 0 or resume < lead else 0

    return inner


def cut_by_tf(store: Store, code: int) -> int:
    """O corte 70/30 por timeframe, em timestamp (secao 16)."""
    stamps = sorted(store.i["ts"][i] for i in range(len(store)) if store.i["tf"][i] == code)
    if not stamps:
        return 0
    return stamps[int(len(stamps) * DISCOVERY_FRACTION)]


def rows_for(store: Store, code: int, cut: int, sample: str = "all", **filters: int) -> list[int]:
    out = []
    for index in range(len(store)):
        if store.i["tf"][index] != code:
            continue
        if sample == "discovery" and store.i["ts"][index] >= cut:
            continue
        if sample == "holdout" and store.i["ts"][index] < cut:
            continue
        if any(store.i[name][index] != value for name, value in filters.items()):
            continue
        out.append(index)
    return out


def univariate(store: Store, rows: Sequence[int], horizon: int = 20) -> dict[str, Any]:
    """AUC de cada feature contra os dois alvos, com o placebo ao lado.

    A feature entra ALINHADA a estrutura: o que se pergunta e se "mais
    oposicao" separa, e nao se "mais alta" separa -- sem isso o painel mede a
    direcao do periodo e nao a leitura.
    """
    out: dict[str, Any] = {}
    for target, hit in (("stale", hit_stale(store, horizon)), ("choch", hit_choch(store, horizon))):
        block: dict[str, Any] = {}
        for name in FEATURES:
            for column, tag in ((name, "auc"), (f"pl_{name}", "placebo")):
                values: list[float] = []
                labels: list[int] = []
                for index in rows:
                    got = hit(index)
                    if got is None:
                        continue
                    raw = store.f[column][index]
                    if math.isnan(raw):
                        continue
                    trend = (
                        MarketDirection.BULLISH
                        if store.i["trend"][index] > 0
                        else MarketDirection.BEARISH
                    )
                    value = raw if name in MAGNITUDE_FEATURES else aligned(raw, trend)
                    assert value is not None
                    values.append(-value)  # oposicao = alinhado NEGATIVO
                    labels.append(got)
                got_auc = auc(values, labels)
                if got_auc is None:
                    continue
                block.setdefault(name, {})[tag] = got_auc[0]
                block[name]["n"] = got_auc[1]
        out[target] = block
    return out


def state_rows(store: Store, rows: Sequence[int], channel: str, persistence: int) -> list[int]:
    column = store.persist if channel == "phase" else store.agg_persist
    return [index for index in rows if column[persistence][index]]


def build(symbols: Sequence[str], windows: int) -> dict[str, Any]:
    import random

    store = Store()
    eps = EpisodeStore()
    cases: dict[str, list[dict]] = {f"{s}_{tf.value}": [] for s, tf in CASES}
    rng = random.Random(PLACEBO_SEED)
    combos = 0
    rows = 0

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
                    rows += collect_combo(
                        symbol,
                        timeframe,
                        series,
                        end,
                        store,
                        eps,
                        rng,
                        cases if window == 0 else {},
                    )
                except Exception as error:  # noqa: BLE001 - feed morto
                    print(f"! janela invalida: {symbol} {timeframe.value} w{window}: {error}")
                    continue
                combos += 1

    vol_terciles(store)
    print(f"combos medidos: {combos}  candles amostrados: {rows}  episodios: {len(eps)}")

    report: dict[str, Any] = {
        "combos": combos,
        "rows": rows,
        "episodes_n": len(eps),
        "stride": STRIDE,
        "by_tf": {},
        "cases": cases,
    }

    for timeframe in TIMEFRAMES:
        code = TF_CODE[timeframe]
        allrows = rows_for(store, code, 0)
        if len(allrows) < MIN_N:
            continue
        cut = cut_by_tf(store, code)
        block: dict[str, Any] = {"n": len(allrows), "cut": cut}
        block["univariate"] = univariate(store, allrows)

        # ablacao por canal: o melhor |AUC-0,5| de cada canal (secao 7)
        ablation: dict[str, Any] = {}
        for channel, names in CHANNELS.items():
            best = None
            for target in ("stale", "choch"):
                for name in names:
                    entry = block["univariate"][target].get(name)
                    if not entry or "auc" not in entry:
                        continue
                    edge = abs(entry["auc"] - 0.5)
                    if best is None or edge > best["edge"]:
                        best = {
                            "feature": name,
                            "target": target,
                            "auc": entry["auc"],
                            "placebo": entry.get("placebo"),
                            "edge": edge,
                        }
            ablation[channel] = best
        block["ablation"] = ablation

        # estados: persistencia x canal x alvo, contra o estrato casado
        states: dict[str, Any] = {}
        for channel in ("phase", "aggression"):
            for persistence in PERSISTENCE:
                fired = state_rows(store, allrows, channel, persistence)
                entry: dict[str, Any] = {
                    "coverage": rate(len(fired), len(allrows)),
                    "n_fired": len(fired),
                }
                for horizon in (10, 20, 40, 80):
                    entry[f"stale{horizon}"] = matched_rate(
                        store, fired, allrows, hit_stale(store, horizon)
                    )
                    entry[f"choch{horizon}"] = matched_rate(
                        store, fired, allrows, hit_choch(store, horizon)
                    )
                    entry[f"false{horizon}"] = matched_rate(
                        store, fired, allrows, hit_resume(store, horizon)
                    )
                # secao 6C severa: o mesmo lift, agora tambem casado pelo
                # quintil de retorno recente. E este numero, e nao a AUC, que
                # decide se o Tide acrescenta alem do preco.
                for horizon in (20, 40):
                    entry[f"stale{horizon}_vs_price"] = matched_rate(
                        store, fired, allrows, hit_stale(store, horizon), by_price=True
                    )
                    entry[f"choch{horizon}_vs_price"] = matched_rate(
                        store, fired, allrows, hit_choch(store, horizon), by_price=True
                    )
                # secao 15: as duas direcoes separadas, cada uma no seu estrato
                for label, trend in (("bullish", 1), ("bearish", -1)):
                    pool = rows_for(store, code, 0, trend=trend)
                    entry[f"stale20_{label}"] = matched_rate(
                        store,
                        [i for i in fired if store.i["trend"][i] == trend],
                        pool,
                        hit_stale(store, 20),
                    )
                # secao 13: STALE como estrato, com taxa-base DENTRO dele
                for label, stale in (("active", 0), ("stale", 1)):
                    pool = rows_for(store, code, 0, stale=stale)
                    entry[f"choch40_{label}"] = matched_rate(
                        store,
                        [i for i in fired if store.i["stale"][i] == stale],
                        pool,
                        hit_choch(store, 40),
                    )
                # secao 16: o mesmo estado no holdout, sem reescolher nada
                for sample in ("discovery", "holdout"):
                    pool = rows_for(store, code, cut, sample=sample)
                    entry[f"stale20_{sample}"] = matched_rate(
                        store,
                        [i for i in state_rows(store, pool, channel, persistence)],
                        pool,
                        hit_stale(store, 20),
                    )
                    entry[f"choch40_{sample}"] = matched_rate(
                        store,
                        [i for i in state_rows(store, pool, channel, persistence)],
                        pool,
                        hit_choch(store, 40),
                    )
                    # O holdout do numero que sobrou DEPOIS do controle de
                    # preco. O holdout do lift bruto nao decide nada: ele
                    # replicaria o retorno recente, que ninguem duvida que
                    # replica.
                    entry[f"choch40_{sample}_vs_price"] = matched_rate(
                        store,
                        [i for i in state_rows(store, pool, channel, persistence)],
                        pool,
                        hit_choch(store, 40),
                        by_price=True,
                    )
                    entry[f"stale20_{sample}_vs_price"] = matched_rate(
                        store,
                        [i for i in state_rows(store, pool, channel, persistence)],
                        pool,
                        hit_stale(store, 20),
                        by_price=True,
                    )
                states[f"{channel}>={persistence}"] = entry
        block["states"] = states

        # episodios (secao 10/12), por canal
        episodes: dict[str, Any] = {}
        for channel_code, channel in enumerate(("phase", "aggression")):
            rows_e = [
                i
                for i in range(len(eps))
                if eps.i["tf"][i] == code and eps.i["channel"][i] == channel_code
            ]
            if len(rows_e) < MIN_N:
                continue
            leads = [eps.i["choch_lead"][i] for i in rows_e if eps.i["choch_lead"][i] >= 0]
            durations = [eps.i["duration"][i] for i in rows_e]
            resumes = sum(
                1
                for i in rows_e
                if 0 <= eps.i["resume"][i]
                and (eps.i["choch_lead"][i] < 0 or eps.i["resume"][i] < eps.i["choch_lead"][i])
            )
            episodes[channel] = {
                "n": len(rows_e),
                "duration_median": statistics.median(durations),
                "duration_p90": quantile(sorted(durations), 0.9),
                "choch_share": rate(len(leads), len(rows_e)),
                "lead_median": statistics.median(leads) if leads else None,
                "false_conflict": rate(resumes, len(rows_e)),
                "mfe_median": statistics.median(eps.f["mfe"][i] for i in rows_e),
                "mae_median": statistics.median(eps.f["mae"][i] for i in rows_e),
                "stale_share": rate(sum(eps.i["stale"][i] for i in rows_e), len(rows_e)),
            }
        block["episodes"] = episodes
        report["by_tf"][timeframe.value] = block

    return report


# --------------------------------------------------------------------------
# os casos obrigatorios (secao 8)
# --------------------------------------------------------------------------


def case_timeline(rows: Sequence[dict], channel: str, persistence: int = 3) -> dict[str, Any]:
    """Cada flip de estrutura confirmada, e o aviso que teria (ou nao) chegado.

    Para cada CHoCH/CHOCH_FAILED que virou o `final_trend`, procura o episodio
    de oposicao imediatamente anterior no canal pedido e mede o lead. Os
    episodios que NAO desembocam num flip sao contados como falsos avisos -- e
    isso, e nao o lead, e o que decide se a leitura vale.
    """
    run_key = "phase_run" if channel == "phase" else "agg_run"
    flips: list[int] = []
    previous: str | None = None
    for index, row in enumerate(rows):
        trend = row["trend"]
        if trend and previous and trend != previous:
            flips.append(index)
        if trend:
            previous = trend

    # Inicios de episodio: primeiro candle em que a corrida atinge a exigencia.
    starts = [
        index
        for index, row in enumerate(rows)
        if row[run_key] == persistence and (index == 0 or rows[index - 1][run_key] < persistence)
    ]

    warned: list[dict[str, Any]] = []
    used: set[int] = set()
    for order, flip in enumerate(flips):
        candidates = [s for s in starts if s < flip and rows[s]["trend"] == rows[flip - 1]["trend"]]
        if not candidates:
            warned.append({"flip": rows[flip]["timestamp"], "lead_bars": None})
            continue
        start = candidates[-1]
        used.add(start)
        close_start = rows[start]["close"]
        close_flip = rows[flip]["close"]
        atr = rows[start]["atr_pct"] or 0.0
        unit = atr * close_start
        # Quanto da perna INTEIRA ja tinha acontecido quando o aviso saiu. A
        # perna vai do flip anterior ate o extremo que ela alcancou; medir do
        # aviso ate o flip em vez disso satura em 100% e nao responde nada.
        origin = flips[order - 1] if order > 0 else 0
        leg = [r["close"] for r in rows[origin : flip + 1]]
        base = rows[origin]["close"]
        extreme = max(leg) if rows[flip]["trend"] == "bearish" else min(leg)
        warned.append(
            {
                "flip": rows[flip]["timestamp"],
                "warning": rows[start]["timestamp"],
                "lead_bars": flip - start,
                "lead_atr": abs(close_flip - close_start) / unit if unit > 0 else None,
                "move_pct": 100.0 * (close_flip - close_start) / close_start,
                "move_done_pct": (
                    100.0 * abs(close_start - base) / abs(extreme - base)
                    if extreme != base
                    else None
                ),
            }
        )
    leads = [w["lead_bars"] for w in warned if w["lead_bars"] is not None]
    return {
        "flips": len(flips),
        "warned": len(leads),
        "lead_median": statistics.median(leads) if leads else None,
        "episodes": len(starts),
        "false_warnings": len(starts) - len(used),
        "false_share": rate(len(starts) - len(used), len(starts)),
        "detail": warned[-6:],
    }


def case_now(rows: Sequence[dict], channel: str) -> dict[str, Any]:
    """O estado no ultimo candle do cache, e ha quanto tempo ele dura."""
    if not rows:
        return {"found": False}
    run_key = "phase_run" if channel == "phase" else "agg_run"
    last = rows[-1]
    return {
        "found": True,
        "timestamp": last["timestamp"],
        "close": last["close"],
        "trend": last["trend"],
        "leg": last["leg"],
        "phase": last["phase"],
        "aggression": last["aggression"],
        "opposing": bool(last[run_key]),
        "run": last[run_key],
    }


# --------------------------------------------------------------------------
# relatorio
# --------------------------------------------------------------------------


def pct(value: float | None) -> str:
    return "  --  " if value is None else f"{100 * value:5.1f}%"


def num(value: float | None, digits: int = 3) -> str:
    return "  --  " if value is None else f"{value:.{digits}f}"


def signed_pp(value: float | None) -> str:
    return "  --  " if value is None else f"{value:+5.1f}pp"


def print_report(report: dict[str, Any]) -> None:
    print("\n" + "=" * 78)
    print("ETAPA 5.1 -- TIDE COMO SINAL DE TRANSICAO ESTRUTURAL")
    print("=" * 78)
    print(
        f"  combos: {report['combos']}   candles: {report['rows']} (stride {report['stride']})"
        f"   episodios: {report['episodes_n']}"
    )
    print(
        "  A cor do Tide E o final_trend (structureTrendByCandle). Oposicao aqui = um\n"
        "  canal NAO-estrutural do Tide (posicao no envelope, agressao) contra a estrutura."
    )

    print("\n== 7. ABLACAO: qual canal separa (melhor |AUC-0,5| do canal) ==")
    for timeframe, block in report["by_tf"].items():
        print(f"  {timeframe}  (n={block['n']})")
        for channel, best in block["ablation"].items():
            if not best:
                continue
            print(
                f"    {channel:<22} {best['feature']:<20} alvo={best['target']:<6} "
                f"AUC={num(best['auc'])}  placebo={num(best['placebo'])}"
            )

    print("\n== 4/9. ESTADOS DE OPOSICAO contra o ESTRATO CASADO (tf x trend x vol) ==")
    print("  lift = taxa disparada - taxa do mesmo estrato. Positivo = acrescenta.")
    for timeframe, block in report["by_tf"].items():
        print(f"\n  {timeframe}")
        print(
            "    estado          cobert.   STALE@20            CHoCH@40           "
            "falso conflito@40"
        )
        print("    " + "-" * 76)
        for label, entry in block["states"].items():
            stale = entry["stale20"]
            choch = entry["choch40"]
            false = entry["false40"]
            print(
                f"    {label:<15} {pct(entry['coverage'])}  "
                f"{pct(stale['rate'])} vs {pct(stale['matched'])} {signed_pp(stale['lift_pp'])}  "
                f"{pct(choch['rate'])} vs {pct(choch['matched'])} {signed_pp(choch['lift_pp'])}  "
                f"{pct(false['rate'])}"
            )

    print("\n== 6C. O TIDE ALEM DO PRECO RECENTE (mesmo lift, casado tambem por ret_atr_10) ==")
    print("  Se o lift some aqui, o canal so estava reordenando o retorno recente.")
    print("    tf     estado           STALE@20 bruto -> casado    CHoCH@40 bruto -> casado")
    print("    " + "-" * 76)
    for timeframe, block in report["by_tf"].items():
        for label, entry in block["states"].items():
            raw_s, price_s = entry["stale20"], entry["stale20_vs_price"]
            raw_c, price_c = entry["choch40"], entry["choch40_vs_price"]
            print(
                f"    {timeframe:<6} {label:<15}  {signed_pp(raw_s['lift_pp'])} -> "
                f"{signed_pp(price_s['lift_pp'])}         {signed_pp(raw_c['lift_pp'])} -> "
                f"{signed_pp(price_c['lift_pp'])}"
            )

    print("\n== 13. STALE COMO ESTRATO (CHoCH@40, taxa-base DENTRO do estrato) ==")
    print("    tf     estado           active                    stale")
    print("    " + "-" * 68)
    for timeframe, block in report["by_tf"].items():
        for label, entry in block["states"].items():
            active = entry["choch40_active"]
            stale = entry["choch40_stale"]
            if active["rate"] is None and stale["rate"] is None:
                continue
            print(
                f"    {timeframe:<6} {label:<15}  {pct(active['rate'])} vs "
                f"{pct(active['matched'])} {signed_pp(active['lift_pp'])}   "
                f"{pct(stale['rate'])} vs {pct(stale['matched'])} {signed_pp(stale['lift_pp'])}"
            )

    print("\n== 15. SIMETRIA (STALE@20, cada direcao no seu proprio estrato) ==")
    print("    tf     estado           bullish                   bearish")
    print("    " + "-" * 68)
    for timeframe, block in report["by_tf"].items():
        for label, entry in block["states"].items():
            bull = entry["stale20_bullish"]
            bear = entry["stale20_bearish"]
            if bull["rate"] is None and bear["rate"] is None:
                continue
            print(
                f"    {timeframe:<6} {label:<15}  {pct(bull['rate'])} vs "
                f"{pct(bull['matched'])} {signed_pp(bull['lift_pp'])}   "
                f"{pct(bear['rate'])} vs {pct(bear['matched'])} {signed_pp(bear['lift_pp'])}"
            )

    print("\n== 16. HOLDOUT (lift JA casado por preco, 70% antigo -> 30% recente) ==")
    print("    tf     estado           STALE@20 disc -> hold      CHoCH@40 disc -> hold")
    print("    " + "-" * 74)
    for timeframe, block in report["by_tf"].items():
        for label, entry in block["states"].items():
            sd = entry["stale20_discovery_vs_price"]
            sh = entry["stale20_holdout_vs_price"]
            cd = entry["choch40_discovery_vs_price"]
            ch = entry["choch40_holdout_vs_price"]
            if sd["lift_pp"] is None and sh["lift_pp"] is None:
                continue
            print(
                f"    {timeframe:<6} {label:<15}  {signed_pp(sd['lift_pp'])} -> "
                f"{signed_pp(sh['lift_pp'])}       {signed_pp(cd['lift_pp'])} -> "
                f"{signed_pp(ch['lift_pp'])}"
            )

    print("\n== 10/11/12. EPISODIOS DE OPOSICAO (unidade principal) ==")
    print("    tf     canal          n      dur    CHoCH<=80   lead   falso conflito  MFE/MAE")
    print("    " + "-" * 80)
    for timeframe, block in report["by_tf"].items():
        for channel, entry in block["episodes"].items():
            print(
                f"    {timeframe:<6} {channel:<13} {entry['n']:>6}  {entry['duration_median']:>5}  "
                f"{pct(entry['choch_share'])}  {num(entry['lead_median'], 0):>6}  "
                f"{pct(entry['false_conflict'])}         "
                f"{num(entry['mfe_median'], 2)}/{num(entry['mae_median'], 2)}"
            )

    print("\n== 8. CASOS OBRIGATORIOS ==")
    for name, entry in report.get("case_report", {}).items():
        print(f"\n  {name}")
        now = entry.get("now", {})
        if now.get("found"):
            print(
                f"    agora ({now['timestamp'][:16]}): estrutura {now['trend']} / perna "
                f"{now['leg']} / phase {num(now['phase'], 1)} / agressao "
                f"{num(now['aggression'], 1)} / oposicao {'SIM' if now['opposing'] else 'nao'}"
                f" ha {now['run']} velas"
            )
        for channel in ("phase", "aggression"):
            line = entry.get(channel)
            if not line:
                continue
            print(
                f"    {channel:<11} flips={line['flips']} avisados={line['warned']} "
                f"lead mediano={num(line['lead_median'], 0)} velas | episodios="
                f"{line['episodes']} falsos={line['false_warnings']} "
                f"({pct(line['false_share'])})"
            )
            for detail in line["detail"]:
                if detail.get("lead_bars") is None:
                    print(f"      flip {detail['flip'][:10]}: SEM aviso previo")
                    continue
                print(
                    f"      flip {detail['flip'][:10]} <- aviso {detail['warning'][:10]}: "
                    f"lead {detail['lead_bars']} velas / {num(detail['lead_atr'], 1)} ATR | "
                    f"movimento {detail['move_pct']:+.1f}% | ja feito no aviso "
                    f"{num(detail['move_done_pct'], 0)}%"
                )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Etapa 5.1 -- Tide e transicao estrutural")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--windows", type=int, default=WINDOWS)
    parser.add_argument(
        "--json",
        type=Path,
        default=Path(__file__).with_name("tide_structural_transition_baseline.json"),
    )
    args = parser.parse_args(argv)

    symbols = args.symbols
    if not symbols:
        from research.choch_leg_opener import expanded_symbols

        symbols = expanded_symbols()

    report = build(symbols, args.windows)
    report["case_report"] = {}
    for symbol, timeframe in CASES:
        rows = report["cases"].get(f"{symbol}_{timeframe.value}", [])
        if not rows:
            continue
        report["case_report"][f"{symbol} {timeframe.value}"] = {
            "now": case_now(rows, "phase"),
            "phase": case_timeline(rows, "phase"),
            "aggression": case_timeline(rows, "aggression"),
        }
    print_report(report)
    args.json.write_text(json.dumps(report, indent=1, default=str))
    print(f"\n-> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
