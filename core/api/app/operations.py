from __future__ import annotations

import json
import os
import secrets
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .auth_utils import hash_password, verify_password
from .platform import pool, require_admin, require_client_read, token_hash, tenant_ids

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090").rstrip("/")
router = APIRouter()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=256)


class UserPasswordCreate(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=256)
    role: Literal["admin", "operator", "reviewer", "client"]
    condominium_ids: list[UUID] = Field(default_factory=list)
    active: bool = True


class UserPasswordReset(BaseModel):
    password: str = Field(min_length=8, max_length=256)


class DVRUpdate(BaseModel):
    condominium_id: UUID
    name: str = Field(min_length=1, max_length=160)
    model: str | None = None
    connection_mode: Literal["intelbras_p2p", "rtsp", "edge_push"] = "intelbras_p2p"
    serial_secret_ref: str | None = None
    username_secret_ref: str | None = None
    password_secret_ref: str | None = None
    ip_lan: str | None = None
    ip_wan: str | None = None
    rtsp_tcp_port: int | None = Field(default=None, ge=1, le=65535)
    rtsp_udp_port: int | None = Field(default=None, ge=1, le=65535)
    tcp_p2p_port: int | None = Field(default=None, ge=1, le=65535)
    channel_count: int | None = Field(default=None, ge=1, le=512)
    ddns_host: str | None = None
    mac: str | None = None
    ddns_lan_ip: str | None = None
    ddns_wan_ip: str | None = None
    notes: str | None = None
    enabled: bool = True


class CameraUpdate(BaseModel):
    condominium_id: UUID
    dvr_id: UUID
    channel: int = Field(ge=1, le=512)
    name: str = Field(min_length=1, max_length=160)
    enabled: bool = True


def _prometheus_query(query: str) -> float | None:
    try:
        url = f"{PROMETHEUS_URL}/api/v1/query?{urllib.parse.urlencode({'query': query})}"
        with urllib.request.urlopen(url, timeout=3) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        result = payload.get("data", {}).get("result", [])
        if not result:
            return None
        return float(result[0]["value"][1])
    except Exception:
        return None


def _issue_session_token(cur, user_id: UUID, role: str) -> str:
    token = secrets.token_urlsafe(32)
    scopes = ["client:read"]
    if role == "admin":
        scopes = ["admin:*", "client:read"]
    elif role in {"operator", "reviewer"}:
        scopes = ["client:read"]
    cur.execute(
        """
        INSERT INTO user_api_tokens(user_id,token_hash,label,scopes,expires_at)
        VALUES (%s,%s,'portal-login',%s,now()+interval '12 hours')
        """,
        (user_id, token_hash(token), scopes),
    )
    return token


@router.post("/api/v1/auth/login")
def login(payload: LoginRequest, request: Request):
    username = payload.username.strip()
    remote_hint = request.client.host if request.client else None
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id,username,password_hash,role,active FROM users WHERE username=%s", (username,))
        user = cur.fetchone()
        ok = bool(user and user["active"] and verify_password(payload.password, user["password_hash"]))
        cur.execute(
            "INSERT INTO portal_login_audit(user_id,username,success,remote_hint) VALUES (%s,%s,%s,%s)",
            (user["id"] if user else None, username, ok, remote_hint),
        )
        if not ok:
            conn.commit()
            raise HTTPException(status_code=401, detail="usuario ou senha invalidos")
        token = _issue_session_token(cur, user["id"], user["role"])
        cur.execute("UPDATE users SET last_login_at=now(),updated_at=now() WHERE id=%s", (user["id"],))
        cur.execute("SELECT condominium_id FROM user_condominiums WHERE user_id=%s ORDER BY condominium_id", (user["id"],))
        condominium_ids = [str(r["condominium_id"]) for r in cur.fetchall()]
        conn.commit()
        return {"token": token, "role": user["role"], "username": user["username"], "condominium_ids": condominium_ids}


@router.get("/api/v1/auth/me")
def auth_me(authorization: str | None = Header(default=None)):
    p = require_client_read(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        ids = tenant_ids(cur, p)
        return {"role": p["role"], "user_id": p.get("user_id"), "condominium_ids": ids}


@router.post("/api/v1/admin/users-password", status_code=201)
def create_user_password(payload: UserPasswordCreate, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    try:
        password_hash = hash_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    with pool.connection() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO users(username,password_hash,role,active) VALUES (%s,%s,%s,%s) RETURNING id,username,role,active,created_at",
                (payload.username.strip(), password_hash, payload.role, payload.active),
            )
            user = cur.fetchone()
            for condominium_id in payload.condominium_ids:
                cur.execute("SELECT id FROM condominiums WHERE id=%s", (condominium_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail=f"condominium not found: {condominium_id}")
                cur.execute(
                    "INSERT INTO user_condominiums(user_id,condominium_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                    (user["id"], condominium_id),
                )
            conn.commit()
            return user
        except Exception as exc:
            if isinstance(exc, HTTPException):
                raise
            if "duplicate key" in str(exc).lower():
                raise HTTPException(status_code=409, detail="username already exists") from exc
            raise


@router.put("/api/v1/admin/users/{user_id}/password")
def reset_password(user_id: UUID, payload: UserPasswordReset, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    try:
        password_hash = hash_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE users SET password_hash=%s,updated_at=now() WHERE id=%s RETURNING id", (password_hash, user_id))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="user not found")
        cur.execute("UPDATE user_api_tokens SET active=false WHERE user_id=%s", (user_id,))
        conn.commit()
    return {"status": "password_updated"}


@router.get("/api/v1/admin/inventory")
def admin_inventory(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM condominiums ORDER BY name")
        condominiums = cur.fetchall()
        cur.execute(
            """
            SELECT d.*,
                   s.id AS active_session_id,s.sdk_local_port,s.rtsp_local_port,s.state AS p2p_state,
                   s.started_at AS p2p_started_at,s.last_frame_at AS p2p_last_frame_at,
                   w.worker_key
              FROM dvrs d
              LEFT JOIN LATERAL (
                  SELECT * FROM p2p_sessions ps
                   WHERE ps.dvr_id=d.id AND ps.ended_at IS NULL
                   ORDER BY ps.started_at DESC LIMIT 1
              ) s ON true
              LEFT JOIN wine_workers w ON w.id=s.wine_worker_id
             ORDER BY d.condominium_id,d.name
            """
        )
        dvrs = cur.fetchall()
        cur.execute(
            """
            SELECT c.*,d.name AS dvr_name,co.name AS condominium_name,
                   r.local_uri AS active_route_uri,r.last_frame_at AS route_last_frame_at,r.source_type AS route_source_type
              FROM cameras c
              JOIN dvrs d ON d.id=c.dvr_id
              JOIN condominiums co ON co.id=c.condominium_id
              LEFT JOIN LATERAL (
                  SELECT sr.local_uri,sr.last_frame_at,sr.source_type
                    FROM stream_routes sr
                   WHERE sr.camera_id=c.id AND sr.state='ACTIVE' AND sr.deactivated_at IS NULL
                   ORDER BY sr.generation DESC LIMIT 1
              ) r ON true
             ORDER BY co.name,d.name,c.channel
            """
        )
        cameras = cur.fetchall()
        cur.execute("SELECT id,username,role,active,last_login_at,created_at FROM users ORDER BY username")
        users = cur.fetchall()
        return {"condominiums": condominiums, "dvrs": dvrs, "cameras": cameras, "users": users}


@router.put("/api/v1/admin/dvrs/{dvr_id}")
def update_dvr(dvr_id: UUID, payload: DVRUpdate, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM condominiums WHERE id=%s", (payload.condominium_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="condominium not found")
        cur.execute(
            """
            UPDATE dvrs SET condominium_id=%s,name=%s,model=%s,connection_mode=%s,
              serial_secret_ref=%s,username_secret_ref=%s,password_secret_ref=%s,
              ip_lan=%s,ip_wan=%s,rtsp_tcp_port=%s,rtsp_udp_port=%s,tcp_p2p_port=%s,
              channel_count=%s,ddns_host=%s,mac=%s,ddns_lan_ip=%s,ddns_wan_ip=%s,notes=%s,
              enabled=%s,updated_at=now()
            WHERE id=%s RETURNING *
            """,
            (payload.condominium_id,payload.name.strip(),payload.model,payload.connection_mode,
             payload.serial_secret_ref,payload.username_secret_ref,payload.password_secret_ref,
             payload.ip_lan,payload.ip_wan,payload.rtsp_tcp_port,payload.rtsp_udp_port,payload.tcp_p2p_port,
             payload.channel_count,payload.ddns_host,payload.mac,payload.ddns_lan_ip,payload.ddns_wan_ip,payload.notes,
             payload.enabled,dvr_id),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="dvr not found")
        conn.commit(); return row


@router.put("/api/v1/admin/cameras/{camera_id}")
def update_camera(camera_id: UUID, payload: CameraUpdate, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT condominium_id FROM dvrs WHERE id=%s", (payload.dvr_id,))
        dvr = cur.fetchone()
        if not dvr:
            raise HTTPException(status_code=404, detail="dvr not found")
        if dvr["condominium_id"] != payload.condominium_id:
            raise HTTPException(status_code=409, detail="camera condominium does not match DVR")
        cur.execute(
            "UPDATE cameras SET condominium_id=%s,dvr_id=%s,channel=%s,name=%s,enabled=%s,updated_at=now() WHERE id=%s RETURNING *",
            (payload.condominium_id,payload.dvr_id,payload.channel,payload.name.strip(),payload.enabled,camera_id),
        )
        row=cur.fetchone()
        if not row: raise HTTPException(status_code=404, detail="camera not found")
        conn.commit(); return row


@router.get("/api/v1/admin/dashboard-full")
def dashboard_full(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT health_state,count(*)::int total FROM cameras WHERE enabled=true GROUP BY health_state")
        camera_states = {r["health_state"]: r["total"] for r in cur.fetchall()}
        cur.execute(
            """
            SELECT co.id,co.name,count(c.id)::int total,
                   count(c.id) FILTER(WHERE c.health_state='ONLINE')::int online,
                   count(c.id) FILTER(WHERE c.health_state<>'ONLINE')::int not_online
              FROM condominiums co LEFT JOIN cameras c ON c.condominium_id=co.id AND c.enabled=true
             WHERE co.active=true GROUP BY co.id,co.name ORDER BY co.name
            """
        )
        by_condominium = cur.fetchall()
        cur.execute("SELECT count(*)::int n FROM event_candidates WHERE review_status='HUMAN_REVIEW'")
        review_pending = cur.fetchone()["n"]
        cur.execute("SELECT count(*)::int n FROM event_logs WHERE status='APPROVED' AND occurred_at>=date_trunc('day',now())")
        approved_today = cur.fetchone()["n"]
        cur.execute("SELECT count(*)::int n FROM event_logs WHERE occurred_at>=now()-interval '5 minutes'")
        logs_realtime = cur.fetchone()["n"]
        cur.execute(
            """
            SELECT d.id,d.name,d.condominium_id,s.sdk_local_port,s.rtsp_local_port,s.state,s.started_at,s.last_frame_at,w.worker_key
              FROM dvrs d LEFT JOIN LATERAL (
                SELECT * FROM p2p_sessions ps WHERE ps.dvr_id=d.id AND ps.ended_at IS NULL ORDER BY ps.started_at DESC LIMIT 1
              ) s ON true LEFT JOIN wine_workers w ON w.id=s.wine_worker_id
             WHERE d.enabled=true ORDER BY d.condominium_id,d.name
            """
        )
        tunnels = cur.fetchall()
    mem_available = _prometheus_query("node_memory_MemAvailable_bytes")
    mem_total = _prometheus_query("node_memory_MemTotal_bytes")
    gpu_util = _prometheus_query("DCGM_FI_DEV_GPU_UTIL")
    gpu_mem_used = _prometheus_query("DCGM_FI_DEV_FB_USED")
    gpu_mem_free = _prometheus_query("DCGM_FI_DEV_FB_FREE")
    ram_used_pct = None
    if mem_total and mem_available is not None and mem_total > 0:
        ram_used_pct = round((1-(mem_available/mem_total))*100,2)
    return {
        "camera_states": camera_states,
        "review_pending": review_pending,
        "approved_today": approved_today,
        "logs_last_5m": logs_realtime,
        "by_condominium": by_condominium,
        "tunnels": tunnels,
        "system": {
            "ram_used_percent": ram_used_pct,
            "ram_total_bytes": mem_total,
            "ram_available_bytes": mem_available,
            "gpu_util_percent": gpu_util,
            "gpu_memory_used_mb": gpu_mem_used,
            "gpu_memory_free_mb": gpu_mem_free,
        },
    }


@router.get("/api/v1/admin/p2p/history/{dvr_id}")
def p2p_history_full(dvr_id: UUID, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id,s.sdk_local_port,s.rtsp_local_port,s.state,s.started_at,s.ended_at,s.open_reason,s.close_reason,
                   s.last_sdk_error,s.last_frame_at,s.frame_probe_count,w.worker_key
              FROM p2p_sessions s LEFT JOIN wine_workers w ON w.id=s.wine_worker_id
             WHERE s.dvr_id=%s ORDER BY s.started_at DESC LIMIT 200
            """, (dvr_id,)
        )
        sessions=cur.fetchall()
        cur.execute("SELECT * FROM p2p_failover_operations WHERE dvr_id=%s ORDER BY started_at DESC LIMIT 100", (dvr_id,))
        operations=cur.fetchall()
        return {"sessions": sessions, "failovers": operations}


@router.get("/api/v1/client/condominiums")
def client_condominiums(authorization: str | None = Header(default=None)):
    principal=require_client_read(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        ids=tenant_ids(cur,principal)
        if not ids: return []
        cur.execute("SELECT id,code,name FROM condominiums WHERE id=ANY(%s) AND active=true ORDER BY name", (ids,))
        return cur.fetchall()
