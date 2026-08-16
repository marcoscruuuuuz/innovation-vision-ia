# INNOVATION VISION IA

Plataforma modular de monitoramento por IA para implantação consolidada em servidor Ubuntu/Proxmox.

## Estado atual

O núcleo não depende de Wine/P2P para subir. A sequência operacional principal é reativa:

`Webhook/evento -> Ingestion API -> Redis -> Detection -> Rule Engine -> Certification -> Evidence/Logs -> Portal/Alerts`

Eventos que exigem contexto temporal usam `TEMPORAL_BURST` e permanecem pendentes de uma fonte de vídeo curto. A integração Intelbras P2P/Wine existe como fundação, mas está deliberadamente adiada para a última fase e só sobe com o profile Docker `p2p`.

## Componentes implementados

- API principal para condomínios, DVRs, câmeras e health.
- Ingestion API com HMAC-SHA256, nonce anti-replay, idempotência, MinIO e Redis Streams.
- Detection Worker pluggable (`disabled` ou backend HTTP real), sem deteções simuladas.
- Rule Engine com geometria normalizada, separação entre snapshot e regras temporais/especializadas.
- Certificação com políticas configuráveis de confiança, revisão humana e bloqueio de visibilidade fora de `PRODUCTION`.
- Evidências em MinIO com visualização/download autenticados.
- Portal administrativo e portal do cliente separados.
- Backend read-only para ISABEL consultar logs do condomínio autorizado.
- Retenção auditada de 7 dias.
- PostgreSQL, Redis, MinIO, Prometheus, Grafana e DCGM opcional.
- Instalador Ubuntu único e CI com build/migrations/smoke tests.

## Detecção

Por segurança, o detector inicia desabilitado:

```text
VISION_DETECTOR_BACKEND=disabled
```

Quando um backend real for homologado, configure `VISION_DETECTOR_BACKEND=http` e `DETECTOR_HTTP_URL`. O contrato `vision.detector.v1` usa caixas `normalized_xyxy_0_1`, compatíveis com as ROIs desenhadas no editor independentemente da resolução da câmera.

## Regras e certificação

As regras são versionadas por câmera. O editor administrativo permite desenhar ROI/polígono/linha sobre o snapshot mais recente e salvar uma nova versão com status de certificação.

Uma regra em `DRAFT`, `SHADOW`, `HOMOLOGATION`, `AI_REVIEW` ou `CERTIFIED` não gera log visível ao cliente automaticamente. A promoção automática para log do cliente exige `PRODUCTION` e respeita os limiares configuráveis por evento.

## Portais

- Admin: `http://127.0.0.1:8083`
- Cliente: `http://127.0.0.1:8084`
- API: `http://127.0.0.1:8080`
- Ingestion API: `http://127.0.0.1:8100`

O instalador gera um token bootstrap de administração em `/opt/vision/secrets/bootstrap-admin-token`. Use-o apenas para o bootstrap e emissão de tokens administrativos normais.

## Wine/P2P por último

O stack padrão não inicia Wine/P2P. Para a fase final:

```bash
INSTALL_WINE=yes sudo -E bash scripts/install_ubuntu.sh
docker compose --profile p2p up -d p2p-supervisor stream-broker
```

Nenhum SDK/DLL/EXE Intelbras proprietário é versionado neste repositório.

## Instalação

Ubuntu 24.04 LTS recomendado:

```bash
sudo bash scripts/install_ubuntu.sh
```

O instalador prepara Docker/Compose, Tailscale opcional, NVIDIA Container Toolkit quando aplicável, `/opt/vision`, bancos, workers, retenção e portais. Wine fica desativado por padrão.

## Documentação

- [Arquitetura](docs/01-ARCHITECTURE.md)
- [Dashboard](docs/02-DASHBOARD.md)
- [Catálogo de eventos](docs/03-EVENTS.md)
- [P2P e Wine](docs/04-P2P-WINE.md)
- [Certificação](docs/05-CERTIFICATION.md)
- [ISABEL Vision IDE](docs/06-AI-IDE.md)
- [Dados e segurança](docs/07-DATA-SECURITY.md)
- [Instalação Ubuntu](docs/08-INSTALLATION.md)
- [Fases](docs/09-IMPLEMENTATION-PHASES.md)
- [Aceite](docs/10-ACCEPTANCE.md)
- [Ingestão reativa](docs/11-REACTIVE-INGESTION.md)

## Limites de homologação

O repositório pode ser validado por CI, migrations, contratos e smoke tests sem câmeras físicas. A homologação positiva de modelos, regras temporais, GPU e Intelbras P2P exige ambiente real, imagens/vídeo reais e os binários/modelos autorizados. O sistema não marca essas dependências como concluídas quando elas não estão disponíveis.
