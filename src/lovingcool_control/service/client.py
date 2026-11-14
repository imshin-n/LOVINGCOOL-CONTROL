"""Talk to a running lovingcool-controld over its control socket."""

from __future__ import annotations

import json
import socket
from pathlib import Path

from .server import default_socket_path


class ServiceUnavailable(Exception):
    pass


def request(req: dict, path: Path | None = None, timeout: float = 15.0) -> dict:
    path = path or default_socket_path()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        try:
            s.connect(str(path))
        except (FileNotFoundError, ConnectionRefusedError) as e:
            raise ServiceUnavailable(f"lovingcool-controld is not running (no socket at {path})") from e
        s.sendall(json.dumps(req).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                raise ServiceUnavailable("lovingcool-controld closed the connection")
            buf += chunk
        return json.loads(buf)
    finally:
        s.close()
