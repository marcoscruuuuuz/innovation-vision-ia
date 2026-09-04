from __future__ import annotations

import os
from pathlib import Path

import yaml
from sqlalchemy import select

from models import Camera, Rule, SessionLocal, create_schema

REGISTRY_FILE = Path(os.getenv("GATEWAY_REGISTRY_FILE", "/config/gateways.yaml"))
SEED_STATE = os.getenv("SEED_RULE_STATE", "SHADOW")
CANARY_EVENTS = [
    item.strip()
    for item in os.getenv(
        "CANARY_EVENT_KEYS",
        "animal_em_geral,animal_solto,animal_com_tutor,cachorro_fazendo_fezes,possiveis_fezes,morador_nao_recolheu_fezes,pessoa_fora_horario_22h,porta_bloco_aberta,entrada_vacuo,saida_vacuo",
    ).split(",")
    if item.strip()
]


def main() -> None:
    create_schema()
    data = yaml.safe_load(REGISTRY_FILE.read_text()) or {}
    cameras = [item for item in data.get("cameras", []) if item.get("enabled", True)]
    if not cameras:
        raise RuntimeError("gateways.yaml contains no enabled cameras")

    with SessionLocal() as session:
        for item in cameras:
            camera_id = str(item["camera_id"])
            camera = session.get(Camera, camera_id) or Camera(id=camera_id)
            camera.condo = str(item.get("condo") or item.get("dvr_id") or "UNASSIGNED")
            camera.name = str(item.get("name") or camera_id)
            camera.dvr_id = str(item["dvr_id"])
            camera.gateway_id = str(item["gateway_id"])
            camera.channel = int(item["channel"])
            camera.enabled = bool(item.get("enabled", True))
            camera.config = {key: value for key, value in item.items() if key not in {"snapshot_request", "playback_request"}}
            session.add(camera)
            session.flush()

            for event_key in CANARY_EVENTS:
                exists = session.scalar(
                    select(Rule).where(Rule.camera_id == camera_id, Rule.event_key == event_key)
                )
                if exists is None:
                    session.add(
                        Rule(
                            camera_id=camera_id,
                            event_key=event_key,
                            enabled=False,
                            state=SEED_STATE,
                            geometry={},
                            config={"seeded": True},
                            version=1,
                        )
                    )
        session.commit()

    print(f"seeded cameras={len(cameras)} rules_per_camera={len(CANARY_EVENTS)} state={SEED_STATE}")


if __name__ == "__main__":
    main()
