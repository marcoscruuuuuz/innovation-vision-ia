#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_ROOT="${SOURCE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
TARGET_ROOT="${TARGET_ROOT:-}"
PROJECT_NAME="${PROJECT_NAME:-visioniav12-platform}"
EDGE_PORT="${VISION_EDGE_PORT:-8088}"
ENABLE_CLOUDFLARE="${ENABLE_CLOUDFLARE:-yes}"
TOKEN_FILE_REL="secrets/cloudflared/visionia.token"
PUBLIC_HOST="${PUBLIC_HOST:-visioniav12.innovationrptelecom.com.br}"

log(){ printf '[%s] %s\n' "$(date --iso-8601=seconds)" "$*"; }
fail(){ log "ERROR: $*" >&2; exit 1; }

if [[ -z "$TARGET_ROOT" ]]; then
  target_container="$(docker ps --filter "label=com.docker.compose.project=${PROJECT_NAME}" --format '{{.ID}}' | head -n1 || true)"
  if [[ -n "$target_container" ]]; then
    TARGET_ROOT="$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$target_container" 2>/dev/null || true)"
  fi
fi

[[ -n "$TARGET_ROOT" ]] || fail "Could not resolve the active ${PROJECT_NAME} working directory. Set TARGET_ROOT explicitly."
[[ -f "$TARGET_ROOT/docker-compose.yml" ]] || fail "Active stack compose not found at $TARGET_ROOT/docker-compose.yml"
[[ -d "$SOURCE_ROOT/edge" ]] || fail "Source edge directory not found at $SOURCE_ROOT/edge"
[[ -f "$SOURCE_ROOT/docker-compose.edge.yml" ]] || fail "Source docker-compose.edge.yml not found"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="$TARGET_ROOT/backups/v12-platform-edge-$stamp"
mkdir -p "$backup" "$TARGET_ROOT/secrets/cloudflared"
chmod 700 "$TARGET_ROOT/secrets" "$TARGET_ROOT/secrets/cloudflared" 2>/dev/null || true

log "Active stack: $TARGET_ROOT"
log "Creating selective backup at $backup"
for item in edge docker-compose.edge.yml; do
  if [[ -e "$TARGET_ROOT/$item" ]]; then
    cp -a "$TARGET_ROOT/$item" "$backup/"
  fi
done

log "Installing unified edge files only; core P2P/Wine/T2U services are untouched"
rm -rf "$TARGET_ROOT/edge"
cp -a "$SOURCE_ROOT/edge" "$TARGET_ROOT/edge"
cp -a "$SOURCE_ROOT/docker-compose.edge.yml" "$TARGET_ROOT/docker-compose.edge.yml"

cd "$TARGET_ROOT"
export VISION_EDGE_PORT="$EDGE_PORT"

log "Rendering the active compose plus the edge override"
docker compose -f docker-compose.yml -f docker-compose.edge.yml config --quiet

log "Building and starting only the edge service"
docker compose -f docker-compose.yml -f docker-compose.edge.yml build edge
docker compose -f docker-compose.yml -f docker-compose.edge.yml up -d --no-deps edge

for _ in $(seq 1 40); do
  if curl -fsS --max-time 3 "http://127.0.0.1:${EDGE_PORT}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
curl -fsS --max-time 5 "http://127.0.0.1:${EDGE_PORT}/health" >/dev/null || fail "Unified edge did not become healthy"

curl -fsS --max-time 5 -o /dev/null "http://127.0.0.1:${EDGE_PORT}/admin/" || fail "Admin route unavailable through edge"
curl -fsS --max-time 5 -o /dev/null "http://127.0.0.1:${EDGE_PORT}/portal/" || fail "Client portal route unavailable through edge"

if [[ "${ENABLE_CLOUDFLARE,,}" == "yes" ]]; then
  if [[ -s "$TARGET_ROOT/$TOKEN_FILE_REL" ]]; then
    chmod 600 "$TARGET_ROOT/$TOKEN_FILE_REL"
    log "Starting only the visionia Cloudflare connector"
    docker compose -f docker-compose.yml -f docker-compose.edge.yml --profile cloudflare up -d --no-deps cloudflared
  else
    log "Cloudflare token not present at $TARGET_ROOT/$TOKEN_FILE_REL"
    log "Place only the token for tunnel visionia in that file, chmod 600 it, and re-run this script."
  fi
fi

cat <<EOF

V12 PLATFORM EDGE INSTALLED
Active stack: $TARGET_ROOT
Local unified origin: http://127.0.0.1:${EDGE_PORT}
Admin:  http://127.0.0.1:${EDGE_PORT}/admin/
Portal: http://127.0.0.1:${EDGE_PORT}/portal/
API:    http://127.0.0.1:${EDGE_PORT}/api/
Public hostname target: https://${PUBLIC_HOST}
Cloudflare token file: $TARGET_ROOT/$TOKEN_FILE_REL
Backup: $backup

No Wine/T2U, P2P, detector, database, MinIO or existing portal service was globally restarted.
EOF
