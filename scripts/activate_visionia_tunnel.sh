#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_NAME="${PROJECT_NAME:-visioniav12-platform}"
TARGET_ROOT="${TARGET_ROOT:-}"
TUNNEL_NAME="visionia"
TUNNEL_ID="b80c0e8d-4ad4-4693-90e1-76b1259d35f2"
PUBLIC_HOST="visioniav12.innovationrptelecom.com.br"

log(){ printf '[%s] %s\n' "$(date --iso-8601=seconds)" "$*"; }
fail(){ log "ERROR: $*" >&2; exit 1; }

if [[ -z "$TARGET_ROOT" ]]; then
  container="$(docker ps --filter "label=com.docker.compose.project=${PROJECT_NAME}" --format '{{.ID}}' | head -n1 || true)"
  [[ -n "$container" ]] || fail "No running container found for compose project ${PROJECT_NAME}"
  TARGET_ROOT="$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$container")"
fi

[[ -f "$TARGET_ROOT/docker-compose.edge.yml" ]] || fail "Run scripts/finish_active_v12_platform.sh first"
mkdir -p "$TARGET_ROOT/secrets/cloudflared"
chmod 700 "$TARGET_ROOT/secrets" "$TARGET_ROOT/secrets/cloudflared" 2>/dev/null || true
TOKEN_FILE="$TARGET_ROOT/secrets/cloudflared/visionia.token"

if [[ ! -s "$TOKEN_FILE" ]]; then
  printf 'Cole o token SOMENTE do tunnel %s (%s) e pressione Enter:\n' "$TUNNEL_NAME" "$TUNNEL_ID"
  IFS= read -r -s TOKEN
  printf '\n'
  [[ -n "$TOKEN" ]] || fail "Empty token"
  printf '%s' "$TOKEN" > "$TOKEN_FILE"
  unset TOKEN
  chmod 600 "$TOKEN_FILE"
fi

cd "$TARGET_ROOT"
docker compose -f docker-compose.yml -f docker-compose.edge.yml --profile cloudflare up -d --no-deps cloudflared
sleep 3
container_id="$(docker compose -f docker-compose.yml -f docker-compose.edge.yml ps -q cloudflared)"
[[ -n "$container_id" ]] || fail "cloudflared container not created"
state="$(docker inspect -f '{{.State.Status}}' "$container_id")"
[[ "$state" == "running" ]] || { docker logs --tail 80 "$container_id"; fail "cloudflared state=$state"; }

cat <<EOF
Tunnel connector started without exposing its token.
Tunnel: $TUNNEL_NAME
Tunnel ID: $TUNNEL_ID
Public hostname expected: https://$PUBLIC_HOST
Origin configured for the public hostname must be: http://edge:8088
Admin:  https://$PUBLIC_HOST/admin/
Portal: https://$PUBLIC_HOST/portal/
API:    https://$PUBLIC_HOST/api/
EOF
