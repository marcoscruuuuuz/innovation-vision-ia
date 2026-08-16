# 09 - Fases de Implementação

## P0 - Infraestrutura
- Docker/Compose.
- PostgreSQL.
- Redis.
- MinIO.
- observabilidade.
- estrutura de segredos.
- backups.

## P1 - Cadastro e saúde
- Condomínios.
- usuários.
- DVRs.
- câmeras.
- online/offline/degraded.

## P2 - P2P/Wine
- CamAccess.
- Session Manager.
- Port Registry.
- Wine Supervisor.
- histórico.
- failover/troca emergencial.
- scheduler de carga.

## P3 - Ingestão/detecção
- Ingestion Worker.
- Redis Streams.
- YOLO.
- tracker.
- pose.
- scene change.

## P4 - Editor/Rule Engine
- editor ao vivo.
- ROI/linhas.
- versionamento.
- presets por setor.
- regras temporais.
- simulador/shadow mode.

## P5 - Certificação
- fila de candidatos.
- AI Reviewer.
- revisão humana.
- métricas de qualidade.
- bloqueio de logs não certificados.

## P6 - Modelos especializados
- placa + OCR.
- face.
- child/adult/unknown.
- regras comportamentais específicas.

## P7 - Portal/Alertas
- dashboard admin.
- portal cliente isolado.
- ISABEL cliente read-only.
- Evolution API/WhatsApp.

## P8 - ISABEL Vision IDE
- Tool Gateway.
- diagnóstico.
- simulações.
- planos de mudança.
- execução aprovada.
- RAG/memória operacional.

## P9 - Hardening
- testes de carga.
- failover P2P.
- restauração de backup.
- auditoria.
- segurança.
- testes de tenant isolation.
