# CHAMADO MESTRE P0 — INNOVATION VISION IA V12 LIGHT

## Nova VM Proxmox + GPU + Wines/T2U + administração + portal cliente + Cloudflare + motor de julho

**Prioridade:** P0  
**Branch:** `visioniav12-light-v1`  
**Hostname público:** `visioniav12.innovationrptelecom.com.br`  
**Diretório da nova aplicação:** `/opt/innovation-vision-light/visioniav12-light`  
**Prazo do canário operacional:** até 4 horas depois de os pré-requisitos e acessos estarem disponíveis.

---

## 1. Resultado obrigatório

Criar uma nova VM Ubuntu 24.04 no mesmo Proxmox, mover a GPU da VM antiga para a nova VM durante janela controlada, manter as VMs Wine/T2U ligadas e implantar um sistema novo, isolado e leve:

```text
Wine/T2U gateways existentes
→ fixed endpoint por DVR
→ sessão NetSDK canônica
→ frame fresco 960×540 / 2 FPS
→ latest-only
→ microbatch 16 / 120 ms
→ YOLO11n CUDA
→ ByteTrack por câmera
→ StateMonitor
→ ROI/linhas determinísticas
→ candidate
→ certificação
→ snapshot + MP4 15 s
→ event_log
→ Admin / Portal Cliente
→ Cloudflare Tunnel
```

---

## 2. Regra de proteção da infraestrutura

1. Identificar a VM antiga de IA e as VMs que hospedam Wine/T2U.
2. A VM antiga de IA pode ser desligada para liberar a GPU.
3. As VMs Wine/T2U NÃO podem ser desligadas durante o cutover.
4. Não mover, duplicar ou reinstalar os WinePREFIX existentes.
5. Não criar um LoginEx2 por canal.
6. Não reiniciar todos os bridges.
7. Preservar o sistema atual e seus discos para rollback.

---

## 3. Recursos iniciais da nova VM

Valores de partida, ajustáveis após inventário físico:

```text
OS: Ubuntu Server 24.04 LTS
Machine: q35 + OVMF
CPU: host, 24 vCPU
RAM: 32 GiB, balloon=0
Disk: 200 GiB inicial
NIC: VirtIO / vmbr0
QEMU Guest Agent: enabled
GPU: RTX 5060 8 GB via PCI passthrough
Autostart: desabilitado até homologação
```

A VM é criada sem GPU. A GPU só é movida após:

```text
backup configs Proxmox
nova VM base e rede PASS
nova VM parada
VM antiga desligada de forma limpa
Wine/T2U gateways confirmados ativos
rollback pronto
```

---

## 4. GPU e driver

Após o passthrough:

```text
lspci mostra NVIDIA na VM
ubuntu-drivers instala driver recomendado
reboot apenas da nova VM
nvidia-smi PASS
Docker PASS
NVIDIA Container Toolkit PASS
docker --gpus all nvidia-smi PASS
```

Não instalar driver NVIDIA no guest antes de a GPU estar realmente visível.

---

## 5. Integração com Wines/T2U

Importar somente um registry sanitizado com:

```text
gateway_id
condomínio
gateway IP
gateway API
DVR ID
fixed endpoint
channel count
stream mapping
health route
snapshot route
playback route
```

Arquitetura por DVR:

```text
1 P2P lógico
1 LocalPort dinâmico
1 fixed endpoint
1 LoginEx2 canônico
N RealPlay handles
```

O Prata32 é o canário de cardinalidade:

```text
1 → 4 → 8 → 16 → 32 canais
```

Não atribuir cap 32 a toda a frota automaticamente.

---

## 6. Motor de IA

```env
YOLO_MODEL=yolo11n.pt
YOLO_POSE_MODEL=yolo11n-pose.pt
YOLO_IMGSZ=640
YOLO_BATCH_SIZE=16
YOLO_BATCH_WAIT_MS=120
MAX_FRAME_AGE_S=10
YOLO_CONFIDENCE=0.25
RULE_REFRESH_S=5
POSE_EVERY_N=5
DETECT_FPS=2
DETECT_WIDTH=960
DETECT_HEIGHT=540
```

Regras:

- somente um detector contínuo;
- `model.names` é a fonte dos IDs das classes;
- um ByteTrack independente por câmera;
- frames antigos são descartados antes da GPU;
- pose somente em pessoas elegíveis;
- nenhuma pose humana é usada como pose de cachorro.

---

## 7. Lógica temporal original de cachorro/fezes

Por `dog track_id`:

```text
history ≈ 6 s
mínimo 4 observações
static_px = 28
static duration >= 5 s
aspect ratio change >= 0,18
→ zona_fezes_suspeita
```

Associação:

```text
owner_radius = 260 px
collect_radius = 90 px
departure_radius = 180 px
pickup presence >= 2 s
não recolheu >= 30 s após saída
```

Saídas:

```text
cachorro_fazendo_fezes
possiveis_fezes
morador_nao_recolheu_fezes
```

Todas permanecem certificadas e acompanhadas por mídia real.

---

## 8. ROI e linhas

O V1 usa motor geométrico determinístico dentro do rule-worker:

```text
point-in-polygon
segment crossing
side-of-line transition
double-line state machine
dwell
direction vector
```

Sem `nvdsanalytics` no hot path inicial.

Contrato:

```text
bbox/track
→ bottom-center
→ geometria normalizada da câmera
→ estado temporal
→ candidate
```

Toda geometria é manual e específica por câmera. Não copiar ROI genérica em massa.

---

## 9. Entrada e saída a vácuo

Entrada:

```text
mesmo track
L1 → L2
dentro do timeout
→ possible_entry_tailgating
```

Saída:

```text
mesmo track
L2 → L1
dentro do timeout
→ possible_exit_tailgating
```

Vídeo isolado produz `possible`. Confirmação exige correlação com controle de acesso.

---

## 10. Mídia obrigatória

Todo log visível deve conter:

```text
1 JPEG válido
1 MP4 válido de 15 s
```

Janela padrão:

```text
T−5 s → T+10 s
```

A certificação de eventos temporais pode consultar uma janela maior, mas o clipe do portal terá 15 segundos.

Sem mídia completa:

```text
client_visible=false
media_status=MEDIA_PENDING
```

---

## 11. Catálogo integral

O arquivo `config/events.yaml` contém:

```text
animal_em_geral
animal_solto
animal_com_tutor
cachorro_fazendo_fezes
possiveis_fezes
morador_nao_recolheu_fezes
face_detectada
pessoa_fora_horario_22h
porta_bloco_aberta
possivel_porteiro_dormindo
entrada_vacuo
saida_vacuo
porteiro_fora_posto
linha_perimetral_cerca_eletrica
linha_perimetral_disparo
porta_manutencao
linha_velocidade
lixo_no_chao
muro_condominio
area_proibida
veiculo_parado_irregular
veiculo_contramao
placa_detectada
crianca_com_pipa
criancas_jogando_bola
pessoa_bicicleta_area_comum
```

---

## 12. Classificação honesta dos eventos

### Base pronta para canário com YOLO11n + tracking

```text
animal_em_geral
animal_solto
animal_com_tutor
pessoa_fora_horario_22h
area_proibida
muro_condominio
linha_perimetral_disparo
porta_manutencao
linha_velocidade
veiculo_contramao
veiculo_parado_irregular
pessoa_bicicleta_area_comum
pessoa_com_pipa
pessoas_jogando_bola
```

### Motor temporal/certificação

```text
cachorro_fazendo_fezes
possiveis_fezes
morador_nao_recolheu_fezes
possivel_porteiro_dormindo
porteiro_fora_posto
porta_bloco_aberta
```

### Dependência externa

```text
entrada/saída a vácuo confirmadas → controle de acesso
cerca elétrica → alarme físico/SDK
```

### Exigem modelo/dados locais para precisão final

```text
face_detectada
placa_detectada
lixo_no_chao
classe etária 'criança'
```

Esses eventos entram no catálogo e nas interfaces, mas não podem ser marcados como homologados sem dados/modelos reais.

---

## 13. Administração

URL:

```text
/admin
```

Módulos mínimos:

```text
Dashboard
Condomínios
DVRs
Câmeras
Regras
Editor de ROI/linhas
Usuários e escopos
Logs
Mídia
Saúde GPU/filas/storage
Cloudflare status
Auditoria
```

O canário de quatro horas entrega Dashboard, Câmeras, Regras e Logs. Os demais módulos podem ser expandidos sem trocar a API.

---

## 14. Portal do cliente

URL:

```text
/portal
```

O cliente vê somente condomínios autorizados.

Cada log mostra:

```text
horário BRT
condomínio
câmera
evento
confiança
snapshot
clipe 15 s
certificação
```

Cross-tenant deve retornar 404/403 conforme contrato.

---

## 15. Cloudflare

Hostname único:

```text
visioniav12.innovationrptelecom.com.br
```

Rotas:

```text
/admin
/portal
/api
```

Cloudflare Tunnel é outbound-only. Não abrir PostgreSQL, Redis, MinIO, métricas ou gateways Wine/T2U na Internet.

Cloudflare Access separado para `/admin*`.

Credenciais e tunnel JSON ficam fora do Git.

---

## 16. GitHub

Repositório:

```text
marcoscruuuuuz/innovation-vision-ia
```

Branch:

```text
visioniav12-light-v1
```

Nenhum segredo entra no commit.

CI deve executar:

```text
Python compile
YAML parse
Docker Compose config
unit tests de geometria/StateMonitor
container build
```

---

## 17. Cronograma do canário — até quatro horas

```text
00:00–00:30  criar VM/base stack
00:30–01:15  integrar 1–4 câmeras reais
01:15–02:00  YOLO11n + microbatch + ByteTrack
02:00–02:40  StateMonitor + polygon/line/double-line
02:40–03:20  snapshot + clip 15 s + event_log
03:20–03:45  Prata32 progressivo até 32 canais
03:45–04:00  freeze, hashes, evidências e relatório
```

A transferência física da GPU e eventual reboot da nova VM dependem da duração da janela de manutenção e podem acrescentar tempo operacional externo.

---

## 18. Aceite das quatro horas

```text
NEW_VM_BASE = PASS
GPU_IN_NEW_VM = PASS
NVIDIA_SMI = PASS
DOCKER_GPU = PASS
WINE_T2U_VMS_UNTOUCHED = PASS
ONE_CANONICAL_LOGIN_PER_DVR = PASS
REAL_FRAME_4_CAMERAS = PASS
LATEST_ONLY = PASS
YOLO11N_CUDA = PASS
MICROBATCH_16 = PASS
BYTETRACK_PER_CAMERA = PASS
DOG_DIRECT_DETECTION = PASS
PERSON_DIRECT_DETECTION = PASS
POLYGON = PASS
SINGLE_LINE = PASS
DOUBLE_LINE = PASS
DOG_STATE_MONITOR = PASS
SNAPSHOT = PASS
CLIP_15S = PASS
EVENT_LOG = PASS
ADMIN_PAGE = PASS
CLIENT_PORTAL = PASS
CLOUDFLARE_PUBLIC_ROUTE = PASS
PRATA32_PROGRESSIVE = PASS_OR_DEVICE_LIMIT_DOCUMENTED
GLOBAL_WINE_T2U_RESTARTS = 0
```

---

## 19. O que não pode ser falsamente declarado em quatro horas

```text
474 câmeras homologadas
todas as ROIs desenhadas
todos os eventos com precisão final
face/placa/lixo custom homologados
controle de acesso integrado em todos os condomínios
24 h soak
7 dias de retenção observados
```

O runtime e o catálogo ficam prontos; a homologação é progressiva por câmera e regra.

---

## 20. Ordem vinculante

```text
01 inventariar Proxmox, VM antiga, GPU e Wine VMs
02 backup das configs
03 criar nova VM sem GPU
04 configurar rede/SSH
05 confirmar Wines/T2U permanecem ativos
06 desligar somente VM antiga de IA
07 mover GPU para nova VM
08 instalar driver/toolkit e validar GPU
09 clonar branch GitHub
10 configurar secrets e gateway registry
11 subir DB/Redis/MinIO/API
12 integrar 1 câmera
13 integrar 4 câmeras
14 ativar YOLO11n/microbatch/ByteTrack
15 validar cachorro e pessoa
16 validar polygon/line/double-line
17 validar StateMonitor
18 validar snapshot + clip 15 s
19 validar Admin + Portal
20 publicar Cloudflare
21 testar Prata32 progressivamente
22 congelar o canário e registrar hashes
23 expandir somente depois do PASS
```

---

## 21. Proibições

```text
não desligar Wine/T2U gateways
não anexar GPU a duas VMs
não usar dois detectores contínuos
não criar login por câmera
não criar P2P por câmera
não importar storage do sistema quebrado
não persistir frames brutos continuamente
não gerar log sem mídia
não copiar ROI em massa
não versionar segredos/SDK proprietário
não prometer precisão sem Gold positivo e negativo
```

---

## 22. Resultado final

O executor deve entregar:

```text
nova VM documentada
GPU validada
stack GitHub implantada
Wines/T2U interligados
Admin público protegido
Portal cliente público protegido
motor YOLO11n/ByteTrack/StateMonitor funcional
catálogo completo configurado
canários de cachorro e ROI aprovados
snapshot + clip 15 s
Prata32 consumido sem duplicar login
rollback completo
```
