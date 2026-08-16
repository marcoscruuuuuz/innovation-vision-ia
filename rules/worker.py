import json
import os

import psycopg
import redis
from psycopg.rows import dict_row

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://vision:vision@postgres:5432/vision")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
INPUT_STREAM = os.getenv("RULE_INPUT_STREAM", "vision:detection:results")
OUTPUT_STREAM = os.getenv("RULE_OUTPUT_STREAM", "vision:rule:candidates")
TEMPORAL_STREAM = os.getenv("RULE_TEMPORAL_STREAM", "vision:rule:temporal")
CONSUMER_GROUP = os.getenv("RULE_CONSUMER_GROUP", "vision-rules")
CONSUMER_NAME = os.getenv("RULE_CONSUMER_NAME", "rule-1")

TEMPORAL_ENGINES = {
    "door_structural_change_temporal",
    "detector_tracker_temporal",
    "detector_pose_temporal_vlm_review",
    "tracker_temporal",
    "person_tracker",
    "child_classifier_object_association",
    "child_classifier_pose_tracker",
    "vehicle_tracker_temporal",
    "vehicle_tracker_direction",
    "motion_scene_change_detector",
    "child_person_ball_association",
    "child_person_kite_temporal",
    "vehicle_plate_detector_ocr_temporal_vote",
    "person_pose_inactivity",
    "person_absence_temporal",
    "person_object_abandonment",
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


def point_in_polygon(x: float, y: float, polygon: list) -> bool:
    inside = False
    n = len(polygon)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = float(polygon[i][0]), float(polygon[i][1])
        xj, yj = float(polygon[j][0]), float(polygon[j][1])
        intersects = ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi)
        if intersects:
            inside = not inside
        j = i
    return inside


def geometry_match(geometry: dict | None, bbox: list[float]) -> bool:
    if not geometry:
        return True
    gtype = geometry.get("type")
    points = geometry.get("points") or []
    cx = (float(bbox[0]) + float(bbox[2])) / 2.0
    cy = (float(bbox[1]) + float(bbox[3])) / 2.0
    if gtype in ("polygon", "rectangle", "door_roi_with_auto_line"):
        return point_in_polygon(cx, cy, points)
    if gtype in ("line", "double_line", "trigger_line"):
        return False
    return True


def policy_for(cur, event_type: str):
    cur.execute("SELECT * FROM event_confidence_policies WHERE event_type=%s", (event_type,))
    row = cur.fetchone()
    if row:
        return row
    cur.execute(
        """
        INSERT INTO event_confidence_policies(event_type)
        VALUES (%s)
        ON CONFLICT(event_type) DO UPDATE SET event_type=excluded.event_type
        RETURNING *
        """,
        (event_type,),
    )
    return cur.fetchone()


def classify_action(confidence: float, policy: dict) -> str:
    if confidence < float(policy["min_log_confidence"]):
        return "DROP"
    if confidence < float(policy["review_from_confidence"]):
        return "TEXT_LOG"
    if confidence < float(policy["evidence_from_confidence"]):
        return "HUMAN_REVIEW"
    return "EVIDENCE_LOG"


def choose_detection(detections: list[dict], params: dict, geometry: dict | None):
    classes = {str(x) for x in params.get("classes", []) if str(x)}
    min_conf = float(params.get("min_confidence", 0.0))
    candidates = []
    for det in detections:
        cls = str(det.get("class", ""))
        conf = float(det.get("confidence", 0.0))
        bbox = det.get("bbox")
        if classes and cls not in classes:
            continue
        if conf < min_conf or not isinstance(bbox, list) or len(bbox) != 4:
            continue
        if not geometry_match(geometry, bbox):
            continue
        candidates.append(det)
    if not candidates:
        return None
    return max(candidates, key=lambda x: float(x.get("confidence", 0.0)))


def process(result_id: str):
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT d.*, i.processing_mode, i.occurred_at, i.condominium_id, i.camera_id AS ingestion_camera_id,
                   i.id AS ingestion_event_id
              FROM detection_results d
              JOIN ingestion_events i ON i.id=d.ingestion_event_id
             WHERE d.id=%s
            """,
            (result_id,),
        )
        detection = cur.fetchone()
        if not detection:
            return
        camera_id = detection["camera_id"] or detection["ingestion_camera_id"]
        if not camera_id:
            return
        cur.execute(
            """
            SELECT r.id AS rule_id, r.event_type, r.enabled, v.id AS version_id,
                   v.geometry, v.parameters, v.model_requirements, v.certification_status
              FROM event_rules r
              JOIN event_rule_versions v ON v.event_rule_id=r.id AND v.version=r.active_version
             WHERE r.camera_id=%s AND r.enabled=true
             ORDER BY r.event_type
            """,
            (camera_id,),
        )
        rules = cur.fetchall()
        detections = detection["detections"] if isinstance(detection["detections"], list) else json.loads(detection["detections"] or "[]")
        temporal_jobs = []
        for rule in rules:
            params = rule["parameters"] if isinstance(rule["parameters"], dict) else json.loads(rule["parameters"] or "{}")
            geometry = rule["geometry"] if isinstance(rule["geometry"], dict) else json.loads(rule["geometry"] or "null")
            requirements = rule["model_requirements"] if isinstance(rule["model_requirements"], dict) else json.loads(rule["model_requirements"] or "{}")
            engine = str(params.get("engine") or requirements.get("engine") or "snapshot_detector")

            if engine in TEMPORAL_ENGINES or bool(params.get("requires_temporal")):
                outcome = "NEEDS_TEMPORAL"
                confidence = None
                details = {"processing_mode": detection["processing_mode"], "engine": engine, "snapshot_status": detection["status"]}
            elif detection["status"] == "BLOCKED_MODEL":
                outcome = "MODEL_REQUIRED"
                confidence = None
                details = {"error": detection["error"], "engine": engine}
            else:
                best = choose_detection(detections, params, geometry)
                outcome = "MATCH" if best else "NO_MATCH"
                confidence = float(best["confidence"]) if best else None
                details = {"detection": best} if best else {}

            cur.execute(
                """
                INSERT INTO rule_evaluations(detection_result_id,event_rule_version_id,outcome,confidence,details)
                VALUES (%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT(detection_result_id,event_rule_version_id) DO UPDATE SET
                  outcome=excluded.outcome, confidence=excluded.confidence, details=excluded.details
                RETURNING id
                """,
                (detection["id"], rule["version_id"], outcome, confidence, json.dumps(details)),
            )
            evaluation_id = cur.fetchone()["id"]
            if outcome == "NEEDS_TEMPORAL":
                temporal_jobs.append((evaluation_id, rule["event_type"], engine))
                continue
            if outcome != "MATCH":
                continue

            policy = policy_for(cur, rule["event_type"])
            action = classify_action(confidence, policy)
            review_status = "REJECTED" if action == "DROP" else ("HUMAN_REVIEW" if action == "HUMAN_REVIEW" else "AI_APPROVED")
            cur.execute(
                """
                INSERT INTO event_candidates(
                    condominium_id,camera_id,event_rule_version_id,detected_at,confidence,payload,review_status,
                    ingestion_event_id,detection_result_id,pipeline_action
                )
                VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)
                RETURNING id
                """,
                (
                    detection["condominium_id"], camera_id, rule["version_id"], detection["occurred_at"], confidence,
                    json.dumps({"rule_evaluation_id": str(evaluation_id), "event_type": rule["event_type"], "certification_status": rule["certification_status"], "details": details}),
                    review_status, detection["ingestion_event_id"], detection["id"], action,
                ),
            )
            candidate_id = cur.fetchone()["id"]
            r.xadd(OUTPUT_STREAM, {"candidate_id": str(candidate_id), "event_type": rule["event_type"], "pipeline_action": action}, maxlen=100000, approximate=True)
        conn.commit()

    for evaluation_id, event_type, engine in temporal_jobs:
        r.xadd(
            TEMPORAL_STREAM,
            {
                "rule_evaluation_id": str(evaluation_id),
                "detection_result_id": str(detection["id"]),
                "ingestion_event_id": str(detection["ingestion_event_id"]),
                "camera_id": str(camera_id),
                "event_type": event_type,
                "engine": engine,
            },
            maxlen=100000,
            approximate=True,
        )


def main():
    ensure_group()
    while True:
        rows = r.xreadgroup(CONSUMER_GROUP, CONSUMER_NAME, {INPUT_STREAM: ">"}, count=8, block=5000)
        for _, messages in rows:
            for message_id, fields in messages:
                try:
                    result_id = fields.get("detection_result_id")
                    if result_id:
                        process(result_id)
                    r.xack(INPUT_STREAM, CONSUMER_GROUP, message_id)
                except Exception as exc:
                    print(json.dumps({"level": "error", "message_id": message_id, "error": str(exc)}), flush=True)


if __name__ == "__main__":
    main()
