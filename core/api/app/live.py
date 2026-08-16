from __future__ import annotations

import os
import socket
import struct
import subprocess
import time
from typing import Iterator
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from .platform import pool, require_admin

FFMPEG_BIN = os.getenv("FFMPEG_BIN", "/usr/bin/ffmpeg")
HOST_GATEWAY_NAME = os.getenv("VISION_HOST_GATEWAY", "")
T2U_CAPTURE_URL = os.getenv("T2U_CAPTURE_URL", "").rstrip("/")
T2U_CAPTURE_TIMEOUT = float(os.getenv("T2U_CAPTURE_TIMEOUT", "30"))
router = APIRouter()


def _docker_gateway() -> str:
    if HOST_GATEWAY_NAME:
        return HOST_GATEWAY_NAME
    try:
        with open("/proc/net/route", "r", encoding="ascii") as handle:
            next(handle, None)
            for line in handle:
                fields = line.split()
                if len(fields) >= 3 and fields[1] == "00000000":
                    return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
    except Exception:
        pass
    return "172.17.0.1"


def _route_for_camera(camera_id: UUID) -> dict:
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id,c.name,c.health_state,c.last_frame_at,d.connection_mode,
                   r.local_uri,r.source_type,r.last_frame_at AS route_last_frame_at,
                   s.last_health_at AS t2u_last_health_at
              FROM cameras c
              JOIN dvrs d ON d.id=c.dvr_id
              LEFT JOIN LATERAL (
                SELECT sr.local_uri,sr.source_type,sr.last_frame_at
                  FROM stream_routes sr
                 WHERE sr.camera_id=c.id AND sr.state='ACTIVE' AND sr.deactivated_at IS NULL
                 ORDER BY sr.generation DESC LIMIT 1
              ) r ON true
              LEFT JOIN LATERAL (
                SELECT ps.last_health_at
                  FROM p2p_sessions ps
                 WHERE ps.dvr_id=c.dvr_id
                   AND ps.vendor_session_ref LIKE 't2u:%'
                   AND ps.state='ACTIVE'
                   AND ps.ended_at IS NULL
                 ORDER BY ps.started_at DESC LIMIT 1
              ) s ON true
             WHERE c.id=%s
            """,
            (camera_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="camera not found")
        return row


def _container_reachable_uri(uri: str) -> str:
    parsed = urlsplit(uri)
    if parsed.scheme.lower() != "rtsp":
        raise HTTPException(status_code=409, detail="active route is not RTSP")
    host = parsed.hostname or ""
    if host not in {"127.0.0.1", "localhost", "0.0.0.0"}:
        return uri
    userinfo = ""
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo += f":{parsed.password}"
        userinfo += "@"
    port = f":{parsed.port}" if parsed.port else ""
    return urlunsplit((parsed.scheme, f"{userinfo}{_docker_gateway()}{port}", parsed.path, parsed.query, parsed.fragment))


def _mjpeg_frames(uri: str, fps: int, quality: int) -> Iterator[bytes]:
    cmd = [
        FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp",
        "-rw_timeout", "5000000", "-i", uri, "-an", "-vf", f"fps={fps}",
        "-q:v", str(quality), "-f", "mjpeg", "pipe:1",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    buffer = bytearray()
    try:
        if proc.stdout is None:
            raise RuntimeError("ffmpeg stdout unavailable")
        while True:
            chunk = proc.stdout.read(32768)
            if not chunk:
                break
            buffer.extend(chunk)
            while True:
                start = buffer.find(b"\xff\xd8")
                if start < 0:
                    if len(buffer) > 1048576:
                        del buffer[:-2]
                    break
                end = buffer.find(b"\xff\xd9", start + 2)
                if end < 0:
                    if start > 0:
                        del buffer[:start]
                    break
                frame = bytes(buffer[start:end+2])
                del buffer[:end+2]
                yield b"--frame\r\nContent-Type: image/jpeg\r\nCache-Control: no-store\r\n\r\n" + frame + b"\r\n"
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()


def _multipart_frame(frame: bytes) -> bytes:
    return b"--frame\r\nContent-Type: image/jpeg\r\nCache-Control: no-store\r\n\r\n" + frame + b"\r\n"


def _t2u_snapshot(camera_id: UUID) -> bytes:
    if not T2U_CAPTURE_URL:
        raise HTTPException(status_code=409, detail="Intelbras T2U capture service is not configured")
    request = urllib.request.Request(
        f"{T2U_CAPTURE_URL}/v1/cameras/{camera_id}/snapshot",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=T2U_CAPTURE_TIMEOUT) as response:
            image = response.read()
            content_type = response.headers.get_content_type()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise HTTPException(status_code=409, detail=f"Intelbras T2U capture rejected: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise HTTPException(status_code=503, detail="Intelbras T2U capture service is unavailable") from exc
    if content_type != "image/jpeg" or not image.startswith(b"\xff\xd8"):
        raise HTTPException(status_code=502, detail="Intelbras T2U capture returned no JPEG frame")
    return image


def _t2u_mjpeg_frames(camera_id: UUID, fps: int, first_frame: bytes) -> Iterator[bytes]:
    frame = first_frame
    while True:
        yield _multipart_frame(frame)
        time.sleep(max(0.05, 1 / fps))
        frame = _t2u_snapshot(camera_id)


@router.get("/api/v1/admin/cameras/{camera_id}/live/status")
def live_status(camera_id: UUID, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    row = _route_for_camera(camera_id)
    t2u_connected = bool(row["connection_mode"] == "intelbras_p2p" and row["t2u_last_health_at"])
    source_type = row["source_type"] or ("INTELBRAS_T2U_SDK" if t2u_connected else None)
    route_last_frame_at = row["route_last_frame_at"] or row["t2u_last_health_at"]
    return {
        "camera_id": row["id"], "name": row["name"], "health_state": row["health_state"],
        "last_frame_at": row["last_frame_at"], "route_source_type": source_type,
        "route_last_frame_at": route_last_frame_at,
        "has_live_route": bool(row["local_uri"] or t2u_connected),
    }


@router.get("/api/v1/admin/cameras/{camera_id}/live.mjpeg")
def live_mjpeg(camera_id: UUID, token: str | None = Query(default=None), authorization: str | None = Header(default=None), fps: int = Query(default=5, ge=1, le=12), quality: int = Query(default=5, ge=2, le=15)):
    auth = authorization or (f"Bearer {token}" if token else None)
    require_admin(auth)
    row = _route_for_camera(camera_id)
    if row["local_uri"]:
        uri = _container_reachable_uri(row["local_uri"])
        return StreamingResponse(
            _mjpeg_frames(uri, fps=fps, quality=quality),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )
    t2u_connected = bool(row["connection_mode"] == "intelbras_p2p" and row["t2u_last_health_at"])
    if not t2u_connected:
        raise HTTPException(status_code=409, detail="camera has no active stream route")
    first_frame = _t2u_snapshot(camera_id)
    return StreamingResponse(
        _t2u_mjpeg_frames(camera_id, fps=fps, first_frame=first_frame),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )

