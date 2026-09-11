"""H1: o join HTF/LTF do HUNT usa candle ainda em formacao?

Um `MarketStructure` da HTF carrega o timestamp do candle que produziu o
evento, e esse timestamp e o **open time** do kline (`binance._to_candle`, que
le `row[0]`). O evento so passa a existir quando aquele candle **fecha**. Mas
`LiquidityHuntEngine._htf_trend_at` filtra os eventos com

    event.timestamp <= at

o que admite um evento cujo candle ainda estava aberto em `at`. Uma perna LTF
que flipa as 13:00 enxerga o BOS de um candio H4 aberto as 12:00 e que so
fechou as 16:00: tres horas de informacao futura.

Isto importa porque a HTF nao e decorativa no HUNT. Ela decide `hunted_side`,
decide `capture_direction`, e decide se a perna e um *hunt* (contra-tendencia)
ou uma *continuation* (alinhada). Uma leitura HTF invertida nao muda um rotulo:
troca o episodio de stream e inverte o lado cacado.

O que este arquivo faz e so medir. Duas variantes leem **o mesmo snapshot**:

    LEGACY   event.timestamp <= at                    (producao, hoje)
    CAUSAL   event.timestamp + periodo_htf <= at      (candle fechado)

Um unico `DashboardData` por janela alimenta as duas, entao todo sinal --
sweeps, VSA, raids, zonas, Supertrend -- e identico nos dois bracos e a unica
variavel do experimento e o join. Nada em `liquidity_hunter/` e tocado: a
variante causal e uma subclasse aqui dentro.

Uma limitacao declarada: o painel roda com um provider de futuros vazio, para
nao gastar tres requisicoes por simbolo num universo de 72 (ver
`project_binance_ban_request_budget`). Sem OI, as fontes `oi_flush`/
`oi_covering` e o `capture_quality` nao existem, entao os numeros ABSOLUTOS do
baseline nao sao os de producao. O contraste LEGACY x CAUSAL -- que e a
pergunta -- nao e afetado: os dois bracos leem o mesmo snapshot empobrecido.

Run:
    poetry run python -m research.hunt_htf_causality \
        --out research/hunt_htf_causality_baseline.json
    poetry run python -m research.hunt_htf_causality \
        --report-only research/hunt_htf_causality_baseline.json
    poetry run python -m research.hunt_htf_causality --case BTCUSDT:M15
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import fmean, median

from liquidity_hunter.app.dashboard_data import (
    _HIGHER_TIMEFRAME_MAP,
    DashboardData,
    default_ohlcv_provider,
    load_dashboard_data,
)
from liquidity_hunter.app.liquidity_hunt import LiquidityHuntEngine
from liquidity_hunter.core.domain import (
    Candle,
    FundingRate,
    LongShortRatio,
    MarketDirection,
    MarketStructure,
    OpenInterestPoint,
    RetailPositioning,
    TimeFrame,
)
from liquidity_hunter.data import OHLCVProvider
from liquidity_hunter.data.providers.base import FuturesDataProvider
from liquidity_hunter.indicators.supertrend import true_range_series
from research._symbols import UNIVERSE, sample_of

# ---------------------------------------------------------------------------
# 1. A regra causal
# ---------------------------------------------------------------------------

#: A duracao real de cada timeframe. Escrita por extenso e nao derivada de um
#: multiplicador generico porque e exatamente o numero que a correcao depende:
#: um W1 tratado como "7 x D1" e um D1 tratado como "24 x H1" acertam, mas um
#: M15 tratado como "15 minutos" quando a HTF e M30 erra por 15 minutos --
#: silenciosamente, e so em algumas pernas.
HTF_PERIOD: dict[TimeFrame, timedelta] = {
    TimeFrame.M1: timedelta(minutes=1),
    TimeFrame.M5: timedelta(minutes=5),
    TimeFrame.M15: timedelta(minutes=15),
    TimeFrame.M30: timedelta(minutes=30),
    TimeFrame.H1: timedelta(hours=1),
    TimeFrame.H4: timedelta(hours=4),
    TimeFrame.D1: timedelta(days=1),
    TimeFrame.W1: timedelta(weeks=1),
}


def is_knowable(event_timestamp: datetime, period: timedelta, at: datetime) -> bool:
    """Um evento HTF aberto em ``event_timestamp`` ja e conhecivel em ``at``?

    A fronteira e o fechamento, e o fechamento **conta**: um candle H4 aberto
    as 12:00 fecha as 16:00, e as 16:00 a informacao existe. Portanto ``<=``,
    nao ``<``. Um candle fecha no instante em que o proximo abre, e o proximo
    candle abrir e precisamente a evidencia de que o anterior terminou.

    Todos os timestamps do projeto sao tz-aware em UTC (o provider constroi com
    ``datetime.fromtimestamp(ms/1000, tz=UTC)``), entao a comparacao nunca cruza
    fuso. Um gap na serie HTF -- candle faltando na venue -- nao afeta a regra:
    ela olha o evento, e um evento so existe sobre um candle que existiu.
    """
    return event_timestamp + period <= at


class CausalHuntEngine(LiquidityHuntEngine):
    """`LiquidityHuntEngine` com o join HTF corrigido, e nada mais.

    Sobrescreve **um unico metodo**. Todo o resto -- pesos, thresholds, gates,
    clusters, pools, ancoragem -- e a producao literal, herdada. E deliberado:
    se a variante reimplementasse o motor, a diferenca medida entre os dois
    bracos poderia vir da reimplementacao em vez de vir do join.

    Desde que a producao adotou a regra (2026-09-11) esta classe deixou de ser
    a implementacao e passou a ser a **testemunha independente** dela: escrita
    antes, derivada do enunciado do problema e nao do codigo corrigido, ela
    serve para conferir que o `htf_period` de producao produz o mesmo stream
    (ver `assert_matches_production`). Uma correcao que so pode ser verificada
    por si mesma nao esta verificada.
    """

    def __init__(self, *args: object, htf_period: timedelta, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.htf_period = htf_period

    def _htf_trend_at(  # type: ignore[override]
        self,
        htf_events: list[MarketStructure],
        at: datetime,
        fallback: MarketDirection,
        htf_period: timedelta | None = None,
    ) -> MarketDirection:
        visible = [
            e for e in htf_events if is_knowable(e.timestamp, self.htf_period, at)
        ]
        trend, _ = self._current_trend(visible)
        return trend if trend is not None else fallback


class LegacyHuntEngine(LiquidityHuntEngine):
    """O join **anterior** a correcao, preservado para o experimento continuar.

    Antes de 2026-09-11 o braco LEGACY era simplesmente `LiquidityHuntEngine()`.
    Com a correcao em producao esse motor passou a ser causal, e usa-lo como
    LEGACY compararia a correcao consigo mesma -- o painel teria dado zero
    diferenca e a medicao teria se apagado no exato momento em que virou codigo.
    Forcar `htf_period=None` reproduz o comportamento antigo pelo caminho que a
    propria producao mantem aberto para o estado vivo.
    """

    @staticmethod
    def _htf_period(data: DashboardData) -> timedelta | None:
        return None


def assert_matches_production(
    data: DashboardData, htf_period: timedelta
) -> tuple[int, int]:
    """A producao corrigida produz o mesmo stream que a variante da pesquisa?

    Equivalencia **estrutural**, episodio a episodio (start, end, lado, score,
    fontes), nao contagem: dois streams de tamanho igual podem estar descrevendo
    pernas diferentes, que e precisamente o erro que o diff deste modulo existe
    para nao cometer.
    """
    production = LiquidityHuntEngine()
    witness = CausalHuntEngine(htf_period=htf_period)
    pairs = 0
    for name, method in (
        ("hunt", "build_history"),
        ("continuation", "build_continuation_history"),
    ):
        a = getattr(production, method)(data)
        b = getattr(witness, method)(data)
        assert len(a) == len(b), f"{name}: {len(a)} != {len(b)}"
        for x, y in zip(a, b, strict=True):
            assert x == y, f"{name}: {x} != {y}"
        pairs += len(a)
    return pairs, 0


# ---------------------------------------------------------------------------
# 2. Providers de painel (uma busca de rede por simbolo/TF)
# ---------------------------------------------------------------------------


class WindowProvider(OHLCVProvider):
    """Serve prefixos de series ja baixadas, uma por timeframe.

    `research._replay.PrefixProvider` serve uma serie so e ignora o timeframe
    pedido, o que basta para o detector de estrutura mas nao aqui: o HUNT pede
    **duas** series na mesma chamada (a do grafico e a da HTF) e receber a
    errada faria a pesquisa medir um join entre uma serie e ela mesma.

    ``cut`` trunca as duas no mesmo instante de tempo real, que e o que torna
    uma janela uma janela: um observador em ``cut`` nao tem candle nenhum
    depois de ``cut``, em nenhum dos dois timeframes.
    """

    max_fetch_limit = 10_000

    def __init__(
        self,
        series: dict[TimeFrame, list[Candle]],
        cut: datetime | None = None,
        drop: frozenset[TimeFrame] = frozenset(),
    ) -> None:
        self.series = series
        self.cut = cut
        # Timeframes cujo ULTIMO candle e removido. Serve a pergunta do estado
        # vivo: a HTF entregue pelo provider tem o candle em formacao no fim, e
        # o `final_trend` do detector o le. Tirar exatamente esse candle mede o
        # repaint sem tocar em mais nada.
        self.drop = drop

    def get_ohlcv(
        self, symbol: str, timeframe: TimeFrame, limit: int = 500
    ) -> list[Candle]:
        candles = self.series.get(timeframe, [])
        if self.cut is not None:
            candles = [c for c in candles if c.timestamp <= self.cut]
        if timeframe in self.drop and candles:
            candles = candles[:-1]
        return candles[-limit:]


class NoFuturesProvider(FuturesDataProvider):
    """Sem OI, sem funding, sem long/short -- e sem tres requisicoes por simbolo."""

    def get_open_interest_history(
        self, symbol: str, timeframe: TimeFrame, limit: int = 500
    ) -> list[OpenInterestPoint]:
        return []

    def get_funding_rate_history(self, symbol: str, limit: int = 500) -> list[FundingRate]:
        return []

    def get_long_short_ratio(
        self, symbol: str, timeframe: TimeFrame, limit: int = 500
    ) -> list[LongShortRatio]:
        return []


# ---------------------------------------------------------------------------
# 3. Identidade de episodio e diff estrutural
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EpisodeRow:
    """Um episodio, achatado para poder ser diffado entre os dois bracos."""

    symbol: str
    tf: str
    window: str
    stream: str  # "hunt" | "continuation"
    start: str
    end: str
    hunted_side: str
    correction: str
    score: float
    sources: tuple[str, ...]
    quality: str
    failed_reversal: bool

    @property
    def identity(self) -> tuple[str, str, str, str]:
        """O que faz dois episodios serem *o mesmo* episodio.

        A captura (`end`) e a identidade, nao o inicio: o inicio de um episodio
        e o grab anterior ou o flip da perna, e ambos se deslocam quando a
        classificacao da perna vizinha muda. Dois episodios que terminam no
        mesmo grab sao o mesmo evento de mercado, ainda que um deles tenha sido
        reclassificado de stream ou de lado.
        """
        return (self.symbol, self.tf, self.window, self.end)


def episode_rows(
    data: DashboardData, engine: LiquidityHuntEngine, symbol: str, tf: str, window: str
) -> list[EpisodeRow]:
    rows = []
    for stream, eps in (
        ("hunt", engine.build_history(data)),
        ("continuation", engine.build_continuation_history(data)),
    ):
        for e in eps:
            rows.append(
                EpisodeRow(
                    symbol=symbol,
                    tf=tf,
                    window=window,
                    stream=stream,
                    start=e.start_timestamp.isoformat(),
                    end=e.end_timestamp.isoformat(),
                    hunted_side=e.hunted_side.value,
                    correction=e.correction_direction.value,
                    score=e.capture_score,
                    sources=tuple(e.capture_sources),
                    quality=e.capture_quality.value,
                    failed_reversal=e.failed_reversal,
                )
            )
    return rows


#: As classes de diferenca da Etapa 7. Uma substituicao -- um episodio que
#: troca de stream -- soma zero numa contagem agregada de episodios, e e
#: exatamente a mudanca mais grave que o join pode causar. Por isso o diff e
#: por identidade, e nao por contagem.
DIFF_CLASSES = (
    "A_identico",
    "B_hunt_para_continuation",
    "C_continuation_para_hunt",
    "D_some_no_causal",
    "E_surge_no_causal",
    "F_hunted_side_muda",
    "G_score_ou_sources_mudam",
)


def diff_streams(
    legacy: list[EpisodeRow], causal: list[EpisodeRow]
) -> tuple[Counter[str], list[dict[str, object]]]:
    by_id_legacy = {r.identity: r for r in legacy}
    by_id_causal = {r.identity: r for r in causal}
    counts: Counter[str] = Counter()
    details: list[dict[str, object]] = []
    for ident in by_id_legacy.keys() | by_id_causal.keys():
        a, b = by_id_legacy.get(ident), by_id_causal.get(ident)
        if a is None and b is not None:
            counts["E_surge_no_causal"] += 1
            details.append({"class": "E_surge_no_causal", "id": list(ident), "causal": b.stream})
            continue
        if b is None and a is not None:
            counts["D_some_no_causal"] += 1
            details.append({"class": "D_some_no_causal", "id": list(ident), "legacy": a.stream})
            continue
        assert a is not None and b is not None
        if a.stream != b.stream:
            key = (
                "B_hunt_para_continuation"
                if a.stream == "hunt"
                else "C_continuation_para_hunt"
            )
            counts[key] += 1
            details.append({"class": key, "id": list(ident)})
        elif a.hunted_side != b.hunted_side:
            counts["F_hunted_side_muda"] += 1
            details.append({"class": "F_hunted_side_muda", "id": list(ident),
                            "legacy": a.hunted_side, "causal": b.hunted_side})
        elif a.score != b.score or a.sources != b.sources:
            counts["G_score_ou_sources_mudam"] += 1
            details.append({"class": "G_score_ou_sources_mudam", "id": list(ident)})
        else:
            counts["A_identico"] += 1
    return counts, details


# ---------------------------------------------------------------------------
# 4. Metricas de causalidade por perna
# ---------------------------------------------------------------------------


def leg_causality(
    data: DashboardData, engine: LiquidityHuntEngine, period: timedelta
) -> list[dict[str, object]]:
    """Por perna LTF: o LEGACY leu um candle HTF aberto, e isso mudou algo?

    O *lead artificial* e quanto tempo o LEGACY se antecipou: a distancia entre
    o flip da perna e o instante em que o evento HTF que ele usou realmente
    fechou. Reportado em tempo e em candles LTF, porque as duas leituras dizem
    coisas diferentes -- quatro horas sao muito num M15 e nada num D1.
    """
    segs = engine._trend_segments(data.internal_structure_events)
    htf_events = data.higher_timeframe_events
    scalar = data.higher_timeframe_direction
    spacing = (
        data.candles[-1].timestamp - data.candles[-2].timestamp
        if len(data.candles) >= 2
        else None
    )
    causal = CausalHuntEngine(htf_period=period)
    out: list[dict[str, object]] = []
    for direction, start, _ev in segs:
        legacy_view = engine._htf_trend_at(htf_events, start, scalar)
        causal_view = causal._htf_trend_at(htf_events, start, scalar)
        used = [e for e in htf_events if e.timestamp <= start]
        open_candle = bool(used) and not is_knowable(
            max(e.timestamp for e in used), period, start
        )
        lead = None
        if open_candle:
            lead = (max(e.timestamp for e in used) + period) - start
        out.append(
            {
                "leg_start": start.isoformat(),
                "ltf_direction": direction.value,
                "legacy_htf": legacy_view.value,
                "causal_htf": causal_view.value,
                "trend_muda": legacy_view is not causal_view,
                "usa_candle_aberto": open_candle,
                "lead_segundos": lead.total_seconds() if lead else None,
                "lead_candles_ltf": (
                    lead / spacing if lead and spacing and spacing > timedelta(0) else None
                ),
                "legacy_stream": "continuation" if direction is legacy_view else "hunt",
                "causal_stream": "continuation" if direction is causal_view else "hunt",
            }
        )
    return out


# ---------------------------------------------------------------------------
# 5. Baseline com controle casado
# ---------------------------------------------------------------------------

HORIZONS = (5, 10, 20, 40)
_CONTROL_DRAWS = 20


def _excursion(
    candles: list[Candle], i: int, up: bool, horizon: int, atr: float
) -> tuple[float, float] | None:
    fut = candles[i + 1 : i + 1 + horizon]
    if len(fut) < horizon or atr <= 0:
        return None
    entry = candles[i].close
    if up:
        return (max(c.high for c in fut) - entry) / atr, (entry - min(c.low for c in fut)) / atr
    return (entry - min(c.low for c in fut)) / atr, (max(c.high for c in fut) - entry) / atr


def baseline_rows(
    data: DashboardData,
    rows: list[EpisodeRow],
    arm: str,
    rng: random.Random,
) -> list[dict[str, object]]:
    """Excursao futura de cada grab, contra controle casado.

    O controle casa simbolo, timeframe, **direcao** e **janela**: os sorteios
    saem da mesma serie visivel do episodio. Casar a direcao e o que a lição do
    `raid_reversal` custou -- sem isso, qualquer periodo que tendeu faz tudo
    parecer preditivo. Casar a janela e o que impede o controle de um episodio
    de 2024 ser sorteado no regime de 2026.
    """
    candles = data.candles
    if len(candles) < max(HORIZONS) + 2:
        return []
    atr = fmean(true_range_series(candles))
    index = {c.timestamp: i for i, c in enumerate(candles)}
    out: list[dict[str, object]] = []
    for r in rows:
        i = index.get(datetime.fromisoformat(r.end))
        if i is None:
            continue
        up = r.hunted_side == RetailPositioning.SHORT.value
        rec: dict[str, object] = {
            "arm": arm, "symbol": r.symbol, "tf": r.tf, "window": r.window,
            "stream": r.stream, "up": up, "sample": sample_of(r.symbol),
            "score": r.score, "sources": list(r.sources),
        }
        ok = False
        for h in HORIZONS:
            real = _excursion(candles, i, up, h, atr)
            if real is None:
                continue
            ctl = [
                e
                for _ in range(_CONTROL_DRAWS)
                if (e := _excursion(candles, rng.randrange(0, len(candles) - h - 1), up, h, atr))
            ]
            if not ctl:
                continue
            ok = True
            rec[f"mfe_{h}"], rec[f"mae_{h}"] = real
            rec[f"ctl_mfe_{h}"] = fmean(c[0] for c in ctl)
            rec[f"ctl_mae_{h}"] = fmean(c[1] for c in ctl)
            rec[f"win_{h}"] = real[0] > real[1]
            rec[f"ctl_win_{h}"] = fmean(1.0 if c[0] > c[1] else 0.0 for c in ctl)
        if ok:
            out.append(rec)
    return out


# ---------------------------------------------------------------------------
# 6. O painel
# ---------------------------------------------------------------------------

TFS: dict[str, TimeFrame] = {"M15": TimeFrame.M15, "H1": TimeFrame.H1, "H4": TimeFrame.H4}
#: Quantas janelas por simbolo/TF, e o passo entre elas em candles. Cada janela
#: e um recorte truncado da MESMA serie baixada, entao o painel inteiro custa
#: uma requisicao por (simbolo, timeframe) -- nao uma por janela.
WINDOWS = 3
WINDOW_STEP = 150
FETCH_LIMIT = 1000
VISIBLE_LIMIT = 500


def fetch_series(
    symbol: str, tf: TimeFrame, htf: TimeFrame
) -> dict[TimeFrame, list[Candle]]:
    """As duas series, uma vez por (simbolo, timeframe).

    Todas as janelas do painel sao recortes desta mesma busca: uma janela extra
    nao custa requisicao nenhuma. E o que torna 72 simbolos viavel sem repetir
    o estouro de orcamento que ja rendeu um ban (`project_binance_ban_request_
    budget`).
    """
    provider = default_ohlcv_provider()
    return {
        tf: provider.get_ohlcv(symbol, tf, FETCH_LIMIT),
        htf: provider.get_ohlcv(symbol, htf, FETCH_LIMIT),
    }


def run_panel(
    symbols: tuple[str, ...], tf_names: tuple[str, ...], seed: int = 7
) -> dict[str, object]:
    rng = random.Random(seed)
    legs: list[dict[str, object]] = []
    diffs: Counter[str] = Counter()
    diff_details: list[dict[str, object]] = []
    baseline: list[dict[str, object]] = []
    live: list[dict[str, object]] = []
    errors: list[str] = []

    for symbol in symbols:
        for tf_name in tf_names:
            tf = TFS[tf_name]
            htf = _HIGHER_TIMEFRAME_MAP[tf]
            period = HTF_PERIOD[htf]
            try:
                series = fetch_series(symbol, tf, htf)
            except Exception as exc:  # noqa: BLE001 - um simbolo morto nao para o painel
                errors.append(f"{symbol} {tf_name}: {type(exc).__name__}: {exc}")
                continue
            if not series[htf] or len(series[tf]) < VISIBLE_LIMIT:
                errors.append(f"{symbol} {tf_name}: serie insuficiente")
                continue
            for w in range(WINDOWS):
                back = w * WINDOW_STEP
                cut = series[tf][-1 - back].timestamp if back < len(series[tf]) else None
                if cut is None:
                    continue
                provider = WindowProvider(series, cut=cut)
                try:
                    data = load_dashboard_data(
                        provider=provider, symbol=symbol, timeframe=tf,
                        limit=VISIBLE_LIMIT,
                        futures_provider=NoFuturesProvider(),
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{symbol} {tf_name} w{w}: {type(exc).__name__}: {exc}")
                    continue
                wname = f"w{w}"
                legacy_engine = LegacyHuntEngine()
                causal_engine = CausalHuntEngine(htf_period=period)
                l_rows = episode_rows(data, legacy_engine, symbol, tf_name, wname)
                c_rows = episode_rows(data, causal_engine, symbol, tf_name, wname)
                counts, details = diff_streams(l_rows, c_rows)
                diffs.update(counts)
                diff_details.extend(details[:5])
                for leg in leg_causality(data, legacy_engine, period):
                    leg.update({"symbol": symbol, "tf": tf_name, "window": wname})
                    legs.append(leg)
                baseline.extend(baseline_rows(data, l_rows, "legacy", rng))
                baseline.extend(baseline_rows(data, c_rows, "causal", rng))
                live.append(
                    {
                        "symbol": symbol, "tf": tf_name, "window": wname,
                        "phase": data.liquidity_hunt.phase.value if data.liquidity_hunt else None,
                        "htf_scalar": data.higher_timeframe_direction.value,
                        "last_htf_candle": series[htf][-1].timestamp.isoformat()
                        if series[htf] else None,
                    }
                )
            print(f"  {symbol} {tf_name}: legs={len(legs)} diffs={sum(diffs.values())}", flush=True)
    return {
        "meta": {
            "generated": datetime.now(tz=UTC).isoformat(),
            "symbols": list(symbols), "timeframes": list(tf_names),
            "windows": WINDOWS, "window_step": WINDOW_STEP,
            "fetch_limit": FETCH_LIMIT, "visible_limit": VISIBLE_LIMIT,
            "seed": seed, "futures": "disabled (NoFuturesProvider)",
        },
        "legs": legs, "diffs": dict(diffs), "diff_details": diff_details,
        "baseline": baseline, "live": live, "errors": errors,
    }


# ---------------------------------------------------------------------------
# 7. Relatorio
# ---------------------------------------------------------------------------


def _pct(n: int, d: int) -> str:
    return f"{n/d:.1%}" if d else "-"


def report(payload: dict[str, object]) -> None:
    legs = payload["legs"]
    assert isinstance(legs, list)
    n = len(legs)
    print("\n" + "=" * 68)
    print("H1 - CAUSALIDADE DO JOIN HTF/LTF NO HUNT")
    print("=" * 68)
    meta = payload["meta"]
    assert isinstance(meta, dict)
    print(f"painel: {len(meta['symbols'])} simbolos x {meta['timeframes']} x "
          f"{meta['windows']} janelas | futuros: {meta['futures']}")
    errors = payload["errors"]
    assert isinstance(errors, list)
    if errors:
        print(f"erros: {len(errors)} (primeiros: {errors[:2]})")

    print(f"\n--- 9. CAUSALIDADE POR PERNA (n={n}) ---")
    if n:
        aberto = sum(1 for r in legs if r["usa_candle_aberto"])
        muda = sum(1 for r in legs if r["trend_muda"])
        stream_muda = sum(1 for r in legs if r["legacy_stream"] != r["causal_stream"])
        print(f"usa candle HTF EM FORMACAO : {aberto:5d}  ({_pct(aberto, n)})")
        print(f"trend HTF MUDA             : {muda:5d}  ({_pct(muda, n)})")
        print(f"hunt <-> continuation      : {stream_muda:5d}  ({_pct(stream_muda, n)})")
        print("  (hunted_side segue o trend HTF: toda perna que muda de trend"
              " muda de lado cacado)")
        leads = [r["lead_segundos"] for r in legs if r["lead_segundos"]]
        lc = [r["lead_candles_ltf"] for r in legs if r["lead_candles_ltf"]]
        if leads:
            print(f"lead artificial (tempo)    : mediana {median(leads)/3600:.2f}h  "
                  f"max {max(leads)/3600:.2f}h")
        if lc:
            print(f"lead artificial (candles)  : mediana {median(lc):.1f}  max {max(lc):.1f}")
        by_tf: dict[str, list] = {}
        for r in legs:
            by_tf.setdefault(str(r["tf"]), []).append(r)
        print("\n  por timeframe:")
        for tf_name, rs in sorted(by_tf.items()):
            a = sum(1 for r in rs if r["usa_candle_aberto"])
            m = sum(1 for r in rs if r["trend_muda"])
            s = sum(1 for r in rs if r["legacy_stream"] != r["causal_stream"])
            print(f"    {tf_name:4s} n={len(rs):5d} | aberto {_pct(a, len(rs)):>6s} | "
                  f"trend muda {_pct(m, len(rs)):>6s} | stream muda {_pct(s, len(rs)):>6s}")

    print("\n--- 7. DIFF ESTRUTURAL DO STREAM DE EPISODIOS ---")
    diffs = payload["diffs"]
    assert isinstance(diffs, dict)
    total = sum(diffs.values())
    for cls in DIFF_CLASSES:
        v = diffs.get(cls, 0)
        print(f"  {cls:28s} {v:5d}  ({_pct(v, total)})")
    alterados = total - diffs.get("A_identico", 0)
    print(f"  {'TOTAL ALTERADO':28s} {alterados:5d}  ({_pct(alterados, total)})")

    print("\n--- 10. BASELINE: LEGACY vs CAUSAL ---")
    rows = payload["baseline"]
    assert isinstance(rows, list)
    for h in HORIZONS:
        print(f"\n  h={h} candles")
        print(f"    {'braco/stream':22s} {'n':>5s} {'MFE':>6s} {'ctl':>6s} "
              f"{'MAE':>6s} {'ctl':>6s} {'MFE/MAE':>8s} {'ctl':>6s} {'MFE>MAE':>8s} {'ctl':>7s}")
        for arm in ("legacy", "causal"):
            for stream in ("hunt", "continuation"):
                sel = [
                    r for r in rows
                    if r["arm"] == arm and r["stream"] == stream and f"mfe_{h}" in r
                ]
                if not sel:
                    print(f"    {arm+'/'+stream:22s} {0:5d}")
                    continue
                mfe = fmean(float(r[f"mfe_{h}"]) for r in sel)
                mae = fmean(float(r[f"mae_{h}"]) for r in sel)
                cm = fmean(float(r[f"ctl_mfe_{h}"]) for r in sel)
                ca = fmean(float(r[f"ctl_mae_{h}"]) for r in sel)
                win = fmean(1.0 if r[f"win_{h}"] else 0.0 for r in sel)
                cwin = fmean(float(r[f"ctl_win_{h}"]) for r in sel)
                ratio = mfe / mae if mae else 0.0
                cratio = cm / ca if ca else 0.0
                print(f"    {arm+'/'+stream:22s} {len(sel):5d} {mfe:6.2f} {cm:6.2f} "
                      f"{mae:6.2f} {ca:6.2f} {ratio:8.2f} {cratio:6.2f} "
                      f"{win:8.1%} {cwin:7.1%}")
    print("\n  Uma queda do CAUSAL em relacao ao LEGACY nao e regressao: e a")
    print("  medida de quanto do resultado historico vinha de informacao futura.")


def live_repaint(symbols: tuple[str, ...], tf_names: tuple[str, ...]) -> None:
    """O estado VIVO repinta por ler o candle HTF em formacao?

    Pergunta diferente da do historico, e nao deve ser misturada com ela.
    `build()` nao chama `_htf_trend_at`: usa o escalar
    `higher_timeframe_direction`, que e o `final_trend` do detector rodado sobre
    a serie HTF **incluindo o candle que ainda esta aberto**. Isso nao e
    lookahead -- ao vivo, a vela em formacao e informacao legitimamente
    disponivel agora -- mas e **repaint**: a leitura pode mudar quando aquela
    vela fechar, e o mesmo instante sera reclassificado depois.

    Aqui se mede a frequencia: a fase do HUNT com a HTF como o provider a
    entrega, contra a fase com o ultimo candio HTF removido.
    """
    mudou = igual = 0
    print("\n=== 13. ESTADO VIVO: repaint por candle HTF em formacao ===")
    for symbol in symbols:
        for tf_name in tf_names:
            tf = TFS[tf_name]
            htf = _HIGHER_TIMEFRAME_MAP[tf]
            try:
                series = fetch_series(symbol, tf, htf)
            except Exception as exc:  # noqa: BLE001
                print(f"  {symbol} {tf_name}: {type(exc).__name__}")
                continue
            reads = {}
            for tag, drop in (("com_forming", frozenset()), ("sem_forming", frozenset({htf}))):
                data = load_dashboard_data(
                    provider=WindowProvider(series, drop=drop), symbol=symbol,
                    timeframe=tf, limit=VISIBLE_LIMIT,
                    futures_provider=NoFuturesProvider(),
                )
                reads[tag] = (
                    data.higher_timeframe_direction.value,
                    data.liquidity_hunt.phase.value if data.liquidity_hunt else None,
                    data.liquidity_hunt.hunted_side.value if data.liquidity_hunt else None,
                )
            diff = reads["com_forming"] != reads["sem_forming"]
            mudou += diff
            igual += not diff
            flag = "  <== REPINTA" if diff else ""
            print(f"  {symbol:10s} {tf_name:4s} com={reads['com_forming']} "
                  f"sem={reads['sem_forming']}{flag}")
    total = mudou + igual
    print(f"\n  repinta em {mudou}/{total} ({mudou/total:.1%})" if total else "  n=0")


def case_timeline(symbol: str, tf_name: str, limit: int = 12) -> None:
    """Timeline de um caso: o que o LEGACY leu, o que o CAUSAL le, e o efeito.

    A Etapa 12 pede o caso nomeado, nao a estatistica: sem ver um evento HTF
    com open time, close time e o flip LTF caindo entre os dois, o numero
    agregado nao e verificavel por ninguem.
    """
    tf = TFS[tf_name]
    htf = _HIGHER_TIMEFRAME_MAP[tf]
    period = HTF_PERIOD[htf]
    data = load_dashboard_data(
        symbol=symbol, timeframe=tf, limit=VISIBLE_LIMIT,
        futures_provider=NoFuturesProvider(),
    )
    legacy = LegacyHuntEngine()
    print(f"\n=== {symbol} {tf_name} (HTF {htf.value}, periodo {period}) ===")
    rows = leg_causality(data, legacy, period)
    mudou = [r for r in rows if r["trend_muda"]]
    print(f"pernas={len(rows)}  usam candle aberto="
          f"{sum(1 for r in rows if r['usa_candle_aberto'])}  trend muda={len(mudou)}")
    for r in rows[-limit:]:
        mark = "  <== MUDA" if r["trend_muda"] else ""
        lead = (
            f" lead={r['lead_segundos']/3600:.2f}h"
            if r["lead_segundos"] else ""
        )
        print(f"  flip LTF {r['leg_start']} dir={r['ltf_direction']:8s} | "
              f"legacy={r['legacy_htf']:8s} -> {r['legacy_stream']:12s} | "
              f"causal={r['causal_htf']:8s} -> {r['causal_stream']:12s}{lead}{mark}")
    for r in mudou:
        at = datetime.fromisoformat(str(r["leg_start"]))
        used = [e for e in data.higher_timeframe_events if e.timestamp <= at]
        if not used:
            continue
        last = max(used, key=lambda e: e.timestamp)
        print(f"\n  evento HTF decisivo para a perna {r['leg_start']}:")
        print(f"    {last.event.value} {last.direction.value} @ open {last.timestamp}")
        print(f"    fecha em {last.timestamp + period}  (o flip LTF ocorreu em {at})")
        print(f"    -> o LEGACY o usou {(last.timestamp + period - at)} antes de existir")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--report-only", type=Path)
    parser.add_argument("--symbols", type=int, default=len(UNIVERSE),
                        help="quantos simbolos do UNIVERSE (default: todos)")
    parser.add_argument("--tfs", default="M15,H1,H4")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--case", help="SIMBOLO:TF, ex BTCUSDT:M15")
    parser.add_argument("--live", action="store_true", help="checar repaint do estado vivo")
    args = parser.parse_args()

    if args.report_only:
        report(json.loads(args.report_only.read_text()))
        return
    if args.case:
        symbol, tf_name = args.case.split(":")
        case_timeline(symbol, tf_name)
        return
    if args.live:
        live_repaint(UNIVERSE[: args.symbols], tuple(args.tfs.split(",")))
        return
    payload = run_panel(
        UNIVERSE[: args.symbols], tuple(args.tfs.split(",")), seed=args.seed
    )
    if args.out:
        args.out.write_text(json.dumps(payload, indent=1))
        print(f"\nescrito: {args.out}")
    report(payload)


if __name__ == "__main__":
    main()
