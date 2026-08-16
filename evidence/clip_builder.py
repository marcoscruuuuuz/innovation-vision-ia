from __future__ import annotations

import hashlib
import os
import socket
import struct
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from minio import Minio
from psycopg.rows import dict_row

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://vision:vision@postgres:5432/vision")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "visionminio")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "vision-evidence")
POLL_SECONDS = float(os.getenv("CLIP_BUILDER_POLL_SECONDS", "3"))
MAX_ATTEMPTS = int(os.getenv("CLIP_BUILDER_MAX_ATTEMPTS", "5"))
HOST_GATEWAY_NAME = os.getenv("VISION_HOST_GATEWAY", "")

minio = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY, secret_key=MINIO_SECRET_KEY, secure=False)


def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def ensure_bucket():
    for _ in range(30):
        try:
            if not minio.bucket_exists(MINIO_BUCKET):
                minio.make_bucket(MINIO_BUCKET)
            return
        except Exception:
            time.sleep(2)
    raise RuntimeError("MinIO evidence bucket unavailable")


def docker_gateway() -> str:
    if HOST_GATEWAY_NAME:
        return HOST_GATEWAY_NAME
    try:
        with open("/proc/net/route", "r", encoding="ascii") as handle:
            next(handle, None)
            for line in handle:
                fields = line.split()
                if len(fields) >= 3 and fields[1] == "00000000":
                    return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
    except Exception:
        pass
    return "172.17.0.1"


def reachable_uri(uri: str) -> str:
    parsed = urlsplit(uri)
    if parsed.scheme.lower() != "rtsp":
        raise RuntimeError("active route is not RTSP")
    if (parsed.hostname or "") not in {"127.0.0.1", "localhost", "0.0.0.0"}:
        return uri
    auth = ""
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth += f":{parsed.password}"
        auth += "@"
    port = f":{parsed.port}" if parsed.port else ""
    return urlunsplit((parsed.scheme, f"{auth}{docker_gateway()}{port}", parsed.path, parsed.query, parsed.fragment))


def claim_job():
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT j.id,j.event_log_id,j.duration_seconds,l.camera_id,l.condominium_id,l.event_type,l.occurred_at,
                   r.local_uri
              FROM evidence_clip_jobs j
              JOIN event_logs l ON l.id=j.event_log_id
              LEFT JOIN LATERAL (
                  SELECT sr.local_uri FROM stream_routes sr
                   WHERE sr.camera_id=l.camera_id AND sr.state='ACTIVE' AND sr.deactivated_at IS NULL
                   ORDER BY sr.generation DESC LIMIT 1
              ) r ON true
             WHERE j.status IN ('PENDING','FAILED')
               AND j.attempts < %s
               AND (j.retry_at IS NULL OR j.retry_at <= now())
             ORDER BY j.created_at
             FOR UPDATE OF j SKIP LOCKED
             LIMIT 1
            """,
            (MAX_ATTEMPTS,),
        )
        job = cur.fetchone()
        if not job:
            return None
        cur.execute(
            "UPDATE evidence_clip_jobs SET status='RUNNING',attempts=attempts+1,started_at=now(),last_error=NULL WHERE id=%s",
            (job["id"],),
        )
        conn.commit()
        return job


def fail_job(job_id, message: str):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE evidence_clip_jobs SET status='FAILED',last_error=%s,retry_at=now()+interval '60 seconds' WHERE id=%s",
            (message[:2000], job_id),
        )
        conn.commit()


def build_clip(job):
    if not job.get("local_uri"):
        raise RuntimeError("camera has no active RTSP route")
    uri = reachable_uri(job["local_uri"])
    duration = int(job["duration_seconds"])
    with tempfile.TemporaryDirectory(prefix="vision-clip-") as tempdir:
        output = Path(tempdir) / "clip.mp4"
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp",
            "-rw_timeout", "5000000", "-i", uri, "-t", str(duration), "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-movflags", "+faststart", "-y", str(output),
        ]
        subprocess.run(cmd, check=True, timeout=duration + 20)
        data = output.read_bytes()
        if len(data) < 4096:
            raise RuntimeError("captured clip is too small")
        digest = hashlib.sha256(data).hexdigest()
        stamp = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        object_key = f"clips/{job['condominium_id']}/{job['camera_id']}/{stamp}/{job['event_log_id']}.mp4"
        minio.fput_object(MINIO_BUCKET, object_key, str(output), content_type="video/mp4")
        with db() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO event_evidence(event_log_id,object_key,media_type,sha256,size_bytes)
                VALUES (%s,%s,'clip',%s,%s)
                ON CONFLICT DO NOTHING
                """,
                (job["event_log_id"], object_key, digest, len(data)),
            )
            cur.execute(
                "UPDATE evidence_clip_jobs SET status='DONE',completed_at=now(),retry_at=NULL WHERE id=%s",
                (job["id"],),
            )
            cur.execute(
                "INSERT INTO audit_logs(actor_type,action,object_type,object_id,metadata) VALUES ('SYSTEM','evidence.clip.created','event_log',%s,jsonb_build_object('object_key',%s,'sha256',%s,'bytes',%s))",
                (str(job["event_log_id"]), object_key, digest, len(data)),
            )
            conn.commit()


def main():
    ensure_bucket()
    while True:
        job = claim_job()
        if not job:
            time.sleep(POLL_SECONDS)
            continue
        try:
            build_clip(job)
        except Exception as exc:
            fail_job(job["id"], f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
