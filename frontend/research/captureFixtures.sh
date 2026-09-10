#!/usr/bin/env bash
# Snapshots reais de /api/dashboard para o harness do PISO.
#
# Exige a API rodando:
#   poetry run uvicorn liquidity_hunter.api.main:app --reload
#
# As fixtures sao gitignored (~1,1MB cada) e reproduziveis por este script.
# Nao editar os JSON a mao: o harness mede a saida real do backend, e um valor
# ajustado para um teste passar deixa de medir o produto.
set -euo pipefail
cd "$(dirname "$0")/fixtures"
API="${PISO_API:-http://127.0.0.1:8000}"
for sym in "${@:-BTCUSDT ETHUSDT SOLUSDT}"; do
  for tf in 15m 1h 4h; do
    f="${sym}_${tf}.json"
    [ -s "$f" ] && { echo "skip $f"; continue; }
    code=$(curl -s -m 180 -w "%{http_code}" \
      "$API/api/dashboard?symbol=${sym}&timeframe=${tf}&limit=1200" -o "$f")
    echo "$f -> $code"
  done
done
