# EQ E3.4 — continuidade com checkpoints no painel completo

Objetivo: verificar que três reinicializações sucessivas do replay causal
preservam o estado e as saídas da execução contínua, em todos os charts do E3.

## Método

- Confrontar as identidades únicas do manifesto E0 com o shadow E3 e verificar
  SHA-256 e identidade de cada fixture antes de executar. Excluir a última barra.
- Produzir uma referência contínua com o Replay existente.
- Executar outra instância e reinicializá-la após `floor(n/4)`, `floor(n/2)` e
  `floor(3*n/4)` barras fechadas. São três restarts sucessivos na mesma trajetória.
- Em cada fronteira, serializar `checkpoint()` em JSON no disco, ler o arquivo
  e chamar `Replay.restore()` para criar uma nova instância.
- Comparar todo o `__dict__` antes/depois do restart por igualdade de valores,
  incluindo candles, zonas R/N, strength, linhagens, consumo, registros,
  sweeps, ordem dos eventos e snapshots. Qualquer diferença aborta o painel.
- Comparar cada snapshot com a referência contínua e, no final, comparar todo
  o estado das duas execuções. Reconciliar também todas as métricas do shadow
  com o baseline anterior.

Os checkpoints temporários são removidos ao terminar cada chart; o relatório
registra tamanho, SHA-256 e tempo de gravação/leitura/restauração de cada um.
O journal parcial é descarregado após cada chart, mas não recebe status de
painel completo. O relatório final é substituído atomicamente somente após
concluir todos os charts. Isso não implementa durabilidade contra perda de
energia: não há `fsync`, retomada automática ou protocolo transacional.

## Escopo e limites

O checkpoint existente armazena todo o histórico de candles. Restaurar significa
reexecutar esse histórico; o custo cresce com a série. O ensaio testa novas
instâncias no mesmo processo, não encerramento do processo/SO, arquivo truncado,
disco cheio, corrupção arbitrária ou recuperação de serviço em produção.

As fronteiras cobrem três posições por chart, não todas as barras possíveis.
A paridade com o detector nativo foi tratada no E3.3; aqui a referência é o
Replay e o baseline E3. O ensaio não altera produção nem demonstra ganho no
holdout, memória limitada ou rollback operacional. O tempo total inclui a
referência e os replays adicionais e não deve ser comparado ao orçamento de
300 segundos do shadow simples.

Execução: `poetry run python -m research.eq_levels_e3_4`.
Código: [eq_levels_e3_4.py](eq_levels_e3_4.py).
Testes: [test_eq_levels_e3_4.py](test_eq_levels_e3_4.py).
Saída: `research/.replay_cache/eq_e3_4.json` e journal `.partial.jsonl`, locais e
ignorados pelo Git. O JSON final contém hashes do código do auditor, Replay,
shadow, manifesto e baseline, além dos hashes das fixtures.
