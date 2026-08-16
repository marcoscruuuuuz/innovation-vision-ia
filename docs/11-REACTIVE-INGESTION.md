# P3 - Reactive ingestion

## Objective

Receive DVR/camera events without requiring continuous full-rate inference. The ingestion API authenticates each source, applies anti-replay/idempotency controls, stores optional snapshots, persists the event and publishes a compact task to Redis for downstream detection/rule processing.

## Endpoint

`POST /v1/events`

Required headers:

- `X-Vision-Source`: logical source key registered in `ingestion_sources.source_key`.
- `X-Vision-Timestamp`: Unix epoch seconds.
- `X-Vision-Nonce`: unique value for the signed request.
- `X-Vision-Signature`: lowercase hex HMAC-SHA256, optionally prefixed with `sha256=`.

Signature payload:

```text
<timestamp>\n<nonce>\n<raw-request-body>
```

The HMAC key is resolved from `ingestion_sources.hmac_secret_ref` through the runtime-only `INGESTION_HMAC_KEYS_JSON` map. Secrets are not stored in Git.

## JSON body

```json
{
  "external_event_id": "vendor-event-123",
  "event_name": "motion",
  "occurred_at": "2026-08-16T07:00:00-03:00",
  "channel": 4,
  "processing_mode": "SNAPSHOT",
  "payload": {"vendor": "intelbras"},
  "snapshot_base64": "...",
  "snapshot_content_type": "image/jpeg"
}
```

`processing_mode`:

- `SNAPSHOT`: enqueue a normal inference task using the supplied snapshot when present.
- `TEMPORAL_BURST`: downstream orchestration must acquire a short video burst before temporal evaluation. This phase only marks the requirement; it does not depend on Wine.
- `METADATA_ONLY`: event metadata is persisted/enqueued without image evidence.

## Safety and consistency

1. Source must be enabled and linked to an enabled DVR.
2. Timestamp must be within the source-specific clock-skew window.
3. HMAC is verified against the exact raw body.
4. Nonce is claimed once; replay returns HTTP 409.
5. `(ingestion_source_id, external_event_id)` is unique; duplicate delivery is idempotent.
6. Snapshot size is bounded before MinIO upload.
7. Snapshot SHA-256 is persisted.
8. Redis task contains identifiers and object key, not the snapshot bytes.
9. Any storage/queue failure marks the event as `FAILED` instead of reporting a false success.

## Redis stream

Default: `vision:ingestion:events`.

Fields include `event_id`, `condominium_id`, `dvr_id`, `camera_id`, `channel`, `event_name`, `processing_mode`, `snapshot_object_key` and `occurred_at`.

## Current boundary

This phase does not implement continuous decoding, detector execution, temporal burst acquisition or Rule Engine evaluation. Those are downstream phases. Wine/P2P vendor integration remains intentionally deferred until the final project phase.
