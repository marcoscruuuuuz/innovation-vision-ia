from __future__ import annotations

import os
import socket
import struct
import subprocess
from typing import Iterator
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from .platform import pool, require_admin

FFMPEG_BIN = os.getenv("FFMPEG_BIN", "/usr/bin/ffmpeg")
HOST_GATEWAY_NAME = os.getenv("VISION_HOST_GATEWAY", "")
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
            SELECT c.id,c.name,c.health_state,c.last_frame_at,r.local_uri,r.source_type,r.last_frame_at AS route_last_frame_at
              FROM cameras c
              LEFT JOIN LATERAL (
                SELECT sr.local_uri,sr.source_type,sr.last_frame_at
                  FROM stream_routes sr
                 WHERE sr.camera_id=c.id AND sr.state='ACTIVE' AND sr.deactivated_at IS NULL
                 ORDER BY sr.generation DESC LIMIT 1
              ) r ON true
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
    cmd = [FFMPEG_BIN,"-hide_banner","-loglevel","error","-rtsp_transport","tcp","-stimeout","5000000","-i",uri,"-an","-vf",f"fps={fps}","-q:v",str(quality),"-f","mjpeg","pipe:1"]
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
                    if len(buffer) > 1048576: del buffer[:-2]
                    break
                end = buffer.find(b"\xff\xd9", start + 2)
                if end < 0:
                    if start > 0: del buffer[:start]
                    break
                frame = bytes(buffer[start:end+2]); del buffer[:end+2]
                yield b"--frame\r\nContent-Type: image/jpeg\r\nCache-Control: no-store\r\n\r\n" + frame + b"\r\n"
    finally:
        if proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=2)
            except subprocess.TimeoutExpired: proc.kill()


@router.get("/api/v1/admin/cameras/{camera_id}/live/status")
def live_status(camera_id: UUID, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    row = _route_for_camera(camera_id)
    return {"camera_id":row["id"],"name":row["name"],"health_state":row["health_state"],"last_frame_at":row["last_frame_at"],"route_source_type":row["source_type"],"route_last_frame_at":row["route_last_frame_at"],"has_live_route":bool(row["local_uri"])}


@router.get("/api/v1/admin/cameras/{camera_id}/live.mjpeg")
def live_mjpeg(camera_id: UUID, token: str | None = Query(default=None), authorization: str | None = Header(default=None), fps: int = Query(default=5, ge=1, le=12), quality: int = Query(default=5, ge=2, le=15)):
    auth = authorization or (f"Bearer {token}" if token else None)
    require_admin(auth)
    row = _route_for_camera(camera_id)
    if not row["local_uri"]:
        raise HTTPException(status_code=409, detail="camera has no active stream route")
    uri = _container_reachable_uri(row["local_uri"])
    return StreamingResponse(_mjpeg_frames(uri,fps=fps,quality=quality),media_type="multipart/x-mixed-replace; boundary=frame",headers={"Cache-Control":"no-store, no-cache, must-revalidate"})
