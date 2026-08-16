from __future__ import annotations

import os
import urllib.error
import urllib.request
from pathlib import Path
from uuid import UUID

import yaml
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse

from .platform import pool, require_admin

router = APIRouter()
EVENT_CATALOG_PATH = Path(os.getenv("EVENT_CATALOG_PATH", "/app/configs/event_catalog.yaml"))
LIVE_HTTP_TIMEOUT = float(os.getenv("VISION_LIVE_HTTP_TIMEOUT", "15"))


@router.get("/api/v1/admin/event-catalog")
def event_catalog(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    try:
        return yaml.safe_load(EVENT_CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="event catalog unavailable") from exc


@router.get("/api/v1/admin/cameras/{camera_id}/live-info")
def live_info(camera_id: UUID, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT cam.id,cam.name,cam.channel,cam.health_state,cam.last_frame_at,
                   d.id AS dvr_id,d.name AS dvr_name,co.id AS condominium_id,co.name AS condominium_name,
                   r.local_uri,r.last_frame_at AS route_last_frame_at,r.metadata,r.source_type,
                   s.sdk_local_port,s.rtsp_local_port,s.state AS session_state
              FROM cameras cam
              JOIN dvrs d ON d.id=cam.dvr_id
              JOIN condominiums co ON co.id=cam.condominium_id
              LEFT JOIN LATERAL (
                SELECT * FROM stream_routes sr
                 WHERE sr.camera_id=cam.id AND sr.state='ACTIVE' AND sr.deactivated_at IS NULL
                 ORDER BY sr.generation DESC LIMIT 1
              ) r ON true
              LEFT JOIN p2p_sessions s ON s.id=r.p2p_session_id
             WHERE cam.id=%s
            """,
            (camera_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="camera not found")
        metadata = row.get("metadata") or {}
        row["browser_live_url"] = metadata.get("live_http_url")
        row["live_capable"] = bool(metadata.get("live_http_url"))
        return row


@router.get("/api/v1/admin/cameras/{camera_id}/live.mjpg")
def live_mjpeg(camera_id: UUID, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT metadata FROM stream_routes
             WHERE camera_id=%s AND state='ACTIVE' AND deactivated_at IS NULL
             ORDER BY generation DESC LIMIT 1
            """,
            (camera_id,),
        )
        route = cur.fetchone()
    url = (route.get("metadata") or {}).get("live_http_url") if route else None
    if not url:
        raise HTTPException(status_code=409, detail="active route has no browser HTTP/MJPEG gateway")
    try:
        upstream = urllib.request.urlopen(url, timeout=LIVE_HTTP_TIMEOUT)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail="live video gateway unavailable") from exc

    def stream():
        try:
            while True:
                data = upstream.read(65536)
                if not data:
                    break
                yield data
        finally:
            upstream.close()

    content_type = upstream.headers.get("Content-Type", "multipart/x-mixed-replace")
    return StreamingResponse(stream(), media_type=content_type, headers={"Cache-Control": "no-store"})
