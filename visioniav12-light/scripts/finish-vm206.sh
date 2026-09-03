#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/opt/innovation-vision-light/visioniav12-light}"
REPOSITORY_DIR="${REPOSITORY_DIR:-/opt/innovation-vision-light}"
BRANCH="${BRANCH:-visioniav12-light-v1}"
CLIENT_SCOPE="${CLIENT_SCOPE:-}"
ENABLE_CLOUDFLARE="${ENABLE_CLOUDFLARE:-yes}"
RUN_SMOKE="${RUN_SMOKE:-yes}"

log() { printf '[%s] %s\n' "$(date --iso-8601=seconds)" "$*"; }
fail() { log "ERROR: $*" >&2; exit 1; }

[[ -d "${REPOSITORY_DIR}/.git" ]] || fail "Git repository not found at ${REPOSITORY_DIR}"

log "Synchronizing branch ${BRANCH} without touching main"
git -C "${REPOSITORY_DIR}" fetch origin "${BRANCH}"
git -C "${REPOSITORY_DIR}" checkout "${BRANCH}"
git -C "${REPOSITORY_DIR}" pull --ff-only origin "${BRANCH}"

[[ -d "${ROOT_DIR}" ]] || fail "Application directory not found: ${ROOT_DIR}"
cd "${ROOT_DIR}"

mkdir -p config models secrets/gateway secrets/cloudflared reports
chmod 700 secrets secrets/gateway secrets/cloudflared

if [[ ! -f config/gateways.yaml ]]; then
  fail "config/gateways.yaml is required. Populate it with the real Wine/T2U gateway and camera registry."
fi

if [[ ! -f .env ]]; then
  [[ -n "${CLIENT_SCOPE}" ]] || fail "Set CLIENT_SCOPE to the initial client's real condominium name."
  log "Generating local credentials and application secrets"
  CLIENT_SCOPE="${CLIENT_SCOPE}" bash scripts/bootstrap-production.sh
fi

[[ -f models/yolo11n.pt ]] || fail "models/yolo11n.pt is missing"
[[ -f models/yolo11n-pose.pt ]] || log "WARNING: yolo11n-pose.pt is absent; human-pose rules remain disabled"

log "Validating that the V12 Light project is not sharing a database or MinIO bucket with the legacy stack"
if grep -Eq '^DATABASE_URL=.*innovation-vision-ia' .env; then
  fail "The V12 Light DATABASE_URL appears to point at the legacy database"
fi
if ! grep -Eq '^MINIO_BUCKET=("?vision-light"?)$' .env; then
  fail "MINIO_BUCKET must be vision-light for isolation"
fi

log "Recording active Compose projects before cutover"
docker ps --format '{{.Label "com.docker.compose.project"}}\t{{.Names}}\t{{.Image}}' \
  | sort -u > "reports/compose-projects-before-$(date -u +%Y%m%dT%H%M%SZ).txt"

log "Building and starting the canonical V12 Light stack"
docker compose config --quiet
docker compose build api ingest detector rules media retention
docker compose up -d postgres redis minio api

log "Waiting for the API"
for _ in $(seq 1 60); do
  if curl -fsS --max-time 3 http://127.0.0.1:8080/health >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
curl -fsS --max-time 5 http://127.0.0.1:8080/health >/dev/null \
  || fail "API did not become healthy"

log "Synchronizing cameras and seeding rule drafts through the application model"
docker compose run --rm api python seed.py

docker compose up -d ingest detector rules media retention

log "Waiting for worker health"
sleep 10
for service in api ingest detector rules media retention; do
  container_id="$(docker compose ps -q "${service}")"
  [[ -n "${container_id}" ]] || fail "Missing container for service ${service}"
  state="$(docker inspect -f '{{.State.Status}}' "${container_id}")"
  [[ "${state}" == "running" ]] || fail "Service ${service} is ${state}"
done

if [[ "${ENABLE_CLOUDFLARE,,}" == "yes" ]]; then
  if [[ -s secrets/cloudflared/visionia.token ]]; then
    log "Starting the existing visionia Cloudflare tunnel connector"
    docker compose --profile cloudflare up -d cloudflared
  else
    log "Cloudflare token file is absent. Run infra/cloudflare/activate-visionia.sh after obtaining the token for tunnel visionia."
  fi
fi

if [[ "${RUN_SMOKE,,}" == "yes" ]]; then
  log "Running the deployment acceptance smoke"
  bash scripts/smoke.sh
fi

log "Deployment state"
docker compose ps

cat <<EOF

V12 LIGHT APPLICATION DEPLOYED
Admin local:  http://127.0.0.1:8080/admin
Portal local: http://127.0.0.1:8080/portal
Public admin: https://visioniav12.innovationrptelecom.com.br/admin
Public portal:https://visioniav12.innovationrptelecom.com.br/portal
Credentials:  ${ROOT_DIR}/secrets/initial-access-credentials.txt

The production event writer remains controlled by PRODUCTION_WRITER_ENABLED.
Enable it only after a real camera/rule/media canary is complete.
EOF
