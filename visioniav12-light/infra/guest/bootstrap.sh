#!/usr/bin/env bash
set -Eeuo pipefail

# Run inside the new Ubuntu 24.04 VM.
# Requires GPU already attached for the driver phase.

[[ ${EUID} -eq 0 ]] || { echo 'Execute com sudo/root.' >&2; exit 1; }

INSTALL_NVIDIA_DRIVER="${INSTALL_NVIDIA_DRIVER:-yes}"
REPO_URL="${REPO_URL:-https://github.com/marcoscruuuuuz/innovation-vision-ia.git}"
REPO_BRANCH="${REPO_BRANCH:-visioniav12-light-v1}"
INSTALL_DIR="${INSTALL_DIR:-/opt/innovation-vision-light}"
APP_SUBDIR="visioniav12-light"
APP_USER="${APP_USER:-innovation}"
CUDA_SMOKE_IMAGE="${CUDA_SMOKE_IMAGE:-nvidia/cuda:12.8.1-base-ubuntu24.04}"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl gnupg git jq unzip make openssl \
  qemu-guest-agent ubuntu-drivers-common pciutils \
  python3 python3-venv ffmpeg
systemctl enable --now qemu-guest-agent

if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd -m -s /bin/bash "$APP_USER"
fi

# Docker official repository
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable" >/etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
usermod -aG docker "$APP_USER"

if [[ "$INSTALL_NVIDIA_DRIVER" == "yes" ]]; then
  lspci -nn | grep -i nvidia || { echo 'GPU NVIDIA não está visível na VM. Abortando driver.' >&2; exit 10; }
  ubuntu-drivers install
  touch /var/run/visioniav12-reboot-required
  echo 'Driver instalado. Reinicie a VM e execute este script novamente com INSTALL_NVIDIA_DRIVER=no.'
  exit 20
fi

nvidia-smi

# NVIDIA Container Toolkit official repository
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#' \
  > /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt-get update
apt-get install -y nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker

docker run --rm --gpus all "$CUDA_SMOKE_IMAGE" nvidia-smi

install -d -m 0755 "$(dirname "$INSTALL_DIR")"
if [[ ! -d "$INSTALL_DIR/.git" ]]; then
  git clone --branch "$REPO_BRANCH" --single-branch "$REPO_URL" "$INSTALL_DIR"
else
  git -C "$INSTALL_DIR" fetch origin "$REPO_BRANCH"
  git -C "$INSTALL_DIR" checkout "$REPO_BRANCH"
  git -C "$INSTALL_DIR" pull --ff-only origin "$REPO_BRANCH"
fi

cd "$INSTALL_DIR/$APP_SUBDIR"
[[ -f .env ]] || cp .env.example .env
[[ -f config/gateways.yaml ]] || cp config/gateways.example.yaml config/gateways.yaml
install -d -m 0750 models secrets/cloudflared
chown -R "$APP_USER:$APP_USER" "$INSTALL_DIR"
chmod 0600 .env || true

cat <<EOF
Bootstrap base concluído.

Próximos passos obrigatórios:
1. preencher $INSTALL_DIR/$APP_SUBDIR/.env;
2. preencher config/gateways.yaml com IPs/ports reais dos Wines/T2U;
3. copiar yolo11n.pt e yolo11n-pose.pt para models/;
4. configurar credencial Cloudflare fora do Git;
5. executar docker compose config;
6. executar docker compose up -d --build;
7. executar scripts/smoke.sh.
EOF
