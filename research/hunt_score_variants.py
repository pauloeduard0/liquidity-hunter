"""H2.1: tres correcoes minimas do score do HUNT, uma variavel por vez.

A H2.0 deixou tres suspeitas concretas, e esta etapa testa exatamente essas
tres -- nao procura a melhor pontuacao possivel:

    V1  `raid` e `zone` no mesmo cluster contam uma vez (contribuicao conjunta
        `max(4, 2) = 4` em vez de 6). Motivo: phi 0,65, 70% no mesmo candle --
        e a mesma vela atravessando o mesmo pool, somada duas vezes.
    V2  continuation exige `n_unique_sources >= 2`. Motivo: 17% dos grabs
        aceitos sao um VSA forte sozinho, e eles medem 41,6% contra um controle
        de 50,8%.
    V3  limiar do hunt 7 -> 10. Motivo: score exatamente 7 mede 46,6% contra
        controle 50,6% -- abaixo do aleatorio -- enquanto a descontinuidade real
        aparece em 10.

Nada em `liquidity_hunter/` e tocado. Cada variante e uma subclasse que muda
**uma** regra e herda todo o resto: pesos, pools, join HTF, assinatura de piso,
guard anti-raid e a fusao em cluster continuam sendo os de producao.

Como as variantes conseguem ser minimas
---------------------------------------
`_capture_grabs` decide tudo dentro de um laco, sem gancho para o score. Em vez
de copia-lo por variante, todas passam por `VariantEngine`, que roda o
`decompose()` da H2.0 -- ja verificado episodio a episodio contra a producao --
e aplica sobre ele **so** o hook da variante. O controle disso e a `V0`, uma
variante que nao muda regra nenhuma: se `V0` nao reproduzir a producao byte a
byte, a maquinaria esta errada e nenhum numero desta etapa vale
(`test_v0_reproduz_a_producao`).

Duas decisoes de medicao que mudam o resultado
----------------------------------------------
1. **Deduplicacao entre janelas.** As tres janelas do painel sao cortes da
   mesma serie e se sobrepoem, entao o mesmo grab aparece ate tres vezes.
   Contar as tres inflaria o n e daria peso triplo ao trecho mais recente. Os
   episodios sao deduplicados por `(symbol, tf, stream, anchor)`.
2. **Holdout temporal, dentro de cada simbolo/TF.** O corte da H2.0 era por
   simbolo, e um corte por simbolo nao responde "isto sobrevive ao proximo
   regime". Aqui os 70% mais antigos de cada serie sao discovery e os 30% mais
   recentes sao holdout; a fronteira sai da **serie**, nao dos episodios, entao
   ela e identica em todos os bracos e nao se move quando uma variante remove
   um grab. O holdout por simbolo continua sendo reportado, como controle
   secundario.

Os valores das tres regras foram fixados pela hipotese **antes** de rodar
(item 14 do enunciado): se V3 falhar em 10, esta etapa nao testa 9 nem 11.

Run:
    poetry run python -m research.hunt_score_variants \
        --out research/hunt_score_variants_baseline.json
    poetry run python -m research.hunt_score_variants \
        --report-only research/hunt_score_variants_baseline.json
    poetry run python -m research.hunt_score_variants --case BTCUSDT:M15
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import fmean, median
from typing import Any

from liquidity_hunter.app.dashboard_data import (
    _HIGHER_TIMEFRAME_MAP,
    DashboardData,
    load_dashboard_data,
)
from liquidity_hunter.app.liquidity_hunt import (
    _CAPTURE_THRESHOLD,
    _WEIGHT_REALIGNMENT,
    LiquidityHuntEngine,
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
from research.hunt_score_redundancy import (
    HORIZONS,
    VISIBLE_LIMIT,
    WINDOW_STEP,
    WINDOWS,
    _excursion,
    decompose,
)

#: Quantos blocos temporais para o item 18. Quatro: o suficiente para um ganho
#: que so existe num regime aparecer como tal, e nao tantos que cada bloco fique
#: pequeno demais para ter opiniao.
BLOCKS = 4

#: Fracao mais antiga de cada serie que forma o discovery.
DISCOVERY_SHARE = 0.7

_CONTROL_DRAWS = 20


# ---------------------------------------------------------------------------
# 1. A maquinaria de variante
# ---------------------------------------------------------------------------


class VariantEngine(LiquidityHuntEngine):
    """Producao com **um** gancho aberto, e nada mais.

    Reconstroi a decisao de `_capture_grabs` a partir de `decompose()` em vez
    de copia-la: os gates de assinatura de piso e anti-raid continuam sendo os
    que `decompose` reproduz da producao (e que o teste de equivalencia da H2.0
    prende), e a variante so interfere depois deles, em tres pontos nomeados --
    o score efetivo, o limiar e um gate adicional.

    O stream nao e inferido do limiar recebido: os dois limiares poderiam
    coincidir e o rotulo continuaria tendo de estar certo. Ele e marcado por
    qual `build_*` esta rodando.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._stream = "hunt"

    # -- hooks ----------------------------------------------------------------

    def effective_score(self, by_source: dict[str, float]) -> float:
        """O score que a variante usa para decidir. Por padrao, o de producao."""
        return sum(by_source.values())

    def threshold_for(self, production_threshold: float) -> float:
        return production_threshold

    def extra_gate(self, by_source: dict[str, float]) -> bool:
        return True

    # -- producao, com os hooks aplicados -------------------------------------

    def build_history(self, data: DashboardData) -> list[Any]:
        self._stream = "hunt"
        return super().build_history(data)

    def build_continuation_history(self, data: DashboardData) -> list[Any]:
        self._stream = "continuation"
        return super().build_continuation_history(data)

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
        grabs: list[tuple[datetime, float, list[str]]] = []
        for by_source, _cluster, anchor, _passed, reason in decompose(
            self, data, signals, merge_gap, capture_direction, threshold, require_vsa
        ):
            # Os gates de producao ficam de pe; o limiar e reavaliado porque e
            # justamente o que V3 investiga, e `decompose` o aplicou com o valor
            # de producao.
            if reason in ("floor_signature", "raid_only"):
                continue
            if not self.extra_gate(by_source):
                continue
            if self.effective_score(by_source) < self.threshold_for(threshold):
                continue
            # A lista de fontes fica **intacta** para diagnostico (item 3): a
            # variante muda o score efetivo, nao a evidencia observada.
            grabs.append((anchor, self.effective_score(by_source), sorted(by_source)))
        return grabs


class V0(VariantEngine):
    """Nenhuma regra alterada. E o controle da maquinaria, nao um experimento."""


class V1(VariantEngine):
    """`raid` e `zone` no mesmo cluster contribuem uma vez.

    A co-ocorrencia e definida pela unidade que a producao ja usa -- o cluster
    -- e nao por uma janela nova: se o motor decidiu que os dois sinais sao o
    mesmo momento de grab, a hipotese e que eles nao deveriam somar duas vezes
    dentro dele. Desconta-se o **menor** dos dois pesos, que e o que torna a
    contribuicao conjunta `max(raid, zone)`.
    """

    def effective_score(self, by_source: dict[str, float]) -> float:
        total = sum(by_source.values())
        if "raid" in by_source and "zone" in by_source:
            total -= min(by_source["raid"], by_source["zone"])
        return total


class V2(VariantEngine):
    """Continuation exige duas fontes distintas. Gate adicional, nada mais.

    `delta` conta como fonte, que e a leitura que a H2.0 usou ao dizer "17% de
    fonte unica": um VSA forte com confirmacao de agressao sobrevive, um VSA
    forte sozinho nao.
    """

    def extra_gate(self, by_source: dict[str, float]) -> bool:
        if self._stream != "continuation":
            return True
        return len(by_source) >= 2


class V3(VariantEngine):
    """Limiar do hunt 7 -> 10. Pesos, gates e continuation intocados."""

    HUNT_THRESHOLD = 10.0

    def threshold_for(self, production_threshold: float) -> float:
        if self._stream != "hunt":
            return production_threshold
        return self.HUNT_THRESHOLD


class V1V3(V1, V3):
    """As duas regras do hunt juntas (item 15).

    Calculada sempre porque nao custa nada sobre o mesmo painel, mas so deve
    ser **interpretada** se V1 e V3 passarem sozinhas -- e o relatorio recusa a
    leitura quando nao passam.
    """


VARIANTS: dict[str, type[VariantEngine]] = {
    "V0": V0,
    "V1": V1,
    "V2": V2,
    "V3": V3,
    "V1V3": V1V3,
}

#: Qual stream cada variante pode alterar. Serve ao item 11 ("mesmo que a
#: continuation quase nao use raid, reportar explicitamente") e a uma checagem:
#: uma variante que mexer no stream que nao e dela e um bug, nao um achado.
VARIANT_SCOPE: dict[str, str] = {
    "V0": "nenhum",
    "V1": "ambos",
    "V2": "continuation",
    "V3": "hunt",
    "V1V3": "hunt",
}


# ---------------------------------------------------------------------------
# 2. Episodios, deduplicados e datados
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EpisodeKey:
    symbol: str
    tf: str
    stream: str
    anchor: str


def engine_episodes(
    engine: LiquidityHuntEngine, data: DashboardData, symbol: str, tf: str
) -> dict[EpisodeKey, dict[str, Any]]:
    out: dict[EpisodeKey, dict[str, Any]] = {}
    for stream, method in (
        ("hunt", "build_history"),
        ("continuation", "build_continuation_history"),
    ):
        for e in getattr(engine, method)(data):
            key = EpisodeKey(symbol, tf, stream, e.end_timestamp.isoformat())
            out[key] = {
                "start": e.start_timestamp.isoformat(),
                "score": e.capture_score,
                "sources": sorted(e.capture_sources),
                "hunted_side": e.hunted_side.value,
            }
    return out


def outcome_of(
    candles: list[Candle], anchor: datetime, up: bool, rng: random.Random
) -> dict[str, Any] | None:
    """Excursao futura e movimento liquido, contra controle casado na direcao."""
    index = {c.timestamp: i for i, c in enumerate(candles)}
    i = index.get(anchor)
    if i is None or len(candles) < max(HORIZONS) + 2:
        return None
    atr = fmean(true_range_series(candles))
    if atr <= 0:
        return None
    rec: dict[str, Any] = {}
    ok = False
    entry = candles[i].close
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
        end = candles[i + h].close
        rec[f"mfe_{h}"], rec[f"mae_{h}"] = real
        rec[f"net_{h}"] = ((end - entry) if up else (entry - end)) / atr
        rec[f"win_{h}"] = real[0] > real[1]
        rec[f"ctl_mfe_{h}"] = fmean(c[0] for c in ctl)
        rec[f"ctl_mae_{h}"] = fmean(c[1] for c in ctl)
        rec[f"ctl_win_{h}"] = fmean(1.0 if c[0] > c[1] else 0.0 for c in ctl)
    return rec if ok else None


# ---------------------------------------------------------------------------
# 3. O painel
# ---------------------------------------------------------------------------


def series_span(candles: list[Candle]) -> tuple[datetime, datetime]:
    """A janela de tempo que o painel realmente enxerga em (simbolo, tf).

    Sai da **serie**, nao dos episodios: se a fronteira discovery/holdout fosse
    calculada sobre os episodios de cada braco, uma variante que remove grabs
    moveria a propria fronteira, e os dois lados deixariam de ser comparaveis.
    """
    first = max(0, len(candles) - (VISIBLE_LIMIT + (WINDOWS - 1) * WINDOW_STEP))
    return candles[first].timestamp, candles[-1].timestamp


def run_panel(
    symbols: tuple[str, ...], tf_names: tuple[str, ...], seed: int = 7
) -> dict[str, Any]:
    rng = random.Random(seed)
    #: (variante, chave) -> episodio; deduplicado entre janelas por construcao
    episodes: dict[str, dict[str, dict[str, Any]]] = {v: {} for v in VARIANTS}
    outcomes: dict[str, dict[str, Any]] = {}
    spans: dict[str, list[str]] = {}
    errors: list[str] = []

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
            lo, hi = series_span(series[tf])
            spans[f"{symbol}:{tf_name}"] = [lo.isoformat(), hi.isoformat()]
            for w in range(WINDOWS):
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
                        compute_narrative=False,
                        futures_provider=NoFuturesProvider(),
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{symbol} {tf_name} w{w}: {type(exc).__name__}: {exc}")
                    continue
                for name, cls in VARIANTS.items():
                    for key, ep in engine_episodes(cls(), data, symbol, tf_name).items():
                        ident = f"{key.symbol}|{key.tf}|{key.stream}|{key.anchor}"
                        # Primeira janela que viu o grab manda: as janelas
                        # posteriores sao o mesmo trecho de fita re-observado.
                        episodes[name].setdefault(ident, {**ep, "window": f"w{w}"})
                        if ident not in outcomes:
                            up = ep["hunted_side"] == RetailPositioning.SHORT.value
                            res = outcome_of(
                                data.candles, datetime.fromisoformat(key.anchor), up, rng
                            )
                            if res is not None:
                                outcomes[ident] = {
                                    "symbol": symbol, "tf": tf_name,
                                    "stream": key.stream, "anchor": key.anchor,
                                    "up": up, "sample": sample_of(symbol), **res,
                                }
            print(
                f"  {symbol} {tf_name}: "
                + " ".join(f"{v}={len(episodes[v])}" for v in VARIANTS),
                flush=True,
            )

    return {
        "meta": {
            "generated": datetime.now(tz=UTC).isoformat(),
            "symbols": list(symbols),
            "timeframes": list(tf_names),
            "windows": WINDOWS,
            "seed": seed,
            "futures": "disabled (NoFuturesProvider)",
            "discovery_share": DISCOVERY_SHARE,
            "blocks": BLOCKS,
            "v3_threshold": V3.HUNT_THRESHOLD,
        },
        "episodes": episodes,
        "outcomes": outcomes,
        "spans": spans,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# 4. Cortes temporais
# ---------------------------------------------------------------------------


def _position(spans: dict[str, list[str]], symbol: str, tf: str, anchor: str) -> float | None:
    """Onde o grab cai dentro da serie do seu proprio (simbolo, tf), em [0, 1]."""
    span = spans.get(f"{symbol}:{tf}")
    if not span:
        return None
    lo, hi = datetime.fromisoformat(span[0]), datetime.fromisoformat(span[1])
    total = (hi - lo).total_seconds()
    if total <= 0:
        return None
    at = datetime.fromisoformat(anchor)
    return min(1.0, max(0.0, (at - lo).total_seconds() / total))


def annotate(payload: dict[str, Any]) -> None:
    """Marca cada outcome com sua fatia temporal e seu bloco."""
    spans = payload["spans"]
    for ident, row in payload["outcomes"].items():
        pos = _position(spans, row["symbol"], row["tf"], row["anchor"])
        row["pos"] = pos
        if pos is None:
            row["split"] = None
            row["block"] = None
            continue
        row["split"] = "discovery" if pos < DISCOVERY_SHARE else "holdout"
        row["block"] = min(BLOCKS - 1, int(pos * BLOCKS))
        assert ident


# ---------------------------------------------------------------------------
# 5. Estatistica
# ---------------------------------------------------------------------------


def _agg(rows: list[dict[str, Any]], h: int = 20) -> dict[str, float] | None:
    rows = [r for r in rows if f"mfe_{h}" in r]
    if not rows:
        return None
    mfe = fmean(float(r[f"mfe_{h}"]) for r in rows)
    mae = fmean(float(r[f"mae_{h}"]) for r in rows)
    return {
        "n": float(len(rows)),
        "mfe": mfe,
        "mae": mae,
        "ratio": mfe / mae if mae else 0.0,
        "net": fmean(float(r[f"net_{h}"]) for r in rows),
        "win": fmean(1.0 if r[f"win_{h}"] else 0.0 for r in rows),
        "ctl_win": fmean(float(r[f"ctl_win_{h}"]) for r in rows),
    }


def rows_for(payload: dict[str, Any], variant: str, stream: str) -> list[dict[str, Any]]:
    eps = payload["episodes"][variant]
    return [
        payload["outcomes"][i]
        for i in eps
        if i in payload["outcomes"] and payload["outcomes"][i]["stream"] == stream
    ]


def removed_by(payload: dict[str, Any], variant: str, stream: str) -> list[dict[str, Any]]:
    base = set(payload["episodes"]["V0"])
    var = set(payload["episodes"][variant])
    return [
        payload["outcomes"][i]
        for i in base - var
        if i in payload["outcomes"] and payload["outcomes"][i]["stream"] == stream
    ]


def _fmt(st: dict[str, float] | None) -> str:
    if st is None:
        return f"{'-':>44s}"
    return (
        f"n={int(st['n']):5d} MFE {st['mfe']:5.2f} MAE {st['mae']:5.2f} "
        f"R {st['ratio']:5.2f} net {st['net']:+5.2f} acerto {st['win']:6.1%} "
        f"(ctl {st['ctl_win']:.1%})"
    )


def _delta_win(base: dict[str, float] | None, var: dict[str, float] | None) -> float | None:
    if base is None or var is None:
        return None
    return var["win"] - base["win"]


# ---------------------------------------------------------------------------
# 6. Relatorio
# ---------------------------------------------------------------------------


def _section(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def report_variant(payload: dict[str, Any], variant: str) -> dict[str, Any]:  # noqa: PLR0915
    scope = VARIANT_SCOPE[variant]
    _section(f"{variant}  (stream afetado por hipotese: {scope})")
    verdict: dict[str, Any] = {"variant": variant}

    for stream in ("hunt", "continuation"):
        base_ids = {
            i for i in payload["episodes"]["V0"]
            if payload["outcomes"].get(i, {}).get("stream") == stream
        }
        var_ids = {
            i for i in payload["episodes"][variant]
            if payload["outcomes"].get(i, {}).get("stream") == stream
        }
        n_base_all = sum(1 for i in payload["episodes"]["V0"] if f"|{stream}|" in i)
        n_var_all = sum(1 for i in payload["episodes"][variant] if f"|{stream}|" in i)
        gone = base_ids - var_ids
        novos = var_ids - base_ids
        score_muda = [
            i for i in base_ids & var_ids
            if payload["episodes"]["V0"][i]["score"] != payload["episodes"][variant][i]["score"]
        ]
        inicio_muda = [
            i for i in base_ids & var_ids
            if payload["episodes"]["V0"][i]["start"] != payload["episodes"][variant][i]["start"]
        ]
        print(f"\n--- {stream} ---")
        print(f"  episodios: baseline {n_base_all} -> variante {n_var_all}  "
              f"(cobertura {n_var_all / n_base_all:.1%})" if n_base_all else "  episodios: 0")
        print(f"  removidos {len(gone)} | preservados {len(base_ids & var_ids)} | "
              f"surgem {len(novos)} | score alterado {len(score_muda)} | "
              f"inicio deslocado {len(inicio_muda)}")

        base_rows = rows_for(payload, "V0", stream)
        var_rows = rows_for(payload, variant, stream)
        gone_rows = [payload["outcomes"][i] for i in gone]
        print(f"  baseline    {_fmt(_agg(base_rows))}")
        print(f"  variante    {_fmt(_agg(var_rows))}")
        print(f"  REMOVIDOS   {_fmt(_agg(gone_rows))}")

        if not gone and stream != scope and scope != "ambos":
            print("  (nenhuma mudanca -- esperado: a hipotese nao alcanca este stream)")
            continue

        for split in ("discovery", "holdout"):
            b = _agg([r for r in base_rows if r.get("split") == split])
            v = _agg([r for r in var_rows if r.get("split") == split])
            d = _delta_win(b, v)
            print(f"  [{split:9s}] base {_fmt(b)}")
            print(f"  [{split:9s}] var  {_fmt(v)}"
                  + (f"   delta {d:+.1%}" if d is not None else ""))
            if split == "holdout":
                verdict[f"{stream}_holdout_delta"] = d
            else:
                verdict[f"{stream}_discovery_delta"] = d

        b = _agg([r for r in base_rows if r["sample"] == "holdout"])
        v = _agg([r for r in var_rows if r["sample"] == "holdout"])
        d = _delta_win(b, v)
        print(f"  [holdout por SIMBOLO] delta {d:+.1%}" if d is not None
              else "  [holdout por SIMBOLO] -")

        print("  por timeframe:")
        for tf in ("M15", "H1", "H4"):
            b = _agg([r for r in base_rows if r["tf"] == tf])
            v = _agg([r for r in var_rows if r["tf"] == tf])
            d = _delta_win(b, v)
            print(f"    {tf:4s} base {b['win']:6.1%} -> var {v['win']:6.1%} "
                  f"({int(v['n']):4d}) delta {d:+.1%}" if b and v and d is not None
                  else f"    {tf:4s} -")

        print("  por direcao:")
        for label, up in (("alta (shorts cacados)", True), ("baixa (longs cacados)", False)):
            b = _agg([r for r in base_rows if r["up"] is up])
            v = _agg([r for r in var_rows if r["up"] is up])
            d = _delta_win(b, v)
            print(f"    {label:24s} base {b['win']:6.1%} -> var {v['win']:6.1%} "
                  f"({int(v['n']):4d}) delta {d:+.1%}" if b and v and d is not None
                  else f"    {label:24s} -")

        print("  por bloco temporal:")
        for blk in range(BLOCKS):
            b = _agg([r for r in base_rows if r.get("block") == blk])
            v = _agg([r for r in var_rows if r.get("block") == blk])
            d = _delta_win(b, v)
            print(f"    bloco {blk} (mais {'antigo' if blk == 0 else 'recente'}) "
                  f"base {b['win']:6.1%} -> var {v['win']:6.1%} ({int(v['n']):4d}) "
                  f"delta {d:+.1%}" if b and v and d is not None else f"    bloco {blk} -")

        por_simbolo = []
        for symbol in {r["symbol"] for r in base_rows}:
            b = _agg([r for r in base_rows if r["symbol"] == symbol])
            v = _agg([r for r in var_rows if r["symbol"] == symbol])
            d = _delta_win(b, v)
            if d is not None and b["n"] >= 3:
                por_simbolo.append((d, symbol, int(b["n"]), int(v["n"])))
        if por_simbolo:
            melhora = sum(1 for d, *_ in por_simbolo if d > 0)
            piora = sum(1 for d, *_ in por_simbolo if d < 0)
            print(f"  por simbolo (n>=3): {len(por_simbolo)} simbolos | "
                  f"melhoram {melhora} ({melhora/len(por_simbolo):.0%}) | "
                  f"pioram {piora} ({piora/len(por_simbolo):.0%}) | "
                  f"mediana {median(d for d, *_ in por_simbolo):+.1%}")
            por_simbolo.sort(reverse=True)
            print("    melhores: " + ", ".join(
                f"{s} {d:+.0%}({nb}->{nv})" for d, s, nb, nv in por_simbolo[:5]))
            print("    piores  : " + ", ".join(
                f"{s} {d:+.0%}({nb}->{nv})" for d, s, nb, nv in por_simbolo[-5:]))
            verdict[f"{stream}_simbolos_melhoram"] = melhora / len(por_simbolo)
        verdict[f"{stream}_coverage"] = n_var_all / n_base_all if n_base_all else 1.0
    return verdict


def report(payload: dict[str, Any]) -> None:
    annotate(payload)
    meta = payload["meta"]
    _section("H2.1 - TRES CORRECOES MINIMAS DO SCORE DO HUNT")
    print(f"painel: {len(meta['symbols'])} simbolos x {meta['timeframes']} x "
          f"{meta['windows']} janelas | futuros: {meta['futures']}")
    print(f"erros: {len(payload['errors'])}")
    print(f"episodios unicos (baseline V0): {len(payload['episodes']['V0'])}  "
          f"com outcome medivel: {len(payload['outcomes'])}")
    print(f"discovery = {DISCOVERY_SHARE:.0%} mais ANTIGO de cada serie; "
          f"holdout = o restante (corte por serie, identico em todos os bracos)")
    v0 = len(payload["episodes"]["V0"])
    print(f"\nV0 (controle da maquinaria): {v0} episodios "
          f"-- tem de bater com a producao, ver test_v0_reproduz_a_producao")

    verdicts = {}
    for variant in ("V1", "V2", "V3"):
        verdicts[variant] = report_variant(payload, variant)

    passa = {
        v: (
            (verdicts[v].get(f"{s}_discovery_delta") or 0) > 0
            and (verdicts[v].get(f"{s}_holdout_delta") or 0) > 0
        )
        for v, s in (("V1", "hunt"), ("V2", "continuation"), ("V3", "hunt"))
    }
    _section("COMBINACAO V1+V3 (item 15)")
    if passa["V1"] and passa["V3"]:
        report_variant(payload, "V1V3")
    else:
        print("  NAO interpretada: o item 15 so autoriza combinar variantes que")
        print(f"  passaram sozinhas, e aqui V1={'passa' if passa['V1'] else 'falha'}, "
              f"V3={'passa' if passa['V3'] else 'falha'}.")
        print("  Os numeros existem no baseline, mas ler uma combinacao cujas partes")
        print("  nao se sustentam e escolher pelo resultado, nao testar a hipotese.")


def case_report(symbol: str, tf_name: str) -> None:
    """Os cinco casos do item 19, com fontes, score e o que veio depois."""
    tf = TFS[tf_name]
    data = load_dashboard_data(
        symbol=symbol, timeframe=tf, limit=VISIBLE_LIMIT,
        compute_narrative=False, futures_provider=NoFuturesProvider(),
    )
    rng = random.Random(1)
    arms = {name: engine_episodes(cls(), data, symbol, tf_name) for name, cls in VARIANTS.items()}
    base = arms["V0"]

    def linha(key: EpisodeKey, ep: dict[str, Any]) -> str:
        up = ep["hunted_side"] == RetailPositioning.SHORT.value
        res = outcome_of(data.candles, datetime.fromisoformat(key.anchor), up, rng)
        desfecho = (
            f"MFE {res['mfe_20']:.2f} MAE {res['mae_20']:.2f} "
            f"{'ACERTOU' if res['win_20'] else 'errou'}"
            if res else "sem horizonte"
        )
        return (f"    {key.stream:12s} {key.anchor} score={ep['score']:5.1f} "
                f"[{' '.join(ep['sources'])}] -> {desfecho}")

    print(f"\n=== {symbol} {tf_name} ===")
    casos: list[tuple[str, list[tuple[EpisodeKey, dict[str, Any]]]]] = [
        ("A) raid+zone cujo cap REMOVE o cluster (V1)",
         [(k, v) for k, v in base.items()
          if {"raid", "zone"} <= set(v["sources"]) and k not in arms["V1"]]),
        ("B) raid+zone cujo cap NAO remove (V1)",
         [(k, v) for k, v in base.items()
          if {"raid", "zone"} <= set(v["sources"]) and k in arms["V1"]]),
        ("C) continuation de UMA fonte (removida por V2)",
         [(k, v) for k, v in base.items()
          if k.stream == "continuation" and len(v["sources"]) == 1]),
        ("D) hunt score 7-9 removido por V3",
         [(k, v) for k, v in base.items()
          if k.stream == "hunt" and 7 <= v["score"] < 10]),
        ("E) hunt score >=10 preservado por V3",
         [(k, v) for k, v in base.items() if k.stream == "hunt" and v["score"] >= 10]),
    ]
    for titulo, sel in casos:
        print(f"\n  -- {titulo} --")
        if not sel:
            print("    (nenhum neste recorte)")
        for key, ep in sel[:3]:
            print(linha(key, ep))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--report-only", type=Path)
    parser.add_argument("--symbols", type=int, default=len(UNIVERSE))
    parser.add_argument("--tfs", default="M15,H1,H4")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--case", help="SIMBOLO:TF, ex BTCUSDT:M15")
    args = parser.parse_args()

    if args.report_only:
        report(json.loads(args.report_only.read_text()))
        return
    if args.case:
        symbol, tf_name = args.case.split(":")
        case_report(symbol, tf_name)
        return
    payload = run_panel(UNIVERSE[: args.symbols], tuple(args.tfs.split(",")), seed=args.seed)
    if args.out:
        args.out.write_text(json.dumps(payload, indent=1, default=str))
        print(f"\nescrito: {args.out}")
    report(payload)


if __name__ == "__main__":
    main()
