from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from minio import Minio
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import Camera, EventLog, Rule, User, create_schema, db_session

APP_NAME = os.getenv("APP_NAME", "INNOVATION VISION IA V12 LIGHT")
SECRET = os.environ["APP_SECRET_KEY"]
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))
ALGORITHM = "HS256"
EVENTS_FILE = Path("/config/events.yaml")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)
app = FastAPI(title=APP_NAME, docs_url="/api/docs", openapi_url="/api/openapi.json")


def get_db() -> Session:
    yield from db_session()


def minio_client() -> Minio:
    return Minio(
        os.getenv("MINIO_ENDPOINT", "minio:9000"),
        access_key=os.environ["MINIO_ACCESS_KEY"],
        secret_key=os.environ["MINIO_SECRET_KEY"],
        secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
    )


def event_catalog() -> dict[str, Any]:
    if not EVENTS_FILE.exists():
        return {"events": {}}
    return yaml.safe_load(EVENTS_FILE.read_text()) or {"events": {}}


def create_token(user: User) -> str:
    payload = {
        "sub": user.id,
        "email": user.email,
        "role": user.role,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, SECRET, algorithm=ALGORITHM)


def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
    try:
        payload = jwt.decode(credentials.credentials, SECRET, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
    except JWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc
    user = db.get(User, user_id)
    if user is None or not user.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "inactive user")
    return user


def admin_user(user: Annotated[User, Depends(current_user)]) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin required")
    return user


def authorized_log(db: Session, user: User, log_id: str) -> EventLog:
    log = db.get(EventLog, log_id)
    if log is None or not log.client_visible:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log not found")
    camera = db.get(Camera, log.camera_id)
    if camera is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "camera not found")
    if user.role != "admin" and camera.condo not in set(user.condo_scope or []):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log not found")
    return log


class LoginInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)


class UserInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    role: str = Field(pattern="^(admin|client)$")
    condo_scope: list[str] = []


class CameraInput(BaseModel):
    id: str
    condo: str
    name: str
    dvr_id: str
    gateway_id: str
    channel: int = Field(ge=1)
    enabled: bool = True
    config: dict[str, Any] = {}


class RuleInput(BaseModel):
    camera_id: str
    event_key: str
    enabled: bool = False
    state: str = Field(default="DRAFT", pattern="^(DRAFT|SHADOW|HOMOLOGATION|PRODUCTION|DISABLED)$")
    geometry: dict[str, Any] = {}
    config: dict[str, Any] = {}


@app.on_event("startup")
def startup() -> None:
    create_schema()
    bucket = os.getenv("MINIO_BUCKET", "vision-light")
    client = minio_client()
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    with next(db_session()) as db:
        admin_email = os.getenv("INITIAL_ADMIN_EMAIL")
        admin_password = os.getenv("INITIAL_ADMIN_PASSWORD")
        if admin_email and admin_password:
            existing = db.scalar(select(User).where(User.email == admin_email.lower()))
            if existing is None:
                db.add(User(email=admin_email.lower(), password_hash=pwd_context.hash(admin_password), role="admin"))
                db.commit()


@app.get("/health")
def health(db: Annotated[Session, Depends(get_db)]) -> dict[str, Any]:
    db.scalar(select(func.count()).select_from(User))
    return {"ok": True, "service": APP_NAME, "time": datetime.now(timezone.utc).isoformat()}


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/portal", status_code=302)


@app.post("/api/auth/token")
def login(data: LoginInput, db: Annotated[Session, Depends(get_db)]) -> dict[str, Any]:
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    if user is None or not user.active or not pwd_context.verify(data.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    return {"access_token": create_token(user), "token_type": "bearer", "role": user.role}


@app.get("/api/me")
def me(user: Annotated[User, Depends(current_user)]) -> dict[str, Any]:
    return {"id": user.id, "email": user.email, "role": user.role, "condo_scope": user.condo_scope}


@app.get("/api/events/catalog")
def events(_: Annotated[User, Depends(current_user)]) -> dict[str, Any]:
    return event_catalog()


@app.post("/api/admin/users")
def create_user(
    data: UserInput,
    _: Annotated[User, Depends(admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    if db.scalar(select(User).where(User.email == data.email.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "email already exists")
    user = User(
        email=data.email.lower(),
        password_hash=pwd_context.hash(data.password),
        role=data.role,
        condo_scope=data.condo_scope,
    )
    db.add(user)
    db.commit()
    return {"id": user.id, "email": user.email, "role": user.role, "condo_scope": user.condo_scope}


@app.get("/api/admin/cameras")
def list_cameras(
    _: Annotated[User, Depends(admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict[str, Any]]:
    rows = db.scalars(select(Camera).order_by(Camera.condo, Camera.dvr_id, Camera.channel)).all()
    return [
        {
            "id": row.id,
            "condo": row.condo,
            "name": row.name,
            "dvr_id": row.dvr_id,
            "gateway_id": row.gateway_id,
            "channel": row.channel,
            "enabled": row.enabled,
            "config": row.config,
        }
        for row in rows
    ]


@app.post("/api/admin/cameras")
def upsert_camera(
    data: CameraInput,
    _: Annotated[User, Depends(admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    camera = db.get(Camera, data.id) or Camera(id=data.id)
    for field, value in data.model_dump().items():
        setattr(camera, field, value)
    db.add(camera)
    db.commit()
    return data.model_dump()


@app.get("/api/admin/rules")
def list_rules(
    _: Annotated[User, Depends(admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict[str, Any]]:
    rows = db.scalars(select(Rule).order_by(Rule.camera_id, Rule.event_key)).all()
    return [
        {
            "id": row.id,
            "camera_id": row.camera_id,
            "event_key": row.event_key,
            "enabled": row.enabled,
            "state": row.state,
            "geometry": row.geometry,
            "config": row.config,
            "version": row.version,
        }
        for row in rows
    ]


@app.post("/api/admin/rules")
def save_rule(
    data: RuleInput,
    _: Annotated[User, Depends(admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    if db.get(Camera, data.camera_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "camera not found")
    if data.event_key not in event_catalog().get("events", {}):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown event_key")
    previous = db.scalar(
        select(Rule)
        .where(Rule.camera_id == data.camera_id, Rule.event_key == data.event_key)
        .order_by(Rule.version.desc())
    )
    version = 1 if previous is None else previous.version + 1
    rule = Rule(**data.model_dump(), version=version)
    db.add(rule)
    db.commit()
    return {"id": rule.id, "version": rule.version, **data.model_dump()}


@app.get("/api/admin/summary")
def summary(
    _: Annotated[User, Depends(admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    return {
        "cameras": db.scalar(select(func.count()).select_from(Camera)) or 0,
        "rules": db.scalar(select(func.count()).select_from(Rule).where(Rule.enabled.is_(True))) or 0,
        "visible_logs": db.scalar(select(func.count()).select_from(EventLog).where(EventLog.client_visible.is_(True))) or 0,
    }


@app.get("/api/client/logs")
def client_logs(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = 100,
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 500))
    query = (
        select(EventLog, Camera)
        .join(Camera, Camera.id == EventLog.camera_id)
        .where(EventLog.client_visible.is_(True))
        .order_by(EventLog.occurred_at.desc())
        .limit(limit)
    )
    if user.role != "admin":
        query = query.where(Camera.condo.in_(user.condo_scope or ["__none__"]))
    rows = db.execute(query).all()
    return [
        {
            "id": log.id,
            "event_key": log.event_key,
            "occurred_at": log.occurred_at.isoformat(),
            "confidence": log.confidence,
            "decision": log.decision,
            "camera_id": camera.id,
            "camera_name": camera.name,
            "condo": camera.condo,
            "has_snapshot": bool(log.snapshot_object),
            "has_clip": bool(log.clip_object),
            "clip_duration_seconds": log.clip_duration_seconds,
        }
        for log, camera in rows
    ]


@app.get("/api/logs/{log_id}/media/{kind}")
def media(
    log_id: str,
    kind: str,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> RedirectResponse:
    if kind not in {"snapshot", "clip"}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "media not found")
    log = authorized_log(db, user, log_id)
    object_name = log.snapshot_object if kind == "snapshot" else log.clip_object
    if not object_name:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "media not available")
    url = minio_client().presigned_get_object(
        os.getenv("MINIO_BUCKET", "vision-light"), object_name, expires=timedelta(minutes=5)
    )
    return RedirectResponse(url, status_code=307)


BASE_HTML = """
<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{title}</title><style>
:root{{--bg:#08111f;--panel:#101c2e;--line:#22334c;--text:#edf5ff;--muted:#8fa8c4;--accent:#30d5c8}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px system-ui}}header{{padding:18px 24px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between}}main{{padding:24px;max-width:1500px;margin:auto}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}}.card{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px}}input,button{{border:1px solid var(--line);border-radius:9px;padding:10px;background:#0b1728;color:var(--text)}}button{{cursor:pointer;background:var(--accent);color:#031515;font-weight:700}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid var(--line);text-align:left}}.muted{{color:var(--muted)}}a{{color:var(--accent)}}
</style></head><body><header><strong>INNOVATION VISION IA V12 LIGHT</strong><span>{surface}</span></header><main>{body}</main></body></html>
"""

LOGIN_BLOCK = """
<div class='card' style='max-width:420px;margin:8vh auto'><h2>Entrar</h2><input id='email' placeholder='E-mail' style='width:100%;margin-bottom:10px'><input id='password' type='password' placeholder='Senha' style='width:100%;margin-bottom:10px'><button onclick='login()' style='width:100%'>Entrar</button><pre id='error' class='muted'></pre></div>
<script>async function login(){const r=await fetch('/api/auth/token',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({email:email.value,password:password.value})});if(!r.ok){error.textContent=await r.text();return}const x=await r.json();localStorage.setItem('vision_token',x.access_token);location.reload()}</script>
"""


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin_page() -> str:
    body = LOGIN_BLOCK + """
<div id='app' style='display:none'><div class='grid'><div class='card'><h3>Câmeras</h3><b id='cameras'>-</b></div><div class='card'><h3>Regras ativas</h3><b id='rules'>-</b></div><div class='card'><h3>Logs visíveis</h3><b id='logs'>-</b></div></div><div class='card' style='margin-top:16px'><h3>Catálogo</h3><div id='catalog'></div></div></div>
<script>const t=localStorage.getItem('vision_token');if(t){document.querySelector('.card').style.display='none';app.style.display='block';Promise.all([fetch('/api/me',{headers:{Authorization:'Bearer '+t}}),fetch('/api/admin/summary',{headers:{Authorization:'Bearer '+t}}),fetch('/api/events/catalog',{headers:{Authorization:'Bearer '+t}})]).then(async rs=>{if(rs.some(r=>!r.ok))throw Error('Acesso administrativo negado');const [me,s,c]=await Promise.all(rs.map(r=>r.json()));if(me.role!=='admin')throw Error('Admin obrigatório');cameras.textContent=s.cameras;rules.textContent=s.rules;logs.textContent=s.visible_logs;catalog.innerHTML=Object.entries(c.events||{}).map(([k,v])=>`<div><b>${v.title}</b> <span class='muted'>${k} · ${v.mode}</span></div>`).join('')}).catch(e=>{localStorage.removeItem('vision_token');location.reload()})}</script>
"""
    return BASE_HTML.format(title="Admin", surface="Administração", body=body)


@app.get("/portal", response_class=HTMLResponse, include_in_schema=False)
def portal_page() -> str:
    body = LOGIN_BLOCK + """
<div id='app' style='display:none'><div class='card'><h2>Logs validados</h2><table><thead><tr><th>Data</th><th>Condomínio</th><th>Câmera</th><th>Evento</th><th>Conf.</th><th>Mídia</th></tr></thead><tbody id='rows'></tbody></table></div></div>
<script>const t=localStorage.getItem('vision_token');if(t){document.querySelector('.card').style.display='none';app.style.display='block';fetch('/api/client/logs?limit=200',{headers:{Authorization:'Bearer '+t}}).then(async r=>{if(!r.ok)throw Error();return r.json()}).then(data=>{rows.innerHTML=data.map(x=>`<tr><td>${new Date(x.occurred_at).toLocaleString('pt-BR')}</td><td>${x.condo}</td><td>${x.camera_name}</td><td>${x.event_key}</td><td>${(x.confidence*100).toFixed(1)}%</td><td>${x.has_snapshot?`<a href='/api/logs/${x.id}/media/snapshot' onclick='return media(event,this)'>Snapshot</a>`:''} ${x.has_clip?`<a href='/api/logs/${x.id}/media/clip' onclick='return media(event,this)'>Clipe 15s</a>`:''}</td></tr>`).join('')}).catch(()=>{localStorage.removeItem('vision_token');location.reload()})}function media(e,a){e.preventDefault();fetch(a.href,{headers:{Authorization:'Bearer '+t}}).then(r=>{if(!r.ok)throw Error();return r.blob()}).then(b=>window.open(URL.createObjectURL(b),'_blank'));return false}</script>
"""
    return BASE_HTML.format(title="Portal do Cliente", surface="Portal do Cliente", body=body)
