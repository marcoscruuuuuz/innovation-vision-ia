import os
import time
from datetime import datetime, timedelta, timezone

import psycopg
from minio import Minio
from psycopg.rows import dict_row

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://vision:vision@postgres:5432/vision")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "visionminio")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "vision-evidence")
RETENTION_DAYS = int(os.getenv("VISION_RETENTION_DAYS", "7"))
INTERVAL_SECONDS = int(os.getenv("RETENTION_INTERVAL_SECONDS", "86400"))

minio = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY, secret_key=MINIO_SECRET_KEY, secure=False)


def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def run_once():
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    with db() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO retention_job_runs(cutoff_at,status) VALUES (%s,'RUNNING') RETURNING id", (cutoff,))
        run_id = cur.fetchone()["id"]
        conn.commit()

    deleted_objects = 0
    deleted_logs = 0
    try:
        with db() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO evidence_deletion_queue(object_key)
                SELECT DISTINCT e.object_key
                  FROM event_evidence e JOIN event_logs l ON l.id=e.event_log_id
                 WHERE l.occurred_at < %s
                ON CONFLICT(object_key) DO NOTHING
                """, (cutoff,)
            )
            cur.execute("SELECT id,object_key,attempts FROM evidence_deletion_queue WHERE deleted_at IS NULL ORDER BY queued_at LIMIT 5000")
            queued = cur.fetchall()
            conn.commit()

        for item in queued:
            try:
                minio.remove_object(MINIO_BUCKET, item["object_key"])
                with db() as conn, conn.cursor() as cur:
                    cur.execute("UPDATE evidence_deletion_queue SET deleted_at=now(),attempts=attempts+1,last_error=NULL WHERE id=%s", (item["id"],))
                    conn.commit()
                deleted_objects += 1
            except Exception as exc:
                with db() as conn, conn.cursor() as cur:
                    cur.execute("UPDATE evidence_deletion_queue SET attempts=attempts+1,last_error=%s WHERE id=%s", (str(exc)[:2000], item["id"]))
                    conn.commit()

        with db() as conn, conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM event_logs l
                 WHERE l.occurred_at < %s
                   AND NOT EXISTS (
                     SELECT 1 FROM event_evidence e
                     JOIN evidence_deletion_queue q ON q.object_key=e.object_key
                     WHERE e.event_log_id=l.id AND q.deleted_at IS NULL
                   )
                """, (cutoff,)
            )
            deleted_logs = cur.rowcount
            cur.execute(
                "UPDATE retention_job_runs SET completed_at=now(),deleted_logs=%s,deleted_evidence=%s,status='SUCCESS' WHERE id=%s",
                (deleted_logs, deleted_objects, run_id),
            )
            cur.execute(
                "INSERT INTO audit_logs(actor_type,action,object_type,object_id,metadata) VALUES ('SYSTEM','retention.complete','retention_job',%s,jsonb_build_object('cutoff',%s,'deleted_logs',%s,'deleted_evidence',%s))",
                (str(run_id), cutoff, deleted_logs, deleted_objects),
            )
            conn.commit()
    except Exception as exc:
        with db() as conn, conn.cursor() as cur:
            cur.execute("UPDATE retention_job_runs SET completed_at=now(),status='FAILED',error=%s WHERE id=%s", (str(exc)[:4000], run_id))
            conn.commit()
        raise


def main():
    while True:
        try:
            run_once()
        except Exception as exc:
            print(f"retention error: {exc}", flush=True)
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
