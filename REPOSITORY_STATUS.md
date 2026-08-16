# Status do Repositório

## Plataforma de software implementada

- Cadastro de condomínios, DVRs, câmeras e health.
- Ingestão reativa por webhook com HMAC-SHA256, nonce anti-replay e idempotência.
- Snapshot em MinIO e fila Redis Streams.
- Detection Worker com contrato `vision.detector.v1`; não produz detecção sintética.
- Rule Engine por câmera com versões, parâmetros, ROI/polígono/linha e separação snapshot/temporal.
- Políticas de confiança configuráveis por evento.
- Certificação conservadora: somente regra `PRODUCTION` pode gerar log visível ao cliente.
- Revisão humana administrativa.
- Evidências autenticadas com Visualizar/Baixar.
- Portal administrativo e portal do cliente separados.
- Role PostgreSQL `vision_portal` + RLS, além do tenant guard da API.
- Backend read-only para ISABEL consultar/contar logs autorizados.
- Retenção auditada de 7 dias com fila de exclusão MinIO.
- AlertPolicy/NotificationQueue + adapter Evolution configurável.
- P2P Supervisor, Port Registry e Wine worker registry.
- StreamBroker com troca transacional e rollback de rota.
- Failover Orchestrator make-before-break, journal, verificação e rollback.
- Painel administrativo P2P com planejamento e botão TROCAR TÚNEL.
- PostgreSQL, Redis, MinIO, Prometheus, Grafana e DCGM opcional.
- Instalador Ubuntu único.
- CI com shell/Python/Compose, builds, migrations PostgreSQL e smoke HTTP.

## Execução segura por padrão

O stack padrão continua sem Wine/P2P. Os serviços finais P2P existem no profile Docker `p2p` e só são iniciados explicitamente. `INSTALL_WINE=no` e `INTELBRAS_VENDOR_ADAPTER_ENABLED=false` permanecem como defaults.

Nenhum SDK/DLL/EXE Intelbras proprietário nem credencial real é versionado.

## Deliberadamente bloqueado até homologação externa

### Detector/modelos reais

`VISION_DETECTOR_BACKEND=disabled` por padrão. É necessário instalar/homologar backend YOLO/OCR/pose/VLM real. Sem backend, o sistema registra `BLOCKED_MODEL`; não inventa resultados.

### Eventos temporais/especializados

Regras dependentes de tracker, pose, direção, ausência, mudança estrutural, OCR temporal ou VLM exigem vídeo/modelos reais antes de produção.

### Intelbras P2P/Wine real

O código do control plane está implementado. A abertura e o failover reais continuam bloqueados até existir o bridge Intelbras/Wine autorizado, com SDK/binários válidos, credenciais reais e DVRs acessíveis. A sessão só pode virar `ACTIVE` depois de probe de frames reais.

### Evolution API

`EVOLUTION_ENABLED=false` por padrão. Uma entrega só vira `SENT` após resposta HTTP 2xx real do provider.

## Critério de conclusão de código

A plataforma é aceita em código quando:

1. `scripts/validate_repo.sh` passa;
2. Python compila sem erro;
3. `docker compose config --quiet` passa;
4. o stack padrão não inicia P2P;
5. `--profile p2p` expõe supervisor, StreamBroker e Failover Orchestrator;
6. todas as imagens constroem;
7. migrations 002..009 aplicam em PostgreSQL real;
8. API, Ingestion API e portais passam smoke HTTP;
9. os três serviços P2P passam health com vendor adapter desabilitado;
10. nenhum segredo real está versionado;
11. regras fora de `PRODUCTION` não geram log automático para cliente;
12. consultas do cliente são limitadas por tenant guard e RLS.

## Homologações físicas ainda necessárias

- YOLO/pose/OCR/VLM com imagens e vídeo reais;
- precisão por evento e cenas reais;
- bursts temporais de vídeo;
- GPU/CUDA no host final;
- Evolution real;
- Intelbras P2P/Wine com DVRs reais e bridge autorizado;
- failover P2P completo em ambiente real.

Essas homologações não devem ser declaradas concluídas somente porque o CI do repositório passou.
