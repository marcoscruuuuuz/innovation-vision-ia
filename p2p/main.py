import os
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import FastAPI, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://vision:vision@postgres:5432/vision")
SDK_PORT_START = int(os.getenv("P2P_SDK_PORT_START", "21000"))
SDK_PORT_END = int(os.getenv("P2P_SDK_PORT_END", "21999"))
RTSP_PORT_START = int(os.getenv("P2P_RTSP_PORT_START", "22000"))
RTSP_PORT_END = int(os.getenv("P2P_RTSP_PORT_END", "22999"))
LEASE_SECONDS = int(os.getenv("P2P_LEASE_SECONDS", "120"))
VENDOR_ADAPTER_ENABLED = os.getenv("INTELBRAS_VENDOR_ADAPTER_ENABLED", "false").lower() == "true"
app = FastAPI(title="INNOVATION VISION P2P Supervisor", version="0.1.0")


def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


class WorkerHeartbeat(BaseModel):
    worker_key: str = Field(min_length=1, max_length=120)
    host: str = Field(default="local", min_length=1, max_length=255)
    pid: int | None = Field(default=None, ge=1)
    state: str = Field(default="RUNNING", max_length=40)
    cpu_percent: float | None = Field(default=None, ge=0)
    ram_mb: float | None = Field(default=None, ge=0)
    active_sessions: int = Field(default=0, ge=0)


class LeaseRequest(BaseModel):
    dvr_id: UUID
    wine_worker_id: UUID | None = None
    actor_type: Literal["USER", "AI", "WATCHDOG", "SYSTEM"] = "SYSTEM"


class RotateRequest(BaseModel):
    actor_type: Literal["USER", "AI", "WATCHDOG", "SYSTEM"] = "USER"
    reason: str = Field(default="emergency_rotate", min_length=1, max_length=255)
    execute: bool = False


def reserve_one(cur, port_type: str, start: int, end: int, dvr_id: UUID, worker_id: UUID | None, owner: str):
    cur.execute(
        "UPDATE p2p_port_leases SET released_at=now() WHERE released_at IS NULL AND lease_expires_at IS NOT NULL AND lease_expires_at < now()"
    )
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS)
    for port in range(start, end + 1):
        cur.execute(
            """
            INSERT INTO p2p_port_leases(port_type, port, dvr_id, wine_worker_id, lease_owner, lease_expires_at)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING
            RETURNING id, port_type, port, dvr_id, wine_worker_id, lease_owner, leased_at, lease_expires_at
            """,
            (port_type, port, dvr_id, worker_id, owner, expires_at),
        )
        row = cur.fetchone()
        if row:
            return row
    raise HTTPException(status_code=503, detail=f"no free {port_type} ports")


@app.get("/health")
def health():
    try:
        with db() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return {"status": "ok", "vendor_adapter_enabled": VENDOR_ADAPTER_ENABLED}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc


@app.post("/v1/wine/heartbeat")
def wine_heartbeat(payload: WorkerHeartbeat):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO wine_workers(worker_key, host, pid, state, cpu_percent, ram_mb, active_sessions, last_heartbeat_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,now())
            ON CONFLICT(worker_key) DO UPDATE SET
              host=excluded.host, pid=excluded.pid, state=excluded.state,
              cpu_percent=excluded.cpu_percent, ram_mb=excluded.ram_mb,
              active_sessions=excluded.active_sessions, last_heartbeat_at=now()
            RETURNING *
            """,
            (payload.worker_key, payload.host, payload.pid, payload.state, payload.cpu_percent, payload.ram_mb, payload.active_sessions),
        )
        row = cur.fetchone()
        conn.commit()
        return row


@app.get("/v1/wine")
def list_wine():
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT w.*, (w.last_heartbeat_at >= now() - interval '90 seconds') AS heartbeat_fresh FROM wine_workers w ORDER BY worker_key"
        )
        return cur.fetchall()


@app.post("/v1/p2p/leases/reserve")
def reserve_ports(payload: LeaseRequest):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, enabled, connection_mode FROM dvrs WHERE id=%s FOR UPDATE", (payload.dvr_id,))
        dvr = cur.fetchone()
        if not dvr:
            raise HTTPException(status_code=404, detail="DVR not found")
        if not dvr["enabled"]:
            raise HTTPException(status_code=409, detail="DVR disabled")
        if dvr["connection_mode"] != "intelbras_p2p":
            raise HTTPException(status_code=409, detail="DVR is not configured for intelbras_p2p")

        owner = f"{payload.actor_type}:{payload.dvr_id}"
        sdk = reserve_one(cur, "SDK_TCP", SDK_PORT_START, SDK_PORT_END, payload.dvr_id, payload.wine_worker_id, owner)
        rtsp = reserve_one(cur, "RTSP", RTSP_PORT_START, RTSP_PORT_END, payload.dvr_id, payload.wine_worker_id, owner)
        cur.execute(
            """
            INSERT INTO audit_logs(actor_type, action, object_type, object_id, metadata)
            VALUES (%s,'p2p.ports.reserve','dvr',%s,%s::jsonb)
            """,
            (payload.actor_type, str(payload.dvr_id), '{"status":"reserved"}'),
        )
        conn.commit()
        return {"sdk": sdk, "rtsp": rtsp}


@app.get("/v1/p2p/leases")
def list_active_leases(dvr_id: UUID | None = None):
    with db() as conn, conn.cursor() as cur:
        if dvr_id:
            cur.execute(
                "SELECT * FROM p2p_port_leases WHERE released_at IS NULL AND dvr_id=%s ORDER BY leased_at DESC",
                (dvr_id,),
            )
        else:
            cur.execute("SELECT * FROM p2p_port_leases WHERE released_at IS NULL ORDER BY leased_at DESC LIMIT 500")
        return cur.fetchall()


@app.get("/v1/p2p/sessions")
def list_sessions(dvr_id: UUID | None = None):
    with db() as conn, conn.cursor() as cur:
        if dvr_id:
            cur.execute(
                "SELECT s.*, w.worker_key FROM p2p_sessions s LEFT JOIN wine_workers w ON w.id=s.wine_worker_id WHERE s.dvr_id=%s ORDER BY s.started_at DESC LIMIT 100",
                (dvr_id,),
            )
        else:
            cur.execute("SELECT s.*, w.worker_key FROM p2p_sessions s LEFT JOIN wine_workers w ON w.id=s.wine_worker_id ORDER BY s.started_at DESC LIMIT 250")
        return cur.fetchall()


@app.get("/v1/p2p/dvrs/{dvr_id}/history")
def dvr_tunnel_history(dvr_id: UUID):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id, s.state, s.sdk_local_port, s.rtsp_local_port, s.started_at, s.ended_at,
                   s.open_reason, s.close_reason, s.latency_ms, s.last_sdk_error, s.actor_type, w.worker_key
            FROM p2p_sessions s LEFT JOIN wine_workers w ON w.id=s.wine_worker_id
            WHERE s.dvr_id=%s ORDER BY s.started_at DESC LIMIT 200
            """,
            (dvr_id,),
        )
        return cur.fetchall()


@app.post("/v1/p2p/dvrs/{dvr_id}/rotate")
def rotate_tunnel(dvr_id: UUID, payload: RotateRequest):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, enabled, connection_mode FROM dvrs WHERE id=%s", (dvr_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="DVR not found")
        cur.execute(
            "SELECT s.*, w.worker_key FROM p2p_sessions s LEFT JOIN wine_workers w ON w.id=s.wine_worker_id WHERE s.dvr_id=%s AND s.ended_at IS NULL ORDER BY s.started_at DESC LIMIT 1",
            (dvr_id,),
        )
        current = cur.fetchone()
        plan = {
            "dvr_id": str(dvr_id),
            "current_session": current,
            "reason": payload.reason,
            "steps": [
                "reserve destination SDK/RTSP ports",
                "open new Intelbras P2P session",
                "validate authentication",
                "validate RTSP and multiple real frames",
                "switch StreamBroker atomically",
                "confirm heartbeat",
                "close previous session and release old leases",
                "persist audit/history",
            ],
            "vendor_adapter_enabled": VENDOR_ADAPTER_ENABLED,
        }
        if not payload.execute:
            return {"mode": "PLAN", "plan": plan}
        if not VENDOR_ADAPTER_ENABLED:
            raise HTTPException(status_code=409, detail={"message": "Intelbras vendor adapter is not installed/enabled; execution blocked", "plan": plan})
        raise HTTPException(status_code=501, detail="vendor adapter execution contract not implemented yet")


@app.get("/v1/scheduler/plan")
def scheduler_plan():
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, worker_key, state, cpu_percent, ram_mb, active_sessions, last_heartbeat_at,
                   (COALESCE(active_sessions,0) * 10.0 + COALESCE(cpu_percent,0) * 0.5 + COALESCE(ram_mb,0) / 1024.0) AS load_score
            FROM wine_workers ORDER BY load_score ASC, worker_key ASC
            """
        )
        return {"workers": cur.fetchall(), "policy": "lowest_load_score_first", "execution": "requires validated destination session"}
