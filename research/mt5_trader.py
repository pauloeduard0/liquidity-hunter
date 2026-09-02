"""O executor: le a fila e manda a ordem. Roda no WINDOWS, junto do terminal.

Deliberadamente burro. Ele nao sabe o que e um bloco, um reclaim ou uma VWAP,
e nao importa uma linha do projeto -- so `MetaTrader5` e a biblioteca padrao.
Tudo o que decide ja foi decidido em `research/ftmo_orders.py`, do lado WSL,
pelo mesmo codigo que produziu a medicao. Aqui so restam tres perguntas: esta
ordem ja foi mandada? os limites permitem? o simbolo esta negociavel?

**Essa burrice e a defesa.** Um executor que soubesse a regra poderia
discordar dela; este so consegue errar mandando o que a fila pediu, ou nao
mandando nada. E os dois modos de falha sao visiveis: cada tentativa vira uma
linha em `fills.jsonl`, inclusive as recusadas, com o motivo.

Instalacao (uma vez, no PowerShell):

    py -m pip install MetaTrader5

Uso, com o terminal aberto e logado:

    py C:\\mt5-export\\mt5_trader.py --loop

Para parar tudo na hora, sem matar o processo: crie o arquivo
`C:\\mt5-export\\HALT`. Enquanto ele existir, nenhuma ordem sai.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone  # noqa: UP017 (3.10)
from pathlib import Path

try:
    import MetaTrader5 as mt5  # noqa: N813
except ImportError:  # pragma: no cover - so existe no Windows
    raise SystemExit(
        "MetaTrader5 nao encontrado. Este script roda no Windows, com o "
        "terminal da corretora aberto:\n    py -m pip install MetaTrader5"
    ) from None

#: Marca as ordens deste robo. Serve para nao contar como dele uma posicao
#: aberta a mao, e para achar as suas no terminal.
MAGIC = 770_425

#: Os limites da corretora, com folga. O da FTMO e 5% no dia e 10% no total;
#: parar antes evita descobrir o limite exato por violacao dele.
MAX_DAILY_LOSS_PCT = 0.04
MAX_TOTAL_LOSS_PCT = 0.08

#: Teto de posicoes simultaneas. O plano produz ~44 operacoes por mes em seis
#: fluxos, entao uma dezena aberta ao mesmo tempo ja seria anormal -- o teto
#: existe para limitar o estrago de um defeito que enfileire em laco.
MAX_OPEN_POSITIONS = 10

#: Quanto uma intencao pode ter esperado na fila e ainda ser mandada. O lado
#: WSL ja filtra por idade, mas a fila pode ter ficado parada (terminal
#: fechado, laco caido), e mandar a mercado uma intencao de ontem seria
#: entrar num preco que nao tem relacao nenhuma com o gatilho.
MAX_INTENT_AGE = timedelta(minutes=10)

#: Desvio maximo aceito, em pontos, entre o preco pedido e o executado.
DEVIATION_POINTS = 20

HALT_NAME = "HALT"


def load_intents(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def done_keys(fills: Path) -> set[str]:
    """O que ja foi tentado -- sucesso ou recusa, as duas coisas contam.

    Uma recusa nao pode virar nova tentativa na volta seguinte: se o motivo
    persistir, o laco tentaria para sempre, e se nao persistir a intencao ja
    envelheceu.
    """
    if not fills.exists():
        return set()
    return {json.loads(line)["key"] for line in
            fills.read_text(encoding="utf-8").splitlines() if line.strip()}


def record(fills: Path, intent: dict, status: str, **extra) -> None:
    stamp = datetime.now(timezone.utc).isoformat()  # noqa: UP017
    row = {**intent, "status": status, "attempted_at": stamp, **extra}
    with fills.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    detail = extra.get("detail", "")
    print(f"  {intent['symbol']:<12} {status:<10} {detail}")


def filling_mode(symbol: str):
    """O modo de preenchimento que ESTE simbolo aceita.

    Nao ha um padrao seguro: um simbolo configurado so para FOK recusa uma
    ordem IOC com `Unsupported filling mode`, e a recusa parece um erro de
    preco. A mascara da ficha responde sem chute.
    """
    info = mt5.symbol_info(symbol)
    mask = getattr(info, "filling_mode", 0) if info else 0
    if mask & 1:  # SYMBOL_FILLING_FOK
        return mt5.ORDER_FILLING_FOK
    if mask & 2:  # SYMBOL_FILLING_IOC
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


ANCHOR_NAME = "equity_anchor.json"


def anchor_equity(export: Path, account) -> float:
    """O saldo inicial contra o qual a perda TOTAL e medida.

    Gravado em arquivo na primeira vez e nunca reescrito. A versao obvia --
    usar a equity de quando o processo subiu -- tem um defeito que so aparece
    no pior momento: depois de um dia ruim, reiniciar o robo redefiniria o
    ponto de partida para o valor ja afundado, e o guarda passaria a permitir
    outra queda inteira a partir dali. O limite da corretora e sobre o saldo
    INICIAL, e o arquivo e o que faz o robo lembrar dele.
    """
    path = export / ANCHOR_NAME
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["balance"]
    path.write_text(json.dumps({
        "login": account.login, "balance": account.balance,
        "anchored_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
    }), encoding="utf-8")
    return account.balance


def guards_ok(export: Path, start_equity: float) -> str | None:
    """O motivo para NAO operar agora, ou `None` se pode.

    A perda do dia sai do historico de negocios do proprio terminal, e nao de
    um contador em arquivo: um contador se perde quando o processo reinicia, e
    e exatamente no reinicio depois de um dia ruim que ele precisaria estar
    certo.
    """
    if (export / HALT_NAME).exists():
        return "HALT presente"
    account = mt5.account_info()
    if account is None:
        return "sem conexao com o terminal"
    if account.equity <= start_equity * (1 - MAX_TOTAL_LOSS_PCT):
        return (f"perda total no limite: equity {account.equity:,.0f} "
                f"contra ancora {start_equity:,.0f}")
    now = datetime.now()
    midnight = datetime(now.year, now.month, now.day)
    deals = mt5.history_deals_get(midnight, now) or []
    today = sum(d.profit + d.swap + d.commission for d in deals
                if d.magic == MAGIC)
    if today <= -account.balance * MAX_DAILY_LOSS_PCT:
        return f"perda do dia no limite ({today:,.2f})"
    positions = mt5.positions_get() or []
    if len([p for p in positions if p.magic == MAGIC]) >= MAX_OPEN_POSITIONS:
        return "posicoes abertas no teto"
    return None


def send(intent: dict, fills: Path) -> None:
    symbol = intent["symbol"]
    if not mt5.symbol_select(symbol, True):
        record(fills, intent, "sem-simbolo", detail="nao selecionavel")
        return
    tick = mt5.symbol_info_tick(symbol)
    if tick is None or tick.ask <= 0 or tick.bid <= 0:
        record(fills, intent, "sem-preco", detail="mercado fechado?")
        return
    buy = intent["side"] == "buy"
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(intent["lots"]),
        "type": mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL,
        "price": tick.ask if buy else tick.bid,
        # Os niveis vao NA ordem, e nao num acompanhamento em Python: se este
        # processo morrer, a posicao continua com stop e alvo no servidor. Um
        # robo que so protege enquanto esta vivo nao protege.
        "sl": float(intent["stop"]),
        "tp": float(intent["target"]),
        "deviation": DEVIATION_POINTS,
        "magic": MAGIC,
        "comment": intent["key"][:31],
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling_mode(symbol),
    }
    # A margem e a ULTIMA coisa que muda entre a decisao e o envio: o lado WSL
    # cortou o lote contra uma margem estatica, mas quem sabe quanto sobrou
    # agora -- com as outras posicoes ja abertas competindo por ela, e em
    # cripto sem alavancagem, onde uma posicao come o nocional inteiro -- e o
    # terminal. Perguntar antes troca um `No money` (que gasta a intencao, ja
    # que recusa nao e repetida) por uma recusa com o numero dentro.
    check = mt5.order_check(request)
    if check is not None and check.retcode != 0 and check.margin_free < 0:
        record(fills, intent, "sem-margem",
               detail=f"faltam {-check.margin_free:,.2f} "
                      f"(margem da ordem {check.margin:,.2f})")
        return
    result = mt5.order_send(request)
    if result is None:
        record(fills, intent, "erro", detail=str(mt5.last_error()))
    elif result.retcode != mt5.TRADE_RETCODE_DONE:
        record(fills, intent, "recusada",
               detail=f"{result.retcode} {result.comment}")
    else:
        price, commission, waited = fill_price(result)
        record(fills, intent, "filled", price=price, commission=commission,
               volume=result.volume, ticket=result.order,
               lookup_seconds=round(waited, 3),
               detail=f"{result.volume} @ {price}")


#: Quanto esperar pelo negocio aparecer no historico depois do envio. Medido
#: na demo: o `order_send` volta DONE antes de o negocio estar consultavel, e
#: cinco segundos e folga larga sobre o que se observou (menos de um). O teto
#: existe para o executor nunca ficar preso -- ele roda dentro do laco de 60s.
FILL_LOOKUP_TIMEOUT = 5.0
FILL_LOOKUP_STEP = 0.1


def fill_price(result) -> tuple[float, float, float]:
    """O preco EXECUTADO, a comissao e quanto se esperou por eles.

    Sob execucao a mercado o retorno vem com `price` zerado mesmo num
    `TRADE_RETCODE_DONE`: o preco so existe no NEGOCIO que o servidor gerou, e
    o negocio ainda NAO esta consultavel quando o `order_send` retorna. Gravar
    esse zero nao e cosmetico -- a derrapagem e o unico numero que a
    `fills.jsonl` existe para produzir, e um zero contamina a media em vez de
    faltar visivelmente.

    Tres pegadinhas, todas descobertas mandando ordem de teste na demo:

    * a consulta e por `position=`, nao por `ticket=`. O `result.deal` e o
      ticket do NEGOCIO, e `history_deals_get(ticket=...)` devolve `None`
      para ele; quem casa e o `position_id`, que e o `result.order`.
    * nem o negocio nem a posicao existem no instante do retorno -- por isso
      isto e um laco com teto, e nao uma leitura unica.
    * o tempo de espera volta junto e vai para o registro: se um dia ele
      encostar no teto, o numero estara la em vez de o preco silenciosamente
      zerar de novo.

    A comissao vem de graca no mesmo negocio, e e custo real: vale gravar.
    """
    position_id = getattr(result, "order", 0)
    started = time.monotonic()
    while True:
        waited = time.monotonic() - started
        if position_id:
            deals = mt5.history_deals_get(position=position_id)
            if deals:
                deal = deals[0]
                return (float(deal.price),
                        float(getattr(deal, "commission", 0.0)), waited)
            for position in mt5.positions_get() or []:
                if position.ticket == position_id:
                    return float(position.price_open), 0.0, waited
        price = float(getattr(result, "price", 0.0) or 0.0)
        if price > 0:
            return price, 0.0, waited
        if waited >= FILL_LOOKUP_TIMEOUT:
            return 0.0, 0.0, waited
        time.sleep(FILL_LOOKUP_STEP)


def run_once(export: Path, start_equity: float) -> None:
    fills = export / "fills.jsonl"
    intents = load_intents(export / "orders.jsonl")
    pending = [i for i in intents if i["key"] not in done_keys(fills)]
    if not pending:
        return
    reason = guards_ok(export, start_equity)
    if reason is not None:
        print(f"  parado: {reason}  ({len(pending)} na fila)")
        return
    now = datetime.now(timezone.utc)  # noqa: UP017
    for intent in pending:
        queued = datetime.fromisoformat(intent["queued_at"])
        if now - queued > MAX_INTENT_AGE:
            record(fills, intent, "expirada",
                   detail=f"esperou {now - queued}")
            continue
        send(intent, fills)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="C:\\mt5-export")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=20)
    parser.add_argument(
        "--allow-live", action="store_true",
        help="permite rodar numa conta REAL. Sem isso o executor so opera demo.",
    )
    args = parser.parse_args()

    if not mt5.initialize():
        raise SystemExit(f"terminal nao respondeu: {mt5.last_error()}")
    account = mt5.account_info()
    if account is None:
        raise SystemExit("sem conta -- o terminal esta logado?")
    demo = account.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
    kind = "DEMO" if demo else "REAL"
    print(f"conta {account.login} ({kind})   {account.balance:,.2f} "
          f"{account.currency}   {account.server}")
    if not demo and not args.allow_live:
        # A trava e explicita porque o erro que ela impede e irreversivel: o
        # terminal nao pergunta duas vezes, e uma conta real logada por
        # engano fica indistinguivel da demo do ponto de vista do codigo.
        raise SystemExit("conta REAL: rode com --allow-live se e isso mesmo")

    export = Path(args.out)
    start_equity = anchor_equity(export, account)
    if start_equity != account.balance:
        print(f"ancora de {start_equity:,.2f}   "
              f"(limite total em {start_equity * (1 - MAX_TOTAL_LOSS_PCT):,.2f})")
    if not args.loop:
        run_once(export, start_equity)
        return
    print(f"laco a cada {args.interval}s   (crie {export / HALT_NAME} para parar)")
    while True:
        stamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{stamp}] verificando fila...")
        try:
            run_once(export, start_equity)
        except Exception as exc:  # o laco nao pode morrer por uma volta ruim
            print(f"  erro na volta: {exc!r}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
