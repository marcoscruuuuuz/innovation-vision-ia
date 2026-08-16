# Relatório de Validação da Fundação

Data: 2026-08-16

## Resultado

Nenhuma falha bloqueante foi encontrada nos testes executáveis no ambiente de construção.

## Testes executados

- `bash -n` em todos os scripts Shell: PASS.
- Parsing YAML de `configs/*.yaml`: PASS.
- Validação semântica do catálogo: 21 eventos, todos os presets referenciam eventos existentes: PASS.
- Validação estrutural local do `docker-compose.yml`: 7 serviços e chaves esperadas: PASS.
- `git diff --check`: PASS.
- scanner de extensões/arquivos sensíveis versionados: PASS.
- scanner heurístico de segredos: PASS.
- `git fsck --full` após limpeza: PASS.

## Correções realizadas durante a varredura

1. Corrigida uma falha de aspas no scanner de segredos do validador.
2. Reorganizada a árvore física para coincidir com os domínios documentados.
3. Criado `p2p_port_leases` para reserva transacional de portas antes da sessão P2P.
4. Criado histórico/atribuição de DVR por Wine e tabelas de modelos, feedback e notificações.
5. Corrigida a estratégia de `chown` do instalador para não alterar ownership dos volumes de banco em reexecuções.
6. Removidos `.gitkeep` de diretórios runtime do PostgreSQL/Redis/MinIO para evitar diretório de dados PostgreSQL não vazio no primeiro `initdb`.
7. Ajustada `veiculo_area_proibida` para a geometria de linha solicitada e adicionada inversão esquerda/direita na regra de porta.

## Verificações dependentes do host Ubuntu

O ambiente de construção atual não possui os binários `shellcheck` e Docker Compose instalados. Por isso, não foram executados aqui:

- `shellcheck scripts/*.sh` completo.
- `docker compose config --quiet` com o binário oficial.
- subida real dos containers.
- inicialização real do PostgreSQL com `architecture/schema.sql`.
- teste NVIDIA/DCGM.
- Wine/Intelbras P2P real.

Essas verificações estão incorporadas ao `scripts/install_ubuntu.sh`, ao `scripts/validate_repo.sh` e ao workflow `.github/workflows/validate.yml` para execução no Ubuntu/GitHub Actions.

## Limites

A validade desta fundação não certifica os microserviços ainda não implementados, os binários Intelbras, modelos de IA, túneis P2P reais nem regras com vídeo real. Esses itens dependem das fases de implementação e homologação descritas na documentação.
