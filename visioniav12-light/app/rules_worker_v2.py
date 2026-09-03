from __future__ import annotations

import json
import logging
import os

import redis

from models import create_schema
from rules_worker import (
    CANDIDATE_STREAM,
    CONSUMER,
    DETECTION_STREAM,
    GROUP,
    REDIS_URL,
    RuleRuntime,
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOG = logging.getLogger("vision-light-rules-v2")


class CanonicalRuleRuntime(RuleRuntime):
    """Keeps the configured rule key as the persistence/authorization key.

    Video-only tailgating remains explicitly marked as possible in metadata,
    while media authorization and rule lookup keep using entrada_vacuo or
    saida_vacuo. This prevents the media worker from dropping a valid candidate
    because there is no separate production Rule row for the normalized output.
    """

    def evaluate_detection(self, rule, detection, all_detections, frame_width, frame_height):  # type: ignore[no-untyped-def]
        candidates = super().evaluate_detection(rule, detection, all_detections, frame_width, frame_height)
        for candidate in candidates:
            if rule.event_key == "entrada_vacuo" and candidate.get("event_key") == "possible_entry_tailgating":
                candidate["normalized_output_event"] = "possible_entry_tailgating"
                candidate["event_key"] = "entrada_vacuo"
            elif rule.event_key == "saida_vacuo" and candidate.get("event_key") == "possible_exit_tailgating":
                candidate["normalized_output_event"] = "possible_exit_tailgating"
                candidate["event_key"] = "saida_vacuo"
        return candidates


def main() -> None:
    create_schema()
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        client.xgroup_create(DETECTION_STREAM, GROUP, id="0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise

    runtime = CanonicalRuleRuntime()
    while True:
        response = client.xreadgroup(GROUP, CONSUMER, {DETECTION_STREAM: ">"}, count=16, block=5000)
        if not response:
            continue
        for _, entries in response:
            for message_id, fields in entries:
                try:
                    payload = json.loads(fields["payload"])
                    for candidate in runtime.evaluate_frame(payload):
                        client.xadd(
                            CANDIDATE_STREAM,
                            {"payload": json.dumps(candidate, separators=(",", ":"))},
                            maxlen=100_000,
                            approximate=True,
                        )
                    client.xack(DETECTION_STREAM, GROUP, message_id)
                except Exception:
                    LOG.exception("rule evaluation failed message=%s", message_id)


if __name__ == "__main__":
    main()
