"""Exporta candles do MetaTrader 5 para CSV, para rodar no lado Windows.

Este arquivo nao roda no WSL: a biblioteca `MetaTrader5` conversa com o
terminal por memoria compartilhada e so existe em Windows. O caminho e o
inverso do resto de `research/` -- em vez de puxar dados de uma API, a gente
pede ao terminal que ja esta aberto e logado na corretora, e le o resultado
do outro lado do `/mnt/c/`.

O que ele traz alem do OHLC:

- **`spread`**, em points, por candle. Essa coluna e a razao inteira de
  exportar do MT5 em vez de uma fonte melhor: e o custo do instrumento que
  voce operaria de fato, medido pelo proprio feed da corretora. O buraco
  declarado do estudo de cripto (`docs/block_reclaim.md`: "medi perpetuo da
  Binance e a FTMO vende CFD, o spread do CFD nao esta em nenhuma dessas
  contas") se fecha exatamente aqui.
- **`tick_volume`** e **`real_volume`**. Num CFD de indice o `real_volume`
  vem zerado -- o feed conta ticks, nao contratos. Exportamos os dois sem
  preencher um com o outro, para que a diferenca fique visivel no CSV em vez
  de virar um numero inventado. A VWAP institucional de indice mora no
  futuro subjacente (ES/NQ/FDAX), nao aqui; este CSV serve para medir
  volatilidade e custo, nao para medir a VWAP.
- O `meta.json` com `point`, `digits`, `trade_contract_size` e o spread
  corrente, sem os quais a coluna de spread em points nao vira percentual.

**O que a coluna `spread` da barra NAO responde** (`--ticks`, abaixo): ela e
um numero por barra, e a documentacao do terminal nao diz de que instante --
abertura, minimo, ultimo tick. Numa barra de M5 o spread abre e fecha varias
vezes, e o gatilho do setup dispara em movimento, que e onde ele abre. Com
stop de 2 pontos-base o custo e dois tercos do R, entao a diferenca entre "o
spread medio da barra" e "o spread no instante da entrada" deixa de ser
detalhe. `--ticks` exporta o bid/ask de verdade (`COPY_TICKS_INFO`) para uma
janela curta, com o proposito de **auditar** a coluna da barra, nao de
substitui-la: tick para 30 simbolos por anos nao cabe em disco nem e
necessario -- se a coluna estiver honesta na janela auditada, ela serve para
o resto.

Uso, no PowerShell ou cmd do Windows, com o terminal da corretora aberto:

    py -m pip install MetaTrader5
    py mt5_export.py --out C:\\mt5-export

Depois, do WSL, os arquivos aparecem em `/mnt/c/mt5-export/`.

Para a auditoria de spread, uma janela curta basta:

    py mt5_export.py --out C:\\mt5-export --ticks --tick-days 30 \\
        --symbols EURUSD GBPJPY XAUUSD
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

#: Os indices e o petroleo da lista da corretora. Petroleo entra porque e o
#: unico da lista que nao e mais uma fatia da mesma aposta: US500/US30/US100
#: se movem juntos, e a Europa idem.
DEFAULT_SYMBOLS: tuple[str, ...] = (
    "US500.cash",
    "US100.cash",
    "US30.cash",
    "GER40.cash",
    "USOIL.cash",
)

#: Os quatro timeframes que o setup ja mede em cripto, para que a comparacao
#: seja entre as mesmas reguas.
TIMEFRAMES: tuple[str, ...] = ("M5", "M15", "M30", "H1", "H4")

#: Quantos candles pedir por timeframe. O terminal corta isso pelo seu
#: proprio teto ("Max bars in chart"); o script relata o que veio de fato em
#: vez de assumir que o pedido foi atendido -- a licao de
#: `project_measurement_window_clamp`, onde uma janela declarada nao era a
#: janela executada.
DEFAULT_COUNT = 60_000

#: Colunas do CSV de ticks. `bid` e `ask` sao o ponto inteiro: a diferenca
#: entre eles e o spread real, no instante real, sem agregacao nenhuma.
TICK_COLUMNS = ("time", "bid", "ask", "last", "flags")

#: Quantas barras pedir no modo `--refresh`, que existe para RODAR AO VIVO em
#: laco e nao para medir. O diario de papel so olha o passado recente (o
#: gatilho tem que ter acabado de fechar, e a liquidacao anda 40 velas), entao
#: uma cauda curta basta e o pedido volta em segundos em vez de minutos.
REFRESH_BARS = 3_000

#: Quantos dias de tick pedir por padrao. Trinta dias de um par liquido ja sao
#: alguns milhoes de linhas; a auditoria nao precisa de mais do que isso, e
#: pedir anos so enche o disco para responder a mesma pergunta.
DEFAULT_TICK_DAYS = 30

COLUMNS = (
    "time",
    "open",
    "high",
    "low",
    "close",
    "tick_volume",
    "spread",
    "real_volume",
)


def _load_mt5():  # noqa: ANN202 - modulo so existe em Windows
    try:
        import MetaTrader5 as mt5  # noqa: N813
    except ImportError:
        sys.exit(
            "MetaTrader5 nao encontrado. Este script roda no Windows, com o "
            "terminal da corretora aberto:\n    py -m pip install MetaTrader5"
        )
    return mt5


#: `copy_rates_from_pos` para em 100.000 barras -- teto da propria chamada,
#: nao da configuracao "Max bars in chart" (verificado: com o terminal em
#: Ilimitado ela devolve exatamente 100.000). Em M5 isso e pouco mais de um
#: ano num indice que negocia quase 24h, curto demais para atravessar mais de
#: um regime. `copy_rates_range` nao tem esse teto, entao o modo profundo pede
#: por intervalo de datas, andando para tras em pedacos ate a historia acabar.
POS_FETCH_CAP = 100_000
#: Quantos dias por pedido no modo profundo. Pequeno o bastante para nao
#: esbarrar no teto de novo em M5 de indice 24h (~276 barras/dia).
DEEP_CHUNK_DAYS = 180
#: Quantos pedacos vazios seguidos aceitar antes de concluir que a historia
#: acabou. Um so seria fragil: feriado longo, suspensao, um buraco no feed.
DEEP_EMPTY_STREAK = 3


def _fetch_deep(mt5, symbol: str, tf: int, since: datetime) -> list:
    """Anda para tras por intervalo de datas, emendando os pedacos."""
    import numpy as np

    chunks, empty = [], 0
    end = datetime.now(UTC) + timedelta(days=1)
    while end > since and empty < DEEP_EMPTY_STREAK:
        start = max(since, end - timedelta(days=DEEP_CHUNK_DAYS))
        part = mt5.copy_rates_range(symbol, tf, start, end)
        if part is None or len(part) == 0:
            empty += 1
        else:
            empty = 0
            chunks.append(part)
        end = start
    if not chunks:
        return []
    joined = np.concatenate(chunks[::-1])
    # Os pedacos se tocam nas bordas: uma barra pode vir duas vezes.
    _, keep = np.unique(joined["time"], return_index=True)
    return joined[sorted(keep)]


def export_symbol(
    mt5, symbol: str, timeframe: str, count: int, out: Path,
    since: datetime | None = None,
) -> dict | None:
    """Um CSV por (simbolo, timeframe). Devolve o resumo, ou None se falhou."""
    if not mt5.symbol_select(symbol, True):
        print(f"  {symbol} {timeframe}: nao consegui selecionar no Market Watch")
        return None
    tf = getattr(mt5, f"TIMEFRAME_{timeframe}")
    if since is not None:
        rates = _fetch_deep(mt5, symbol, tf, since)
    else:
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    if rates is None or len(rates) == 0:
        print(f"  {symbol} {timeframe}: sem barras ({mt5.last_error()})")
        return None

    path = out / f"{symbol.replace('.', '_')}_{timeframe}.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for row in rates:
            writer.writerow(
                [
                    datetime.fromtimestamp(int(row["time"]), UTC).isoformat(),
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    int(row["tick_volume"]),
                    int(row["spread"]),
                    int(row["real_volume"]),
                ]
            )

    first = datetime.fromtimestamp(int(rates[0]["time"]), UTC)
    last = datetime.fromtimestamp(int(rates[-1]["time"]), UTC)
    summary = {
        "symbol": symbol,
        "timeframe": timeframe,
        "bars_requested": count if since is None else None,
        "bars_returned": len(rates),
        # `clamped` e o aviso: uma janela menor que a pedida muda o que a
        # medicao cobre. No modo profundo nao ha pedido em barras -- o limite
        # passa a ser a historia que a corretora guarda.
        "clamped": since is None and len(rates) >= POS_FETCH_CAP,
        "first": first.isoformat(),
        "last": last.isoformat(),
        "file": path.name,
    }
    flag = "  (cortado em 100k -- use --since para ir mais fundo)" if summary["clamped"] else ""
    print(f"  {symbol} {timeframe}: {len(rates)} barras  {first.date()} -> {last.date()}{flag}")
    return summary


def export_ticks(mt5, symbol: str, days: int, out: Path) -> dict | None:
    """Bid/ask tick a tick, para auditar a coluna `spread` das barras.

    `copy_ticks_range` com `COPY_TICKS_INFO` traz as mudancas de cotacao (nao
    os negocios), que e exatamente o que forma o spread. O arquivo sai
    separado dos candles e nenhuma medicao le ele por padrao: quem le e
    `research/spread_audit.py`, cuja unica pergunta e se o numero por barra
    mente.
    """
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    ticks = mt5.copy_ticks_range(symbol, start, end, mt5.COPY_TICKS_INFO)
    if ticks is None or len(ticks) == 0:
        print(f"  {symbol} ticks: nada ({mt5.last_error()})")
        return None
    path = out / f"{symbol.replace('.', '_')}_TICKS.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(TICK_COLUMNS)
        for row in ticks:
            writer.writerow([
                datetime.fromtimestamp(int(row["time_msc"]) / 1000, UTC).isoformat(
                    timespec="milliseconds"
                ),
                row["bid"], row["ask"], row["last"], int(row["flags"]),
            ])
    first = datetime.fromtimestamp(int(ticks[0]["time_msc"]) / 1000, UTC)
    last = datetime.fromtimestamp(int(ticks[-1]["time_msc"]) / 1000, UTC)
    print(f"  {symbol} ticks: {len(ticks)} linhas  {first.date()} -> {last.date()}")
    return {
        "symbol": symbol, "timeframe": "TICKS", "bars_requested": None,
        "bars_returned": len(ticks), "clamped": False,
        "first": first.isoformat(), "last": last.isoformat(), "file": path.name,
    }


def _margin_rate(mt5, symbol: str, info) -> dict:
    """A fracao do nocional que a corretora exige como margem, por lote.

    Devolve um dicionario para poder sair vazio: sem preco (mercado fechado,
    simbolo nao selecionado) e melhor NAO gravar o campo do que gravar um
    numero errado que ninguem consegue distinguir de um certo.
    """
    tick = mt5.symbol_info_tick(symbol)
    price = getattr(tick, "ask", 0.0) if tick else 0.0
    notional = price * info.trade_contract_size
    if notional <= 0:
        return {}
    margin = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, symbol, 1.0, price)
    if not margin or margin <= 0:
        return {}
    return {"margin_rate": margin / notional}


def symbol_meta(mt5, symbol: str) -> dict | None:
    """O que converte spread em points para spread em percentual do preco."""
    info = mt5.symbol_info(symbol)
    if info is None:
        return None
    return {
        "symbol": symbol,
        "description": info.description,
        "digits": info.digits,
        "point": info.point,
        "spread_current_points": info.spread,
        "spread_float": bool(info.spread_float),
        "trade_contract_size": info.trade_contract_size,
        "trade_tick_size": info.trade_tick_size,
        "trade_tick_value": info.trade_tick_value,
        "volume_min": info.volume_min,
        "volume_max": info.volume_max,
        "volume_step": info.volume_step,
        # A margem nao entra no dimensionamento por risco, mas LIMITA a ordem:
        # um stop muito curto produz um lote correto em risco e grande demais
        # em nocional. Sem estes campos o limite so aparece na recusa da
        # corretora, que e o pior lugar para descobrir.
        "margin_initial": info.margin_initial,
        "margin_maintenance": info.margin_maintenance,
        # A TAXA de margem, e nao a margem em dolares: `margin_per_lot =
        # preco * trade_contract_size * margin_rate`. Guardar a taxa em vez do
        # valor e o que deixa a medicao historica usar o preco da EPOCA -- a
        # margem em dolares vale so para o preco do dia da exportacao, e um
        # estudo que a usasse mediria o limite de hoje sobre trades de dois
        # anos atras.
        #
        # Vem do `order_calc_margin` e nao de uma conta nossa porque a taxa
        # muda por CLASSE e sem aviso: medido nesta conta, cripto e 1:1
        # (margem = nocional), indice 15x e cambio 30x, com a conta declarando
        # 1:30. Derivar do `leverage` da conta erraria em cripto por 30 vezes.
        **_margin_rate(mt5, symbol, info),
        "trade_mode": info.trade_mode,
        "currency_profit": info.currency_profit,
        "swap_long": info.swap_long,
        "swap_short": info.swap_short,
        "swap_mode": info.swap_mode,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="mt5-export", help="pasta de destino")
    parser.add_argument("--symbols", nargs="*", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--timeframes", nargs="*", default=list(TIMEFRAMES))
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument(
        "--since",
        help="AAAA-MM-DD: busca por intervalo de datas em vez de por posicao, "
        "sem o teto de 100k barras. E o modo para M5.",
    )
    parser.add_argument(
        "--ticks", action="store_true",
        help="exporta bid/ask tick a tick para auditar a coluna de spread das "
        "barras, em vez de exportar candles",
    )
    parser.add_argument("--tick-days", type=int, default=DEFAULT_TICK_DAYS)
    parser.add_argument(
        "--meta-only", action="store_true",
        help="regera so o meta.json (fichas dos simbolos), sem tocar em "
             "candle nenhum. E o modo para adotar um campo novo da ficha: o "
             "`--refresh` do laco ao vivo NAO reescreve o meta, entao um meta "
             "antigo sobrevive indefinidamente a uma atualizacao do exportador.",
    )
    parser.add_argument(
        "--refresh", action="store_true",
        help=f"exporta so a cauda recente ({REFRESH_BARS} barras) e nao "
        "reescreve o meta.json -- o modo para rodar em laco ao vivo",
    )
    args = parser.parse_args()
    if args.refresh:
        args.count = REFRESH_BARS

    since = (
        datetime.fromisoformat(args.since).replace(tzinfo=UTC) if args.since else None
    )
    mt5 = _load_mt5()
    if not mt5.initialize():
        sys.exit(f"nao consegui falar com o terminal: {mt5.last_error()}")

    account = mt5.account_info()
    if account is not None:
        print(f"terminal: {account.server} / conta {account.login} ({account.company})")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    exports: list[dict] = []
    metas: list[dict] = []
    for symbol in args.symbols:
        print(symbol)
        meta = symbol_meta(mt5, symbol)
        if meta is None:
            print(f"  {symbol}: nao existe neste terminal -- confira o nome exato no Market Watch")
            continue
        metas.append(meta)
        if args.meta_only:
            continue
        if args.ticks:
            summary = export_ticks(mt5, symbol, args.tick_days, out)
            if summary is not None:
                exports.append(summary)
            continue
        for timeframe in args.timeframes:
            summary = export_symbol(mt5, symbol, timeframe, args.count, out, since)
            if summary is not None:
                exports.append(summary)

    # O meta e ACUMULADO, nao sobrescrito: uma exportacao de cripto nao pode
    # apagar a ficha dos indices exportados antes. Sem isso, medir o swap de um
    # instrumento exige reexportar o outro -- que foi exatamente o que
    # aconteceu.
    meta_path = out / "meta.json"
    if args.refresh and not args.meta_only and meta_path.exists():
        # A ficha do instrumento (point, swap, contrato) nao muda a cada
        # minuto, e reescrever o meta a cada volta do laco so cria uma janela
        # em que o leitor do outro lado pega o arquivo pela metade.
        mt5.shutdown()
        print(f"\n{len(exports)} arquivos atualizados em {out.resolve()}")
        return
    previous: dict[str, Any] = {}
    if meta_path.exists():
        try:
            previous = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = {}
    merged_symbols = {s["symbol"]: s for s in previous.get("symbols", [])}
    merged_symbols.update({s["symbol"]: s for s in metas})
    merged_exports = {
        (e["symbol"], e["timeframe"]): e for e in previous.get("exports", [])
    }
    merged_exports.update({(e["symbol"], e["timeframe"]): e for e in exports})
    meta_path.write_text(
        json.dumps(
            {
                "exported_at": datetime.now(UTC).isoformat(),
                "server": account.server if account is not None else None,
                "symbols": sorted(merged_symbols.values(), key=lambda s: s["symbol"]),
                "exports": sorted(
                    merged_exports.values(), key=lambda e: (e["symbol"], e["timeframe"])
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    mt5.shutdown()
    print(f"\n{len(exports)} arquivos + meta.json em {out.resolve()}")


if __name__ == "__main__":
    main()
