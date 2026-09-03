#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail(){ echo "SMOKE FAIL: $*" >&2; exit 1; }
pass(){ echo "SMOKE PASS: $*"; }

[[ -f .env ]] || fail '.env ausente; execute scripts/bootstrap-production.sh.'
[[ -f config/gateways.yaml ]] || fail 'config/gateways.yaml ausente.'
[[ -s models/yolo11n.pt ]] || fail 'models/yolo11n.pt ausente.'
[[ -s models/yolo11n-pose.pt ]] || echo 'WARN: yolo11n-pose.pt ausente; regras de pose permanecem bloqueadas.'

if grep -R -n 'CHANGE_ME' .env config/gateways.yaml 2>/dev/null; then
  fail 'placeholders CHANGE_ME ainda existem.'
fi

set -a
. ./.env
set +a

[[ "${ALLOW_MANUAL_EVENT_INSERT:-false}" == "false" ]] || fail 'ALLOW_MANUAL_EVENT_INSERT precisa permanecer false.'
[[ "${GLOBAL_WINE_T2U_RESTART_ALLOWED:-false}" == "false" ]] || fail 'GLOBAL_WINE_T2U_RESTART_ALLOWED precisa permanecer false.'

docker compose config --quiet
pass 'docker compose config'

if [[ -s secrets/cloudflared/visionia.token ]]; then
  docker compose --profile cloudflare config --quiet
  pass 'Cloudflare Compose profile'
else
  echo 'WARN: tunnel token ainda não instalado; execução local continuará disponível.'
fi

python3 - <<'PY'
from pathlib import Path
import ast, yaml
for path in Path('app').glob('*.py'):
    ast.parse(path.read_text(), filename=str(path))
for path in [
    Path('config/events.yaml'),
    Path('config/gateways.yaml'),
    Path('infra/cloudflare/config.yml.example'),
]:
    yaml.safe_load(path.read_text())
for path in [
    Path('app/static/admin.html'),
    Path('app/static/admin.js'),
    Path('app/static/portal.html'),
    Path('app/static/portal.js'),
    Path('app/static/app.css'),
]:
    assert path.exists() and path.stat().st_size > 100, path
print('syntax/yaml/static assets ok')
PY
pass 'Python syntax, YAML and static assets'

docker compose up -d postgres redis minio api

for attempt in $(seq 1 90); do
  if curl -fsS http://127.0.0.1:8080/health >/tmp/vision-light-health.json; then
    break
  fi
  sleep 2
  [[ "$attempt" != 90 ]] || { docker compose ps; docker compose logs --no-color --tail=200 api postgres redis minio; fail 'API health timeout'; }
done
pass "API health $(cat /tmp/vision-light-health.json)"

curl -fsS -o /dev/null http://127.0.0.1:8080/admin
curl -fsS -o /dev/null http://127.0.0.1:8080/portal
curl -fsS -o /dev/null http://127.0.0.1:8080/assets/app.css
curl -fsS -o /dev/null http://127.0.0.1:8080/assets/admin.js
curl -fsS -o /dev/null http://127.0.0.1:8080/assets/portal.js
pass 'Admin, portal and assets HTTP'

admin_token="$(curl -fsS http://127.0.0.1:8080/api/auth/token \
  -H 'content-type: application/json' \
  --data "$(python3 - <<PY
import json
print(json.dumps({'email': '${INITIAL_ADMIN_EMAIL}', 'password': '${INITIAL_ADMIN_PASSWORD}'}))
PY
)" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')"

[[ -n "$admin_token" ]] || fail 'admin authentication returned no token'
curl -fsS -o /tmp/vision-light-overview.json http://127.0.0.1:8080/api/admin/overview -H "Authorization: Bearer ${admin_token}"
curl -fsS -o /tmp/vision-light-cameras.json http://127.0.0.1:8080/api/admin/cameras/status -H "Authorization: Bearer ${admin_token}"
curl -fsS -o /tmp/vision-light-users.json http://127.0.0.1:8080/api/admin/users -H "Authorization: Bearer ${admin_token}"
pass 'Administrative authentication and APIs'

if [[ -n "${INITIAL_CLIENT_EMAIL:-}" && -n "${INITIAL_CLIENT_PASSWORD:-}" && -n "${INITIAL_CLIENT_CONDO_SCOPE:-}" ]]; then
  client_token="$(curl -fsS http://127.0.0.1:8080/api/auth/token \
    -H 'content-type: application/json' \
    --data "$(python3 - <<PY
import json
print(json.dumps({'email': '${INITIAL_CLIENT_EMAIL}', 'password': '${INITIAL_CLIENT_PASSWORD}'}))
PY
)" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')"
  [[ -n "$client_token" ]] || fail 'client authentication returned no token'
  curl -fsS -o /tmp/vision-light-client-summary.json http://127.0.0.1:8080/api/client/summary -H "Authorization: Bearer ${client_token}"
  curl -fsS -o /tmp/vision-light-client-cameras.json http://127.0.0.1:8080/api/client/cameras -H "Authorization: Bearer ${client_token}"
  curl -fsS -o /tmp/vision-light-client-logs.json http://127.0.0.1:8080/api/client/logs -H "Authorization: Bearer ${client_token}"
  pass 'Client authentication, camera status and log APIs'
fi

docker compose run --rm api python seed.py
pass 'idempotent camera/rule seed'

if ! nvidia-smi >/tmp/vision-light-nvidia-smi.txt; then
  fail 'nvidia-smi failed in guest'
fi
pass 'guest NVIDIA driver'

docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi >/tmp/vision-light-docker-gpu.txt
pass 'Docker GPU runtime'

docker compose up -d ingest detector rules media retention
sleep 10

docker compose ps
for service in ingest detector rules media retention; do
  id="$(docker compose ps -q "$service")"
  [[ -n "$id" ]] || fail "container $service not created"
  state="$(docker inspect -f '{{.State.Status}}' "$id")"
  [[ "$state" == "running" ]] || { docker compose logs --no-color --tail=200 "$service"; fail "$service state=$state"; }
done
pass 'application workers running'

redis_id="$(docker compose ps -q redis)"
docker exec "$redis_id" redis-cli XLEN "${REDIS_PREFIX:-vl:}frames" || true
docker exec "$redis_id" redis-cli XLEN "${REDIS_PREFIX:-vl:}detections" || true
docker exec "$redis_id" redis-cli XLEN "${REDIS_PREFIX:-vl:}candidates" || true

if [[ "${PRODUCTION_WRITER_ENABLED:-false}" == "true" ]]; then
  echo 'WARN: production writer is enabled. This is valid only after real rule/media approval.'
else
  pass 'writer remains fail-closed until production promotion'
fi

cat <<EOF

SMOKE DE IMPLANTAÇÃO CONCLUÍDO.
Admin:  http://127.0.0.1:8080/admin
Portal: http://127.0.0.1:8080/portal
Credenciais locais: ${ROOT}/secrets/initial-access-credentials.txt

Os testes acima validam implantação, autenticação e contratos sem fabricar eventos.
Os eventos somente são promovidos após frame real, regra, snapshot e clipe válido.
EOF
