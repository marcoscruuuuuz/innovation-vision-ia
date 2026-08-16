#!/usr/bin/env bash
set -Eeuo pipefail

# INNOVATION VISION IA - instalador/atualizador unico para Ubuntu.
# Uso normal (usuario innovation):
#   bash scripts/install_ubuntu.sh
# ou:
#   sudo -E bash scripts/install_ubuntu.sh
#
# O script aceita execucao sem root e se eleva automaticamente via sudo.
# Instalacao canonica: /home/innovation/innovation-vision-ia

VISION_USER="${VISION_USER:-innovation}"
VISION_HOME="${VISION_HOME:-/home/${VISION_USER}}"
VISION_ROOT="${VISION_ROOT:-${VISION_HOME}/innovation-vision-ia}"
REPO_URL="${REPO_URL:-https://github.com/marcoscruuuuuz/innovation-vision-ia.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
INSTALL_TAILSCALE="${INSTALL_TAILSCALE:-yes}"
INSTALL_WINE="${INSTALL_WINE:-no}"
INSTALL_NVIDIA_TOOLKIT="${INSTALL_NVIDIA_TOOLKIT:-auto}"
INSTALL_NVIDIA_DRIVER="${INSTALL_NVIDIA_DRIVER:-no}"
START_STACK="${START_STACK:-yes}"
ENABLE_P2P="${ENABLE_P2P:-no}"
ENABLE_T2U_GATEWAY="${ENABLE_T2U_GATEWAY:-auto}"
T2U_GATEWAY_ROOT="${T2U_GATEWAY_ROOT:-/opt/vision-ia/qr-gateway}"
FULL_UPGRADE="${FULL_UPGRADE:-yes}"
UPDATE_REPOSITORY="${UPDATE_REPOSITORY:-yes}"
BACKUP_BEFORE_UPDATE="${BACKUP_BEFORE_UPDATE:-yes}"
REBUILD_IMAGES="${REBUILD_IMAGES:-yes}"
REBOOT_REQUIRED=0
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

log() { printf '\n[vision-install] %s\n' "$*"; }
warn() { printf '\n[vision-install][WARN] %s\n' "$*" >&2; }
die() { printf '\n[vision-install][ERROR] %s\n' "$*" >&2; exit 1; }

as_user() {
  sudo -u "$VISION_USER" -H "$@"
}

require_sudo() {
  if [[ ${EUID} -ne 0 ]]; then
    command -v sudo >/dev/null 2>&1 || die 'sudo nao encontrado'
    sudo -v || die 'o usuario atual precisa ter sudo ativo'
    exec sudo -E bash "$0" "$@"
  fi
}

require_sudo "$@"

[[ -r /etc/os-release ]] || die '/etc/os-release nao encontrado'
# shellcheck disable=SC1091
source /etc/os-release
[[ ${ID:-} == ubuntu ]] || die 'este instalador suporta Ubuntu Server/Desktop'
case "${VERSION_ID:-}" in
  24.04|22.04) ;;
  *) die "Ubuntu ${VERSION_ID:-desconhecido} nao homologado; use 24.04 LTS ou 22.04 LTS" ;;
esac

ARCH="$(dpkg --print-architecture)"
[[ "$ARCH" == amd64 ]] || die "arquitetura $ARCH nao homologada; esperado amd64"
CODENAME="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
[[ -n "$CODENAME" ]] || die 'nao foi possivel determinar o codename Ubuntu'

if ! id "$VISION_USER" >/dev/null 2>&1; then
  die "usuario ${VISION_USER} nao existe. Crie-o antes ou execute com VISION_USER=<usuario>."
fi
VISION_HOME="$(getent passwd "$VISION_USER" | cut -d: -f6)"
[[ -n "$VISION_HOME" && -d "$VISION_HOME" ]] || die "home do usuario ${VISION_USER} nao encontrado"
VISION_ROOT="${VISION_ROOT:-${VISION_HOME}/innovation-vision-ia}"

export DEBIAN_FRONTEND=noninteractive

update_ubuntu() {
  log 'Atualizando Ubuntu e pacotes base'
  apt-get update
  if [[ "$FULL_UPGRADE" == yes ]]; then
    apt-get upgrade -y
    apt-get autoremove -y
  fi
  apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg openssl jq git rsync tar gzip unzip \
    python3 python3-yaml shellcheck pciutils lsb-release \
    apt-transport-https software-properties-common
}

install_docker() {
  log 'Instalando/atualizando Docker Engine e Docker Compose'
  install -m 0755 -d /etc/apt/keyrings
  if [[ ! -s /etc/apt/keyrings/docker.asc ]]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  fi
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
  usermod -aG docker "$VISION_USER"
  docker version >/dev/null
  docker compose version >/dev/null
}

install_tailscale() {
  [[ "$INSTALL_TAILSCALE" == yes ]] || return 0
  log 'Instalando/atualizando Tailscale'
  install -m 0755 -d /usr/share/keyrings
  curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${CODENAME}.noarmor.gpg" -o /usr/share/keyrings/tailscale-archive-keyring.gpg
  curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${CODENAME}.tailscale-keyring.list" -o /etc/apt/sources.list.d/tailscale.list
  apt-get update
  apt-get install -y tailscale
  systemctl enable --now tailscaled
  if ! tailscale status >/dev/null 2>&1; then
    warn 'Tailscale instalado, mas ainda nao autenticado. Use: sudo tailscale up'
  fi
}

install_wine() {
  [[ "$INSTALL_WINE" == yes ]] || { log 'Wine/P2P nao sera instalado (INSTALL_WINE=no)'; return 0; }
  log 'Instalando Wine 64/32 bits para Intelbras P2P'
  dpkg --add-architecture i386
  apt-get update
  apt-get install -y wine64 wine32:i386 winbind cabextract xvfb
  wine --version || true
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
  [[ "$should_install" == yes ]] || { log 'GPU NVIDIA nao detectada; toolkit NVIDIA ignorado'; return 0; }

  if ! command -v nvidia-smi >/dev/null 2>&1 || ! nvidia-smi >/dev/null 2>&1; then
    if [[ "$INSTALL_NVIDIA_DRIVER" == yes ]]; then
      log 'Instalando driver NVIDIA recomendado pelo Ubuntu'
      apt-get install -y ubuntu-drivers-common
      ubuntu-drivers install
      REBOOT_REQUIRED=1
    else
      warn 'GPU NVIDIA detectada sem nvidia-smi funcional. Driver nao sera alterado automaticamente.'
    fi
  fi

  log 'Instalando/atualizando NVIDIA Container Toolkit'
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    > /etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update
  apt-get install -y nvidia-container-toolkit
  nvidia-ctk runtime configure --runtime=docker
  systemctl restart docker
}

fix_application_ownership() {
  # Nunca altera ownership de data/postgres, data/redis ou data/minio.
  # Esses diretórios podem ter UIDs/GIDs internos definidos pelos containers.
  chown "$VISION_USER:$VISION_USER" "$VISION_ROOT"
  find "$VISION_ROOT" -mindepth 1 -maxdepth 1 ! -name data \
    -exec chown -R "$VISION_USER:$VISION_USER" {} +
}

backup_current_install() {
  [[ "$BACKUP_BEFORE_UPDATE" == yes ]] || return 0
  [[ -d "$VISION_ROOT" ]] || return 0
  local stamp backup_dir
  stamp="$(date +%Y%m%dT%H%M%S)"
  backup_dir="${VISION_HOME}/vision-backups/${stamp}"
  log "Criando backup pre-atualizacao em ${backup_dir}"
  mkdir -p "$backup_dir"

  if [[ -f "$VISION_ROOT/.env" ]]; then cp -a "$VISION_ROOT/.env" "$backup_dir/.env"; fi
  if [[ -d "$VISION_ROOT/secrets" ]]; then cp -a "$VISION_ROOT/secrets" "$backup_dir/secrets"; fi
  if [[ -d "$VISION_ROOT/configs" ]]; then cp -a "$VISION_ROOT/configs" "$backup_dir/configs"; fi

  if [[ -f "$VISION_ROOT/docker-compose.yml" && -f "$VISION_ROOT/.env" ]]; then
    if docker compose -f "$VISION_ROOT/docker-compose.yml" --env-file "$VISION_ROOT/.env" ps postgres --status running 2>/dev/null | grep -q postgres; then
      docker compose -f "$VISION_ROOT/docker-compose.yml" --env-file "$VISION_ROOT/.env" exec -T postgres \
        pg_dump -U "${POSTGRES_USER:-vision}" -d "${POSTGRES_DB:-vision}" -Fc >"$backup_dir/postgres.dump" || warn 'pg_dump falhou; continuando com backup de configuracao'
    fi
  fi
  chown -R "$VISION_USER:$VISION_USER" "${VISION_HOME}/vision-backups"
}

install_or_update_repository() {
  log "Preparando repositorio em ${VISION_ROOT}"
  mkdir -p "$VISION_HOME"
  chown "$VISION_USER:$VISION_USER" "$VISION_HOME"

  if [[ -d "$VISION_ROOT/.git" ]]; then
    backup_current_install
    if [[ "$UPDATE_REPOSITORY" == yes ]]; then
      log "Atualizando repositorio Git para ${REPO_BRANCH}"
      if ! as_user git -C "$VISION_ROOT" diff --quiet || ! as_user git -C "$VISION_ROOT" diff --cached --quiet; then
        die "existem alteracoes locais em ${VISION_ROOT}. Commit/stash antes de atualizar para evitar perda de dados."
      fi
      as_user git -C "$VISION_ROOT" fetch --prune origin
      as_user git -C "$VISION_ROOT" checkout "$REPO_BRANCH"
      as_user git -C "$VISION_ROOT" pull --ff-only origin "$REPO_BRANCH"
    fi
  elif [[ -d "$SOURCE_DIR/.git" ]]; then
    log 'Instalacao inicial usando a copia local do repositorio'
    mkdir -p "$VISION_ROOT"
    rsync -a --delete \
      --exclude='.env' \
      --exclude='data/postgres/***' \
      --exclude='data/redis/***' \
      --exclude='data/minio/***' \
      --exclude='models/***' \
      --exclude='logs/***' \
      --exclude='backups/***' \
      --exclude='secrets/***' \
      "$SOURCE_DIR/" "$VISION_ROOT/"
    fix_application_ownership
  else
    log "Clonando ${REPO_URL}"
    as_user git clone --branch "$REPO_BRANCH" "$REPO_URL" "$VISION_ROOT" || die 'falha no clone. Para repositorio privado, autentique o GitHub/SSH e execute novamente.'
  fi

  mkdir -p \
    "$VISION_ROOT/data/postgres" "$VISION_ROOT/data/redis" "$VISION_ROOT/data/minio" \
    "$VISION_ROOT/models" "$VISION_ROOT/logs" "$VISION_ROOT/backups" \
    "$VISION_ROOT/secrets/vendor/intelbras" "$VISION_ROOT/secrets/cameras" "$VISION_ROOT/secrets/evolution"
  fix_application_ownership
}

prepare_environment() {
  log 'Preparando .env e credenciais locais'
  if [[ ! -f "$VISION_ROOT/.env" ]]; then
    cp "$VISION_ROOT/.env.example" "$VISION_ROOT/.env"
    local postgres_password minio_password grafana_password initial_admin_password
    postgres_password="$(openssl rand -hex 32)"
    minio_password="$(openssl rand -hex 32)"
    grafana_password="$(openssl rand -hex 24)"
    initial_admin_password="$(openssl rand -hex 24)"
    sed -i "s/CHANGE_ME_POSTGRES/${postgres_password}/" "$VISION_ROOT/.env"
    sed -i "s/CHANGE_ME_MINIO/${minio_password}/" "$VISION_ROOT/.env"
    sed -i "s/CHANGE_ME_GRAFANA/${grafana_password}/" "$VISION_ROOT/.env"
    sed -i "s/CHANGE_ME_INITIAL_ADMIN_PASSWORD/${initial_admin_password}/" "$VISION_ROOT/.env"
  fi
  # Bootstrap tokens are deliberately removed: portal access is exclusively
  # username/password, with a session issued only after a successful login.
  sed -i '/^VISION_BOOTSTRAP_ADMIN_TOKEN=/d; /^VISION_PORTAL_SETUP_TOKEN=/d' "$VISION_ROOT/.env"
  rm -f "$VISION_ROOT/secrets/bootstrap-admin-token" "$VISION_ROOT/secrets/portal-setup-token"
  if ! grep -q '^VISION_INITIAL_ADMIN_USERNAME=' "$VISION_ROOT/.env"; then
    printf '\nVISION_INITIAL_ADMIN_USERNAME=innovation-admin\n' >>"$VISION_ROOT/.env"
  fi
  if ! grep -q '^VISION_INITIAL_ADMIN_PASSWORD=' "$VISION_ROOT/.env"; then
    local initial_admin_password
    initial_admin_password="$(openssl rand -hex 24)"
    printf 'VISION_INITIAL_ADMIN_PASSWORD=%s\n' "$initial_admin_password" >>"$VISION_ROOT/.env"
  fi
  local initial_admin_username initial_admin_password_value
  initial_admin_username="$(sed -n 's/^VISION_INITIAL_ADMIN_USERNAME=//p' "$VISION_ROOT/.env" | tail -n 1)"
  initial_admin_password_value="$(sed -n 's/^VISION_INITIAL_ADMIN_PASSWORD=//p' "$VISION_ROOT/.env" | tail -n 1)"
  [[ -n "$initial_admin_username" && -n "$initial_admin_password_value" && "$initial_admin_password_value" != "CHANGE_ME_INITIAL_ADMIN_PASSWORD" ]] \
    || die 'credenciais iniciais do administrador nao foram configuradas'
  printf 'usuario=%s\nsenha=%s\n' "$initial_admin_username" "$initial_admin_password_value" >"$VISION_ROOT/secrets/initial-portal-admin-credentials"

  case "$ENABLE_T2U_GATEWAY" in
    auto)
      if [[ -f "$T2U_GATEWAY_ROOT/sdk/bin/libdhnetsdk.so" && -d "$T2U_GATEWAY_ROOT/status" && -f "$T2U_GATEWAY_ROOT/imported/config-original/condo-device-map.json" && -f "$T2U_GATEWAY_ROOT/imported/config-original/fleet-secrets.json" ]]; then
        ENABLE_T2U_GATEWAY=yes
      else
        ENABLE_T2U_GATEWAY=no
      fi
      ;;
    yes|no) ;;
    *) die 'ENABLE_T2U_GATEWAY deve ser auto, yes ou no' ;;
  esac
  if [[ "$ENABLE_T2U_GATEWAY" == yes && "$ENABLE_P2P" != yes ]]; then
    die 'o gateway T2U exige ENABLE_P2P=yes'
  fi
  sed -i '/^T2U_GATEWAY_ROOT=/d; /^T2U_CAPTURE_URL=/d' "$VISION_ROOT/.env"
  printf 'T2U_GATEWAY_ROOT=%s\n' "$T2U_GATEWAY_ROOT" >>"$VISION_ROOT/.env"
  if [[ "$ENABLE_T2U_GATEWAY" == yes ]]; then
    printf 'T2U_CAPTURE_URL=http://t2u-capture:8093\n' >>"$VISION_ROOT/.env"
    log "Gateway T2U real habilitado: $T2U_GATEWAY_ROOT"
    p2p_services+=(t2u-capture t2u-status-sync)
  else
    printf 'T2U_CAPTURE_URL=\n' >>"$VISION_ROOT/.env"
    log 'Gateway T2U nao encontrado; captura SDK permanece desabilitada'
  fi

  chmod 0600 "$VISION_ROOT/.env"
  find "$VISION_ROOT/secrets" -type d -exec chmod 0750 {} +
  find "$VISION_ROOT/secrets" -type f -exec chmod 0600 {} +
  fix_application_ownership
}

validate_repository() {
  log 'Validando codigo e Docker Compose'
  cd "$VISION_ROOT"
  bash scripts/validate_repo.sh
  docker compose --env-file .env config --quiet
  if [[ "$ENABLE_P2P" == yes ]]; then
    docker compose --env-file .env --profile p2p config --quiet
  fi
}

migrate_and_build() {
  cd "$VISION_ROOT"
  log 'Aplicando migrations PostgreSQL'
  docker compose --env-file .env up -d postgres
  docker compose --env-file .env run --rm db-migrate

  if [[ "$REBUILD_IMAGES" == yes ]]; then
    log 'Reconstruindo imagens da aplicacao'
    docker compose --env-file .env build \
      api ingestion-api detection-worker rule-worker temporal-worker certification-worker \
      notification-worker retention-worker clip-builder portal-admin portal-client
    if [[ "$ENABLE_P2P" == yes ]]; then
      docker compose --env-file .env --profile p2p build \
        "${p2p_services[@]}"
    fi
  fi
}

provision_initial_admin() {
  cd "$VISION_ROOT"
  log 'Garantindo administrador inicial por usuario e senha'
  docker compose --env-file .env run --rm --no-deps api python -m app.provision_admin
}

core_services=(postgres redis minio prometheus node-exporter grafana api ingestion-api detection-worker rule-worker temporal-worker certification-worker notification-worker retention-worker clip-builder portal-admin portal-client)
p2p_services=(p2p-supervisor stream-broker failover-orchestrator p2p-watchdog)

install_systemd_unit() {
  log 'Instalando servico systemd'
  local p2p_start=""
  if [[ "$ENABLE_P2P" == yes ]]; then
    p2p_start="--profile p2p"
  fi
  cat >/etc/systemd/system/innovation-vision.service <<UNIT_EOF
[Unit]
Description=INNOVATION VISION IA
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${VISION_ROOT}
ExecStart=/usr/bin/docker compose --env-file ${VISION_ROOT}/.env ${p2p_start} up -d ${core_services[*]} $([[ "$ENABLE_P2P" == yes ]] && printf '%s' "${p2p_services[*]}")
ExecStop=/usr/bin/docker compose --env-file ${VISION_ROOT}/.env ${p2p_start} stop
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
UNIT_EOF
  systemctl daemon-reload
  systemctl enable innovation-vision.service
}

start_stack() {
  [[ "$START_STACK" == yes ]] || return 0
  cd "$VISION_ROOT"
  log 'Subindo/recriando plataforma'
  docker compose --env-file .env up -d --remove-orphans "${core_services[@]}"
  if [[ "$ENABLE_P2P" == yes ]]; then
    docker compose --env-file .env --profile p2p up -d "${p2p_services[@]}"
  fi
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    docker compose --env-file .env --profile gpu up -d dcgm-exporter || warn 'DCGM exporter nao iniciou'
  fi
  docker compose --env-file .env ps
}

health_check() {
  [[ "$START_STACK" == yes ]] || return 0
  log 'Executando health checks locais'
  local url ok_flag
  for url in \
    http://127.0.0.1:8080/health \
    http://127.0.0.1:8100/health \
    http://127.0.0.1:8083/ \
    http://127.0.0.1:8084/
  do
    ok_flag=no
    for _ in $(seq 1 30); do
      if curl -fsS "$url" >/dev/null 2>&1; then ok_flag=yes; break; fi
      sleep 2
    done
    [[ "$ok_flag" == yes ]] || die "health check falhou: ${url}"
    log "OK: ${url}"
  done
  for service in temporal-worker clip-builder; do
    docker compose --env-file .env ps --status running --services "$service" | grep -qx "$service" \
      || die "worker obrigatorio nao esta em execucao: ${service}"
    log "OK: worker ${service}"
  done
  if [[ "$ENABLE_T2U_GATEWAY" == yes ]]; then
    docker compose --env-file .env ps --status running --services t2u-capture | grep -qx t2u-capture \
      || die 'captura T2U obrigatoria nao esta em execucao'
    curl -fsS http://127.0.0.1:8093/health | grep -q '"connected_tunnels":' \
      || die 'health check da captura T2U falhou'
    log 'OK: captura e sincronizacao T2U'
  fi
}

print_summary() {
  log 'Instalacao/atualizacao concluida'
  printf 'Usuario operacional: %s\n' "$VISION_USER"
  printf 'Projeto: %s\n' "$VISION_ROOT"
  printf 'API: http://127.0.0.1:8080\n'
  printf 'Admin: http://127.0.0.1:8083\n'
  printf 'Cliente: http://127.0.0.1:8084\n'
  printf 'Ingestion: http://127.0.0.1:8100\n'
  printf 'Grafana: http://127.0.0.1:3000\n'
  printf 'MinIO Console: http://127.0.0.1:9001\n'
  printf 'Credenciais iniciais do administrador: %s/secrets/initial-portal-admin-credentials\n' "$VISION_ROOT"
  printf 'Backups de update: %s/vision-backups/\n' "$VISION_HOME"
  printf '\nPara atualizar futuramente, execute novamente:\n'
  printf '  cd %s && bash scripts/install_ubuntu.sh\n' "$VISION_ROOT"
  if [[ "$ENABLE_P2P" != yes ]]; then
    printf '\nP2P/Wine permanece desligado. Para ativar depois:\n'
    printf '  ENABLE_P2P=yes INSTALL_WINE=yes bash scripts/install_ubuntu.sh\n'
  fi
  if [[ "$ENABLE_T2U_GATEWAY" != yes ]]; then
    printf '\nGateway QR/T2U nao foi habilitado. Para ativar quando estiver instalado:\n'
    printf '  ENABLE_P2P=yes ENABLE_T2U_GATEWAY=yes bash scripts/install_ubuntu.sh\n'
  fi
  if (( REBOOT_REQUIRED == 1 )); then
    warn 'Driver NVIDIA instalado/alterado; reinicie o Ubuntu e execute o instalador novamente.'
  fi
  warn "Se esta foi a primeira instalacao, saia e entre novamente na sessao do usuario ${VISION_USER} para o grupo docker ser aplicado ao shell interativo."
}

main() {
  update_ubuntu
  install_docker
  install_tailscale
  install_wine
  install_nvidia
  install_or_update_repository
  prepare_environment
  validate_repository
  migrate_and_build
  provision_initial_admin
  install_systemd_unit
  start_stack
  health_check
  print_summary
}

main "$@"

