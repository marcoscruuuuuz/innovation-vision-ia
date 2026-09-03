#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail(){ echo "SMOKE FAIL: $*" >&2; exit 1; }
pass(){ echo "SMOKE PASS: $*"; }

[[ -f .env ]] || fail '.env ausente; copie .env.example e preencha.'
[[ -f config/gateways.yaml ]] || fail 'config/gateways.yaml ausente.'
[[ -s models/yolo11n.pt ]] || fail 'models/yolo11n.pt ausente.'
[[ -s models/yolo11n-pose.pt ]] || fail 'models/yolo11n-pose.pt ausente.'
[[ -f infra/cloudflare/config.yml ]] || echo 'WARN: Cloudflare config ainda não criado; o profile cloudflare não será validado.'

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

python3 - <<'PY'
from pathlib import Path
import ast, yaml
for path in Path('app').glob('*.py'):
    ast.parse(path.read_text(), filename=str(path))
yaml.safe_load(Path('config/events.yaml').read_text())
yaml.safe_load(Path('config/gateways.yaml').read_text())
print('syntax/yaml ok')
PY
pass 'Python syntax and YAML parse'

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
pass 'Admin and client portal HTTP'

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
  echo 'WARN: production writer is enabled. This is allowed only after canary rules are explicitly PRODUCTION.'
else
  pass 'writer remains fail-closed during initial smoke'
fi

cat <<'EOF'

SMOKE BASE CONCLUÍDO.
Próximos gates manuais/observáveis:
- provar snapshot real em 4 câmeras;
- provar dog/person na telemetria de detecção;
- executar Gold polygon/line/double-line;
- promover somente regras aprovadas para PRODUCTION;
- provar snapshot + clip 15 s + log;
- subir Cloudflare somente após auth/ACL local PASS.
EOF
