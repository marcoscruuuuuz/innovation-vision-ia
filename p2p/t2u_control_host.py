from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
from pathlib import Path


def valid_bridges(gateway_root: Path) -> set[str]:
    return {
        item.stem.removeprefix("bridge-")
        for item in (gateway_root / "config").glob("bridge-*.json")
        if item.is_file() and item.stem.removeprefix("bridge-")
    }


def handle(request: bytes, gateway_root: Path) -> bytes:
    try:
        payload = json.loads(request.decode("utf-8"))
        action = payload.get("action")
        bridge = payload.get("bridge")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return b'{"ok":false,"error":"invalid control request"}'
    if action != "restart" or not isinstance(bridge, str) or bridge not in valid_bridges(gateway_root):
        return b'{"ok":false,"error":"unauthorized bridge action"}'
    unit = f"vision-ia-t2u-bridge@{bridge}.service"
    result = subprocess.run(
        ["systemctl", "restart", unit],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        return b'{"ok":false,"error":"systemd restart failed"}'
    return json.dumps({"ok": True, "bridge": bridge, "unit": unit}).encode("utf-8")


def serve(socket_path: Path, gateway_root: Path) -> None:
    socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(socket_path))
        os.chmod(socket_path, 0o600)
        server.listen(16)
        while True:
            connection, _ = server.accept()
            with connection:
                connection.settimeout(15)
                request = connection.recv(65536)
                connection.sendall(handle(request, gateway_root))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--gateway-root", required=True)
    args = parser.parse_args()
    serve(Path(args.socket), Path(args.gateway_root))

