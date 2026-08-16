from __future__ import annotations

import os

import yaml
from fastapi import APIRouter, Header, HTTPException

from .platform import require_admin

CATALOG_PATH = os.getenv("VISION_EVENT_CATALOG", "/app/config/event_catalog.yaml")
router = APIRouter()


@router.get("/api/v1/admin/event-catalog")
def event_catalog(authorization: str | None = Header(default=None)):
    require_admin(authorization)
    try:
        with open(CATALOG_PATH, "r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
    except OSError as exc:
        raise HTTPException(status_code=503, detail="event catalog unavailable") from exc
    return payload
