import json
import os

import psycopg
import redis
from psycopg.rows import dict_row

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://vision:vision@postgres:5432/vision")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
INPUT_STREAM = os.getenv("CERTIFICATION_INPUT_STREAM", "vision:rule:candidates")
NOTIFICATION_STREAM = os.getenv("NOTIFICATION_STREAM", "vision:notifications")
CONSUMER_GROUP = os.getenv("CERTIFICATION_CONSUMER_GROUP", "vision-certification")
CONSUMER_NAME = os.getenv("CERTIFICATION_CONSUMER_NAME", "certification-1")

r = redis.Redis.from_url(REDIS_URL, decode_responses=True)


def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def ensure_group():
    try:
        r.xgroup_create(INPUT_STREAM, CONSUMER_GROUP, id="0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def process(candidate_id: str):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.*, r.event_type, COALESCE(r.display_label,r.event_type) AS display_name,
                   v.certification_status, i.snapshot_object_key, i.snapshot_sha256
              FROM event_candidates c
              JOIN event_rule_versions v ON v.id=c.event_rule_version_id
              JOIN event_rules r ON r.id=v.event_rule_id
              LEFT JOIN ingestion_events i ON i.id=c.ingestion_event_id
             WHERE c.id=%s
             FOR UPDATE OF c
            """,
            (candidate_id,),
        )
        candidate = cur.fetchone()
        if not candidate:
            return
        action = candidate.get("pipeline_action")
        certification_status = candidate["certification_status"]

        if action == "DROP":
            cur.execute("UPDATE event_candidates SET review_status='REJECTED' WHERE id=%s", (candidate_id,))
            conn.commit()
            return

        if certification_status != "PRODUCTION":
            cur.execute("UPDATE event_candidates SET review_status='HUMAN_REVIEW' WHERE id=%s", (candidate_id,))
            conn.commit()
            return

        if action == "HUMAN_REVIEW":
            cur.execute("UPDATE event_candidates SET review_status='HUMAN_REVIEW' WHERE id=%s", (candidate_id,))
            conn.commit()
            return

        if action == "EVIDENCE_LOG" and not candidate.get("snapshot_object_key"):
            cur.execute("UPDATE event_candidates SET review_status='HUMAN_REVIEW' WHERE id=%s", (candidate_id,))
            conn.commit()
            return

        cur.execute(
            """
            INSERT INTO event_logs(candidate_id,condominium_id,camera_id,event_type,display_name,occurred_at,confidence,client_visible,status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,true,'APPROVED')
            ON CONFLICT(candidate_id) DO UPDATE SET
              confidence=excluded.confidence, client_visible=true, status='APPROVED'
            RETURNING *
            """,
            (
                candidate["id"], candidate["condominium_id"], candidate["camera_id"], candidate["event_type"],
                candidate["display_name"], candidate["detected_at"], candidate["confidence"],
            ),
        )
        log = cur.fetchone()

        # If the ingest event already carried a real snapshot, preserve it for every approved log.
        # EVIDENCE_LOG still requires one; TEXT_LOG may attach it opportunistically when available.
        if candidate.get("snapshot_object_key"):
            cur.execute(
                """
                INSERT INTO event_evidence(event_log_id,event_candidate_id,object_key,media_type,sha256)
                SELECT %s,%s,%s,'snapshot',%s
                WHERE NOT EXISTS (
                  SELECT 1 FROM event_evidence WHERE event_log_id=%s AND object_key=%s
                )
                """,
                (
                    log["id"], candidate["id"], candidate["snapshot_object_key"], candidate["snapshot_sha256"],
                    log["id"], candidate["snapshot_object_key"],
                ),
            )

        cur.execute("UPDATE event_candidates SET review_status='APPROVED' WHERE id=%s", (candidate_id,))
        cur.execute(
            "INSERT INTO audit_logs(actor_type,action,object_type,object_id,metadata) VALUES ('SYSTEM','event.promote','event_log',%s,%s::jsonb)",
            (str(log["id"]), json.dumps({"candidate_id": candidate_id, "action": action, "event_type": candidate["event_type"]})),
        )
        conn.commit()

    r.xadd(
        NOTIFICATION_STREAM,
        {"event_log_id": str(log["id"]), "condominium_id": str(log["condominium_id"]), "event_type": log["event_type"]},
        maxlen=100000,
        approximate=True,
    )


def main():
    ensure_group()
    while True:
        rows = r.xreadgroup(CONSUMER_GROUP, CONSUMER_NAME, {INPUT_STREAM: ">"}, count=16, block=5000)
        for _, messages in rows:
            for message_id, fields in messages:
                try:
                    candidate_id = fields.get("candidate_id")
                    if candidate_id:
                        process(candidate_id)
                    r.xack(INPUT_STREAM, CONSUMER_GROUP, message_id)
                except Exception as exc:
                    print(json.dumps({"level": "error", "message_id": message_id, "error": str(exc)}), flush=True)


if __name__ == "__main__":
    main()
