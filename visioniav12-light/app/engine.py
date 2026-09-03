from __future__ import annotations

import base64
import json
import logging
import os
import signal
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

import cv2
import numpy as np
import redis
import supervision as sv
from ultralytics import YOLO

from geometry import bottom_center
from state_monitor import DogStateMonitor

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOG = logging.getLogger("vision-light-detector")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
PREFIX = os.getenv("REDIS_PREFIX", "vl:")
FRAME_STREAM = f"{PREFIX}frames"
DETECTION_STREAM = f"{PREFIX}detections"
CANDIDATE_STREAM = f"{PREFIX}candidates"
HEALTH_KEY = f"{PREFIX}health:detector"

MODEL_PATH = os.getenv("YOLO_MODEL", "/models/yolo11n.pt")
DEVICE = os.getenv("YOLO_DEVICE", "0")
IMGSZ = int(os.getenv("YOLO_IMGSZ", "640"))
BATCH_SIZE = int(os.getenv("YOLO_BATCH_SIZE", "16"))
BATCH_WAIT_S = float(os.getenv("YOLO_BATCH_WAIT_MS", "120")) / 1000.0
CONFIDENCE = float(os.getenv("YOLO_CONFIDENCE", "0.25"))
MAX_FRAME_AGE_S = float(os.getenv("MAX_FRAME_AGE_S", "10"))
DETECT_FPS = float(os.getenv("DETECT_FPS", "2"))

RUNNING = True


def stop_handler(*_: object) -> None:
    global RUNNING
    RUNNING = False


@dataclass(slots=True)
class FrameEnvelope:
    message_id: str
    camera_id: str
    capture_ts: float
    image: np.ndarray


def decode_frame(message_id: bytes, fields: dict[bytes, bytes]) -> FrameEnvelope | None:
    try:
        camera_id = fields[b"camera_id"].decode()
        capture_ts = float(fields[b"capture_ts"].decode())
        age = time.time() - capture_ts
        if age > MAX_FRAME_AGE_S or age < -5:
            return None
        raw = base64.b64decode(fields[b"jpeg_b64"], validate=True)
        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("OpenCV could not decode JPEG")
        return FrameEnvelope(message_id.decode(), camera_id, capture_ts, image)
    except Exception:
        LOG.exception("invalid frame message id=%r", message_id)
        return None


def normalized_bbox(xyxy: np.ndarray, width: int, height: int) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = map(float, xyxy)
    return (
        max(0.0, min(1.0, x1 / width)),
        max(0.0, min(1.0, y1 / height)),
        max(0.0, min(1.0, x2 / width)),
        max(0.0, min(1.0, y2 / height)),
    )


def main() -> None:
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)

    client = redis.Redis.from_url(REDIS_URL, decode_responses=False)
    client.ping()

    LOG.info("loading model=%s device=%s imgsz=%s", MODEL_PATH, DEVICE, IMGSZ)
    model = YOLO(MODEL_PATH)
    class_names: dict[int, str] = {int(k): str(v) for k, v in model.names.items()}
    LOG.info("model classes=%s", class_names)

    trackers: dict[str, sv.ByteTrack] = {}
    dog_monitor = DogStateMonitor()
    last_id = "$"
    latest: dict[str, FrameEnvelope] = {}
    batch_started_at = time.monotonic()

    while RUNNING:
        response = client.xread({FRAME_STREAM: last_id}, count=max(BATCH_SIZE * 4, 32), block=50)
        for _, messages in response:
            for message_id, fields in messages:
                last_id = message_id.decode()
                envelope = decode_frame(message_id, fields)
                if envelope is not None:
                    latest[envelope.camera_id] = envelope

        due = len(latest) >= BATCH_SIZE or (latest and time.monotonic() - batch_started_at >= BATCH_WAIT_S)
        if not due:
            client.setex(HEALTH_KEY, 15, str(time.time()))
            continue

        selected = sorted(latest.values(), key=lambda item: item.capture_ts)[:BATCH_SIZE]
        for item in selected:
            latest.pop(item.camera_id, None)
        batch_started_at = time.monotonic()

        images = [item.image for item in selected]
        started = time.perf_counter()
        try:
            results = model.predict(
                source=images,
                imgsz=IMGSZ,
                conf=CONFIDENCE,
                device=DEVICE,
                verbose=False,
                stream=False,
            )
        except Exception:
            LOG.exception("YOLO inference failed")
            time.sleep(1)
            continue
        inference_ms = (time.perf_counter() - started) * 1000.0

        for item, result in zip(selected, results, strict=True):
            height, width = item.image.shape[:2]
            tracker = trackers.setdefault(item.camera_id, sv.ByteTrack(frame_rate=max(1, int(DETECT_FPS))))
            detections = sv.Detections.from_ultralytics(result)
            tracked = tracker.update_with_detections(detections)

            person_centers: list[tuple[int, tuple[float, float]]] = []
            payload_detections: list[dict] = []

            for index in range(len(tracked)):
                class_id = int(tracked.class_id[index]) if tracked.class_id is not None else -1
                class_name = class_names.get(class_id, f"class_{class_id}")
                confidence = float(tracked.confidence[index]) if tracked.confidence is not None else 0.0
                track_id = int(tracked.tracker_id[index]) if tracked.tracker_id is not None else -1
                bbox = normalized_bbox(tracked.xyxy[index], width, height)
                anchor_norm = bottom_center(bbox)
                anchor_px = (anchor_norm[0] * width, anchor_norm[1] * height)

                detection = {
                    "camera_id": item.camera_id,
                    "capture_ts": item.capture_ts,
                    "class_id": class_id,
                    "class_name": class_name,
                    "confidence": confidence,
                    "track_id": track_id,
                    "bbox": bbox,
                    "bottom_center": anchor_norm,
                }
                payload_detections.append(detection)

                if class_name == "person" and track_id >= 0:
                    person_centers.append((track_id, anchor_px))
                elif class_name == "dog" and track_id >= 0:
                    candidates = dog_monitor.observe_dog(
                        camera_id=item.camera_id,
                        track_id=track_id,
                        bbox=bbox,
                        frame_width=width,
                        frame_height=height,
                        timestamp=item.capture_ts,
                    )
                    for candidate in candidates:
                        client.xadd(
                            CANDIDATE_STREAM,
                            {"payload": json.dumps(candidate, separators=(",", ":"))},
                            maxlen=100_000,
                            approximate=True,
                        )

            for candidate in dog_monitor.update_people(item.camera_id, person_centers, item.capture_ts):
                client.xadd(
                    CANDIDATE_STREAM,
                    {"payload": json.dumps(candidate, separators=(",", ":"))},
                    maxlen=100_000,
                    approximate=True,
                )

            output = {
                "contract": "vision.light.detections.v1",
                "camera_id": item.camera_id,
                "capture_ts": item.capture_ts,
                "processed_at": datetime.now(timezone.utc).isoformat(),
                "frame_age_s": max(0.0, time.time() - item.capture_ts),
                "frame_width": width,
                "frame_height": height,
                "model": MODEL_PATH,
                "model_classes": class_names,
                "batch_size": len(selected),
                "batch_inference_ms": inference_ms,
                "detections": payload_detections,
            }
            client.xadd(
                DETECTION_STREAM,
                {"payload": json.dumps(output, separators=(",", ":"))},
                maxlen=100_000,
                approximate=True,
            )

        client.hset(
            HEALTH_KEY,
            mapping={
                "timestamp": time.time(),
                "batch_size": len(selected),
                "inference_ms": inference_ms,
                "latest_pending": len(latest),
            },
        )
        client.expire(HEALTH_KEY, 15)

    LOG.info("detector stopped")


if __name__ == "__main__":
    main()
