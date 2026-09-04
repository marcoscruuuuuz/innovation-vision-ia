from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import redis
import yaml
from minio import Minio
from sqlalchemy import select

from models import Camera, Candidate, EventLog, Rule, SessionLocal, create_schema

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOG = logging.getLogger("vision-light-media")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
PREFIX = os.getenv("REDIS_PREFIX", "vl:")
STREAM = f"{PREFIX}candidates"
GROUP = f"{PREFIX}media"
CONSUMER = os.getenv("HOSTNAME", "media-1")
REGISTRY_FILE = Path(os.getenv("GATEWAY_REGISTRY_FILE", "/config/gateways.yaml"))
BUCKET = os.getenv("MINIO_BUCKET", "vision-light")
PRE_SECONDS = float(os.getenv("CLIP_PRE_SECONDS", "5"))
POST_SECONDS = float(os.getenv("CLIP_POST_SECONDS", "10"))
MIN_DURATION = float(os.getenv("FFPROBE_DURATION_MIN", "14.5"))
MAX_DURATION = float(os.getenv("FFPROBE_DURATION_MAX", "15.5"))
WRITER_ENABLED = os.getenv("PRODUCTION_WRITER_ENABLED", "false").lower() == "true"


def registry() -> tuple[dict[str, Any], dict[str, Any]]:
    data = yaml.safe_load(REGISTRY_FILE.read_text()) or {}
    gateways = {item["id"]: item for item in data.get("gateways", [])}
    cameras = {item["camera_id"]: item for item in data.get("cameras", [])}
    return gateways, cameras


def token_for(gateway: dict[str, Any]) -> str | None:
    if gateway.get("auth_token"):
        return str(gateway["auth_token"])
    path = gateway.get("auth_token_file")
    return Path(path).read_text().strip() if path and Path(path).exists() else None


def render_template(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        return value.format(**replacements)
    if isinstance(value, dict):
        return {key: render_template(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [render_template(item, replacements) for item in value]
    return value


def fetch_clip(camera: dict[str, Any], gateway: dict[str, Any], start_at: str, end_at: str, output: Path) -> None:
    request = camera.get("playback_request", {})
    method = str(request.get("method", "POST")).upper()
    base = str(gateway["api_base_url"]).rstrip("/")
    path = str(gateway.get("playback_clip_path", "/v1/playback-clip"))
    replacements = {"start_at": start_at, "end_at": end_at}
    headers = {"Accept": "video/mp4"}
    token = token_for(gateway)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with httpx.Client(timeout=httpx.Timeout(40, connect=5), follow_redirects=False) as client:
        response = client.request(
            method,
            f"{base}{path}",
            headers=headers,
            params=render_template(request.get("params"), replacements),
            json=render_template(request.get("json_template") or request.get("json"), replacements),
        )
        response.raise_for_status()
        output.write_bytes(response.content)


def probe(path: Path) -> dict[str, Any]:
    command = [
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,format_name,size:stream=index,codec_type,codec_name,width,height,r_frame_rate",
        "-of", "json", str(path),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
    metadata = json.loads(result.stdout)
    duration = float(metadata.get("format", {}).get("duration") or 0)
    video_streams = [stream for stream in metadata.get("streams", []) if stream.get("codec_type") == "video"]
    if not (MIN_DURATION <= duration <= MAX_DURATION):
        raise ValueError(f"invalid clip duration {duration}")
    if not video_streams:
        raise ValueError("clip has no video stream")
    metadata["validated_duration"] = duration
    return metadata


def extract_snapshot(clip: Path, output: Path) -> None:
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", str(PRE_SECONDS), "-i", str(clip), "-frames:v", "1", "-q:v", "2", str(output),
    ]
    subprocess.run(command, check=True, timeout=30)
    raw = output.read_bytes()
    if len(raw) < 1024 or not raw.startswith(b"\xff\xd8"):
        raise ValueError("invalid JPEG snapshot")


def upload(client: Minio, object_name: str, path: Path, content_type: str) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    client.fput_object(BUCKET, object_name, str(path), content_type=content_type)
    client.stat_object(BUCKET, object_name)
    return digest


def rule_is_production(session: SessionLocal, camera_id: str, event_key: str) -> Rule | None:
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


def process(payload: dict[str, Any], gateways: dict[str, Any], cameras: dict[str, Any], minio: Minio) -> None:
    camera_id = str(payload["camera_id"])
    event_key = str(payload["event_key"])
    occurred_ts = float(payload.get("occurred_at") or time.time())
    occurred = datetime.fromtimestamp(occurred_ts, tz=timezone.utc)
    camera_config = cameras.get(camera_id)
    if camera_config is None:
        raise KeyError(f"camera not in gateway registry: {camera_id}")
    gateway = gateways[camera_config["gateway_id"]]

    with SessionLocal() as session:
        db_camera = session.get(Camera, camera_id)
        if db_camera is None:
            raise KeyError(f"camera not registered in database: {camera_id}")
        rule = rule_is_production(session, camera_id, event_key)
        if rule is None:
            LOG.info("candidate remains shadow camera=%s event=%s", camera_id, event_key)
            return

        candidate_id = str(payload.get("candidate_id") or payload.get("id") or hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:36])
        existing = session.scalar(select(EventLog).where(EventLog.candidate_id == candidate_id))
        if existing:
            return

        candidate = session.get(Candidate, candidate_id)
        if candidate is None:
            candidate = Candidate(
                id=candidate_id,
                camera_id=camera_id,
                event_key=event_key,
                occurred_at=occurred,
                confidence=float(payload.get("confidence") or 0),
                status="MEDIA_PENDING",
                rule_id=rule.id,
                payload=payload,
            )
            session.add(candidate)
            session.commit()

        if not WRITER_ENABLED:
            LOG.info("writer disabled; candidate persisted id=%s", candidate_id)
            return

        start = datetime.fromtimestamp(occurred_ts - PRE_SECONDS, tz=timezone.utc)
        end = datetime.fromtimestamp(occurred_ts + POST_SECONDS, tz=timezone.utc)
        prefix = f"events/{occurred:%Y/%m/%d}/{camera_id}/{candidate_id}"

        with tempfile.TemporaryDirectory(prefix="vision-light-") as tmp:
            tmp_path = Path(tmp)
            clip_path = tmp_path / "clip.mp4"
            snapshot_path = tmp_path / "snapshot.jpg"
            fetch_clip(camera_config, gateway, start.isoformat(), end.isoformat(), clip_path)
            metadata = probe(clip_path)
            extract_snapshot(clip_path, snapshot_path)
            clip_object = f"{prefix}/clip-15s.mp4"
            snapshot_object = f"{prefix}/snapshot.jpg"
            clip_sha = upload(minio, clip_object, clip_path, "video/mp4")
            snapshot_sha = upload(minio, snapshot_object, snapshot_path, "image/jpeg")

        event = EventLog(
            candidate_id=candidate_id,
            camera_id=camera_id,
            event_key=event_key,
            occurred_at=occurred,
            confidence=float(payload.get("confidence") or 0),
            decision="APPROVED",
            snapshot_object=snapshot_object,
            clip_object=clip_object,
            clip_duration_seconds=float(metadata["validated_duration"]),
            media_metadata={
                "clip_sha256": clip_sha,
                "snapshot_sha256": snapshot_sha,
                "ffprobe": metadata,
                "playback_source": gateway["id"],
                "clip_start": start.isoformat(),
                "clip_end": end.isoformat(),
            },
            client_visible=True,
        )
        candidate.status = "CERTIFIED"
        session.add(event)
        session.commit()
        LOG.info("visible event created id=%s event=%s camera=%s", event.id, event_key, camera_id)


def main() -> None:
    create_schema()
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        client.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise
    gateways, cameras = registry()
    minio = Minio(
        os.getenv("MINIO_ENDPOINT", "minio:9000"),
        access_key=os.environ["MINIO_ACCESS_KEY"],
        secret_key=os.environ["MINIO_SECRET_KEY"],
        secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
    )
    if not minio.bucket_exists(BUCKET):
        minio.make_bucket(BUCKET)

    while True:
        messages = client.xreadgroup(GROUP, CONSUMER, {STREAM: ">"}, count=1, block=5000)
        if not messages:
            continue
        for _, entries in messages:
            for message_id, fields in entries:
                try:
                    payload = json.loads(fields["payload"])
                    payload.setdefault("candidate_id", message_id.replace("-", "")[:36])
                    process(payload, gateways, cameras, minio)
                    client.xack(STREAM, GROUP, message_id)
                except Exception:
                    LOG.exception("media processing failed message=%s", message_id)
                    time.sleep(2)


if __name__ == "__main__":
    main()
