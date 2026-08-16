from __future__ import annotations

from io import BytesIO
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse

from .media import MINIO_BUCKET, minio
from .platform import pool, require_admin

router = APIRouter()


@router.get("/api/v1/admin/cameras/{camera_id}/latest-snapshot")
def latest_snapshot(camera_id: UUID, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT snapshot_object_key
              FROM ingestion_events
             WHERE camera_id=%s AND snapshot_object_key IS NOT NULL
             ORDER BY occurred_at DESC LIMIT 1
            """, (camera_id,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="camera has no snapshot yet")
    try:
        obj = minio.get_object(MINIO_BUCKET, row["snapshot_object_key"])
        data = obj.read(); obj.close(); obj.release_conn()
    except Exception as exc:
        raise HTTPException(status_code=404, detail="snapshot object not found") from exc
    return StreamingResponse(BytesIO(data), media_type="image/jpeg", headers={"Cache-Control": "no-store"})
