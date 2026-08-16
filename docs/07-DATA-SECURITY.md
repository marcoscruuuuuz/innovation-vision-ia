# 07 - Dados, Segurança e Auditoria

## Segredos

Nunca versionar:

- senhas de DVR.
- tokens P2P.
- chaves HMAC.
- JWT secrets.
- credenciais MinIO/PostgreSQL.
- tokens Evolution API.
- chaves Tailscale.

O diretório `secrets/` no Git contém somente instruções e `.gitkeep`.

## Isolamento

O portal do cliente é separado do administrativo. Toda consulta de cliente passa por `tenant_id/condominium_id` autorizado e é read-only para logs.

## Evidências

Metadados no PostgreSQL; binários em MinIO. Toda evidência deve registrar:

- hash.
- evento.
- câmera.
- horário.
- regra/version.
- modelo/version.
- status de revisão.

## Auditoria

Ações críticas registram:

- ator.
- origem.
- timestamp.
- intenção.
- parâmetros sanitizados.
- resultado.
- objeto afetado.

## Rede

PostgreSQL, Redis e MinIO não devem ser publicados diretamente na LAN/WAN. Acesso externo ocorre por API/proxy/Tailscale conforme política do ambiente.

## Backups

Backups devem incluir:

- schema/dump PostgreSQL.
- MinIO/evidências conforme política de retenção.
- configurações e versões de regras.
- inventário de modelos/checksums.
- configuração P2P sem segredos em claro.
