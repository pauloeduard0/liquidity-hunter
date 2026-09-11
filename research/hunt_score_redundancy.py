"""H2: o score ponderado do HUNT soma evidencias independentes ou a mesma?

O `LiquidityHuntEngine` decide um grab somando pesos de ate dez fontes
(`realignment` 4, `raid` 4, `vsa` 3-4, `sweep` 3, `oi_flush` 3, `supertrend` 3,
`zone` 2, `delta` 1) e comparando com um limiar (7 no hunt contra-tendencia, 4
na continuation). A soma so significa "confluencia" se as parcelas forem
leituras *diferentes* do mercado. Se `raid` e `zone` sao o mesmo pool descrito
duas vezes, ou se `sweep` e `realignment` sao a mesma quebra, o score nao mede
confluencia: mede quantos detectores olharam para o mesmo candle.

Este arquivo **so mede**. Nao muda peso, limiar, pool, gate nem semantica.

Metodo, e por que ele e assim:

- A unidade e o **cluster de captura**, nao o candle. O cluster e a unidade em
  que o motor de fato soma (`_capture_grabs` funde sinais dentro de
  `_GRAB_MERGE_CANDLES`), entao qualquer co-ocorrencia medida em outra unidade
  responderia uma pergunta que a producao nao faz.
- Os clusters sao lidos **de dentro da producao**, instrumentando
  `_capture_grabs` (ver `AuditHuntEngine`): a leg segmentation, o `start`/`end`
  de cada perna, o `threshold`, o `require_vsa` e o `allow_raid` sao os de
  producao porque sao os argumentos que a producao passou. O metodo devolve
  literalmente `super()._capture_grabs(...)`, entao o stream de episodios do
  motor auditado e o stream de producao por construcao -- e um teste de
  equivalencia confere que a decomposicao paralela concorda com ele.
- Clusters **reprovados** sao guardados junto com os aprovados, com o motivo
  (`floor_signature`, `raid_only`, `below_threshold`). Sem eles nao ha como
  responder se um gate ajuda: um gate so pode ser avaliado contra o que ele
  removeu.
- Todo resultado futuro e comparado a um controle casado em simbolo,
  timeframe, **direcao** e janela (a licao que o `raid_reversal` custou).

Duas limitacoes declaradas de saida:

1. O painel amplo roda com `NoFuturesProvider` -- sem OI nao ha `oi_flush` nem
   `oi_covering`, e tres requisicoes por simbolo em 72 simbolos ja renderam um
   ban antes. A pergunta 8 ("OI adiciona algo?") e respondida por um sub-painel
   separado, pequeno e recente (`--oi`), com a cobertura reportada. Ausencia de
   OI no painel amplo **nao** e evidencia contra o OI.
2. `_collect_capture_signals` emite `FLUSH` e `COVERING` sob a *mesma* chave
   `"oi_flush"`, e o cluster colapsa fontes iguais com `max`. Entao o par
   `oi_flush x oi_covering` nao existe como co-ocorrencia no score: e uma fonte
   so. Isso e um achado sobre a arquitetura, nao uma falha da medicao, e por
   isso os sinais crus (antes do colapso) tambem sao guardados.

Run:
    poetry run python -m research.hunt_score_redundancy \
        --out research/hunt_score_redundancy_baseline.json
    poetry run python -m research.hunt_score_redundancy \
        --report-only research/hunt_score_redundancy_baseline.json
    poetry run python -m research.hunt_score_redundancy --case BTCUSDT:M15
    poetry run python -m research.hunt_score_redundancy --oi --symbols 12
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import fmean
from typing import Any

from liquidity_hunter.app.dashboard_data import (
    _HIGHER_TIMEFRAME_MAP,
    DashboardData,
    load_dashboard_data,
)
from liquidity_hunter.app.liquidity_hunt import (
    _CAPTURE_THRESHOLD,
    _CONTINUATION_CAPTURE_THRESHOLD,
    _FLOOR_SIGNATURE_SOURCES,
    _RAID_SHAPED_SOURCES,
    _WEIGHT_DELTA_MODIFIER,
    _WEIGHT_REALIGNMENT,
    LiquidityHuntEngine,
    _opposite,
)
from liquidity_hunter.core.domain import Candle, RetailPositioning
from liquidity_hunter.indicators.supertrend import true_range_series
from research._symbols import UNIVERSE, sample_of
from research.hunt_htf_causality import (
    TFS,
    NoFuturesProvider,
    WindowProvider,
    fetch_series,
)

#: Toda fonte que o motor sabe emitir. Escrita por extenso para que uma fonte
#: que nunca aparecer no painel apareca como *zero* no relatorio em vez de
#: sumir da tabela -- a diferenca entre "nao ocorre" e "nao foi medido".
SOURCES: tuple[str, ...] = (
    "realignment",
    "raid",
    "vsa",
    "sweep",
    "oi_flush",
    "supertrend",
    "zone",
    "delta",
)

#: Os pares que a auditoria H0 levantou como suspeitos, testados nominalmente
#: (item 8) alem da matriz completa.
SUSPECT_PAIRS: tuple[tuple[str, str], ...] = (
    ("raid", "sweep"),
    ("raid", "zone"),
    ("sweep", "zone"),
    ("realignment", "sweep"),
    ("raid", "supertrend"),
    ("vsa", "delta"),
)

HORIZONS = (5, 10, 20, 40)
_CONTROL_DRAWS = 20

#: Baldes de score pedidos no item 10. Abertos no topo porque um cluster com
#: todas as fontes chega a 24.
SCORE_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("4-5", 4.0, 6.0),
    ("6", 6.0, 7.0),
    ("7", 7.0, 8.0),
    ("8-9", 8.0, 10.0),
    ("10-12", 10.0, 13.0),
    ("13+", 13.0, math.inf),
)

#: Deslocamentos em candles LTF para a redundancia temporal (item 4). O cluster
#: ja funde ate 3 candles, entao medir so o candle identico responderia uma
#: pergunta menor que a que a producao faz.
LAGS = (0, 1, 2, 3)


# ---------------------------------------------------------------------------
# 1. O cluster, lido de dentro da producao
# ---------------------------------------------------------------------------


@dataclass
class ClusterRow:
    """Um cluster de captura, aprovado ou nao, com tudo que o decidiu."""

    symbol: str
    tf: str
    window: str
    stream: str  # "hunt" | "continuation"
    leg_start: str
    leg_end: str
    anchor: str
    first_ts: str
    last_ts: str
    hunted_side: str
    capture_direction: str
    threshold: float
    require_vsa: bool
    allow_raid: bool
    #: fonte -> peso somado (apos o colapso `max` por tipo que a producao faz)
    by_source: dict[str, float]
    #: (timestamp iso, peso, fonte) antes do colapso -- e o unico lugar em que
    #: um FLUSH ainda e distinguivel de um COVERING, e onde a distancia em
    #: candles entre duas fontes do mesmo cluster ainda existe.
    raw: list[tuple[str, float, str]]
    score: float
    passed: bool
    reason: str  # "" quando passou
    oi_available: bool

    @property
    def sources(self) -> frozenset[str]:
        return frozenset(self.by_source)

    @property
    def n_sources(self) -> int:
        return len(self.by_source)


def decompose(
    engine: LiquidityHuntEngine,
    data: DashboardData,
    signals: list[tuple[datetime, float, str]],
    merge_gap: timedelta | None,
    capture_direction: Any,
    threshold: float,
    require_vsa: bool,
) -> list[tuple[dict[str, float], list[tuple[datetime, float, str]], datetime, bool, str]]:
    """Reproduz o agrupamento e os gates de `_capture_grabs`, sem decidir nada.

    Espelha `LiquidityHuntEngine._capture_grabs` passo a passo, na **mesma
    ordem** -- fusao por `merge_gap`, colapso `max` por tipo, gate de assinatura
    de piso, modificador de delta (que entra *depois* do gate de piso e *antes*
    do guard anti-raid), guard anti-raid, limiar. A ordem importa: um cluster
    reprovado no piso nunca chega a ganhar delta, e trocar isso mudaria a
    contagem de quem cada gate remove.

    Existe uma copia porque a producao devolve so os aprovados, e a pergunta de
    H2 e sobre os reprovados tambem. Que a copia nao divergiu e verificado por
    teste (`test_decomposicao_reproduz_os_grabs_de_producao`), nao prometido
    aqui.
    """
    clusters: list[list[tuple[datetime, float, str]]] = []
    for signal in sorted(signals, key=lambda s: s[0]):
        if clusters and merge_gap is not None and signal[0] - clusters[-1][-1][0] <= merge_gap:
            clusters[-1].append(signal)
        else:
            clusters.append([signal])

    out = []
    for cluster in clusters:
        by_source: dict[str, float] = {}
        for _ts, weight, source in cluster:
            by_source[source] = max(by_source.get(source, 0.0), weight)
        first_ts, last_ts = cluster[0][0], cluster[-1][0]
        anchor = first_ts
        passed, reason = True, ""
        if require_vsa and not (_FLOOR_SIGNATURE_SOURCES & by_source.keys()):
            passed, reason = False, "floor_signature"
        else:
            if engine._delta_confirms(data, capture_direction, first_ts, last_ts):
                by_source["delta"] = _WEIGHT_DELTA_MODIFIER
            elif "raid" in by_source and engine._delta_confirms(
                data, _opposite(capture_direction), first_ts, last_ts
            ):
                by_source["delta"] = _WEIGHT_DELTA_MODIFIER
            if set(by_source) <= _RAID_SHAPED_SOURCES:
                passed, reason = False, "raid_only"
            elif sum(by_source.values()) < threshold:
                passed, reason = False, "below_threshold"
            if require_vsa:
                floor = [ts for ts, _w, s in cluster if s in ("vsa", "raid", "supertrend")]
                if floor:
                    anchor = min(floor)
        out.append((by_source, cluster, anchor, passed, reason))
    return out


class AuditHuntEngine(LiquidityHuntEngine):
    """Producao literal, que anota cada cluster que ela avaliou.

    Sobrescreve um unico metodo, e o devolve *inalterado*: o valor de retorno e
    `super()._capture_grabs(...)`. O motor auditado nao pode divergir da
    producao porque nao decide nada -- so escuta os argumentos com que ela foi
    chamada, que sao justamente o contexto (perna, limiar, gates) que uma
    reimplementacao teria de adivinhar.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.sink: list[tuple[dict[str, float], list[tuple[datetime, float, str]],
                              datetime, bool, str, dict[str, Any]]] = []
        self._ctx: dict[str, Any] = {}

    def _capture_grabs(  # type: ignore[override]
        self,
        data: DashboardData,
        hunted_short: bool,
        capture_direction: Any,
        start: datetime,
        end: datetime,
        merge_gap: timedelta | None,
        threshold: float = _CAPTURE_THRESHOLD,
        require_vsa: bool = False,
        realignment_ts: datetime | None = None,
        allow_raid: bool = True,
    ) -> list[tuple[datetime, float, list[str]]]:
        signals = self._collect_capture_signals(
            data, hunted_short, capture_direction, start, end, allow_raid=allow_raid
        )
        if realignment_ts is not None:
            signals.append((realignment_ts, _WEIGHT_REALIGNMENT, "realignment"))
        ctx = {
            "leg_start": start,
            "leg_end": end,
            "hunted_short": hunted_short,
            "capture_direction": capture_direction,
            "threshold": threshold,
            "require_vsa": require_vsa,
            "allow_raid": allow_raid,
            **self._ctx,
        }
        for entry in decompose(
            self, data, signals, merge_gap, capture_direction, threshold, require_vsa
        ):
            self.sink.append((*entry, ctx))
        return super()._capture_grabs(
            data,
            hunted_short,
            capture_direction,
            start,
            end,
            merge_gap,
            threshold,
            require_vsa,
            realignment_ts,
            allow_raid,
        )


def collect_clusters(
    data: DashboardData, symbol: str, tf: str, window: str
) -> list[ClusterRow]:
    """Todos os clusters avaliados pelos dois streams neste snapshot.

    O stream e determinado por qual `build_*` estava rodando, nao inferido do
    limiar: os dois limiares poderiam coincidir um dia, e o rotulo tem de
    continuar certo se coincidirem.
    """
    rows: list[ClusterRow] = []
    oi = data.oi_analysis is not None and bool(data.oi_analysis.qualified_events)
    for stream, method in (
        ("hunt", "build_history"),
        ("continuation", "build_continuation_history"),
    ):
        engine = AuditHuntEngine()
        engine._ctx = {"stream": stream}
        getattr(engine, method)(data)
        for by_source, cluster, anchor, passed, reason, ctx in engine.sink:
            # O primeiro argumento de `_capture_grabs` e o lado do WICK, nao o
            # lado cacado, e os dois so coincidem no hunt. Na continuation o
            # grab e o pullback *contra* a perna (`grab_up = htf is BEARISH`)
            # enquanto o lado cacado segue a HTF (`SHORT` sob HTF bullish), ou
            # seja: invertidos. Registrar o wick como lado cacado inverteria a
            # direcao de toda a excursao futura da continuation -- que e
            # exatamente o erro que o `raid_reversal` ensinou a procurar.
            hunted_short = bool(ctx["hunted_short"])
            if stream == "continuation":
                hunted_short = not hunted_short
            rows.append(
                ClusterRow(
                    symbol=symbol,
                    tf=tf,
                    window=window,
                    stream=stream,
                    leg_start=ctx["leg_start"].isoformat(),
                    leg_end=ctx["leg_end"].isoformat(),
                    anchor=anchor.isoformat(),
                    first_ts=cluster[0][0].isoformat(),
                    last_ts=cluster[-1][0].isoformat(),
                    hunted_side=(
                        RetailPositioning.SHORT if hunted_short else RetailPositioning.LONG
                    ).value,
                    capture_direction=ctx["capture_direction"].value,
                    threshold=float(ctx["threshold"]),
                    require_vsa=bool(ctx["require_vsa"]),
                    allow_raid=bool(ctx["allow_raid"]),
                    by_source=dict(by_source),
                    raw=[(ts.isoformat(), w, s) for ts, w, s in cluster],
                    score=sum(by_source.values()),
                    passed=passed,
                    reason=reason,
                    oi_available=oi,
                )
            )
    return rows


# ---------------------------------------------------------------------------
# 2. Co-ocorrencia (itens 3 e 4)
# ---------------------------------------------------------------------------


def cooccurrence(rows: list[ClusterRow]) -> dict[str, dict[str, float]]:
    """P(B|A), P(A|B), Jaccard e phi para cada par de fontes.

    Sobre os clusters **aprovados**: a pergunta e se o score de um grab soma
    evidencia repetida, e um cluster reprovado nunca virou score. Phi e a
    correlacao de Pearson entre dois indicadores binarios, entao ela pune o par
    que aparece junto so porque as duas fontes sao frequentes -- o que a
    contagem conjunta crua nao faz.
    """
    n = len(rows)
    out: dict[str, dict[str, float]] = {}
    if not n:
        return out
    present = {s: [s in r.sources for r in rows] for s in SOURCES}
    for i, a in enumerate(SOURCES):
        for b in SOURCES[i + 1 :]:
            na = sum(present[a])
            nb = sum(present[b])
            both = sum(1 for x, y in zip(present[a], present[b], strict=True) if x and y)
            union = na + nb - both
            denom = math.sqrt(na * nb * (n - na) * (n - nb))
            phi = ((both * n) - (na * nb)) / denom if denom > 0 else 0.0
            out[f"{a}|{b}"] = {
                "n_a": float(na),
                "n_b": float(nb),
                "n_both": float(both),
                "p_b_given_a": both / na if na else 0.0,
                "p_a_given_b": both / nb if nb else 0.0,
                "jaccard": both / union if union else 0.0,
                "phi": phi,
            }
    return out


def temporal_proximity(
    rows: list[ClusterRow], spacing: dict[str, float]
) -> dict[str, dict[str, float]]:
    """Distancia em candles LTF entre duas fontes dentro do mesmo cluster.

    Duas fontes no mesmo cluster nao estao necessariamente no mesmo candle: o
    cluster funde ate `_GRAB_MERGE_CANDLES`. Se um par esta quase sempre a zero
    candles de distancia, e o mesmo candle descrito duas vezes; se esta sempre a
    dois ou tres, e o mesmo *evento* deslocado, que continua sendo uma coisa so
    para efeito de contagem de evidencia.
    """
    acc: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        step = spacing.get(r.tf, 0.0)
        if step <= 0:
            continue
        by: dict[str, list[datetime]] = defaultdict(list)
        for ts, _w, s in r.raw:
            by[s].append(datetime.fromisoformat(ts))
        for i, a in enumerate(SOURCES):
            for b in SOURCES[i + 1 :]:
                if a not in by or b not in by:
                    continue
                gap = min(abs((x - y).total_seconds()) for x in by[a] for y in by[b])
                acc[f"{a}|{b}"].append(gap / step)
    out: dict[str, dict[str, float]] = {}
    for key, gaps in acc.items():
        out[key] = {
            "n": float(len(gaps)),
            "media_candles": fmean(gaps),
            **{f"pct_lag_{lag}": sum(1 for g in gaps if g <= lag) / len(gaps) for lag in LAGS},
        }
    return out


# ---------------------------------------------------------------------------
# 3. Resultado futuro (itens 5, 6, 10, 11)
# ---------------------------------------------------------------------------


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


def outcome_rows(
    data: DashboardData, rows: list[ClusterRow], rng: random.Random
) -> list[dict[str, Any]]:
    """Excursao futura de cada cluster, ancorada onde a producao ancora o grab.

    Ancorada no `anchor` -- o instante em que a producao diz que a caca
    terminou -- e nao no primeiro sinal, porque e esse instante que qualquer
    leitura derivada do HUNT usaria. Os clusters reprovados entram tambem, com
    o motivo: sem eles nao ha como perguntar se um gate removeu o que era pior.
    """
    candles = data.candles
    if len(candles) < max(HORIZONS) + 2:
        return []
    atr = fmean(true_range_series(candles))
    index = {c.timestamp: i for i, c in enumerate(candles)}
    out: list[dict[str, Any]] = []
    for r in rows:
        i = index.get(datetime.fromisoformat(r.anchor))
        if i is None:
            continue
        up = r.hunted_side == RetailPositioning.SHORT.value
        rec: dict[str, Any] = {
            "symbol": r.symbol,
            "tf": r.tf,
            "window": r.window,
            "stream": r.stream,
            "sample": sample_of(r.symbol),
            "up": up,
            "score": r.score,
            "sources": sorted(r.by_source),
            "n_sources": r.n_sources,
            "passed": r.passed,
            "reason": r.reason,
            "oi_available": r.oi_available,
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


def _stats(sel: list[dict[str, Any]], h: int) -> dict[str, float] | None:
    sel = [r for r in sel if f"mfe_{h}" in r]
    if not sel:
        return None
    mfe = fmean(float(r[f"mfe_{h}"]) for r in sel)
    mae = fmean(float(r[f"mae_{h}"]) for r in sel)
    return {
        "n": float(len(sel)),
        "mfe": mfe,
        "mae": mae,
        "ratio": mfe / mae if mae else 0.0,
        "win": fmean(1.0 if r[f"win_{h}"] else 0.0 for r in sel),
        "ctl_mfe": fmean(float(r[f"ctl_mfe_{h}"]) for r in sel),
        "ctl_mae": fmean(float(r[f"ctl_mae_{h}"]) for r in sel),
        "ctl_win": fmean(float(r[f"ctl_win_{h}"]) for r in sel),
    }


def _bucket_of(score: float) -> str:
    for name, lo, hi in SCORE_BUCKETS:
        if lo <= score < hi:
            return name
    return "<4"


def incremental_value(rows: list[dict[str, Any]], h: int = 20) -> dict[str, dict[str, float]]:
    """Ganho de A **dentro** de estratos de composicao equivalente.

    Uma fonte pode medir bem sozinha so por aparecer em clusters que ja eram
    bons por outro motivo. O estrato casa stream, timeframe, direcao e balde de
    score, e a diferenca e a media ponderada por estrato das diferencas dentro
    dele -- entao um estrato que so tem clusters com A nao contribui, em vez de
    contribuir com o proprio nivel.
    """
    out: dict[str, dict[str, float]] = {}
    for source in SOURCES:
        strata: dict[tuple[Any, ...], tuple[list[float], list[float]]] = defaultdict(
            lambda: ([], [])
        )
        for r in rows:
            if f"win_{h}" not in r:
                continue
            key = (r["stream"], r["tf"], r["up"], _bucket_of(float(r["score"])))
            strata[key][0 if source in r["sources"] else 1].append(
                1.0 if r[f"win_{h}"] else 0.0
            )
        num = den = 0.0
        n_with = n_without = 0
        for withs, withouts in strata.values():
            if not withs or not withouts:
                continue
            weight = float(min(len(withs), len(withouts)))
            num += weight * (fmean(withs) - fmean(withouts))
            den += weight
            n_with += len(withs)
            n_without += len(withouts)
        out[source] = {
            "delta_win": num / den if den else 0.0,
            "n_com": float(n_with),
            "n_sem": float(n_without),
            "estratos": float(sum(1 for w, o in strata.values() if w and o)),
        }
    return out


# ---------------------------------------------------------------------------
# 4. Ablation (item 7)
# ---------------------------------------------------------------------------


def ablate(source: str) -> type[LiquidityHuntEngine]:
    """Um motor identico a producao, cego para **uma** fonte.

    A remocao e feita na emissao do sinal, nao no score: tirar o peso depois de
    somado deixaria a fonte ainda contando para o gate de assinatura de piso e
    para o guard anti-raid, e a pergunta do item 7 e o que acontece quando a
    evidencia nao existe. `zone` e removida como *evidencia* e nao como
    geometria -- os niveis continuam formando pools para o `raid`, porque o
    contrario mediria a remocao de duas fontes.
    """

    class Ablated(LiquidityHuntEngine):
        def _collect_capture_signals(  # type: ignore[override]
            self, *args: Any, **kwargs: Any
        ) -> list[tuple[datetime, float, str]]:
            signals = super()._collect_capture_signals(*args, **kwargs)
            return [s for s in signals if s[2] != source]

        def _capture_grabs(  # type: ignore[override]
            self, *args: Any, **kwargs: Any
        ) -> list[tuple[datetime, float, list[str]]]:
            if source == "realignment":
                kwargs["realignment_ts"] = None
                if len(args) >= 9:
                    args = (*args[:8], None, *args[9:])
            return super()._capture_grabs(*args, **kwargs)

        @staticmethod
        def _delta_confirms(*args: Any, **kwargs: Any) -> bool:
            if source == "delta":
                return False
            return LiquidityHuntEngine._delta_confirms(*args, **kwargs)

    Ablated.__name__ = f"Ablated_{source}"
    return Ablated


def _episode_ids(data: DashboardData, engine: LiquidityHuntEngine) -> dict[tuple[str, str], float]:
    """Episodios por (stream, timestamp de captura) -> score.

    A captura e a identidade, pelo mesmo motivo de H1: o inicio de um episodio
    e o grab anterior, e se desloca quando o vizinho muda.
    """
    out: dict[tuple[str, str], float] = {}
    for stream, method in (
        ("hunt", "build_history"),
        ("continuation", "build_continuation_history"),
    ):
        for e in getattr(engine, method)(data):
            out[(stream, e.end_timestamp.isoformat())] = e.capture_score
    return out


def ablation_effect(data: DashboardData) -> dict[str, dict[str, Any]]:
    """Quantos episodios somem, sobrevivem ou mudam quando a fonte some."""
    base = _episode_ids(data, LiquidityHuntEngine())
    out: dict[str, dict[str, Any]] = {}
    for source in SOURCES:
        after = _episode_ids(data, ablate(source)())
        sumiram = [k for k in base if k not in after]
        surgiram = [k for k in after if k not in base]
        mudaram = [k for k in base.keys() & after.keys() if base[k] != after[k]]
        out[source] = {
            "base": len(base),
            "sobrevivem": len(base) - len(sumiram),
            "somem": len(sumiram),
            "surgem": len(surgiram),
            "score_muda": len(mudaram),
            "somem_ids": [list(k) for k in sumiram[:5]],
        }
    return out


# ---------------------------------------------------------------------------
# 5. Familias derivadas dos dados (item 9)
# ---------------------------------------------------------------------------


def derive_families(matrix: dict[str, dict[str, float]], threshold: float = 0.5) -> list[list[str]]:
    """Agrupa fontes por phi, sem impor uma taxonomia antes de medir.

    Union-find guloso: duas fontes cuja correlacao binaria passa `threshold`
    caem na mesma familia, e a transitividade e aceita de proposito -- se A e o
    mesmo evento que B e B o mesmo que C, contar tres evidencias e contar uma
    tres vezes ainda que A e C raramente se encontrem.
    """
    parent = {s: s for s in SOURCES}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for key, stats in matrix.items():
        a, b = key.split("|")
        if stats["phi"] >= threshold:
            parent[find(a)] = find(b)
    groups: dict[str, list[str]] = defaultdict(list)
    for s in SOURCES:
        groups[find(s)].append(s)
    return sorted(groups.values(), key=len, reverse=True)


def effective_sources(row: ClusterRow, families: list[list[str]]) -> int:
    """Quantas familias distintas de evidencia o cluster carrega."""
    return sum(1 for fam in families if any(s in row.sources for s in fam))


# ---------------------------------------------------------------------------
# 6. O painel
# ---------------------------------------------------------------------------

WINDOWS = 3
WINDOW_STEP = 150
FETCH_LIMIT = 1000
VISIBLE_LIMIT = 500

#: Segundos por candle de cada timeframe do painel, para a redundancia
#: temporal. Local: a pesquisa mede em candles do grafico, nao em minutos.
SPACING: dict[str, float] = {"M15": 900.0, "H1": 3600.0, "H4": 14400.0}


def run_panel(
    symbols: tuple[str, ...],
    tf_names: tuple[str, ...],
    seed: int = 7,
    with_oi: bool = False,
    windows: int = WINDOWS,
) -> dict[str, Any]:
    rng = random.Random(seed)
    clusters: list[ClusterRow] = []
    outcomes: list[dict[str, Any]] = []
    ablation: Counter[str] = Counter()
    ablation_detail: list[dict[str, Any]] = []
    errors: list[str] = []
    coverage: Counter[str] = Counter()

    for symbol in symbols:
        for tf_name in tf_names:
            tf = TFS[tf_name]
            htf = _HIGHER_TIMEFRAME_MAP[tf]
            try:
                series = fetch_series(symbol, tf, htf)
            except Exception as exc:  # noqa: BLE001 - um simbolo morto nao para o painel
                errors.append(f"{symbol} {tf_name}: {type(exc).__name__}: {exc}")
                continue
            if not series[htf] or len(series[tf]) < VISIBLE_LIMIT:
                errors.append(f"{symbol} {tf_name}: serie insuficiente")
                continue
            for w in range(windows):
                back = w * WINDOW_STEP
                if back >= len(series[tf]):
                    continue
                cut = series[tf][-1 - back].timestamp
                try:
                    data = load_dashboard_data(
                        provider=WindowProvider(series, cut=cut),
                        symbol=symbol,
                        timeframe=tf,
                        limit=VISIBLE_LIMIT,
                                        futures_provider=None if with_oi else NoFuturesProvider(),
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{symbol} {tf_name} w{w}: {type(exc).__name__}: {exc}")
                    continue
                wname = f"w{w}"
                rows = collect_clusters(data, symbol, tf_name, wname)
                clusters.extend(rows)
                outcomes.extend(outcome_rows(data, rows, rng))
                coverage[f"{tf_name}:{'oi' if (rows and rows[0].oi_available) else 'sem_oi'}"] += 1
                for source, eff in ablation_effect(data).items():
                    ablation[f"{source}:base"] += int(eff["base"])
                    ablation[f"{source}:somem"] += int(eff["somem"])
                    ablation[f"{source}:surgem"] += int(eff["surgem"])
                    ablation[f"{source}:score_muda"] += int(eff["score_muda"])
                    if eff["somem_ids"]:
                        ablation_detail.append(
                            {"symbol": symbol, "tf": tf_name, "window": wname,
                             "source": source, "somem": eff["somem_ids"][:2]}
                        )
            print(f"  {symbol} {tf_name}: clusters={len(clusters)}", flush=True)

    return {
        "meta": {
            "generated": datetime.now(tz=UTC).isoformat(),
            "symbols": list(symbols),
            "timeframes": list(tf_names),
            "windows": windows,
            "window_step": WINDOW_STEP,
            "visible_limit": VISIBLE_LIMIT,
            "seed": seed,
            "futures": "enabled" if with_oi else "disabled (NoFuturesProvider)",
            "threshold_hunt": _CAPTURE_THRESHOLD,
            "threshold_continuation": _CONTINUATION_CAPTURE_THRESHOLD,
        },
        "clusters": [c.__dict__ for c in clusters],
        "outcomes": outcomes,
        "ablation": dict(ablation),
        "ablation_detail": ablation_detail[:60],
        "coverage": dict(coverage),
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# 7. Relatorio
# ---------------------------------------------------------------------------


def _rows_of(payload: dict[str, Any]) -> list[ClusterRow]:
    return [ClusterRow(**c) for c in payload["clusters"]]


def _pct(n: float, d: float) -> str:
    return f"{n/d:.1%}" if d else "-"


def report(payload: dict[str, Any]) -> None:  # noqa: PLR0915 - um relatorio por item
    rows = _rows_of(payload)
    outcomes: list[dict[str, Any]] = payload["outcomes"]
    meta = payload["meta"]
    print("\n" + "=" * 74)
    print("H2.0 - REDUNDANCIA E QUALIDADE DO SCORE DO HUNT")
    print("=" * 74)
    print(f"painel: {len(meta['symbols'])} simbolos x {meta['timeframes']} x "
          f"{meta['windows']} janelas | futuros: {meta['futures']}")
    if payload["errors"]:
        print(f"erros: {len(payload['errors'])} (primeiros: {payload['errors'][:2]})")
    print(f"clusters avaliados: {len(rows)}")

    for stream in ("hunt", "continuation"):
        sel = [r for r in rows if r.stream == stream]
        ok = [r for r in sel if r.passed]
        thr = meta["threshold_hunt"] if stream == "hunt" else meta["threshold_continuation"]
        print(f"\n{'='*74}\nSTREAM {stream.upper()}  (limiar {thr})")
        print(f"{'='*74}")
        print(f"clusters={len(sel)}  aprovados={len(ok)} ({_pct(len(ok), len(sel))})")
        motivos = Counter(r.reason for r in sel if not r.passed)
        for reason, k in motivos.most_common():
            print(f"  reprovado por {reason:16s} {k:6d}  ({_pct(k, len(sel))})")
        if not ok:
            continue

        print("\n--- 3. FREQUENCIA POR FONTE (clusters aprovados) ---")
        for s in SOURCES:
            k = sum(1 for r in ok if s in r.sources)
            print(f"  {s:12s} {k:6d}  ({_pct(k, len(ok))})")

        print("\n--- 3. PARES: co-ocorrencia (ordenado por phi) ---")
        matrix = cooccurrence(ok)
        print(f"  {'par':26s} {'n_amb':>6s} {'P(B|A)':>7s} {'P(A|B)':>7s} "
              f"{'Jacc':>6s} {'phi':>6s}")
        for key, st in sorted(matrix.items(), key=lambda kv: -kv[1]["phi"])[:12]:
            print(f"  {key:26s} {int(st['n_both']):6d} {st['p_b_given_a']:7.2f} "
                  f"{st['p_a_given_b']:7.2f} {st['jaccard']:6.2f} {st['phi']:6.2f}")

        print("\n--- 4. REDUNDANCIA TEMPORAL (distancia em candles LTF) ---")
        prox = temporal_proximity(ok, SPACING)
        print(f"  {'par':26s} {'n':>6s} {'media':>6s} " +
              " ".join(f"{'<=' + str(lag) + 'c':>6s}" for lag in LAGS))
        for key, st in sorted(prox.items(), key=lambda kv: -kv[1]["n"])[:12]:
            print(f"  {key:26s} {int(st['n']):6d} {st['media_candles']:6.2f} " +
                  " ".join(f"{st[f'pct_lag_{lag}']:6.0%}" for lag in LAGS))

        print("\n--- 8. PARES SUSPEITOS ---")
        for a, b in SUSPECT_PAIRS:
            st = matrix.get(f"{a}|{b}") or matrix.get(f"{b}|{a}")
            if st is None:
                continue
            sel_out = [r for r in outcomes if r["stream"] == stream and "win_20" in r]
            only_a = [r for r in sel_out if a in r["sources"] and b not in r["sources"]]
            only_b = [r for r in sel_out if b in r["sources"] and a not in r["sources"]]
            both = [r for r in sel_out if a in r["sources"] and b in r["sources"]]
            def w(xs: list[dict[str, Any]]) -> str:
                if not xs:
                    return "-"
                return f"{fmean(1.0 if x['win_20'] else 0.0 for x in xs):.1%}/{len(xs)}"
            print(f"  {a}+{b:12s} phi={st['phi']:5.2f} P(B|A)={st['p_b_given_a']:.2f} "
                  f"| ambos {w(both):>12s} so_A {w(only_a):>12s} so_B {w(only_b):>12s}")

        print("\n--- 9. FAMILIAS DERIVADAS (phi >= 0.5) ---")
        families = derive_families(matrix)
        for fam in families:
            print(f"  {' + '.join(fam)}")
        eff = Counter(effective_sources(r, families) for r in ok)
        print("  familias distintas por cluster: " +
              "  ".join(f"{k}:{v} ({_pct(v, len(ok))})" for k, v in sorted(eff.items())))
        dupes = sum(1 for r in ok if r.n_sources > effective_sources(r, families))
        print(f"  clusters com dupla contagem material: {dupes} ({_pct(dupes, len(ok))})")

        print("\n--- 12. FONTE UNICA PESADA ---")
        solo = [r for r in ok if r.n_sources <= 2 and "delta" in r.sources]
        one = [r for r in ok if r.n_sources == 1]
        print(f"  clusters de 1 fonte so : {len(one)} ({_pct(len(one), len(ok))})")
        print(f"  clusters fonte+delta   : {len(solo)} ({_pct(len(solo), len(ok))})")
        heavy = Counter(
            max(r.by_source, key=lambda s: r.by_source[s])
            for r in ok
            if r.by_source and max(r.by_source.values()) / r.score >= 0.5
        )
        print("  fonte que sozinha vale >=50% do score: " +
              "  ".join(f"{k}:{v}" for k, v in heavy.most_common()))

        print("\n--- 10/11. SCORE E NUMERO DE FONTES vs RESULTADO (h=20) ---")
        sel_out = [r for r in outcomes if r["stream"] == stream and r["passed"]]
        print(f"  {'balde score':14s} {'n':>6s} {'MFE':>6s} {'MAE':>6s} "
              f"{'MFE/MAE':>8s} {'MFE>MAE':>8s} {'ctl':>7s}")
        for name, _lo, _hi in SCORE_BUCKETS:
            st = _stats([r for r in sel_out if _bucket_of(float(r["score"])) == name], 20)
            if st:
                print(f"  {name:14s} {int(st['n']):6d} {st['mfe']:6.2f} {st['mae']:6.2f} "
                      f"{st['ratio']:8.2f} {st['win']:8.1%} {st['ctl_win']:7.1%}")
        print(f"  {'n fontes':14s}")
        for k in (1, 2, 3, 4):
            sub = [r for r in sel_out if (r["n_sources"] >= 4 if k == 4 else r["n_sources"] == k)]
            st = _stats(sub, 20)
            if st:
                label = "4+" if k == 4 else str(k)
                print(f"  {label:14s} {int(st['n']):6d} {st['mfe']:6.2f} {st['mae']:6.2f} "
                      f"{st['ratio']:8.2f} {st['win']:8.1%} {st['ctl_win']:7.1%}")

        print("\n--- 11. DESCONTINUIDADE EM TORNO DO LIMIAR ---")
        for label, lo, hi in (
            (f"score < {thr:.0f} (reprovado)", -math.inf, thr),
            (f"score == {thr:.0f}", thr, thr + 0.5),
            (f"score in ({thr:.0f}, {thr + 3:.0f})", thr + 0.5, thr + 3),
            (f"score >= {thr + 3:.0f}", thr + 3, math.inf),
        ):
            st = _stats(
                [
                    r
                    for r in outcomes
                    if r["stream"] == stream and lo <= float(r["score"]) < hi
                ],
                20,
            )
            if st:
                print(f"  {label:24s} n={int(st['n']):5d} MFE/MAE {st['ratio']:5.2f} "
                      f"acerto {st['win']:6.1%} (ctl {st['ctl_win']:.1%})")

        print("\n--- 5. FONTE ISOLADA vs CONTROLE (h=20, clusters aprovados) ---")
        print(f"  {'fonte':12s} {'n':>6s} {'MFE':>6s} {'MAE':>6s} {'MFE/MAE':>8s} "
              f"{'MFE>MAE':>8s} {'ctl':>7s}")
        for s in SOURCES:
            st = _stats([r for r in sel_out if s in r["sources"]], 20)
            if st:
                print(f"  {s:12s} {int(st['n']):6d} {st['mfe']:6.2f} {st['mae']:6.2f} "
                      f"{st['ratio']:8.2f} {st['win']:8.1%} {st['ctl_win']:7.1%}")

        print("\n--- 6. VALOR INCREMENTAL (estratos casados: tf x direcao x balde) ---")
        inc = incremental_value(sel_out, 20)
        for s, st in sorted(inc.items(), key=lambda kv: -kv[1]["delta_win"]):
            if st["estratos"]:
                print(f"  {s:12s} delta_acerto {st['delta_win']:+6.1%}  "
                      f"(com {int(st['n_com'])}, sem {int(st['n_sem'])}, "
                      f"{int(st['estratos'])} estratos)")

        print("\n--- 12/13. GATES: o que eles removeram era pior? ---")
        for reason in ("floor_signature", "raid_only", "below_threshold"):
            st = _stats(
                [r for r in outcomes if r["stream"] == stream and r["reason"] == reason], 20
            )
            if st:
                print(f"  removidos por {reason:16s} n={int(st['n']):5d} "
                      f"MFE/MAE {st['ratio']:5.2f} acerto {st['win']:6.1%} "
                      f"(ctl {st['ctl_win']:.1%})")
        st = _stats([r for r in outcomes if r["stream"] == stream and r["passed"]], 20)
        if st:
            print(f"  {'ACEITOS':30s} n={int(st['n']):5d} MFE/MAE {st['ratio']:5.2f} "
                  f"acerto {st['win']:6.1%} (ctl {st['ctl_win']:.1%})")

        print("\n--- 18. HOLDOUT ---")
        for sample in ("search", "holdout"):
            sub = [r for r in sel_out if r["sample"] == sample]
            st = _stats(sub, 20)
            if st:
                print(f"  {sample:8s} n={int(st['n']):5d} acerto {st['win']:6.1%} "
                      f"(ctl {st['ctl_win']:.1%})")

    print("\n--- 7. ABLATION (episodios, painel inteiro) ---")
    abl = payload["ablation"]
    print(f"  {'fonte':12s} {'base':>7s} {'somem':>7s} {'%':>7s} {'surgem':>7s} "
          f"{'score muda':>11s}")
    for s in SOURCES:
        base = abl.get(f"{s}:base", 0)
        gone = abl.get(f"{s}:somem", 0)
        print(f"  {s:12s} {base:7d} {gone:7d} {_pct(gone, base):>7s} "
              f"{abl.get(f'{s}:surgem', 0):7d} {abl.get(f'{s}:score_muda', 0):11d}")

    print("\n--- 14. COBERTURA DE OI ---")
    cov = payload["coverage"]
    for key in sorted(cov):
        print(f"  {key:16s} {cov[key]:5d} janelas")
    if meta["futures"].startswith("disabled"):
        print("  OI DESLIGADO neste painel: ausencia de oi_flush aqui nao e evidencia"
              " contra o OI (rodar --oi).")


def case_report(symbol: str, tf_name: str) -> None:
    """Clusters reais de um simbolo, com fontes e timestamps (item 18).

    O agregado nao e verificavel sem ver um cluster: um score 7 feito de
    `raid + zone + delta` e um feito de `sweep + vsa + delta` sao o mesmo numero
    e leituras diferentes, e so a listagem mostra qual dos dois o painel contou.
    """
    tf = TFS[tf_name]
    data = load_dashboard_data(
        symbol=symbol, timeframe=tf, limit=VISIBLE_LIMIT,
        futures_provider=NoFuturesProvider(),
    )
    rows = collect_clusters(data, symbol, tf_name, "live")
    print(f"\n=== {symbol} {tf_name}: {len(rows)} clusters "
          f"({sum(1 for r in rows if r.passed)} aprovados) ===")
    for label, sel in (
        ("score alto, muitas fontes", sorted(
            (r for r in rows if r.passed), key=lambda r: (-r.n_sources, -r.score))[:3]),
        ("score no limiar exato", [r for r in rows if r.passed and r.score == r.threshold][:3]),
        ("removido pelo anti-raid-solitario", [r for r in rows if r.reason == "raid_only"][:3]),
        ("removido pela assinatura de piso",
         [r for r in rows if r.reason == "floor_signature"][:3]),
    ):
        print(f"\n  -- {label} --")
        for r in sel:
            parts = " ".join(f"{s}={w:.0f}" for s, w in sorted(r.by_source.items()))
            print(f"    {r.stream:12s} {r.anchor} score={r.score:5.1f} "
                  f"lado={r.hunted_side:7s} [{parts}]")
            for ts, weight, source in r.raw:
                print(f"        {ts}  {source:12s} {weight:.0f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--report-only", type=Path)
    parser.add_argument("--symbols", type=int, default=len(UNIVERSE))
    parser.add_argument("--tfs", default="M15,H1,H4")
    parser.add_argument("--windows", type=int, default=WINDOWS)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--case", help="SIMBOLO:TF, ex BTCUSDT:M15")
    parser.add_argument("--oi", action="store_true",
                        help="sub-painel COM futuros (poucos simbolos: 3 requisicoes cada)")
    args = parser.parse_args()

    if args.report_only:
        report(json.loads(args.report_only.read_text()))
        return
    if args.case:
        symbol, tf_name = args.case.split(":")
        case_report(symbol, tf_name)
        return
    payload = run_panel(
        UNIVERSE[: args.symbols],
        tuple(args.tfs.split(",")),
        seed=args.seed,
        with_oi=args.oi,
        windows=args.windows,
    )
    if args.out:
        args.out.write_text(json.dumps(payload, indent=1, default=str))
        print(f"\nescrito: {args.out}")
    report(payload)


if __name__ == "__main__":
    main()
