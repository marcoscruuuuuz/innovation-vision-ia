import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class VendorAdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class AdapterStatus:
    enabled: bool
    configured: bool
    executable: str | None
    executable_found: bool
    timeout_seconds: int
    contract_version: str = "vision.intelbras.wine.v1"


@dataclass(frozen=True)
class OpenSessionResult:
    session_ref: str
    sdk_local_port: int
    rtsp_local_port: int
    transport: str
    relay_mode: str | None = None
    vendor_metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class FrameProbeResult:
    frames_ok: bool
    frame_count: int
    first_frame_ms: float | None = None
    width: int | None = None
    height: int | None = None
    codec: str | None = None


class IntelbrasWineAdapter:
    """Adapter for an authorized external Intelbras/Wine bridge.

    Proprietary SDK/binaries stay outside this repository. The supervisor
    invokes one configured executable without a shell and exchanges one JSON
    document through stdin/stdout using contract vision.intelbras.wine.v1.
    """

    def __init__(self) -> None:
        self.enabled = os.getenv("INTELBRAS_VENDOR_ADAPTER_ENABLED", "false").lower() == "true"
        self.executable = os.getenv("INTELBRAS_VENDOR_ADAPTER_COMMAND") or None
        self.timeout_seconds = int(os.getenv("INTELBRAS_VENDOR_ADAPTER_TIMEOUT", "30"))

    def status(self) -> AdapterStatus:
        found = False
        if self.executable:
            path = Path(self.executable)
            found = (path.is_absolute() and path.is_file() and os.access(path, os.X_OK)) or shutil.which(self.executable) is not None
        return AdapterStatus(
            enabled=self.enabled,
            configured=bool(self.executable),
            executable=self.executable,
            executable_found=found,
            timeout_seconds=self.timeout_seconds,
        )

    def _require_ready(self) -> None:
        status = self.status()
        if not status.enabled:
            raise VendorAdapterError("Intelbras vendor adapter is disabled")
        if not status.configured:
            raise VendorAdapterError("INTELBRAS_VENDOR_ADAPTER_COMMAND is not configured")
        if not status.executable_found:
            raise VendorAdapterError("configured Intelbras vendor adapter executable was not found or is not executable")

    def _invoke(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_ready()
        request = {"contract": "vision.intelbras.wine.v1", "action": action, "payload": payload}
        try:
            proc = subprocess.run(
                [self.executable],
                input=json.dumps(request),
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise VendorAdapterError(f"vendor adapter timeout after {self.timeout_seconds}s") from exc
        except OSError as exc:
            raise VendorAdapterError(f"failed to execute vendor adapter: {exc}") from exc

        if proc.returncode != 0:
            stderr = proc.stderr.strip()[-2000:]
            raise VendorAdapterError(f"vendor adapter returned {proc.returncode}: {stderr}")
        try:
            response = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise VendorAdapterError("vendor adapter did not return valid JSON") from exc
        if not isinstance(response, dict):
            raise VendorAdapterError("vendor adapter response must be a JSON object")
        if response.get("contract") != "vision.intelbras.wine.v1":
            raise VendorAdapterError("vendor adapter contract version mismatch")
        if response.get("ok") is not True:
            raise VendorAdapterError(str(response.get("error") or "vendor adapter operation failed"))
        result = response.get("result")
        if not isinstance(result, dict):
            raise VendorAdapterError("vendor adapter result must be a JSON object")
        return result

    def open_session(
        self,
        *,
        dvr_id: str,
        serial_secret_ref: str,
        username_secret_ref: str,
        password_secret_ref: str,
        sdk_local_port: int,
        rtsp_local_port: int,
        wine_worker_key: str,
    ) -> OpenSessionResult:
        result = self._invoke(
            "open_session",
            {
                "dvr_id": dvr_id,
                "serial_secret_ref": serial_secret_ref,
                "username_secret_ref": username_secret_ref,
                "password_secret_ref": password_secret_ref,
                "sdk_local_port": sdk_local_port,
                "rtsp_local_port": rtsp_local_port,
                "wine_worker_key": wine_worker_key,
            },
        )
        try:
            parsed = OpenSessionResult(
                session_ref=str(result["session_ref"]),
                sdk_local_port=int(result["sdk_local_port"]),
                rtsp_local_port=int(result["rtsp_local_port"]),
                transport=str(result.get("transport", "intelbras_p2p")),
                relay_mode=result.get("relay_mode"),
                vendor_metadata=result.get("vendor_metadata") if isinstance(result.get("vendor_metadata"), dict) else None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise VendorAdapterError("vendor adapter open_session result is incomplete") from exc
        if parsed.sdk_local_port != sdk_local_port or parsed.rtsp_local_port != rtsp_local_port:
            raise VendorAdapterError("vendor adapter returned ports different from the reserved leases")
        return parsed

    def probe_frames(self, *, session_ref: str, minimum_frames: int = 3) -> FrameProbeResult:
        result = self._invoke("probe_frames", {"session_ref": session_ref, "minimum_frames": minimum_frames})
        try:
            parsed = FrameProbeResult(
                frames_ok=bool(result["frames_ok"]),
                frame_count=int(result["frame_count"]),
                first_frame_ms=float(result["first_frame_ms"]) if result.get("first_frame_ms") is not None else None,
                width=int(result["width"]) if result.get("width") is not None else None,
                height=int(result["height"]) if result.get("height") is not None else None,
                codec=str(result["codec"]) if result.get("codec") is not None else None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise VendorAdapterError("vendor adapter probe_frames result is incomplete") from exc
        if not parsed.frames_ok or parsed.frame_count < minimum_frames:
            raise VendorAdapterError(f"frame probe failed: received {parsed.frame_count}/{minimum_frames} frames")
        return parsed

    def close_session(self, *, session_ref: str) -> dict[str, Any]:
        return self._invoke("close_session", {"session_ref": session_ref})

    def health(self, *, session_ref: str) -> dict[str, Any]:
        return self._invoke("health", {"session_ref": session_ref})

    def public_status(self) -> dict[str, Any]:
        data = asdict(self.status())
        data["executable"] = Path(data["executable"]).name if data["executable"] else None
        return data
