import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

import boto3
import psycopg
import redis
from botocore.client import Config
from fastapi import FastAPI, HTTPException, Request
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://vision:vision@postgres:5432/vision")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
REDIS_STREAM = os.getenv("INGESTION_REDIS_STREAM", "vision:ingestion:events")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "visionminio")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
MINIO_BUCKET = os.getenv("MINIO_INGESTION_BUCKET", "vision-ingestion")
MAX_BODY_BYTES = int(os.getenv("INGESTION_MAX_BODY_BYTES", str(12 * 1024 * 1024)))
MAX_SNAPSHOT_BYTES = int(os.getenv("INGESTION_MAX_SNAPSHOT_BYTES", str(10 * 1024 * 1024)))
HMAC_KEYS = json.loads(os.getenv("INGESTION_HMAC_KEYS_JSON", "{}"))

app = FastAPI(title="INNOVATION VISION Reactive Ingestion", version="0.1.0")


def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def redis_client():
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)


def minio_client():
    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def ensure_bucket(client) -> None:
    try:
        client.head_bucket(Bucket=MINIO_BUCKET)
    except Exception:
        try:
            client.create_bucket(Bucket=MINIO_BUCKET)
        except Exception as exc:
            raise RuntimeError(f"MinIO bucket unavailable: {exc}") from exc


class IngestionPayload(BaseModel):
    external_event_id: str = Field(min_length=1, max_length=255)
    event_name: str = Field(min_length=1, max_length=160)
    occurred_at: datetime
    channel: int | None = Field(default=None, ge=1)
    processing_mode: Literal["SNAPSHOT", "TEMPORAL_BURST", "METADATA_ONLY"] = "SNAPSHOT"
    payload: dict[str, Any] = Field(default_factory=dict)
    snapshot_base64: str | None = None
    snapshot_content_type: str | None = Field(default="image/jpeg", max_length=120)


def verify_request(source: dict[str, Any], timestamp: str, nonce: str, signature: str, body: bytes) -> None:
    secret_ref = source["hmac_secret_ref"]
    secret = HMAC_KEYS.get(secret_ref)
    if not secret:
        raise HTTPException(status_code=503, detail="ingestion HMAC secret is not configured")
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid timestamp") from exc
    now = int(datetime.now(timezone.utc).timestamp())
    if abs(now - ts) > int(source["allowed_clock_skew_seconds"]):
        raise HTTPException(status_code=401, detail="request timestamp outside allowed clock skew")
    signed = timestamp.encode() + b"\n" + nonce.encode() + b"\n" + body
    expected = hmac.new(str(secret).encode(), signed, hashlib.sha256).hexdigest()
    supplied = signature.removeprefix("sha256=").lower()
    if not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=401, detail="invalid signature")


def resolve_source(source_key: str):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.*, d.condominium_id
              FROM ingestion_sources s
              JOIN dvrs d ON d.id=s.dvr_id
             WHERE s.source_key=%s AND s.enabled=true AND d.enabled=true
            """,
            (source_key,),
        )
        source = cur.fetchone()
    if not source:
        raise HTTPException(status_code=401, detail="unknown or disabled ingestion source")
    return source


def claim_nonce(source_id: UUID, nonce: str, ttl_seconds: int) -> None:
    if not nonce or len(nonce) > 200:
        raise HTTPException(status_code=401, detail="invalid nonce")
    with db() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM ingestion_nonces WHERE expires_at < now()")
        cur.execute(
            """
            INSERT INTO ingestion_nonces(ingestion_source_id,nonce,expires_at)
            VALUES (%s,%s,now() + (%s || ' seconds')::interval)
            ON CONFLICT DO NOTHING
            RETURNING nonce
            """,
            (source_id, nonce, ttl_seconds),
        )
        claimed = cur.fetchone()
        conn.commit()
    if not claimed:
        raise HTTPException(status_code=409, detail="replayed nonce")


@app.get("/health")
def health():
    try:
        with db() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        redis_client().ping()
        return {"status": "ok", "queue": REDIS_STREAM, "bucket": MINIO_BUCKET}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"dependency unavailable: {exc}") from exc


@app.post("/v1/events")
async def ingest_event(request: Request):
    source_key = request.headers.get("x-vision-source", "")
    timestamp = request.headers.get("x-vision-timestamp", "")
    nonce = request.headers.get("x-vision-nonce", "")
    signature = request.headers.get("x-vision-signature", "")
    if not all((source_key, timestamp, nonce, signature)):
        raise HTTPException(status_code=401, detail="missing ingestion authentication headers")

    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="request body too large")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="request body too large")

    source = resolve_source(source_key)
    verify_request(source, timestamp, nonce, signature, body)
    claim_nonce(source["id"], nonce, int(source["allowed_clock_skew_seconds"]) * 2)

    try:
        payload = IngestionPayload.model_validate_json(body)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid event payload: {exc}") from exc

    camera_id = None
    if payload.channel is not None:
        with db() as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM cameras WHERE dvr_id=%s AND channel=%s AND enabled=true", (source["dvr_id"], payload.channel))
            camera = cur.fetchone()
            camera_id = camera["id"] if camera else None

    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingestion_events(
                ingestion_source_id,condominium_id,dvr_id,camera_id,channel,external_event_id,event_name,
                occurred_at,payload,processing_mode,queue_status
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,'RECEIVED')
            ON CONFLICT(ingestion_source_id,external_event_id) DO NOTHING
            RETURNING *
            """,
            (
                source["id"], source["condominium_id"], source["dvr_id"], camera_id, payload.channel,
                payload.external_event_id, payload.event_name, payload.occurred_at,
                json.dumps(payload.payload), payload.processing_mode,
            ),
        )
        event = cur.fetchone()
        if not event:
            cur.execute(
                "SELECT * FROM ingestion_events WHERE ingestion_source_id=%s AND external_event_id=%s",
                (source["id"], payload.external_event_id),
            )
            existing = cur.fetchone()
            conn.commit()
            return {"status": "DUPLICATE", "event_id": str(existing["id"]), "queue_status": existing["queue_status"]}
        conn.commit()

    snapshot_object_key = None
    snapshot_sha256 = None
    try:
        if payload.snapshot_base64:
            try:
                snapshot = base64.b64decode(payload.snapshot_base64, validate=True)
            except Exception as exc:
                raise RuntimeError("snapshot_base64 is not valid base64") from exc
            if len(snapshot) > MAX_SNAPSHOT_BYTES:
                raise RuntimeError("snapshot exceeds configured size limit")
            snapshot_sha256 = hashlib.sha256(snapshot).hexdigest()
            occurred = payload.occurred_at.astimezone(timezone.utc)
            snapshot_object_key = f"{source['condominium_id']}/{source['dvr_id']}/{occurred:%Y/%m/%d}/{event['id']}.jpg"
            s3 = minio_client()
            ensure_bucket(s3)
            s3.put_object(
                Bucket=MINIO_BUCKET,
                Key=snapshot_object_key,
                Body=snapshot,
                ContentType=payload.snapshot_content_type or "image/jpeg",
                Metadata={"sha256": snapshot_sha256, "event-id": str(event["id"])},
            )

        task = {
            "event_id": str(event["id"]),
            "condominium_id": str(source["condominium_id"]),
            "dvr_id": str(source["dvr_id"]),
            "camera_id": str(camera_id) if camera_id else "",
            "channel": str(payload.channel or ""),
            "event_name": payload.event_name,
            "processing_mode": payload.processing_mode,
            "snapshot_object_key": snapshot_object_key or "",
            "occurred_at": payload.occurred_at.isoformat(),
        }
        queue_id = redis_client().xadd(REDIS_STREAM, task, maxlen=100000, approximate=True)

        with db() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ingestion_events
                   SET snapshot_object_key=%s,snapshot_sha256=%s,queue_status='QUEUED'
                 WHERE id=%s
                 RETURNING *
                """,
                (snapshot_object_key, snapshot_sha256, event["id"]),
            )
            updated = cur.fetchone()
            cur.execute(
                "INSERT INTO audit_logs(actor_type,action,object_type,object_id,metadata) VALUES ('SYSTEM','ingestion.event.queued','ingestion_event',%s,%s::jsonb)",
                (str(event["id"]), json.dumps({"queue_id": queue_id, "source_key": source_key, "processing_mode": payload.processing_mode})),
            )
            conn.commit()
        return {"status": "QUEUED", "event": updated, "queue_id": queue_id}
    except Exception as exc:
        with db() as conn, conn.cursor() as cur:
            cur.execute("UPDATE ingestion_events SET queue_status='FAILED',error=%s WHERE id=%s", (str(exc)[:2000], event["id"]))
            conn.commit()
        raise HTTPException(status_code=503, detail=f"event accepted but enqueue/storage failed: {exc}") from exc


@app.get("/v1/events/{event_id}")
def get_event(event_id: UUID):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM ingestion_events WHERE id=%s", (event_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="event not found")
    return row
