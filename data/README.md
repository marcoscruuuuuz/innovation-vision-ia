# Runtime data

Os subdiretórios `postgres/`, `redis/` e `minio/` são criados no host pelo instalador/Docker e não devem conter arquivos versionados.

Nunca coloque `.gitkeep` em `data/postgres/`: o diretório de dados inicial do PostgreSQL deve estar vazio antes do primeiro `initdb`.
