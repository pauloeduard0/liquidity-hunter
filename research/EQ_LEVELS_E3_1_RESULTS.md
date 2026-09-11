# EQ E3.1 — contrato operacional do shadow

E3.1 formaliza a fronteira operacional para uma futura execução em shadow. O
relatório precisa conter exatamente o painel esperado, identidades únicas,
divergências não negativas e runtime válido. Painel incompleto ou malformado
falha fechado.

A decisão operacional é separada da qualidade do indicador: latência acima do
limite operacional retorna `rollback`; qualquer divergência R/N mantém o
candidato em `shadow-only`; somente um painel completo sem divergência pode ser
`eligible-for-review`. Isso não ativa flag nem altera produção.

O contrato também exige a série de divergências nos quatro blocos temporais,
evitando que um agregado esconda instabilidade concentrada em um período.

Artefatos: [contrato](eq_levels_e3_contract.py) e [testes](test_eq_levels_e3_contract.py).
