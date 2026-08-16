from __future__ import annotations

import hashlib
import os
import secrets
from datetime import date, datetime, time, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://vision:vision@postgres:5432/vision")
pool = ConnectionPool(conninfo=DATABASE_URL, min_size=1, max_size=10, kwargs={"row_factory": dict_row})
router = APIRouter()


class RuleConfig(BaseModel):
    camera_id: UUID
    event_type: str = Field(min_length=1, max_length=120)
    display_label: str | None = Field(default=None, max_length=160)
    enabled: bool = False
    geometry: dict | None = None
    parameters: dict = Field(default_factory=dict)
    model_requirements: dict = Field(default_factory=dict)
    certification_status: Literal[
        "DRAFT", "CONFIGURED", "SHADOW", "HOMOLOGATION", "AI_REVIEW", "CERTIFIED", "PRODUCTION", "REJECTED", "ADJUSTMENT_REQUIRED"
    ] = "DRAFT"


class ConfidencePolicy(BaseModel):
    min_log_confidence: float = Field(ge=0, le=1)
    review_from_confidence: float = Field(ge=0, le=1)
    evidence_from_confidence: float = Field(ge=0, le=1)


class TokenIssue(BaseModel):
    user_id: UUID
    label: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=lambda: ["client:read"])
    expires_at: datetime | None = None


class ReviewDecision(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    notes: str | None = Field(default=None, max_length=2000)


class IsabelQuery(BaseModel):
    intent: Literal["count_logs", "search_logs"]
    event_type: str | None = None
    camera_id: UUID | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    limit: int = Field(default=50, ge=1, le=200)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_principal(authorization: str | None):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    token = authorization.split(" ", 1)[1].strip()
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.id AS token_id,t.user_id,t.scopes,u.role,u.active
              FROM user_api_tokens t JOIN users u ON u.id=t.user_id
             WHERE t.token_hash=%s AND t.active=true AND u.active=true
               AND (t.expires_at IS NULL OR t.expires_at > now())
            """,
            (token_hash(token),),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="invalid or expired token")
        cur.execute("UPDATE user_api_tokens SET last_used_at=now() WHERE id=%s", (row["token_id"],))
        conn.commit()
        return row


def require_admin(authorization: str | None):
    p = get_principal(authorization)
    if p["role"] != "admin" or "admin:*" not in p["scopes"]:
        raise HTTPException(status_code=403, detail="admin scope required")
    return p


def require_client_read(authorization: str | None):
    p = get_principal(authorization)
    if "client:read" not in p["scopes"] and "admin:*" not in p["scopes"]:
        raise HTTPException(status_code=403, detail="client:read scope required")
    return p


def tenant_ids(cur, principal) -> list[UUID]:
    cur.execute("SELECT condominium_id FROM user_condominiums WHERE user_id=%s", (principal["user_id"],))
    return [r["condominium_id"] for r in cur.fetchall()]


@router.post("/api/v1/admin/tokens", status_code=201)
def issue_token(payload: TokenIssue, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    plain = secrets.token_urlsafe(32)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE id=%s AND active=true", (payload.user_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="active user not found")
        cur.execute(
            """
            INSERT INTO user_api_tokens(user_id,token_hash,label,scopes,expires_at)
            VALUES (%s,%s,%s,%s,%s) RETURNING id,user_id,label,scopes,expires_at,created_at
            """,
            (payload.user_id, token_hash(plain), payload.label, payload.scopes, payload.expires_at),
        )
        row = cur.fetchone()
        conn.commit()
    return {**row, "token": plain}


@router.get("/api/v1/admin/rules")
def list_rules(camera_id: UUID | None = None, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        if camera_id:
            cur.execute(
                """SELECT r.*,v.geometry,v.parameters,v.model_requirements,v.certification_status,v.created_at AS version_created_at
                     FROM event_rules r JOIN event_rule_versions v ON v.event_rule_id=r.id AND v.version=r.active_version
                    WHERE r.camera_id=%s ORDER BY r.event_type""",
                (camera_id,),
            )
        else:
            cur.execute(
                """SELECT r.*,v.geometry,v.parameters,v.model_requirements,v.certification_status,v.created_at AS version_created_at
                     FROM event_rules r JOIN event_rule_versions v ON v.event_rule_id=r.id AND v.version=r.active_version
                    ORDER BY r.camera_id,r.event_type"""
            )
        return cur.fetchall()


@router.post("/api/v1/admin/rules", status_code=201)
def save_rule(payload: RuleConfig, authorization: str | None = Header(default=None)):
    principal = require_admin(authorization)
    # Abertura de porta exige validação física: nenhuma chamada administrativa pode
    # promover a regra antes dessa evidência externa existir.
    certification_status = "HOMOLOGATION" if payload.event_type in {"porta_aberta_bloco", "porta_bloco_aberta"} else payload.certification_status
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM cameras WHERE id=%s", (payload.camera_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="camera not found")
        cur.execute("SELECT * FROM event_rules WHERE camera_id=%s AND event_type=%s FOR UPDATE", (payload.camera_id, payload.event_type))
        rule = cur.fetchone()
        if not rule:
            cur.execute(
                "INSERT INTO event_rules(camera_id,event_type,display_label,active_version,enabled) VALUES (%s,%s,%s,1,%s) RETURNING *",
                (payload.camera_id, payload.event_type, payload.display_label, payload.enabled),
            )
            rule = cur.fetchone()
            version = 1
        else:
            version = int(rule["active_version"]) + 1
            cur.execute(
                "UPDATE event_rules SET display_label=%s,active_version=%s,enabled=%s WHERE id=%s RETURNING *",
                (payload.display_label, version, payload.enabled, rule["id"]),
            )
            rule = cur.fetchone()
        cur.execute(
            """
            INSERT INTO event_rule_versions(event_rule_id,version,geometry,parameters,model_requirements,certification_status,created_by)
            VALUES (%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s) RETURNING *
            """,
            (
                rule["id"], version,
                __import__("json").dumps(payload.geometry) if payload.geometry is not None else None,
                __import__("json").dumps(payload.parameters), __import__("json").dumps(payload.model_requirements),
                certification_status, principal.get("user_id"),
            ),
        )
        version_row = cur.fetchone()
        cur.execute(
            "INSERT INTO audit_logs(actor_type,actor_id,action,object_type,object_id,metadata) VALUES ('USER',%s,'rule.version.create','event_rule',%s,jsonb_build_object('version',%s,'status',%s))",
            (str(principal["user_id"]), str(rule["id"]), version, certification_status),
        )
        conn.commit()
        return {"rule": rule, "version": version_row}


@router.put("/api/v1/admin/confidence/{event_type}")
def set_confidence(event_type: str, payload: ConfidencePolicy, authorization: str | None = Header(default=None)):
    require_admin(authorization)
    if not (payload.min_log_confidence <= payload.review_from_confidence <= payload.evidence_from_confidence):
        raise HTTPException(status_code=422, detail="thresholds must be monotonic")
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO event_confidence_policies(event_type,min_log_confidence,review_from_confidence,evidence_from_confidence)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT(event_type) DO UPDATE SET min_log_confidence=excluded.min_log_confidence,
              review_from_confidence=excluded.review_from_confidence,evidence_from_confidence=excluded.evidence_from_confidence,updated_at=now()
            RETURNING *
            """,
            (event_type, payload.min_log_confidence, payload.review_from_confidence, payload.evidence_from_confidence),
        )
        row = cur.fetchone(); conn.commit(); return row


@router.get("/api/v1/admin/review")
def review_queue(
    condominium_id: UUID | None = None,
    event_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    clauses = ["c.review_status='HUMAN_REVIEW'"]
    params: list[object] = []
    if condominium_id:
        clauses.append("c.condominium_id=%s"); params.append(condominium_id)
    if event_type:
        clauses.append("r.event_type=%s"); params.append(event_type)
    params.append(limit)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT c.*,r.event_type,COALESCE(r.display_label,r.event_type) AS display_name,v.certification_status,
                   i.snapshot_object_key,i.snapshot_sha256,cam.name AS camera_name
              FROM event_candidates c
              JOIN event_rule_versions v ON v.id=c.event_rule_version_id
              JOIN event_rules r ON r.id=v.event_rule_id
              JOIN cameras cam ON cam.id=c.camera_id
              LEFT JOIN ingestion_events i ON i.id=c.ingestion_event_id
             WHERE {' AND '.join(clauses)} ORDER BY c.created_at DESC LIMIT %s
            """, tuple(params)
        )
        return cur.fetchall()


@router.post("/api/v1/admin/review/{candidate_id}")
def review_candidate(candidate_id: UUID, payload: ReviewDecision, authorization: str | None = Header(default=None)):
    principal = require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.*,r.event_type,COALESCE(r.display_label,r.event_type) AS display_name,v.certification_status,
                   i.snapshot_object_key,i.snapshot_sha256
              FROM event_candidates c JOIN event_rule_versions v ON v.id=c.event_rule_version_id
              JOIN event_rules r ON r.id=v.event_rule_id LEFT JOIN ingestion_events i ON i.id=c.ingestion_event_id
             WHERE c.id=%s FOR UPDATE OF c
            """, (candidate_id,)
        )
        c = cur.fetchone()
        if not c:
            raise HTTPException(status_code=404, detail="candidate not found")
        if payload.decision == "REJECT":
            cur.execute("UPDATE event_candidates SET review_status='REJECTED',reviewed_by=%s,reviewed_at=now(),review_notes=%s WHERE id=%s", (principal.get("user_id"), payload.notes, candidate_id))
            conn.commit(); return {"status": "REJECTED", "client_visible": False}
        cur.execute("UPDATE event_candidates SET review_status='APPROVED',reviewed_by=%s,reviewed_at=now(),review_notes=%s WHERE id=%s", (principal.get("user_id"), payload.notes, candidate_id))
        if c["certification_status"] != "PRODUCTION":
            conn.commit(); return {"status": "APPROVED_INTERNAL", "client_visible": False, "reason": "rule_not_production"}
        cur.execute(
            """
            INSERT INTO event_logs(candidate_id,condominium_id,camera_id,event_type,display_name,occurred_at,confidence,client_visible,status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,true,'APPROVED') ON CONFLICT(candidate_id) DO UPDATE SET client_visible=true,status='APPROVED' RETURNING *
            """,
            (c["id"],c["condominium_id"],c["camera_id"],c["event_type"],c["display_name"],c["detected_at"],c["confidence"]),
        )
        log = cur.fetchone()
        if c.get("snapshot_object_key"):
            cur.execute(
                """INSERT INTO event_evidence(event_log_id,event_candidate_id,object_key,media_type,sha256)
                   SELECT %s,%s,%s,'snapshot',%s WHERE NOT EXISTS (SELECT 1 FROM event_evidence WHERE event_log_id=%s AND object_key=%s)""",
                (log["id"],c["id"],c["snapshot_object_key"],c["snapshot_sha256"],log["id"],c["snapshot_object_key"]),
            )
        conn.commit(); return {"status": "APPROVED", "client_visible": True, "log": log}


@router.get("/api/v1/admin/logs")
def admin_logs(
    condominium_id: UUID | None = None, camera_id: UUID | None = None, event_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=500), authorization: str | None = Header(default=None),
):
    require_admin(authorization)
    clauses = ["1=1"]; params: list[object] = []
    if condominium_id: clauses.append("l.condominium_id=%s"); params.append(condominium_id)
    if camera_id: clauses.append("l.camera_id=%s"); params.append(camera_id)
    if event_type: clauses.append("l.event_type=%s"); params.append(event_type)
    params.append(limit)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT l.*,cam.name AS camera_name FROM event_logs l JOIN cameras cam ON cam.id=l.camera_id WHERE {' AND '.join(clauses)} ORDER BY l.occurred_at DESC LIMIT %s", tuple(params))
        return cur.fetchall()


@router.get("/api/v1/admin/dashboard")
def admin_dashboard(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*)::int AS n FROM cameras WHERE enabled=true"); cameras = cur.fetchone()["n"]
        cur.execute("SELECT count(*)::int AS n FROM cameras WHERE enabled=true AND health_state='ONLINE'"); online = cur.fetchone()["n"]
        cur.execute("SELECT count(*)::int AS n FROM event_candidates WHERE review_status='HUMAN_REVIEW'"); review = cur.fetchone()["n"]
        cur.execute("SELECT count(*)::int AS n FROM event_logs WHERE occurred_at >= date_trunc('day',now())"); logs_today = cur.fetchone()["n"]
        cur.execute("SELECT queue_status,count(*)::int AS total FROM ingestion_events GROUP BY queue_status"); ingestion = {x["queue_status"]: x["total"] for x in cur.fetchall()}
        cur.execute("SELECT status,count(*)::int AS total FROM detection_results GROUP BY status"); detection = {x["status"]: x["total"] for x in cur.fetchall()}
        return {"cameras": cameras, "online": online, "review_pending": review, "logs_today": logs_today, "ingestion": ingestion, "detection": detection}


@router.get("/api/v1/client/logs")
def client_logs(
    condominium_id: UUID | None = None, event_type: str | None = None, camera_id: UUID | None = None,
    date_from: datetime | None = None, date_to: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=200), authorization: str | None = Header(default=None),
):
    p = require_client_read(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        allowed = tenant_ids(cur, p)
        if not allowed:
            return []
        if condominium_id and condominium_id not in allowed:
            raise HTTPException(status_code=404, detail="condominium not found")
        scoped = [condominium_id] if condominium_id else allowed
        clauses = ["l.client_visible=true", "l.condominium_id = ANY(%s)"]; params: list[object] = [scoped]
        if event_type: clauses.append("l.event_type=%s"); params.append(event_type)
        if camera_id: clauses.append("l.camera_id=%s"); params.append(camera_id)
        if date_from: clauses.append("l.occurred_at >= %s"); params.append(date_from)
        if date_to: clauses.append("l.occurred_at <= %s"); params.append(date_to)
        params.append(limit)
        cur.execute(
            f"""SELECT l.*,cam.name AS camera_name,
                       EXISTS(SELECT 1 FROM event_evidence e WHERE e.event_log_id=l.id) AS has_evidence
                  FROM event_logs l JOIN cameras cam ON cam.id=l.camera_id
                 WHERE {' AND '.join(clauses)} ORDER BY l.occurred_at DESC LIMIT %s""", tuple(params)
        )
        return cur.fetchall()


@router.get("/api/v1/client/logs/{log_id}/evidence")
def client_evidence(log_id: UUID, authorization: str | None = Header(default=None)):
    p = require_client_read(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        allowed = tenant_ids(cur, p)
        cur.execute("SELECT id FROM event_logs WHERE id=%s AND client_visible=true AND condominium_id=ANY(%s)", (log_id, allowed))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="log not found")
        cur.execute("SELECT id,object_key,media_type,sha256,size_bytes,created_at FROM event_evidence WHERE event_log_id=%s ORDER BY created_at", (log_id,))
        return cur.fetchall()


@router.post("/api/v1/isabel/query")
def isabel_query(payload: IsabelQuery, authorization: str | None = Header(default=None)):
    p = require_client_read(authorization)
    with pool.connection() as conn, conn.cursor() as cur:
        allowed = tenant_ids(cur, p)
        if not allowed:
            return {"intent": payload.intent, "count": 0, "logs": []}
        clauses = ["client_visible=true", "condominium_id=ANY(%s)"]; params: list[object] = [allowed]
        if payload.event_type: clauses.append("event_type=%s"); params.append(payload.event_type)
        if payload.camera_id: clauses.append("camera_id=%s"); params.append(payload.camera_id)
        if payload.date_from: clauses.append("occurred_at >= %s"); params.append(payload.date_from)
        if payload.date_to: clauses.append("occurred_at <= %s"); params.append(payload.date_to)
        if payload.intent == "count_logs":
            cur.execute(f"SELECT count(*)::int AS total FROM event_logs WHERE {' AND '.join(clauses)}", tuple(params))
            return {"intent": "count_logs", "count": cur.fetchone()["total"]}
        params.append(payload.limit)
        cur.execute(f"SELECT id,condominium_id,camera_id,event_type,display_name,occurred_at,confidence FROM event_logs WHERE {' AND '.join(clauses)} ORDER BY occurred_at DESC LIMIT %s", tuple(params))
        return {"intent": "search_logs", "logs": cur.fetchall()}
