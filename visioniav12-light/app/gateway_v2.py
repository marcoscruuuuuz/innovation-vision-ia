from __future__ import annotations

import asyncio
import base64
import time
from typing import Any

import httpx

import gateway as base


def format_path(template: str, camera: dict[str, Any]) -> str:
    gateway_camera_id = str(camera.get("gateway_camera_id") or camera["camera_id"])
    values = {
        "camera_id": str(camera["camera_id"]),
        "gateway_camera_id": gateway_camera_id,
        "dvr_id": str(camera.get("dvr_id", "")),
        "channel": str(camera.get("channel", "")),
    }
    try:
        return template.format(**values)
    except KeyError as exc:
        raise ValueError(f"unknown gateway path template field: {exc.args[0]}") from exc


async def fetch_snapshot(
    client: httpx.AsyncClient,
    camera: dict[str, Any],
    gateway: dict[str, Any],
) -> bytes:
    base_url = str(gateway["api_base_url"]).rstrip("/")
    path = format_path(str(gateway.get("snapshot_path", "/v1/cameras/{gateway_camera_id}/snapshot")), camera)
    request = camera.get("snapshot_request", {})
    method = str(request.get("method", "POST")).upper()
    headers: dict[str, str] = {"Accept": "image/jpeg"}
    token = base.token_for(gateway)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = await client.request(
        method,
        f"{base_url}{path}",
        headers=headers,
        params=request.get("params"),
        json=request.get("json"),
    )
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").lower()
    if "image/jpeg" not in content_type and not response.content.startswith(b"\xff\xd8"):
        raise ValueError(f"gateway returned non-JPEG content type={content_type}")
    return response.content


# base.camera_loop resolves fetch_snapshot from the base module globals.
base.fetch_snapshot = fetch_snapshot


if __name__ == "__main__":
    asyncio.run(base.main())
