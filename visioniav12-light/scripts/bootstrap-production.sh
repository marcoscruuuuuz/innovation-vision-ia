#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env}"
EXAMPLE_FILE="${ROOT_DIR}/.env.example"
REGISTRY_FILE="${ROOT_DIR}/config/gateways.yaml"
CREDENTIAL_FILE="${ROOT_DIR}/secrets/initial-access-credentials.txt"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@innovationrptelecom.com.br}"
CLIENT_EMAIL="${CLIENT_EMAIL:-cliente@innovationrptelecom.com.br}"
CLIENT_SCOPE="${CLIENT_SCOPE:-}"
ROTATE_EXISTING="${ROTATE_EXISTING:-no}"

umask 077
mkdir -p "${ROOT_DIR}/secrets/gateway" "${ROOT_DIR}/secrets/cloudflared" "${ROOT_DIR}/models" "${ROOT_DIR}/config"

if [[ ! -f "${EXAMPLE_FILE}" ]]; then
  echo "Missing ${EXAMPLE_FILE}" >&2
  exit 1
fi

if [[ -z "${CLIENT_SCOPE}" && -f "${REGISTRY_FILE}" ]]; then
  CLIENT_SCOPE="$(awk '
    /^[[:space:]]+condo:[[:space:]]*/ {
      value=$0
      sub(/^[[:space:]]+condo:[[:space:]]*/, "", value)
      gsub(/^['\"]|['\"]$/, "", value)
      if (length(value) > 0) { print value; exit }
    }
  ' "${REGISTRY_FILE}")"
fi

if [[ -z "${CLIENT_SCOPE}" ]]; then
  echo "CLIENT_SCOPE is required, or config/gateways.yaml must contain at least one condo entry." >&2
  exit 2
fi

if [[ -f "${ENV_FILE}" && "${ROTATE_EXISTING,,}" != "yes" ]]; then
  echo "Existing ${ENV_FILE} preserved. Set ROTATE_EXISTING=yes only for an intentional credential rotation." >&2
  exit 3
fi

APP_SECRET_KEY="$(openssl rand -hex 32)"
DB_PASSWORD="$(openssl rand -hex 24)"
MINIO_PASSWORD="$(openssl rand -hex 24)"
ADMIN_PASSWORD="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(24))
PY
)"
CLIENT_PASSWORD="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(24))
PY
)"

cp "${EXAMPLE_FILE}" "${ENV_FILE}"

python3 - "${ENV_FILE}" \
  "${APP_SECRET_KEY}" \
  "${DB_PASSWORD}" \
  "${MINIO_PASSWORD}" \
  "${ADMIN_EMAIL}" \
  "${ADMIN_PASSWORD}" \
  "${CLIENT_EMAIL}" \
  "${CLIENT_PASSWORD}" \
  "${CLIENT_SCOPE}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
app_secret, db_password, minio_password = sys.argv[2:5]
admin_email, admin_password = sys.argv[5:7]
client_email, client_password, client_scope = sys.argv[7:10]

values = {
    "APP_SECRET_KEY": app_secret,
    "INITIAL_ADMIN_EMAIL": admin_email,
    "INITIAL_ADMIN_PASSWORD": admin_password,
    "INITIAL_CLIENT_EMAIL": client_email,
    "INITIAL_CLIENT_PASSWORD": client_password,
    "INITIAL_CLIENT_CONDO_SCOPE": client_scope,
    "POSTGRES_PASSWORD": db_password,
    "DATABASE_URL": f"postgresql+psycopg://vision_light:{db_password}@postgres:5432/vision_light",
    "MINIO_ROOT_PASSWORD": minio_password,
    "CLOUDFLARE_TUNNEL_NAME": "visionia",
    "CLOUDFLARE_TUNNEL_UUID": "b80c0e8d-4ad4-4693-90e1-76b1259d35f2",
    "PUBLIC_BASE_URL": "https://visioniav12.innovationrptelecom.com.br",
    "PRODUCTION_WRITER_ENABLED": "false",
    "ALLOW_MANUAL_EVENT_INSERT": "false",
    "GLOBAL_WINE_T2U_RESTART_ALLOWED": "false",
}

lines = path.read_text(encoding="utf-8").splitlines()
output = []
seen = set()
for line in lines:
    if not line or line.lstrip().startswith("#") or "=" not in line:
        output.append(line)
        continue
    key = line.split("=", 1)[0]
    if key in values:
        value = str(values[key]).replace("\\", "\\\\").replace('"', '\\"')
        output.append(f'{key}="{value}"')
        seen.add(key)
    else:
        output.append(line)
for key, raw in values.items():
    if key not in seen:
        value = str(raw).replace("\\", "\\\\").replace('"', '\\"')
        output.append(f'{key}="{value}"')
path.write_text("\n".join(output) + "\n", encoding="utf-8")
PY

chmod 600 "${ENV_FILE}"

cat > "${CREDENTIAL_FILE}" <<EOF
INNOVATION VISION IA V12 LIGHT
Generated: $(date --iso-8601=seconds)

ADMIN
URL: https://visioniav12.innovationrptelecom.com.br/admin
Email: ${ADMIN_EMAIL}
Password: ${ADMIN_PASSWORD}

CLIENT CANARY
URL: https://visioniav12.innovationrptelecom.com.br/portal
Email: ${CLIENT_EMAIL}
Password: ${CLIENT_PASSWORD}
Condominium scope: ${CLIENT_SCOPE}

SECURITY
- Move these values to the corporate password vault.
- Delete this plaintext file after vault confirmation.
- Never commit .env or this file to Git.
EOF
chmod 600 "${CREDENTIAL_FILE}"

cat <<EOF
Production secrets created locally.
Environment: ${ENV_FILE}
Initial credentials: ${CREDENTIAL_FILE}
Client scope: ${CLIENT_SCOPE}
Production writer remains disabled until a real media/rule canary passes.
EOF
