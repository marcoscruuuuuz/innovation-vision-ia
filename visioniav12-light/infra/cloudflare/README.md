# Cloudflare — visioniav12.innovationrptelecom.com.br

## Publicação

O mesmo hostname publica três superfícies, roteadas pela aplicação:

- `/admin` — administração;
- `/portal` — cliente;
- `/api` — API autenticada.

O tunnel aponta o hostname inteiro para `http://api:8080`. A aplicação aplica autenticação e autorização por rota.

## Passos

```bash
cloudflared tunnel login
cloudflared tunnel create visioniav12
cloudflared tunnel route dns visioniav12 visioniav12.innovationrptelecom.com.br
```

Copie `config.yml.example` para `config.yml`, informe o UUID e coloque o JSON de credencial em `secrets/cloudflared/` apenas na VM.

Valide antes de iniciar:

```bash
cloudflared tunnel ingress validate
cloudflared tunnel ingress rule https://visioniav12.innovationrptelecom.com.br/admin
cloudflared tunnel info visioniav12
```

Suba o profile:

```bash
docker compose --profile cloudflare up -d cloudflared
```

## Cloudflare Access

Criar aplicações Access separadas, mesmo usando o mesmo hostname:

1. `visioniav12-admin`: caminho `/admin*` e, se desejado, `/api/admin*`; permitir somente e-mails/grupos administrativos da INNOVATION RP TELECOM.
2. `visioniav12-client`: caminho `/portal*`; o login interno da aplicação continua obrigatório.

Não publicar MinIO, PostgreSQL, Redis, métricas internas ou endpoints Wine/T2U pela Internet.

## Segurança

- O JSON do tunnel e tokens nunca entram no Git.
- Cookies da aplicação devem ser `Secure`, `HttpOnly` e `SameSite=Lax/Strict` conforme a rota.
- O último ingress deve ser `http_status:404`.
- O tunnel é outbound-only; não abrir a porta 8080 no roteador.
