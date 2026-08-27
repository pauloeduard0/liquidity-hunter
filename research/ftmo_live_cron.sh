#!/usr/bin/env bash
# Uma passada do diario de papel contra o feed da corretora.
#
# Instalar NO WSL (a cada minuto):
#   crontab -e
#   * * * * * /home/paulo/projects/liquidity-hunter/research/ftmo_live_cron.sh
#
# Tres coisas precisam estar VIVAS para isto valer alguma coisa, e nenhuma
# delas e este script:
#   1. o terminal da corretora, aberto e logado (senao o exportador nao le);
#   2. o refresh.ps1 rodando no Windows (senao os CSV congelam e o guard de
#      idade descarta tudo -- o diario fica vazio em vez de errado, que e o
#      comportamento certo mas nao avisa sozinho);
#   3. uma janela do WSL aberta: o WSL2 desliga a VM quando nao sobra
#      processo, e leva o cron junto.
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
} >> "$LOG"

# Nao deixa o log crescer sem fim: 5000 linhas cobrem alguns dias.
tail -5000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
