"""Roda o plano operacional contra o feed da corretora, em papel.

Este e o mesmo `paper_journal` que ja existe -- **nenhuma logica de deteccao
muda aqui**, so o provider. Em vez do perpetuo da Binance, os candles vem dos
CSV que `mt5_export.py --refresh` mantem atualizados a partir do terminal da
FTMO. O que se opera e o que se mede passam a ser o mesmo instrumento, que era
a ultima costura solta entre o estudo e a conta.

**Nao manda ordem e nao guarda credencial.** Decide, registra e liquida em
papel. O numero que ele existe para produzir e a **derrapagem em R**: todo
resultado de `docs/block_reclaim.md` assume entrada no fechamento da vela do
gatilho, e so a fita ao vivo diz o que essa suposicao custa.

Os seis fluxos abaixo sao os do plano validado, e a lista de simbolos de cada
um e a que passou no walk-forward -- nem mais (um simbolo a mais nao esta
medido) nem menos.

Como rodar, com o terminal da corretora aberto:

    # 1. no Windows, o laco que mantem os CSV frescos (deixe a janela aberta).
    #    Ele tambem chama o executor a cada volta -- veja `write_refresh`.
    powershell -ExecutionPolicy Bypass -File C:\\mt5-export\\refresh.ps1

    # 2. no WSL, uma passada por vez -- idempotente, bom para cron
    poetry run python -m research.ftmo_live
    poetry run python -m research.ftmo_live --report-only

O `refresh.ps1` e **gerado** por `--write-refresh`, nunca editado a mao: as
listas de simbolos daqui sao as que passaram no walk-forward, e um simbolo a
mais no script do Windows faria o que roda divergir do que foi medido.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from liquidity_hunter.app.paper_journal import (
    read_journal,
    record_decisions,
    resolve_open,
)
from liquidity_hunter.app.paper_runner import report
from liquidity_hunter.core.domain import TimeFrame
from research._mt5 import EXPORT_DIR, MT5CsvProvider

#: Onde o diario do feed da corretora mora. Separado do diario de cripto da
#: Binance de proposito: sao instrumentos diferentes com custos diferentes, e
#: misturar os dois na mesma serie apagaria justamente a comparacao.
JOURNAL_PATH = Path("research/.datasets/ftmo_paper_journal.jsonl")

#: Os seis indices baratos do M5. O criterio nao foi geografia e sim custo: no
#: M5 o spread come 0,671R de 0,750R de bruto, entao os caros perdem dinheiro
#: acertando a direcao (o N25 custa 1,417R por operacao e acerta 60%).
INDEX_M5: tuple[str, ...] = (
    "GER40.cash", "JP225.cash", "UKOIL.cash",
    "US100.cash", "US30.cash", "US500.cash",
)

#: M15 e M30 rodam os quinze sem filtro: os folds recusaram todo filtro
#: oferecido, e no M30 escolheram "todos" em 4 de 11 janelas.
INDEX_ALL: tuple[str, ...] = (
    "US500.cash", "US100.cash", "US30.cash", "US2000.cash",
    "GER40.cash", "UK100.cash", "FRA40.cash", "EU50.cash",
    "SPN35.cash", "N25.cash", "AUS200.cash", "JP225.cash", "HK50.cash",
    "USOIL.cash", "UKOIL.cash",
)

#: Os CFD de cripto cujo par na Binance passou o corte de spread (<0,1%), com
#: os nomes que o terminal usa -- que nao sao os da Binance (HBARUSDT vira
#: BARUSD, LINKUSDT vira LNKUSD, AAVEUSDT vira AAVUSD).
CRYPTO: tuple[str, ...] = (
    "AAVUSD", "BNBUSD", "BTCUSD", "DOGEUSD", "ETCUSD", "ETHUSD",
    "GRTUSD", "BARUSD", "ICPUSD", "IMXUSD", "LNKUSD", "MANUSD",
    "SOLUSD", "UNIUSD", "VECUSD", "XLMUSD",
)

#: Os 28 pares mais os dois metais, sem selecao: no cambio a busca por filtro
#: deu PBO 0,933, e o que passou no walk-forward foi a regra sem filtro nenhum.
FOREX: tuple[str, ...] = (
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD",
    "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD",
    "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD", "GBPNZD",
    "AUDJPY", "AUDCHF", "AUDCAD", "AUDNZD",
    "NZDJPY", "NZDCHF", "NZDCAD",
    "CADJPY", "CADCHF", "CHFJPY",
    "XAUUSD", "XAGUSD",
)

#: Quanto tempo REAL um gatilho pode ter fechado e ainda virar decisao. O
#: `MAX_DECISION_AGE_CANDLES = 1` do diario ja limita a idade, mas em unidades
#: de vela -- e uma vela do H4 sao quatro horas. Precificar um gatilho de
#: quatro horas atras contra a fita de agora mede DERIVA DE PRECO, nao
#: derrapagem: e a mesma classe do bug que registrou 1,6R na primeira passada
#: ao vivo, so que mais discreta e por isso mais perigosa.
#:
#: Cinco minutos e generoso para um laco que roda a cada minuto, e aperta o
#: bastante para que a linha meça o que promete. Contamina so a primeira
#: passada depois de um periodo parado, que e exatamente quando ela deve
#: descartar.
MAX_SIGNAL_AGE = timedelta(minutes=5)

#: O gate do M5 nao tem piso de acumulacao de VWAP medido -- `OPERATING_GATES`
#: para no M15. Declarar que nao ha e honesto e so pode SUBESTIMAR; escolher
#: um numero aqui seria ajustar sem medicao.
_M5_GATE = {TimeFrame.M5: (1.0, 1)}


@dataclass(frozen=True)
class Stream:
    """Um fluxo do plano: uma lista de simbolos, um timeframe, um gate."""

    name: str
    symbols: tuple[str, ...]
    timeframe: TimeFrame
    gates: dict[TimeFrame, tuple[float, int]] | None = None


#: Os seis fluxos validados, na ordem em que aparecem no plano.
STREAMS: tuple[Stream, ...] = (
    Stream("indice M5", INDEX_M5, TimeFrame.M5, _M5_GATE),
    Stream("indice M15", INDEX_ALL, TimeFrame.M15),
    Stream("indice M30", INDEX_ALL, TimeFrame.M30),
    Stream("cripto M15", CRYPTO, TimeFrame.M15),
    Stream("cripto H4", CRYPTO, TimeFrame.H4),
    Stream("cambio H4", FOREX, TimeFrame.H4),
)


def server_offset(provider: MT5CsvProvider) -> timedelta:
    """Quanto a hora do servidor da corretora adianta em relacao ao UTC real.

    O MetaTrader marca cada vela em **hora do servidor**, nao em UTC (a FTMO
    roda em GMT+3, para o candle diario fechar as 17h de Nova York). O
    exportador grava esse instante com sufixo `+00:00`, entao comparar um
    timestamp de vela com `datetime.now(UTC)` erra por tres horas.

    O deslocamento e inferido do proprio dado -- a vela mais nova de M5 nao
    pode estar no futuro -- em vez de ser uma constante: o servidor muda de
    offset no horario de verao, e um numero fixo aqui quebraria em silencio
    duas vezes por ano. **Nao e aplicado aos candles**: o dia do servidor e a
    sessao correta para a ancora da VWAP, e desloca-lo mudaria `vwap_candles`,
    que e gate de producao.
    """
    newest = None
    for symbol in (*INDEX_M5, *CRYPTO):
        for timeframe in (TimeFrame.M5, TimeFrame.M15):
            path = provider.path(symbol, timeframe)
            if not path.exists():
                continue
            candles = provider.get_ohlcv(symbol, timeframe, 1)
            if candles and (newest is None or candles[-1].timestamp > newest):
                newest = candles[-1].timestamp
    if newest is None:
        return timedelta(0)
    ahead = newest - datetime.now(UTC)
    # Arredonda para a meia hora: fusos de corretora sao offsets inteiros ou
    # de meia hora, e a vela mais nova esta em algum ponto DENTRO do seu
    # periodo, o que deixa um residuo de ate um candle.
    return timedelta(minutes=round(ahead.total_seconds() / 1800) * 30)


def available(symbols: tuple[str, ...], timeframe: TimeFrame,
              provider: MT5CsvProvider) -> tuple[str, ...]:
    """So os que o exportador de fato trouxe.

    Um simbolo sem CSV nao vira erro nem some em silencio: ele e reportado,
    porque um fluxo rodando com metade da lista rende metade e a diferenca
    tem que ser visivel na hora, nao no fim do mes.
    """
    return tuple(s for s in symbols if provider.path(s, timeframe).exists())


#: A distribuicao do WSL que o laco do Windows mantem viva. Se voce renomear
#: ou trocar de distro, `wsl.exe -l` diz o nome e este e o unico lugar a
#: mudar.
WSL_DISTRO = "Ubuntu"


def write_refresh(export: Path, distro: str = WSL_DISTRO) -> str:
    """Gera o laco do lado Windows a partir das listas deste modulo.

    Gerado e nao escrito a mao porque as listas sao as que passaram no
    walk-forward: um simbolo digitado a mais no script do Windows faria o que
    roda divergir, em silencio, do que foi medido.
    """
    exe = "py C:\\mt5-export\\mt5_export.py --out C:\\mt5-export --refresh"
    trader = "py C:\\mt5-export\\mt5_trader.py --out C:\\mt5-export"
    blocks = [
        ("indices M5 (so os 6 baratos)", INDEX_M5, ["M5"]),
        ("indices M15 e M30 (os 15)", INDEX_ALL, ["M15", "M30"]),
        ("cripto M15 e H4", CRYPTO, ["M15", "H4"]),
        ("cambio H4", FOREX, ["H4"]),
    ]
    lines = [
        "# Atualiza os candles do plano operacional, em laco.",
        "# GERADO por `python -m research.ftmo_live --write-refresh`.",
        "# Nao editar a mao -- veja a docstring do modulo.",
        "#",
        "# Uso:  powershell -ExecutionPolicy Bypass -File C:\\mt5-export\\refresh.ps1",
        "",
        "$ErrorActionPreference = 'Continue'",
        "",
        "while ($true) {",
        "  $t = Get-Date -Format 'HH:mm:ss'",
        "  Write-Host \"[$t] atualizando...\"",
        "  # Mantem a VM do WSL viva: ela desliga quando nao sobra processo, e",
        "  # levaria o cron do diario junto. Uma chamada por volta basta, e",
        "  # dispensa deixar uma janela do WSL aberta so para isso.",
        f"  wsl.exe -d {distro} -e true 2>$null",
    ]
    for label, symbols, timeframes in blocks:
        lines.append(f"  # {label}")
        lines.append(
            f"  {exe} --timeframes {' '.join(timeframes)} "
            f"--symbols {' '.join(symbols)}"
        )
    lines += [
        "  # O executor, em passada UNICA: este laco e o relogio dele, entao ele",
        "  # nao precisa de um proprio. Sem `--loop` ele confere a fila, manda o",
        "  # que houver e sai -- se a fila estiver vazia (o caso comum) nao faz",
        "  # nada. E ele NAO le candle nenhum: quem alimenta a decisao sao as",
        "  # linhas acima, e por isso a ordem aqui importa.",
        "  #",
        "  # Comente esta linha para voltar ao modo papel sem mexer em mais nada.",
        f"  {trader}",
        "  # Uma volta a cada 60s: a vela mais rapida do plano e de 5 minutos,",
        "  # entao atualizar mais rapido nao traz vela nova -- so gasta pedido.",
        "  Start-Sleep -Seconds 60",
        "}",
        "",
    ]
    path = export / "refresh.ps1"
    path.write_text("\r\n".join(lines), encoding="utf-8")
    return f"escrito {path}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", default=str(EXPORT_DIR))
    parser.add_argument("--journal", default=str(JOURNAL_PATH))
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument(
        "--write-refresh", action="store_true",
        help="(re)gera o refresh.ps1 do lado Windows a partir das listas daqui",
    )
    args = parser.parse_args()

    if args.write_refresh:
        print(write_refresh(Path(args.export)))
        return

    path = Path(args.journal)
    path.parent.mkdir(parents=True, exist_ok=True)
    provider = MT5CsvProvider(Path(args.export))

    if not args.report_only:
        offset = server_offset(provider)
        print(f"servidor da corretora: UTC{offset.total_seconds() / 3600:+.0f}h"
              f"   idade maxima do gatilho: {MAX_SIGNAL_AGE}")
        settled = resolve_open(path=path, provider=provider)
        if settled:
            print(f"liquidadas {len(settled)}")
        for stream in STREAMS:
            symbols = available(stream.symbols, stream.timeframe, provider)
            missing = len(stream.symbols) - len(symbols)
            if not symbols:
                print(f"  {stream.name:<12} sem CSV -- rode o exportador")
                continue
            fresh = record_decisions(
                path=path, provider=provider, symbols=symbols,
                timeframes=(stream.timeframe,),
                max_signal_age=MAX_SIGNAL_AGE, clock_offset=offset,
                **({"gates": stream.gates} if stream.gates else {}),
            )
            note = f"   ({missing} sem CSV)" if missing else ""
            print(f"  {stream.name:<12} {len(symbols):>3} simbolos"
                  f"   {len(fresh)} novas{note}")

    print()
    print(report(read_journal(path)))


if __name__ == "__main__":
    main()
