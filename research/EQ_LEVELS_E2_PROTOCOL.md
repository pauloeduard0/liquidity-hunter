# EQ E2 — candidato causal mínimo

E2 testa somente a propriedade escolhida: uma membership confirmada recebe uma
publicação única e `strength` não pode ser reescrito por candles posteriores.
O candidato usa o replay N de E1, mantendo exatamente as factories, três
toques, lookback 5, tolerância 0,5 e lifecycle de produção.

Invariantes obrigatórias:

- `publish.observed_at == record.known_time`;
- cada versão tem uma identidade estável;
- a strength de uma versão é constante depois da publicação;
- execução contínua e checkpoint/restart produzem os mesmos eventos e snapshots;
- nenhum limiar ou regra de wick/close é alterado.

Este é um harness de pesquisa. Ainda não mede ganho de qualidade nem autoriza
integração no backend.
