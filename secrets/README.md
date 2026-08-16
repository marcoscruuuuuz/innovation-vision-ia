# Secrets

Não versionar credenciais, DLL/EXE proprietário, tokens ou chaves neste diretório.

No host instalado, usar subdiretórios protegidos, por exemplo:

- `/opt/vision/secrets/vendor/intelbras/`
- `/opt/vision/secrets/cameras/`
- `/opt/vision/secrets/evolution/`

Permissões recomendadas: owner `vision`, modo `0700` para diretórios e `0600` para arquivos sensíveis.
