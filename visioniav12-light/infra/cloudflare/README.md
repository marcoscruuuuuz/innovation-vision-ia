# Cloudflare — `visioniav12.innovationrptelecom.com.br`

## Tunnel oficial desta implantação

```text
Nome: visionia
ID: b80c0e8d-4ad4-4693-90e1-76b1259d35f2
Hostname: visioniav12.innovationrptelecom.com.br
Origin: http://api:8080
```

Os túneis `condoin` e `isabel-chat-solucao` não pertencem ao V12 Light e não devem ser alterados.

## Superfícies publicadas

O mesmo hostname publica:

- `/admin` — administração do VISION IA;
- `/portal` — portal do cliente;
- `/api` — API autenticada;
- `/health` — health básico do origin.

PostgreSQL, Redis, MinIO, métricas internas e os endpoints Wine/T2U não são publicados.

## Ativação do conector na VM 206

O token é obtido exclusivamente no painel Cloudflare Zero Trust para o tunnel `visionia`. Ele nunca deve ser enviado ao GitHub, escrito em relatório ou exibido em logs.

Execute na VM 206:

```bash
cd /opt/innovation-vision-light/visioniav12-light
bash infra/cloudflare/activate-visionia.sh
```

O script solicita o token de forma silenciosa, salva em:

```text
secrets/cloudflared/visionia.token
```

com permissão `0600`, inicia somente o serviço `cloudflared` e valida o origin local.

Também pode receber o segredo apenas no ambiente do processo:

```bash
CLOUDFLARE_TUNNEL_TOKEN='TOKEN_DO_TUNNEL_VISIONIA' \
  bash infra/cloudflare/activate-visionia.sh
```

Não salve esse comando no histórico do shell. O modo interativo é preferível.

## Public hostname no painel

No tunnel `visionia`, configure:

```text
Public hostname: visioniav12.innovationrptelecom.com.br
Service:         HTTP
URL:             api:8080
```

O `cloudflared` está na mesma rede Docker da API; por isso o origin é `api:8080`, não `127.0.0.1` dentro do container.

## Cloudflare Access

Crie uma aplicação Access para a administração:

```text
Nome: visionia-v12-admin
Hostname: visioniav12.innovationrptelecom.com.br
Path: /admin*
```

Opcionalmente inclua `/api/admin*` em uma segunda aplicação ou política equivalente.

A política deve permitir somente e-mails ou grupos administrativos da INNOVATION RP TELECOM.

O `/portal` continua protegido pela autenticação da própria aplicação e pela ACL por condomínio. Cloudflare Access adicional no portal é opcional.

## Verificação

Depois da ativação:

```bash
docker compose --profile cloudflare ps
docker compose --profile cloudflare logs --tail=100 cloudflared
curl -I https://visioniav12.innovationrptelecom.com.br/portal
curl -I https://visioniav12.innovationrptelecom.com.br/admin
```

Estados esperados:

```text
cloudflared = running
replica count >= 1
/portal = HTTP 200 ou fluxo de autenticação da aplicação
/admin = política Access antes da aplicação
```

## Alternativa local-managed

`config.yml.example` permanece apenas como referência para um tunnel local-managed. A implantação oficial usa o token do tunnel remoto `visionia` através do `--token-file` no Docker Compose.

## Segurança

- Nunca versionar token ou credencial JSON.
- Não abrir a porta 8080 no roteador.
- Não publicar MinIO, PostgreSQL, Redis ou gateways.
- Preservar o catch-all/negação no Cloudflare quando usar regras adicionais.
- Revogar e rotacionar o token se ele for exibido acidentalmente.
