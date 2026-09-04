from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

from minio import Minio
from sqlalchemy import select

from models import Candidate, EventLog, SessionLocal, create_schema

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOG = logging.getLogger("vision-light-retention")

RETENTION_DAYS = int(os.getenv("MEDIA_RETENTION_DAYS", "7"))
INTERVAL_SECONDS = int(os.getenv("RETENTION_INTERVAL_SECONDS", "3600"))
BATCH_SIZE = int(os.getenv("RETENTION_BATCH_SIZE", "200"))
BUCKET = os.getenv("MINIO_BUCKET", "vision-light")


def minio_client() -> Minio:
    return Minio(
        os.getenv("MINIO_ENDPOINT", "minio:9000"),
        access_key=os.environ["MINIO_ACCESS_KEY"],
        secret_key=os.environ["MINIO_SECRET_KEY"],
        secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
    )


def protected(log: EventLog) -> bool:
    metadata = log.media_metadata or {}
    return bool(metadata.get("legal_hold") or metadata.get("retention_protected"))


def run_once() -> dict[str, int]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    deleted_logs = 0
    deleted_objects = 0
    errors = 0
    client = minio_client()

    with SessionLocal() as session:
        rows = session.scalars(
            select(EventLog)
            .where(EventLog.created_at < cutoff)
            .order_by(EventLog.created_at.asc())
            .limit(BATCH_SIZE)
        ).all()

        for log in rows:
            if protected(log):
                continue
            objects = [value for value in (log.snapshot_object, log.clip_object) if value]
            try:
                for object_name in objects:
                    client.remove_object(BUCKET, object_name)
                    deleted_objects += 1
                candidate = session.get(Candidate, log.candidate_id)
                session.delete(log)
                if candidate is not None:
                    session.delete(candidate)
                session.commit()
                deleted_logs += 1
            except Exception:
                session.rollback()
                errors += 1
                LOG.exception("retention failed event_log=%s", log.id)

    result = {"deleted_logs": deleted_logs, "deleted_objects": deleted_objects, "errors": errors}
    LOG.info("retention cutoff=%s result=%s", cutoff.isoformat(), result)
    return result


def main() -> None:
    create_schema()
    while True:
        try:
            result = run_once()
            if result["deleted_logs"] >= BATCH_SIZE:
                time.sleep(2)
                continue
        except Exception:
            LOG.exception("retention cycle failed")
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
