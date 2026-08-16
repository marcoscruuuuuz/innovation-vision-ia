# INNOVATION VISION IA

Plataforma modular de monitoramento por IA para implantação consolidada em servidor Ubuntu/Proxmox.

## Estado atual

A sequência operacional principal é reativa e não depende de Wine/P2P para subir:

`Webhook/evento -> Ingestion API -> Redis -> Detection -> Rule Engine -> Certification -> Evidence/Logs -> Portal/Alerts`

O control plane final Intelbras P2P/Wine também está implementado, porém isolado no profile Docker `p2p` e desabilitado por padrão até a homologação do bridge/SDK autorizado.

## Componentes implementados

- API principal para condomínios, DVRs, câmeras e health.
- Ingestion API com HMAC-SHA256, nonce anti-replay, idempotência, MinIO e Redis Streams.
- Detection Worker pluggable (`disabled` ou backend HTTP real), sem detecções simuladas.
- Rule Engine com geometria normalizada e separação entre snapshot e regras temporais/especializadas.
- Certificação com políticas configuráveis de confiança, revisão humana e bloqueio de visibilidade fora de `PRODUCTION`.
- Evidências em MinIO com visualização/download autenticados.
- Portal administrativo e portal do cliente separados.
- Backend read-only para ISABEL consultar logs do condomínio autorizado.
- Retenção auditada de 7 dias.
- Notification Queue e adapter Evolution configurável.
- P2P Supervisor, Port Registry, StreamBroker e Failover Orchestrator transacional.
- Watchdog P2P automático com hysteresis/cooldown e disparo do failover.
- Painel P2P administrativo com planejamento e botão de troca emergencial de túnel.
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

Uma regra em `DRAFT`, `SHADOW`, `HOMOLOGATION`, `AI_REVIEW` ou `CERTIFIED` não gera log visível ao cliente automaticamente. A promoção automática exige `PRODUCTION` e respeita os limiares configuráveis por evento.

## Portais

- Admin: `http://127.0.0.1:8083`
- Editor de regras: `http://127.0.0.1:8083/rules.html`
- P2P/Wine: `http://127.0.0.1:8083/p2p.html`
- Cliente: `http://127.0.0.1:8084`
- API: `http://127.0.0.1:8080`
- Ingestion API: `http://127.0.0.1:8100`

O instalador gera um token bootstrap de administração em `/opt/vision/secrets/bootstrap-admin-token`. Use-o apenas para bootstrap e emissão de tokens administrativos normais.

## P2P/Wine final

O stack padrão continua sem Wine/P2P. O código final fica atrás do profile `p2p`:

```bash
INSTALL_WINE=yes sudo -E bash scripts/install_ubuntu.sh
docker compose --profile p2p up -d p2p-supervisor stream-broker failover-orchestrator p2p-watchdog
```

Serviços locais:

- P2P Supervisor: `127.0.0.1:8090`
- StreamBroker: `127.0.0.1:8091`
- Failover Orchestrator: `127.0.0.1:8092`
- Watchdog: serviço interno, sem porta publicada.

`INTELBRAS_VENDOR_ADAPTER_ENABLED=false` permanece como default. Sem o executável autorizado, a abertura real de sessão é bloqueada e o watchdog não dispara alterações. Uma sessão só pode virar `ACTIVE` após probe de frames reais. O watchdog mede saúde do túnel; depois do limiar configurado de falhas o StreamBroker aplica hysteresis/cooldown e o Failover Orchestrator executa make-before-break, valida todas as rotas e faz rollback explícito em qualquer falha.

Nenhum SDK/DLL/EXE Intelbras proprietário é versionado neste repositório.

## Instalação

Ubuntu 24.04 LTS recomendado:

```bash
sudo bash scripts/install_ubuntu.sh
```

O instalador prepara Docker/Compose, Tailscale opcional, NVIDIA Container Toolkit quando aplicável, `/opt/vision`, bancos, workers, retenção e portais. Wine fica desativado por padrão e só é instalado com `INSTALL_WINE=yes`.

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
- [Core Platform](docs/12-CORE-PLATFORM.md)

## Limites de homologação

O repositório pode ser validado por CI, migrations, contratos e smoke tests sem câmeras físicas. A homologação positiva de modelos, regras temporais, GPU, Evolution e Intelbras P2P exige ambiente real, imagens/vídeo reais e os binários/modelos autorizados. O sistema não marca essas dependências como concluídas quando elas não estão disponíveis.
