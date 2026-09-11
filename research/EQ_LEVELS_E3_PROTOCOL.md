# EQ E3 — shadow controlado

E3 prepara uma futura feature flag sem alterar produção. O mesmo stream fechado
é executado no braço R (equivalente ao detector atual) e no braço N (identidade
causal/strength congelada). O shadow registra apenas divergências internas:
densidade R/N por snapshot, publicações, tipos de evento e estabilidade da
strength.

Critérios para qualquer futura promoção: zero mudança não explicada no payload,
restart determinístico, divergências de identidade auditáveis, ausência de
degradação no holdout completo e custo aceitável. Sem esses critérios, o shadow
permanece diagnóstico e não recebe endpoint, UI ou flag ativa.
