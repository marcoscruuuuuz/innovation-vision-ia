import os
from datetime import datetime, timedelta, timezone
from uuid import UUID

import psycopg
from fastapi import FastAPI, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://vision:vision@postgres:5432/vision")
MIN_PROBE_FRAMES = int(os.getenv("STREAMBROKER_MIN_PROBE_FRAMES", "3"))
FAILURE_THRESHOLD = int(os.getenv("STREAMBROKER_FAILURE_THRESHOLD", "3"))
RECOVERY_THRESHOLD = int(os.getenv("STREAMBROKER_RECOVERY_THRESHOLD", "3"))
FAILOVER_COOLDOWN_SECONDS = int(os.getenv("STREAMBROKER_FAILOVER_COOLDOWN_SECONDS", "120"))

app = FastAPI(title="INNOVATION VISION StreamBroker", version="0.2.0")


def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


class RouteSwitchRequest(BaseModel):
    camera_id: UUID
    p2p_session_id: UUID
    local_uri: str = Field(pattern=r"^rtsp://127\.0\.0\.1:[0-9]{1,5}(/.*)?$")
    probe_frames: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=255)
    actor_type: str = Field(default="SYSTEM", pattern=r"^(USER|AI|WATCHDOG|SYSTEM)$")


class RouteRollbackRequest(BaseModel):
    camera_id: UUID
    expected_current_session_id: UUID
    reason: str = Field(min_length=1, max_length=255)
    actor_type: str = Field(default="SYSTEM", pattern=r"^(USER|AI|WATCHDOG|SYSTEM)$")


class HealthObservation(BaseModel):
    dvr_id: UUID
    healthy: bool
    reason: str = Field(min_length=1, max_length=255)


@app.get("/health")
def health():
    try:
        with db() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc


@app.get("/v1/routes")
def list_routes(camera_id: UUID | None = None):
    with db() as conn, conn.cursor() as cur:
        if camera_id:
            cur.execute("SELECT * FROM stream_routes WHERE camera_id=%s ORDER BY created_at DESC", (camera_id,))
        else:
            cur.execute("SELECT * FROM stream_routes ORDER BY created_at DESC LIMIT 500")
        return cur.fetchall()


@app.post("/v1/routes/switch")
def switch_route(payload: RouteSwitchRequest):
    if payload.probe_frames < MIN_PROBE_FRAMES:
        raise HTTPException(status_code=409, detail=f"at least {MIN_PROBE_FRAMES} validated frames are required")
    with db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT s.*,c.dvr_id FROM p2p_sessions s JOIN cameras c ON c.dvr_id=s.dvr_id WHERE s.id=%s AND c.id=%s FOR UPDATE""", (payload.p2p_session_id,payload.camera_id))
        session=cur.fetchone()
        if not session: raise HTTPException(status_code=404, detail="session/camera pair not found")
        if session["state"]!="ACTIVE" or session["ended_at"] is not None: raise HTTPException(status_code=409, detail="destination P2P session is not ACTIVE")
        if not session.get("last_frame_at"): raise HTTPException(status_code=409, detail="destination session has no validated frame timestamp")
        cur.execute("SELECT * FROM stream_routes WHERE camera_id=%s AND state='ACTIVE' AND deactivated_at IS NULL FOR UPDATE",(payload.camera_id,))
        current=cur.fetchone(); next_generation=int(current["generation"])+1 if current else 1
        cur.execute("INSERT INTO stream_route_switches(camera_id,from_route_id,reason,actor_type,status,probe_frames) VALUES (%s,%s,%s,%s,'VALIDATING',%s) RETURNING id",(payload.camera_id,current["id"] if current else None,payload.reason,payload.actor_type,payload.probe_frames)); switch_id=cur.fetchone()["id"]
        cur.execute("INSERT INTO stream_routes(camera_id,p2p_session_id,source_type,local_uri,state,generation,last_frame_at) VALUES (%s,%s,'P2P_RTSP',%s,'ACTIVE',%s,%s) RETURNING *",(payload.camera_id,payload.p2p_session_id,payload.local_uri,next_generation,session["last_frame_at"])); new_route=cur.fetchone()
        if current: cur.execute("UPDATE stream_routes SET state='INACTIVE',deactivated_at=now() WHERE id=%s",(current["id"],))
        cur.execute("UPDATE stream_route_switches SET to_route_id=%s,status='COMMITTED',completed_at=now() WHERE id=%s",(new_route["id"],switch_id))
        cur.execute("INSERT INTO audit_logs(actor_type,action,object_type,object_id,metadata) VALUES (%s,'stream.route.switch','camera',%s,jsonb_build_object('route_id',%s,'generation',%s))",(payload.actor_type,str(payload.camera_id),str(new_route["id"]),next_generation))
        conn.commit(); return {"status":"COMMITTED","route":new_route,"switch_id":switch_id}


@app.post("/v1/routes/rollback")
def rollback_route(payload: RouteRollbackRequest):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM stream_routes WHERE camera_id=%s AND state='ACTIVE' AND deactivated_at IS NULL FOR UPDATE",(payload.camera_id,)); current=cur.fetchone()
        if not current: raise HTTPException(status_code=404, detail="camera has no active route")
        if current["p2p_session_id"]!=payload.expected_current_session_id: raise HTTPException(status_code=409, detail="active route changed since failover; rollback refused")
        cur.execute("SELECT * FROM stream_routes WHERE camera_id=%s AND id<>%s AND state='INACTIVE' ORDER BY generation DESC LIMIT 1 FOR UPDATE",(payload.camera_id,current["id"])); previous=cur.fetchone()
        if not previous: raise HTTPException(status_code=409, detail="no previous route available for rollback")
        cur.execute("UPDATE stream_routes SET state='INACTIVE',deactivated_at=now() WHERE id=%s",(current["id"],))
        cur.execute("UPDATE stream_routes SET state='ACTIVE',deactivated_at=NULL WHERE id=%s RETURNING *",(previous["id"],)); restored=cur.fetchone()
        cur.execute("INSERT INTO stream_route_switches(camera_id,from_route_id,to_route_id,reason,actor_type,status,probe_frames,completed_at) VALUES (%s,%s,%s,%s,%s,'ROLLED_BACK',0,now()) RETURNING id",(payload.camera_id,current["id"],previous["id"],payload.reason,payload.actor_type)); switch_id=cur.fetchone()["id"]
        cur.execute("INSERT INTO audit_logs(actor_type,action,object_type,object_id,metadata) VALUES (%s,'stream.route.rollback','camera',%s,jsonb_build_object('from_route_id',%s,'to_route_id',%s))",(payload.actor_type,str(payload.camera_id),str(current["id"]),str(previous["id"])))
        conn.commit(); return {"status":"ROLLED_BACK","route":restored,"switch_id":switch_id}


@app.post("/v1/failover/observe")
def observe_health(payload: HealthObservation):
    now=datetime.now(timezone.utc)
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM p2p_failover_state WHERE dvr_id=%s FOR UPDATE",(payload.dvr_id,)); state=cur.fetchone()
        if not state:
            cur.execute("INSERT INTO p2p_failover_state(dvr_id) VALUES (%s) RETURNING *",(payload.dvr_id,)); state=cur.fetchone()
        failure_streak=0 if payload.healthy else int(state["failure_streak"])+1
        recovery_streak=int(state["recovery_streak"])+1 if payload.healthy else 0
        cooldown_until=state["cooldown_until"]
        trigger_failover=not payload.healthy and failure_streak>=FAILURE_THRESHOLD and (cooldown_until is None or cooldown_until<=now)
        if trigger_failover: cooldown_until=now+timedelta(seconds=FAILOVER_COOLDOWN_SECONDS)
        cur.execute("""UPDATE p2p_failover_state SET failure_streak=%s,recovery_streak=%s,cooldown_until=%s,last_reason=%s,updated_at=now(),last_failover_at=CASE WHEN %s THEN now() ELSE last_failover_at END WHERE dvr_id=%s RETURNING *""",(failure_streak,recovery_streak,cooldown_until,payload.reason,trigger_failover,payload.dvr_id)); updated=cur.fetchone(); conn.commit()
        return {"state":updated,"trigger_failover":trigger_failover,"recovered":payload.healthy and recovery_streak>=RECOVERY_THRESHOLD,"policy":{"failure_threshold":FAILURE_THRESHOLD,"recovery_threshold":RECOVERY_THRESHOLD,"cooldown_seconds":FAILOVER_COOLDOWN_SECONDS}}
