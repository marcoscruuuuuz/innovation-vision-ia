# 08 - Instalação Ubuntu

## Alvo

- Ubuntu Server 24.04 LTS recomendado; Ubuntu 22.04 LTS aceito pelo bootstrap.
- amd64/x86_64, Docker Engine e Docker Compose plugin.
- Wine é opcional e só é instalado para P2P autorizado.
- Tailscale pode ser instalado, mas a autenticação e as ACLs continuam explícitas.
- NVIDIA Container Toolkit é configurado apenas quando o hardware/driver NVIDIA estiver operacional.

## Caminho canônico

O usuário operacional é `innovation`, com sudo, e existe um único diretório canônico:

```text
/home/innovation/innovation-vision-ia
```

Não use `/opt/vision`; esse caminho não faz parte da instalação suportada.

## Instalação e atualização

Execute como `innovation`:

```bash
cd /home/innovation/innovation-vision-ia
bash scripts/install_ubuntu.sh
```

O script se eleva com sudo quando necessário. Também é válido:

```bash
sudo -E bash scripts/install_ubuntu.sh
```

Em instalação limpa, ele cria as credenciais locais e o token bootstrap em:

```text
/home/innovation/innovation-vision-ia/secrets/bootstrap-admin-token
```

Em atualização, ele faz backup de `.env`, `secrets`, `configs` e PostgreSQL quando disponível, preserva os dados de runtime, aplica migrations e reconstrói/sobe explicitamente todos os workers, incluindo `temporal-worker` e `clip-builder`.

## Opções úteis

```bash
ENABLE_P2P=yes INSTALL_WINE=yes bash scripts/install_ubuntu.sh
FULL_UPGRADE=no bash scripts/install_ubuntu.sh
START_STACK=no bash scripts/install_ubuntu.sh
UPDATE_REPOSITORY=no bash scripts/install_ubuntu.sh
```

## NVIDIA, Tailscale e Intelbras

O instalador não substitui silenciosamente o driver NVIDIA. Se houver GPU sem `nvidia-smi` funcional, ele registra a pendência; instalar driver exige `INSTALL_NVIDIA_DRIVER=yes`.

A autenticação Tailscale é consciente e posterior à instalação:

```bash
sudo tailscale up
```

SDKs, DLLs e executáveis Intelbras não entram no Git. O pacote autorizado é mantido somente em runtime:

```text
/home/innovation/innovation-vision-ia/secrets/vendor/intelbras/
```

## Validação

```bash
cd /home/innovation/innovation-vision-ia
bash scripts/validate_repo.sh
docker compose --env-file .env config --quiet
docker compose --env-file .env --profile p2p config --quiet
```
