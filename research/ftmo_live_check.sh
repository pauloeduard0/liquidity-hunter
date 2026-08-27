#!/usr/bin/env bash
# As tres coisas que precisam estar vivas, num comando so.
#
#   ./research/ftmo_live_check.sh
#
# Existe porque cada uma delas falha em SILENCIO: o diario simplesmente fica
# vazio, que e indistinguivel de "nao houve sinal hoje".
set -uo pipefail
cd /home/paulo/projects/liquidity-hunter

ok() { printf '  \033[32m OK \033[0m %s\n' "$1"; }
bad() { printf '  \033[31mFALHA\033[0m %s\n' "$1"; }

echo "verificacao do diario ao vivo   $(date -u '+%Y-%m-%d %H:%M UTC')"
echo

# 1. Os CSV estao sendo atualizados? (o refresh.ps1 rodando no Windows)
newest=$(find /mnt/c/mt5-export -name '*_M5.csv' -newermt '-3 minutes' 2>/dev/null | wc -l)
if [ "$newest" -gt 0 ]; then
  ok "refresh.ps1 vivo ($newest CSV de M5 tocados nos ultimos 3 min)"
else
  bad "CSV parados -- o refresh.ps1 nao esta rodando, ou o terminal fechou"
  echo "        powershell -ExecutionPolicy Bypass -File C:\\mt5-export\\refresh.ps1"
fi

# 2. O cron esta chamando o runner?
if [ -f research/.datasets/ftmo_live.log ] && \
   [ -n "$(find research/.datasets/ftmo_live.log -newermt '-3 minutes' 2>/dev/null)" ]; then
  ok "cron vivo (log escrito nos ultimos 3 min)"
else
  bad "log parado -- cron nao esta rodando, ou a janela do WSL fechou"
  echo "        crontab -l   # deve listar ftmo_live_cron.sh"
fi

# 3. O que o diario ja sabe.
echo
/usr/bin/poetry run python -m research.ftmo_live --report-only 2>&1 | sed 's/^/  /'
