# EQ E1.4 — restaurador compacto

E1.4 implementou, exclusivamente em pesquisa, um checkpoint compacto que retém os últimos 11 candles para descobrir o próximo fractal e serializa o restante do estado causal: pivôs conhecidos, memberships, zonas R/N, força congelada, versões, ancestrais, consumo, acumuladores, eventos, snapshots, diagnósticos e índices globais.

O restaurador foi comparado com replay contínuo após um corte no meio da série. Quando não nasce uma nova membership depois do corte, a comparação exige igualdade de IDs vivos, records, eventos, sweeps, snapshots e payload. Também há checksum, schema, ordenação, contagem e rejeição de candle duplicado.

No caso sintético sem crescimento de membership, o restart compacto reproduziu a execução contínua nas camadas verificadas. Quando chega um novo pivô que cria uma nova membership, o restaurador recusa continuar: onze candles não contêm o histórico de volume necessário para calcular `strength` causal da nova versão. Isso é uma falha deliberadamente explícita, não uma aproximação silenciosa.

O formato não é ainda uma mudança produtiva. Nos nove charts BTC/ETH/SOL × M15/H1/H4 do piloto, todos os restarts encontraram uma nova membership após o corte e recusaram continuar por falta do histórico de volume da nova versão. Além disso, ao guardar snapshots/eventos completos, o compact checkpoint atual ficou maior que o checkpoint de candles simples (aprox. 364–472 KB contra 208–218 KB). Ele é um contrato de correção, não uma otimização de storage. Também não cobre store distribuído, locking entre processos, migração de schema ou recuperação de gaps de dados.

Decisão: o princípio do ledger compacto **não está validado**. O formato atual não é compacto em escala, não restaura crescimento de clusters e recusa os casos reais justamente onde o detector evolui. Antes de produção, é necessário separar diário de eventos, armazenar uma representação causal da área de volume e medir um restaurador realmente compacto. A próxima etapa será E1.5, teste de falha e concorrência do store de pesquisa, incluindo gaps e novas memberships. Thresholds e UI continuam intocados.

Artefatos: [restaurador](eq_levels_e1_4.py), [testes](test_eq_levels_e1_4.py) e `research/eq_levels_e1_4_baseline.json`.
