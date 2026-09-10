"""Surfar a tendencia da EMA9 + VWAP, com o pinbar como gatilho. Sem OB.

Setup NOVO, irmao do `deep_reclaim` e igualmente separado do caminho ao vivo:
nao le nem escreve nada de `app/block_reclaim.py`, `app/paper_journal.py` ou
`research/ftmo_*.py`. O que ele reaproveita e a maquina ja medida -- os tres
graus de pinbar, a calda de 65% no `legacy`, a cor exigida no `l2`, o controle
aleatorio casado em SIMBOLO e DIRECAO, os alvos/horizontes e o custo em R.

A leitura, na ordem em que aparece no grafico:

1. **A tendencia sao as duas linhas concordando.** Nao e a EMA9 sozinha nem a
   VWAP sozinha: e o preco fechando do mesmo lado das duas, com a EMA9
   inclinada a favor. `trend_age` conta ha quantas velas isso vale, e entra
   como exigencia MINIMA de duas velas -- se a primeira vela a alinhar e o
   proprio pinbar, nao havia tendencia para surfar, havia so o pinbar. E a
   mesma licao do `departure` no deep, que la custou uma rodada.

2. **O pullback e o preco voltando para as linhas.** Nao ha zona desenhada:
   a zona SAO as linhas. `pullback_candles` e `pullback_atr` medem quanto o
   preco recuou desde o extremo da perna, e `run_atr` mede quanto a perna ja
   correu antes disso -- "o trade ja foi" e uma coisa que se mede, nao se
   supoe.

3. **O gatilho e a sombra**, exigida (nao emitida): a minima do pinbar tem que
   FURAR a VWAP ou a EMA9 e o fechamento tem que voltar do lado bom das duas.
   Uma vela que so fecha acima nao testou nada. `wick_line` diz qual linha foi
   furada, e `ambas` -- as duas no mesmo candle -- e o caso forte da leitura;
   sai como campo para ser medido, nao para filtrar.

3b. **A sombra na linha do POC** (`footprint_poc.py`, 2026-09-06), exigida
   como as outras: a minima tem que FURAR o POC do perfil de 23 barras e o
   fechamento tem que voltar do lado bom dele. As duas metades do pedido
   ("sombra na linha" e "fechar acima dela") sao a mesma condicao escrita
   uma vez: `low <= poc <= close`. `--no-poc` mede o custo do corte.

   A linha e o POC *de grafico* do Volume Footprint no engine Geometric --
   uma mistura de sinos truncados, nao um histograma --, entao ela se mexe
   com o perfil e nao e um nivel fixo. `poc_dist_atr`, `poc_pos` (onde o POC
   caiu dentro da moldura do perfil) e `poc_lado` saem como campo.

   **O LADO do POC** (`buy_intensity > sell_intensity` para comprar, o azul
   contra o vermelho do grafico) foi medido e ficou DESLIGADO: ele ganha por
   operacao e perde por risco no tempo -- as duas contas estao certas e
   respondem perguntas diferentes; a que decide e a segunda. `--poc-side`
   liga. `poc_lado` continua saindo como campo.

3c. **A MEDIA do RSI** (a SMA de 14 sobre o RSI de 14, a linha amarela do
   painel do TradingView) foi implementada em `indicators/rsi.py`, medida
   como gate e DESLIGADA no mesmo dia -- ver `RSI_MA_LEVEL`. O campo
   `rsi_ma` continua saindo.

4. **A ORDEM das duas linhas, exigida** (`MIN_LINE_SEP`, ligado em
   2026-09-06): a EMA9 tem que estar do lado bom da VWAP. Linhas coladas nao
   sao confluencia, sao lateralizacao -- foi medido, e o texto que estava aqui
   dizia o contrario. A magnitude (`line_gap_atr`, `ema_side_atr`) e o quanto
   o fechamento limpou cada linha (`clear_vwap_atr`, `clear_ema_atr`)
   continuam saindo como campo, para o limiar poder ser medido sem ser
   escolhido na mesma tabela em que foi visto.

4b. **A EMA50 como apoio** -- medida e DESLIGADA em 2026-09-06 (ver
   `MIN_EMA50_STACK`). A leitura era "para comprar, a media de 50 por baixo,
   ou no cruzamento"; ela custava 20% do fluxo por zero de acerto. Os campos
   (`ema50_stack_atr`, `ema50_dist_atr`, `ema50_slope_lag1`) continuam saindo.

5. **O momento, emitido.** RSI(14) em nivel, defasagem e inclinacao. Aqui a
   hipotese e o OPOSTO da do deep: naquele setup o RSI foi medido e rejeitado
   porque os gates de contexto ja tinham comido a variancia do momento
   (85% das entradas entre 45 e 55). Este setup nao tem esses gates, entao a
   variancia deve existir -- e por isso o RSI vale ser perguntado de novo, num
   universo onde ele ainda pode responder. Continua sem filtrar.

6. **O tempo grafico de cima, sem uma segunda requisicao.** As velas ja
   baixadas sao reagrupadas localmente em baldes de `HTF_FACTOR` (H1 -> H4) e a
   EMA9 e recalculada la. `htf_side` e `htf_slope` saem como campo: a pergunta
   "so opero H1 a favor do H4" fica medivel sem dobrar o custo de rede, e sem
   comprar o alinhamento antes de saber se ele paga.

O stop tem cinco candidatos medidos na MESMA passada, sobre as mesmas entradas
(trocar o stop muda o R, e o R e o denominador do custo): `pinbar` (o extremo
do proprio gatilho -- o que venceu no deep), `pullback` (o extremo do recuo),
`look10`/`look20`, e `farline` (um quarto de ATR alem da linha mais distante).

Alvos 2R/2,5R/3R, horizontes 40 e 120 velas. Nada aqui esta ligado ao vivo.

Run:
    poetry run python -m research.trend_ride --timeframe 1h --out /tmp/tr_h1.json
    poetry run python -m research.trend_ride --report-only /tmp/tr_h1.json
"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Sequence
from pathlib import Path
from statistics import fmean

from liquidity_hunter.app.block_reclaim import (
    STRICT_WICK_FRACTION,
    pinbar_grades,
    surviving_grades,
)
from liquidity_hunter.app.dashboard_data import load_dashboard_data
from liquidity_hunter.core.domain import Candle, TimeFrame
from liquidity_hunter.data.exceptions import DataProviderError
from liquidity_hunter.indicators import ema, ema_series, rsi_ma_series, rsi_series
from liquidity_hunter.indicators.footprint_poc import footprint_poc_series
from pydantic import ValidationError
from research._paginated import NoFuturesProvider, PaginatedFuturesProvider
from research._symbols import UNIVERSE, sample_of
from research.deep_reclaim import COST_PCT, HORIZONS, outcome, wick_touch

ATR_PERIOD = 14
#: Alvos PROPRIOS, mais longos que os do `deep_reclaim` (2/2,5/3R). Este setup
#: nao e um reclaim num nivel: e uma perna de tendencia, e a pergunta "ate onde
#: ela vai" merece mais casas do que a de um teste de bloco. Os tres primeiros
#: sao os mesmos do outro estudo de proposito, para os dois serem comparaveis.
TARGETS = (2.0, 2.5, 3.0, 4.0, 5.0)
LOOKBACKS = (10, 20)
#: Quantas velas deste TF cabem no TF de cima. 4 e H1->H4 e M15->H1; o padrao
#: serve os dois porque a escada que interessa e sempre de um degrau.
HTF_FACTOR = 4
#: Piso de acumulacao da VWAP de sessao, herdado medido do outro estudo: a
#: VWAP reancora a meia-noite UTC e nas primeiras velas do dia ela cruza o
#: preco sozinha, sem nada ter acontecido.
MIN_VWAP_CANDLES = 4
#: Minimo de velas com as duas linhas alinhadas antes do gatilho.
MIN_TREND_AGE = 2
#: Piso de R em ATR, ligado em 2026-09-06. NAO e um eixo de leitura e nao foi
#: catado numa tabela: e aritmetica. O custo em R e `COST_PCT / r_pct`, entao
#: uma entrada cujo stop vale 0,06% do preco custa 1,7R de giro e nao tem como
#: dar certo nem indo para o lado certo. Apareceu nos trades perdedores do H1
#: (BTC 15/08/2026, R = 37 pontos em 63.077) e e a lacuna mais barata do
#: arquivo. No walk-forward do H4 e a melhor regra da grade (SR 1,21 contra
#: 0,53 da base), o que e esperado de um conserto e nao de um achado.
MIN_R_ATR = 0.5
#: A media do RSI (o `Smoothing: SMA / 14` do TradingView, a linha amarela do
#: painel) e o nivel que ela precisa respeitar: acima de 50 para comprar,
#: abaixo para vender. Leitura do usuario, 2026-09-06 -- e note que o filtro e
#: sobre a MEDIA, nao sobre o RSI. Sao perguntas diferentes: o RSI cru diz se o
#: momento esta esticado agora, a media dele diz de que lado o momento tem
#: estado, e um nivel como 50 so significa alguma coisa na segunda. O RSI cru
#: ja foi medido e rejeitado duas vezes neste projeto (no `deep_reclaim` e na
#: primeira grade daqui); a media dele nunca foi medida.
#: DESLIGADO em 2026-09-06, medido: sozinho, no H4, o gate baixava o ganho por
#: operacao em todos os alvos (3R +0,253 -> +0,215; 5R +0,349 -> +0,282) e
#: cortava 20% do fluxo. E a TERCEIRA rejeicao do RSI neste projeto -- cru no
#: `deep_reclaim`, cru na primeira grade daqui, e agora suavizado. Tres
#: desenhos, tres negativos. `--rsi-ma` liga de volta; `rsi_ma` continua
#: saindo como campo, e `indicators/rsi.py` guarda a serie.
RSI_MA_LEVEL = 50.0
#: Separacao minima entre as linhas, em ATR, com sinal a favor da operacao:
#: a EMA9 tem que estar do lado bom da VWAP. Ligado em 2026-09-06 depois da
#: grade H4 dos majors, e ligado em ZERO de proposito -- o corte e a ORDEM das
#: linhas, que e leitura de grafico, e nao uma distancia catada numa tabela.
#:
#: O mecanismo apareceu em tres recortes independentes do mesmo dado (H4,
#: BTC/ETH/SOL/BNB, alvo 3R): EMA9 do lado errado da VWAP dava 16,1% de acerto
#: e -74,4R; linhas coladas (gap < 0,3 ATR) 21,5% e -66,2R; pavio furando AS
#: DUAS 21,4% e -71,8R. Sao a mesma frase dita tres vezes: quando a VWAP e a
#: EMA9 estao em cima uma da outra nao ha perna para surfar, ha lateralizacao,
#: e o pinbar ali e ruido.
#:
#: Isso DERRUBOU duas suposicoes do desenho original deste arquivo, que ficam
#: registradas porque estavam escritas aqui como se fossem sabidas: linhas
#: coladas foram chamadas de "confluencia de verdade" e o pavio furando as duas
#: de "o caso forte da leitura". As duas medem o contrario. O caso forte e o
#: preco voltando para a EMA9 com a VWAP atras: a EMA9 e o retorno a media da
#: perna, a VWAP e o piso do controle.
#:
#: `--min-line-sep` mede a MAGNITUDE (o balde >= 0,5 ATR media melhor), mas ela
#: fica desligada: um limiar escolhido na mesma tabela em que foi visto, sobre
#: 4 simbolos da metade de busca com 74 dos 93R vindo de um unico ano, e o
#: caminho que ja matou quatro achados neste projeto.
MIN_LINE_SEP = 0.0
#: A EMA50 como APOIO da perna (leitura do usuario, 2026-09-06): para comprar,
#: a media de 50 tem que estar POR BAIXO -- ou no cruzamento, que e o zero
#: daqui. Como o corte das linhas acima, o gate e a ORDEM (`>= 0`), nao uma
#: distancia: `sign * (EMA9 - EMA50) >= MIN_EMA50_STACK`.
#:
#: A outra metade da leitura -- "se estiver muito longe tambem e ruim, o preco
#: ja esticou" -- NAO vira gate, vira campo (`ema50_dist_atr`). Ela e um
#: limiar, e limiar escolhido na mesma tabela em que foi visto e o que ja
#: matou quatro achados neste projeto; a curva sai no relatorio por faixa para
#: o teto poder ser escolhido com o numero na frente, em outra amostra.
#: DESLIGADO em 2026-09-06 a pedido do leitor, e o numero concordava: o gate
#: custava 15-26% do fluxo sem mover o acerto em nenhum TF (+-0,5pp, ruido) e
#: PIORAVA o unico terreno positivo (H4, +0,070 -> +0,050 no 3R). O que ele
#: exigia ja estava contido no resto da regra -- preco acima das duas linhas,
#: EMA9 acima da VWAP e inclinada a favor ha >=2 velas quase sempre implica a
#: EMA50 por baixo. Os campos continuam saindo; so o corte saiu. `--ema50`
#: liga de volta.
MIN_EMA50_STACK = -9e9
EMA50_PERIOD = 50
RANDOM_REPS = 1


def _row_outcomes(candles, i0, entry, stop, r, *, bull) -> dict[str, float]:
    """Como o do `deep_reclaim`, mas sobre os alvos DESTE setup."""
    out = {}
    for target in TARGETS:
        tag = str(target).replace(".", "").rstrip("0") or "0"
        for h in HORIZONS:
            out[f"r{tag}_h{h}"] = outcome(
                candles, i0, entry, stop, r, bull=bull, target=target, horizon=h)
    return out


def _atr(candles: Sequence[Candle], i: int) -> float | None:
    if i < ATR_PERIOD:
        return None
    trs = [
        max(candles[j].high - candles[j].low,
            abs(candles[j].high - candles[j - 1].close),
            abs(candles[j].low - candles[j - 1].close))
        for j in range(i - ATR_PERIOD + 1, i + 1)
    ]
    return fmean(trs) or None


def htf_ema(candles: Sequence[Candle], factor: int, period: int = 9) -> list[float | None]:
    """A EMA9 do tempo grafico de CIMA, alinhada vela a vela deste TF.

    Reagrupa as velas em baldes de `factor` e calcula a media la. Cada indice
    recebe o valor do ultimo balde **completo** antes dele -- nunca o do balde
    em formacao, que so estaria disponivel depois do fato. Isso resolve o
    "tempos graficos" sem uma segunda requisicao por simbolo, que e o que
    tornaria a pergunta cara o bastante para nao ser feita.
    """
    buckets = [c.close for c in candles[factor - 1 :: factor]]
    series = ema(buckets, period)
    out: list[float | None] = [None] * len(candles)
    for i in range(len(candles)):
        # o ultimo balde FECHADO antes de `i`, nunca o que ainda esta se formando
        b = i // factor - 1
        out[i] = series[b] if 0 <= b < len(series) else None
    return out


def _leg(
    candles: Sequence[Candle], vwap_at: Sequence[float | None],
    e9: Sequence[float | None], i: int, *, bull: bool, cap: int = 200,
) -> int:
    """Ha quantas velas o preco fecha do lado bom das DUAS linhas, ate `i`."""
    n = 0
    for j in range(i, max(-1, i - cap), -1):
        v, e = vwap_at[j], e9[j]
        if v is None or e is None:
            break
        c = candles[j].close
        if (c > v and c > e) if bull else (c < v and c < e):
            n += 1
        else:
            break
    return n


def stops(
    candles: Sequence[Candle], i0: int, pull_start: int, *, bull: bool,
    far_line: float, atr: float,
) -> dict[str, float]:
    """Cada definicao de stop, nomeada. `pinbar` e a da hipotese."""
    out: dict[str, float] = {}
    out["pinbar"] = candles[i0].low if bull else candles[i0].high
    w = candles[pull_start : i0 + 1]
    out["pullback"] = min(c.low for c in w) if bull else max(c.high for c in w)
    for k in LOOKBACKS:
        w = candles[max(0, i0 - k + 1) : i0 + 1]
        out[f"look{k}"] = min(c.low for c in w) if bull else max(c.high for c in w)
    out["farline"] = far_line - 0.25 * atr if bull else far_line + 0.25 * atr
    return out


def run(symbols, timeframe, limit, out, *, require_ema9, min_vwap,
        pinbar_color="l2", min_trend_age=MIN_TREND_AGE, htf_factor=HTF_FACTOR,
        min_line_sep=MIN_LINE_SEP, min_ema50_stack=MIN_EMA50_STACK,
        require_poc=True, min_r_atr=MIN_R_ATR,
        require_poc_side=False, require_rsi_ma=False):
    provider, futures = PaginatedFuturesProvider(), NoFuturesProvider()
    rng = random.Random(7)
    rows: list[dict] = []
    dropped = {"sem pinbar": 0, "toque de corpo": 0, "fechou entre as linhas": 0,
               "ema9 contra": 0, "sem tendencia": 0, "vwap nova": 0,
               "linhas invertidas": 0, "ema50 contra": 0,
               "poc sem sombra": 0, "sem poc": 0,
               "r_atr abaixo do piso": 0,
               "poc do lado contrario": 0, "media do rsi contra": 0}
    for n, symbol in enumerate(symbols, 1):
        try:
            data = load_dashboard_data(
                provider=provider, symbol=symbol, timeframe=timeframe, limit=limit,
                futures_provider=futures, compute_narrative=False,
            )
        except (DataProviderError, ValidationError) as exc:
            lines = str(exc).splitlines()
            detail = lines[1].strip() if len(lines) > 1 else (lines[0] if lines else "")
            print(f"  ! {symbol} pulado: {type(exc).__name__}: {detail[:120]}", flush=True)
            continue
        candles = data.candles
        if len(candles) < 400 or data.vwap is None:
            continue
        e9 = ema_series(candles, 9)
        e50 = ema_series(candles, EMA50_PERIOD)
        # O POC do footprint: mesma linha do indicador, uma leitura por
        # vela, sem olhar para a frente. E a serie mais cara do arquivo.
        pocs = footprint_poc_series(candles)
        r14 = rsi_series(candles, 14)
        rma = rsi_ma_series(candles, 14, 14)
        h9 = htf_ema(candles, htf_factor)
        vwap_by_ts = {p.timestamp: p.value for p in data.vwap.points}
        vwap_at = [vwap_by_ts.get(c.timestamp) for c in candles]
        # Ha quantas velas esta VWAP acumula: ela reancora a meia-noite UTC.
        vwap_age: list[int] = []
        age = 0
        for i in range(len(candles)):
            age = age + 1 if vwap_at[i] is not None and (
                i and vwap_at[i - 1] is not None
                and candles[i].timestamp.date() == candles[i - 1].timestamp.date()
            ) else 1
            vwap_age.append(age)
        kept = 0
        for i0 in range(ATR_PERIOD + 12, len(candles) - max(HORIZONS) - 1):
            v0, e0 = vwap_at[i0], e9[i0]
            if v0 is None or e0 is None:
                continue
            atr = _atr(candles, i0)
            if not atr:
                continue
            candle = candles[i0]
            for bull in (True, False):
                sign = 1.0 if bull else -1.0
                grades = pinbar_grades(
                    candle, bullish=bull, min_tail_fraction=STRICT_WICK_FRACTION)
                if pinbar_color and grades:
                    grades = surviving_grades(
                        candle, grades, bullish=bull, scope=pinbar_color)
                if not grades:
                    dropped["sem pinbar"] += 1
                    continue
                # O fechamento do lado bom das DUAS linhas.
                close0 = candle.close
                if not ((close0 > v0 and close0 > e0) if bull
                        else (close0 < v0 and close0 < e0)):
                    dropped["fechou entre as linhas"] += 1
                    continue
                # A sombra: exigida, nao emitida. E o pedido do leitor e a
                # unica parte do gatilho que diz que houve TESTE.
                touched = wick_touch(candle, v0, e0, bull=bull)
                if touched is None:
                    dropped["toque de corpo"] += 1
                    continue
                if vwap_age[i0] < min_vwap:
                    dropped["vwap nova"] += 1
                    continue
                # A inclinacao termina na vela ANTERIOR: o proprio pinbar
                # levanta a EMA9, e sem a defasagem o eixo vira o gatilho dito
                # com outro nome.
                slope = (
                    None if e9[i0 - 1] is None or e9[i0 - 10] is None
                    else sign * (e9[i0 - 1] - e9[i0 - 10]) / atr
                )
                if require_ema9 and not (slope is not None and slope > 0):
                    dropped["ema9 contra"] += 1
                    continue
                # A ORDEM das linhas: a EMA9 do lado bom da VWAP. Sem isso o
                # gatilho vale dentro de lateralizacao, que e onde ele perde.
                if (e0 - v0) * sign < min_line_sep * atr:
                    dropped["linhas invertidas"] += 1
                    continue
                # O POC do footprint tratado como as outras linhas: a sombra
                # tem que FURAR a linha e o fechamento tem que voltar do lado
                # bom dela. As duas exigencias do leitor sao uma condicao so --
                # `low <= poc <= close` na compra ja diz "furou e fechou
                # acima", que e o mesmo teste do `wick_touch`.
                poc = pocs[i0]
                if poc is None:
                    dropped["sem poc"] += 1
                    continue
                pp = poc.price
                poc_ok = ((candle.low <= pp <= close0) if bull
                          else (candle.high >= pp >= close0))
                if require_poc and not poc_ok:
                    dropped["poc sem sombra"] += 1
                    continue
                # O LADO do POC: o nivel tem que estar dominado pelo lado da
                # operacao. O indicador ja separa as duas intensidades no
                # ponto -- azul (compra) contra vermelho (venda) no grafico.
                # Um gatilho de compra num POC vendido esta comprando contra
                # o volume que formou o proprio nivel.
                poc_buy = poc.buy_intensity > poc.sell_intensity
                if require_poc_side and poc_buy is not bull:
                    dropped["poc do lado contrario"] += 1
                    continue
                # A MEDIA do RSI (SMA 14 sobre o RSI 14) contra o nivel 50.
                ma = rma[i0]
                if ma is None:
                    continue
                if require_rsi_ma and (
                    ma < RSI_MA_LEVEL if bull else ma > RSI_MA_LEVEL
                ):
                    dropped["media do rsi contra"] += 1
                    continue
                # A EMA50 como apoio: por baixo na compra, ou no cruzamento.
                m50 = e50[i0]
                if m50 is None:
                    continue
                if (e0 - m50) * sign < min_ema50_stack * atr:
                    dropped["ema50 contra"] += 1
                    continue
                # A tendencia: as duas linhas ja concordavam ANTES do gatilho.
                trend_age = _leg(candles, vwap_at, e9, i0, bull=bull)
                if trend_age < min_trend_age:
                    dropped["sem tendencia"] += 1
                    continue
                # A perna e o recuo. O extremo da perna e o topo (fundo) desde
                # que o alinhamento comecou; o pullback e o que veio depois.
                leg0 = i0 - trend_age + 1
                ext_i = (
                    max(range(leg0, i0 + 1), key=lambda j: candles[j].high) if bull
                    else min(range(leg0, i0 + 1), key=lambda j: candles[j].low)
                )
                leg_ext = candles[ext_i].high if bull else candles[ext_i].low
                run_atr = sign * (leg_ext - candles[leg0].close) / atr
                pull_atr = ((leg_ext - candle.low) if bull
                            else (candle.high - leg_ext)) / atr
                far_line = min(v0, e0) if bull else max(v0, e0)
                entry = close0
                all_stops = stops(candles, i0, ext_i, bull=bull,
                                  far_line=far_line, atr=atr)
                stop = all_stops["pinbar"]
                r = (entry - stop) if bull else (stop - entry)
                if r <= 0:
                    continue
                if r / atr < min_r_atr:
                    dropped["r_atr abaixo do piso"] += 1
                    continue
                row = {
                    "symbol": symbol, "sample": sample_of(symbol),
                    "timestamp": candle.timestamp.isoformat(),
                    "direction": "bullish" if bull else "bearish",
                    "arm": "pinbar",
                    "entry": entry, "stop": stop,
                    "r_pct": r / entry, "r_atr": r / atr,
                    "pinbar_grade": ",".join(sorted(grades)),
                    "wick_line": touched,
                    "ema9_slope_lag1": slope,
                    "vwap_candles": vwap_age[i0],
                    # A geometria das duas linhas.
                    "line_gap_atr": abs(v0 - e0) / atr,
                    "ema_side_atr": sign * (e0 - v0) / atr,
                    "clear_vwap_atr": sign * (close0 - v0) / atr,
                    "clear_ema_atr": sign * (close0 - e0) / atr,
                    # A perna e o recuo.
                    "trend_age": trend_age,
                    "run_atr": run_atr,
                    "pullback_atr": pull_atr,
                    "pullback_candles": i0 - ext_i,
                    # O POC do footprint (perfil de 23 barras, engine
                    # Geometric). `poc_dist_atr` mede o quanto o fechamento
                    # limpou a linha; `poc_lado` diz qual lado do volume
                    # domina o proprio nivel -- o POC de um nivel comprado e
                    # uma leitura diferente do de um nivel vendido, e isso
                    # sai como campo em vez de virar um segundo gate.
                    "poc_price": pp,
                    "poc_ok": poc_ok,
                    "poc_dist_atr": sign * (close0 - pp) / atr,
                    "poc_lado": (
                        "compra" if poc.buy_intensity > poc.sell_intensity
                        else "venda"
                    ),
                    "poc_pos": (
                        (pp - poc.profile_low) / (poc.profile_high - poc.profile_low)
                        if poc.profile_high > poc.profile_low else None
                    ),
                    # A EMA50. O empilhamento e gate; a DISTANCIA e so medida,
                    # porque "longe demais" e um limiar e ele nao pode ser
                    # escolhido na mesma tabela em que foi visto.
                    "ema50_stack_atr": sign * (e0 - m50) / atr,
                    "ema50_side_atr": sign * (close0 - m50) / atr,
                    "ema50_dist_atr": abs(close0 - m50) / atr,
                    "ema50_slope_lag1": (
                        None if e50[i0 - 1] is None or e50[i0 - 10] is None
                        else sign * (e50[i0 - 1] - e50[i0 - 10]) / atr
                    ),
                    # O momento.
                    "rsi": r14[i0],
                    # A media do RSI: o eixo do filtro novo, e a versao
                    # defasada ao lado para a diferenca ser mensuravel.
                    "rsi_ma": ma,
                    "rsi_ma_lag1": rma[i0 - 1],
                    "rsi_ma_dist": sign * (ma - RSI_MA_LEVEL),
                    "rsi_lag1": r14[i0 - 1],
                    "rsi_slope_lag1": (
                        None if r14[i0 - 1] is None or r14[i0 - 4] is None
                        else sign * (r14[i0 - 1] - r14[i0 - 4])
                    ),
                    # O tempo grafico de cima, do ultimo balde COMPLETO.
                    "htf_side": (
                        None if h9[i0] is None else sign * (close0 - h9[i0]) / atr
                    ),
                    "htf_slope": (
                        None if h9[i0] is None or h9[max(0, i0 - htf_factor)] is None
                        else sign * (h9[i0] - h9[max(0, i0 - htf_factor)]) / atr
                    ),
                }
                row.update(_row_outcomes(candles, i0, entry, stop, r, bull=bull))
                row["alts"] = {}
                for name, alt in all_stops.items():
                    ra = (entry - alt) if bull else (alt - entry)
                    row[f"r_atr_{name}"] = (ra / atr) if ra > 0 else None
                    if ra <= 0:
                        continue
                    row["alts"][name] = {
                        "r_atr": ra / atr, "r_pct": ra / entry,
                        **_row_outcomes(candles, i0, entry, alt, ra, bull=bull),
                    }
                rows.append(row)
                kept += 1
                # Controle casado em simbolo E direcao, com o R deste braco.
                for _ in range(RANDOM_REPS):
                    j = rng.randrange(ATR_PERIOD, len(candles) - max(HORIZONS) - 1)
                    centry = candles[j].close
                    cstop = centry - r if bull else centry + r
                    crow = {
                        "symbol": symbol, "sample": sample_of(symbol),
                        "timestamp": candles[j].timestamp.isoformat(),
                        "direction": "bullish" if bull else "bearish",
                        "arm": "aleatorio",
                        "r_pct": r / centry, "r_atr": r / atr,
                    }
                    crow.update(_row_outcomes(candles, j, centry, cstop, r, bull=bull))
                    rows.append(crow)
        print(f"[{n}/{len(symbols)}] {symbol:11s} {kept} entradas", flush=True)
    Path(out).write_text(json.dumps(rows))
    print(f"\ngravado {len(rows)} linhas -> {out}", flush=True)
    print("descartados: " + ", ".join(f"{k} {v}" for k, v in dropped.items()))
    report(rows)


def _net(rs, key, target) -> str:
    if len(rs) < 40:
        return f"n={len(rs):5d} (poucos)"
    hit = sum(1 for r in rs if r[key] >= target - 0.01) / len(rs)
    net = fmean(r[key] - COST_PCT / r["r_pct"] for r in rs)
    tot = sum(r[key] - COST_PCT / r["r_pct"] for r in rs)
    return f"n={len(rs):5d}  acerto {hit:5.1%}  liq {net:+.3f}  total {tot:+8.1f}R"


INF = float("inf")
#: Cada eixo em faixas DISJUNTAS: faixas cumulativas escondem que o lucro de um
#: teto largo pode vir inteiro do nucleo apertado dentro dele.
AXES = (
    ("r_atr", ((0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.5), (2.5, INF))),
    ("line_gap_atr", ((0, 0.1), (0.1, 0.3), (0.3, 0.6), (0.6, 1.2), (1.2, INF))),
    ("ema_side_atr", ((-INF, -0.3), (-0.3, 0), (0, 0.3), (0.3, 1.0), (1.0, INF))),
    ("trend_age", ((2, 4), (4, 8), (8, 16), (16, 40), (40, INF))),
    ("run_atr", ((-INF, 1), (1, 2), (2, 4), (4, 8), (8, INF))),
    ("pullback_atr", ((-INF, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, INF))),
    ("pullback_candles", ((0, 1), (1, 3), (3, 6), (6, 12), (12, INF))),
    ("rsi_ma", ((0, 45), (45, 50), (50, 55), (55, 60), (60, 101))),
    ("rsi_ma_dist", ((-INF, 0), (0, 3), (3, 8), (8, INF))),
    ("rsi_lag1", ((0, 30), (30, 45), (45, 55), (55, 70), (70, 101))),
    ("rsi_slope_lag1", ((-INF, -5), (-5, 0), (0, 5), (5, INF))),
    ("poc_dist_atr", ((-INF, 0), (0, 0.25), (0.25, 0.6), (0.6, 1.2), (1.2, INF))),
    ("poc_pos", ((0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.01))),
    ("ema50_stack_atr", ((0, 0.3), (0.3, 0.8), (0.8, 1.5), (1.5, INF))),
    ("ema50_dist_atr", ((0, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 3.5), (3.5, INF))),
    ("ema50_slope_lag1", ((-INF, 0), (0, 0.3), (0.3, 1.0), (1.0, INF))),
    ("htf_side", ((-INF, 0), (0, 1), (1, 3), (3, INF))),
    ("htf_slope", ((-INF, 0), (0, 0.5), (0.5, 1.5), (1.5, INF))),
    ("vwap_candles", ((4, 8), (8, 16), (16, 30), (30, INF))),
)


def report(rows: Sequence[dict], horizon: int = HORIZONS[0]) -> None:
    for sample in ("search", "holdout"):
        S = [r for r in rows if r["sample"] == sample]
        real = [r for r in S if r["arm"] != "aleatorio"]
        ctrl = [r for r in S if r["arm"] == "aleatorio"]
        if len(real) < 40:
            continue
        for target in TARGETS:
            tag = str(target).replace(".", "").rstrip("0") or "0"
            key = f"r{tag}_h{horizon}"
            print(f"\n=== {sample} · alvo {target:g}R · h{horizon}")
            print(f"  {'tudo':24s} {_net(real, key, target)}")
            print(f"  {'  controle aleatorio':24s} {_net(ctrl, key, target)}")
            for line in ("vwap", "ema", "ambas"):
                sel = [r for r in real if r.get("wick_line") == line]
                print(f"  {'pavio ' + line:24s} {_net(sel, key, target)}")
            for grade in sorted({r.get("pinbar_grade") or "" for r in real}):
                sel = [r for r in real if (r.get("pinbar_grade") or "") == grade]
                print(f"  {'grau ' + grade:24s} {_net(sel, key, target)}")
            for field, bands in AXES:
                for lo, hi in bands:
                    sel = [r for r in real
                           if r.get(field) is not None and lo <= r[field] < hi]
                    lbl = f"{field} {lo:g}-{hi:g}".replace("-inf", "+")
                    print(f"  {lbl:24s} {_net(sel, key, target)}")
            names = sorted({n for r in real for n in (r.get("alts") or {})})
            for name in names:
                sel = [r["alts"][name] for r in real if name in (r.get("alts") or {})]
                if len(sel) < 40:
                    print(f"  {'stop ' + name:24s} n={len(sel):5d} (poucos)")
                    continue
                mean_r = fmean(a["r_atr"] for a in sel)
                print(f"  {'stop ' + name:24s} {_net(sel, key, target)}"
                      f"   r_atr {mean_r:.2f}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="+", default=list(UNIVERSE))
    p.add_argument("--timeframe", default="1h")
    p.add_argument("--limit", type=int, default=60_000)
    p.add_argument("--no-ema9", action="store_true",
                   help="nao exigir a EMA9 inclinada a favor (mede o custo dela)")
    p.add_argument("--no-color", action="store_true",
                   help="aceita pinbar l2 de cor contraria")
    p.add_argument("--min-vwap", type=int, default=MIN_VWAP_CANDLES)
    p.add_argument("--min-trend-age", type=int, default=MIN_TREND_AGE)
    p.add_argument("--min-line-sep", type=float, default=MIN_LINE_SEP,
                   help="separacao minima EMA9-VWAP em ATR, com sinal a favor "
                        "(0 = so a ordem das linhas, que e o padrao)")
    p.add_argument("--min-ema50-stack", type=float, default=MIN_EMA50_STACK,
                   help="empilhamento minimo EMA9-EMA50 em ATR a favor "
                        "(0 = so a ordem/cruzamento, que e o padrao)")
    p.add_argument("--poc-side", action="store_true",
                   help="exigir o POC dominado pelo lado da operacao. MEDIDO e "
                        "desligado: ganha POR OPERACAO (3R +0,253 -> +0,278) e "
                        "PERDE POR RISCO NO TEMPO (SR 0,53 -> 0,38), porque "
                        "corta 30%% do fluxo e rareia a serie diaria")
    p.add_argument("--rsi-ma", action="store_true",
                   help="liga de volta a media do RSI contra o 50 (medida e "
                        "desligada: custava 20%% do fluxo e PIORAVA o H4)")
    p.add_argument("--min-r-atr", type=float, default=MIN_R_ATR,
                   help="piso de R em ATR (0 desliga; ver MIN_R_ATR)")
    p.add_argument("--no-poc", action="store_true",
                   help="nao exigir a sombra na linha do POC (mede o custo)")
    p.add_argument("--ema50", action="store_true",
                   help="liga de volta o apoio da EMA50 (medido e desligado: "
                        "custava 20%% do fluxo por zero de acerto)")
    p.add_argument("--no-line-sep", action="store_true",
                   help="desliga o corte, para medir o que ele custa")
    p.add_argument("--htf-factor", type=int, default=HTF_FACTOR)
    p.add_argument("--out", default="/tmp/trend_ride.json")
    p.add_argument("--report-only", default=None)
    p.add_argument("--horizon", type=int, default=HORIZONS[0], choices=HORIZONS)
    a = p.parse_args()
    if a.report_only:
        report(json.loads(Path(a.report_only).read_text()), a.horizon)
        return
    run(a.symbols, TimeFrame(a.timeframe), a.limit, a.out,
        require_ema9=not a.no_ema9, min_vwap=a.min_vwap,
        min_trend_age=a.min_trend_age, htf_factor=a.htf_factor,
        min_line_sep=-9e9 if a.no_line_sep else a.min_line_sep,
        min_ema50_stack=a.min_ema50_stack if a.ema50 else -9e9,
        require_poc=not a.no_poc, min_r_atr=a.min_r_atr,
        require_poc_side=a.poc_side,
        require_rsi_ma=a.rsi_ma,
        pinbar_color=None if a.no_color else "l2")


if __name__ == "__main__":
    main()
