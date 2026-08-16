# 08 - Instalação Ubuntu

## Alvo

- Ubuntu Server 24.04 LTS recomendado.
- Ubuntu Server 22.04 LTS aceito pelo bootstrap.
- amd64/x86_64.
- Docker Engine + Docker Compose plugin.
- Wine 64/32 bits para componentes P2P compatíveis.
- Tailscale instalado, autenticação separada.
- NVIDIA Container Toolkit quando GPU/driver NVIDIA estiverem funcionais.

## Pré-requisitos de hardware

O dimensionamento final depende da quantidade de streams, resolução, FPS e modelos. Como ponto de partida para o servidor único:

- 12-16 vCPU ou superior.
- 32 GB RAM ou superior.
- NVMe.
- GPU NVIDIA dedicada quando inferência local for habilitada.

## Instalação

```bash
sudo bash scripts/install_ubuntu.sh
```

Variáveis opcionais:

```bash
INSTALL_TAILSCALE=yes
INSTALL_WINE=yes
INSTALL_NVIDIA_TOOLKIT=auto
START_INFRA=yes
VISION_ROOT=/opt/vision
sudo -E bash scripts/install_ubuntu.sh
```

## NVIDIA

O bootstrap não força substituição silenciosa de driver. Se houver GPU NVIDIA sem `nvidia-smi` funcional, ele informa a pendência. Depois de o driver estar operacional, reexecute o instalador para configurar o NVIDIA Container Toolkit.

## Tailscale

O cliente é instalado, mas `tailscale up` não é executado automaticamente porque autenticação e ACL são decisões do ambiente.

## Intelbras P2P

O instalador não baixa SDK/DLL proprietário. Após a base instalada, copiar o pacote autorizado para a área indicada em `/opt/vision/secrets/vendor/intelbras/` e registrar checksums/inventário.

## Validação

```bash
bash scripts/validate_repo.sh
sudo /opt/vision/scripts/validate_repo.sh
```

## Fontes de instalação

- Docker Engine Ubuntu: documentação oficial Docker.
- NVIDIA Container Toolkit: documentação oficial NVIDIA.
- Tailscale: pacotes oficiais Tailscale.
- Wine: pacotes da distribuição Ubuntu no bootstrap base.
