from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
import redis.asyncio as redis
import yaml

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOG = logging.getLogger("vision-light-ingest")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
PREFIX = os.getenv("REDIS_PREFIX", "vl:")
FRAME_STREAM = f"{PREFIX}frames"
HEALTH_KEY = f"{PREFIX}health:ingest"
REGISTRY_FILE = Path(os.getenv("GATEWAY_REGISTRY_FILE", "/config/gateways.yaml"))
DETECT_FPS = float(os.getenv("DETECT_FPS", "2"))
CONNECT_TIMEOUT = float(os.getenv("GATEWAY_CONNECT_TIMEOUT_S", "5"))
READ_TIMEOUT = float(os.getenv("GATEWAY_READ_TIMEOUT_S", "35"))
LATEST_FRAME_TTL_S = int(os.getenv("LATEST_FRAME_TTL_S", "30"))


def load_registry() -> dict[str, Any]:
    if not REGISTRY_FILE.exists():
        raise FileNotFoundError(f"gateway registry not found: {REGISTRY_FILE}")
    data = yaml.safe_load(REGISTRY_FILE.read_text()) or {}
    gateways = {item["id"]: item for item in data.get("gateways", [])}
    cameras = [item for item in data.get("cameras", []) if item.get("enabled", True)]
    return {"gateways": gateways, "cameras": cameras}


def token_for(gateway: dict[str, Any]) -> str | None:
    value = gateway.get("auth_token")
    if value:
        return str(value)
    token_file = gateway.get("auth_token_file")
    if token_file and Path(token_file).exists():
        return Path(token_file).read_text().strip()
    return None


async def fetch_snapshot(client: httpx.AsyncClient, camera: dict[str, Any], gateway: dict[str, Any]) -> bytes:
    base = str(gateway["api_base_url"]).rstrip("/")
    path = str(gateway.get("snapshot_path", "/v1/snapshot"))
    request = camera.get("snapshot_request", {})
    method = str(request.get("method", "POST")).upper()
    headers: dict[str, str] = {"Accept": "image/jpeg"}
    token = token_for(gateway)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = await client.request(
        method,
        f"{base}{path}",
        headers=headers,
        params=request.get("params"),
        json=request.get("json"),
    )
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").lower()
    if "image/jpeg" not in content_type and not response.content.startswith(b"\xff\xd8"):
        raise ValueError(f"gateway returned non-JPEG content type={content_type}")
    return response.content


async def camera_loop(
    redis_client: redis.Redis,
    http_client: httpx.AsyncClient,
    camera: dict[str, Any],
    gateway: dict[str, Any],
) -> None:
    camera_id = str(camera["camera_id"])
    interval = 1.0 / max(DETECT_FPS, 0.1)
    failures = 0
    while True:
        started = time.monotonic()
        try:
            jpeg = await fetch_snapshot(http_client, camera, gateway)
            capture_ts = time.time()
            encoded = base64.b64encode(jpeg).decode()
            digest = hashlib.sha256(jpeg).hexdigest()
            await redis_client.xadd(
                FRAME_STREAM,
                {
                    "camera_id": camera_id,
                    "capture_ts": f"{capture_ts:.6f}",
                    "jpeg_b64": encoded,
                },
                maxlen=10_000,
                approximate=True,
            )
            # The editor needs a current frame, but raw images must never become
            # long-term storage. This key is overwritten per camera and expires.
            await redis_client.set(
                f"{PREFIX}camera:{camera_id}:latest_jpeg_b64",
                encoded,
                ex=max(5, LATEST_FRAME_TTL_S),
            )
            await redis_client.hset(
                f"{PREFIX}camera:{camera_id}:health",
                mapping={
                    "last_frame_at": capture_ts,
                    "jpeg_bytes": len(jpeg),
                    "jpeg_sha256": digest,
                    "failures": 0,
                    "gateway_id": camera["gateway_id"],
                    "dvr_id": camera.get("dvr_id", ""),
                    "channel": camera.get("channel", ""),
                    "runtime_state": "ONLINE",
                },
            )
            await redis_client.expire(f"{PREFIX}camera:{camera_id}:health", max(30, LATEST_FRAME_TTL_S))
            failures = 0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failures += 1
            LOG.warning("snapshot failed camera=%s failures=%s error=%s", camera_id, failures, exc)
            await redis_client.hset(
                f"{PREFIX}camera:{camera_id}:health",
                mapping={
                    "last_error": str(exc),
                    "failures": failures,
                    "updated_at": time.time(),
                    "gateway_id": camera.get("gateway_id", ""),
                    "dvr_id": camera.get("dvr_id", ""),
                    "channel": camera.get("channel", ""),
                    "runtime_state": "DEGRADED",
                },
            )
            await redis_client.expire(f"{PREFIX}camera:{camera_id}:health", max(30, LATEST_FRAME_TTL_S))
            await asyncio.sleep(min(30.0, 0.5 * (2 ** min(failures, 6))))
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(0.0, interval - elapsed))


async def main() -> None:
    registry = load_registry()
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    await redis_client.ping()
    timeout = httpx.Timeout(connect=CONNECT_TIMEOUT, read=READ_TIMEOUT, write=READ_TIMEOUT, pool=READ_TIMEOUT)
    limits = httpx.Limits(max_connections=64, max_keepalive_connections=32)
    async with httpx.AsyncClient(timeout=timeout, limits=limits, follow_redirects=False) as http_client:
        tasks = []
        for camera in registry["cameras"]:
            gateway = registry["gateways"].get(camera["gateway_id"])
            if not gateway:
                LOG.error("camera=%s references unknown gateway=%s", camera.get("camera_id"), camera.get("gateway_id"))
                continue
            tasks.append(asyncio.create_task(camera_loop(redis_client, http_client, camera, gateway)))
        if not tasks:
            raise RuntimeError("no enabled cameras with valid gateways")
        await redis_client.hset(
            HEALTH_KEY,
            mapping={
                "started_at": time.time(),
                "camera_tasks": len(tasks),
                "state": "RUNNING",
                "latest_frame_ttl_s": LATEST_FRAME_TTL_S,
            },
        )
        await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
