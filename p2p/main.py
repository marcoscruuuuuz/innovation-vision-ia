import json
import os
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import FastAPI, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from vendor_adapter import IntelbrasWineAdapter, VendorAdapterError

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://vision:vision@postgres:5432/vision")
SDK_PORT_START = int(os.getenv("P2P_SDK_PORT_START", "21000"))
SDK_PORT_END = int(os.getenv("P2P_SDK_PORT_END", "21999"))
RTSP_PORT_START = int(os.getenv("P2P_RTSP_PORT_START", "22000"))
RTSP_PORT_END = int(os.getenv("P2P_RTSP_PORT_END", "22999"))
LEASE_SECONDS = int(os.getenv("P2P_LEASE_SECONDS", "120"))
MINIMUM_PROBE_FRAMES = int(os.getenv("P2P_MINIMUM_PROBE_FRAMES", "3"))

adapter = IntelbrasWineAdapter()
app = FastAPI(title="INNOVATION VISION P2P Supervisor", version="0.3.0")


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


class SessionOpenRequest(BaseModel):
    actor_type: Literal["USER", "AI", "WATCHDOG", "SYSTEM"] = "USER"
    reason: str = Field(default="manual_open", min_length=1, max_length=255)
    wine_worker_id: UUID | None = None
    allow_parallel: bool = False


class SessionCloseRequest(BaseModel):
    actor_type: Literal["USER", "AI", "WATCHDOG", "SYSTEM"] = "USER"
    reason: str = Field(default="manual_close", min_length=1, max_length=255)


class RotateRequest(BaseModel):
    actor_type: Literal["USER", "AI", "WATCHDOG", "SYSTEM"] = "USER"
    reason: str = Field(default="emergency_rotate", min_length=1, max_length=255)
    execute: bool = False


def reserve_one(cur, port_type: str, start: int, end: int, dvr_id: UUID, worker_id: UUID | None, owner: str):
    cur.execute("UPDATE p2p_port_leases SET released_at=now() WHERE released_at IS NULL AND lease_expires_at IS NOT NULL AND lease_expires_at < now()")
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


def release_dvr_leases(cur, dvr_id: UUID, worker_id: UUID | None = None) -> None:
    if worker_id:
        cur.execute("UPDATE p2p_port_leases SET released_at=now() WHERE dvr_id=%s AND wine_worker_id=%s AND released_at IS NULL", (dvr_id, worker_id))
    else:
        cur.execute("UPDATE p2p_port_leases SET released_at=now() WHERE dvr_id=%s AND released_at IS NULL", (dvr_id,))


def select_worker(cur, requested: UUID | None):
    if requested:
        cur.execute("SELECT * FROM wine_workers WHERE id=%s AND state='RUNNING' AND last_heartbeat_at >= now() - interval '90 seconds'", (requested,))
    else:
        cur.execute(
            """
            SELECT *, (COALESCE(active_sessions,0)*10.0 + COALESCE(cpu_percent,0)*0.5 + COALESCE(ram_mb,0)/1024.0) AS load_score
            FROM wine_workers
            WHERE state='RUNNING' AND last_heartbeat_at >= now() - interval '90 seconds'
            ORDER BY load_score ASC, worker_key ASC LIMIT 1
            """
        )
    worker = cur.fetchone()
    if not worker:
        raise HTTPException(status_code=503, detail="no healthy Wine worker available")
    return worker


@app.get("/health")
def health():
    try:
        with db() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return {"status": "ok", "vendor_adapter": adapter.public_status()}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc


@app.get("/v1/vendor/status")
def vendor_status():
    return adapter.public_status()


@app.post("/v1/wine/heartbeat")
def wine_heartbeat(payload: WorkerHeartbeat):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO wine_workers(worker_key, host, pid, state, cpu_percent, ram_mb, active_sessions, last_heartbeat_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,now())
            ON CONFLICT(worker_key) DO UPDATE SET host=excluded.host, pid=excluded.pid, state=excluded.state,
            cpu_percent=excluded.cpu_percent, ram_mb=excluded.ram_mb, active_sessions=excluded.active_sessions, last_heartbeat_at=now()
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
        cur.execute("SELECT w.*, (w.last_heartbeat_at >= now() - interval '90 seconds') AS heartbeat_fresh FROM wine_workers w ORDER BY worker_key")
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
        cur.execute("INSERT INTO audit_logs(actor_type, action, object_type, object_id, metadata) VALUES (%s,'p2p.ports.reserve','dvr',%s,%s::jsonb)", (payload.actor_type, str(payload.dvr_id), '{"status":"reserved"}'))
        conn.commit()
        return {"sdk": sdk, "rtsp": rtsp}


@app.post("/v1/p2p/dvrs/{dvr_id}/open")
def open_dvr_session(dvr_id: UUID, payload: SessionOpenRequest):
    status = adapter.status()
    if not (status.enabled and status.configured and status.executable_found):
        raise HTTPException(status_code=409, detail={"message": "Intelbras vendor adapter is not ready", "adapter": adapter.public_status()})

    vendor_session_ref = None
    worker = None
    sdk = None
    rtsp = None
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM dvrs WHERE id=%s FOR UPDATE", (dvr_id,))
        dvr = cur.fetchone()
        if not dvr:
            raise HTTPException(status_code=404, detail="DVR not found")
        if not dvr["enabled"] or dvr["connection_mode"] != "intelbras_p2p":
            raise HTTPException(status_code=409, detail="DVR is not enabled for intelbras_p2p")
        missing = [k for k in ("serial_secret_ref", "username_secret_ref", "password_secret_ref") if not dvr.get(k)]
        if missing:
            raise HTTPException(status_code=409, detail={"message": "DVR secret references are incomplete", "missing": missing})
        cur.execute("SELECT id,wine_worker_id FROM p2p_sessions WHERE dvr_id=%s AND ended_at IS NULL AND state IN ('OPENING','ACTIVE','DEGRADED') ORDER BY started_at DESC", (dvr_id,))
        existing = cur.fetchall()
        if existing and not payload.allow_parallel:
            raise HTTPException(status_code=409, detail="DVR already has an active/opening P2P session")
        if payload.allow_parallel and len(existing) >= 2:
            raise HTTPException(status_code=409, detail="DVR already has the maximum two concurrent failover sessions")
        worker = select_worker(cur, payload.wine_worker_id)
        if payload.allow_parallel and any(row["wine_worker_id"] == worker["id"] for row in existing):
            raise HTTPException(status_code=409, detail="parallel failover session must use a different Wine worker")
        owner = f"SESSION:{dvr_id}:{worker['worker_key']}"
        sdk = reserve_one(cur, "SDK_TCP", SDK_PORT_START, SDK_PORT_END, dvr_id, worker["id"], owner)
        rtsp = reserve_one(cur, "RTSP", RTSP_PORT_START, RTSP_PORT_END, dvr_id, worker["id"], owner)
        conn.commit()

    try:
        opened = adapter.open_session(
            dvr_id=str(dvr_id),
            serial_secret_ref=dvr["serial_secret_ref"],
            username_secret_ref=dvr["username_secret_ref"],
            password_secret_ref=dvr["password_secret_ref"],
            sdk_local_port=sdk["port"],
            rtsp_local_port=rtsp["port"],
            wine_worker_key=worker["worker_key"],
        )
        vendor_session_ref = opened.session_ref
        probe = adapter.probe_frames(session_ref=opened.session_ref, minimum_frames=MINIMUM_PROBE_FRAMES)
    except VendorAdapterError as exc:
        if vendor_session_ref:
            try:
                adapter.close_session(session_ref=vendor_session_ref)
            except VendorAdapterError:
                pass
        with db() as conn, conn.cursor() as cur:
            release_dvr_leases(cur, dvr_id, worker["id"])
            cur.execute("INSERT INTO audit_logs(actor_type, action, object_type, object_id, metadata) VALUES (%s,'p2p.session.open_failed','dvr',%s,%s::jsonb)", (payload.actor_type, str(dvr_id), json.dumps({"error": str(exc), "worker": worker["worker_key"]})))
            conn.commit()
        raise HTTPException(status_code=502, detail=f"Intelbras session validation failed: {exc}") from exc

    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO p2p_sessions(dvr_id,wine_worker_id,sdk_local_port,rtsp_local_port,state,open_reason,actor_type,vendor_session_ref,relay_mode,last_health_at,last_frame_at,frame_probe_count,vendor_metadata)
            VALUES (%s,%s,%s,%s,'ACTIVE',%s,%s,%s,%s,now(),now(),%s,%s::jsonb) RETURNING *
            """,
            (dvr_id, worker["id"], sdk["port"], rtsp["port"], payload.reason, payload.actor_type, opened.session_ref, opened.relay_mode, probe.frame_count, json.dumps(opened.vendor_metadata or {})),
        )
        session = cur.fetchone()
        cur.execute("UPDATE p2p_port_leases SET lease_expires_at=NULL WHERE dvr_id=%s AND wine_worker_id=%s AND released_at IS NULL", (dvr_id, worker["id"]))
        cur.execute("UPDATE wine_workers SET active_sessions=active_sessions+1 WHERE id=%s", (worker["id"],))
        cur.execute("INSERT INTO audit_logs(actor_type, action, object_type, object_id, metadata) VALUES (%s,'p2p.session.active','dvr',%s,%s::jsonb)", (payload.actor_type, str(dvr_id), json.dumps({"session_id": str(session["id"]), "worker": worker["worker_key"], "frames": probe.frame_count, "parallel": payload.allow_parallel})))
        conn.commit()
        return {"session": session, "probe": probe.__dict__, "worker": worker["worker_key"]}


@app.post("/v1/p2p/sessions/{session_id}/close")
def close_session(session_id: UUID, payload: SessionCloseRequest):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM p2p_sessions WHERE id=%s", (session_id,))
        session = cur.fetchone()
        if not session:
            raise HTTPException(status_code=404, detail="P2P session not found")
        if session["ended_at"] is not None or session["state"] == "CLOSED":
            return {"status": "ALREADY_CLOSED", "session_id": str(session_id)}

    if session.get("vendor_session_ref"):
        try:
            adapter.close_session(session_ref=session["vendor_session_ref"])
        except VendorAdapterError as exc:
            raise HTTPException(status_code=502, detail=f"vendor session close failed: {exc}") from exc

    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM p2p_sessions WHERE id=%s FOR UPDATE", (session_id,))
        locked = cur.fetchone()
        if locked["ended_at"] is not None:
            return {"status": "ALREADY_CLOSED", "session_id": str(session_id)}
        cur.execute("UPDATE p2p_sessions SET state='CLOSED',ended_at=now(),close_reason=%s WHERE id=%s RETURNING *", (payload.reason, session_id))
        closed = cur.fetchone()
        release_dvr_leases(cur, locked["dvr_id"], locked["wine_worker_id"])
        if locked["wine_worker_id"]:
            cur.execute("UPDATE wine_workers SET active_sessions=GREATEST(active_sessions-1,0) WHERE id=%s", (locked["wine_worker_id"],))
        cur.execute("INSERT INTO audit_logs(actor_type,action,object_type,object_id,metadata) VALUES (%s,'p2p.session.closed','p2p_session',%s,%s::jsonb)", (payload.actor_type, str(session_id), json.dumps({"reason": payload.reason, "dvr_id": str(locked["dvr_id"])})))
        conn.commit()
        return {"status": "CLOSED", "session": closed}


@app.get("/v1/p2p/leases")
def list_active_leases(dvr_id: UUID | None = None):
    with db() as conn, conn.cursor() as cur:
        if dvr_id:
            cur.execute("SELECT * FROM p2p_port_leases WHERE released_at IS NULL AND dvr_id=%s ORDER BY leased_at DESC", (dvr_id,))
        else:
            cur.execute("SELECT * FROM p2p_port_leases WHERE released_at IS NULL ORDER BY leased_at DESC LIMIT 500")
        return cur.fetchall()


@app.get("/v1/p2p/sessions")
def list_sessions(dvr_id: UUID | None = None):
    with db() as conn, conn.cursor() as cur:
        if dvr_id:
            cur.execute("SELECT s.*, w.worker_key FROM p2p_sessions s LEFT JOIN wine_workers w ON w.id=s.wine_worker_id WHERE s.dvr_id=%s ORDER BY s.started_at DESC LIMIT 100", (dvr_id,))
        else:
            cur.execute("SELECT s.*, w.worker_key FROM p2p_sessions s LEFT JOIN wine_workers w ON w.id=s.wine_worker_id ORDER BY s.started_at DESC LIMIT 250")
        return cur.fetchall()


@app.get("/v1/p2p/dvrs/{dvr_id}/history")
def dvr_tunnel_history(dvr_id: UUID):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT s.*, w.worker_key FROM p2p_sessions s LEFT JOIN wine_workers w ON w.id=s.wine_worker_id WHERE s.dvr_id=%s ORDER BY s.started_at DESC LIMIT 200", (dvr_id,))
        return cur.fetchall()


@app.post("/v1/p2p/dvrs/{dvr_id}/rotate")
def rotate_tunnel(dvr_id: UUID, payload: RotateRequest):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM dvrs WHERE id=%s", (dvr_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="DVR not found")
        cur.execute("SELECT s.*, w.worker_key FROM p2p_sessions s LEFT JOIN wine_workers w ON w.id=s.wine_worker_id WHERE s.dvr_id=%s AND s.ended_at IS NULL ORDER BY s.started_at DESC LIMIT 1", (dvr_id,))
        current = cur.fetchone()
    plan = {"dvr_id": str(dvr_id), "current_session": current, "reason": payload.reason, "steps": ["reserve destination ports", "open destination vendor session", "probe real frames", "switch StreamBroker atomically", "confirm active routes", "close previous session", "release old leases", "persist audit/history"], "vendor_adapter": adapter.public_status()}
    if not payload.execute:
        return {"mode": "PLAN", "plan": plan}
    raise HTTPException(status_code=409, detail={"message": "use Failover Orchestrator /v1/failover/{dvr_id}/run for transactional execution", "plan": plan})


@app.get("/v1/scheduler/plan")
def scheduler_plan():
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id,worker_key,state,cpu_percent,ram_mb,active_sessions,last_heartbeat_at,(COALESCE(active_sessions,0)*10.0+COALESCE(cpu_percent,0)*0.5+COALESCE(ram_mb,0)/1024.0) AS load_score FROM wine_workers ORDER BY load_score ASC,worker_key ASC")
        return {"workers": cur.fetchall(), "policy": "lowest_load_score_first", "execution": "destination must pass vendor session + frame probe"}
