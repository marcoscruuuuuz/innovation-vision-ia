from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

import media_worker_v2 as routed
from models import Camera, Candidate, EventLog, Rule, SessionLocal

base = routed.base
LOG = logging.getLogger("vision-light-media-v3")
ORIGINAL_PROCESS = base.process


def production_rule(session: SessionLocal, camera_id: str, event_key: str) -> Rule | None:
    return session.scalar(
        select(Rule)
        .where(
            Rule.camera_id == camera_id,
            Rule.event_key == event_key,
            Rule.enabled.is_(True),
            Rule.state == "PRODUCTION",
        )
        .order_by(Rule.version.desc())
    )


def persist_pending_certification(payload: dict[str, Any]) -> None:
    camera_id = str(payload["camera_id"])
    event_key = str(payload["event_key"])
    candidate_id = str(payload["candidate_id"])
    occurred_ts = float(payload.get("occurred_at") or datetime.now(timezone.utc).timestamp())

    with SessionLocal() as session:
        if session.get(Camera, camera_id) is None:
            raise KeyError(f"camera not registered in database: {camera_id}")
        rule = production_rule(session, camera_id, event_key)
        if rule is None:
            LOG.info("certification candidate remains shadow camera=%s event=%s", camera_id, event_key)
            return
        if session.scalar(select(EventLog).where(EventLog.candidate_id == candidate_id)):
            return
        candidate = session.get(Candidate, candidate_id)
        if candidate is None:
            candidate = Candidate(
                id=candidate_id,
                camera_id=camera_id,
                event_key=event_key,
                occurred_at=datetime.fromtimestamp(occurred_ts, tz=timezone.utc),
                confidence=float(payload.get("confidence") or 0),
                status="CERTIFICATION_PENDING",
                rule_id=rule.id,
                payload=payload,
            )
            session.add(candidate)
        else:
            candidate.status = "CERTIFICATION_PENDING"
            candidate.payload = payload
        session.commit()
        LOG.info("candidate held fail-closed id=%s event=%s", candidate_id, event_key)


def guarded_process(payload: dict[str, Any], gateways: dict[str, Any], cameras: dict[str, Any], minio) -> None:  # type: ignore[no-untyped-def]
    requires_certification = bool(payload.get("certification_required"))
    certification_status = str(payload.get("certification_status") or "").upper()
    if requires_certification and certification_status != "APPROVED":
        persist_pending_certification(payload)
        return
    ORIGINAL_PROCESS(payload, gateways, cameras, minio)


base.process = guarded_process


if __name__ == "__main__":
    base.main()
