# INNOVATION VISION IA

Base arquitetural oficial para a implantação consolidada do sistema de monitoramento por IA em um único servidor Ubuntu/Proxmox.

## Objetivo

Consolidar captura Intelbras RTSP/P2P, supervisão Wine, distribuição de carga, detecção, regras geométricas e temporais, certificação por IA, evidências, dashboards, alertas WhatsApp e portal do cliente em uma plataforma modular e auditável.

## Princípio de arquitetura

A solução continua modular mesmo em um único host:

`CamAccess/P2P -> Ingestion -> Redis -> Detection -> Rule Engine -> Certification -> Evidence/Logs -> Portal/Alerts`

A ISABEL Vision IDE atua como camada de operação, diagnóstico e configuração. Ela não substitui o motor de detecção nem pode executar mudanças críticas sem passar pelo Tool Gateway e pela política de aprovação.

## Documentação

- [Arquitetura](docs/01-ARCHITECTURE.md)
- [Dashboard](docs/02-DASHBOARD.md)
- [Catálogo de eventos](docs/03-EVENTS.md)
- [P2P e Wine](docs/04-P2P-WINE.md)
- [Certificação](docs/05-CERTIFICATION.md)
- [ISABEL Vision IDE](docs/06-AI-IDE.md)
- [Dados e segurança](docs/07-DATA-SECURITY.md)
- [Instalação Ubuntu](docs/08-INSTALLATION.md)
- [Fases de implementação](docs/09-IMPLEMENTATION-PHASES.md)
- [Critérios de aceite](docs/10-ACCEPTANCE.md)

## Estrutura

```text
/opt/vision/
├── core/                    # API, autenticação, tenant, auditoria
├── devices/                 # condomínios, DVR/NVR, câmeras, health
├── p2p/                     # túneis, portas, Wine, failover
├── scheduler/               # carga de câmeras/Wine/GPU
├── ingestion/               # captura e normalização de frames
├── detection/               # YOLO, pose, tracker, placa, OCR, scene change
├── rules/                   # regras, ROI, temporal, versionamento, simulador
├── certification/           # homologação, AI review, revisão humana
├── evidence/                # prebuffer, snapshot, mini-clipe
├── ai/                      # ISABEL IDE, Tool Gateway, RAG, memória
├── notifications/           # políticas, fila, Evolution API
├── observability/           # Prometheus, node exporter, DCGM
├── portal-admin/
├── portal-client/
├── postgres/
├── redis/
├── minio/
├── configs/
├── models/
├── secrets/
├── logs/
└── backups/
```

## Instalação base

Em Ubuntu 24.04 LTS:

```bash
sudo bash scripts/install_ubuntu.sh
```

O instalador prepara o host, Docker Engine/Compose, Wine, Tailscale, estrutura `/opt/vision`, infraestrutura de dados e observabilidade. O NVIDIA Container Toolkit é instalado automaticamente quando uma GPU NVIDIA com driver funcional é detectada.

## Importante

Este repositório é a fundação arquitetural e de infraestrutura. Binários proprietários Intelbras, credenciais, modelos treinados e chaves não são versionados. Os módulos de aplicação devem ser implementados e homologados por etapas conforme `docs/09-IMPLEMENTATION-PHASES.md`.
