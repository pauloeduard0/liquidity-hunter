"""K3: o contexto do HUNT/CONT, lido AO VIVO, separa as operacoes do Block Reclaim?

De onde vem
-----------
K1 (`research/hunt_knowability.py`) e K2 (`research/hunt_reclaim_setup.py`)
fecharam o HUNT/CONT como GATILHO: ao vivo o episodio chega atrasado e some
em 57% dos casos, e o gatilho causal empata com o controle. A pergunta que
sobra e outra: ele serve de FILTRO de um setup que ja tem lugar? Aqui o HUNT
nao precisa acertar o momento, so descrever o ambiente de uma entrada que o
Block Reclaim ja ia tomar.

Populacao
---------
A do plano operacional, sem reescrever nada: `research.quality_features.scan`
(`detect_block_reclaims` + `OPERATING_GATES` + `test_pierced_the_block`) e,
na analise, `MAX_BLOCK_PENETRATION` onde esta ligado (M15/M30/H1). Custo,
alvo 2R, horizonte h120 e os quatro recortes (search/holdout por simbolo,
early/late no tempo) sao os do `quality_features`.

O contexto (so o que um observador tinha no FECHAMENTO do candle de entrada)
---------------------------------------------------------------------------
Para cada entrada, `load_dashboard_data` sobre os 500 candles que terminam no
candle do gatilho, com o candle HTF em formacao REMOVIDO (o mesmo corte do K2).
Nada da passada final e usado: o rotulo final do hunt e exatamente o que o K1
mostrou ser atraso + sobrevivencia.

    htf      direcao da HTF a favor / contra / neutra em relacao ao trade
    regime   perna LTF CONTRA a HTF (terreno do HUNT) ou ALINHADA (da CONT)
    state    `LiquidityHuntEngine.build`: fase != NONE e captura a favor/contra
    episode  episodio HUNT ou CONT visivel no snapshot, terminado nos ultimos
             `RECENT` candles, com captura a favor / contra / nenhum

Regras, fixadas ANTES de rodar
------------------------------
    R1  exigir episodio recente A FAVOR
    R2  vetar episodio recente CONTRA
    R3  exigir HTF A FAVOR
    R4  vetar hunt ativo CONTRA (state)

Criterio: uma regra passa se, com n >= MIN_N, bate o baseline no R medio
liquido E no Sharpe diario (dias sem trade contam zero) nos QUATRO recortes.
Replicar por trade e empatar por dia e reprovacao (licao da EMA9 e do
`acum15`). Um grupo descartado positivo e reportado como o custo do corte.

Predicao escrita antes: nenhuma passa. R1 pode subir o R por trade e perder
por dia (corta demais); R3 ja morreu no re-baseline do Block Reclaim (as
claims de direcao nao sobreviveram), entao e o controle de que o contexto do
hunt acrescenta algo alem da HTF.

Nada em `liquidity_hunter/` e tocado.

Run:
    poetry run python -m research.block_reclaim_hunt_context --tfs 15m,30m,1h,4h \\
        --out research/block_reclaim_hunt_context_baseline.json
    poetry run python -m research.block_reclaim_hunt_context \\
        --report-only research/block_reclaim_hunt_context_baseline.json
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import multiprocessing
from collections import defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any

from liquidity_hunter.app.dashboard_data import _HIGHER_TIMEFRAME_MAP, load_dashboard_data
from liquidity_hunter.app.liquidity_hunt import LiquidityHuntEngine
from liquidity_hunter.app.paper_journal import MAX_BLOCK_PENETRATION
from liquidity_hunter.core.domain import (
    Candle,
    LiquidityHuntPhase,
    MarketDirection,
    RetailPositioning,
    TimeFrame,
)
from liquidity_hunter.data.providers.base import OHLCVProvider
from research._paginated import NoFuturesProvider
from research._symbols import UNIVERSE
from research.hunt_reclaim_setup import ltf_trend
from research.quality_features import (
    MAIN_HORIZON,
    CachedProvider,
    cuts,
    metrics,
    net,
    scan,
)

VISIBLE = 500
RECENT = 20
LIMIT = 60_000
MIN_N = 60
TF_MINUTES = {"15m": 15, "30m": 30, "1h": 60, "4h": 240}


class SlicedProvider(OHLCVProvider):
    """Prefixo de series longas por bisect (o filtro linear de 60k candles por
    entrada custaria mais que o proprio snapshot)."""

    max_fetch_limit = 10_000

    def __init__(self, series: dict[TimeFrame, list[Candle]], htf: TimeFrame) -> None:
        self.series = series
        self.stamps = {tf: [c.timestamp for c in cs] for tf, cs in series.items()}
        self.htf = htf
        self.cut: datetime | None = None

    def get_ohlcv(self, symbol: str, timeframe: TimeFrame, limit: int = 500) -> list[Candle]:
        candles = self.series.get(timeframe, [])
        end = bisect.bisect_right(self.stamps.get(timeframe, []), self.cut)
        if timeframe is self.htf:
            end -= 1  # o candle HTF em formacao no corte
        return candles[max(0, end - limit) : max(0, end)]


def _side(direction: MarketDirection, bull: bool) -> str:
    if direction not in (MarketDirection.BULLISH, MarketDirection.BEARISH):
        return "neutral"
    return "with" if (direction is MarketDirection.BULLISH) == bull else "against"


def context_at(data: Any, bull: bool, recent_from: datetime) -> dict[str, Any]:
    engine = LiquidityHuntEngine()
    htf = data.higher_timeframe_direction
    ltf = ltf_trend(data)
    regime = "none"
    if htf.value in ("bullish", "bearish") and ltf in ("bullish", "bearish"):
        regime = "counter" if ltf != htf.value else "aligned"
    state = engine.build(data)
    state_side = "none"
    if state.phase is not LiquidityHuntPhase.NONE:
        capture_bull = state.hunted_side is RetailPositioning.SHORT
        state_side = "with" if capture_bull == bull else "against"
    episode, episode_stream = "none", None
    latest: datetime | None = None
    for stream, eps in (
        ("hunt", engine.build_history(data)),
        ("cont", engine.build_continuation_history(data)),
    ):
        for e in eps:
            if e.end_timestamp < recent_from or (latest and e.end_timestamp <= latest):
                continue
            latest = e.end_timestamp
            capture_bull = e.hunted_side is RetailPositioning.SHORT
            episode = "with" if capture_bull == bull else "against"
            episode_stream = stream
    return {
        "htf": _side(htf, bull),
        "regime": regime,
        "state": state_side,
        "episode": episode,
        "episode_stream": episode_stream,
    }


def annotate_symbol(symbol: str, tf_name: str, rows: list[dict]) -> list[dict]:
    tf = TimeFrame(tf_name)
    htf = _HIGHER_TIMEFRAME_MAP[tf]
    provider = CachedProvider()
    series = {
        tf: provider.get_ohlcv(symbol, tf, LIMIT),
        htf: provider.get_ohlcv(symbol, htf, LIMIT),
    }
    window = SlicedProvider(series, htf)
    step = timedelta(minutes=TF_MINUTES[tf_name])
    out = []
    for row in rows:
        ts = datetime.fromisoformat(row["timestamp"])
        window.cut = ts
        try:
            data = load_dashboard_data(
                provider=window, symbol=symbol, timeframe=tf, limit=VISIBLE,
                futures_provider=NoFuturesProvider(),
            )
            ctx = context_at(data, row["direction"] == "bullish", ts - RECENT * step)
        except Exception as exc:  # noqa: BLE001 - uma entrada sem contexto fica marcada
            ctx = {"error": f"{type(exc).__name__}: {exc}"[:160]}
        out.append({**row, "tf": tf_name, **ctx})
    return out


def _scan_one(symbol: str, tf_name: str, scan_dir: Path) -> list[dict]:
    path = scan_dir / f"scan_{tf_name}_{symbol}.json"
    if path.exists():
        return json.loads(path.read_text())
    return scan([symbol], TimeFrame(tf_name), LIMIT, str(path))


def _annotate_job(symbol: str, tf_name: str, scan_dir: Path) -> list[dict]:
    rows = _scan_one(symbol, tf_name, scan_dir)
    return annotate_symbol(symbol, tf_name, rows) if rows else []


def run(
    symbols: Sequence[str], tf_names: Sequence[str], workers: int, scan_dir: Path
) -> list[dict]:
    scan_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    ctx = multiprocessing.get_context("spawn")
    for tf_name in tf_names:
        print(f"\n{tf_name}: varredura + contexto ao vivo em {workers} processos", flush=True)
        with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
            futs = {pool.submit(_annotate_job, s, tf_name, scan_dir): s for s in symbols}
            for k, fut in enumerate(as_completed(futs), 1):
                try:
                    res = fut.result()
                except Exception as exc:  # noqa: BLE001
                    print(f"  ! {futs[fut]}: {type(exc).__name__}: {exc}", flush=True)
                    continue
                rows.extend(res)
                errs = sum(1 for r in res if r.get("error"))
                print(
                    f"  [{k}/{len(futs)}] {tf_name} {futs[fut]:11s} {len(res):4d} "
                    f"({errs} sem contexto)",
                    flush=True,
                )
    return rows


# ---------------------------------------------------------------------------
# relatorio
# ---------------------------------------------------------------------------


def daily_sharpe(rows: Sequence[dict], day_span: tuple[datetime, datetime]) -> float:
    key = f"r2_h{MAIN_HORIZON}"
    by_day: dict[str, float] = defaultdict(float)
    for r in rows:
        by_day[r["timestamp"][:10]] += net(r, key)
    days = max(1, (day_span[1] - day_span[0]).days + 1)
    vals = list(by_day.values()) + [0.0] * max(0, days - len(by_day))
    sd = pstdev(vals) if len(vals) > 1 else 0.0
    return fmean(vals) / sd * math.sqrt(365) if sd > 0 else 0.0


Rule = tuple[str, Callable[[dict], bool]]
RULES: list[Rule] = [
    ("R1 exigir episodio a favor", lambda r: r["episode"] == "with"),
    ("R2 vetar episodio contra", lambda r: r["episode"] != "against"),
    ("R3 exigir HTF a favor", lambda r: r["htf"] == "with"),
    ("R4 vetar hunt ativo contra", lambda r: r["state"] != "against"),
]


def _span(rows: Sequence[dict]) -> tuple[datetime, datetime]:
    stamps = [datetime.fromisoformat(r["timestamp"]) for r in rows]
    return min(stamps), max(stamps)


def _line(tag: str, rows: Sequence[dict], span: tuple[datetime, datetime]) -> dict:
    m = metrics(rows, MAIN_HORIZON)
    if m["n"] == 0:
        print(f"    {tag:26s} n=0")
        return {"n": 0}
    m["sr"] = daily_sharpe(rows, span)
    print(
        f"    {tag:26s} n={m['n']:5d}  2R {m['wr_2R']:5.1%}  medio {m['avg_R']:+.3f}  "
        f"total {m['total_R']:+7.1f}R  SR/dia {m['sr']:5.2f}"
    )
    return m


def report(rows: list[dict]) -> None:
    print(f"\n{'=' * 78}\nK3 - CONTEXTO DO HUNT (AO VIVO) COMO FILTRO DO BLOCK RECLAIM\n{'=' * 78}")
    for tf_name in TF_MINUTES:
        pop = [r for r in rows if r.get("tf") == tf_name]
        if not pop:
            continue
        cap = MAX_BLOCK_PENETRATION.get(TimeFrame(tf_name))
        if cap is not None:
            pop = [r for r in pop if r["pen_pct"] is None or r["pen_pct"] <= cap]
        errors = sum(1 for r in pop if r.get("error"))
        pop = [r for r in pop if not r.get("error")]
        print(f"\n######## {tf_name}  ({len(pop)} entradas, {errors} sem contexto descartadas)")
        parts = {"all": pop, **cuts(pop)}
        spans = {k: _span(v) for k, v in parts.items() if v}
        print("  baseline")
        base = {k: _line(k, v, spans[k]) for k, v in parts.items() if v}
        print("  distribuicao dos grupos (descritivo)")
        for field in ("htf", "regime", "state", "episode", "episode_stream"):
            for value in sorted({str(r[field]) for r in pop}):
                sel = [r for r in pop if str(r[field]) == value]
                _line(f"{field}={value}", sel, spans["all"])
        for name, keep in RULES:
            print(f"  {name}")
            ok = True
            for k, part in parts.items():
                if not part:
                    continue
                m = _line(f"retido  {k}", [r for r in part if keep(r)], spans[k])
                if k != "all":
                    b = base[k]
                    if m["n"] < MIN_N // 2 or m["avg_R"] <= b["avg_R"] or m["sr"] <= b["sr"]:
                        ok = False
                elif m["n"] < MIN_N:
                    ok = False
            _line("descartado all", [r for r in pop if not keep(r)], spans["all"])
            print(f"    => {'PASSA' if ok else 'reprovada'}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tfs", default="15m,30m,1h,4h")
    p.add_argument("--symbols", type=int, default=len(UNIVERSE))
    p.add_argument("--workers", type=int, default=10)
    p.add_argument("--scan-dir", type=Path, default=Path("/tmp"))
    p.add_argument("--out", type=Path)
    p.add_argument("--report-only", type=Path)
    a = p.parse_args()
    if a.report_only:
        report(json.loads(a.report_only.read_text()))
        return
    rows = run(UNIVERSE[: a.symbols], a.tfs.split(","), a.workers, a.scan_dir)
    if a.out:
        a.out.write_text(json.dumps(rows, default=str))
        print(f"\nescrito: {a.out}")
    report(rows)


if __name__ == "__main__":
    main()
