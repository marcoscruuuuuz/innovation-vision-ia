from __future__ import annotations

import base64
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

import redis
import yaml
from fastapi import Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from main import (
    Camera,
    EventLog,
    Rule,
    User,
    admin_user,
    app,
    current_user,
    get_db,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
REGISTRY_FILE = Path(os.getenv("GATEWAY_REGISTRY_FILE", "/config/gateways.yaml"))
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
PREFIX = os.getenv("REDIS_PREFIX", "vl:")
ONLINE_MAX_AGE_S = float(os.getenv("CAMERA_ONLINE_MAX_AGE_S", "20"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://visioniav12.innovationrptelecom.com.br")

# Replace only the initial scaffold pages. Existing API routes remain intact.
app.router.routes[:] = [
    route
    for route in app.router.routes
    if getattr(route, "path", None) not in {"/admin", "/portal"}
]

if STATIC_DIR.exists() and not any(getattr(route, "path", None) == "/assets" for route in app.router.routes):
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR)), name="assets")


def redis_text() -> redis.Redis:
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def camera_runtime(camera: Camera, client: redis.Redis | None = None) -> dict[str, Any]:
    own_client = client is None
    client = client or redis_text()
    key = f"{PREFIX}camera:{camera.id}:health"
    data = client.hgetall(key)
    ttl = client.ttl(key)
    last_frame_at = parse_float(data.get("last_frame_at"))
    updated_at = parse_float(data.get("updated_at"))
    failures = int(parse_float(data.get("failures")))
    age = time.time() - last_frame_at if last_frame_at else None
    online = bool(last_frame_at and age is not None and age <= ONLINE_MAX_AGE_S)
    if online and failures == 0:
        runtime_state = "ONLINE"
    elif last_frame_at and ttl > 0:
        runtime_state = "DEGRADED"
    else:
        runtime_state = "OFFLINE"
    result = {
        "online": online,
        "runtime_state": runtime_state,
        "last_frame_at": last_frame_at or None,
        "last_frame_age_s": round(age, 3) if age is not None else None,
        "jpeg_bytes": int(parse_float(data.get("jpeg_bytes"))),
        "failures": failures,
        "last_error": data.get("last_error"),
        "gateway_id_runtime": data.get("gateway_id"),
        "health_ttl_s": ttl,
        "updated_at": updated_at or None,
    }
    if own_client:
        client.close()
    return result


def camera_payload(camera: Camera, runtime: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": camera.id,
        "condo": camera.condo,
        "name": camera.name,
        "dvr_id": camera.dvr_id,
        "gateway_id": camera.gateway_id,
        "channel": camera.channel,
        "enabled": camera.enabled,
        "config": camera.config or {},
        **(runtime or {}),
    }


class UserStatusInput(BaseModel):
    active: bool | None = None
    condo_scope: list[str] | None = None
    role: str | None = Field(default=None, pattern="^(admin|client)$")


class RuleStateInput(BaseModel):
    enabled: bool
    state: str = Field(pattern="^(DRAFT|SHADOW|HOMOLOGATION|PRODUCTION|DISABLED)$")


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin_v3() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "admin.html").read_text(encoding="utf-8"))


@app.get("/portal", response_class=HTMLResponse, include_in_schema=False)
def portal_v3() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "portal.html").read_text(encoding="utf-8"))


@app.get("/api/admin/overview")
def admin_overview(
    _: Annotated[User, Depends(admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    client = redis_text()
    cameras = db.scalars(select(Camera).order_by(Camera.condo, Camera.dvr_id, Camera.channel)).all()
    runtime_rows = [camera_runtime(camera, client) for camera in cameras]
    states = {"ONLINE": 0, "DEGRADED": 0, "OFFLINE": 0}
    for runtime in runtime_rows:
        states[runtime["runtime_state"]] += 1

    rule_rows = db.execute(select(Rule.state, func.count(Rule.id)).group_by(Rule.state)).all()
    rules_by_state = {state_name: count for state_name, count in rule_rows}

    since = now_utc() - timedelta(hours=24)
    logs_24h = db.scalar(select(func.count(EventLog.id)).where(EventLog.created_at >= since)) or 0
    visible_24h = db.scalar(
        select(func.count(EventLog.id)).where(
            EventLog.created_at >= since,
            EventLog.client_visible.is_(True),
        )
    ) or 0

    services: list[dict[str, Any]] = []
    for raw_key in client.scan_iter(match=f"{PREFIX}health:*", count=100):
        key = str(raw_key)
        values = client.hgetall(key)
        timestamp = parse_float(values.get("timestamp") or values.get("started_at"))
        services.append(
            {
                "key": key,
                "name": key.removeprefix(f"{PREFIX}health:"),
                "state": values.get("state", "RUNNING"),
                "timestamp": timestamp or None,
                "age_s": round(time.time() - timestamp, 2) if timestamp else None,
                "data": values,
            }
        )
    client.close()

    return {
        "generated_at": now_utc().isoformat(),
        "public_base_url": PUBLIC_BASE_URL,
        "cameras": {
            "total": len(cameras),
            "enabled": sum(1 for camera in cameras if camera.enabled),
            "online": states["ONLINE"],
            "degraded": states["DEGRADED"],
            "offline": states["OFFLINE"],
        },
        "rules": {
            "total": sum(rules_by_state.values()),
            "by_state": rules_by_state,
            "enabled": db.scalar(select(func.count(Rule.id)).where(Rule.enabled.is_(True))) or 0,
        },
        "logs": {
            "last_24h": logs_24h,
            "visible_last_24h": visible_24h,
            "latest_at": db.scalar(select(func.max(EventLog.created_at))),
        },
        "services": sorted(services, key=lambda item: item["name"]),
    }


@app.get("/api/admin/cameras/status")
def cameras_status(
    _: Annotated[User, Depends(admin_user)],
    db: Annotated[Session, Depends(get_db)],
    condo: str | None = None,
    state_filter: str | None = Query(default=None, alias="state"),
) -> list[dict[str, Any]]:
    query = select(Camera).order_by(Camera.condo, Camera.dvr_id, Camera.channel)
    if condo:
        query = query.where(Camera.condo == condo)
    rows = db.scalars(query).all()
    client = redis_text()
    output = []
    for camera in rows:
        runtime = camera_runtime(camera, client)
        if state_filter and runtime["runtime_state"] != state_filter.upper():
            continue
        output.append(camera_payload(camera, runtime))
    client.close()
    return output


@app.get("/api/admin/cameras/{camera_id}/latest-snapshot")
def latest_snapshot(
    camera_id: str,
    _: Annotated[User, Depends(admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    if db.get(Camera, camera_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "camera not found")
    client = redis_text()
    encoded = client.get(f"{PREFIX}camera:{camera_id}:latest_jpeg_b64")
    client.close()
    if not encoded:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "latest frame not available")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "invalid latest frame") from exc
    return Response(
        raw,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.post("/api/admin/cameras/sync-registry")
def sync_registry(
    _: Annotated[User, Depends(admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    if not REGISTRY_FILE.exists():
        raise HTTPException(status.HTTP_409_CONFLICT, "gateway registry not found")
    data = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8")) or {}
    rows = data.get("cameras") or []
    synced = 0
    for item in rows:
        camera_id = str(item["camera_id"])
        camera = db.get(Camera, camera_id) or Camera(id=camera_id)
        camera.condo = str(item.get("condo") or item.get("dvr_id") or "UNASSIGNED")
        camera.name = str(item.get("name") or camera_id)
        camera.dvr_id = str(item["dvr_id"])
        camera.gateway_id = str(item["gateway_id"])
        camera.channel = int(item["channel"])
        camera.enabled = bool(item.get("enabled", True))
        camera.config = {
            key: value
            for key, value in item.items()
            if key not in {"snapshot_request", "playback_request", "auth_token"}
        }
        db.add(camera)
        synced += 1
    db.commit()
    return {"synced": synced, "source": str(REGISTRY_FILE)}


@app.get("/api/admin/users")
def list_users(
    _: Annotated[User, Depends(admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict[str, Any]]:
    rows = db.scalars(select(User).order_by(User.created_at.desc())).all()
    return [
        {
            "id": row.id,
            "email": row.email,
            "role": row.role,
            "active": row.active,
            "condo_scope": row.condo_scope or [],
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@app.patch("/api/admin/users/{user_id}")
def update_user(
    user_id: str,
    data: UserStatusInput,
    actor: Annotated[User, Depends(admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    if user.id == actor.id and data.active is False:
        raise HTTPException(status.HTTP_409_CONFLICT, "cannot disable the current administrator")
    if data.active is not None:
        user.active = data.active
    if data.condo_scope is not None:
        user.condo_scope = sorted(set(data.condo_scope))
    if data.role is not None:
        user.role = data.role
    db.add(user)
    db.commit()
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "active": user.active,
        "condo_scope": user.condo_scope or [],
    }


@app.get("/api/admin/rules/by-camera/{camera_id}")
def rules_by_camera(
    camera_id: str,
    _: Annotated[User, Depends(admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(Rule).where(Rule.camera_id == camera_id).order_by(Rule.event_key, Rule.version.desc())
    ).all()
    return [
        {
            "id": row.id,
            "camera_id": row.camera_id,
            "event_key": row.event_key,
            "enabled": row.enabled,
            "state": row.state,
            "geometry": row.geometry or {},
            "config": row.config or {},
            "version": row.version,
            "updated_at": row.updated_at.isoformat(),
        }
        for row in rows
    ]


@app.patch("/api/admin/rules/{rule_id}/state")
def update_rule_state(
    rule_id: str,
    data: RuleStateInput,
    _: Annotated[User, Depends(admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    source = db.get(Rule, rule_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")
    latest_version = db.scalar(
        select(func.max(Rule.version)).where(
            Rule.camera_id == source.camera_id,
            Rule.event_key == source.event_key,
        )
    ) or 0
    clone = Rule(
        camera_id=source.camera_id,
        event_key=source.event_key,
        enabled=data.enabled,
        state=data.state,
        geometry=source.geometry or {},
        config=source.config or {},
        version=int(latest_version) + 1,
    )
    db.add(clone)
    db.commit()
    return {
        "id": clone.id,
        "camera_id": clone.camera_id,
        "event_key": clone.event_key,
        "enabled": clone.enabled,
        "state": clone.state,
        "version": clone.version,
    }


@app.get("/api/admin/logs")
def admin_logs(
    _: Annotated[User, Depends(admin_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = 200,
    condo: str | None = None,
    event_key: str | None = None,
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 1000))
    query = (
        select(EventLog, Camera)
        .join(Camera, Camera.id == EventLog.camera_id)
        .order_by(EventLog.occurred_at.desc())
        .limit(limit)
    )
    if condo:
        query = query.where(Camera.condo == condo)
    if event_key:
        query = query.where(EventLog.event_key == event_key)
    rows = db.execute(query).all()
    return [
        {
            "id": log.id,
            "event_key": log.event_key,
            "occurred_at": log.occurred_at.isoformat(),
            "created_at": log.created_at.isoformat(),
            "confidence": log.confidence,
            "decision": log.decision,
            "client_visible": log.client_visible,
            "camera_id": camera.id,
            "camera_name": camera.name,
            "condo": camera.condo,
            "dvr_id": camera.dvr_id,
            "channel": camera.channel,
            "has_snapshot": bool(log.snapshot_object),
            "has_clip": bool(log.clip_object),
            "clip_duration_seconds": log.clip_duration_seconds,
        }
        for log, camera in rows
    ]


@app.get("/api/client/summary")
def client_summary(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    camera_query = select(Camera)
    if user.role != "admin":
        camera_query = camera_query.where(Camera.condo.in_(user.condo_scope or ["__none__"]))
    cameras = db.scalars(camera_query.order_by(Camera.condo, Camera.channel)).all()
    client = redis_text()
    online = 0
    degraded = 0
    offline = 0
    for camera in cameras:
        state_name = camera_runtime(camera, client)["runtime_state"]
        if state_name == "ONLINE":
            online += 1
        elif state_name == "DEGRADED":
            degraded += 1
        else:
            offline += 1
    client.close()

    log_query = select(func.count(EventLog.id)).join(Camera, Camera.id == EventLog.camera_id).where(
        EventLog.client_visible.is_(True)
    )
    if user.role != "admin":
        log_query = log_query.where(Camera.condo.in_(user.condo_scope or ["__none__"]))
    return {
        "condo_scope": user.condo_scope or [],
        "cameras": {"total": len(cameras), "online": online, "degraded": degraded, "offline": offline},
        "visible_logs": db.scalar(log_query) or 0,
        "generated_at": now_utc().isoformat(),
    }


@app.get("/api/client/cameras")
def client_cameras(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict[str, Any]]:
    query = select(Camera).where(Camera.enabled.is_(True))
    if user.role != "admin":
        query = query.where(Camera.condo.in_(user.condo_scope or ["__none__"]))
    rows = db.scalars(query.order_by(Camera.condo, Camera.dvr_id, Camera.channel)).all()
    client = redis_text()
    payload = [camera_payload(row, camera_runtime(row, client)) for row in rows]
    client.close()
    return payload
