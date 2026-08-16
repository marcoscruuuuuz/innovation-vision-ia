import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import redis
from minio import Minio
from psycopg.rows import dict_row

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://vision:vision@postgres:5432/vision")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
INPUT_STREAM = os.getenv("CERTIFICATION_INPUT_STREAM", "vision:rule:candidates")
NOTIFICATION_STREAM = os.getenv("NOTIFICATION_STREAM", "vision:notifications")
CONSUMER_GROUP = os.getenv("CERTIFICATION_CONSUMER_GROUP", "vision-certification")
CONSUMER_NAME = os.getenv("CERTIFICATION_CONSUMER_NAME", "certification-1")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "visionminio")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "vision-evidence")
HOST_GATEWAY_NAME = os.getenv("VISION_HOST_GATEWAY", "host.docker.internal")
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "/usr/bin/ffmpeg")
DEFAULT_CLIP_SECONDS = int(os.getenv("VISION_MINI_CLIP_SECONDS", "10"))
MAX_CLIP_SECONDS = int(os.getenv("VISION_MAX_MINI_CLIP_SECONDS", "20"))

r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
minio = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY, secret_key=MINIO_SECRET_KEY, secure=False)


def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def ensure_group():
    try:
        r.xgroup_create(INPUT_STREAM, CONSUMER_GROUP, id="0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def ensure_bucket():
    if not minio.bucket_exists(MINIO_BUCKET):
        minio.make_bucket(MINIO_BUCKET)


def container_reachable_uri(uri: str) -> str:
    parsed = urlsplit(uri)
    if parsed.scheme.lower() != "rtsp":
        raise RuntimeError("active route is not RTSP")
    host = parsed.hostname or ""
    if host not in {"127.0.0.1", "localhost", "0.0.0.0"}:
        return uri
    userinfo = ""
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo += f":{parsed.password}"
        userinfo += "@"
    port = f":{parsed.port}" if parsed.port else ""
    return urlunsplit((parsed.scheme, f"{userinfo}{HOST_GATEWAY_NAME}{port}", parsed.path, parsed.query, parsed.fragment))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def capture_required_clip(cur, candidate: dict) -> bool:
    params = candidate.get("parameters") or {}
    if not params.get("capture_mini_clip"):
        return True
    if candidate.get("clip_object_key"):
        return True

    requested = params.get("mini_clip_seconds", DEFAULT_CLIP_SECONDS)
    try:
        seconds = max(2, min(MAX_CLIP_SECONDS, int(requested)))
    except (TypeError, ValueError):
        seconds = DEFAULT_CLIP_SECONDS

    cur.execute(
        """
        SELECT local_uri FROM stream_routes
         WHERE camera_id=%s AND state='ACTIVE' AND deactivated_at IS NULL
         ORDER BY generation DESC LIMIT 1
        """,
        (candidate["camera_id"],),
    )
    route = cur.fetchone()
    if not route or not route.get("local_uri"):
        return False

    uri = container_reachable_uri(route["local_uri"])
    with tempfile.TemporaryDirectory(prefix="vision-clip-") as tmp:
        output = Path(tmp) / "evidence.mp4"
        cmd = [
            FFMPEG_BIN,
            "-hide_banner",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-i",
            uri,
            "-t",
            str(seconds),
            "-an",
            "-c:v",
            "mpeg4",
            "-q:v",
            "5",
            "-movflags",
            "+faststart",
            "-y",
            str(output),
        ]
        try:
            subprocess.run(cmd, check=True, timeout=seconds + 20, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            return False
        if not output.exists() or output.stat().st_size < 2048:
            return False
        digest = sha256_file(output)
        object_key = (
            f"{candidate['condominium_id']}/{candidate['dvr_id']}/clips/"
            f"{candidate['ingestion_event_id'] or candidate['id']}.mp4"
        )
        ensure_bucket()
        minio.fput_object(MINIO_BUCKET, object_key, str(output), content_type="video/mp4", metadata={"sha256": digest})
        if candidate.get("ingestion_event_id"):
            cur.execute(
                """
                UPDATE ingestion_events
                   SET clip_object_key=%s,clip_sha256=%s,clip_duration_seconds=%s
                 WHERE id=%s
                """,
                (object_key, digest, seconds, candidate["ingestion_event_id"]),
            )
        candidate["clip_object_key"] = object_key
        candidate["clip_sha256"] = digest
        candidate["clip_duration_seconds"] = seconds
        return True


def attach_evidence(cur, log: dict, candidate: dict):
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
    if candidate.get("clip_object_key"):
        cur.execute(
            """
            INSERT INTO event_evidence(event_log_id,event_candidate_id,object_key,media_type,sha256)
            SELECT %s,%s,%s,'clip',%s
            WHERE NOT EXISTS (
              SELECT 1 FROM event_evidence WHERE event_log_id=%s AND object_key=%s
            )
            """,
            (
                log["id"], candidate["id"], candidate["clip_object_key"], candidate["clip_sha256"],
                log["id"], candidate["clip_object_key"],
            ),
        )


def process(candidate_id: str):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.*, r.event_type, COALESCE(r.display_label,r.event_type) AS display_name,
                   v.certification_status,v.parameters,
                   i.id AS ingestion_event_id,i.dvr_id,i.snapshot_object_key,i.snapshot_sha256,
                   i.clip_object_key,i.clip_sha256,i.clip_duration_seconds
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

        if not capture_required_clip(cur, candidate):
            cur.execute(
                "UPDATE event_candidates SET review_status='HUMAN_REVIEW',review_notes=COALESCE(review_notes,'mini_clip_required_but_capture_failed') WHERE id=%s",
                (candidate_id,),
            )
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
        if action == "EVIDENCE_LOG" or candidate.get("clip_object_key"):
            attach_evidence(cur, log, candidate)

        cur.execute("UPDATE event_candidates SET review_status='APPROVED' WHERE id=%s", (candidate_id,))
        cur.execute(
            "INSERT INTO audit_logs(actor_type,action,object_type,object_id,metadata) VALUES ('SYSTEM','event.promote','event_log',%s,%s::jsonb)",
            (
                str(log["id"]),
                json.dumps(
                    {
                        "candidate_id": candidate_id,
                        "action": action,
                        "event_type": candidate["event_type"],
                        "has_snapshot": bool(candidate.get("snapshot_object_key")),
                        "has_clip": bool(candidate.get("clip_object_key")),
                    }
                ),
            ),
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
