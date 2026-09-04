#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_NAME="${PROJECT_NAME:-visioniav12-platform}"
TARGET_ROOT="${TARGET_ROOT:-}"
TUNNEL_NAME="visionia"
TUNNEL_ID="b80c0e8d-4ad4-4693-90e1-76b1259d35f2"
PUBLIC_HOST="visioniav12.innovationrptelecom.com.br"
EDGE_PORT="${VISION_EDGE_PORT:-8088}"
TOKEN_FILE_REL="secrets/cloudflared/visionia.token"
PUBLIC_WAIT_SECONDS="${PUBLIC_WAIT_SECONDS:-120}"

log(){ printf '[%s] %s\n' "$(date --iso-8601=seconds)" "$*"; }
fail(){ log "ERROR: $*" >&2; exit 1; }

resolve_target_root() {
  if [[ -n "$TARGET_ROOT" ]]; then return 0; fi
  local container
  container="$(docker ps --filter "label=com.docker.compose.project=${PROJECT_NAME}" --format '{{.ID}}' | head -n1 || true)"
  [[ -n "$container" ]] || fail "No running container found for compose project ${PROJECT_NAME}"
  TARGET_ROOT="$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$container" 2>/dev/null || true)"
  [[ -n "$TARGET_ROOT" ]] || fail "Could not resolve active compose working directory"
}

check_local_origin() {
  log "Checking unified local origin before touching Cloudflare"
  curl -fsS --max-time 5 "http://127.0.0.1:${EDGE_PORT}/health" >/dev/null || fail "local edge /health failed"
  curl -fsS --max-time 5 -o /dev/null "http://127.0.0.1:${EDGE_PORT}/admin/" || fail "local /admin/ failed"
  curl -fsS --max-time 5 -o /dev/null "http://127.0.0.1:${EDGE_PORT}/portal/" || fail "local /portal/ failed"
}

ensure_token() {
  TOKEN_FILE="$TARGET_ROOT/$TOKEN_FILE_REL"
  mkdir -p "$TARGET_ROOT/secrets/cloudflared"
  chmod 700 "$TARGET_ROOT/secrets" "$TARGET_ROOT/secrets/cloudflared" 2>/dev/null || true
  if [[ ! -s "$TOKEN_FILE" ]]; then
    printf 'Cole o token SOMENTE do tunnel %s (%s) e pressione Enter:\n' "$TUNNEL_NAME" "$TUNNEL_ID"
    IFS= read -r -s TOKEN
    printf '\n'
    [[ -n "$TOKEN" ]] || fail "Empty token"
    printf '%s' "$TOKEN" > "$TOKEN_FILE"
    unset TOKEN
  fi
  chmod 600 "$TOKEN_FILE"
}

start_connector() {
  cd "$TARGET_ROOT"
  log "Starting only the cloudflared connector for tunnel ${TUNNEL_NAME}"
  docker compose -f docker-compose.yml -f docker-compose.edge.yml --profile cloudflare up -d --no-deps cloudflared
  sleep 4
  local container_id state
  container_id="$(docker compose -f docker-compose.yml -f docker-compose.edge.yml ps -q cloudflared)"
  [[ -n "$container_id" ]] || fail "cloudflared container not created"
  state="$(docker inspect -f '{{.State.Status}}' "$container_id")"
  if [[ "$state" != "running" ]]; then
    docker logs --tail 120 "$container_id" >&2 || true
    fail "cloudflared state=$state"
  fi
  docker logs --tail 120 "$container_id" 2>&1 | sed -E 's/(eyJ[A-Za-z0-9._-]+)/[REDACTED_TOKEN]/g' > "$TARGET_ROOT/reports-cloudflared-last.log" || true
}

has_dns() {
  getent ahosts "$PUBLIC_HOST" >/dev/null 2>&1
}

try_dns_route_with_management_certificate() {
  if has_dns; then return 0; fi

  local cert=""
  for candidate in \
    "${CLOUDFLARED_CERT_FILE:-}" \
    "$HOME/.cloudflared/cert.pem" \
    "/root/.cloudflared/cert.pem"; do
    if [[ -n "$candidate" && -s "$candidate" ]]; then cert="$candidate"; break; fi
  done

  if command -v cloudflared >/dev/null 2>&1 && [[ -n "$cert" ]]; then
    log "Public DNS is absent; attempting tunnel DNS route with the existing Cloudflare management certificate"
    TUNNEL_ORIGIN_CERT="$cert" cloudflared tunnel route dns "$TUNNEL_ID" "$PUBLIC_HOST" || true
  fi
}

wait_for_public() {
  local deadline=$((SECONDS + PUBLIC_WAIT_SECONDS))
  local dns_ok=0 health_ok=0 admin_ok=0 portal_ok=0
  while (( SECONDS < deadline )); do
    if has_dns; then
      dns_ok=1
      if curl -fsS --max-time 8 "https://${PUBLIC_HOST}/health" >/dev/null 2>&1; then health_ok=1; else health_ok=0; fi
      if curl -fsS --max-time 8 -o /dev/null "https://${PUBLIC_HOST}/admin/"; then admin_ok=1; else admin_ok=0; fi
      if curl -fsS --max-time 8 -o /dev/null "https://${PUBLIC_HOST}/portal/"; then portal_ok=1; else portal_ok=0; fi
      if (( health_ok && admin_ok && portal_ok )); then
        cat <<EOF
PUBLICATION_PASS
Tunnel: ${TUNNEL_NAME}
Tunnel ID: ${TUNNEL_ID}
Public hostname: https://${PUBLIC_HOST}
Origin: http://edge:8088
Health: PASS
Admin: PASS
Portal: PASS
EOF
        return 0
      fi
    fi
    sleep 5
  done

  if (( ! dns_ok )); then
    cat >&2 <<EOF
PUBLIC_DNS_ROUTE_REQUIRED
The tunnel connector may be running, but ${PUBLIC_HOST} has no resolvable DNS route.
Create the Cloudflare Tunnel published application / DNS mapping:
  Public hostname: ${PUBLIC_HOST}
  Service: http://edge:8088
  Tunnel: ${TUNNEL_NAME} (${TUNNEL_ID})
Cloudflare should create a CNAME to ${TUNNEL_ID}.cfargotunnel.com.
After creating the published application, rerun this script. No Wine/T2U or core service restart is required.
EOF
    return 42
  fi

  cat >&2 <<EOF
PUBLIC_ROUTE_UNHEALTHY
DNS resolves, but one or more public routes did not pass within ${PUBLIC_WAIT_SECONDS}s.
Check Cloudflare published application service URL is exactly http://edge:8088 and inspect reports-cloudflared-last.log.
EOF
  return 43
}

resolve_target_root
[[ -f "$TARGET_ROOT/docker-compose.edge.yml" ]] || fail "docker-compose.edge.yml not found in active stack"
check_local_origin
ensure_token
start_connector
try_dns_route_with_management_certificate
wait_for_public
