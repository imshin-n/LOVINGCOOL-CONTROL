"""
lovingcool-control :: gui/bridge.py
===================================
The GUI's only way to reach the screen: requests to lovingcool-controld
over its control socket, made on a worker thread so the window never
blocks on device I/O.

If no service is running, the bridge starts one inside the GUI process
(same code, same socket), so the app also works on its own, e.g. on a
machine without the systemd service, or on Windows/macOS. That built-in
service stops when the window closes; the background one keeps going.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QSettings, QThreadPool, QTimer, pyqtSignal

from ..service.client import ServiceUnavailable, request
from ..service.server import default_socket_path, socket_is_live

log = logging.getLogger(__name__)

Reply = dict
Callback = Callable[[Reply], None]

MODE_SERVICE = "service"       # talking to a separately running lovingcool-controld
MODE_EMBEDDED = "embedded"     # we started the service inside this process
MODE_NONE = "none"             # couldn't reach or start one


class _Job(QRunnable):
    def __init__(self, bridge: ServiceBridge, req: dict, callback: Callback | None):
        super().__init__()
        self.bridge, self.req, self.callback = bridge, req, callback

    def run(self) -> None:
        try:
            reply = request(self.req, self.bridge.socket_path)
        except ServiceUnavailable as e:
            reply = {"ok": False, "error": str(e), "unavailable": True}
        except Exception as e:   # socket timeouts, bad JSON, ...
            reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        self.bridge._done.emit(self.callback, self.req, reply)


class ServiceBridge(QObject):
    status_changed = pyqtSignal(dict)       # latest status reply (also on connection changes)
    mode_changed = pyqtSignal(str)
    log_message = pyqtSignal(str)
    _done = pyqtSignal(object, dict, dict)  # worker -> GUI thread: (callback, request, reply)

    def __init__(self, socket_path: Path | None = None, embed: bool = True, poll_ms: int = 1500,
                 runner_factory: Callable[[Path], object] | None = None):
        super().__init__()
        self.socket_path = socket_path or default_socket_path()
        self.embed = embed
        self.mode = MODE_NONE
        self.status: dict = {}
        self._runner = None
        self._runner_factory = runner_factory
        self._poll_pending = False
        self._settings = QSettings("lovingcool-control", "lovingcool-control")
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)   # requests run in the order they were made
        self._done.connect(self._on_done)
        self._timer = QTimer(self)
        self._timer.setInterval(poll_ms)
        self._timer.timeout.connect(self.refresh)

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self) -> None:
        self._connect_or_embed()
        self.refresh()
        self._timer.start()

    def shutdown(self) -> None:
        self._timer.stop()
        self._pool.waitForDone(5000)
        if self._runner is not None:
            self._runner.stop()
            self._runner = None

    def _set_mode(self, mode: str) -> None:
        if mode != self.mode:
            self.mode = mode
            self.mode_changed.emit(mode)

    def _connect_or_embed(self) -> None:
        if socket_is_live(self.socket_path):
            self._set_mode(MODE_SERVICE)
            return
        if not self.embed:
            self._set_mode(MODE_NONE)
            return
        try:
            if self._runner_factory is not None:
                runner = self._runner_factory(self.socket_path)
            else:
                from ..service.daemon import ServiceRunner
                runner = ServiceRunner(self.socket_path)
            runner.start()
        except Exception as e:
            self.log_message.emit(f"[ERROR] Could not start the built-in service: {e}")
            self._set_mode(MODE_NONE)
            return
        self._runner = runner
        self._set_mode(MODE_EMBEDDED)
        self.log_message.emit("[INFO] No background service found; running one inside this window")

    # ── Requests ──────────────────────────────────────────────

    def call(self, req: dict, callback: Callback | None = None) -> None:
        """Send req on the worker thread; callback(reply) runs on the GUI thread."""
        self._pool.start(_Job(self, req, callback))

    def refresh(self) -> None:
        if self._poll_pending:
            return
        self._poll_pending = True
        self.call({"cmd": "status"}, self._on_status)

    def _on_done(self, callback, req: dict, reply: dict) -> None:
        if reply.get("unavailable"):
            # The service went away (stopped, crashed, restarted): take over or report it.
            if self.mode != MODE_EMBEDDED:
                self._connect_or_embed()
        if not reply.get("ok") and req.get("cmd") != "status":
            self.log_message.emit(f"[ERROR] {reply.get('error')}")
        if callback is not None:
            callback(reply)

    def _on_status(self, reply: dict) -> None:
        self._poll_pending = False
        status = reply if reply.get("ok") else {"connected": False, "error": reply.get("error")}
        size = self._panel_from(status)
        if size:
            self._settings.setValue("panel_size", list(size))
        self.status = status
        self.status_changed.emit(status)

    # ── Helpers for pages ─────────────────────────────────────

    @staticmethod
    def _panel_from(status: dict) -> tuple[int, int] | None:
        panel = (status.get("device") or {}).get("panel")
        if panel and len(panel) == 2 and all(isinstance(n, int) and n > 0 for n in panel):
            return panel[0], panel[1]
        return None

    @property
    def connected(self) -> bool:
        return bool(self.status.get("connected"))

    def panel_size(self) -> tuple[int, int]:
        """Native panel size: live from the device, else the last one seen, else 480x480."""
        size = self._panel_from(self.status)
        if size:
            return size
        saved = self._settings.value("panel_size")
        try:
            w, h = (int(n) for n in saved)
            return w, h
        except (TypeError, ValueError):
            return 480, 480
