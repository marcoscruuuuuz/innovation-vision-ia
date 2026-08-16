#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VISION_ROOT="${VISION_ROOT:-/opt/vision}"
INSTALL_TAILSCALE="${INSTALL_TAILSCALE:-yes}"
INSTALL_WINE="${INSTALL_WINE:-no}"
INSTALL_NVIDIA_TOOLKIT="${INSTALL_NVIDIA_TOOLKIT:-auto}"
INSTALL_NVIDIA_DRIVER="${INSTALL_NVIDIA_DRIVER:-no}"
START_INFRA="${START_INFRA:-yes}"
VISION_USER="${VISION_USER:-vision}"
REBOOT_REQUIRED=0

log() { printf '\n[vision-install] %s\n' "$*"; }
warn() { printf '\n[vision-install][WARN] %s\n' "$*" >&2; }
die() { printf '\n[vision-install][ERROR] %s\n' "$*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || die 'execute como root: sudo bash scripts/install_ubuntu.sh'
[[ -r /etc/os-release ]] || die '/etc/os-release não encontrado'
# shellcheck disable=SC1091
source /etc/os-release
[[ ${ID:-} == ubuntu ]] || die 'este instalador suporta Ubuntu Server'

case "${VERSION_ID:-}" in
  24.04|22.04) ;;
  *) die "Ubuntu ${VERSION_ID:-desconhecido} não homologado; use 24.04 LTS ou 22.04 LTS" ;;
esac

ARCH="$(dpkg --print-architecture)"
[[ "$ARCH" == amd64 ]] || die "arquitetura $ARCH não homologada; esperado amd64"
CODENAME="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
[[ -n "$CODENAME" ]] || die 'não foi possível determinar o codename Ubuntu'

export DEBIAN_FRONTEND=noninteractive

log 'Atualizando pacotes base'
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl gnupg openssl jq git rsync \
  python3 python3-yaml shellcheck pciutils lsb-release \
  apt-transport-https software-properties-common

remove_if_installed() {
  local pkg
  for pkg in "$@"; do
    if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'ok installed'; then
      apt-get remove -y "$pkg"
    fi
  done
}

install_docker() {
  log 'Instalando Docker Engine e Docker Compose pelo repositório oficial'
  remove_if_installed docker.io docker-compose docker-compose-v2 docker-doc podman-docker containerd runc
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  cat >/etc/apt/sources.list.d/docker.sources <<DOCKER_EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${CODENAME}
Components: stable
Architectures: ${ARCH}
Signed-By: /etc/apt/keyrings/docker.asc
DOCKER_EOF
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
  docker version >/dev/null
  docker compose version >/dev/null
}

install_wine() {
  [[ "$INSTALL_WINE" == yes ]] || { log 'Wine/P2P adiado; não será instalado nesta fase'; return 0; }
  log 'Instalando Wine 64/32 bits para a fase final P2P'
  dpkg --add-architecture i386
  apt-get update
  apt-get install -y wine64 wine32:i386 winbind cabextract xvfb
  wine --version || true
}

install_tailscale() {
  [[ "$INSTALL_TAILSCALE" == yes ]] || return 0
  log 'Instalando Tailscale pelo repositório oficial'
  install -m 0755 -d /usr/share/keyrings
  curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${CODENAME}.noarmor.gpg" -o /usr/share/keyrings/tailscale-archive-keyring.gpg
  curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${CODENAME}.tailscale-keyring.list" -o /etc/apt/sources.list.d/tailscale.list
  apt-get update
  apt-get install -y tailscale
  systemctl enable --now tailscaled
  warn 'Tailscale instalado. Autentique manualmente com: sudo tailscale up'
}

has_nvidia_hardware() { lspci 2>/dev/null | grep -qi 'NVIDIA'; }

install_nvidia() {
  local should_install=no
  case "$INSTALL_NVIDIA_TOOLKIT" in
    yes) should_install=yes ;;
    no) return 0 ;;
    auto)
      if has_nvidia_hardware || command -v nvidia-smi >/dev/null 2>&1; then should_install=yes; fi
      ;;
    *) die 'INSTALL_NVIDIA_TOOLKIT deve ser auto, yes ou no' ;;
  esac
  [[ "$should_install" == yes ]] || { log 'GPU NVIDIA não detectada; NVIDIA Container Toolkit não será instalado'; return 0; }
  if ! command -v nvidia-smi >/dev/null 2>&1 || ! nvidia-smi >/dev/null 2>&1; then
    if [[ "$INSTALL_NVIDIA_DRIVER" == yes ]]; then
      log 'GPU NVIDIA detectada sem driver funcional; instalando driver recomendado Ubuntu'
      apt-get install -y ubuntu-drivers-common
      ubuntu-drivers install
      REBOOT_REQUIRED=1
    else
      warn 'GPU NVIDIA detectada, mas nvidia-smi não está funcional. Instale/homologue o driver e reexecute o script.'
    fi
  fi
  log 'Instalando NVIDIA Container Toolkit'
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' > /etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update
  apt-get install -y nvidia-container-toolkit
  nvidia-ctk runtime configure --runtime=docker
  systemctl restart docker
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then log 'Driver NVIDIA funcional; runtime Docker configurado'; else warn 'Toolkit configurado, mas validação GPU depende de reboot/driver funcional'; fi
}

create_service_user() {
  if ! id "$VISION_USER" >/dev/null 2>&1; then
    log "Criando usuário de serviço $VISION_USER"
    useradd --system --home-dir "$VISION_ROOT" --create-home --shell /usr/sbin/nologin "$VISION_USER"
  fi
}

install_repository() {
  log "Instalando estrutura em $VISION_ROOT"
  mkdir -p "$VISION_ROOT"
  if [[ "$(readlink -f "$SOURCE_DIR")" != "$(readlink -f "$VISION_ROOT")" ]]; then
    rsync -a --delete --exclude='.git/' --exclude='.env' --exclude='data/postgres/*' --exclude='data/redis/*' --exclude='data/minio/*' --exclude='models/*' --exclude='logs/*' --exclude='backups/*' --exclude='secrets/*' "$SOURCE_DIR/" "$VISION_ROOT/"
  fi
  mkdir -p "$VISION_ROOT/data/postgres" "$VISION_ROOT/data/redis" "$VISION_ROOT/data/minio" "$VISION_ROOT/models" "$VISION_ROOT/logs" "$VISION_ROOT/backups" "$VISION_ROOT/secrets/vendor/intelbras" "$VISION_ROOT/secrets/cameras" "$VISION_ROOT/secrets/evolution"
  if [[ ! -f "$VISION_ROOT/.env" ]]; then
    cp "$VISION_ROOT/.env.example" "$VISION_ROOT/.env"
    local postgres_password minio_password grafana_password admin_token
    postgres_password="$(openssl rand -hex 32)"
    minio_password="$(openssl rand -hex 32)"
    grafana_password="$(openssl rand -hex 24)"
    admin_token="$(openssl rand -hex 32)"
    sed -i "s/CHANGE_ME_POSTGRES/${postgres_password}/" "$VISION_ROOT/.env"
    sed -i "s/CHANGE_ME_MINIO/${minio_password}/" "$VISION_ROOT/.env"
    sed -i "s/CHANGE_ME_GRAFANA/${grafana_password}/" "$VISION_ROOT/.env"
    sed -i "s/CHANGE_ME_ADMIN_TOKEN/${admin_token}/" "$VISION_ROOT/.env"
    printf '%s\n' "$admin_token" >"$VISION_ROOT/secrets/bootstrap-admin-token"
  fi
  chown "$VISION_USER:$VISION_USER" "$VISION_ROOT"
  find "$VISION_ROOT" -mindepth 1 -maxdepth 1 ! -name data -exec chown -R "$VISION_USER:$VISION_USER" {} +
  chmod 0750 "$VISION_ROOT/secrets"
  find "$VISION_ROOT/secrets" -type d -exec chmod 0750 {} +
  find "$VISION_ROOT/secrets" -type f -exec chmod 0600 {} +
  chmod 0600 "$VISION_ROOT/.env"
}

core_services=(postgres redis minio prometheus node-exporter grafana api ingestion-api detection-worker rule-worker certification-worker retention-worker portal-admin portal-client)

install_systemd_unit() {
  log 'Instalando unidade systemd da plataforma VISION'
  cat >/etc/systemd/system/innovation-vision-infra.service <<UNIT_EOF
[Unit]
Description=INNOVATION VISION IA core platform
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${VISION_ROOT}
ExecStart=/usr/bin/docker compose --env-file ${VISION_ROOT}/.env up -d ${core_services[*]}
ExecStop=/usr/bin/docker compose --env-file ${VISION_ROOT}/.env stop
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
UNIT_EOF
  systemctl daemon-reload
  systemctl enable innovation-vision-infra.service
}

validate_installation() {
  log 'Validando repositório e Compose'
  cd "$VISION_ROOT"
  bash scripts/validate_repo.sh
  docker compose --env-file .env config --quiet
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    if docker info 2>/dev/null | grep -qi nvidia; then log 'Runtime NVIDIA visível ao Docker'; else warn 'runtime NVIDIA não apareceu no docker info; revisar configuração'; fi
  fi
}

start_infrastructure() {
  [[ "$START_INFRA" == yes ]] || return 0
  log 'Subindo plataforma VISION sem Wine/P2P'
  cd "$VISION_ROOT"
  docker compose --env-file .env up -d "${core_services[@]}"
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then docker compose --env-file .env --profile gpu up -d dcgm-exporter || warn 'DCGM exporter não iniciou; revisar driver/runtime NVIDIA'; fi
  docker compose --env-file .env ps
}

main() {
  install_docker
  install_wine
  install_tailscale
  install_nvidia
  create_service_user
  install_repository
  install_systemd_unit
  validate_installation
  start_infrastructure
  log 'Instalação da plataforma base concluída'
  printf 'Diretório: %s\n' "$VISION_ROOT"
  printf 'Validador: %s/scripts/validate_repo.sh\n' "$VISION_ROOT"
  printf 'API local: http://127.0.0.1:8080\n'
  printf 'Portal Admin: http://127.0.0.1:8083\n'
  printf 'Portal Cliente: http://127.0.0.1:8084\n'
  printf 'Ingestion API: http://127.0.0.1:8100\n'
  printf 'Prometheus: http://127.0.0.1:9090\n'
  printf 'Grafana: http://127.0.0.1:3000\n'
  printf 'MinIO Console: http://127.0.0.1:9001\n'
  printf 'Token bootstrap admin: %s/secrets/bootstrap-admin-token\n' "$VISION_ROOT"
  printf 'Wine/P2P: fase final; habilitar explicitamente com INSTALL_WINE=yes e docker compose --profile p2p ...\n'
  if (( REBOOT_REQUIRED == 1 )); then warn 'Reboot necessário para concluir a instalação/ativação do driver NVIDIA. Após reiniciar, execute novamente este instalador.'; fi
}

main "$@"
