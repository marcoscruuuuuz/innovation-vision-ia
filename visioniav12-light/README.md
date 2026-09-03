# INNOVATION VISION IA V12 LIGHT

Novo runtime isolado para monitoramento multicâmera da INNOVATION RP TELECOM.

## Objetivo

Criar uma nova VM no Proxmox, preservar as VMs Wine/T2U existentes e implantar um sistema leve baseado no motor funcional de julho:

```text
frame fresco
→ microbatch 16
→ YOLO11n CUDA
→ ByteTrack por câmera
→ StateMonitor
→ regras geométricas determinísticas
→ certificação
→ snapshot + clipe MP4 de 15 s
→ log
```

## Branch

`visioniav12-light-v1`

O código desta pasta é independente do runtime atual. O sistema antigo não é atualizado por estes manifests.

## Capacidades reaproveitadas

- 1 P2P lógico por DVR.
- 1 LocalPort/fixed endpoint por DVR.
- 1 LoginEx2 canônico por DVR.
- N canais = N RealPlay handles.
- StartListenEx, QueryRecordFile, RealPlay, Playback, Snapshot e health reutilizando a sessão canônica.
- Prata32 já comprovado com 32/32 canais e bytes durante janela prolongada.
- Recuperação seletiva por DVR; sem restart global de Wine/T2U.

## Motor inicial

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

## URLs planejadas

- Administração: `https://visioniav12.innovationrptelecom.com.br/admin`
- Portal do cliente: `https://visioniav12.innovationrptelecom.com.br/portal`
- API: `https://visioniav12.innovationrptelecom.com.br/api`

O Cloudflare Tunnel aponta o hostname para o serviço web local. O acesso administrativo deve receber uma política Cloudflare Access separada.

## Implantação

1. Execute `infra/proxmox/create-vm.sh` no host Proxmox para criar a VM sem GPU.
2. Instale o sistema base e valide a rede.
3. Na janela de manutenção, desligue somente a VM antiga que possui a GPU. Não desligue as VMs Wine/T2U.
4. Execute `infra/proxmox/gpu-cutover.sh` para mover a GPU para a nova VM.
5. Execute `infra/guest/bootstrap.sh` dentro da nova VM.
6. Preencha `.env` e `config/gateways.yaml` sem versionar segredos.
7. Execute `docker compose up -d --build`.
8. Configure o Cloudflare Tunnel conforme `infra/cloudflare/README.md`.
9. Execute `scripts/smoke.sh`.

## Limite honesto de quatro horas

Em até quatro horas deve existir um canário real com VM, GPU, quatro câmeras, YOLO11n, ByteTrack, ROI/linha, log, snapshot e clipe. A homologação das 474 câmeras e de todas as regras exige expansão e testes por câmera; não é convertida artificialmente em PASS no prazo do canário.

## Segurança

- Nenhuma senha, token Cloudflare, chave SSH, credencial DVR ou binário proprietário Intelbras entra no Git.
- A GPU não é anexada a duas VMs simultaneamente.
- O script de cutover exige confirmação explícita e gera rollback.
- Administração e cliente possuem autenticação e escopos separados.
