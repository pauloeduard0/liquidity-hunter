# EQ E1.6 — log causal de eventos

E1.6 testou um contrato de eventos separado do snapshot. Cada evento tem
`symbol`, `timeframe`, `seq`, `event_id`, `kind`, `observed_at` e `payload`.

O journal aceita apenas a próxima sequência, permite retry exato pelo mesmo
`event_id` e rejeita reutilização do ID com payload diferente. Um gap só pode
ser preenchido por `append_backfill`, com faixa declarada e eventos contíguos.
O replay aplica os eventos em ordem e produz o mesmo estado para a mesma
sequência.

Os quatro testes E1.6 passam. Isso não é uma recomendação de backend: ainda
faltam persistência, lock distribuído, retenção e reconciliação com snapshot.
O resultado apenas torna explícito que um gap não deve ser tratado como avanço
normal do lifecycle. A próxima pesquisa pode comparar snapshot puro contra
snapshot mais journal em holdout, sem alterar threshold, clustering, lifecycle
ou UI de produção.

Artefatos: [journal experimental](eq_levels_e1_6.py) e [testes](test_eq_levels_e1_6.py).
