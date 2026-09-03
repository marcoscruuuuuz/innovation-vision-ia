from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

import media_worker as base


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


def fetch_clip(
    camera: dict[str, Any],
    gateway: dict[str, Any],
    start_at: str,
    end_at: str,
    output: Path,
) -> None:
    request = camera.get("playback_request", {})
    method = str(request.get("method", "POST")).upper()
    base_url = str(gateway["api_base_url"]).rstrip("/")
    path = format_path(
        str(gateway.get("playback_clip_path", "/v1/cameras/{gateway_camera_id}/playback-clip")),
        camera,
    )
    replacements = {
        "start_at": start_at,
        "end_at": end_at,
        "camera_id": str(camera["camera_id"]),
        "gateway_camera_id": str(camera.get("gateway_camera_id") or camera["camera_id"]),
        "dvr_id": str(camera.get("dvr_id", "")),
        "channel": str(camera.get("channel", "")),
    }
    headers = {"Accept": "video/mp4"}
    token = base.token_for(gateway)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    with httpx.Client(timeout=httpx.Timeout(40, connect=5), follow_redirects=False) as client:
        response = client.request(
            method,
            f"{base_url}{path}",
            headers=headers,
            params=base.render_template(request.get("params"), replacements),
            json=base.render_template(request.get("json_template") or request.get("json"), replacements),
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if "video/mp4" not in content_type and not response.content:
            raise ValueError(f"gateway returned invalid clip content type={content_type}")
        output.write_bytes(response.content)


# base.process resolves fetch_clip from the base module globals.
base.fetch_clip = fetch_clip


if __name__ == "__main__":
    base.main()
