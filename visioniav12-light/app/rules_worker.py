from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import redis
from sqlalchemy import select

from geometry import DoubleLineState, crossed_line, distance, point_in_polygon
from models import Rule, SessionLocal, create_schema

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOG = logging.getLogger("vision-light-rules")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
PREFIX = os.getenv("REDIS_PREFIX", "vl:")
DETECTION_STREAM = f"{PREFIX}detections"
CANDIDATE_STREAM = f"{PREFIX}candidates"
GROUP = f"{PREFIX}rules"
CONSUMER = os.getenv("HOSTNAME", "rules-1")
RULE_REFRESH_S = float(os.getenv("RULE_REFRESH_S", "5"))

ANIMAL_CLASSES = {"dog", "cat"}
VEHICLE_CLASSES = {"car", "motorcycle", "bus", "truck"}


@dataclass
class TrackRuleState:
    previous_point: tuple[float, float] | None = None
    entered_at: float | None = None
    stationary_since: float | None = None
    last_point: tuple[float, float] | None = None
    last_seen: float = 0.0
    hits: int = 0
    line_state: DoubleLineState = field(default_factory=DoubleLineState)
    last_emitted_at: float = 0.0


class RuleRuntime:
    def __init__(self) -> None:
        self.rules: dict[str, list[Rule]] = {}
        self.loaded_at = 0.0
        self.track_states: dict[tuple[str, str, int], TrackRuleState] = defaultdict(TrackRuleState)
        self.guard_last_seen: dict[tuple[str, str], float] = {}

    def refresh(self) -> None:
        if time.time() - self.loaded_at < RULE_REFRESH_S:
            return
        with SessionLocal() as session:
            rows = session.scalars(
                select(Rule).where(Rule.enabled.is_(True), Rule.state == "PRODUCTION").order_by(Rule.version.desc())
            ).all()
            selected: dict[tuple[str, str], Rule] = {}
            for row in rows:
                selected.setdefault((row.camera_id, row.event_key), row)
            by_camera: dict[str, list[Rule]] = defaultdict(list)
            for row in selected.values():
                by_camera[row.camera_id].append(row)
            self.rules = dict(by_camera)
        self.loaded_at = time.time()

    @staticmethod
    def candidate(rule: Rule, detection: dict, occurred_at: float, confidence: float, reason: dict) -> dict:
        return {
            "event_key": rule.event_key,
            "camera_id": rule.camera_id,
            "rule_id": rule.id,
            "occurred_at": occurred_at,
            "confidence": confidence,
            "reason": reason,
            "certification_required": bool(rule.config.get("certification_required", False)),
        }

    @staticmethod
    def in_schedule(rule: Rule, timestamp: float) -> bool:
        schedule = rule.config.get("schedule") or {}
        start = schedule.get("start", "22:00")
        end = schedule.get("end", "06:00")
        zone = ZoneInfo(schedule.get("timezone", "America/Sao_Paulo"))
        local = datetime.fromtimestamp(timestamp, tz=zone).strftime("%H:%M")
        return local >= start or local < end if start > end else start <= local < end

    @staticmethod
    def geometry(rule: Rule) -> dict:
        return rule.geometry or {}

    @staticmethod
    def eligible_class(event_key: str, class_name: str) -> bool:
        if event_key in {"animal_em_geral", "animal_solto", "animal_com_tutor"}:
            return class_name in ANIMAL_CLASSES
        if event_key in {"pessoa_fora_horario_22h", "area_proibida", "muro_condominio", "porta_manutencao", "porteiro_fora_posto", "possivel_porteiro_dormindo", "porta_bloco_aberta", "entrada_vacuo", "saida_vacuo"}:
            return class_name == "person"
        if event_key in {"veiculo_parado_irregular", "veiculo_contramao", "linha_velocidade", "placa_detectada"}:
            return class_name in VEHICLE_CLASSES or class_name == "bicycle"
        if event_key == "pessoa_bicicleta_area_comum":
            return class_name == "bicycle"
        if event_key == "crianca_com_pipa":
            return class_name == "kite"
        if event_key == "criancas_jogando_bola":
            return class_name == "sports ball"
        if event_key.startswith("linha_perimetral"):
            return class_name in {"person", "dog", "cat", *VEHICLE_CLASSES}
        return False

    def emit_once(self, state: TrackRuleState, cooldown: float, now: float) -> bool:
        if now - state.last_emitted_at < cooldown:
            return False
        state.last_emitted_at = now
        return True

    def evaluate_detection(
        self,
        rule: Rule,
        detection: dict,
        all_detections: list[dict],
        frame_width: int,
        frame_height: int,
    ) -> list[dict]:
        event_key = rule.event_key
        class_name = detection["class_name"]
        if not self.eligible_class(event_key, class_name):
            return []

        track_id = int(detection.get("track_id", -1))
        if track_id < 0:
            return []
        now = float(detection["capture_ts"])
        point = tuple(map(float, detection["bottom_center"]))
        key = (rule.camera_id, event_key, track_id)
        state = self.track_states[key]
        state.hits += 1
        state.last_seen = now
        geometry = self.geometry(rule)
        config = rule.config or {}
        cooldown = float(config.get("cooldown_seconds", 30))
        results: list[dict] = []

        if event_key == "animal_em_geral":
            if state.hits >= int(config.get("min_hits", 3)) and self.emit_once(state, cooldown, now):
                results.append(self.candidate(rule, detection, now, detection["confidence"], {"class": class_name, "hits": state.hits}))
            return results

        if event_key in {"animal_solto", "animal_com_tutor"}:
            polygon = geometry.get("points") or []
            if polygon and not point_in_polygon(point, polygon):
                return []
            animal_px = (point[0] * frame_width, point[1] * frame_height)
            people = [item for item in all_detections if item.get("class_name") == "person"]
            nearest = min(
                (distance(animal_px, (float(item["bottom_center"][0]) * frame_width, float(item["bottom_center"][1]) * frame_height)) for item in people),
                default=10_000.0,
            )
            radius = float(config.get("owner_radius_px", 260))
            matched = nearest <= radius
            desired = matched if event_key == "animal_com_tutor" else not matched
            if desired and state.hits >= int(config.get("min_hits", 3)) and self.emit_once(state, cooldown, now):
                results.append(self.candidate(rule, detection, now, detection["confidence"], {"nearest_person_px": nearest, "owner_radius_px": radius}))
            return results

        if event_key == "pessoa_fora_horario_22h":
            if self.in_schedule(rule, now) and self.emit_once(state, cooldown, now):
                results.append(self.candidate(rule, detection, now, detection["confidence"], {"schedule": config.get("schedule", {})}))
            return results

        geometry_type = geometry.get("type")
        if geometry_type == "polygon":
            inside = point_in_polygon(point, geometry.get("points") or [])
            if inside:
                state.entered_at = state.entered_at or now
            else:
                state.entered_at = None
                state.previous_point = point
                return []
            dwell = float(config.get("dwell_seconds", 0))
            if state.entered_at and now - state.entered_at >= dwell:
                if event_key == "porta_bloco_aberta" and not config.get("door_state_source"):
                    # Fail closed: a person in the doorway is not proof that the door is open.
                    return []
                if self.emit_once(state, cooldown, now):
                    results.append(self.candidate(rule, detection, now, detection["confidence"], {"inside_roi": True, "dwell_seconds": now - state.entered_at}))
            state.previous_point = point
            return results

        if geometry_type == "single_line":
            line = geometry.get("points") or []
            if state.previous_point and crossed_line(state.previous_point, point, line):
                direction = geometry.get("direction")
                dx = point[0] - state.previous_point[0]
                dy = point[1] - state.previous_point[1]
                allowed = True
                if direction == "positive_x":
                    allowed = dx > 0
                elif direction == "negative_x":
                    allowed = dx < 0
                elif direction == "positive_y":
                    allowed = dy > 0
                elif direction == "negative_y":
                    allowed = dy < 0
                if allowed and self.emit_once(state, cooldown, now):
                    results.append(self.candidate(rule, detection, now, detection["confidence"], {"line_crossed": True, "dx": dx, "dy": dy}))
            state.previous_point = point
            return results

        if geometry_type == "double_line":
            lines = geometry.get("lines") or {}
            crossed: list[str] = []
            if state.previous_point:
                for label in ("L1", "L2"):
                    line = lines.get(label) or []
                    if crossed_line(state.previous_point, point, line):
                        crossed.append(label)
            expected = tuple(geometry.get("sequence") or (["L1", "L2"] if event_key == "entrada_vacuo" else ["L2", "L1"]))
            timeout = float(config.get("timeout_seconds", 8))
            for label in crossed:
                if state.line_state.observe(label, now, expected, timeout):
                    if self.emit_once(state, cooldown, now):
                        output_key = "possible_entry_tailgating" if event_key == "entrada_vacuo" else "possible_exit_tailgating"
                        payload = self.candidate(rule, detection, now, detection["confidence"], {"sequence": expected, "timeout_seconds": timeout})
                        payload["event_key"] = output_key
                        results.append(payload)
            state.previous_point = point
            return results

        state.previous_point = point
        return results

    def evaluate_frame(self, payload: dict) -> list[dict]:
        self.refresh()
        camera_id = payload["camera_id"]
        detections = payload.get("detections") or []
        width = int(payload.get("frame_width") or 1)
        height = int(payload.get("frame_height") or 1)
        results: list[dict] = []
        for rule in self.rules.get(camera_id, []):
            for detection in detections:
                results.extend(self.evaluate_detection(rule, detection, detections, width, height))
        return results


def main() -> None:
    create_schema()
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        client.xgroup_create(DETECTION_STREAM, GROUP, id="0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise
    runtime = RuleRuntime()
    while True:
        response = client.xreadgroup(GROUP, CONSUMER, {DETECTION_STREAM: ">"}, count=16, block=5000)
        if not response:
            continue
        for _, entries in response:
            for message_id, fields in entries:
                try:
                    payload = json.loads(fields["payload"])
                    for candidate in runtime.evaluate_frame(payload):
                        client.xadd(CANDIDATE_STREAM, {"payload": json.dumps(candidate, separators=(",", ":"))}, maxlen=100_000, approximate=True)
                    client.xack(DETECTION_STREAM, GROUP, message_id)
                except Exception:
                    LOG.exception("rule evaluation failed message=%s", message_id)


if __name__ == "__main__":
    main()
