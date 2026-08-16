import base64
import json
import os
import time
import urllib.error
import urllib.request

import psycopg
import redis
from minio import Minio
from psycopg.rows import dict_row

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://vision:vision@postgres:5432/vision")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
INPUT_STREAM = os.getenv("DETECTION_INPUT_STREAM", "vision:ingestion:events")
OUTPUT_STREAM = os.getenv("DETECTION_OUTPUT_STREAM", "vision:detection:results")
CONSUMER_GROUP = os.getenv("DETECTION_CONSUMER_GROUP", "vision-detection")
CONSUMER_NAME = os.getenv("DETECTION_CONSUMER_NAME", "detector-1")
BACKEND = os.getenv("VISION_DETECTOR_BACKEND", "disabled").lower()
DETECTOR_HTTP_URL = os.getenv("DETECTOR_HTTP_URL", "")
DETECTOR_MODEL_KEY = os.getenv("DETECTOR_MODEL_KEY", "")
DETECTOR_MODEL_VERSION = os.getenv("DETECTOR_MODEL_VERSION", "")
DETECTOR_HTTP_TIMEOUT = float(os.getenv("DETECTOR_HTTP_TIMEOUT", "20"))
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "visionminio")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "vision-evidence")

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


def load_event(event_id: str):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM ingestion_events WHERE id=%s", (event_id,))
        return cur.fetchone()


def load_snapshot(object_key: str | None) -> bytes | None:
    if not object_key:
        return None
    obj = minio.get_object(MINIO_BUCKET, object_key)
    try:
        return obj.read()
    finally:
        obj.close()
        obj.release_conn()


def call_backend(image: bytes | None, event: dict) -> tuple[list[dict], float]:
    if BACKEND == "disabled":
        raise RuntimeError("MODEL_REQUIRED: VISION_DETECTOR_BACKEND is disabled")
    if BACKEND != "http":
        raise RuntimeError(f"unsupported detector backend: {BACKEND}")
    if not DETECTOR_HTTP_URL:
        raise RuntimeError("MODEL_REQUIRED: DETECTOR_HTTP_URL is not configured")
    payload = {
        "contract": "vision.detector.v1",
        "bbox_space": "normalized_xyxy_0_1",
        "event_id": str(event["id"]),
        "camera_id": str(event["camera_id"]) if event.get("camera_id") else None,
        "event_name": event["event_name"],
        "processing_mode": event["processing_mode"],
        "image_base64": base64.b64encode(image).decode("ascii") if image else None,
        "model_key": DETECTOR_MODEL_KEY or None,
        "model_version": DETECTOR_MODEL_VERSION or None,
    }
    req = urllib.request.Request(
        DETECTOR_HTTP_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=DETECTOR_HTTP_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        raise RuntimeError(f"detector backend request failed: {exc}") from exc
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if body.get("contract") != "vision.detector.v1" or body.get("ok") is not True:
        raise RuntimeError(str(body.get("error") or "invalid detector response"))
    if body.get("bbox_space") not in (None, "normalized_xyxy_0_1"):
        raise RuntimeError("detector bbox_space must be normalized_xyxy_0_1")
    detections = body.get("detections")
    if not isinstance(detections, list):
        raise RuntimeError("detector response detections must be a list")
    normalized = []
    for item in detections:
        if not isinstance(item, dict):
            continue
        cls = str(item.get("class", "")).strip()
        confidence = float(item.get("confidence", 0))
        bbox = item.get("bbox")
        if not cls or confidence < 0 or confidence > 1 or not isinstance(bbox, list) or len(bbox) != 4:
            continue
        try:
            b = [float(x) for x in bbox]
        except (TypeError, ValueError):
            continue
        if any(x < 0 or x > 1 for x in b) or b[0] >= b[2] or b[1] >= b[3]:
            continue
        normalized.append({"class": cls, "confidence": confidence, "bbox": b, "track_id": item.get("track_id")})
    return normalized, elapsed_ms


def persist(event: dict, status: str, detections: list[dict], inference_ms: float | None, error: str | None):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO detection_results(ingestion_event_id,camera_id,backend,model_key,model_version,status,detections,inference_ms,error)
            VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
            ON CONFLICT(ingestion_event_id) DO UPDATE SET
              backend=excluded.backend, model_key=excluded.model_key, model_version=excluded.model_version,
              status=excluded.status, detections=excluded.detections, inference_ms=excluded.inference_ms, error=excluded.error
            RETURNING *
            """,
            (event["id"], event.get("camera_id"), BACKEND, DETECTOR_MODEL_KEY or None, DETECTOR_MODEL_VERSION or None, status, json.dumps(detections), inference_ms, error),
        )
        result = cur.fetchone()
        cur.execute("UPDATE ingestion_events SET queue_status=%s,error=%s WHERE id=%s", ("DONE" if status == "SUCCESS" else "FAILED", error, event["id"]))
        conn.commit()
    r.xadd(OUTPUT_STREAM, {"detection_result_id": str(result["id"]), "ingestion_event_id": str(event["id"]), "status": status}, maxlen=100000, approximate=True)


def process(event_id: str):
    event = load_event(event_id)
    if not event:
        return
    image = load_snapshot(event.get("snapshot_object_key"))
    try:
        detections, elapsed = call_backend(image, event)
        persist(event, "SUCCESS", detections, elapsed, None)
    except RuntimeError as exc:
        message = str(exc)
        status = "BLOCKED_MODEL" if message.startswith("MODEL_REQUIRED:") else "FAILED"
        persist(event, status, [], None, message)


def main():
    ensure_group()
    while True:
        rows = r.xreadgroup(CONSUMER_GROUP, CONSUMER_NAME, {INPUT_STREAM: ">"}, count=8, block=5000)
        for _, messages in rows:
            for message_id, fields in messages:
                event_id = fields.get("ingestion_event_id")
                try:
                    if event_id:
                        process(event_id)
                    r.xack(INPUT_STREAM, CONSUMER_GROUP, message_id)
                except Exception as exc:
                    print(json.dumps({"level": "error", "message_id": message_id, "error": str(exc)}), flush=True)


if __name__ == "__main__":
    main()
