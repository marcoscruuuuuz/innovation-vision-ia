from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .platform import pool, require_admin

router = APIRouter()


class CondominiumPayload(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    active: bool = True


class DVRPayload(BaseModel):
    condominium_id: UUID
    name: str = Field(min_length=1, max_length=160)
    model: str | None = None
    connection_mode: str = "intelbras_p2p"
    serial_secret_ref: str | None = None
    username_secret_ref: str | None = None
    password_secret_ref: str | None = None
    ip_lan: str | None = None
    ip_wan: str | None = None
    rtsp_tcp_port: int | None = None
    rtsp_udp_port: int | None = None
    tcp_p2p_port: int | None = None
    channel_count: int | None = None
    ddns_host: str | None = None
    mac: str | None = None
    ddns_lan_ip: str | None = None
    ddns_wan_ip: str | None = None
    notes: str | None = None
    enabled: bool = True


class CameraPayload(BaseModel):
    condominium_id: UUID
    dvr_id: UUID
    channel: int = Field(ge=1, le=512)
    name: str = Field(min_length=1, max_length=160)
    enabled: bool = True


@router.get("/api/v1/admin/condominiums")
def list_condominiums(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM condominiums ORDER BY name")
        return cur.fetchall()


@router.post("/api/v1/admin/condominiums", status_code=201)
def create_condominium(payload: CondominiumPayload, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                "INSERT INTO condominiums(code,name,active) VALUES (%s,%s,%s) RETURNING *",
                (payload.code.strip(), payload.name.strip(), payload.active),
            )
            row = cur.fetchone(); conn.commit(); return row
        except Exception as exc:
            if "duplicate key" in str(exc).lower():
                raise HTTPException(status_code=409, detail="condominium code already exists") from exc
            raise


@router.put("/api/v1/admin/condominiums/{condominium_id}")
def update_condominium(condominium_id: UUID, payload: CondominiumPayload, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE condominiums SET code=%s,name=%s,active=%s,updated_at=now() WHERE id=%s RETURNING *",
            (payload.code.strip(), payload.name.strip(), payload.active, condominium_id),
        )
        row=cur.fetchone()
        if not row: raise HTTPException(status_code=404, detail="condominium not found")
        conn.commit(); return row


@router.get("/api/v1/admin/dvrs")
def list_dvrs(condominium_id: UUID | None = None, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        if condominium_id:
            cur.execute("SELECT * FROM dvrs WHERE condominium_id=%s ORDER BY name", (condominium_id,))
        else:
            cur.execute("SELECT * FROM dvrs ORDER BY condominium_id,name")
        return cur.fetchall()


@router.post("/api/v1/admin/dvrs", status_code=201)
def create_dvr(payload: DVRPayload, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM condominiums WHERE id=%s", (payload.condominium_id,))
        if not cur.fetchone(): raise HTTPException(status_code=404, detail="condominium not found")
        cur.execute(
            """
            INSERT INTO dvrs(condominium_id,name,model,connection_mode,serial_secret_ref,username_secret_ref,password_secret_ref,
              ip_lan,ip_wan,rtsp_tcp_port,rtsp_udp_port,tcp_p2p_port,channel_count,ddns_host,mac,ddns_lan_ip,ddns_wan_ip,notes,enabled)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
            """,
            (payload.condominium_id,payload.name.strip(),payload.model,payload.connection_mode,payload.serial_secret_ref,
             payload.username_secret_ref,payload.password_secret_ref,payload.ip_lan,payload.ip_wan,payload.rtsp_tcp_port,
             payload.rtsp_udp_port,payload.tcp_p2p_port,payload.channel_count,payload.ddns_host,payload.mac,payload.ddns_lan_ip,
             payload.ddns_wan_ip,payload.notes,payload.enabled),
        )
        row=cur.fetchone(); conn.commit(); return row


@router.get("/api/v1/admin/cameras")
def list_cameras(condominium_id: UUID | None = None, dvr_id: UUID | None = None, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    clauses=[]; params=[]
    if condominium_id: clauses.append("c.condominium_id=%s"); params.append(condominium_id)
    if dvr_id: clauses.append("c.dvr_id=%s"); params.append(dvr_id)
    where=(" WHERE "+" AND ".join(clauses)) if clauses else ""
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT c.*,d.name AS dvr_name,co.name AS condominium_name FROM cameras c JOIN dvrs d ON d.id=c.dvr_id JOIN condominiums co ON co.id=c.condominium_id{where} ORDER BY co.name,d.name,c.channel", tuple(params))
        return cur.fetchall()


@router.post("/api/v1/admin/cameras", status_code=201)
def create_camera(payload: CameraPayload, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT condominium_id FROM dvrs WHERE id=%s", (payload.dvr_id,))
        dvr=cur.fetchone()
        if not dvr: raise HTTPException(status_code=404, detail="dvr not found")
        if dvr["condominium_id"] != payload.condominium_id: raise HTTPException(status_code=409, detail="camera condominium does not match DVR")
        cur.execute("INSERT INTO cameras(condominium_id,dvr_id,channel,name,enabled) VALUES (%s,%s,%s,%s,%s) RETURNING *", (payload.condominium_id,payload.dvr_id,payload.channel,payload.name.strip(),payload.enabled))
        row=cur.fetchone(); conn.commit(); return row
