# 10 - Critérios de Aceite

## Infra
- [ ] `docker compose config` válido.
- [ ] PostgreSQL healthy.
- [ ] Redis healthy.
- [ ] MinIO healthy.
- [ ] métricas do host disponíveis.
- [ ] GPU detectada quando aplicável.

## P2P
- [ ] portas sem colisão.
- [ ] sessão por DVR identificável.
- [ ] histórico de trocas persistido.
- [ ] botão de troca emergencial auditado.
- [ ] migração Wine validada sem sessão órfã.

## Câmeras
- [ ] estado online baseado em frame real.
- [ ] offline detectado por timeout.
- [ ] degraded diferenciado.
- [ ] filtro por condomínio/DVR/câmera.

## Regras
- [ ] geometria obrigatória bloqueia ativação sem ROI.
- [ ] display label não altera event_type.
- [ ] versionamento/rollback.
- [ ] shadow mode.
- [ ] câmera seguinte/anterior no editor.

## Certificação
- [ ] log de homologação não aparece ao cliente.
- [ ] revisão IA separada.
- [ ] feedback FP/FN persistido.
- [ ] versão de regra/modelo em todo evento.

## Evidências
- [ ] snapshot.
- [ ] mini-clipe.
- [ ] visualizar.
- [ ] download autorizado.
- [ ] hash/metadados.

## Cliente
- [ ] tenant isolation.
- [ ] login separado.
- [ ] ISABEL read-only sobre logs autorizados.

## Segurança
- [ ] nenhum segredo no Git.
- [ ] dados internos sem portas públicas desnecessárias.
- [ ] ações críticas auditadas.
