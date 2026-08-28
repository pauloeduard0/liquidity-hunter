#!/usr/bin/env bash
# Uma passada do diario de papel contra o feed da corretora.
#
# Instalar NO WSL (a cada minuto):
#   crontab -e
#   * * * * * /home/paulo/projects/liquidity-hunter/research/ftmo_live_cron.sh
#
# Duas coisas precisam estar VIVAS para isto valer alguma coisa, e nenhuma
# delas e este script:
#   1. o terminal da corretora, aberto e logado (senao o exportador nao le);
#   2. o refresh.ps1 rodando no Windows (senao os CSV congelam e o guard de
#      idade descarta tudo -- o diario fica vazio em vez de errado, que e o
#      comportamento certo mas nao avisa sozinho). Ele tambem mantem a VM do
#      WSL de pe, que e o que segura este cron.
#
# Com AUTO=1, cada passada tambem ENFILEIRA as decisoes novas como ordens.
# Quem manda e o `mt5_trader.py`, do lado Windows; sem ele a fila so cresce e
# nada e executado -- que e o modo papel de sempre.
#
# Idempotente: uma decisao ja registrada nao entra duas vezes, e uma passada
# perdida so custa os gatilhos daquele minuto.
set -uo pipefail

ROOT=/home/paulo/projects/liquidity-hunter
# Caminho absoluto de proposito: o cron roda com um PATH minimo, e um
# `poetry: command not found` num job de cron falha em SILENCIO -- o diario
# ficaria vazio e parecendo "nenhum sinal hoje".
POETRY=/usr/bin/poetry
LOG="$ROOT/research/.datasets/ftmo_live.log"

cd "$ROOT" || exit 1
{
  echo "=== $(date -u '+%Y-%m-%d %H:%M:%S') UTC"
  if [ ! -x "$POETRY" ]; then
    echo "ERRO: $POETRY nao existe -- ajuste POETRY neste script"
    exit 1
  fi
  "$POETRY" run python -m research.ftmo_live 2>&1
  if [ "${AUTO:-0}" = "1" ]; then
    "$POETRY" run python -m research.ftmo_orders 2>&1
  fi
} >> "$LOG"

# Nao deixa o log crescer sem fim: 5000 linhas cobrem alguns dias.
tail -5000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
