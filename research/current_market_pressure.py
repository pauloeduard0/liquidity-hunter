"""Etapa 5.0: existe pressao de mercado ATUAL, mensuravel e separavel da estrutura confirmada?

O commit `f7abe0b` fechou a linha 4.1-4.6 com uma conclusao negativa e uma
positiva. A negativa: nao da para antecipar o CHoCH confirmado sem quebrar o
que o torna confiavel. A positiva: o atraso e *deliberado*, entao a leitura que
falta nao e um CHoCH mais cedo -- e uma **segunda dimensao**, medida no mesmo
candle e independente da primeira:

    confirmed structure  = bearish   (o que a maquina ja provou)
    leg activity         = stale     (a perna vigente parou)
    current pressure     = bullish   (o que o mercado esta fazendo agora)

Isso nao e contradicao. Sao tres perguntas diferentes, e so as duas primeiras
existem no projeto hoje. Este modulo mede se a terceira tem substrato causal.

O QUE ESTE MODULO NAO E
-----------------------

Nao e um preditor de reversao. Uma pressao bullish contra uma estrutura bearish
pode estar CERTA sobre os proximos 20 candles e a estrutura bearish continuar
depois -- as duas coisas cabem no mesmo grafico. Por isso a avaliacao e
separada em duas familias que nunca se misturam:

- **PRESSURE PERSISTENCE** (secao 8): o preco realmente andou na direcao
  apontada, antes de devolver? Alvo primario, independente de CHoCH.
- **STRUCTURAL OUTCOME** (secao 9): depois veio CHoCH na direcao da pressao, a
  estrutura antiga retomou com BOS, houve CHOCH_FAILED, ou nada? Alvo
  secundario, medido mas nunca otimizado.

Uma regra escolhida por acertar CHoCH seria um CHoCH precoce disfarcado, que e
exatamente o que a etapa anterior rejeitou seis vezes.

O CONTROLE QUE ESTA MEDICAO PRECISA
-----------------------------------

`CLAUDE.md` exige controle casado em simbolo, timeframe **e direcao**, e aqui
isso e mais que uma formalidade: qualquer feature de momento parece preditiva
num periodo que subiu. Tres defesas, todas obrigatorias:

1. **Toda feature e assinada** (positivo = bullish), e o alvo tambem. Uma
   feature que so funciona porque o painel subiu produz AUC alto no agregado e
   ~0.50 dentro dos estratos.
2. **AUC por estrato de tendencia confirmada** (secao 13): bullish e bearish
   separados, sempre reportados lado a lado. Assimetria nao e agregada.
3. **Placebo de lead casado**: a mesma feature lida num candle embaralhado por
   blocos dentro do mesmo simbolo/TF. Preserva distribuicao e autocorrelacao
   local, destroi o alinhamento temporal. Se o placebo tambem separa, a
   separacao e do periodo, nao do sinal.

CAMADAS (secao 4)
-----------------

A medicao e feita em tres passos cumulativos, e o relatorio mostra o ganho de
cada um: `A` price-only (OHLC + ATR + estrutura ja existente), `B` +EMA9/VWAP,
`C` +fluxo (volume, delta, CVD). Se `A` resolve, `B` e `C` nao se justificam.

CAUSALIDADE
-----------

Toda feature no candle T le apenas candles ate T (inclusive). Todo alvo le
apenas candles depois de T. O estado da perna (`active`/`stale`) e reproduzido
por uma varredura para frente equivalente a `detect_structural_stall`, e
`test_current_market_pressure.py` compara os dois em prefixos amostrados --
o modo de falha silencioso aqui seria uma perna marcada stale por um extremo
que ainda nao tinha acontecido.

NADA AQUI TOCA PRODUCAO. Somente `research/`.

Uso:

    poetry run python -m research.current_market_pressure --windows 3
    poetry run python -m research.current_market_pressure --symbols BTCUSDT ZECUSDT
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from array import array
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
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
from liquidity_hunter.indicators.ema import ema
from liquidity_hunter.indicators.volume_delta import volume_delta
from liquidity_hunter.liquidity.structural_stall import (
    DEFAULT_STALL_BARS,
    DEFAULT_STALL_RETRACEMENT_ATR,
    frozen_atr_pct,
)
from research.choch_leg_opener import DISCOVERY_FRACTION, TIMEFRAMES, WINDOWS
from research.choch_reference_audit import quantile
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series

#: Janela do ATR causal (media de true range em fracao de preco). Mesma forma
#: do `_mean_tr_pct` do 4.5, mas rolante: congelar o ATR faz sentido para uma
#: perna, nao para uma leitura por candle.
ATR_WINDOW = 50

#: Horizontes de PERSISTENCE (secao 8) e de desfecho estrutural (secao 9).
PERSIST_HORIZONS = (5, 10, 20, 40)
STRUCTURE_HORIZONS = (10, 20, 40, 80)

#: Passo de amostragem do painel univariado. Candles vizinhos sao quase o mesmo
#: candle (secao 6): sem isto o n cresce sem informacao e todo intervalo de
#: confianca vira ficcao. As metricas finais sao por EPISODIO, nao por candle.
STRIDE = 4

#: Lookbacks das features de preco.
RETURN_LOOKBACKS = (3, 5, 10, 20)

#: Bloco do placebo (candles). Grande o bastante para preservar a
#: autocorrelacao local, pequeno o bastante para destruir o alinhamento.
PLACEBO_BLOCK = 64
PLACEBO_SEED = 20260909

#: Um episodio de pressao tolera este tanto de candles "balanced" antes de
#: fechar -- sem isso um unico candle indeciso picota o episodio em dois.
EPISODE_GAP = 3

#: Os casos obrigatorios da secao 10.
ZEC_BULLISH = ("ZECUSDT", TimeFrame.D1, "2026-04-06T00:00:00+00:00")
BTC_CASE = ("BTCUSDT", TimeFrame.H1)

ADVANCES = (
    StructureEvent.BREAK_OF_STRUCTURE,
    StructureEvent.CHANGE_OF_CHARACTER,
    StructureEvent.CHOCH_FAILED,
)


# --------------------------------------------------------------------------
# series causais auxiliares
# --------------------------------------------------------------------------


def atr_pct_series(candles: Sequence[Candle], window: int = ATR_WINDOW) -> list[float]:
    """True range medio como fracao do preco, rolante e causal.

    O valor no indice i le apenas ate i. Antes de haver `window` candles usa o
    que existe; abaixo de dois candles nao existe true range e o valor fica 0,
    o que desqualifica o candle (toda feature em ATR checa isto).
    """
    out = [0.0] * len(candles)
    trs: list[float] = []
    total = 0.0
    for i in range(1, len(candles)):
        previous, current = candles[i - 1], candles[i]
        tr = (
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
            / current.close
        )
        trs.append(tr)
        total += tr
        if len(trs) > window:
            total -= trs.pop(0)
        out[i] = total / len(trs)
    return out


def rolling_vwap(candles: Sequence[Candle], window: int) -> list[float | None]:
    """VWAP rolante de `window` candles.

    A VWAP de producao (`indicators.vwap`) e ancorada por sessao/periodo, e o
    ponto dela e ser um preco de referencia compartilhado. Aqui a pergunta e
    outra -- "o preco esta acima ou abaixo do custo medio recente" -- e uma
    ancora de sessao introduz um degrau artificial na virada do dia que nada
    tem a ver com pressao. A janela rolante e a leitura honesta para esta
    pergunta; a de producao permanece intocada.
    """
    out: list[float | None] = [None] * len(candles)
    pv = 0.0
    vol = 0.0
    for i, candle in enumerate(candles):
        typical = (candle.high + candle.low + candle.close) / 3.0
        pv += typical * candle.volume
        vol += candle.volume
        if i >= window:
            old = candles[i - window]
            pv -= ((old.high + old.low + old.close) / 3.0) * old.volume
            vol -= old.volume
        out[i] = pv / vol if vol > 0 else None
    return out


def trend_by_index(
    events: Sequence[MarketStructure],
    by_ts: dict[datetime, int],
    length: int,
) -> list[MarketDirection | None]:
    """Tendencia confirmada vigente por candle (semantica do `_advance_boundaries`).

    Um `CHOCH_FAILED` inverte: a falha de um CHoCH bullish confirma bearish.
    """
    # `key=` explicito: dois advances podem cair no MESMO candle, e sem a chave
    # o desempate cai no `MarketStructure`, que nao tem ordem. Isso nao falha
    # alto num lugar so -- derruba a janela inteira, e uma janela perdida some
    # do painel sem aparecer em nenhuma metrica.
    advances = sorted(
        (
            (by_ts[event.timestamp], event)
            for event in events
            if not event.provisional and event.event in ADVANCES and event.timestamp in by_ts
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


@dataclass(frozen=True)
class LegState:
    """O estado da perna vigente num candle, sem olhar para frente."""

    direction: MarketDirection | None
    stale: bool
    bars_since_advance: int | None
    retracement_atr: float | None
    giveback_atr: float | None


def leg_state_by_index(
    candles: Sequence[Candle],
    events: Sequence[MarketStructure],
    by_ts: dict[datetime, int],
    *,
    n: int = DEFAULT_STALL_BARS,
    k_atr: float = DEFAULT_STALL_RETRACEMENT_ATR,
) -> list[LegState]:
    """`active`/`stale` por candle, em UMA varredura para frente.

    Reproduz `liquidity.structural_stall.detect_structural_stall` termo a
    termo, e as tres escolhas dele importam demais para serem aproximadas:

    - **so um BOS abre perna.** Uma perna encerrada por CHoCH ou CHOCH_FAILED
      ja foi fechada pela maquina; nao ha o que chamar de parado.
    - **o ATR e congelado no advance**, pela media EXPANDIDA de true range da
      producao (`frozen_atr_pct`), nao pela janela rolante que as features
      usam. As duas leituras coexistem de proposito: a perna tem uma unidade
      so, do momento em que nasceu; a leitura por candle tem a unidade do
      agora.
    - **o denominador e `price_level` do proprio advance**, nao o fechamento
      corrente -- a devolucao e lida em ATR da perna que a produziu.

    Trocar qualquer um desses por uma aproximacao razoavel move o estado em
    candles reais; `test_o_estado_da_perna_bate_com_o_detector_de_producao`
    compara os dois em prefixos de serie real justamente por isso.

    Chamar `detect_structural_stall` em cada prefixo daria o mesmo resultado e
    custa O(n^2) sobre centenas de milhares de candles.
    """
    # `key=` explicito: dois advances podem cair no MESMO candle, e sem a chave
    # o desempate cai no `MarketStructure`, que nao tem ordem. Isso nao falha
    # alto num lugar so -- derruba a janela inteira, e uma janela perdida some
    # do painel sem aparecer em nenhuma metrica.
    advances = sorted(
        (
            (by_ts[event.timestamp], event)
            for event in events
            if not event.provisional and event.event in ADVANCES and event.timestamp in by_ts
        ),
        key=lambda pair: pair[0],
    )
    out: list[LegState] = [LegState(None, False, None, None, None)] * len(candles)
    position = 0
    direction: MarketDirection | None = None
    opener: int | None = None
    frozen = 0.0
    entry = 0.0
    extreme = 0.0
    stale = False
    for index in range(len(candles)):
        while position < len(advances) and advances[position][0] <= index:
            at, event = advances[position]
            position += 1
            stale = False
            if event.event is StructureEvent.BREAK_OF_STRUCTURE and event.price_level > 0:
                direction = event.direction
                opener = at
                frozen = frozen_atr_pct(candles, at)
                entry = event.price_level
                extreme = candles[at].close
            else:
                direction = None
                opener = None
        if direction is None or opener is None or frozen <= 0 or entry <= 0:
            out[index] = LegState(None, False, None, None, None)
            continue
        close = candles[index].close
        if direction is MarketDirection.BULLISH:
            extreme = max(extreme, close)
            give_back = extreme - close
        else:
            extreme = min(extreme, close)
            give_back = close - extreme
        giveback = give_back / entry / frozen
        bars = index - opener
        if not stale and bars >= n and giveback >= k_atr:
            stale = True
        out[index] = LegState(direction, stale, bars, giveback, giveback)
    return out


# --------------------------------------------------------------------------
# features -- todas assinadas: positivo = bullish
# --------------------------------------------------------------------------

#: nome -> familia (secao 4). `A` price-only, `B` medias, `C` fluxo.
FEATURE_FAMILY: dict[str, str] = {}


def _register(name: str, family: str) -> str:
    FEATURE_FAMILY[name] = family
    return name


RET_NAMES = [_register(f"ret_atr_{h}", "A") for h in RETURN_LOOKBACKS]
EFF_10 = _register("eff_10", "A")
EFF_20 = _register("eff_20", "A")
CLOSES_DIR_10 = _register("closes_dir_10", "A")
RANGE_POS_20 = _register("range_pos_20", "A")
SLOPE_ATR_10 = _register("slope_atr_10", "A")
LEG_GIVEBACK = _register("leg_giveback_signed", "A")
EMA9_DIST = _register("close_vs_ema9_atr", "B")
EMA9_SLOPE = _register("ema9_slope_atr", "B")
VWAP_DIST = _register("close_vs_vwap_atr", "B")
VWAP_SLOPE = _register("vwap_slope_atr", "B")
EMA_VS_VWAP = _register("ema9_vs_vwap_atr", "B")
DELTA_SHARE = _register("delta_share_10", "C")
CVD_SLOPE = _register("cvd_slope_10", "C")
VOL_THRUST = _register("vol_thrust_5", "C")

FEATURES: tuple[str, ...] = tuple(FEATURE_FAMILY)
FAMILY_ORDER = ("A", "B", "C")

VWAP_WINDOW = 20
EMA_PERIOD = 9


def _slope(values: Sequence[float]) -> float:
    """Coeficiente angular de minimos quadrados, por candle."""
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    num = 0.0
    den = 0.0
    for i, value in enumerate(values):
        dx = i - mean_x
        num += dx * (value - mean_y)
        den += dx * dx
    return num / den if den else 0.0


def feature_matrix(
    candles: Sequence[Candle],
    atr: Sequence[float],
    legs: Sequence[LegState],
) -> dict[str, list[float | None]]:
    """Toda feature no candle i le apenas candles <= i.

    O sinal e sempre de mercado (positivo = bullish), nunca relativo a
    estrutura. A normalizacao pela direcao da estrutura (secao 5) acontece uma
    unica vez, em `signed_to_aligned`, e nao em quatorze lugares diferentes:
    duplicar o par bullish/bearish aqui seria o convite a um erro de sinal que
    so aparece em metade da amostra.

    A excecao deliberada e `leg_giveback_signed`, que ja nasce relativo a perna
    (devolucao de perna nao tem sentido "de mercado"): ele e convertido para o
    espaco assinado no proprio calculo.
    """
    n = len(candles)
    closes = [c.close for c in candles]
    out: dict[str, list[float | None]] = {name: [None] * n for name in FEATURES}

    # caminhos e contagens acumulados, para os lookbacks nao virarem O(n*h)
    path = [0.0] * (n + 1)
    ups = [0] * (n + 1)
    vols = [0.0] * (n + 1)
    deltas = [0.0] * (n + 1)
    for i in range(n):
        step = abs(closes[i] - closes[i - 1]) if i else 0.0
        path[i + 1] = path[i] + step
        ups[i + 1] = ups[i] + (1 if i and closes[i] > closes[i - 1] else 0)
        vols[i + 1] = vols[i] + candles[i].volume
        deltas[i + 1] = deltas[i] + volume_delta(candles[i])

    ema9 = ema(closes, EMA_PERIOD)
    vwap = rolling_vwap(candles, VWAP_WINDOW)

    for i in range(n):
        unit = atr[i] * closes[i]
        if unit <= 0:
            continue
        close = closes[i]

        for lookback, name in zip(RETURN_LOOKBACKS, RET_NAMES, strict=True):
            if i >= lookback:
                out[name][i] = (close - closes[i - lookback]) / unit

        for lookback, name in ((10, EFF_10), (20, EFF_20)):
            if i >= lookback:
                travelled = path[i + 1] - path[i + 1 - lookback]
                if travelled > 0:
                    out[name][i] = (close - closes[i - lookback]) / travelled

        if i >= 10:
            up = ups[i + 1] - ups[i + 1 - 10]
            out[CLOSES_DIR_10][i] = (2 * up - 10) / 10.0
            out[SLOPE_ATR_10][i] = _slope(closes[i - 9 : i + 1]) / unit

        if i >= 20:
            window = candles[i - 19 : i + 1]
            high = max(c.high for c in window)
            low = min(c.low for c in window)
            if high > low:
                out[RANGE_POS_20][i] = 2.0 * (close - low) / (high - low) - 1.0

        leg = legs[i]
        if leg.direction is not None and leg.giveback_atr is not None:
            # devolucao e sempre contra a perna: numa perna bullish ela empurra
            # a leitura para bearish, e vice-versa.
            sign = -1.0 if leg.direction is MarketDirection.BULLISH else 1.0
            out[LEG_GIVEBACK][i] = sign * leg.giveback_atr

        mean = ema9[i]
        if mean is not None:
            out[EMA9_DIST][i] = (close - mean) / unit
            previous = ema9[i - 1] if i else None
            if previous is not None:
                out[EMA9_SLOPE][i] = (mean - previous) / unit

        reference = vwap[i]
        if reference is not None:
            out[VWAP_DIST][i] = (close - reference) / unit
            earlier = vwap[i - 5] if i >= 5 else None
            if earlier is not None:
                out[VWAP_SLOPE][i] = (reference - earlier) / (5.0 * unit)
            if mean is not None:
                out[EMA_VS_VWAP][i] = (mean - reference) / unit

        if i >= 10:
            volume = vols[i + 1] - vols[i + 1 - 10]
            if volume > 0:
                out[DELTA_SHARE][i] = (deltas[i + 1] - deltas[i + 1 - 10]) / volume
                cvd = [deltas[j + 1] for j in range(i - 9, i + 1)]
                out[CVD_SLOPE][i] = _slope(cvd) / (volume / 10.0)

        if i >= 20:
            recent = vols[i + 1] - vols[i + 1 - 5]
            base = (vols[i + 1] - vols[i + 1 - 20]) / 4.0
            if base > 0 and i >= 5:
                move = close - closes[i - 5]
                direction = 1.0 if move > 0 else (-1.0 if move < 0 else 0.0)
                out[VOL_THRUST][i] = direction * (recent / base - 1.0)

    return out


def signed_to_aligned(value: float, trend: MarketDirection) -> float:
    """A normalizacao pela direcao da secao 5, num lugar so.

    Positivo = a favor da estrutura confirmada. `-value` para uma tendencia
    bearish e toda a simetria bullish/bearish do modulo.
    """
    return value if trend is MarketDirection.BULLISH else -value


# --------------------------------------------------------------------------
# alvos -- avaliacao apenas, NUNCA feature (secoes 8 e 9)
# --------------------------------------------------------------------------


def future_targets(
    candles: Sequence[Candle],
    atr: Sequence[float],
    index: int,
) -> dict[int, tuple[float, float, float, float]] | None:
    """`(net, mfe, mae, eff)` em ATR, assinados em termos de mercado.

    `mfe` e a maior alta acima do fechamento de T, `mae` a maior baixa abaixo
    dele -- as duas positivas. Quem inverte para a direcao da pressao e
    `pressure_targets`, uma vez so.
    """
    unit = atr[index] * candles[index].close
    if unit <= 0:
        return None
    close = candles[index].close
    out: dict[int, tuple[float, float, float, float]] = {}
    high = -math.inf
    low = math.inf
    travelled = 0.0
    horizon = max(PERSIST_HORIZONS)
    for step in range(1, horizon + 1):
        position = index + step
        if position >= len(candles):
            return out or None
        high = max(high, candles[position].high)
        low = min(low, candles[position].low)
        travelled += abs(candles[position].close - candles[position - 1].close)
        if step in PERSIST_HORIZONS:
            net = candles[position].close - close
            out[step] = (
                net / unit,
                (high - close) / unit,
                (close - low) / unit,
                net / travelled if travelled > 0 else 0.0,
            )
    return out


def pressure_targets(
    raw: tuple[float, float, float, float], direction: int
) -> tuple[float, float, float, float]:
    """Espelha os alvos para a direcao apontada pela pressao."""
    net, mfe, mae, eff = raw
    if direction >= 0:
        return net, mfe, mae, eff
    return -net, mae, mfe, -eff


def structural_outcome(
    events: Sequence[MarketStructure],
    by_ts: dict[datetime, int],
    index: int,
    horizon: int,
    pressure: int,
    confirmed: MarketDirection,
) -> str:
    """O PRIMEIRO advance nao-provisional na janela, classificado (secao 9).

    Quatro desfechos e nada mais: `choch_pressure` (a estrutura virou para o
    lado que a pressao apontava), `bos_resume` (a estrutura antiga retomou),
    `choch_failed`, `none`. Este alvo e reportado e nunca otimizado -- uma
    regra escolhida por ele seria o CHoCH precoce que a Etapa 4 rejeitou.
    """
    want = MarketDirection.BULLISH if pressure > 0 else MarketDirection.BEARISH
    best: tuple[int, MarketStructure] | None = None
    for event in events:
        if event.provisional or event.event not in ADVANCES:
            continue
        at = by_ts.get(event.timestamp)
        if at is None or at <= index or at > index + horizon:
            continue
        if best is None or at < best[0]:
            best = (at, event)
    if best is None:
        return "none"
    event = best[1]
    if event.event is StructureEvent.CHOCH_FAILED:
        return "choch_failed"
    if event.event is StructureEvent.CHANGE_OF_CHARACTER:
        return "choch_pressure" if event.direction is want else "choch_against"
    return "bos_resume" if event.direction is confirmed else "bos_pressure"


# --------------------------------------------------------------------------
# as regras candidatas -- registradas ANTES de olhar qualquer numero (secao 16)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    """No maximo tres condicoes, todas explicaveis, todas sobre features assinadas."""

    label: str
    family: str
    threshold: float
    efficiency: float | None = None
    agree: str | None = None

    def call(self, values: dict[str, float | None]) -> int:
        """+1 pressao bullish, -1 bearish, 0 balanced (abstencao, secao 18)."""
        drive = values.get("ret_atr_10")
        if drive is None or abs(drive) < self.threshold:
            return 0
        sign = 1 if drive > 0 else -1
        if self.efficiency is not None:
            eff = values.get(EFF_10)
            if eff is None or abs(eff) < self.efficiency or (eff > 0) != (sign > 0):
                return 0
        if self.agree is not None:
            other = values.get(self.agree)
            if other is None or other == 0 or (other > 0) != (sign > 0):
                return 0
        return sign


DRIVE_GRID = (0.5, 1.0, 1.5, 2.0)
EFFICIENCY_GRID = (0.3, 0.5, 0.7)


def rule_grid() -> list[Rule]:
    rules = [Rule(f"ret>={x}", "A", x) for x in DRIVE_GRID]
    rules += [
        Rule(f"ret>={x} & eff>={y}", "A", x, efficiency=y)
        for x in DRIVE_GRID
        for y in EFFICIENCY_GRID
    ]
    rules += [Rule(f"ret>={x} & vwap", "B", x, agree=VWAP_DIST) for x in DRIVE_GRID]
    rules += [Rule(f"ret>={x} & delta", "C", x, agree=DELTA_SHARE) for x in DRIVE_GRID]
    return rules


RULES = rule_grid()
#: Quantas regras a etapa testou. Registrado porque uma busca nao declarada e
#: uma correcao de multiplicidade que ninguem pode aplicar depois.
RULES_TESTED = len(RULES)


# --------------------------------------------------------------------------
# acumuladores -- arrays paralelos, porque o painel tem centenas de milhares
# de linhas e uma dataclass por linha nao cabe na memoria
# --------------------------------------------------------------------------

OUTCOMES = ("choch_pressure", "bos_pressure", "bos_resume", "choch_against", "choch_failed", "none")
OUTCOME_CODE = {name: code for code, name in enumerate(OUTCOMES)}


class CandleStore:
    """Uma linha por candle amostrado (`STRIDE`), por timeframe.

    Guarda a feature e o alvo lado a lado, mais o estrato (tendencia
    confirmada, perna active/stale) e o timestamp -- o corte discovery/holdout
    e feito no fim, sobre o timestamp, e nunca sobre nada que dependa do
    resultado.
    """

    def __init__(self) -> None:
        self.ts = array("q")
        self.trend = array("b")
        self.stale = array("b")
        self.net: dict[int, array] = {h: array("f") for h in PERSIST_HORIZONS}
        self.values: dict[str, array] = {name: array("f") for name in FEATURES}
        self.placebo: dict[str, array] = {name: array("f") for name in FEATURES}

    def add(
        self,
        ts: datetime,
        trend: MarketDirection,
        stale: bool,
        nets: dict[int, float],
        values: dict[str, float | None],
        placebo: dict[str, float | None],
    ) -> None:
        self.ts.append(int(ts.timestamp()))
        self.trend.append(1 if trend is MarketDirection.BULLISH else -1)
        self.stale.append(1 if stale else 0)
        for horizon in PERSIST_HORIZONS:
            self.net[horizon].append(nets.get(horizon, float("nan")))
        for name in FEATURES:
            raw = values.get(name)
            self.values[name].append(float("nan") if raw is None else raw)
            other = placebo.get(name)
            self.placebo[name].append(float("nan") if other is None else other)

    def __len__(self) -> int:
        return len(self.ts)


class EpisodeStore:
    """Episodios de pressao de UMA regra (secao 7), em arrays paralelos."""

    def __init__(self) -> None:
        self.ts = array("q")
        self.tf = array("b")
        self.direction = array("b")
        self.conflict = array("b")
        self.stale = array("b")
        self.duration = array("i")
        self.intensity = array("f")
        self.mfe = array("f")
        self.mae = array("f")
        self.net40 = array("f")
        self.outcome = array("b")
        self.resume_at = array("i")

    def add(self, **row: Any) -> None:
        for name, value in row.items():
            getattr(self, name).append(value)

    def __len__(self) -> int:
        return len(self.ts)


TF_CODE = {tf: code for code, tf in enumerate(TIMEFRAMES)}
TF_BY_CODE = {code: tf for tf, code in TF_CODE.items()}


def block_permutation(length: int, rng: random.Random) -> list[int]:
    """Indices embaralhados por blocos: o placebo de lead casado.

    Preserva a distribuicao da feature e a autocorrelacao dentro do bloco,
    destroi o alinhamento com o futuro. Uma feature que separa no placebo esta
    medindo o periodo, nao a pressao.
    """
    blocks = [
        list(range(start, min(start + PLACEBO_BLOCK, length)))
        for start in range(0, length, PLACEBO_BLOCK)
    ]
    rng.shuffle(blocks)
    return [index for block in blocks for index in block]


def first_resume(
    events: Sequence[MarketStructure],
    by_ts: dict[datetime, int],
    index: int,
    confirmed: MarketDirection,
    limit: int,
) -> int:
    """Candles ate o primeiro BOS nao-provisional da estrutura ORIGINAL.

    E a definicao de `false conflict` da secao 20, analoga ao `quick_resume` do
    StructuralStall: a pressao apontou contra a estrutura e a estrutura seguiu
    andando logo depois. Devolve -1 se nao houve nenhum dentro de `limit`.
    """
    best = -1
    for event in events:
        if (
            event.provisional
            or event.event is not StructureEvent.BREAK_OF_STRUCTURE
            or event.direction is not confirmed
        ):
            continue
        at = by_ts.get(event.timestamp)
        if at is None or at <= index or at > index + limit:
            continue
        if best < 0 or at - index < best:
            best = at - index
    return best


def episode_bounds(
    row: Sequence[int],
    start: int,
    limit: int,
    *,
    gap: int = EPISODE_GAP,
) -> int:
    """O ultimo candle do episodio aberto em `start`, tolerando `gap` de buraco.

    Extraida do laco que vivia dentro de `collect_combo` sem nenhuma mudanca
    de regra: o episodio segue enquanto a mesma direcao reaparece, e uma
    chamada da direcao OPOSTA o encerra na hora (nao e buraco, e desmentido).

    O `gap + 1` e a correcao do off-by-one. O que se compara com `gap` aqui e
    `cursor - last`, que e a DISTANCIA entre o ultimo candle aceso e o
    candidato -- e a distancia atraves de um buraco de N candles desligados
    vale N + 1. Com a condicao ingenua, `EPISODE_GAP = 3` saía do laco no
    exato candle que religava a oposicao depois de tres desligados, partindo
    em dois um episodio que a tolerancia declarada mandava manter inteiro.
    Le-se assim: o que nao pode passar de `gap` e o tamanho do buraco,
    `cursor - last - 1`.
    """
    direction = row[start]
    last = start
    cursor = start
    while cursor < limit and cursor - last <= gap + 1:
        if row[cursor] == direction:
            last = cursor
        elif row[cursor] == -direction:
            break
        cursor += 1
    return last


@dataclass
class CaseRow:
    """Uma linha dos casos obrigatorios da secao 10."""

    symbol: str
    timeframe: str
    timestamp: str
    close: float
    atr_pct: float
    confirmed_trend: str | None
    leg_state: str
    pressure: int
    drive: float | None
    efficiency: float | None
    vwap_dist: float | None
    delta_share: float | None


def collect_combo(
    symbol: str,
    timeframe: TimeFrame,
    series: Sequence[Candle],
    end: int,
    store: CandleStore,
    episodes: Sequence[EpisodeStore],
    rng: random.Random,
    case_rule: Rule,
    cases: dict[str, list[CaseRow]],
) -> int:
    """Uma janela de producao inteira, medida. Devolve quantos candles entraram."""
    run = dd._run_internal_structure(
        provider=SliceProvider(list(series[end - LIMIT - BUFFER : end])),
        symbol=symbol,
        timeframe=timeframe,
        limit=LIMIT,
        confluence_filter=True,
    )
    candles = run.candles
    by_ts = {candle.timestamp: index for index, candle in enumerate(candles)}
    atr = atr_pct_series(candles)
    legs = leg_state_by_index(candles, run.events, by_ts)
    trends = trend_by_index(run.events, by_ts, len(candles))
    matrix = feature_matrix(candles, atr, legs)
    permutation = block_permutation(len(candles), rng)
    horizon = max(PERSIST_HORIZONS)
    structure_horizon = max(STRUCTURE_HORIZONS)

    def values_at(index: int) -> dict[str, float | None]:
        return {name: matrix[name][index] for name in FEATURES}

    # ---- linhas por candle (univariado, secao 12) --------------------------
    used = 0
    for index in range(0, len(candles) - horizon, STRIDE):
        trend = trends[index]
        if trend is None:
            continue
        raw = future_targets(candles, atr, index)
        if raw is None or len(raw) < len(PERSIST_HORIZONS):
            continue
        placebo_index = permutation[index]
        store.add(
            candles[index].timestamp,
            trend,
            legs[index].stale,
            {h: raw[h][0] for h in PERSIST_HORIZONS},
            values_at(index),
            {name: matrix[name][placebo_index] for name in FEATURES},
        )
        used += 1

    # ---- episodios por regra (secoes 7 e 19) -------------------------------
    calls: list[list[int]] = []
    for rule in RULES:
        row = [0] * len(candles)
        for index in range(len(candles)):
            if trends[index] is None:
                continue
            row[index] = rule.call(values_at(index))
        calls.append(row)

    for rule_index, row in enumerate(calls):
        index = 0
        while index < len(candles) - horizon:
            direction = row[index]
            if direction == 0:
                index += 1
                continue
            start = index
            last = episode_bounds(row, index, len(candles) - horizon)
            trend = trends[start]
            assert trend is not None
            raw = future_targets(candles, atr, start)
            if raw is None or horizon not in raw:
                index = last + 1
                continue
            net, mfe, mae, _eff = pressure_targets(raw[horizon], direction)
            drives = [
                abs(matrix["ret_atr_10"][j])
                for j in range(start, last + 1)
                if matrix["ret_atr_10"][j] is not None
            ]
            want = MarketDirection.BULLISH if direction > 0 else MarketDirection.BEARISH
            conflict = want is not trend
            episodes[rule_index].add(
                ts=int(candles[start].timestamp.timestamp()),
                tf=TF_CODE[timeframe],
                direction=direction,
                conflict=1 if conflict else 0,
                stale=1 if legs[start].stale else 0,
                duration=last - start + 1,
                intensity=statistics.median(drives) if drives else float("nan"),
                mfe=mfe,
                mae=mae,
                net40=net,
                outcome=OUTCOME_CODE[
                    structural_outcome(
                        run.events, by_ts, start, structure_horizon, direction, trend
                    )
                ],
                resume_at=first_resume(
                    run.events, by_ts, start, trend, max(PERSIST_HORIZONS)
                ),
            )
            index = last + 1

    # ---- os casos obrigatorios (secao 10) ----------------------------------
    key = f"{symbol}_{timeframe.value}"
    if key in cases:
        rule_row = calls[RULES.index(case_rule)]
        for index in range(len(candles)):
            trend = trends[index]
            cases[key].append(
                CaseRow(
                    symbol=symbol,
                    timeframe=timeframe.value,
                    timestamp=candles[index].timestamp.isoformat(),
                    close=candles[index].close,
                    atr_pct=atr[index],
                    confirmed_trend=trend.value if trend else None,
                    leg_state=(
                        "stale"
                        if legs[index].stale
                        else ("active" if legs[index].direction else "none")
                    ),
                    pressure=rule_row[index],
                    drive=matrix["ret_atr_10"][index],
                    efficiency=matrix[EFF_10][index],
                    vwap_dist=matrix[VWAP_DIST][index],
                    delta_share=matrix[DELTA_SHARE][index],
                )
            )
    return used


# --------------------------------------------------------------------------
# estatistica
# --------------------------------------------------------------------------


def auc(values: Sequence[float], labels: Sequence[int]) -> tuple[float, int] | None:
    """AUC de Mann-Whitney, com empates tratados por posto medio.

    Devolve `(auc, n)` ou `None` se um dos lados nao tem 30 observacoes. AUC
    0.50 e "nao separa"; a leitura util aqui e a DISTANCIA de 0.50, porque uma
    feature assinada com AUC 0.44 separa tanto quanto uma com 0.56.
    """
    pairs = [
        (value, label)
        for value, label in zip(values, labels, strict=True)
        if not math.isnan(value)
    ]
    positives = sum(1 for _v, label in pairs if label > 0)
    negatives = len(pairs) - positives
    if positives < 30 or negatives < 30:
        return None
    pairs.sort(key=lambda pair: pair[0])
    ranks = [0.0] * len(pairs)
    position = 0
    while position < len(pairs):
        stop = position
        while stop + 1 < len(pairs) and pairs[stop + 1][0] == pairs[position][0]:
            stop += 1
        mean_rank = (position + stop) / 2.0 + 1.0
        for i in range(position, stop + 1):
            ranks[i] = mean_rank
        position = stop + 1
    rank_sum = sum(rank for rank, (_v, label) in zip(ranks, pairs, strict=True) if label > 0)
    value = (rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives)
    return value, len(pairs)


def rate(hits: int, total: int) -> float | None:
    return hits / total if total else None


@dataclass
class Selection:
    """Um recorte de linhas do `CandleStore`, ja resolvido em indices."""

    label: str
    indices: list[int]


def cut_by_timeframe(store: CandleStore) -> int:
    """O timestamp que separa discovery (70% mais antigo) de holdout (secao 21)."""
    if not len(store):
        return 0
    stamps = sorted(store.ts)
    return stamps[int(len(stamps) * DISCOVERY_FRACTION)]


def select(
    store: CandleStore,
    cut: int,
    *,
    sample: str | None = None,
    trend: int | None = None,
    stale: int | None = None,
) -> list[int]:
    out = []
    for index in range(len(store)):
        if sample == "discovery" and store.ts[index] >= cut:
            continue
        if sample == "holdout" and store.ts[index] < cut:
            continue
        if trend is not None and store.trend[index] != trend:
            continue
        if stale is not None and store.stale[index] != stale:
            continue
        out.append(index)
    return out


def univariate(store: CandleStore, indices: Sequence[int], horizon: int = 10) -> dict:
    """AUC de cada feature (e do seu placebo) contra o movimento futuro assinado."""
    labels = []
    keep = []
    for index in indices:
        net = store.net[horizon][index]
        if math.isnan(net) or net == 0:
            continue
        keep.append(index)
        labels.append(1 if net > 0 else 0)
    out: dict[str, dict] = {}
    for name in FEATURES:
        column = store.values[name]
        placebo_column = store.placebo[name]
        real = auc([column[i] for i in keep], labels)
        fake = auc([placebo_column[i] for i in keep], labels)
        clean = [column[i] for i in keep if not math.isnan(column[i])]
        out[name] = {
            "family": FEATURE_FAMILY[name],
            "auc": None if real is None else real[0],
            "n": None if real is None else real[1],
            "placebo_auc": None if fake is None else fake[0],
            "p25": quantile(clean, 0.25) if clean else None,
            "median": statistics.median(clean) if clean else None,
            "p75": quantile(clean, 0.75) if clean else None,
        }
    return out


def rule_on_store(store: CandleStore, rule: Rule, index: int) -> int:
    return rule.call({name: store.values[name][index] for name in FEATURES})


def rule_metrics(store: CandleStore, rule: Rule, indices: Sequence[int]) -> dict:
    """Cobertura e persistencia por candle, com abstencao explicita (secao 18)."""
    fired = 0
    conflict = 0
    persist: dict[int, list[float]] = {h: [] for h in PERSIST_HORIZONS}
    by_side: dict[int, list[float]] = {1: [], -1: []}
    by_stale: dict[int, list[float]] = {0: [], 1: []}
    for index in indices:
        direction = rule_on_store(store, rule, index)
        if direction == 0:
            continue
        fired += 1
        if direction != store.trend[index]:
            conflict += 1
        for horizon in PERSIST_HORIZONS:
            net = store.net[horizon][index]
            if not math.isnan(net):
                persist[horizon].append(direction * net)
        net10 = store.net[10][index]
        if not math.isnan(net10):
            by_side[direction].append(direction * net10)
            by_stale[store.stale[index]].append(direction * net10)
    # A TAXA-BASE, sem a qual `persist` nao quer dizer nada. Num painel que
    # caiu no periodo, uma chamada bullish acerta menos de 50% sem que a
    # camada tenha errado -- e uma bearish acerta mais sem ter acertado nada.
    # `persist10_bullish` le-se contra `base_bullish`; `persist10_bearish`
    # contra `1 - base_bullish`. Comparar com 50% e o erro que este bloco
    # existe para impedir.
    base = [store.net[10][index] for index in indices if not math.isnan(store.net[10][index])]
    out: dict[str, Any] = {
        "coverage": rate(fired, len(indices)),
        "n_fired": fired,
        "conflict_share": rate(conflict, fired),
        "base_bullish": rate(sum(1 for value in base if value > 0), len(base)),
    }
    for horizon in PERSIST_HORIZONS:
        moves = persist[horizon]
        out[f"persist_{horizon}"] = rate(sum(1 for m in moves if m > 0), len(moves))
        out[f"net_{horizon}"] = statistics.median(moves) if moves else None
    for side, key in ((1, "bullish"), (-1, "bearish")):
        moves = by_side[side]
        out[f"persist10_{key}"] = rate(sum(1 for m in moves if m > 0), len(moves))
        out[f"n_{key}"] = len(moves)
    for flag, key in ((0, "active"), (1, "stale")):
        moves = by_stale[flag]
        out[f"persist10_{key}"] = rate(sum(1 for m in moves if m > 0), len(moves))
        out[f"n_{key}"] = len(moves)
    return out


def episode_metrics(
    store: EpisodeStore, cuts: dict[TimeFrame, int], sample: str | None
) -> dict:
    """Metricas por EPISODIO (secao 19), nunca por candle: um episodio de 40
    candles nao e quarenta evidencias.

    O corte discovery/holdout e o do TIMEFRAME do episodio -- um corte unico
    misturaria o holdout do D1 com o discovery do M15, que foi exatamente a
    correcao da Etapa 4.2.
    """
    keep = []
    for index in range(len(store)):
        cut = cuts[TF_BY_CODE[store.tf[index]]]
        if sample == "discovery" and store.ts[index] >= cut:
            continue
        if sample == "holdout" and store.ts[index] < cut:
            continue
        keep.append(index)
    if not keep:
        return {"n": 0}
    conflicts = [i for i in keep if store.conflict[i]]
    durations = [store.duration[i] for i in keep]
    mfe = [store.mfe[i] for i in keep if not math.isnan(store.mfe[i])]
    mae = [store.mae[i] for i in keep if not math.isnan(store.mae[i])]
    net = [store.net40[i] for i in keep if not math.isnan(store.net40[i])]
    outcomes = {name: 0 for name in OUTCOMES}
    for i in keep:
        outcomes[OUTCOMES[store.outcome[i]]] += 1
    false_conflict = {}
    for horizon in PERSIST_HORIZONS:
        hits = sum(
            1 for i in conflicts if 0 <= store.resume_at[i] <= horizon
        )
        false_conflict[horizon] = rate(hits, len(conflicts))
    return {
        "n": len(keep),
        "n_conflict": len(conflicts),
        "duration_median": statistics.median(durations),
        "mfe_median": statistics.median(mfe) if mfe else None,
        "mae_median": statistics.median(mae) if mae else None,
        "net40_median": statistics.median(net) if net else None,
        "net40_positive": rate(sum(1 for value in net if value > 0), len(net)),
        "outcomes": {name: rate(count, len(keep)) for name, count in outcomes.items()},
        "false_conflict": false_conflict,
    }


# --------------------------------------------------------------------------
# o score da secao 17 -- so vale se BATER a regra simples
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoreRule(Rule):
    """`pressure_score` em [-1, +1], como media de componentes explicaveis.

    Nao e um modelo: cada componente e uma das features assinadas, cortada em
    [-1, +1] por uma escala fixa e declarada. Existe para responder uma
    pergunta so -- combinar acrescenta sobre a melhor regra de duas condicoes?
    Se nao acrescentar, e descartado, e a secao 17 permite exatamente isso.
    """

    scales: tuple[tuple[str, float], ...] = (
        ("ret_atr_10", 2.0),
        (EFF_10, 1.0),
        (VWAP_DIST, 2.0),
        (DELTA_SHARE, 0.5),
    )

    def score(self, values: dict[str, float | None]) -> float | None:
        parts = []
        for name, scale in self.scales:
            raw = values.get(name)
            if raw is None:
                continue
            parts.append(max(-1.0, min(1.0, raw / scale)))
        if len(parts) < len(self.scales):
            return None
        return sum(parts) / len(parts)

    def call(self, values: dict[str, float | None]) -> int:
        value = self.score(values)
        if value is None or abs(value) < self.threshold:
            return 0
        return 1 if value > 0 else -1


SCORE_GRID = (0.3, 0.5)
RULES = rule_grid() + [ScoreRule(f"score>={s}", "C", s) for s in SCORE_GRID]
RULES_TESTED = len(RULES)

#: O criterio de escolha, congelado ANTES de olhar o discovery (secao 21): a
#: maior persistencia em 10 candles entre as regras que falam o bastante para
#: serem uma camada (>=5% dos candles) e tem n suficiente. Empate resolve pela
#: menor familia -- price-only ganha de EMA/VWAP, que ganha de fluxo.
MIN_COVERAGE = 0.05
MIN_FIRED = 200


def choose_rule(
    store_by_tf: dict[TimeFrame, CandleStore], cuts: dict[TimeFrame, int]
) -> tuple[Rule, dict]:
    scored: list[tuple[float, int, Rule, dict]] = []
    for rule in RULES:
        totals = {"n_fired": 0, "n_rows": 0, "hits": 0, "moves": 0}
        for timeframe, store in store_by_tf.items():
            indices = select(store, cuts[timeframe], sample="discovery")
            metrics = rule_metrics(store, rule, indices)
            totals["n_rows"] += len(indices)
            totals["n_fired"] += metrics["n_fired"]
            persist = metrics["persist_10"]
            if persist is not None:
                moves = metrics["n_bullish"] + metrics["n_bearish"]
                totals["hits"] += round(persist * moves)
                totals["moves"] += moves
        coverage = rate(totals["n_fired"], totals["n_rows"]) or 0.0
        persist = rate(totals["hits"], totals["moves"])
        if persist is None or coverage < MIN_COVERAGE or totals["n_fired"] < MIN_FIRED:
            continue
        scored.append((persist, -FAMILY_ORDER.index(rule.family), rule, {
            "coverage": coverage,
            "persist_10": persist,
            "n_fired": totals["n_fired"],
        }))
    if not scored:
        return RULES[0], {}
    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return scored[0][2], scored[0][3]


# --------------------------------------------------------------------------
# coleta
# --------------------------------------------------------------------------


def build(symbols: Sequence[str], windows: int) -> dict:
    store_by_tf: dict[TimeFrame, CandleStore] = {tf: CandleStore() for tf in TIMEFRAMES}
    episodes = [EpisodeStore() for _ in RULES]
    cases: dict[str, list[CaseRow]] = {
        f"{ZEC_BULLISH[0]}_{ZEC_BULLISH[1].value}": [],
        f"{BTC_CASE[0]}_{BTC_CASE[1].value}": [],
    }
    rng = random.Random(PLACEBO_SEED)
    combos = 0
    rows = 0
    case_rule = RULES[RULES.index(Rule("ret>=1.0", "A", 1.0))]

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
                        store_by_tf[timeframe],
                        episodes,
                        rng,
                        case_rule,
                        cases if window == 0 else {},
                    )
                except Exception as error:  # noqa: BLE001 - feed morto
                    print(f"! janela invalida: {symbol} {timeframe.value} w{window}: {error}")
                    continue
                combos += 1

    cuts = {tf: cut_by_timeframe(store) for tf, store in store_by_tf.items()}
    print(f"combos medidos: {combos}  candles amostrados: {rows}  regras testadas: {RULES_TESTED}")

    report: dict[str, Any] = {
        "symbols": list(symbols),
        "combos": combos,
        "rows": rows,
        "stride": STRIDE,
        "rules_tested": RULES_TESTED,
        "univariate": {},
        "rules": {},
        "episodes": {},
        "cases": {},
    }

    # ---- 12/13. univariado, por timeframe e por estrato de tendencia -------
    for timeframe, store in store_by_tf.items():
        if not len(store):
            continue
        cut = cuts[timeframe]
        report["univariate"][timeframe.value] = {
            "all": univariate(store, select(store, cut)),
            "discovery": univariate(store, select(store, cut, sample="discovery")),
            "holdout": univariate(store, select(store, cut, sample="holdout")),
            "trend_bullish": univariate(store, select(store, cut, trend=1)),
            "trend_bearish": univariate(store, select(store, cut, trend=-1)),
            "n": len(store),
        }

    # ---- 16/21. regra escolhida no discovery, congelada, e o holdout -------
    chosen, chosen_discovery = choose_rule(store_by_tf, cuts)
    report["chosen_rule"] = {"label": chosen.label, "family": chosen.family, **chosen_discovery}
    for rule in RULES:
        entry: dict[str, Any] = {"family": rule.family}
        for timeframe, store in store_by_tf.items():
            if not len(store):
                continue
            cut = cuts[timeframe]
            entry[timeframe.value] = {
                "discovery": rule_metrics(store, rule, select(store, cut, sample="discovery")),
                "holdout": rule_metrics(store, rule, select(store, cut, sample="holdout")),
            }
        report["rules"][rule.label] = entry

    for rule, store in zip(RULES, episodes, strict=True):
        if rule.label != chosen.label and rule.family != "A":
            continue
        report["episodes"][rule.label] = {
            "all": episode_metrics(store, cuts, None),
            "discovery": episode_metrics(store, cuts, "discovery"),
            "holdout": episode_metrics(store, cuts, "holdout"),
        }

    report["cases"] = {key: [asdict(row) for row in rows_] for key, rows_ in cases.items()}
    return report


# --------------------------------------------------------------------------
# os casos obrigatorios (secao 10)
# --------------------------------------------------------------------------


def zec_case(rows: Sequence[dict], choch_iso: str) -> dict:
    """Quando a pressao bullish teria aparecido no ZEC D1, e a que distancia.

    O CHoCH bullish oficial e o alvo apenas para MEDIR o lead. Nada aqui
    escolhe regra nem limiar (secao 22): se a regra escolhida no painel nao
    antecipa o ZEC, isso e reportado como falha da regra, nao corrigido.
    """
    by_ts = {row["timestamp"]: index for index, row in enumerate(rows)}
    at = by_ts.get(choch_iso)
    if at is None:
        return {"found": False}
    # Dois candidatos, porque as duas leituras sao defensaveis e escondê-las
    # uma da outra seria escolher a que da o lead mais bonito:
    # `signal`   -- inicio do ULTIMO bloco contiguo de pressao bullish antes do
    #               CHoCH (o sinal que ainda estava de pe quando ele saiu);
    # `earliest` -- a PRIMEIRA vez que a pressao apontou bullish com a
    #               estrutura ainda bearish, dentro da mesma perna bearish.
    signal = None
    for index in range(at - 1, -1, -1):
        row = rows[index]
        if row["pressure"] == 1 and row["confirmed_trend"] == "bearish":
            signal = index
        elif signal is not None and row["pressure"] == -1:
            break
    leg_start = 0
    for index in range(at - 1, -1, -1):
        if rows[index]["confirmed_trend"] != "bearish":
            leg_start = index + 1
            break
    earliest = next(
        (
            index
            for index in range(leg_start, at)
            if rows[index]["pressure"] == 1 and rows[index]["confirmed_trend"] == "bearish"
        ),
        None,
    )
    if signal is None:
        return {"found": True, "signal": None, "choch": rows[at]}
    origin = min(row["close"] for row in rows[max(0, signal - 60) : signal + 1])
    unit = rows[signal]["atr_pct"] * rows[signal]["close"]
    return {
        "found": True,
        "signal": rows[signal],
        "earliest": None if earliest is None else rows[earliest],
        "earliest_lead_bars": None if earliest is None else at - earliest,
        "choch": rows[at],
        "lead_bars": at - signal,
        "lead_atr": (rows[at]["close"] - rows[signal]["close"]) / unit if unit else None,
        "move_at_signal_pct": 100.0 * (rows[signal]["close"] / origin - 1.0),
        "move_at_choch_pct": 100.0 * (rows[at]["close"] / origin - 1.0),
    }


def btc_case(rows: Sequence[dict]) -> dict:
    """O veredito na borda viva do BTC H1: bearish pressure ou balanced?

    A pergunta da secao 10B e sobre a diferenca entre STALE e OPPOSING
    PRESSURE. Uma perna parada nao e pressao contraria, e a resposta honesta
    aqui pode perfeitamente ser `balanced` -- forcar bearish porque a perna
    esta stale seria justamente o erro que a secao adverte.
    """
    if not rows:
        return {"found": False}
    tail = rows[-40:]
    counts = {"bullish": 0, "bearish": 0, "balanced": 0}
    for row in tail:
        counts[{1: "bullish", -1: "bearish", 0: "balanced"}[row["pressure"]]] += 1
    last = rows[-1]
    return {
        "found": True,
        "last": last,
        "verdict": {1: "bullish", -1: "bearish", 0: "balanced"}[last["pressure"]],
        "last40": counts,
        "stale_share_last40": sum(1 for row in tail if row["leg_state"] == "stale") / len(tail),
    }


# --------------------------------------------------------------------------
# relatorio
# --------------------------------------------------------------------------


def num(value: float | None, digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def pct(value: float | None) -> str:
    return "-" if value is None else f"{100 * value:.1f}%"


def print_report(report: dict) -> None:
    print("\n== 12. UNIVARIADO: AUC vs movimento assinado em 10 candles ==")
    print("  (0.500 = nao separa. `placebo` = mesma feature em bloco embaralhado.)")
    for timeframe, block in report["univariate"].items():
        print(f"\n  --- {timeframe}  (n={block['n']}) ---")
        print("    feature                  fam    AUC  placebo    bull    bear   hold")
        print("    " + "-" * 68)
        rows = sorted(
            block["all"].items(),
            key=lambda item: abs((item[1]["auc"] or 0.5) - 0.5),
            reverse=True,
        )
        for name, stats in rows:
            print(
                f"    {name:<22} {stats['family']:>3}  "
                f"{num(stats['auc'])}  {num(stats['placebo_auc'])}  "
                f"{num(block['trend_bullish'][name]['auc'])}  "
                f"{num(block['trend_bearish'][name]['auc'])}  "
                f"{num(block['holdout'][name]['auc'])}"
            )

    print("\n== 4. GANHO POR FAMILIA (melhor |AUC-0.5| de cada camada) ==")
    print("    tf     A price-only        B +EMA/VWAP         C +fluxo")
    print("    " + "-" * 62)
    for timeframe, block in report["univariate"].items():
        cells = []
        for family in FAMILY_ORDER:
            best = max(
                (
                    (abs((stats["auc"] or 0.5) - 0.5), name, stats["auc"])
                    for name, stats in block["all"].items()
                    if stats["family"] == family
                ),
                default=(0.0, "-", None),
            )
            cells.append(f"{best[1][:14]:<14} {num(best[2])}")
        print(f"    {timeframe:<6} " + "  ".join(cells))

    chosen = report.get("chosen_rule", {})
    print(f"\n== 16/21. REGRA ESCOLHIDA NO DISCOVERY (de {report['rules_tested']} testadas) ==")
    print(
        f"    {chosen.get('label', '-')}  familia {chosen.get('family', '-')}  "
        f"cobertura {pct(chosen.get('coverage'))}  persist_10 {pct(chosen.get('persist_10'))}"
    )

    print("\n== 19. REGRAS: cobertura e persistencia por candle ==")
    print("  regra                 tf     cov   confl   p5     p10    p20    p40   |  hold p10")
    print("  " + "-" * 82)
    for label, entry in report["rules"].items():
        for timeframe in report["univariate"]:
            block = entry.get(timeframe)
            if not block:
                continue
            d = block["discovery"]
            h = block["holdout"]
            print(
                f"  {label:<20} {timeframe:<5} {pct(d['coverage']):>6} "
                f"{pct(d['conflict_share']):>7} "
                f"{pct(d['persist_5']):>6} {pct(d['persist_10']):>6} "
                f"{pct(d['persist_20']):>6} {pct(d['persist_40']):>6}  |  {pct(h['persist_10']):>6}"
            )

    print("\n== 7/19/20. EPISODIOS ==")
    for label, block in report["episodes"].items():
        for sample in ("all", "discovery", "holdout"):
            stats = block[sample]
            if not stats.get("n"):
                continue
            print(
                f"  {label:<22} {sample:<10} n={stats['n']:<6} conflito={stats['n_conflict']:<6} "
                f"dur={stats['duration_median']:<4} MFE={num(stats['mfe_median'], 2)} "
                f"MAE={num(stats['mae_median'], 2)} net40={num(stats['net40_median'], 2)} "
                f"({pct(stats['net40_positive'])} >0)"
            )
            if sample == "all":
                outcomes = "  ".join(
                    f"{name}={pct(value)}" for name, value in stats["outcomes"].items()
                )
                print(f"      desfecho estrutural: {outcomes}")
                fc = "  ".join(
                    f"<={h}:{pct(value)}" for h, value in stats["false_conflict"].items()
                )
                print(f"      false conflict:      {fc}")

    print("\n== 14. STALE COMO CONTEXTO (persist_10 da regra escolhida) ==")
    entry = report["rules"].get(chosen.get("label", ""), {})
    print("    tf      active    stale     n_active   n_stale")
    print("    " + "-" * 50)
    for timeframe in report["univariate"]:
        block = entry.get(timeframe)
        if not block:
            continue
        d = block["discovery"]
        print(
            f"    {timeframe:<6} {pct(d['persist10_active']):>7} {pct(d['persist10_stale']):>8} "
            f"{d['n_active']:>10} {d['n_stale']:>9}"
        )

    print("\n== 13. SIMETRIA (persist_10 da regra escolhida, contra a TAXA-BASE) ==")
    print("  base = P(alta em 10 velas) no timeframe. A chamada bearish le-se contra 1-base.")
    print("    tf     bullish  (base)   bearish  (base)    n_bull    n_bear")
    print("    " + "-" * 64)
    for timeframe in report["univariate"]:
        block = entry.get(timeframe)
        if not block:
            continue
        d = block["discovery"]
        base = d["base_bullish"]
        print(
            f"    {timeframe:<6} {pct(d['persist10_bullish']):>7} {pct(base):>8}  "
            f"{pct(d['persist10_bearish']):>7} {pct(None if base is None else 1 - base):>8} "
            f"{d['n_bullish']:>9} {d['n_bearish']:>9}"
        )

    print("\n== 10. CASOS OBRIGATORIOS ==")
    zec = report.get("zec", {})
    if zec.get("found") and zec.get("signal"):
        print(
            f"  A) ZEC D1: primeiro bullish pressure {zec['signal']['timestamp'][:10]} "
            f"(close {zec['signal']['close']:.2f}) | CHoCH oficial "
            f"{zec['choch']['timestamp'][:10]} (close {zec['choch']['close']:.2f})"
        )
        print(
            f"     lead {zec['lead_bars']} velas / {num(zec['lead_atr'], 1)} ATR | "
            f"movimento no sinal {zec['move_at_signal_pct']:.1f}% "
            f"vs no CHoCH {zec['move_at_choch_pct']:.1f}%"
        )
        first = zec.get("earliest")
        if first:
            print(
                f"     primeiro bullish pressure da perna bearish: "
                f"{first['timestamp'][:10]} (lead {zec['earliest_lead_bars']} velas)"
            )
    else:
        print(f"  A) ZEC D1: {zec}")
    btc = report.get("btc", {})
    if btc.get("found"):
        last = btc["last"]
        print(
            f"  B) BTC H1 borda viva {last['timestamp'][:16]}: "
            f"confirmed={last['confirmed_trend']} leg={last['leg_state']} "
            f"-> pressure = {btc['verdict'].upper()}"
        )
        print(
            f"     ultimos 40 candles: {btc['last40']} | "
            f"stale em {pct(btc['stale_share_last40'])}"
        )
    else:
        print(f"  B) BTC H1: {btc}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--windows", type=int, default=WINDOWS)
    parser.add_argument(
        "--json",
        type=Path,
        default=Path(__file__).with_name("current_market_pressure_baseline.json"),
    )
    args = parser.parse_args(argv)

    symbols = args.symbols
    if not symbols:
        from research.choch_leg_opener import expanded_symbols

        symbols = expanded_symbols()

    report = build(symbols, args.windows)
    report["zec"] = zec_case(
        report["cases"].get(f"{ZEC_BULLISH[0]}_{ZEC_BULLISH[1].value}", []), ZEC_BULLISH[2]
    )
    report["btc"] = btc_case(report["cases"].get(f"{BTC_CASE[0]}_{BTC_CASE[1].value}", []))
    print_report(report)
    args.json.write_text(json.dumps(report, indent=1, default=str))
    print(f"\n-> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
