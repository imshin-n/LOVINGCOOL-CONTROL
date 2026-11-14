"""
Local control socket: newline-delimited JSON, one request -> one reply.

    {"cmd": "status"}                       -> {"ok": true, "connected": true, ...}
    {"cmd": "show", "path": "/x/cat.gif"}   -> {"ok": true, "applied": true, ...}
    {"cmd": "nope"}                         -> {"ok": false, "error": "..."}

The socket lives in $XDG_RUNTIME_DIR (private to the user, cleared at
logout) and is created with mode 0600, so only the owning user can drive
the screen.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

SOCKET_NAME = "lovingcool-control.sock"
MAX_REQUEST = 64 * 1024


def default_socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and Path(runtime).is_dir():
        return Path(runtime) / SOCKET_NAME
    return Path(tempfile.gettempdir()) / f"lovingcool-control-{os.getuid()}.sock"


def socket_is_live(path: Path) -> bool:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        s.close()


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        handler: Callable[[dict], dict] = self.server.request_handler   # type: ignore[attr-defined]
        while True:
            line = self.rfile.readline(MAX_REQUEST + 1)
            if not line:
                return
            if len(line) > MAX_REQUEST:
                reply = {"ok": False, "error": "request too large"}
            else:
                try:
                    req = json.loads(line)
                    reply = handler(req) if isinstance(req, dict) else {"ok": False, "error": "expected a JSON object"}
                except json.JSONDecodeError as e:
                    reply = {"ok": False, "error": f"invalid JSON: {e}"}
                except Exception as e:
                    log.exception("Request failed")
                    reply = {"ok": False, "error": f"internal error: {e}"}
            self.wfile.write(json.dumps(reply).encode() + b"\n")


class _UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class Server:
    def __init__(self, path: Path, request_handler: Callable[[dict], dict]):
        self.path = path
        if path.exists():
            if socket_is_live(path):
                raise RuntimeError(f"Another lovingcool-controld is already running ({path})")
            path.unlink()   # stale, left by a crash
        old_umask = os.umask(0o177)
        try:
            self._server = _UnixServer(str(path), _Handler)
        finally:
            os.umask(old_umask)
        self._server.request_handler = request_handler
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="control-socket")
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
