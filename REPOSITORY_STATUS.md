# Status do Repositório

## Núcleo implementado

- Cadastro de condomínios, DVRs, câmeras e health.
- Ingestão reativa por webhook com HMAC-SHA256, nonce anti-replay e idempotência.
- Snapshot em MinIO e fila Redis Streams.
- Detection Worker com contrato `vision.detector.v1`; não produz detecção sintética.
- Rule Engine por câmera com versões, parâmetros, ROI/polígono/linha e separação snapshot/temporal.
- Políticas de confiança configuráveis por evento.
- Certificação automática conservadora: somente regra `PRODUCTION` pode gerar log visível ao cliente.
- Revisão humana administrativa.
- Evidências autenticadas com Visualizar/Baixar.
- Portal administrativo separado.
- Portal do cliente separado e isolado por condomínio.
- Role PostgreSQL `vision_portal` + RLS para leituras do portal, além do tenant guard da API.
- Backend read-only para ISABEL consultar/contar logs autorizados.
- Retenção auditada de 7 dias com fila de exclusão MinIO.
- AlertPolicy/NotificationQueue + adapter Evolution configurável e desabilitado por padrão.
- PostgreSQL, Redis, MinIO, Prometheus, Grafana e DCGM opcional.
- Instalador Ubuntu único.
- CI com shell/Python/Compose, build das imagens, migrations PostgreSQL e smoke HTTP.

## Deliberadamente desabilitado até homologação externa

### Detector/modelos reais

`VISION_DETECTOR_BACKEND=disabled` por padrão. É necessário instalar/homologar backend YOLO/OCR/pose/VLM real antes de declarar detecção de produção. Quando desabilitado, o sistema registra `BLOCKED_MODEL`; não inventa resultados.

### Eventos temporais/especializados

Regras que dependem de tracker, pose, direção, ausência, mudança estrutural, OCR temporal ou VLM são registradas como necessidade temporal/modelo especializado enquanto o respectivo pipeline não estiver homologado com vídeo real.

### Wine/P2P Intelbras

Wine/P2P foi movido para a última fase. Os serviços ficam no profile Docker `p2p` e não sobem no stack padrão. SDK/DLL/EXE Intelbras proprietário e credenciais reais não são versionados.

### Evolution API

O worker de notificações existe, mas `EVOLUTION_ENABLED=false` por padrão. Uma entrega só vira `SENT` após resposta HTTP real do provider configurado.

## Critério de conclusão do núcleo sem Wine

O núcleo é aceito em código quando:

1. `scripts/validate_repo.sh` passa;
2. Python compila sem erro;
3. `docker compose config --quiet` passa;
4. o stack padrão não inclui os serviços do profile `p2p`;
5. todas as imagens do núcleo constroem;
6. migrations 002..008 aplicam em PostgreSQL real;
7. API, Ingestion API, Portal Admin e Portal Cliente passam smoke HTTP;
8. nenhum segredo real está versionado;
9. regras fora de `PRODUCTION` não geram log automático para cliente;
10. consultas do cliente são limitadas por vínculo de condomínio e RLS.

## Bloqueios que exigem ambiente real

- homologação positiva de YOLO/pose/OCR/VLM;
- validação de precisão por evento com cenas reais;
- bursts temporais de vídeo real;
- GPU/CUDA no host final;
- envio Evolution real;
- Intelbras P2P/Wine e failover real, por último.

Esses itens não devem ser declarados concluídos somente porque o CI do repositório passou.
