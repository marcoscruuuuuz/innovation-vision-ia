#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

failures=0
ok() { printf '[OK] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; failures=$((failures + 1)); }

required_files=(README.md docker-compose.yml .env.example architecture/schema.sql configs/event_catalog.yaml configs/p2p.example.yaml scripts/install_ubuntu.sh docs/01-ARCHITECTURE.md docs/02-DASHBOARD.md docs/03-EVENTS.md docs/04-P2P-WINE.md docs/05-CERTIFICATION.md docs/06-AI-IDE.md docs/07-DATA-SECURITY.md docs/08-INSTALLATION.md docs/09-IMPLEMENTATION-PHASES.md docs/10-ACCEPTANCE.md)
for path in "${required_files[@]}"; do
  if [[ -f "$path" ]]; then ok "arquivo presente: $path"; else fail "arquivo ausente: $path"; fi
done

required_dirs=(core devices p2p scheduler ingestion detection rules certification evidence ai notifications observability portal-admin portal-client)
for path in "${required_dirs[@]}"; do
  if [[ -d "$path" ]]; then ok "domínio presente: $path"; else fail "domínio ausente: $path"; fi
done

while IFS= read -r -d '' script; do
  if bash -n "$script"; then ok "bash -n: ${script#./}"; else fail "erro de sintaxe shell: ${script#./}"; fi
done < <(find scripts -type f -name '*.sh' -print0)

if command -v shellcheck >/dev/null 2>&1; then
  if shellcheck --severity=warning scripts/*.sh; then ok 'shellcheck'; else fail 'shellcheck encontrou problemas'; fi
else
  warn 'shellcheck não instalado; validação estática avançada ignorada'
fi

if command -v python3 >/dev/null 2>&1; then
  if python3 - <<'PY'
from pathlib import Path
import sys
try:
    import yaml
except Exception as exc:
    print(f'PyYAML indisponível: {exc}', file=sys.stderr)
    raise SystemExit(2)
for path in sorted(Path('configs').glob('*.yaml')):
    with path.open('r', encoding='utf-8') as fh:
        yaml.safe_load(fh)
    print(f'YAML OK: {path}')
catalog = yaml.safe_load(Path('configs/event_catalog.yaml').read_text(encoding='utf-8'))
events = catalog.get('events', {})
if not events:
    raise SystemExit('event_catalog.yaml não contém eventos')
for sector, names in catalog.get('sectors', {}).items():
    missing = [name for name in names if name not in events]
    if missing:
        raise SystemExit(f'setor {sector} referencia eventos inexistentes: {missing}')
print(f'Catálogo OK: {len(events)} eventos')
PY
  then ok 'YAML e catálogo de eventos'; else rc=$?; if [[ $rc -eq 2 ]]; then warn 'python3-yaml não instalado; YAML não validado'; else fail 'YAML/catálogo inválido'; fi; fi
fi

if command -v python3 >/dev/null 2>&1; then
  if python3 - <<'PY'
from pathlib import Path
import sys
try:
    import yaml
except Exception as exc:
    print(f'PyYAML indisponível: {exc}', file=sys.stderr)
    raise SystemExit(2)
compose = yaml.safe_load(Path('docker-compose.yml').read_text(encoding='utf-8'))
if not isinstance(compose, dict) or not isinstance(compose.get('services'), dict):
    raise SystemExit('docker-compose.yml deve conter services como objeto')
allowed_top = {'name','services','networks','volumes','secrets','configs'}
unknown_top = set(compose) - allowed_top
if unknown_top:
    raise SystemExit(f'chaves top-level Compose não reconhecidas: {sorted(unknown_top)}')
allowed_service = {'image','build','restart','environment','volumes','healthcheck','networks','command','ports','profiles','deploy','pid','cap_add','depends_on','user','entrypoint','working_dir','read_only','tmpfs','security_opt','devices','runtime','ipc','shm_size','ulimits'}
for name, svc in compose['services'].items():
    if not isinstance(svc, dict):
        raise SystemExit(f'serviço {name} não é objeto')
    unknown = set(svc) - allowed_service
    if unknown:
        raise SystemExit(f'serviço {name} contém chaves inesperadas: {sorted(unknown)}')
print(f'Compose estrutural OK: {len(compose["services"])} serviços')
PY
  then ok 'estrutura local do Docker Compose'; else rc=$?; if [[ $rc -eq 2 ]]; then warn 'python3-yaml não instalado; estrutura Compose não validada'; else fail 'estrutura local do Docker Compose inválida'; fi; fi
fi

if [[ ! -f .env ]]; then cp .env.example .env.validation; ENV_FILE=.env.validation; else ENV_FILE=.env; fi
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  if docker compose --env-file "$ENV_FILE" config --quiet; then ok 'docker compose config'; else fail 'docker compose config inválido'; fi
else
  warn 'Docker Compose indisponível; validação Compose ignorada'
fi
rm -f .env.validation

if git ls-files | grep -E '(^|/)(\.env|.*\.(key|pem|p12|pfx|dll|exe|onnx|engine|pt|pth))$' >/dev/null 2>&1; then fail 'arquivo potencialmente sensível/binário proibido está versionado'; else ok 'nenhum segredo/binário proibido versionado'; fi

if command -v python3 >/dev/null 2>&1; then
  if python3 - <<'PY'
from pathlib import Path
import re, subprocess
tracked = subprocess.check_output(['git','ls-files'], text=True).splitlines()
pattern = re.compile(r'(?i)^\s*[A-Za-z0-9_.-]*(password|token|secret)\s*[:=]\s*[\"\']?([^\s\"\']+)')
hits=[]
for name in tracked:
    if name in {'.env.example','scripts/validate_repo.sh'} or name.endswith('.md'):
        continue
    path=Path(name)
    if not path.is_file():
        continue
    try: text=path.read_text(encoding='utf-8')
    except UnicodeDecodeError: continue
    for lineno,line in enumerate(text.splitlines(),1):
        m=pattern.search(line)
        if not m: continue
        value=m.group(2)
        if 'CHANGE_ME' in value or value.startswith('${') or value.startswith('$(') or value.startswith('secret://'): continue
        hits.append(f'{name}:{lineno}')
Path('/tmp/vision-secret-scan.txt').write_text('\n'.join(hits), encoding='utf-8')
raise SystemExit(1 if hits else 0)
PY
  then ok 'scanner heurístico de segredos'; else warn 'scanner heurístico encontrou possíveis segredos; revisar /tmp/vision-secret-scan.txt'; fi
fi

if (( failures > 0 )); then printf '\nValidação concluída com %d falha(s).\n' "$failures" >&2; exit 1; fi
printf '\nValidação concluída sem falhas bloqueantes.\n'
