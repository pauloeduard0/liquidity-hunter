# EQ E1.5 — falhas e concorrência do store

E1.5 criou um store local de pesquisa com escrita atômica por arquivo e compare-and-swap. Ele não foi conectado ao backend.

Garantias testadas:

- primeiro write e load com checksum;
- retry idempotente do mesmo estado;
- escritor obsoleto rejeitado;
- timestamp/contagem regressivos rejeitados;
- arquivo corrompido falha fechado;
- arquivo temporário parcial não substitui o commit anterior;
- chaves de símbolo/timeframe isoladas;
- gap sem metadados não é aceito como continuidade.

Os seis testes E1.5 passam; a suíte combinada de auditoria e pesquisa passa com
52 testes. Ruff também passa nos artefatos envolvidos.

O store confirma que a parte mecânica do contrato é simples, mas revela duas decisões que não podem ser escondidas numa implementação: um gap precisa de backfill/replay explícito, e o estado de cada símbolo/timeframe precisa de um escritor lógico ou lock externo. `os.replace` protege o arquivo local; não resolve concorrência entre máquinas, NFS, cache distribuído ou crash entre atualização de dados e invalidação de leitura.

Decisão: não conectar este store em produção. A próxima etapa é E1.6, definir a política de gap/backfill e testar um log de eventos separado do snapshot, reduzindo o estado sem perder `strength` causal de novas memberships. Thresholds, lifecycle e UI permanecem inalterados.

Artefatos: [store experimental](eq_levels_e1_5.py), [testes](test_eq_levels_e1_5.py).
