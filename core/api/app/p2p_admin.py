from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .platform import pool, require_admin

FAILOVER_ORCHESTRATOR_URL = os.getenv("FAILOVER_ORCHESTRATOR_URL", "http://failover-orchestrator:8092").rstrip("/")
FAILOVER_API_TIMEOUT = float(os.getenv("FAILOVER_API_TIMEOUT", "60"))
router = APIRouter()


class FailoverAction(BaseModel):
    reason: str = Field(default="admin_tunnel_switch", min_length=1, max_length=255)
    execute: bool = False


def orchestrator_call(dvr_id: UUID, body: dict):
    req = urllib.request.Request(
        f"{FAILOVER_ORCHESTRATOR_URL}/v1/failover/{dvr_id}/run",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=FAILOVER_API_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail={"message": "P2P orchestrator rejected request", "upstream_status": exc.code, "upstream": detail[:3000]}) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise HTTPException(status_code=503, detail="P2P/Wine control plane is not active") from exc


@router.get("/api/v1/admin/p2p/sessions")
def p2p_sessions(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id,s.dvr_id,d.name AS dvr_name,s.wine_worker_id,w.worker_key,
                   s.sdk_local_port,s.rtsp_local_port,s.state,s.started_at,s.ended_at,
                   s.last_health_at,s.last_frame_at,s.frame_probe_count,s.last_sdk_error
              FROM p2p_sessions s
              JOIN dvrs d ON d.id=s.dvr_id
              LEFT JOIN wine_workers w ON w.id=s.wine_worker_id
             ORDER BY s.started_at DESC LIMIT 500
            """
        )
        return cur.fetchall()


@router.get("/api/v1/admin/p2p/status")
def p2p_status(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*)::int AS total FROM p2p_sessions WHERE ended_at IS NULL AND state IN ('ACTIVE','DEGRADED')")
        active = cur.fetchone()["total"]
        cur.execute("SELECT count(*)::int AS total FROM wine_workers WHERE state='RUNNING' AND last_heartbeat_at>=now()-interval '90 seconds'")
        wines = cur.fetchone()["total"]
        cur.execute("SELECT count(*)::int AS total FROM p2p_failover_operations WHERE completed_at IS NULL AND state NOT IN ('COMMITTED','ROLLED_BACK','FAILED')")
        operations = cur.fetchone()["total"]
        return {"active_sessions": active, "healthy_wine_workers": wines, "failovers_in_progress": operations}


@router.get("/api/v1/admin/p2p/failover/{dvr_id}")
def failover_history(dvr_id: UUID, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM p2p_failover_operations WHERE dvr_id=%s ORDER BY started_at DESC LIMIT 100", (dvr_id,))
        return cur.fetchall()


@router.post("/api/v1/admin/p2p/failover/{dvr_id}")
def failover_action(dvr_id: UUID, payload: FailoverAction, authorization: str | None = Header(default=None)):
    principal = require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id,name,enabled,connection_mode FROM dvrs WHERE id=%s", (dvr_id,))
        dvr = cur.fetchone()
        if not dvr:
            raise HTTPException(status_code=404, detail="DVR not found")
        if not dvr["enabled"] or dvr["connection_mode"] != "intelbras_p2p":
            raise HTTPException(status_code=409, detail="DVR is not enabled for Intelbras P2P")
    result = orchestrator_call(dvr_id, {"actor_type": "USER", "reason": payload.reason, "execute": payload.execute})
    if payload.execute:
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO audit_logs(actor_type,actor_id,action,object_type,object_id,metadata) VALUES ('USER',%s,'p2p.failover.request','dvr',%s,%s::jsonb)",
                (str(principal["user_id"]), str(dvr_id), json.dumps({"reason": payload.reason})),
            )
            conn.commit()
    return result
