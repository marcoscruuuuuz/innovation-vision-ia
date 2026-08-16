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
- Instalador/atualizador Ubuntu único e CI com build/migrations/smoke tests.

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

O instalador cria o primeiro administrador corporativo com usuário e senha. As credenciais iniciais ficam protegidas em `/home/innovation/innovation-vision-ia/secrets/initial-portal-admin-credentials`; o portal não aceita token bootstrap.

## Instalação e atualização no Ubuntu

O cenário padrão usa o usuário `innovation` com `sudo` e salva tudo dentro da home:

```text
/home/innovation/innovation-vision-ia
```

Não é necessário entrar como root. Execute como `innovation`:

```bash
cd /home/innovation/innovation-vision-ia
bash scripts/install_ubuntu.sh
```

Se o script precisar de privilégios, ele chama `sudo` automaticamente. Também é válido executar diretamente com:

```bash
sudo -E bash scripts/install_ubuntu.sh
```

O mesmo comando serve para instalação limpa e para atualização. Em uma atualização o script:

1. atualiza os pacotes Ubuntu;
2. cria backup de `.env`, `secrets`, `configs` e PostgreSQL quando disponível em `/home/innovation/vision-backups/`;
3. executa `git fetch` e `git pull --ff-only` na branch `main`;
4. preserva `.env`, `data/`, `models/`, `logs/`, `backups/` e `secrets/`;
5. aplica migrations;
6. reconstrói as imagens da aplicação;
7. recria/sube os serviços;
8. executa health checks locais.

O instalador adiciona `innovation` ao grupo `docker`. Na primeira instalação, saia e entre novamente na sessão SSH/shell depois da conclusão para que o grupo seja aplicado ao terminal interativo.

### Opções úteis

Instalação normal, sem Wine/P2P:

```bash
bash scripts/install_ubuntu.sh
```

Ativar também a fase Intelbras/Wine/P2P:

```bash
ENABLE_P2P=yes INSTALL_WINE=yes bash scripts/install_ubuntu.sh
```

Não executar `apt upgrade` completo:

```bash
FULL_UPGRADE=no bash scripts/install_ubuntu.sh
```

Somente preparar/atualizar sem subir a stack:

```bash
START_STACK=no bash scripts/install_ubuntu.sh
```

Não atualizar o Git nesta execução:

```bash
UPDATE_REPOSITORY=no bash scripts/install_ubuntu.sh
```

## P2P/Wine final

O stack padrão continua sem Wine/P2P. O código final fica atrás do profile `p2p`.

Serviços locais quando ativados:

- P2P Supervisor: `127.0.0.1:8090`
- StreamBroker: `127.0.0.1:8091`
- Failover Orchestrator: `127.0.0.1:8092`
- Watchdog: serviço interno, sem porta publicada.

`INTELBRAS_VENDOR_ADAPTER_ENABLED=false` permanece como default. Sem o executável autorizado, a abertura real de sessão é bloqueada e o watchdog não dispara alterações. Uma sessão só pode virar `ACTIVE` após probe de frames reais. O watchdog mede saúde do túnel; depois do limiar configurado de falhas o StreamBroker aplica hysteresis/cooldown e o Failover Orchestrator executa make-before-break, valida todas as rotas e faz rollback explícito em qualquer falha.

Nenhum SDK/DLL/EXE Intelbras proprietário é versionado neste repositório.

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
