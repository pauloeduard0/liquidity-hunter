# EQ E4.2 — compatibilidade de payload

O candidato causal foi projetado pelo shape existente de `LiquidityZone` e
validado contra o domínio real em BTC/ETH/SOL × M15/H1/H4. Nenhum campo novo,
score ou semântica de lifecycle foi adicionado; a projeção apenas confirma que
uma eventual leitura pode continuar usando o contrato atual.

Este teste remove uma barreira de integração, mas não é autorização para
conectar o candidato. O gate operacional continua bloqueado por divergências
R/N materiais e pela ausência de ganho robusto de qualidade.

Artefatos: [verificador](eq_levels_e4_payload.py) e [teste](test_eq_levels_e4_payload.py).
