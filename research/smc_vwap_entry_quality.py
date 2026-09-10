"""Etapa 7.0 -- a VWAP ajuda a escolher ONDE e QUANDO entrar, depois da estrutura?

Esta e uma pergunta diferente das tres anteriores, e a diferenca importa mais
que os numeros. A Etapa 5.0 perguntou se existe uma leitura de pressao atual
(nao existe). A 5.1 perguntou se o Tide antecipa a transicao (nao antecipa: a
cor do Tide **e** o `final_trend`). A 6.0 perguntou se a VWAP qualifica um
evento ja confirmado (nao qualifica: o evento ja nasce do lado certo da VWAP, e
o que separava era o deslocamento pos-evento).

As tres perguntavam da VWAP alguma coisa sobre **estrutura**. Esta pergunta
inverte os papeis, e e a divisao de trabalho em que os dois conceitos de fato
se complementam:

> **SMC decide direcao, estrutura, nivel e contexto. A VWAP decide *location*:
> valor, extensao, qualidade do reteste e timing.** Depois que a estrutura ja
> existe, entrar perto do valor e melhor do que perseguir a extensao?

Nada aqui vai para producao, nada toca BOS/CHoCH/`CHOCH_FAILED`/`final_trend`,
e nada tenta antecipar estrutura ou ressuscitar *current market pressure*.

--------------------------------------------------------------------------
1. O BASELINE E SMC PURO, E ELE VEM PRIMEIRO
--------------------------------------------------------------------------

A VWAP so pode "melhorar" alguma coisa se existir um alguma coisa medido sem
ela. Entao cada familia define entrada, stop e alvo **sem olhar a VWAP**, e
todo numero de VWAP e um recorte dentro dessa populacao -- nunca uma populacao
propria. Um filtro que muda a populacao base nao esta melhorando o setup, esta
medindo outro setup.

| familia | evento | entrada | stop |
|---|---|---|---|
| `bos_retest` | `BREAK_OF_STRUCTURE` | 1o fechamento que volta ao nivel | extremo do reteste |
| `choch_retest` | `CHANGE_OF_CHARACTER` | idem, no nivel do CHoCH | idem |
| `sweep_retest` | `LiquidityGrab` REJECTED alinhado | fechamento da rejeicao | extremo varrido |

(so eventos nao-provisionais, nas tres.)

O stop no **extremo confirmado** e a convencao ja medida do repositorio
(`docs/block_reclaim.md`: afrouxar o stop piora monotonicamente), e a
simulacao de trade -- alvo kR, stop 1R, stop checado primeiro dentro do
candle, marcado a mercado no fim do horizonte -- e a de
`research/vwap_ob_pinbar.py`, copiada em regra e nao em espirito, para que os
numeros desta etapa sejam comparaveis com os das anteriores.

--------------------------------------------------------------------------
2. O CONTROLE QUE DECIDE
--------------------------------------------------------------------------

A Etapa 6.0 morreu num controle e este e o mesmo em outra roupa: **"perto da
VWAP" pode ser so "o pullback foi profundo"**. Um pullback profundo chega perto
da VWAP por aritmetica, e tambem tem stop mais curto, o que sozinho aumenta o
R de qualquer coisa. Entao toda leitura de VWAP e lida contra quatro controles
que nao usam VWAP nenhuma:

- `pullback_atr` -- profundidade do pullback desde o extremo da perna, em ATR;
- `retrace_pct` -- a mesma coisa em percentual da perna;
- `swing_dist_atr` -- distancia ao ultimo swing confirmado;
- `ema9_dist_atr` -- distancia a EMA(9), a outra "linha de valor" do projeto.

E `r_atr` (o tamanho do stop no ATR do simbolo) e **emitido e nunca filtrado**:
a memoria do projeto diz que num outro setup esse gate *era* o setup, e
importar o limiar de la para ca seria transplantar um resultado em vez de
medi-lo.

--------------------------------------------------------------------------
3. POR TRADE E POR DIA
--------------------------------------------------------------------------

Todo recorte reporta as duas contas. A pesquisa de OB+VWAP deste repositorio
ja pagou para aprender que elas discordam: um filtro que sobe a expectativa
por trade e corta 80% das oportunidades pode perder dinheiro no tempo. Um
recorte so e melhor se `expectancy_per_day` nao cair -- e `per_day` aqui e
`soma de R liquido / dias-simbolo ativos`, nao uma media de medias.

--------------------------------------------------------------------------
4. CUSTO
--------------------------------------------------------------------------

`research/spread_cost.py` fechou que o spread **nao** e estimavel a partir de
candles em perp cripto, e `research/spread_trades.py` mediu na fita que a
**taxa** e o custo dominante (spread 0,3-8,5 bp contra 10 bp de taxa
round-trip). Entao o custo aqui e o round trip de taxa,
`2 x DEFAULT_TAKER_FEE`, convertido para R por trade como
`custo / r_pct` -- exatamente a conta de `research/vwap_exit_grid.py`. Toda
tabela mostra bruto e liquido.

--------------------------------------------------------------------------
5. O QUE NAO ENTRA NESTA RODADA
--------------------------------------------------------------------------

Sem fluxo (OI, `control_score`, CVD): se a combinacao SMC+VWAP pura nao
funcionar, empilhar fluxo por cima e procurar um resultado. Sem a **cor** do
Tide, que e a estrutura confirmada e ja esta na populacao. Sem
`convictionScale`, que normaliza pelo p90 da janela inteira e e lookahead.

Uso:

    poetry run python -m research.smc_vwap_entry_quality --expanded
    poetry run python -m research.smc_vwap_entry_quality --symbols BTCUSDT ETHUSDT SOLUSDT
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from liquidity_hunter.app import dashboard_data as dd
from liquidity_hunter.app.liquidity_grabs import build_liquidity_grabs
from liquidity_hunter.core.domain import (
    Candle,
    LiquidityGrabOutcome,
    LiquiditySide,
    MarketDirection,
    MarketStructure,
    StructureEvent,
    TimeFrame,
)
from liquidity_hunter.indicators.ema import ema_series
from liquidity_hunter.liquidity.detectors import (
    POIDetector,
    SwingHighDetector,
    SwingLowDetector,
)
from liquidity_hunter.liquidity.mitigation import mark_swept_zones
from research.choch_leg_opener import DISCOVERY_FRACTION, TIMEFRAMES, WINDOWS
from research.current_market_pressure import (
    ATR_WINDOW,
    atr_pct_series,
    auc,
    quantile,
    trend_by_index,
)
from research.range_choch import BUFFER, CACHE_DIR, LIMIT, SliceProvider, load_series
from research.spread_trades import DEFAULT_TAKER_FEE
from research.tide_structural_transition import phase_series, tide_geometry

BULL = MarketDirection.BULLISH
BEAR = MarketDirection.BEARISH

# --------------------------------------------------------------------------
# parametros -- fixados antes de olhar qualquer numero
# --------------------------------------------------------------------------

#: Candles depois do evento em que o reteste ainda conta como "deste evento".
RETEST_WINDOW = 40
#: Quantos retestes do mesmo nivel entram (secao B11: 1o vs revisitas).
MAX_TESTS = 3
#: Horizonte da simulacao. Alem disso o trade e marcado a mercado.
TARGET_HORIZON = 120
#: Alvos em R. O stop e sempre 1R, por construcao.
TARGETS = (1.0, 2.0, 3.0)
#: Round trip de taxa (entrada + saida), Binance USDT-M base tier.
ROUND_TRIP = 2 * DEFAULT_TAKER_FEE
#: EMA de referencia do projeto (`_BLOCK_RECLAIM_EMA_PERIOD`).
EMA_PERIOD = 9
#: "Perto da VWAP" em sigma, para o primeiro recorte de location.
NEAR_SIGMA = 0.5
#: Piso de amostra para publicar um recorte.
MIN_N = 150
#: Sanity checks obrigatorios (B20).
CASES = ("BTCUSDT", "ETHUSDT", "SOLUSDT")

FAMILIES = ("bos_retest", "choch_retest", "sweep_retest")
FAMILY_CODE = {name: code for code, name in enumerate(FAMILIES)}

TF_CODE = {tf: code for code, tf in enumerate(TIMEFRAMES)}
TF_BY_CODE = {code: tf for tf, code in TF_CODE.items()}

#: Segundos por candle, para a conta por dia.
TF_SECONDS = {
    TimeFrame.M15: 900,
    TimeFrame.H1: 3600,
    TimeFrame.H4: 14400,
    TimeFrame.D1: 86400,
}


# --------------------------------------------------------------------------
# o trade
# --------------------------------------------------------------------------


@dataclass
class Trade:
    """Um setup SMC com entrada, stop e alvo fixados NO candle de entrada.

    Tudo que esta aqui e legivel em `entry_index`. Os campos de desfecho sao
    preenchidos depois, por `simulate`, e nenhum deles pode voltar a decidir
    quem entra -- e por isso que a colecao e uma so e todo recorte desta etapa
    e uma leitura sobre ela, nunca uma varredura refeita.
    """

    family: str
    symbol: str
    timeframe: TimeFrame
    direction: MarketDirection
    event_index: int
    entry_index: int
    entry: float
    stop: float
    r: float
    ts: int
    test_ordinal: int
    #: contexto SMC, sem VWAP -- os controles da secao 2
    r_atr: float
    pullback_atr: float
    retrace_pct: float
    swing_dist_atr: float
    penetration_atr: float
    #: --- EMITIDOS, NUNCA FILTRAM. Acrescentados para a Etapa 7.1; nenhum
    #: deles toca entrada, stop, alvo, horizonte ou custo, entao a populacao
    #: desta etapa continua sendo byte a byte a mesma que a 7.0 mediu.
    #:
    #: O impulso que o pullback esta corrigindo, em ATR: |extremo - nivel|.
    #: Com ele a identidade `retrace_pct = 100 * pullback_atr / impulse_atr`
    #: fica visivel (e testavel), o que evita medir a mesma coisa duas vezes
    #: com dois nomes.
    impulse_atr: float = float("nan")
    #: A perna estrutural inteira, do `origin_price_level` do evento ate o
    #: extremo. `nan` quando o detector nao publicou origem.
    leg_atr: float = float("nan")
    #: `MarketStructure.reference_structural`: a referencia rompida era um
    #: nivel estrutural (True) ou fraco (False). `nan` fora do CHoCH, que e o
    #: unico evento que classifica a sua referencia.
    reference_structural: float = float("nan")
    #: contexto VWAP, causal
    vwap_side: float = float("nan")
    vwap_dist_atr: float = float("nan")
    vwap_dist_sigma: float = float("nan")
    vwap_phase: float = float("nan")
    vwap_slope_atr: float = float("nan")
    vwap_width: float = float("nan")
    vwap_crossed_since: float = float("nan")
    vwap_touches_since: float = float("nan")
    vwap_first_touch: float = float("nan")
    vwap_time_near: float = float("nan")
    vwap_reclaim: float = float("nan")
    vwap_reject: float = float("nan")
    vwap_between_stop: float = float("nan")
    vwap_between_target: float = float("nan")
    #: EMA(9), o controle C
    ema_dist_atr: float = float("nan")
    ema_side: float = float("nan")
    #: desfecho
    hit: dict[float, float] = field(default_factory=dict)
    r_grid: dict[float, float] = field(default_factory=dict)
    mfe_r: float = float("nan")
    mae_r: float = float("nan")
    time_to_1r: float = float("nan")
    time_to_2r: float = float("nan")
    stopped_first: float = float("nan")

    @property
    def r_pct(self) -> float:
        return self.r / self.entry


def simulate(trade: Trade, candles: Sequence[Candle]) -> None:
    """Alvo kR, stop 1R, stop checado primeiro, marcado a mercado no horizonte.

    A regra exata de `research/vwap_ob_pinbar.py::_measure`, e nao uma variante
    dela: dentro de um candle nao da para saber a ordem dos toques, entao o
    lado adverso leva o credito. Um estudo que resolvesse o empate a favor do
    alvo produziria uma taxa de acerto que nenhuma execucao reproduz.
    """
    bull = trade.direction is BULL
    i0 = trade.entry_index
    window = candles[i0 + 1 : i0 + 1 + TARGET_HORIZON]
    if not window:
        return
    # MFE/MAE ate a RESOLUCAO do trade (stop, alvo mais distante, ou fim do
    # horizonte), e nao ate o fim do horizonte sempre. Um trade estopado no
    # terceiro candle nao "sofreu" a excursao dos 117 seguintes: media-la ali
    # produz MAE de centenas de R, que nao descreve nenhuma posicao real.
    resolved = len(window)
    for step, candle in enumerate(window, start=1):
        stopped = candle.low <= trade.stop if bull else candle.high >= trade.stop
        far = trade.entry + TARGETS[-1] * trade.r * (1 if bull else -1)
        reached = candle.high >= far if bull else candle.low <= far
        if stopped or reached:
            resolved = step
            break
    walked = window[:resolved]
    high = max(c.high for c in walked)
    low = min(c.low for c in walked)
    trade.mfe_r = (high - trade.entry) / trade.r if bull else (trade.entry - low) / trade.r
    trade.mae_r = (trade.entry - low) / trade.r if bull else (high - trade.entry) / trade.r

    for k in TARGETS:
        target = trade.entry + k * trade.r if bull else trade.entry - k * trade.r
        payoff = None
        hit = 0.0
        for step, candle in enumerate(window, start=1):
            stopped = candle.low <= trade.stop if bull else candle.high >= trade.stop
            reached = candle.high >= target if bull else candle.low <= target
            if stopped:
                payoff = -1.0
                break
            if reached:
                payoff = k
                hit = 1.0
                if k == 1.0:
                    trade.time_to_1r = float(step)
                if k == 2.0:
                    trade.time_to_2r = float(step)
                break
        if payoff is None:
            move = window[-1].close - trade.entry
            payoff = (move if bull else -move) / trade.r
        trade.hit[k] = hit
        trade.r_grid[k] = payoff
    trade.stopped_first = 1.0 if trade.r_grid[TARGETS[-1]] == -1.0 else 0.0


def net_r(trade: Trade, target: float = 2.0) -> float:
    """R liquido: o bruto menos o round trip convertido para R.

    `custo / r_pct` e a conta de `research/vwap_exit_grid.py`. Um stop apertado
    e mais barato em preco e mais caro em R, e essa e exatamente a tensao que a
    Etapa 7 precisa nao esconder: "entrar perto da VWAP" costuma apertar o
    stop, e um stop apertado paga mais taxa por unidade de risco.
    """
    return trade.r_grid.get(target, 0.0) - ROUND_TRIP / trade.r_pct


# --------------------------------------------------------------------------
# a populacao: setups SMC, sem VWAP nenhuma
# --------------------------------------------------------------------------


def leg_extreme(
    candles: Sequence[Candle], start: int, end: int, direction: MarketDirection
) -> tuple[int, float]:
    """O extremo da perna entre `start` e `end`, na direcao dela."""
    if direction is BULL:
        index = max(range(start, end + 1), key=lambda i: candles[i].high)
        return index, candles[index].high
    index = min(range(start, end + 1), key=lambda i: candles[i].low)
    return index, candles[index].low


def last_swing(
    events: Sequence[MarketStructure],
    by_ts: dict[Any, int],
    index: int,
    direction: MarketDirection,
) -> float | None:
    """O ultimo pivo confirmado antes de `index`, do lado que o trade defende.

    Confirmado quer dizer: o detector ja o emitiu num candle <= `index`. Um
    pivo "que so se sabe depois" nao existe para quem esta decidindo agora.
    """
    wanted = (
        (StructureEvent.HIGHER_LOW, StructureEvent.LOWER_LOW)
        if direction is BULL
        else (StructureEvent.HIGHER_HIGH, StructureEvent.LOWER_HIGH)
    )
    best: tuple[int, float] | None = None
    for event in events:
        if event.provisional or event.event not in wanted:
            continue
        at = by_ts.get(event.timestamp)
        if at is None or at > index:
            continue
        if best is None or at > best[0]:
            best = (at, event.price_level)
    return None if best is None else best[1]


def structure_retests(
    candles: Sequence[Candle],
    index: int,
    level: float,
    direction: MarketDirection,
) -> list[int]:
    """Os candles que voltam ao nivel rompido e o defendem, em ordem.

    Um reteste e o pavio atravessar o nivel e o FECHAMENTO voltar para o lado
    do evento. A varredura para no primeiro fechamento do lado errado: ali o
    rompimento deixou de valer, e o que vier depois nao e mais reteste deste
    evento -- e outro contexto usando o mesmo preco.
    """
    bull = direction is BULL
    out: list[int] = []
    for at in range(index + 1, min(index + 1 + RETEST_WINDOW, len(candles))):
        candle = candles[at]
        if (candle.close < level) if bull else (candle.close > level):
            break
        touched = (candle.low <= level) if bull else (candle.high >= level)
        if touched:
            out.append(at)
            if len(out) >= MAX_TESTS:
                break
    return out


def build_structure_trades(
    symbol: str,
    timeframe: TimeFrame,
    candles: Sequence[Candle],
    events: Sequence[MarketStructure],
    by_ts: dict[Any, int],
    atr: Sequence[float],
    kind: StructureEvent,
    family: str,
) -> list[Trade]:
    """Familias A e B: reteste do nivel rompido por um BOS ou por um CHoCH."""
    out: list[Trade] = []
    for event in events:
        if event.provisional or event.event is not kind:
            continue
        index = by_ts.get(event.timestamp)
        if index is None or index < ATR_WINDOW or atr[index] <= 0:
            continue
        direction = event.direction
        # `reference_price_level` e o nivel que o evento ROMPEU; `price_level`
        # e o extremo novo que a perna alcancou (a outra ponta da linha
        # desenhada). Um reteste e do nivel rompido -- confundir os dois faz o
        # scanner procurar o preco voltar a um lugar onde ele nunca esteve, e
        # devolver zero setups sem erro nenhum.
        level = event.reference_price_level or event.price_level
        bull = direction is BULL
        for ordinal, at in enumerate(structure_retests(candles, index, level, direction), 1):
            if at + TARGET_HORIZON >= len(candles) or atr[at] <= 0:
                continue
            # O extremo da perna e lido de `index` ate o CANDLE DE ENTRADA, e
            # nunca ate o fim da janela de reteste. Ler o extremo da janela
            # inteira poria candles POSTERIORES a entrada dentro de
            # `pullback_atr` / `retrace_pct` / `leg_atr` -- lookahead, e do
            # tipo que melhora o resultado em vez de quebrar alguma coisa.
            _origin, extreme = leg_extreme(candles, index, at, direction)
            entry = candles[at].close
            stop = candles[at].low if bull else candles[at].high
            r = (entry - stop) if bull else (stop - entry)
            if r <= 0:
                continue
            unit = atr[at] * entry
            if unit <= 0:
                continue
            span = abs(extreme - level)
            depth = (extreme - entry) if bull else (entry - extreme)
            penetration = (level - stop) if bull else (stop - level)
            swing = last_swing(events, by_ts, at, direction)
            leg = (
                float("nan")
                if event.origin_price_level is None
                else abs(extreme - event.origin_price_level) / unit
            )
            out.append(
                Trade(
                    family=family,
                    symbol=symbol,
                    timeframe=timeframe,
                    direction=direction,
                    event_index=index,
                    entry_index=at,
                    entry=entry,
                    stop=stop,
                    r=r,
                    ts=int(candles[at].timestamp.timestamp()),
                    test_ordinal=ordinal,
                    r_atr=r / unit,
                    pullback_atr=depth / unit,
                    retrace_pct=100.0 * depth / span if span > 0 else float("nan"),
                    swing_dist_atr=(
                        float("nan")
                        if swing is None
                        else ((entry - swing) if bull else (swing - entry)) / unit
                    ),
                    penetration_atr=penetration / unit,
                    impulse_atr=span / unit if span > 0 else float("nan"),
                    leg_atr=leg,
                    reference_structural=(
                        float("nan")
                        if event.reference_structural is None
                        else float(event.reference_structural)
                    ),
                )
            )
    return out


def build_sweep_trades(
    symbol: str,
    timeframe: TimeFrame,
    candles: Sequence[Candle],
    events: Sequence[MarketStructure],
    by_ts: dict[Any, int],
    atr: Sequence[float],
    trends: Sequence[MarketDirection | None],
    internal_candles: Sequence[Candle],
) -> list[Trade]:
    """Familia C: varrida rejeitada, alinhada com a estrutura confirmada.

    Reusa a composicao de producao (`build_liquidity_grabs`) sobre os mesmos
    detectores que `load_dashboard_data` monta -- inventar aqui uma definicao
    paralela de varrida seria criar um segundo conceito com o mesmo nome.

    `rejection_confirmed` existe no dominio e **nao e usado**: ele olha os dois
    candles seguintes, e a entrada e no proprio candle da rejeicao. Seria
    lookahead com aparencia de rigor.
    """
    zones = mark_swept_zones(
        [
            *SwingHighDetector().detect(list(candles)),
            *SwingLowDetector().detect(list(candles)),
            *dd._equal_high_detector().detect(list(candles)),
            *dd._equal_low_detector().detect(list(candles)),
        ],
        list(candles),
    )
    poi = [
        zone
        for zone in POIDetector().detect(list(internal_candles))
        if candles[0].timestamp <= zone.created_at <= candles[-1].timestamp
    ]
    grabs = build_liquidity_grabs(
        symbol=symbol,
        timeframe=timeframe,
        liquidity_zones=zones,
        poi_zones=poi,
        candles=list(candles),
    )
    out: list[Trade] = []
    for grab in grabs:
        if grab.outcome is not LiquidityGrabOutcome.REJECTED:
            continue
        at = by_ts.get(grab.timestamp)
        if at is None or at < ATR_WINDOW or at + TARGET_HORIZON >= len(candles):
            continue
        # Uma varrida de liquidez ACIMA (buy-side) e combustivel para uma perna
        # de baixa: quem foi estopado ali vendeu. A entrada so existe quando a
        # estrutura confirmada ja aponta para o mesmo lado -- e a exigencia de
        # "structure alignment" da familia, e o que a separa de um contra-trend.
        direction = BEAR if grab.side is LiquiditySide.BUY_SIDE else BULL
        if trends[at] is not direction:
            continue
        bull = direction is BULL
        entry = candles[at].close
        stop = candles[at].high if not bull else candles[at].low
        r = (entry - stop) if bull else (stop - entry)
        if r <= 0 or atr[at] <= 0:
            continue
        unit = atr[at] * entry
        if unit <= 0:
            continue
        swing = last_swing(events, by_ts, at, direction)
        level = grab.price_level
        out.append(
            Trade(
                family="sweep_retest",
                symbol=symbol,
                timeframe=timeframe,
                direction=direction,
                event_index=at,
                entry_index=at,
                entry=entry,
                stop=stop,
                r=r,
                ts=int(candles[at].timestamp.timestamp()),
                test_ordinal=1,
                r_atr=r / unit,
                pullback_atr=abs(entry - level) / unit,
                retrace_pct=float("nan"),
                swing_dist_atr=(
                    float("nan")
                    if swing is None
                    else ((entry - swing) if bull else (swing - entry)) / unit
                ),
                penetration_atr=(
                    (level - stop) if bull else (stop - level)
                ) / unit,
                impulse_atr=abs(entry - level) / unit,
            )
        )
    return out


# --------------------------------------------------------------------------
# as features de VWAP, todas lidas no candle de entrada
# --------------------------------------------------------------------------


def tag_vwap(
    trade: Trade,
    candles: Sequence[Candle],
    atr: Sequence[float],
    geometry: Any,
    phase: Sequence[float | None],
    ema: Sequence[float | None],
) -> None:
    """Location e qualidade de reteste no candle de entrada.

    Todo campo aqui e funcao de candles ate `entry_index` inclusive. As
    contagens "since" varrem do evento ate a entrada, nunca alem -- e por isso
    `vwap_between_stop` / `vwap_between_target` sao geometria do trade, nao
    desfecho: eles perguntam onde a linha ESTA em relacao a niveis que acabaram
    de ser fixados, e nao se o preco chegou neles.
    """
    at = trade.entry_index
    mid = geometry.mid[at]
    span = geometry.span[at]
    unit = atr[at] * trade.entry
    if unit <= 0:
        return
    bull = trade.direction is BULL
    sign = 1 if bull else -1

    value = ema[at]
    if value is not None:
        trade.ema_dist_atr = sign * (trade.entry - value) / unit
        trade.ema_side = 1.0 if (trade.entry >= value) == bull else 0.0

    if mid is None or span is None or span <= 0:
        return
    trade.vwap_side = 1.0 if (trade.entry >= mid) == bull else 0.0
    trade.vwap_dist_atr = sign * (trade.entry - mid) / unit
    trade.vwap_dist_sigma = sign * (trade.entry - mid) / span
    trade.vwap_width = span / abs(mid)
    if phase[at] is not None:
        trade.vwap_phase = sign * phase[at]
    lag = 5
    before = geometry.mid[at - lag] if at >= lag else None
    if before is not None:
        trade.vwap_slope_atr = sign * (mid - before) / (unit * lag)

    # A VWAP esta ENTRE a entrada e o stop? E entre a entrada e o alvo de 2R?
    target = trade.entry + 2 * trade.r * sign
    lo, hi = sorted((trade.entry, trade.stop))
    trade.vwap_between_stop = 1.0 if lo <= mid <= hi else 0.0
    lo, hi = sorted((trade.entry, target))
    trade.vwap_between_target = 1.0 if lo <= mid <= hi else 0.0

    # Historia entre o evento e a entrada.
    crosses = 0
    touches = 0
    near = 0
    previous: int | None = None
    for j in range(trade.event_index, at + 1):
        line = geometry.mid[j]
        line_span = geometry.span[j]
        if line is None or line_span is None or line_span <= 0:
            continue
        side = 1 if candles[j].close >= line else -1
        if previous is not None and side != previous:
            crosses += 1
        previous = side
        if candles[j].low <= line <= candles[j].high:
            touches += 1
        if abs(candles[j].close - line) <= NEAR_SIGMA * line_span:
            near += 1
    horizon = at - trade.event_index + 1
    trade.vwap_crossed_since = float(crosses)
    trade.vwap_touches_since = float(touches)
    trade.vwap_first_touch = 1.0 if touches <= 1 else 0.0
    trade.vwap_time_near = near / horizon if horizon > 0 else float("nan")

    # Reclaim / rejeicao no proprio candle de entrada: o pavio atravessou a
    # linha e o fechamento voltou para o lado do trade.
    candle = candles[at]
    pierced = (candle.low <= mid) if bull else (candle.high >= mid)
    closed_back = (candle.close > mid) if bull else (candle.close < mid)
    trade.vwap_reclaim = 1.0 if pierced and closed_back else 0.0
    trade.vwap_reject = 1.0 if (candle.low <= mid <= candle.high) and closed_back else 0.0


# --------------------------------------------------------------------------
# recortes, e as duas contas
# --------------------------------------------------------------------------

VWAP_CUTS: dict[str, Any] = {
    "perto da VWAP (<=0.5s)": lambda t: abs(t.vwap_dist_sigma) <= NEAR_SIGMA,
    "dentro de +/-1s": lambda t: abs(t.vwap_dist_sigma) <= 1.0,
    "esticado (>1s)": lambda t: t.vwap_dist_sigma > 1.0,
    "abaixo do valor (<0)": lambda t: t.vwap_dist_sigma < 0.0,
    "first touch": lambda t: t.vwap_first_touch > 0,
    "slope alinhado": lambda t: t.vwap_slope_atr > 0,
    "reclaim na entrada": lambda t: t.vwap_reclaim > 0,
    "VWAP entre entry e stop": lambda t: t.vwap_between_stop > 0,
    "VWAP entre entry e alvo": lambda t: t.vwap_between_target > 0,
}

CONTROL_CUTS: dict[str, Any] = {
    "pullback fundo (>=med)": None,  # resolvido por mediana, na hora
    "1o teste estrutural": lambda t: t.test_ordinal == 1,
    "revisita estrutural": lambda t: t.test_ordinal > 1,
    "EMA9 do lado": lambda t: t.ema_side > 0,
    "EMA9 perto (<=0.5 ATR)": lambda t: abs(t.ema_dist_atr) <= 0.5,
}

FEATURES = (
    "vwap_dist_sigma",
    "vwap_dist_atr",
    "vwap_phase",
    "vwap_slope_atr",
    "vwap_width",
    "vwap_crossed_since",
    "vwap_touches_since",
    "vwap_time_near",
    "pullback_atr",
    "retrace_pct",
    "swing_dist_atr",
    "ema_dist_atr",
    "r_atr",
    "penetration_atr",
)


def span_days(trades: Sequence[Trade]) -> float:
    """Dias-simbolo cobertos: a base honesta do `per_day`.

    Somar o calendario global daria o mesmo denominador para uma amostra de um
    simbolo e para uma de setenta -- e a conta por dia deixaria de significar
    "oportunidade". Aqui cada par (simbolo, timeframe) contribui o seu proprio
    intervalo coberto.
    """
    by_combo: dict[tuple[str, str], list[int]] = {}
    for trade in trades:
        by_combo.setdefault((trade.symbol, trade.timeframe.value), []).append(trade.ts)
    total = 0.0
    for stamps in by_combo.values():
        if len(stamps) < 2:
            continue
        total += (max(stamps) - min(stamps)) / 86400.0
    return total


def summarize(trades: Sequence[Trade], pool: Sequence[Trade], target: float = 2.0) -> dict:
    """As duas contas, sempre juntas, e sempre com a frequencia ao lado."""
    if not trades:
        return {"n": 0}
    gross = [t.r_grid.get(target, 0.0) for t in trades]
    net = [net_r(t, target) for t in trades]
    days = span_days(trades)
    pool_days = span_days(pool) if pool else days
    out: dict[str, Any] = {
        "n": len(trades),
        "coverage": len(trades) / len(pool) if pool else 1.0,
        "hit1": statistics.fmean(t.hit.get(1.0, 0.0) for t in trades),
        "hit2": statistics.fmean(t.hit.get(2.0, 0.0) for t in trades),
        "hit3": statistics.fmean(t.hit.get(3.0, 0.0) for t in trades),
        "stopped_first": statistics.fmean(t.stopped_first for t in trades),
        "gross_r": statistics.fmean(gross),
        "net_r": statistics.fmean(net),
        "median_r": quantile(sorted(gross), 0.5),
        "mfe": statistics.fmean(t.mfe_r for t in trades if not math.isnan(t.mfe_r)),
        "mae": statistics.fmean(t.mae_r for t in trades if not math.isnan(t.mae_r)),
        "r_atr": statistics.fmean(t.r_atr for t in trades),
        "days": days,
        "trades_per_day": len(trades) / days if days > 0 else None,
        # A conta que decide: R liquido TOTAL dividido pelos dias-simbolo do
        # POOL, nao pelos do recorte. Um filtro que corta trades nao ganha um
        # denominador menor de presente.
        "net_per_day": sum(net) / pool_days if pool_days > 0 else None,
    }
    times = [t.time_to_2r for t in trades if not math.isnan(t.time_to_2r)]
    out["time_to_2r"] = quantile(sorted(times), 0.5) if len(times) >= 10 else None
    return out


def profit_factor(trades: Sequence[Trade], target: float = 2.0) -> float | None:
    wins = sum(r for t in trades if (r := net_r(t, target)) > 0)
    losses = -sum(r for t in trades if (r := net_r(t, target)) < 0)
    return wins / losses if losses > 0 else None


def feature_auc(trades: Sequence[Trade], name: str, target: float = 2.0) -> dict:
    """AUC da feature contra "bateu 2R antes do stop"."""
    values = []
    labels = []
    for trade in trades:
        raw = getattr(trade, name)
        if math.isnan(raw):
            continue
        values.append(raw)
        labels.append(int(trade.hit.get(target, 0.0) > 0))
    got = auc(values, labels)
    return {} if got is None else {"auc": got[0], "n": got[1]}


# --------------------------------------------------------------------------
# coleta
# --------------------------------------------------------------------------


def collect_combo(
    symbol: str,
    timeframe: TimeFrame,
    series: Sequence[Candle],
    end: int,
) -> list[Trade]:
    """Uma janela de producao inteira, com as tres familias."""
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
    ema = ema_series(candles, EMA_PERIOD)

    trades = build_structure_trades(
        symbol, timeframe, candles, run.events, by_ts, atr,
        StructureEvent.BREAK_OF_STRUCTURE, "bos_retest",
    )
    trades += build_structure_trades(
        symbol, timeframe, candles, run.events, by_ts, atr,
        StructureEvent.CHANGE_OF_CHARACTER, "choch_retest",
    )
    try:
        trades += build_sweep_trades(
            symbol, timeframe, candles, run.events, by_ts, atr, trends,
            run.internal_candles,
        )
    except Exception as error:  # noqa: BLE001 - feed morto na camada de pools
        print(f"! sweep indisponivel: {symbol} {timeframe.value}: {error}")

    for trade in trades:
        tag_vwap(trade, candles, atr, geometry, phase, ema)
        simulate(trade, candles)
    # Um trade sem simulacao (borda da janela) nao entra: manter a linha com
    # desfecho vazio contaminaria toda media com um zero que nao e um trade.
    return [t for t in trades if t.r_grid]


def cut_by_tf(trades: Sequence[Trade], timeframe: TimeFrame) -> int:
    """O corte 70/30 por timeframe, em timestamp (secao B19)."""
    stamps = sorted(t.ts for t in trades if t.timeframe is timeframe)
    if not stamps:
        return 0
    return stamps[int(len(stamps) * DISCOVERY_FRACTION)]


def cuts_of(trades: Sequence[Trade]) -> dict[TimeFrame, int]:
    return {tf: cut_by_tf(trades, tf) for tf in TIMEFRAMES}


def sample_of(trade: Trade, cuts: dict[TimeFrame, int]) -> str:
    return "discovery" if trade.ts < cuts[trade.timeframe] else "holdout"


def cut_table(pool: Sequence[Trade], cuts: dict[str, Any]) -> dict[str, Any]:
    """Cada recorte contra o pool, com as duas contas e a AUC ao lado."""
    out: dict[str, Any] = {}
    for label, predicate in cuts.items():
        if predicate is None:
            continue
        subset = [t for t in pool if predicate(t)]
        if len(subset) < MIN_N:
            out[label] = {"n": len(subset)}
            continue
        entry = summarize(subset, pool)
        entry["profit_factor"] = profit_factor(subset)
        out[label] = entry
    return out


def build(symbols: Sequence[str], windows: int) -> dict[str, Any]:
    trades: list[Trade] = []
    combos = 0
    for position, symbol in enumerate(symbols, 1):
        print(f"[{position}/{len(symbols)}] {symbol}  trades={len(trades)}", flush=True)
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
                    trades += collect_combo(symbol, timeframe, series, end)
                except Exception as error:  # noqa: BLE001 - feed morto
                    print(f"! janela invalida: {symbol} {timeframe.value} w{window}: {error}")
                    continue
                combos += 1

    print(f"combos medidos: {combos}  trades: {len(trades)}")
    cuts = cuts_of(trades)
    report: dict[str, Any] = {
        "combos": combos,
        "trades": len(trades),
        "round_trip": ROUND_TRIP,
        "target_horizon": TARGET_HORIZON,
        "families": {},
        "rules_tested": 0,
    }

    for family in FAMILIES:
        pool = [t for t in trades if t.family == family]
        if not pool:
            continue
        block: dict[str, Any] = {"baseline": summarize(pool, pool)}
        block["baseline"]["profit_factor"] = profit_factor(pool)
        block["by_tf"] = {}
        for timeframe in TIMEFRAMES:
            sub = [t for t in pool if t.timeframe is timeframe]
            if sub:
                block["by_tf"][timeframe.value] = summarize(sub, sub)
        block["by_direction"] = {}
        for label, direction in (("long", BULL), ("short", BEAR)):
            sub = [t for t in pool if t.direction is direction]
            if sub:
                block["by_direction"][label] = summarize(sub, sub)
        report["families"][family] = block

    # ---- o setup principal (B7): BOS continuation retest -------------------
    main_pool = [t for t in trades if t.family == "bos_retest"]
    focus: dict[str, Any] = {}
    for timeframe in TIMEFRAMES:
        pool = [t for t in main_pool if t.timeframe is timeframe]
        if len(pool) < MIN_N:
            continue
        entry: dict[str, Any] = {"baseline": summarize(pool, pool)}
        entry["baseline"]["profit_factor"] = profit_factor(pool)
        entry["vwap_cuts"] = cut_table(pool, VWAP_CUTS)
        entry["control_cuts"] = cut_table(pool, CONTROL_CUTS)
        # o controle por mediana de pullback, resolvido dentro do timeframe
        depths = sorted(t.pullback_atr for t in pool if not math.isnan(t.pullback_atr))
        if depths:
            median = quantile(depths, 0.5)
            deep = [t for t in pool if t.pullback_atr >= median]
            if len(deep) >= MIN_N:
                got = summarize(deep, pool)
                got["profit_factor"] = profit_factor(deep)
                entry["control_cuts"][f"pullback >= {median:.2f} ATR"] = got
        entry["auc"] = {name: feature_auc(pool, name) for name in FEATURES}
        focus[timeframe.value] = entry
    report["focus_bos_retest"] = focus

    # ---- ablacao (B14): cada componente sozinho, e as combinacoes -----------
    ablation: dict[str, Any] = {}
    combos_to_test: dict[str, Any] = {
        "SMC puro": lambda t: True,
        "SMC + VWAP distance": lambda t: abs(t.vwap_dist_sigma) <= 1.0,
        "SMC + VWAP phase": lambda t: t.vwap_phase <= 50.0,
        "SMC + VWAP slope": lambda t: t.vwap_slope_atr > 0,
        "SMC + VWAP first touch": lambda t: t.vwap_first_touch > 0,
        "SMC + VWAP rejection": lambda t: t.vwap_reject > 0,
        "SMC + EMA9": lambda t: t.ema_side > 0,
        "SMC + VWAP + EMA9": lambda t: abs(t.vwap_dist_sigma) <= 1.0 and t.ema_side > 0,
    }
    for label, predicate in combos_to_test.items():
        subset = [t for t in main_pool if predicate(t)]
        if len(subset) < MIN_N:
            ablation[label] = {"n": len(subset)}
            continue
        got = summarize(subset, main_pool)
        got["profit_factor"] = profit_factor(subset)
        ablation[label] = got
    report["ablation"] = ablation

    # ---- a regra: escolhida so no discovery, congelada, e so entao holdout --
    grid: list[dict[str, Any]] = []
    for label, predicate in {**VWAP_CUTS, **combos_to_test}.items():
        if label == "SMC puro":
            continue
        discovery = [t for t in main_pool if sample_of(t, cuts) == "discovery"]
        fired = [t for t in discovery if predicate(t)]
        if len(fired) < MIN_N:
            continue
        base = summarize(discovery, discovery)
        got = summarize(fired, discovery)
        grid.append({
            "label": label,
            "coverage": got["coverage"],
            "net_r": got["net_r"],
            "base_net_r": base["net_r"],
            "delta_per_trade": got["net_r"] - base["net_r"],
            "net_per_day": got["net_per_day"],
            "base_net_per_day": base["net_per_day"],
            "delta_per_day": got["net_per_day"] - base["net_per_day"],
        })
    report["rules_tested"] = len(grid)
    report["grid"] = grid
    # O criterio de escolha e POR DIA, e nao por trade: a memoria deste
    # repositorio ja registra um achado que subia por trade e empatava por dia.
    scored = [g for g in grid if g["coverage"] >= 0.05]
    chosen = max(scored, key=lambda g: g["delta_per_day"]) if scored else None
    report["chosen"] = chosen
    if chosen is not None:
        predicate = {**VWAP_CUTS, **combos_to_test}[chosen["label"]]
        holdout = [t for t in main_pool if sample_of(t, cuts) == "holdout"]
        fired = [t for t in holdout if predicate(t)]
        base = summarize(holdout, holdout)
        got = summarize(fired, holdout) if len(fired) >= 30 else {"n": len(fired)}
        report["holdout"] = {
            "base": base,
            "rule": got,
            "delta_per_trade": (
                got["net_r"] - base["net_r"] if got.get("n", 0) >= 30 else None
            ),
            "delta_per_day": (
                got["net_per_day"] - base["net_per_day"] if got.get("n", 0) >= 30 else None
            ),
        }
    return report


# --------------------------------------------------------------------------
# saida
# --------------------------------------------------------------------------


def num(value: float | None, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "  --  "
    return f"{value:.{digits}f}"


def signed(value: float | None, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "  --   "
    return f"{value:+.{digits}f}"


def pct(value: float | None) -> str:
    return "  --  " if value is None else f"{100 * value:5.1f}%"


def line(label: str, got: dict[str, Any]) -> str:
    if got.get("n", 0) == 0 or "net_r" not in got:
        return f"  {label:<28} n={got.get('n', 0):>5}  (abaixo do piso)"
    return (
        f"  {label:<28} n={got['n']:>5} cov {pct(got.get('coverage'))} "
        f"hit2 {pct(got['hit2'])} bruto {num(got['gross_r'], 3):>7} "
        f"liq {num(got['net_r'], 3):>7} /dia {num(got.get('net_per_day'), 3):>7} "
        f"MFE {num(got['mfe'], 2)} MAE {num(got['mae'], 2)} r_atr {num(got['r_atr'], 2)}"
    )


def print_report(report: dict[str, Any]) -> None:
    print()
    print("=" * 110)
    print("ETAPA 7.0 -- SMC x VWAP ENTRY / RETEST QUALITY")
    print("=" * 110)
    print(f"combos {report['combos']}  trades {report['trades']}  "
          f"round trip {report['round_trip']:.4f}  horizonte {report['target_horizon']}")

    for family, block in report["families"].items():
        print()
        print(f"--- familia {family} ---")
        print(line("baseline SMC puro", block["baseline"]))
        for tf, got in block["by_tf"].items():
            print(line(f"  {tf}", got))
        for label, got in block["by_direction"].items():
            print(line(f"  {label}", got))

    for tf, entry in report.get("focus_bos_retest", {}).items():
        print()
        print(f"=== BOS continuation retest / {tf} ===")
        print(line("BASELINE", entry["baseline"]))
        print("  -- recortes de VWAP --")
        for label, got in entry["vwap_cuts"].items():
            print(line(label, got))
        print("  -- controles sem VWAP --")
        for label, got in entry["control_cuts"].items():
            print(line(label, got))
        print("  -- AUC contra 'bateu 2R' --")
        cells = [
            f"{name} {num(got.get('auc'))}"
            for name, got in entry["auc"].items()
            if got.get("auc") is not None
        ]
        for start in range(0, len(cells), 4):
            print("     " + "  ".join(f"{c:<26}" for c in cells[start : start + 4]))

    print()
    print("--- ablacao (B14) ---")
    for label, got in report.get("ablation", {}).items():
        print(line(label, got))

    print()
    print(f"--- regra ({report['rules_tested']} testadas, escolhida so no discovery) ---")
    for got in report.get("grid", []):
        print(f"  {got['label']:<28} cov {pct(got['coverage'])} "
              f"liq/trade {num(got['net_r'], 3):>7} ({signed(got['delta_per_trade']):>8}) "
              f"liq/dia {num(got['net_per_day'], 3):>7} ({signed(got['delta_per_day']):>8})")
    chosen = report.get("chosen")
    if chosen is None:
        print("  nenhuma regra passou o piso.")
        return
    print(f"\n  ESCOLHIDA (por dia): {chosen['label']}")
    hold = report.get("holdout")
    if hold:
        print(line("  holdout baseline", hold["base"]))
        print(line("  holdout regra", hold["rule"]))
        print(f"    delta por trade {num(hold['delta_per_trade'], 3)}  "
              f"delta por dia {num(hold['delta_per_day'], 3)}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--expanded", action="store_true")
    parser.add_argument("--windows", type=int, default=WINDOWS)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.symbols:
        symbols = list(args.symbols)
    elif args.expanded:
        from research.choch_leg_opener import expanded_symbols

        symbols = expanded_symbols()
    else:
        symbols = list(CASES)

    report = build(symbols, args.windows)
    print_report(report)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, default=str))
        print(f"\njson: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
