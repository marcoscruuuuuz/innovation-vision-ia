from __future__ import annotations

import ctypes
import json
import os
import subprocess
import socket
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from psycopg.rows import dict_row


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://vision:vision@postgres:5432/vision")
GATEWAY_ROOT = Path(os.getenv("T2U_GATEWAY_ROOT", "/gateway"))
SDK_LIBRARY = Path(os.getenv("T2U_SDK_LIBRARY", str(GATEWAY_ROOT / "sdk/bin/libdhnetsdk.so")))
GATEWAY_HOST = os.getenv("T2U_GATEWAY_HOST", "host.docker.internal")
CONTROL_SOCKET = os.getenv("T2U_CONTROL_SOCKET", "/host-control/t2u-control.sock")
CAPTURE_SECONDS = max(2.0, min(float(os.getenv("T2U_CAPTURE_SECONDS", "4")), 12.0))
CAPTURE_TIMEOUT = max(10.0, float(os.getenv("T2U_CAPTURE_TIMEOUT", "25")))
SDK_LOCK = threading.Lock()


class GatewayError(RuntimeError):
    """A real bridge or SDK condition prevented the capture."""


@dataclass(frozen=True)
class DeviceBinding:
    dvr_name: str
    bridge_slug: str
    device_id: int
    local_port: int | None
    connected: bool
    updated_at: str | None
    bridge_started_at: str | None
    username: str | None
    password: str | None


def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GatewayError(f"gateway metadata unavailable: {path.name}") from exc
    if not isinstance(value, dict):
        raise GatewayError(f"gateway metadata is invalid: {path.name}")
    return value


class T2UGateway:
    def __init__(self, root: Path = GATEWAY_ROOT) -> None:
        self.root = root
        self._sdk: ctypes.CDLL | None = None

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def status_dir(self) -> Path:
        return self.root / "status"

    @property
    def map_file(self) -> Path:
        return self.root / "condo-device-map.json"

    @property
    def credential_file(self) -> Path:
        return self.root / "fleet-secrets.json"

    def bindings(self, *, include_credentials: bool = True) -> list[DeviceBinding]:
        serial_to_name: dict[str, str] = {}
        for rule in _read_json(self.map_file).get("rules", []):
            if not isinstance(rule, dict):
                continue
            match = rule.get("match")
            serial = match.get("serial") if isinstance(match, dict) else None
            name = rule.get("device_name")
            if isinstance(serial, str) and serial and isinstance(name, str) and name:
                serial_to_name[serial] = name

        devices: dict[int, tuple[str, str]] = {}
        for config_path in self.config_dir.glob("bridge-*.json"):
            config = _read_json(config_path)
            slug = config_path.stem.removeprefix("bridge-")
            for device in config.get("Devices", []):
                if not isinstance(device, dict):
                    continue
                try:
                    device_id = int(device["DeviceId"])
                except (KeyError, TypeError, ValueError):
                    continue
                serial = device.get("Serial")
                if isinstance(serial, str) and serial in serial_to_name:
                    devices[device_id] = (slug, serial_to_name[serial])

        statuses: dict[int, tuple[int | None, bool, str | None, str | None]] = {}
        for status_path in self.status_dir.glob("bridge-status-*.json"):
            status = _read_json(status_path)
            bridge_started_at = str(status.get("StartedAt")) if status.get("StartedAt") else None
            for device in status.get("Devices", []):
                if not isinstance(device, dict):
                    continue
                try:
                    device_id = int(device["DeviceId"])
                except (KeyError, TypeError, ValueError):
                    continue
                raw_port = device.get("LocalPort")
                try:
                    local_port = int(raw_port) if raw_port is not None else None
                except (TypeError, ValueError):
                    local_port = None
                statuses[device_id] = (
                    local_port,
                    device.get("State") == "connected",
                    str(device.get("UpdatedAt")) if device.get("UpdatedAt") else None,
                    bridge_started_at,
                )

        credentials: dict[int, tuple[str, str]] = {}
        if include_credentials:
            for item in _read_json(self.credential_file).get("devices", []):
                if not isinstance(item, dict):
                    continue
                try:
                    device_id = int(item["device_id"])
                    username = str(item["username"])
                    password = str(item["password"])
                except (KeyError, TypeError, ValueError):
                    continue
                credentials[device_id] = (username, password)

        bindings: list[DeviceBinding] = []
        for device_id, (slug, dvr_name) in devices.items():
            local_port, connected, updated_at, bridge_started_at = statuses.get(device_id, (None, False, None, None))
            credential = credentials.get(device_id)
            bindings.append(
                DeviceBinding(
                    dvr_name=dvr_name,
                    bridge_slug=slug,
                    device_id=device_id,
                    local_port=local_port,
                    connected=connected,
                    updated_at=updated_at,
                    bridge_started_at=bridge_started_at,
                    username=credential[0] if credential else None,
                    password=credential[1] if credential else None,
                )
            )
        return sorted(bindings, key=lambda item: item.dvr_name)

    def binding_for_dvr(self, dvr_id: UUID, *, require_credentials: bool = False) -> DeviceBinding:
        with db() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.name
                  FROM dvrs d
                 WHERE d.id=%s AND d.enabled=true
                """,
                (dvr_id,),
            )
            row = cur.fetchone()
        if not row:
            raise GatewayError("DVR is not available")
        binding = next((item for item in self.bindings() if item.dvr_name == row["name"]), None)
        if not binding:
            raise GatewayError("DVR is not mapped to the T2U gateway")
        if not binding.connected or not binding.local_port:
            raise GatewayError("P2P gateway has no connected tunnel for this DVR")
        if require_credentials and (not binding.username or not binding.password):
            raise GatewayError("P2P gateway credentials are unavailable for this DVR")
        return binding

    def binding_for_camera(self, camera_id: UUID) -> DeviceBinding:
        with db() as conn, conn.cursor() as cur:
            cur.execute("SELECT dvr_id FROM cameras WHERE id=%s AND enabled=true", (camera_id,))
            camera = cur.fetchone()
        if not camera:
            raise GatewayError("camera is not available")
        return self.binding_for_dvr(camera["dvr_id"], require_credentials=True)

    def rotate_dvr_tunnel(self, dvr_id: UUID) -> dict[str, Any]:
        binding = self.binding_for_dvr(dvr_id)
        request = json.dumps({"action": "restart", "bridge": binding.bridge_slug}).encode("utf-8")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(90)
                client.connect(CONTROL_SOCKET)
                client.sendall(request)
                response = bytearray()
                while True:
                    chunk = client.recv(65536)
                    if not chunk:
                        break
                    response.extend(chunk)
        except OSError as exc:
            raise GatewayError("T2U bridge control service is unavailable") from exc
        try:
            result = json.loads(response.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise GatewayError("T2U bridge control service returned invalid data") from exc
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise GatewayError(str(result.get("error") if isinstance(result, dict) else "T2U bridge restart failed"))

        deadline = time.monotonic() + 75
        while time.monotonic() < deadline:
            refreshed = next(
                (item for item in self.bindings(include_credentials=False) if item.device_id == binding.device_id),
                None,
            )
            if refreshed and refreshed.connected and refreshed.local_port:
                return {
                    "status": "RECONNECTED",
                    "bridge": binding.bridge_slug,
                    "device_id": binding.device_id,
                    "sdk_local_port": refreshed.local_port,
                    "bridge_started_at": refreshed.bridge_started_at,
                }
            time.sleep(2)
        raise GatewayError("T2U bridge restart completed but the tunnel did not reconnect in time")

    def _sdk_client(self) -> ctypes.CDLL:
        if self._sdk is not None:
            return self._sdk
        if not SDK_LIBRARY.is_file():
            raise GatewayError("Intelbras Linux SDK library is not mounted")
        try:
            library = ctypes.CDLL(str(SDK_LIBRARY))
        except OSError as exc:
            raise GatewayError("Intelbras Linux SDK could not be loaded") from exc

        callback_type = ctypes.CFUNCTYPE(
            None,
            ctypes.c_longlong,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_uint32,
            ctypes.c_int32,
            ctypes.c_ulong,
        )
        library.CLIENT_Init.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        library.CLIENT_Init.restype = ctypes.c_int
        library.CLIENT_Cleanup.argtypes = []
        library.CLIENT_Cleanup.restype = None
        library.CLIENT_SetConnectTime.argtypes = [ctypes.c_int, ctypes.c_int]
        library.CLIENT_LoginEx2.argtypes = [
            ctypes.c_char_p,
            ctypes.c_uint16,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
        ]
        library.CLIENT_LoginEx2.restype = ctypes.c_longlong
        library.CLIENT_Logout.argtypes = [ctypes.c_longlong]
        library.CLIENT_Logout.restype = ctypes.c_int
        library.CLIENT_StartRealPlay.argtypes = [
            ctypes.c_longlong,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_int,
            callback_type,
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_uint32,
        ]
        library.CLIENT_StartRealPlay.restype = ctypes.c_longlong
        library.CLIENT_StopRealPlay.argtypes = [ctypes.c_longlong]
        library.CLIENT_StopRealPlay.restype = ctypes.c_int
        library.CLIENT_GetLastError.argtypes = []
        library.CLIENT_GetLastError.restype = ctypes.c_uint32
        library._vision_callback_type = callback_type
        self._sdk = library
        return library

    def _capture_raw(self, binding: DeviceBinding, channel: int, target: Path) -> None:
        library = self._sdk_client()
        if channel < 1:
            raise GatewayError("camera channel is invalid")
        captured = 0

        with SDK_LOCK, target.open("wb") as output:
            callback_type = library._vision_callback_type

            def on_stream(
                _preview: int,
                _data_type: int,
                buffer: ctypes.POINTER(ctypes.c_ubyte),
                size: int,
                _param: int,
                _user: int,
            ) -> None:
                nonlocal captured
                if buffer and size:
                    output.write(ctypes.string_at(buffer, size))
                    captured += size

            callback = callback_type(on_stream)
            if not library.CLIENT_Init(None, 0):
                raise GatewayError(f"Intelbras SDK initialization failed ({library.CLIENT_GetLastError()})")
            login = 0
            preview = 0
            try:
                library.CLIENT_SetConnectTime(5000, 1)
                login_error = ctypes.c_int(0)
                device_info = ctypes.create_string_buffer(4096)
                login = library.CLIENT_LoginEx2(
                    GATEWAY_HOST.encode("utf-8"),
                    int(binding.local_port),
                    binding.username.encode("utf-8"),
                    binding.password.encode("utf-8"),
                    0,
                    None,
                    ctypes.byref(device_info),
                    ctypes.byref(login_error),
                )
                if not login:
                    raise GatewayError(f"Intelbras SDK login failed ({login_error.value}/{library.CLIENT_GetLastError()})")
                preview = library.CLIENT_StartRealPlay(
                    login,
                    channel - 1,
                    None,
                    0,
                    callback,
                    None,
                    0,
                    10000,
                )
                if not preview:
                    raise GatewayError(f"Intelbras SDK stream request failed ({library.CLIENT_GetLastError()})")
                deadline = time.monotonic() + CAPTURE_SECONDS
                while time.monotonic() < deadline:
                    time.sleep(0.1)
            finally:
                if preview:
                    library.CLIENT_StopRealPlay(preview)
                if login:
                    library.CLIENT_Logout(login)
                library.CLIENT_Cleanup()

        if captured <= 0:
            raise GatewayError("Intelbras SDK returned no video frames")

    def capture_jpeg(self, camera_id: UUID) -> bytes:
        binding = self.binding_for_camera(camera_id)
        with db() as conn, conn.cursor() as cur:
            cur.execute("SELECT channel FROM cameras WHERE id=%s", (camera_id,))
            camera = cur.fetchone()
        if not camera:
            raise GatewayError("camera is not available")

        with tempfile.TemporaryDirectory(prefix="vision-t2u-") as directory:
            raw = Path(directory) / "capture.dhav"
            self._capture_raw(binding, int(camera["channel"]), raw)
            try:
                converted = subprocess.run(
                    [
                        "ffmpeg", "-v", "error", "-y", "-i", str(raw),
                        "-frames:v", "1", "-q:v", "3",
                        "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
                    ],
                    capture_output=True,
                    timeout=CAPTURE_TIMEOUT,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise GatewayError("frame decoder failed to run") from exc
            if converted.returncode != 0 or not converted.stdout.startswith(b"\xff\xd8"):
                raise GatewayError("captured stream could not be decoded as a video frame")
            image = converted.stdout

        with db() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE cameras
                   SET health_state='ONLINE',last_frame_at=now(),last_heartbeat_at=now(),updated_at=now()
                 WHERE id=%s
                """,
                (camera_id,),
            )
            cur.execute(
                """
                INSERT INTO camera_health_history(camera_id,state,fps,frame_gap_ms,decode_latency_ms)
                VALUES (%s,'ONLINE',NULL,NULL,NULL)
                """,
                (camera_id,),
            )
            conn.commit()
        return image


gateway = T2UGateway()
app = FastAPI(title="INNOVATION VISION IA T2U Capture", version="1.0.0")


@app.get("/health")
def health() -> dict[str, Any]:
    try:
        bindings = gateway.bindings()
    except GatewayError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "status": "ok",
        "sdk_library_present": SDK_LIBRARY.is_file(),
        "mapped_dvrs": len(bindings),
        "connected_tunnels": sum(1 for item in bindings if item.connected and item.local_port),
        "capture_seconds": CAPTURE_SECONDS,
    }


@app.post("/v1/cameras/{camera_id}/snapshot")
def snapshot(camera_id: UUID) -> Response:
    try:
        image = gateway.capture_jpeg(camera_id)
    except GatewayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(
        content=image,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.post("/v1/dvrs/{dvr_id}/rotate")
def rotate_tunnel(dvr_id: UUID) -> dict[str, Any]:
    try:
        return gateway.rotate_dvr_tunnel(dvr_id)
    except GatewayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

