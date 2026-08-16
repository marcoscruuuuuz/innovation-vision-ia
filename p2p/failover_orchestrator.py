import json
import os
import urllib.error
import urllib.request
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import FastAPI, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://vision:vision@postgres:5432/vision")
P2P_SUPERVISOR_URL = os.getenv("P2P_SUPERVISOR_URL", "http://p2p-supervisor:8090")
STREAM_BROKER_URL = os.getenv("STREAM_BROKER_URL", "http://stream-broker:8091")
REQUEST_TIMEOUT = float(os.getenv("FAILOVER_REQUEST_TIMEOUT", "45"))

app = FastAPI(title="INNOVATION VISION Failover Orchestrator", version="0.2.0")


def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def api(method: str, url: str, body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {payload}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"request failed for {url}: {exc}") from exc


class FailoverRequest(BaseModel):
    actor_type: Literal["USER", "AI", "WATCHDOG", "SYSTEM"] = "USER"
    reason: str = Field(default="automatic_failover", min_length=1, max_length=255)
    execute: bool = False


def set_operation(cur, operation_id: UUID, state: str, step: str, *, error: str | None = None, details: dict | None = None, completed: bool = False):
    cur.execute(
        """
        UPDATE p2p_failover_operations
           SET state=%s, step=%s, error=%s,
               details=details || %s::jsonb,
               updated_at=now(),
               completed_at=CASE WHEN %s THEN now() ELSE completed_at END
         WHERE id=%s
        """,
        (state, step, error, json.dumps(details or {}), completed, operation_id),
    )


def select_destination_worker(cur, source_worker_id):
    cur.execute(
        """
        SELECT id, worker_key, state, cpu_percent, ram_mb, active_sessions,
               (COALESCE(active_sessions,0)*10.0 + COALESCE(cpu_percent,0)*0.5 + COALESCE(ram_mb,0)/1024.0) AS load_score
          FROM wine_workers
         WHERE state='RUNNING'
           AND last_heartbeat_at >= now() - interval '90 seconds'
           AND (%s::uuid IS NULL OR id <> %s::uuid)
         ORDER BY load_score ASC, worker_key ASC
         LIMIT 1
        """,
        (source_worker_id, source_worker_id),
    )
    return cur.fetchone()


@app.get("/health")
def health():
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()
    return {"status": "ok", "p2p_supervisor": P2P_SUPERVISOR_URL, "stream_broker": STREAM_BROKER_URL}


@app.get("/v1/failover/{dvr_id}")
def history(dvr_id: UUID):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM p2p_failover_operations WHERE dvr_id=%s ORDER BY started_at DESC LIMIT 100", (dvr_id,))
        return cur.fetchall()


@app.post("/v1/failover/{dvr_id}/run")
def run_failover(dvr_id: UUID, payload: FailoverRequest):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM dvrs WHERE id=%s FOR UPDATE", (dvr_id,))
        dvr = cur.fetchone()
        if not dvr:
            raise HTTPException(status_code=404, detail="DVR not found")
        cur.execute(
            """
            SELECT * FROM p2p_sessions
             WHERE dvr_id=%s AND ended_at IS NULL AND state IN ('ACTIVE','DEGRADED')
             ORDER BY started_at DESC LIMIT 1
            """,
            (dvr_id,),
        )
        source = cur.fetchone()
        source_worker_id = source["wine_worker_id"] if source else None
        destination_worker = select_destination_worker(cur, source_worker_id)
        if not destination_worker:
            raise HTTPException(status_code=503, detail="no alternate healthy Wine worker available")
        cur.execute(
            """
            INSERT INTO p2p_failover_operations(dvr_id,source_session_id,source_wine_worker_id,destination_wine_worker_id,actor_type,reason,state,step,details)
            VALUES (%s,%s,%s,%s,%s,%s,'PLANNED','select_destination',%s::jsonb)
            RETURNING *
            """,
            (
                dvr_id,
                source["id"] if source else None,
                source_worker_id,
                destination_worker["id"],
                payload.actor_type,
                payload.reason,
                json.dumps({"destination_worker": destination_worker["worker_key"], "load_score": float(destination_worker["load_score"])}),
            ),
        )
        op = cur.fetchone()
        conn.commit()

    plan = {
        "operation_id": str(op["id"]),
        "dvr_id": str(dvr_id),
        "source_session_id": str(source["id"]) if source else None,
        "destination_worker_id": str(destination_worker["id"]),
        "destination_worker_key": destination_worker["worker_key"],
        "steps": [
            "open destination P2P session on alternate Wine",
            "validate destination with real frames",
            "switch every camera route to destination session",
            "verify resulting active routes",
            "close source session",
            "release old leases",
            "commit operation and audit",
        ],
    }
    if not payload.execute:
        return {"mode": "PLAN", "plan": plan}

    destination_session = None
    switched_cameras: list[str] = []
    rollback_errors: list[str] = []
    try:
        with db() as conn, conn.cursor() as cur:
            set_operation(cur, op["id"], "OPENING_DESTINATION", "open_destination")
            conn.commit()

        opened = api(
            "POST",
            f"{P2P_SUPERVISOR_URL}/v1/p2p/dvrs/{dvr_id}/open",
            {
                "actor_type": payload.actor_type,
                "reason": f"failover:{op['id']}",
                "wine_worker_id": str(destination_worker["id"]),
                "allow_parallel": True,
            },
        )
        destination_session = opened["session"]
        probe_frames = int(opened["probe"]["frame_count"])

        with db() as conn, conn.cursor() as cur:
            cur.execute("UPDATE p2p_failover_operations SET destination_session_id=%s WHERE id=%s", (destination_session["id"], op["id"]))
            set_operation(cur, op["id"], "VALIDATING_DESTINATION", "destination_validated", details={"probe_frames": probe_frames})
            cur.execute("SELECT id, channel FROM cameras WHERE dvr_id=%s AND enabled=true ORDER BY channel", (dvr_id,))
            cameras = cur.fetchall()
            conn.commit()

        if not cameras:
            raise RuntimeError("DVR has no enabled cameras to switch")

        with db() as conn, conn.cursor() as cur:
            set_operation(cur, op["id"], "SWITCHING_ROUTES", "switch_routes", details={"camera_count": len(cameras)})
            conn.commit()

        for camera in cameras:
            channel = int(camera["channel"])
            local_uri = f"rtsp://127.0.0.1:{int(destination_session['rtsp_local_port'])}/cam/realmonitor?channel={channel}&subtype=0"
            api(
                "POST",
                f"{STREAM_BROKER_URL}/v1/routes/switch",
                {
                    "camera_id": str(camera["id"]),
                    "p2p_session_id": str(destination_session["id"]),
                    "local_uri": local_uri,
                    "probe_frames": probe_frames,
                    "reason": f"failover:{op['id']}",
                    "actor_type": payload.actor_type,
                },
            )
            switched_cameras.append(str(camera["id"]))

        with db() as conn, conn.cursor() as cur:
            set_operation(cur, op["id"], "VERIFYING", "verify_routes", details={"switched_cameras": switched_cameras})
            cur.execute(
                """
                SELECT count(*) AS total
                  FROM stream_routes r
                  JOIN cameras c ON c.id=r.camera_id
                 WHERE c.dvr_id=%s AND c.enabled=true
                   AND r.state='ACTIVE' AND r.deactivated_at IS NULL
                   AND r.p2p_session_id=%s
                """,
                (dvr_id, destination_session["id"]),
            )
            verified = int(cur.fetchone()["total"])
            cur.execute("SELECT count(*) AS total FROM cameras WHERE dvr_id=%s AND enabled=true", (dvr_id,))
            expected = int(cur.fetchone()["total"])
            if verified != expected:
                raise RuntimeError(f"route verification mismatch: {verified}/{expected}")
            conn.commit()

        if source:
            with db() as conn, conn.cursor() as cur:
                set_operation(cur, op["id"], "CLOSING_SOURCE", "close_source")
                conn.commit()
            api(
                "POST",
                f"{P2P_SUPERVISOR_URL}/v1/p2p/sessions/{source['id']}/close",
                {"actor_type": payload.actor_type, "reason": f"failover_committed:{op['id']}"},
            )

        with db() as conn, conn.cursor() as cur:
            set_operation(cur, op["id"], "COMMITTED", "complete", details={"verified_routes": verified}, completed=True)
            cur.execute(
                "INSERT INTO audit_logs(actor_type,action,object_type,object_id,metadata) VALUES (%s,'p2p.failover.committed','dvr',%s,%s::jsonb)",
                (payload.actor_type, str(dvr_id), json.dumps({"operation_id": str(op["id"]), "destination_session_id": str(destination_session["id"]), "routes": verified})),
            )
            conn.commit()
        return {"status": "COMMITTED", "plan": plan, "destination_session": destination_session, "routes": verified}

    except Exception as exc:
        with db() as conn, conn.cursor() as cur:
            set_operation(cur, op["id"], "ROLLING_BACK", "rollback", error=str(exc), details={"switched_cameras": switched_cameras})
            conn.commit()

        if destination_session:
            for camera_id in reversed(switched_cameras):
                try:
                    api(
                        "POST",
                        f"{STREAM_BROKER_URL}/v1/routes/rollback",
                        {
                            "camera_id": camera_id,
                            "expected_current_session_id": str(destination_session["id"]),
                            "reason": f"failover_rollback:{op['id']}",
                            "actor_type": "SYSTEM",
                        },
                    )
                except Exception as rollback_exc:
                    rollback_errors.append(f"route:{camera_id}:{rollback_exc}")
            try:
                api(
                    "POST",
                    f"{P2P_SUPERVISOR_URL}/v1/p2p/sessions/{destination_session['id']}/close",
                    {"actor_type": "SYSTEM", "reason": f"failover_rollback:{op['id']}"},
                )
            except Exception as rollback_exc:
                rollback_errors.append(f"destination_close:{rollback_exc}")

        final_state = "ROLLED_BACK" if not rollback_errors else "FAILED"
        with db() as conn, conn.cursor() as cur:
            set_operation(
                cur,
                op["id"],
                final_state,
                "rollback_complete" if not rollback_errors else "rollback_incomplete",
                error=str(exc),
                details={"rollback_errors": rollback_errors},
                completed=True,
            )
            cur.execute(
                "INSERT INTO audit_logs(actor_type,action,object_type,object_id,metadata) VALUES ('SYSTEM','p2p.failover.rollback','dvr',%s,%s::jsonb)",
                (str(dvr_id), json.dumps({"operation_id": str(op["id"]), "error": str(exc), "rollback_errors": rollback_errors})),
            )
            conn.commit()
        raise HTTPException(
            status_code=502,
            detail={
                "message": "failover rolled back" if not rollback_errors else "failover rollback incomplete; operator intervention required",
                "operation_id": str(op["id"]),
                "error": str(exc),
                "rollback_errors": rollback_errors,
            },
        ) from exc
