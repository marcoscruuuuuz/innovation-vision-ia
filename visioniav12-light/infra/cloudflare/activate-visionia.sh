#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
TOKEN_FILE="${ROOT_DIR}/secrets/cloudflared/visionia.token"
PUBLIC_HOSTNAME="${PUBLIC_HOSTNAME:-visioniav12.innovationrptelecom.com.br}"
TUNNEL_NAME="${TUNNEL_NAME:-visionia}"
TUNNEL_ID="${TUNNEL_ID:-b80c0e8d-4ad4-4693-90e1-76b1259d35f2}"

umask 077
mkdir -p "$(dirname "${TOKEN_FILE}")"

if [[ -n "${CLOUDFLARE_TUNNEL_TOKEN:-}" ]]; then
  TOKEN="${CLOUDFLARE_TUNNEL_TOKEN}"
else
  read -r -s -p "Paste the token for tunnel ${TUNNEL_NAME} (${TUNNEL_ID}): " TOKEN
  echo
fi

if [[ ${#TOKEN} -lt 80 ]]; then
  echo "The supplied tunnel token does not look valid." >&2
  exit 2
fi

printf '%s' "${TOKEN}" > "${TOKEN_FILE}"
chmod 600 "${TOKEN_FILE}"
unset TOKEN CLOUDFLARE_TUNNEL_TOKEN

cd "${ROOT_DIR}"
docker compose config --quiet
docker compose --profile cloudflare up -d cloudflared

for _ in $(seq 1 30); do
  state="$(docker inspect -f '{{.State.Status}}' visioniav12-light-cloudflared-1 2>/dev/null || true)"
  if [[ "${state}" == "running" ]]; then
    break
  fi
  sleep 2
done

state="$(docker inspect -f '{{.State.Status}}' visioniav12-light-cloudflared-1 2>/dev/null || true)"
if [[ "${state}" != "running" ]]; then
  docker compose --profile cloudflare logs --tail=80 cloudflared >&2 || true
  echo "cloudflared did not remain running." >&2
  exit 3
fi

if docker compose --profile cloudflare logs --since=5m cloudflared 2>&1 \
  | grep -Eqi 'registered tunnel connection|connection .* registered'; then
  connector_state="CONNECTED"
else
  connector_state="RUNNING_AWAITING_CONFIRMATION"
fi

local_health="FAIL"
if curl -fsS --max-time 5 http://127.0.0.1:8080/health >/dev/null; then
  local_health="PASS"
fi

public_health="PENDING_ROUTE_OR_ACCESS"
if curl -fsS --max-time 12 "https://${PUBLIC_HOSTNAME}/health" >/dev/null 2>&1; then
  public_health="PASS"
fi

cat <<EOF
CLOUDFLARED_SERVICE=ACTIVE
CLOUDFLARE_CONNECTOR=${connector_state}
TUNNEL_NAME=${TUNNEL_NAME}
TUNNEL_ID=${TUNNEL_ID}
LOCAL_HEALTH=${local_health}
PUBLIC_HOSTNAME=${PUBLIC_HOSTNAME}
PUBLIC_HEALTH=${public_health}
TOKEN_FILE=${TOKEN_FILE}
EOF

if [[ "${public_health}" != "PASS" ]]; then
  cat >&2 <<EOF
The connector is installed, but the public hostname is not confirmed yet.
In Cloudflare Zero Trust, configure tunnel ${TUNNEL_NAME} with public hostname:
  ${PUBLIC_HOSTNAME} -> http://api:8080
Then protect /admin* with Cloudflare Access.
EOF
fi
