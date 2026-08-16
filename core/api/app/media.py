from __future__ import annotations

import os
from io import BytesIO
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from minio import Minio

from .platform import pool, require_admin, require_client_read, tenant_ids

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "visionminio")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "vision-evidence")

minio = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY, secret_key=MINIO_SECRET_KEY, secure=False)
router = APIRouter()


def _stream_object(object_key: str, disposition: str):
    try:
        obj = minio.get_object(MINIO_BUCKET, object_key)
        data = obj.read()
        obj.close()
        obj.release_conn()
    except Exception as exc:
        raise HTTPException(status_code=404, detail="evidence object not found") from exc
    media_type = "image/jpeg" if object_key.lower().endswith((".jpg", ".jpeg")) else ("image/png" if object_key.lower().endswith(".png") else "application/octet-stream")
    filename = object_key.rsplit("/", 1)[-1]
    return StreamingResponse(BytesIO(data), media_type=media_type, headers={"Content-Disposition": f'{disposition}; filename="{filename}"'})


@router.get("/api/v1/client/evidence/{evidence_id}/view")
def client_view(evidence_id: UUID, authorization: str | None = Header(default=None)):
    p = require_client_read(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        allowed = tenant_ids(cur, p)
        cur.execute(
            """
            SELECT e.object_key FROM event_evidence e
            JOIN event_logs l ON l.id=e.event_log_id
            WHERE e.id=%s AND l.client_visible=true AND l.condominium_id=ANY(%s)
            """, (evidence_id, allowed)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="evidence not found")
    return _stream_object(row["object_key"], "inline")


@router.get("/api/v1/client/evidence/{evidence_id}/download")
def client_download(evidence_id: UUID, authorization: str | None = Header(default=None)):
    p = require_client_read(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        allowed = tenant_ids(cur, p)
        cur.execute(
            """
            SELECT e.object_key FROM event_evidence e
            JOIN event_logs l ON l.id=e.event_log_id
            WHERE e.id=%s AND l.client_visible=true AND l.condominium_id=ANY(%s)
            """, (evidence_id, allowed)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="evidence not found")
    return _stream_object(row["object_key"], "attachment")


@router.get("/api/v1/admin/evidence/{evidence_id}/view")
def admin_view(evidence_id: UUID, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT object_key FROM event_evidence WHERE id=%s", (evidence_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="evidence not found")
    return _stream_object(row["object_key"], "inline")
