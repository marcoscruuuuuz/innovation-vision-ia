# Core Platform — P4 a P8

## Fluxo padrão

```text
DVR/event source
  -> ingestion-api
  -> Redis vision:ingestion:events
  -> detection-worker
  -> Redis vision:detection:results
  -> rule-worker
  -> Redis vision:rule:candidates
  -> certification-worker
  -> event_logs/event_evidence
  -> Redis vision:notifications
  -> notification-worker
  -> portal/admin/client
```

Wine/P2P não participa deste fluxo padrão e permanece no profile `p2p`.

## Detector

O backend real é externo ao worker e usa o contrato `vision.detector.v1`.

Entrada relevante:

```json
{
  "contract": "vision.detector.v1",
  "bbox_space": "normalized_xyxy_0_1",
  "event_id": "uuid",
  "camera_id": "uuid",
  "event_name": "source-event",
  "processing_mode": "SNAPSHOT",
  "image_base64": "...",
  "model_key": "optional",
  "model_version": "optional"
}
```

Resposta:

```json
{
  "contract": "vision.detector.v1",
  "ok": true,
  "bbox_space": "normalized_xyxy_0_1",
  "detections": [
    {"class": "person", "confidence": 0.97, "bbox": [0.1, 0.2, 0.4, 0.9], "track_id": null}
  ]
}
```

`bbox` deve estar em `[0,1]` e obedecer `x1 < x2`, `y1 < y2`. Resultado fora do contrato não é promovido.

## Regras

Cada câmera possui `event_rules` e versões imutáveis em `event_rule_versions`. Alterar uma regra cria nova versão e atualiza `active_version`.

O editor visual usa o snapshot mais recente e persiste geometrias normalizadas. Eventos com engine temporal/especializado não são decididos por snapshot único; o Rule Engine retorna `NEEDS_TEMPORAL`/`MODEL_REQUIRED` conforme a dependência.

## Confiança e certificação

Os limiares ficam em `event_confidence_policies`, por `event_type`:

- `min_log_confidence`;
- `review_from_confidence`;
- `evidence_from_confidence`.

O sistema não depende de valores hard-coded para operação futura. O default inicial é conservador e pode ser alterado pela API administrativa.

Uma regra só pode gerar log automático visível ao cliente quando sua versão ativa está em `PRODUCTION`. `HOMOLOGATION`, `SHADOW`, `CERTIFIED` e demais estados não são promovidos automaticamente para o cliente.

## Portal e tenant

O portal usa token Bearer armazenado apenas no navegador do operador/cliente. Tokens persistidos no banco são guardados como SHA-256; o valor puro só é retornado no momento da emissão.

A leitura do cliente aplica duas camadas:

1. tenant guard da API via `user_condominiums`;
2. `SET LOCAL ROLE vision_portal` + RLS PostgreSQL em logs, evidências e câmeras.

## ISABEL

`POST /api/v1/isabel/query` é read-only e trabalha sobre o mesmo escopo de tenant do portal. Intenções atuais:

- `count_logs`;
- `search_logs`.

A camada LLM pode traduzir linguagem natural para essas ferramentas, mas não recebe permissão de alterar regras, dispositivos ou eventos do cliente.

## Retenção

`retention-worker` executa o ciclo configurado por `RETENTION_INTERVAL_SECONDS`. O padrão é sete dias.

1. encontra evidências de logs vencidos;
2. coloca os object keys em `evidence_deletion_queue`;
3. remove do MinIO com retry/auditoria;
4. só então remove o log correspondente;
5. registra `retention_job_runs` e `audit_logs`.

## Alertas

O Certification Worker publica apenas logs já aprovados em `vision:notifications`. O Notification Worker consulta `alert_policies` e cria `notification_deliveries`.

Evolution fica desligado por padrão. O adapter exige URL, API key e instância/provider configurados. Sem resposta HTTP real, a entrega nunca é marcada como `SENT`.

## Portas locais

- API: 8080
- Admin: 8083
- Cliente: 8084
- Ingestion API: 8100
- MinIO: 9000 / 9001
- Prometheus: 9090
- Grafana: 3000

Por padrão, os binds publicados são `127.0.0.1`.
