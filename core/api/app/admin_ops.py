from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .platform import pool, require_admin

router = APIRouter()


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    role: str = Field(pattern=r"^(admin|operator|reviewer|client)$")


class IngestionSourceCreate(BaseModel):
    dvr_id: UUID
    source_key: str = Field(min_length=1, max_length=160)
    hmac_secret_ref: str = Field(min_length=1, max_length=160)
    allowed_clock_skew_seconds: int = Field(default=300, ge=30, le=3600)
    enabled: bool = True


class AlertPolicyCreate(BaseModel):
    condominium_id: UUID
    event_type: str = Field(min_length=1, max_length=120)
    channel: str = Field(pattern=r"^(WHATSAPP|WEBHOOK|EMAIL|INTERNAL)$")
    recipient_ref: str = Field(min_length=1, max_length=255)
    provider: str | None = Field(default=None, max_length=160)
    enabled: bool = True


@router.get("/api/v1/admin/users")
def list_users(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id,username,role,active,created_at FROM users ORDER BY username")
        return cur.fetchall()


@router.post("/api/v1/admin/users", status_code=201)
def create_user(payload: UserCreate, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO users(username,password_hash,role) VALUES (%s,'!token-only',%s) RETURNING id,username,role,active,created_at",
                (payload.username.strip(), payload.role),
            )
            row = cur.fetchone(); conn.commit(); return row
        except Exception as exc:
            if "duplicate key" in str(exc).lower():
                raise HTTPException(status_code=409, detail="username already exists") from exc
            raise


@router.put("/api/v1/admin/users/{user_id}/condominiums/{condominium_id}")
def bind_user_condominium(user_id: UUID, condominium_id: UUID, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE id=%s", (user_id,))
        if not cur.fetchone(): raise HTTPException(status_code=404, detail="user not found")
        cur.execute("SELECT id FROM condominiums WHERE id=%s", (condominium_id,))
        if not cur.fetchone(): raise HTTPException(status_code=404, detail="condominium not found")
        cur.execute("INSERT INTO user_condominiums(user_id,condominium_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (user_id, condominium_id))
        conn.commit(); return {"status": "bound"}


@router.delete("/api/v1/admin/users/{user_id}/condominiums/{condominium_id}")
def unbind_user_condominium(user_id: UUID, condominium_id: UUID, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM user_condominiums WHERE user_id=%s AND condominium_id=%s", (user_id, condominium_id))
        conn.commit(); return {"status": "unbound"}


@router.get("/api/v1/admin/ingestion-sources")
def list_ingestion_sources(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT s.id,s.dvr_id,s.source_key,s.hmac_secret_ref,s.enabled,s.allowed_clock_skew_seconds,s.created_at,s.updated_at,d.name AS dvr_name
                 FROM ingestion_sources s JOIN dvrs d ON d.id=s.dvr_id ORDER BY d.name"""
        )
        return cur.fetchall()


@router.post("/api/v1/admin/ingestion-sources", status_code=201)
def save_ingestion_source(payload: IngestionSourceCreate, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM dvrs WHERE id=%s", (payload.dvr_id,))
        if not cur.fetchone(): raise HTTPException(status_code=404, detail="dvr not found")
        cur.execute(
            """
            INSERT INTO ingestion_sources(dvr_id,source_key,hmac_secret_ref,enabled,allowed_clock_skew_seconds)
            VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT(dvr_id) DO UPDATE SET source_key=excluded.source_key,hmac_secret_ref=excluded.hmac_secret_ref,
              enabled=excluded.enabled,allowed_clock_skew_seconds=excluded.allowed_clock_skew_seconds,updated_at=now()
            RETURNING *
            """,
            (payload.dvr_id,payload.source_key,payload.hmac_secret_ref,payload.enabled,payload.allowed_clock_skew_seconds),
        )
        row = cur.fetchone(); conn.commit(); return row


@router.get("/api/v1/admin/alert-policies")
def list_alert_policies(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM alert_policies ORDER BY condominium_id,event_type,channel,recipient_ref")
        return cur.fetchall()


@router.post("/api/v1/admin/alert-policies", status_code=201)
def save_alert_policy(payload: AlertPolicyCreate, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM condominiums WHERE id=%s", (payload.condominium_id,))
        if not cur.fetchone(): raise HTTPException(status_code=404, detail="condominium not found")
        cur.execute(
            """
            INSERT INTO alert_policies(condominium_id,event_type,channel,recipient_ref,provider,enabled)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT(condominium_id,event_type,channel,recipient_ref) DO UPDATE SET provider=excluded.provider,enabled=excluded.enabled,updated_at=now()
            RETURNING *
            """,
            (payload.condominium_id,payload.event_type,payload.channel,payload.recipient_ref,payload.provider,payload.enabled),
        )
        row = cur.fetchone(); conn.commit(); return row


@router.delete("/api/v1/admin/alert-policies/{policy_id}")
def delete_alert_policy(policy_id: UUID, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM alert_policies WHERE id=%s RETURNING id", (policy_id,))
        if not cur.fetchone(): raise HTTPException(status_code=404, detail="policy not found")
        conn.commit(); return {"status": "deleted"}
