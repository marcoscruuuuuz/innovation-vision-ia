from __future__ import annotations

import base64
import io
import json
import math
import os
import subprocess
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

import psycopg
import redis
from PIL import Image, ImageChops, ImageStat
from psycopg.rows import dict_row

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://vision:vision@postgres:5432/vision")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
INPUT_STREAM = os.getenv("TEMPORAL_INPUT_STREAM", "vision:rule:temporal")
OUTPUT_STREAM = os.getenv("TEMPORAL_OUTPUT_STREAM", "vision:rule:candidates")
CONSUMER_GROUP = os.getenv("TEMPORAL_CONSUMER_GROUP", "vision-temporal")
CONSUMER_NAME = os.getenv("TEMPORAL_CONSUMER_NAME", "temporal-1")
HOST_GATEWAY_NAME = os.getenv("VISION_HOST_GATEWAY", "host.docker.internal")
DETECTOR_HTTP_URL = os.getenv("DETECTOR_HTTP_URL", "")
SPECIALIST_HTTP_URL = os.getenv("TEMPORAL_SPECIALIST_HTTP_URL", "")
HTTP_TIMEOUT = float(os.getenv("TEMPORAL_HTTP_TIMEOUT", "20"))
MAX_BURST_SECONDS = int(os.getenv("TEMPORAL_MAX_BURST_SECONDS", "20"))
DEFAULT_FPS = int(os.getenv("TEMPORAL_SAMPLE_FPS", "2"))
MAX_MODEL_FRAMES = int(os.getenv("TEMPORAL_MAX_MODEL_FRAMES", "10"))

SPECIALIST_ENGINES = {
    "detector_pose_temporal_vlm_review",
    "child_classifier_object_association",
    "child_classifier_pose_tracker",
    "child_person_ball_association",
    "child_person_kite_temporal",
    "vehicle_plate_detector_ocr_temporal_vote",
}

r = redis.Redis.from_url(REDIS_URL, decode_responses=True)


def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def ensure_group():
    try:
        r.xgroup_create(INPUT_STREAM, CONSUMER_GROUP, id="0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def reachable_uri(uri: str) -> str:
    parsed = urlsplit(uri)
    if parsed.scheme.lower() != "rtsp":
        raise RuntimeError("NO_VIDEO: active route is not RTSP")
    if (parsed.hostname or "") not in {"127.0.0.1", "localhost", "0.0.0.0"}:
        return uri
    auth = ""
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth += f":{parsed.password}"
        auth += "@"
    port = f":{parsed.port}" if parsed.port else ""
    return urlunsplit((parsed.scheme, f"{auth}{HOST_GATEWAY_NAME}{port}", parsed.path, parsed.query, parsed.fragment))


def parse_jpegs(data: bytes) -> list[bytes]:
    frames = []
    pos = 0
    while True:
        start = data.find(b"\xff\xd8", pos)
        if start < 0:
            break
        end = data.find(b"\xff\xd9", start + 2)
        if end < 0:
            break
        frames.append(data[start : end + 2])
        pos = end + 2
    return frames


def capture_frames(uri: str, seconds: int, fps: int) -> list[bytes]:
    seconds = max(2, min(MAX_BURST_SECONDS, seconds))
    fps = max(1, min(5, fps))
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp",
        "-rw_timeout", "5000000", "-i", reachable_uri(uri), "-t", str(seconds), "-an",
        "-vf", f"fps={fps}", "-q:v", "5", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, check=True, timeout=seconds + 20, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise RuntimeError("NO_VIDEO: temporal RTSP burst failed") from exc
    frames = parse_jpegs(proc.stdout)
    if len(frames) < 2:
        raise RuntimeError("NO_VIDEO: temporal burst returned fewer than 2 frames")
    return frames


def point_in_polygon(x: float, y: float, polygon: list) -> bool:
    inside = False
    if len(polygon) < 3:
        return False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = float(polygon[i][0]), float(polygon[i][1])
        xj, yj = float(polygon[j][0]), float(polygon[j][1])
        if ((yi > y) != (yj > y)) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi:
            inside = not inside
        j = i
    return inside


def center(det: dict) -> tuple[float, float]:
    b = det["bbox"]
    return ((float(b[0]) + float(b[2])) / 2, (float(b[1]) + float(b[3])) / 2)


def in_geometry(det: dict, geometry: dict | None) -> bool:
    if not geometry:
        return True
    pts = geometry.get("points") or []
    if geometry.get("type") in {"polygon", "rectangle", "door_roi_with_auto_line"}:
        x, y = center(det)
        return point_in_polygon(x, y, pts)
    return True


def normalized_detections(body: dict) -> list[dict]:
    if body.get("contract") != "vision.detector.v1" or body.get("ok") is not True:
        raise RuntimeError(str(body.get("error") or "invalid detector response"))
    result = []
    for item in body.get("detections") or []:
        try:
            bbox = [float(x) for x in item["bbox"]]
            conf = float(item.get("confidence", 0))
        except (KeyError, TypeError, ValueError):
            continue
        if len(bbox) != 4 or any(x < 0 or x > 1 for x in bbox) or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
            continue
        result.append({"class": str(item.get("class", "")), "confidence": conf, "bbox": bbox, "track_id": item.get("track_id")})
    return result


def call_detector(frame: bytes, context: dict, frame_index: int) -> list[dict]:
    if not DETECTOR_HTTP_URL:
        raise RuntimeError("MODEL_REQUIRED: DETECTOR_HTTP_URL is not configured")
    payload = {
        "contract": "vision.detector.v1",
        "bbox_space": "normalized_xyxy_0_1",
        "event_id": str(context["ingestion_event_id"]),
        "camera_id": str(context["camera_id"]),
        "event_name": context["event_type"],
        "processing_mode": "TEMPORAL_BURST",
        "frame_index": frame_index,
        "image_base64": base64.b64encode(frame).decode("ascii"),
        "requested_engine": context["engine"],
    }
    req = urllib.request.Request(DETECTOR_HTTP_URL, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return normalized_detections(json.loads(resp.read().decode()))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        raise RuntimeError(f"MODEL_REQUIRED: detector backend failed: {exc}") from exc


def sample_for_models(frames: list[bytes]) -> list[bytes]:
    if len(frames) <= MAX_MODEL_FRAMES:
        return frames
    step = (len(frames) - 1) / (MAX_MODEL_FRAMES - 1)
    return [frames[round(i * step)] for i in range(MAX_MODEL_FRAMES)]


def call_specialist(frames: list[bytes], context: dict) -> dict:
    if not SPECIALIST_HTTP_URL:
        raise RuntimeError(f"MODEL_REQUIRED: specialist backend required for {context['engine']}")
    selected = sample_for_models(frames)
    payload = {
        "contract": "vision.temporal.v1",
        "engine": context["engine"],
        "event_type": context["event_type"],
        "camera_id": str(context["camera_id"]),
        "geometry": context["geometry"],
        "parameters": context["parameters"],
        "model_requirements": context["model_requirements"],
        "frames_base64": [base64.b64encode(x).decode("ascii") for x in selected],
    }
    req = urllib.request.Request(SPECIALIST_HTTP_URL, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=max(HTTP_TIMEOUT, 45)) as resp:
            body = json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        raise RuntimeError(f"MODEL_REQUIRED: specialist backend failed: {exc}") from exc
    if body.get("contract") != "vision.temporal.v1" or body.get("ok") is not True:
        raise RuntimeError(str(body.get("error") or "invalid specialist response"))
    return {
        "match": bool(body.get("match")),
        "confidence": float(body.get("confidence", 0.0)),
        "details": body.get("details") or {},
        "review": bool(body.get("review", False)),
    }


def crop_box(image: Image.Image, geometry: dict | None, side: str | None = None) -> Image.Image:
    if not geometry or not geometry.get("points"):
        return image
    pts = geometry["points"]
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    w, h = image.size
    left, right = max(0, int(min(xs) * w)), min(w, int(max(xs) * w))
    top, bottom = max(0, int(min(ys) * h)), min(h, int(max(ys) * h))
    if right <= left or bottom <= top:
        return image
    if side == "left":
        right = left + max(1, (right - left) // 2)
    elif side == "right":
        left = right - max(1, (right - left) // 2)
    return image.crop((left, top, right, bottom))


def image_diff(a: bytes, b: bytes, geometry: dict | None, side: str | None = None) -> float:
    ia = crop_box(Image.open(io.BytesIO(a)).convert("L"), geometry, side).resize((160, 90))
    ib = crop_box(Image.open(io.BytesIO(b)).convert("L"), geometry, side).resize((160, 90))
    diff = ImageChops.difference(ia, ib)
    return float(ImageStat.Stat(diff).mean[0]) / 255.0


def line_side(p, a, b):
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def first_crossing(points: list[tuple[float, float]], line: list) -> int | None:
    if len(line) != 2 or len(points) < 2:
        return None
    prev = line_side(points[0], line[0], line[1])
    for idx in range(1, len(points)):
        cur = line_side(points[idx], line[0], line[1])
        if prev == 0 or cur == 0 or (prev < 0 < cur) or (prev > 0 > cur):
            return idx
        prev = cur
    return None


def detections_over_time(frames: list[bytes], context: dict) -> list[list[dict]]:
    return [call_detector(frame, context, idx) for idx, frame in enumerate(sample_for_models(frames))]


def best_track(series: list[list[dict]], classes: set[str], geometry: dict | None) -> tuple[list[tuple[float, float]], list[float]]:
    points, confidences = [], []
    last = None
    for detections in series:
        matches = [d for d in detections if (not classes or d["class"] in classes) and in_geometry(d, geometry)]
        if not matches:
            continue
        if last is None:
            chosen = max(matches, key=lambda d: d["confidence"])
        else:
            chosen = min(matches, key=lambda d: math.dist(center(d), last))
        last = center(chosen)
        points.append(last)
        confidences.append(float(chosen["confidence"]))
    return points, confidences


def eval_builtin(frames: list[bytes], context: dict) -> dict:
    engine = context["engine"]
    params = context["parameters"]
    geometry = context["geometry"]
    event_type = context["event_type"]

    if engine in SPECIALIST_ENGINES:
        return call_specialist(frames, context)

    if engine in {"door_structural_change_temporal", "motion_scene_change_detector"}:
        threshold = float(params.get("scene_change_threshold", 0.08 if engine == "door_structural_change_temporal" else 0.05))
        side = params.get("line_side") if engine == "door_structural_change_temporal" else None
        tail = frames[max(1, len(frames) // 2) :]
        diffs = [image_diff(frames[0], f, geometry, side) for f in tail]
        persistent = bool(diffs) and sum(x >= threshold for x in diffs) >= max(1, int(len(diffs) * 0.7))
        confidence = min(0.99, max(diffs, default=0.0) / max(threshold, 1e-6) * 0.6) if persistent else 0.0
        return {"match": persistent, "confidence": confidence, "details": {"diffs": diffs, "threshold": threshold, "line_side": side}, "review": False}

    series = detections_over_time(frames, context)
    classes = {str(x) for x in params.get("classes", []) if str(x)}

    if engine in {"tracker_temporal", "person_tracker"}:
        if engine == "person_tracker":
            classes = {"person"}
        points, confs = best_track(series, classes, None)
        gtype = (geometry or {}).get("type")
        lines = (geometry or {}).get("points") or []
        if gtype == "double_line" and len(lines) == 4:
            c1, c2 = first_crossing(points, lines[:2]), first_crossing(points, lines[2:4])
            expected = params.get("crossing_order", "forward")
            match = c1 is not None and c2 is not None and ((c1 <= c2) if expected == "forward" else (c2 <= c1))
            details = {"line1_cross": c1, "line2_cross": c2, "order": expected}
        else:
            cross = first_crossing(points, lines[:2]) if len(lines) >= 2 else None
            match = cross is not None
            details = {"cross": cross}
        return {"match": match, "confidence": sum(confs) / len(confs) if match and confs else 0.0, "details": details, "review": False}

    if engine == "vehicle_tracker_direction":
        vehicle_classes = classes or {"car", "truck", "bus", "motorcycle", "vehicle"}
        points, confs = best_track(series, vehicle_classes, geometry)
        if len(points) < 3:
            return {"match": False, "confidence": 0.0, "details": {"reason": "insufficient_track"}, "review": False}
        allowed = params.get("allowed_direction") or [1.0, 0.0]
        vx, vy = points[-1][0] - points[0][0], points[-1][1] - points[0][1]
        norm = math.hypot(vx, vy)
        anorm = math.hypot(float(allowed[0]), float(allowed[1])) or 1.0
        dot = (vx * float(allowed[0]) + vy * float(allowed[1])) / ((norm or 1.0) * anorm)
        min_motion = float(params.get("min_track_displacement", 0.04))
        match = norm >= min_motion and dot < float(params.get("wrong_way_dot_threshold", -0.35))
        return {"match": match, "confidence": sum(confs) / len(confs) if match else 0.0, "details": {"displacement": norm, "direction_dot": dot, "allowed_direction": allowed}, "review": False}

    if engine == "vehicle_tracker_temporal":
        vehicle_classes = classes or {"car", "truck", "bus", "motorcycle", "vehicle"}
        points, confs = best_track(series, vehicle_classes, geometry)
        if len(points) < max(3, len(series) // 2):
            return {"match": False, "confidence": 0.0, "details": {"reason": "vehicle_not_persistent"}, "review": False}
        movement = math.dist(points[0], points[-1])
        max_move = float(params.get("max_stationary_displacement", 0.04))
        match = movement <= max_move
        return {"match": match, "confidence": sum(confs) / len(confs) if match else 0.0, "details": {"movement": movement, "max": max_move}, "review": False}

    if engine == "person_absence_temporal":
        seen = []
        for ds in series:
            seen.append(any(d["class"] == "person" and in_geometry(d, geometry) for d in ds))
        match = bool(seen) and not any(seen)
        return {"match": match, "confidence": 0.95 if match else 0.0, "details": {"person_seen": seen}, "review": False}

    if engine == "person_pose_inactivity":
        points, confs = best_track(series, {"person"}, geometry)
        if len(points) < max(3, len(series) // 2):
            return {"match": False, "confidence": 0.0, "details": {"reason": "person_not_persistent"}, "review": False}
        max_move = max(math.dist(points[0], p) for p in points[1:]) if len(points) > 1 else 0.0
        threshold = float(params.get("max_inactivity_displacement", 0.03))
        match = max_move <= threshold
        return {"match": match, "confidence": sum(confs) / len(confs) if match else 0.0, "details": {"max_displacement": max_move, "threshold": threshold}, "review": False}

    if engine == "detector_tracker_temporal" and event_type == "cachorro_solto":
        dog_classes = {"dog", "cachorro"}
        dog_hits, tutor_hits, confidences = 0, 0, []
        for ds in series:
            dogs = [d for d in ds if d["class"] in dog_classes and in_geometry(d, geometry)]
            people = [d for d in ds if d["class"] == "person"]
            if dogs:
                dog_hits += 1
                confidences.extend(d["confidence"] for d in dogs)
                for dog in dogs:
                    if any(math.dist(center(dog), center(p)) <= float(params.get("tutor_near_distance", 0.22)) for p in people):
                        tutor_hits += 1
                        break
        match = dog_hits >= max(2, len(series) // 3)
        return {"match": match, "confidence": sum(confidences) / len(confidences) if match and confidences else 0.0, "details": {"dog_frames": dog_hits, "tutor_near_frames": tutor_hits, "with_tutor": tutor_hits > 0}, "review": False}

    if engine == "person_object_abandonment":
        object_classes = set(params.get("object_classes") or ["backpack", "handbag", "suitcase", "bottle", "cup", "bowl", "bag"])
        first_people = any(d["class"] == "person" for d in series[0]) if series else False
        last_objects = [d for d in series[-1] if d["class"] in object_classes and in_geometry(d, geometry)] if series else []
        last_people = [d for d in series[-1] if d["class"] == "person" and in_geometry(d, geometry)] if series else []
        match = first_people and bool(last_objects) and not last_people
        conf = max((d["confidence"] for d in last_objects), default=0.0)
        return {"match": match, "confidence": conf if match else 0.0, "details": {"objects": [d["class"] for d in last_objects], "person_left": not last_people}, "review": False}

    raise RuntimeError(f"MODEL_REQUIRED: temporal engine {engine} requires a specialist adapter")


def policy_for(cur, event_type: str):
    cur.execute("SELECT * FROM event_confidence_policies WHERE event_type=%s", (event_type,))
    row = cur.fetchone()
    if row:
        return row
    cur.execute("INSERT INTO event_confidence_policies(event_type) VALUES (%s) ON CONFLICT(event_type) DO UPDATE SET event_type=excluded.event_type RETURNING *", (event_type,))
    return cur.fetchone()


def classify_action(confidence: float, policy: dict) -> str:
    if confidence < float(policy["min_log_confidence"]):
        return "DROP"
    if confidence < float(policy["review_from_confidence"]):
        return "TEXT_LOG"
    if confidence < float(policy["evidence_from_confidence"]):
        return "HUMAN_REVIEW"
    return "EVIDENCE_LOG"


def load_context(evaluation_id: str):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id AS evaluation_id,e.detection_result_id,e.event_rule_version_id,
                   r.event_type,COALESCE(r.display_label,r.event_type) AS display_name,
                   v.geometry,v.parameters,v.model_requirements,v.certification_status,
                   d.camera_id,i.id AS ingestion_event_id,i.condominium_id,i.occurred_at,
                   sr.local_uri
              FROM rule_evaluations e
              JOIN event_rule_versions v ON v.id=e.event_rule_version_id
              JOIN event_rules r ON r.id=v.event_rule_id
              JOIN detection_results d ON d.id=e.detection_result_id
              JOIN ingestion_events i ON i.id=d.ingestion_event_id
              LEFT JOIN LATERAL (
                SELECT x.local_uri FROM stream_routes x
                 WHERE x.camera_id=d.camera_id AND x.state='ACTIVE' AND x.deactivated_at IS NULL
                 ORDER BY x.generation DESC LIMIT 1
              ) sr ON true
             WHERE e.id=%s
            """,
            (evaluation_id,),
        )
        return cur.fetchone()


def persist(context: dict, result: dict):
    match = bool(result.get("match"))
    confidence = max(0.0, min(1.0, float(result.get("confidence", 0.0)))) if match else None
    outcome = "MATCH" if match else "NO_MATCH"
    details = result.get("details") or {}
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE rule_evaluations SET outcome=%s,confidence=%s,details=%s::jsonb WHERE id=%s",
            (outcome, confidence, json.dumps(details), context["evaluation_id"]),
        )
        candidate_id = None
        action = None
        if match:
            policy = policy_for(cur, context["event_type"])
            action = "HUMAN_REVIEW" if result.get("review") else classify_action(confidence, policy)
            review_status = "REJECTED" if action == "DROP" else ("HUMAN_REVIEW" if action == "HUMAN_REVIEW" else "AI_APPROVED")
            cur.execute(
                """
                INSERT INTO event_candidates(
                    condominium_id,camera_id,event_rule_version_id,detected_at,confidence,payload,review_status,
                    ingestion_event_id,detection_result_id,pipeline_action
                ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)
                RETURNING id
                """,
                (
                    context["condominium_id"],context["camera_id"],context["event_rule_version_id"],context["occurred_at"],confidence,
                    json.dumps({"rule_evaluation_id": str(context["evaluation_id"]),"event_type":context["event_type"],"certification_status":context["certification_status"],"temporal":True,"details":details}),
                    review_status,context["ingestion_event_id"],context["detection_result_id"],action,
                ),
            )
            candidate_id = cur.fetchone()["id"]
        conn.commit()
    if candidate_id:
        r.xadd(OUTPUT_STREAM,{"candidate_id":str(candidate_id),"event_type":context["event_type"],"pipeline_action":action},maxlen=100000,approximate=True)


def persist_blocked(context: dict, outcome: str, error: str):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE rule_evaluations SET outcome=%s,confidence=NULL,details=%s::jsonb WHERE id=%s",
            (outcome, json.dumps({"error": error[:1500], "engine": context.get("engine")}), context["evaluation_id"]),
        )
        conn.commit()


def process(evaluation_id: str, engine_hint: str | None = None):
    context = load_context(evaluation_id)
    if not context:
        return
    context["geometry"] = context["geometry"] if isinstance(context["geometry"], dict) else json.loads(context["geometry"] or "null")
    context["parameters"] = context["parameters"] if isinstance(context["parameters"], dict) else json.loads(context["parameters"] or "{}")
    context["model_requirements"] = context["model_requirements"] if isinstance(context["model_requirements"], dict) else json.loads(context["model_requirements"] or "{}")
    context["engine"] = str(context["parameters"].get("engine") or context["model_requirements"].get("engine") or engine_hint or "snapshot_detector")
    if not context.get("local_uri"):
        persist_blocked(context, "NO_VIDEO", "camera has no active RTSP route")
        return
    seconds = int(context["parameters"].get("duration_seconds") or context["parameters"].get("open_persistence_seconds") or 10)
    fps = int(context["parameters"].get("sample_fps") or DEFAULT_FPS)
    try:
        frames = capture_frames(context["local_uri"], seconds, fps)
        result = eval_builtin(frames, context)
        persist(context, result)
    except RuntimeError as exc:
        message = str(exc)
        if message.startswith("MODEL_REQUIRED:"):
            persist_blocked(context, "MODEL_REQUIRED", message)
        elif message.startswith("NO_VIDEO:"):
            persist_blocked(context, "NO_VIDEO", message)
        else:
            persist_blocked(context, "TEMPORAL_FAILED", message)


def main():
    ensure_group()
    while True:
        rows = r.xreadgroup(CONSUMER_GROUP, CONSUMER_NAME, {INPUT_STREAM: ">"}, count=4, block=5000)
        for _, messages in rows:
            for message_id, fields in messages:
                try:
                    evaluation_id = fields.get("rule_evaluation_id")
                    if evaluation_id:
                        process(evaluation_id, fields.get("engine"))
                    r.xack(INPUT_STREAM, CONSUMER_GROUP, message_id)
                except Exception as exc:
                    print(json.dumps({"level":"error","message_id":message_id,"error":str(exc)}), flush=True)
                    time.sleep(0.2)


if __name__ == "__main__":
    main()
