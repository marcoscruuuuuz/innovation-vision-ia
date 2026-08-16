from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://vision:vision@postgres:5432/vision",
)

pool = ConnectionPool(conninfo=DATABASE_URL, min_size=1, max_size=10, kwargs={"row_factory": dict_row})
app = FastAPI(title="INNOVATION VISION IA API", version="0.1.0")

CameraState = Literal[
    "ONLINE",
    "DEGRADED",
    "OFFLINE",
    "P2P_CONNECTED_NO_VIDEO",
    "VIDEO_NO_FRAMES",
    "DECODER_ERROR",
    "AUTH_ERROR",
]


class CondominiumCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)


class DVRCreate(BaseModel):
    condominium_id: UUID
    name: str = Field(min_length=1, max_length=160)
    model: str | None = Field(default=None, max_length=160)
    connection_mode: Literal["intelbras_p2p", "rtsp", "edge_push"] = "intelbras_p2p"
    serial_secret_ref: str | None = None
    username_secret_ref: str | None = None
    password_secret_ref: str | None = None


class CameraCreate(BaseModel):
    condominium_id: UUID
    dvr_id: UUID
    channel: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=160)


class CameraHealthUpdate(BaseModel):
    state: CameraState
    fps: float | None = Field(default=None, ge=0)
    frame_gap_ms: float | None = Field(default=None, ge=0)
    decode_latency_ms: float | None = Field(default=None, ge=0)
    frame_received: bool = False
    heartbeat_received: bool = True


def fetch_one(sql: str, params: tuple = ()):
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()


def fetch_all(sql: str, params: tuple = ()):
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


@app.get("/health")
def health():
    try:
        row = fetch_one("SELECT now() AS db_time")
        return {"status": "ok", "database": "ok", "db_time": row["db_time"]}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"database unavailable: {type(exc).__name__}") from exc


@app.get("/api/v1/condominiums")
def list_condominiums(active: bool | None = Query(default=None)):
    if active is None:
        return fetch_all("SELECT * FROM condominiums ORDER BY name")
    return fetch_all("SELECT * FROM condominiums WHERE active = %s ORDER BY name", (active,))


@app.post("/api/v1/condominiums", status_code=201)
def create_condominium(payload: CondominiumCreate):
    try:
        row = fetch_one(
            """
            INSERT INTO condominiums (code, name)
            VALUES (%s, %s)
            RETURNING *
            """,
            (payload.code.strip(), payload.name.strip()),
        )
        return row
    except Exception as exc:
        if "duplicate key" in str(exc).lower():
            raise HTTPException(status_code=409, detail="condominium code already exists") from exc
        raise


@app.get("/api/v1/dvrs")
def list_dvrs(condominium_id: UUID | None = None):
    if condominium_id is None:
        return fetch_all("SELECT * FROM dvrs ORDER BY name")
    return fetch_all(
        "SELECT * FROM dvrs WHERE condominium_id = %s ORDER BY name",
        (condominium_id,),
    )


@app.post("/api/v1/dvrs", status_code=201)
def create_dvr(payload: DVRCreate):
    condominium = fetch_one("SELECT id FROM condominiums WHERE id = %s", (payload.condominium_id,))
    if not condominium:
        raise HTTPException(status_code=404, detail="condominium not found")

    return fetch_one(
        """
        INSERT INTO dvrs (
            condominium_id, name, model, connection_mode,
            serial_secret_ref, username_secret_ref, password_secret_ref
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            payload.condominium_id,
            payload.name.strip(),
            payload.model,
            payload.connection_mode,
            payload.serial_secret_ref,
            payload.username_secret_ref,
            payload.password_secret_ref,
        ),
    )


@app.get("/api/v1/cameras")
def list_cameras(
    condominium_id: UUID | None = None,
    dvr_id: UUID | None = None,
    state: CameraState | None = None,
):
    clauses: list[str] = []
    params: list[object] = []
    if condominium_id is not None:
        clauses.append("condominium_id = %s")
        params.append(condominium_id)
    if dvr_id is not None:
        clauses.append("dvr_id = %s")
        params.append(dvr_id)
    if state is not None:
        clauses.append("health_state = %s")
        params.append(state)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return fetch_all(f"SELECT * FROM cameras{where} ORDER BY dvr_id, channel", tuple(params))


@app.post("/api/v1/cameras", status_code=201)
def create_camera(payload: CameraCreate):
    dvr = fetch_one(
        "SELECT id, condominium_id FROM dvrs WHERE id = %s",
        (payload.dvr_id,),
    )
    if not dvr:
        raise HTTPException(status_code=404, detail="dvr not found")
    if dvr["condominium_id"] != payload.condominium_id:
        raise HTTPException(status_code=409, detail="camera condominium does not match DVR condominium")

    return fetch_one(
        """
        INSERT INTO cameras (condominium_id, dvr_id, channel, name)
        VALUES (%s, %s, %s, %s)
        RETURNING *
        """,
        (payload.condominium_id, payload.dvr_id, payload.channel, payload.name.strip()),
    )


@app.put("/api/v1/cameras/{camera_id}/health")
def update_camera_health(camera_id: UUID, payload: CameraHealthUpdate):
    now = datetime.now(timezone.utc)
    last_frame_at = now if payload.frame_received else None
    last_heartbeat_at = now if payload.heartbeat_received else None

    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM cameras WHERE id = %s FOR UPDATE", (camera_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="camera not found")

            cur.execute(
                """
                UPDATE cameras
                SET health_state = %s,
                    last_frame_at = COALESCE(%s, last_frame_at),
                    last_heartbeat_at = COALESCE(%s, last_heartbeat_at),
                    updated_at = now()
                WHERE id = %s
                RETURNING *
                """,
                (payload.state, last_frame_at, last_heartbeat_at, camera_id),
            )
            camera = cur.fetchone()

            cur.execute(
                """
                INSERT INTO camera_health_history
                    (camera_id, state, fps, frame_gap_ms, decode_latency_ms)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    camera_id,
                    payload.state,
                    payload.fps,
                    payload.frame_gap_ms,
                    payload.decode_latency_ms,
                ),
            )
        conn.commit()
    return camera


@app.get("/api/v1/dashboard/summary")
def dashboard_summary():
    camera_counts = fetch_all(
        """
        SELECT health_state, count(*)::int AS total
        FROM cameras
        GROUP BY health_state
        """
    )
    by_condominium = fetch_all(
        """
        SELECT
            c.id,
            c.code,
            c.name,
            count(cam.id)::int AS total_cameras,
            count(cam.id) FILTER (WHERE cam.health_state = 'ONLINE')::int AS online,
            count(cam.id) FILTER (WHERE cam.health_state = 'OFFLINE')::int AS offline,
            count(cam.id) FILTER (WHERE cam.health_state = 'DEGRADED')::int AS degraded
        FROM condominiums c
        LEFT JOIN cameras cam ON cam.condominium_id = c.id
        WHERE c.active = true
        GROUP BY c.id, c.code, c.name
        ORDER BY c.name
        """
    )
    return {
        "camera_states": {row["health_state"]: row["total"] for row in camera_counts},
        "condominiums": by_condominium,
    }
