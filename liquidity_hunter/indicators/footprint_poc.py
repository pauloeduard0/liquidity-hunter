"""Point of Control da mistura de sinos do "Volume Footprint" (VFP-Intro).

Porte fiel do POC *de gráfico* do indicador Pine v6 de `ata_sabanci`, no
engine **Geometric** — o default do script e o único que se sustenta em
OHLCV puro: os engines Intrabar e Footprint leem volume de um timeframe
inferior / do tape por tick, dados que este projeto não tem.

O perfil não é um histograma de baldes. Cada candle com range entra como
**duas normais truncadas** ao seu próprio [low, high] — uma alimentada pelo
volume comprador, outra pelo vendedor — e o perfil é a soma dessas
componentes. O POC é o ponto da grade de amostragem onde a soma das duas
intensidades (compra + venda) é máxima. Consequências que o histograma
clássico não tem, e que precisam ser preservadas para a linha bater com a
do TradingView:

- o centro de cada sino não é o preço médio: o sino comprador senta em
  ``low + (close - low) / 2`` e o vendedor em ``close + (high - close) / 2``;
- ``sigma`` é o range do candle dividido pela concentração (3.0 por padrão),
  e cada componente é normalizada pela sua própria massa truncada, de modo
  que a área sob ela é exatamente o volume daquele lado do candle;
- candles de range zero viram *átomos* e ficam **fora** das curvas — não
  participam do POC (é assim no script);
- o POC vive na grade de ``resolution + 1`` pontos entre a mínima e a máxima
  do período, não num nível de tick: mudar ``resolution`` move a linha.

O script desenha o POC apenas na última barra. Aqui `footprint_poc_series`
repete esse cálculo com cada candle no papel de "última barra", que é o que
dá a linha ao vivo como série utilizável em backtest.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from liquidity_hunter.core.domain import Candle

#: Barras que entram no perfil (`Profile Period` no menu do script).
DEFAULT_PERIOD = 23

#: Pontos de amostragem da curva menos um (`Width / Resolution`, campo direito).
DEFAULT_RESOLUTION = 100

#: Divisor do range que vira o desvio-padrão do sino (`Volume Concentration`).
DEFAULT_CONCENTRATION = 3.0

_INV_SQRT_2PI = 3.989422804014327e-1
_INV_SQRT_2 = 7.071067811865476e-1


@dataclass(frozen=True)
class _Component:
    """Uma normal truncada: um lado do volume de um candle."""

    mu: float
    sigma: float
    coef: float
    price_lo: float
    price_hi: float


@dataclass(frozen=True)
class FootprintPOC:
    """Leitura do perfil numa barra: o POC e o quadro em que ele foi achado."""

    price: float
    #: Intensidade total (compra + venda) no POC, em volume por unidade de preço.
    intensity: float
    #: Intensidades separadas no mesmo ponto — qual lado domina o nível.
    buy_intensity: float
    sell_intensity: float
    #: Mínima e máxima do período: a moldura em que a grade foi traçada.
    profile_low: float
    profile_high: float
    #: Quantas barras tinham dados (o script tolera período incompleto).
    bars: int


def _norm_pdf(z: float) -> float:
    return _INV_SQRT_2PI * math.exp(-0.5 * z * z)


def _norm_cdf(z: float) -> float:
    """Phi(z) pela identidade do erfc, como `f_profNormCdf` no Pine."""
    return 0.5 * math.erfc(-z * _INV_SQRT_2)


def _density(price: float, comps: Sequence[_Component]) -> float:
    """Intensidade de um lado do perfil num preço (`f_profDensity`)."""
    total = 0.0
    for comp in comps:
        if comp.price_lo <= price <= comp.price_hi:
            total += comp.coef * _norm_pdf((price - comp.mu) / comp.sigma)
    return total


def _geometric_split(candle: Candle) -> tuple[float, float]:
    """Volume comprador/vendedor pela posição do fechamento no range."""
    bar_range = candle.high - candle.low
    share = (candle.close - candle.low) / bar_range if bar_range > 0.0 else 0.5
    return candle.volume * share, candle.volume * (1.0 - share)


def footprint_poc(
    candles: Sequence[Candle],
    *,
    period: int = DEFAULT_PERIOD,
    resolution: int = DEFAULT_RESOLUTION,
    concentration: float = DEFAULT_CONCENTRATION,
) -> FootprintPOC | None:
    """O POC do perfil das últimas `period` velas de `candles`.

    Devolve `None` quando não há volume, quando o período não tem nenhuma
    vela com range (só átomos, que não entram nas curvas) ou quando a
    moldura é degenerada (mínima == máxima).
    """
    if period < 2 or resolution < 1 or concentration <= 0.0:
        raise ValueError("period >= 2, resolution >= 1 e concentration > 0")
    if not candles:
        return None

    window = candles[-period:]
    comps_buy: list[_Component] = []
    comps_sell: list[_Component] = []
    profile_low = math.inf
    profile_high = -math.inf
    total_volume = 0.0

    for candle in window:
        profile_low = min(profile_low, candle.low)
        profile_high = max(profile_high, candle.high)
        buy_volume, sell_volume = _geometric_split(candle)
        total_volume += buy_volume + sell_volume
        bar_range = candle.high - candle.low
        if bar_range <= 0.0:
            # Vela sem range é átomo: soma no volume do período, mas fica
            # fora das curvas — e portanto fora da disputa pelo POC.
            continue
        sigma = bar_range / concentration
        mu_buy = candle.low + (candle.close - candle.low) / 2.0
        mu_sell = candle.close + (candle.high - candle.close) / 2.0
        for mu, volume, comps in (
            (mu_buy, buy_volume, comps_buy),
            (mu_sell, sell_volume, comps_sell),
        ):
            mass = _norm_cdf((candle.high - mu) / sigma) - _norm_cdf(
                (candle.low - mu) / sigma
            )
            if volume > 0.0 and mass > 0.0:
                comps.append(
                    _Component(
                        mu=mu,
                        sigma=sigma,
                        coef=volume / (sigma * mass),
                        price_lo=candle.low,
                        price_hi=candle.high,
                    )
                )

    span = profile_high - profile_low
    if total_volume <= 0.0 or span <= 0.0 or not (comps_buy or comps_sell):
        return None

    step = span / resolution
    best_total = -1.0
    best_price = profile_low
    best_buy = 0.0
    best_sell = 0.0
    for k in range(resolution + 1):
        price = profile_low + k * step
        buy_intensity = _density(price, comps_buy)
        sell_intensity = _density(price, comps_sell)
        # Estritamente maior: num empate a grade fica com o preço mais baixo,
        # como o laço do Pine.
        if buy_intensity + sell_intensity > best_total:
            best_total = buy_intensity + sell_intensity
            best_price = price
            best_buy = buy_intensity
            best_sell = sell_intensity

    return FootprintPOC(
        price=best_price,
        intensity=best_total,
        buy_intensity=best_buy,
        sell_intensity=best_sell,
        profile_low=profile_low,
        profile_high=profile_high,
        bars=len(window),
    )


def footprint_poc_series(
    candles: Sequence[Candle],
    *,
    period: int = DEFAULT_PERIOD,
    resolution: int = DEFAULT_RESOLUTION,
    concentration: float = DEFAULT_CONCENTRATION,
    warmup_full_period: bool = True,
) -> list[FootprintPOC | None]:
    """A leitura do POC em cada candle, alinhada 1:1 com `candles`.

    Cada posição é o que o indicador mostraria com aquele candle como última
    barra — nenhuma posição olha para o futuro. Com `warmup_full_period`
    (o padrão) as posições antes de `period` velas ficam `None`; desligue
    para deixar o perfil abrir com o período incompleto, como o script faz
    quando o histórico do símbolo é curto.
    """
    readings: list[FootprintPOC | None] = []
    for index in range(len(candles)):
        if warmup_full_period and index + 1 < period:
            readings.append(None)
            continue
        readings.append(
            footprint_poc(
                candles[: index + 1],
                period=period,
                resolution=resolution,
                concentration=concentration,
            )
        )
    return readings
